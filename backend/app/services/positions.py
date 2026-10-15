"""Wszystkie stanowiska do RADIO i dopasowania kontrolerów online: plik .ese EPWW, vacs-data, pliki .ese sąsiadów."""

import json
from collections.abc import Collection
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Aerodrome, AtcPosition, NavPoint
from .neighbours import listed_callsigns, nb_owners, nb_positions
from .radio import INFO_MID, Station, merge_positions, neighbour_tab
from .vatsim import match_positions


@lru_cache(maxsize=1)
def vacs_positions() -> list[dict]:
    try:
        return json.loads((settings.seed_dir / "vacs_epww.json").read_text("utf-8")).get("neighbour_positions", [])
    except (OSError, ValueError):
        return []


def all_positions(db: Session, airports: bool = False) -> list[dict]:
    """airports=True: także stanowiska lotnisk z plików sąsiadów, które nie obsługują żadnego sektora (karty lotnisk
    w AERODROME); RADIO, MAP i dopasowanie zalogowanych biorą listę bez nich."""
    ese = [{"callsign": p.callsign, "name": p.name, "frequency": p.frequency, "position_id": p.position_id,
            "prefix": p.prefix} for p in db.scalars(select(AtcPosition))]
    return merge_positions(ese, vacs_positions(), nb_positions(db, airports), listed_callsigns(db))


def online_stations(db: Session, positions: list[dict] | None = None) -> list:
    """Stanowiska do dopasowania zalogowanych (/api/vatsim/online): najpierw plik .ese EPWW (kolejność z bazy), potem
    pozostałe z all_positions (vacs-data, pliki .ese sąsiadów). positions: gotowy wynik all_positions."""
    stations = list(db.scalars(select(AtcPosition)).all())
    known = {p.callsign for p in stations}
    return stations + [Station(p["callsign"], p["prefix"], p["frequency"] or "")
                       for p in (positions if positions is not None else all_positions(db)) if p["callsign"] not in known]


def sim_twins(stations: list, prefer: Collection[str]) -> dict[str, str]:
    """Drugi znak stanowiska: zalogowany na nim kontroler jest w /api/vatsim/online także pod stanowiskiem sąsiada
    z `prefer` o tym samym prefiksie, typie i częstotliwości (alias_of z match_positions, np. EKDK_CTR → EKDK_UN_CTR,
    właściciel sektorów Danii). Symulacja logowania w MAP rysuje go tam tak samo. Najpierw tani filtr (typ
    i częstotliwość jak u któregoś stanowiska z `prefer`), potem match_positions dla jednego kontrolera."""
    def key(s):
        return s.callsign.split("_")[-1], (s.frequency or "")[:7]
    keys = {key(s) for s in stations if s.callsign in prefer}
    out = {}
    for s in stations:
        if s.callsign in prefer or key(s) not in keys:
            continue
        hit = match_positions([{"callsign": s.callsign, "frequency": s.frequency or ""}], stations, prefer)
        out.update({s.callsign: cs for cs, c in hit.items() if c.get("alias_of") == s.callsign})
    return out


# --- drzewo "Pozycje" w MAP (runda 12, Marek): FIR → ACC / FIS / lotniska → stanowiska
# kolejność FIR-ów: najpierw EPWW, potem sąsiedzi (zakładki RADIO; EDGG osobno, choć w vacs-data jest w katalogu EDMM)
TREE_FIRS = ("EPWW", "EDWW", "EDMM", "EDUU", "EDGG", "EKDK", "ESAA", "EYVL", "LKAA", "LZBB", "UKLV", "UMKK", "UMMV", "INNE")
TREE_FIR_NAMES = {"EPWW": "FIR Warszawa", "EDWW": "FIR Bremen", "EDMM": "FIR München", "EDUU": "UIR Rhein, UAC Maastricht",
                  "EDGG": "FIR Langen", "EKDK": "FIR København", "ESAA": "FIR Sverige", "EYVL": "FIR Vilnius",
                  "LKAA": "FIR Praha", "LZBB": "FIR Bratislava", "UKLV": "FIR Lviv", "UMKK": "FIR Kaliningrad",
                  "UMMV": "FIR Minsk", "INNE": "inne"}
TREE_LABELS = {"EDUU": "EDUU/EDYY", "ESAA": "ESAA/ESMM/ESOS"}
# kolejność w grupie lotniska: APP/DEP, TWR, GND, DEL, ATIS, potem reszta (wg nazwy)
TREE_FACILITY_ORDER = {"APP": 0, "DEP": 0, "TWR": 1, "GND": 2, "RMP": 2, "DEL": 3, "ATIS": 4}


def tree_place(p: dict) -> tuple[str, str]:
    """(FIR, grupa) stanowiska w drzewie: grupa "ACC" (CTR/FSS), "FIS" (informacja: EPWA_I_APP, EKDK_I_CTR,
    LKAA_FIC_FSS) albo ICAO lotniska z prefiksu znaku (EPGD_TWR → EPGD; AFIS lotniska, np. EPBC_I_TWR, też tam)."""
    cs = (p.get("callsign") or "").upper()
    parts = cs.split("_")
    pre = (p.get("prefix") or parts[0]).upper()
    tab = neighbour_tab(p)
    # EDGG: ACC z prefiksu (w vacs-data katalog EDMM), lotniska i APP z pakietu EDGG.ese (fir EDGG, zakładka RADIO INNE,
    # np. EDDS_STG_APP, ETHN_TWR); EDUU/EDYY zostają w swojej zakładce
    edgg = pre == "EDGG" or parts[0] == "EDGG" or (tab == "INNE" and (p.get("fir") or "").upper() == "EDGG")
    fir = "EPWW" if tab is None else "EDGG" if edgg else tab
    fac = p.get("facility") or ""
    if fac in ("CTR", "FSS", "APP") and INFO_MID & set(parts[1:-1]):
        return fir, "FIS"
    if fac in ("CTR", "FSS"):
        return fir, "ACC"
    return fir, parts[0]


def position_tree(db: Session) -> dict:
    """Wszystkie stanowiska (all_positions: znak, nazwa, częstotliwość, rodzaj) z polami drzewa: fir, group, order
    (kolejność w grupie lotniska), twin (drugi znak, sim_twins; tylko gdy jest); firs: FIR-y w kolejności drzewa
    z nazwami; airports: nazwa i położenie lotnisk z grup (plakietki symulowanych stanowisk)."""
    allp = all_positions(db)
    twins = sim_twins(online_stations(db, allp), nb_owners(db))
    out = []
    for p in allp:
        fir, group = tree_place(p)
        out.append({k: p.get(k) for k in ("callsign", "name", "frequency", "facility")}
                   | {"fir": fir, "group": group, "order": TREE_FACILITY_ORDER.get(p.get("facility") or "", 5)}
                   | ({"twin": twins[p["callsign"]]} if p["callsign"] in twins else {}))
    icaos = {p["group"] for p in out if p["group"] not in ("ACC", "FIS")}
    airports = {a.icao: {"name": a.name, "lat": a.lat, "lon": a.lon}
                for a in db.scalars(select(Aerodrome).where(Aerodrome.icao.in_(icaos)))}
    rest = icaos - set(airports)
    if rest:
        for n in db.scalars(select(NavPoint).where(NavPoint.kind == "AD", NavPoint.ident.in_(rest))):
            airports.setdefault(n.ident, {"name": n.name, "lat": n.lat, "lon": n.lon})
    firs = [{"id": f, "label": TREE_LABELS.get(f, f), "name": TREE_FIR_NAMES.get(f, "")} for f in TREE_FIRS
            if any(p["fir"] == f for p in out)]
    return {"positions": out, "firs": firs, "airports": airports}
