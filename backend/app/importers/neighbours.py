"""Pliki .ese FIR-ów sąsiednich (data/import/neighbours/): wybór sektorów do data/seed/sectors.geojson
(generator sectors_geojson.py) i import stanowisk przy starcie aplikacji (tabela nb_positions).

Z każdego pliku bierzemy tylko FIR-y, które ten pakiet utrzymuje (NB_SOURCES): kopie sektorów EPWW i innych
sąsiadów w cudzych plikach bywają nieaktualne (pakiet ukraiński ma stare polskie sektory). Tylko sektory w rejonie
Polski (REGION), bez technicznych (TECH …, T-…) i bez wariantów pasów innych niż domyślny (ACTIVE, zob. pick_runways).
Listy OWNER zamieniamy na znaki stanowisk już w generatorze, bo identyfikatory z [POSITIONS] są ważne
tylko w obrębie jednego pliku (to samo ID w dwóch plikach to różne stanowiska)."""

import json
import re
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from ..models import NbPosition
from .ese import ese_sections, parse_positions, read_airspace
from .sct import read_text

# klucz pliku (fragment nazwy pliku) → FIR-y (pierwszy człon nazwy sektora) brane z tego pliku, katalog vacs-data
# i prefiksy ICAO lotnisk i FIR-ów pakietu (airports): ich stanowiska (TWR, GND, DEL, ATIS, APP…) trafiają do kart
# lotnisk w AERODROME, także gdy nie obsługują żadnego sektora w rejonie Polski
NB_SOURCES = {
    "EDWW": {"firs": ["EDWW"], "vacs": "EDWW", "airports": ("ED", "ET")},  # Bremen z EDYY (Maastricht) i EDUU HVL/OSE
    "EDMM": {"firs": ["EDMM"], "vacs": "EDMM", "airports": ("ED", "ET")},  # München z sektorami EDUU (Rhein) nad nim
    "EDGG": {"firs": ["EDGG"], "vacs": "EDGG", "airports": ("ED", "ET")},  # Langen z sektorami EDUU (Rhein) nad nim
    "ESAA": {"firs": ["ESAA"], "vacs": "ES", "airports": ("ES",)},
    "EKDK": {"firs": ["EKDK"], "vacs": "EK", "airports": ("EK",)},
    "EYVL": {"firs": ["EYVL"], "vacs": "EY", "airports": ("EY",)},
    "LKAA": {"firs": ["LKAA"], "vacs": "LK", "airports": ("LK",)},
    "LZBB": {"firs": ["LZBB"], "vacs": "LZ", "airports": ("LZ",)},
    "UKBV": {"firs": ["UKLV"], "vacs": None, "airports": ("UKL",)},  # pakiet ukraiński: tylko Lwów (sąsiad Polski)
    "ULLL": {"firs": ["UMKK"], "vacs": "UMKK", "airports": ("UMK",)},  # pakiet rosyjski (Petersburg): tylko Kaliningrad
}
# rejon Polski z zapasem: lat min, lat max, lon min, lon max (sektory z ramką poza nim pomijamy)
REGION = (46.5, 58.0, 10.0, 28.5)
SIMPLIFY_DEG = 0.001  # uproszczenie linii granic (~100 m): mniej danych dla mapy, różnica niewidoczna
_TECH = re.compile(r"^(?:TECH |T-)", re.I)


def source_key(path: Path) -> str | None:
    """Klucz pakietu z nazwy pliku: EDMM-AeroNav_2026….ese → EDMM, SF_-_LKAA.ESE → LKAA."""
    stem = path.stem.upper()
    return next((k for k in NB_SOURCES if k in stem), None)


WIND_FROM = 250  # przeważający wiatr w tym rejonie (z zachodu–południowego zachodu)


def _rwy_num(rwy: str) -> int | None:
    m = re.match(r"\d{1,2}", rwy or "")
    return int(m.group()) if m else None


def pick_runways(sectors: list[dict]) -> dict[str, int]:
    """Domyślna konfiguracja sektorów zależnych od pasa (ACTIVE): jeden kierunek na lotnisko, najbliższy wiatrowi
    z WIND_FROM (LKPR 24, nie jednocześnie 24 i 30, które w pliku LKAA są alternatywnymi układami TMA).
    Pasy równoległe (24L/24R) mają ten sam numer, więc zostają razem."""
    nums: dict[str, set[int]] = {}
    for s in sectors:
        for a in s["active"]:
            icao, _, rwy = a.partition(":")
            if (n := _rwy_num(rwy)) is not None:
                nums.setdefault(icao, set()).add(n)
    dist = lambda n: min(abs(n * 10 - WIND_FROM), 360 - abs(n * 10 - WIND_FROM))  # noqa: E731
    return {icao: min(ns, key=lambda n: (dist(n), -n)) for icao, ns in nums.items()}


def rwy_active(active: list[str], chosen: dict[str, int]) -> bool:
    """Sektor bez ACTIVE jest zawsze; z ACTIVE – gdy któryś wpis to wybrany pas lotniska (albo pas bez numeru)."""
    if not active:
        return True
    for a in active:
        icao, _, rwy = a.partition(":")
        n = _rwy_num(rwy)
        if n is None or chosen.get(icao) == n:
            return True
    return False


def _simplify(pts: list[list[float]], tol: float) -> list[list[float]]:
    """Douglas–Peucker dla otwartej łamanej [[lon, lat], ...] (bez rekurencji)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        best, idx = 0.0, None
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / norm if norm else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def simplify_line(pts: list, tol: float = SIMPLIFY_DEG) -> list:
    """Linia graniczna (SECTORLINE) po uproszczeniu; końce zostają, więc linie dalej się sklejają. Upraszczamy każdą
    linię raz, przed złożeniem sektorów: sąsiednie sektory mają wtedy na wspólnej granicy te same wierzchołki
    (MAP i RADIO łączą sektory jednego stanowiska w jeden obrys)."""
    if len(pts) > 2 and pts[0] == pts[-1]:  # linia zamknięta (okrąg): dwie połowy, żeby zachować kształt
        half = len(pts) // 2
        return _simplify(pts[:half + 1], tol)[:-1] + _simplify(pts[half:], tol)
    return _simplify(pts, tol)


def _in_region(ring: list[list[float]]) -> bool:
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    la0, la1, lo0, lo1 = REGION
    return min(lats) <= la1 and max(lats) >= la0 and min(lons) <= lo1 and max(lons) >= lo0


def select_sectors(data: dict, key: str) -> tuple[list[dict], list[dict]]:
    """Sektory i stanowiska z wyniku parse_ese dla pakietu `key` (geometria bez zmian, uproszcza ją generator).

    Wynik: (sektory {fir, name, lower_ft, upper_ft, owners: [znaki], active: [..], border, geometry}, stanowiska
    z kolejności)."""
    firs = set(NB_SOURCES[key]["firs"])
    by_id = {p["position_id"]: p for p in data["positions"]}
    home = [s for s in data["sectors"] if s["fir"].upper() in firs and not _TECH.match(s["name"]) and s["lower_ft"] < 66000]
    chosen = pick_runways(home)
    sectors, used = [], {}
    for s in home:
        if not rwy_active(s["active"], chosen):
            continue
        if not _in_region(json.loads(s["geometry"])["coordinates"][0]):
            continue
        owners = []
        for oid in (o for o in s["owners"].split(":") if o):
            p = by_id.get(oid)
            if p and p["callsign"] not in owners:
                owners.append(p["callsign"])
                if not p["callsign"].upper().startswith("EP"):
                    used[p["callsign"]] = p
        if not owners:  # sektory tylko do wyświetlania (bez OWNER): nikt ich nie obsługuje, zasłaniałyby obsadzone
            continue
        sectors.append({**s, "owners": owners})
    return sectors, list(used.values())


def pack_positions(data: dict, key: str) -> list[dict]:
    """Stanowiska pakietu `key` do tabeli nb_positions (bez EP**): stanowiska lotnisk i FIR-ów pakietu (znak zaczyna
    się od prefiksu z NB_SOURCES[key]["airports"]: EDDF_TWR, EDDL_APP, LKPR_GND) oraz wszystkie z list OWNER sektorów
    FIR-ów pakietu (też spoza nich, np. RU-WRC_FSS w pakiecie ULLL). Kolejność przejmowania, RADIO i MAP biorą
    z tej tabeli tylko stanowiska z list OWNER sektorów sąsiada (services/neighbours.nb_positions), karty lotnisk
    wszystkie."""
    src = NB_SOURCES[key]
    firs, prefixes = set(src["firs"]), tuple(src.get("airports") or ())
    by_id = {p["position_id"]: p for p in data["positions"]}
    owners = {by_id[o]["callsign"] for s in data["sectors"] if s["fir"].upper() in firs
              for o in s["owners"].split(":") if o in by_id}
    return [p for p in data["positions"] if not p["callsign"].upper().startswith("EP")
            and (p["callsign"] in owners or p["callsign"].split("_")[0].upper().startswith(prefixes))]


def import_nb_ese(db: Session, path: Path) -> dict:
    """Stanowiska pakietu sąsiada (tabela nb_positions). Sektory sąsiadów są w data/seed/sectors.geojson."""
    key = source_key(path)
    if not key:
        return {"skipped": f"nieznany pakiet (nazwa pliku powinna zawierać jeden z: {', '.join(NB_SOURCES)})"}
    secs = ese_sections(read_text(path))
    _, sectors = read_airspace(secs.get("[AIRSPACE]", []))
    positions = pack_positions({"positions": parse_positions(secs.get("[POSITIONS]", [])), "sectors": sectors}, key)
    db.execute(delete(NbPosition).where(NbPosition.source == key))
    if positions:
        db.execute(insert(NbPosition), [{"source": key, "callsign": p["callsign"], "name": p["name"],
                                         "frequency": p["frequency"], "prefix": p["prefix"]} for p in positions])
    db.commit()
    return {"source": key, "positions": len(positions)}
