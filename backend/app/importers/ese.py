"""Plik .ese EuroScope: stanowiska ATC ([POSITIONS]) i sektory ([AIRSPACE]).

Sektor składa się z linii granicznych (SECTORLINE, CIRCLE_SECTORLINE) wymienionych w BORDER; łączymy je w wielokąt.
Właścicielem sektora jest pierwsze zalogowane stanowisko z listy OWNER.
Przy starcie aplikacji z pliku EPWW wczytujemy tylko stanowiska; sektory są w data/seed/sectors.geojson
(generator: sectors_geojson.py, który używa parse_ese z tego modułu)."""

import json
import math
import re
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from ..models import AtcPosition
from .sct import parse_coord, read_text


def parse_positions(lines: list[str]) -> list[dict]:
    out = {}
    for line in lines:
        p = line.split(":")
        if len(p) < 7 or not p[0]:
            continue
        out[p[0]] = {"callsign": p[0], "name": p[1], "frequency": p[2], "position_id": p[3],
                     "suffix": p[4] or None, "prefix": p[5] or None}
    return list(out.values())


def _chain(lines: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Łączy linie graniczne w zamknięty pierścień, odwracając je w razie potrzeby.

    Linie o zerowej długości (wszystkie punkty w jednym miejscu, np. 861 i 862 w UKLV_FIR) pomijamy:
    nie wnoszą geometrii, a doklejone na końcu jako "najbliższe" przecinały cały sektor fałszywymi krawędziami."""
    close = lambda a, b: abs(a[0] - b[0]) < 1e-4 and abs(a[1] - b[1]) < 1e-4  # noqa: E731
    lines = [ln for ln in lines if ln and any(not close(p, ln[0]) for p in ln)]
    if not lines:
        return []
    ring = list(lines.pop(0))
    while lines:
        end = ring[-1]
        for i, ln in enumerate(lines):
            if close(ln[0], end):
                ring.extend(ln[1:])
                break
            if close(ln[-1], end):
                ring.extend(list(reversed(ln))[1:])
                break
        else:
            # brak styku: dołącz najbliższą linię, żeby nie zgubić geometrii
            ln = min(lines, key=lambda ln: min(abs(ln[0][0] - end[0]) + abs(ln[0][1] - end[1]),
                                               abs(ln[-1][0] - end[0]) + abs(ln[-1][1] - end[1])))
            i = lines.index(ln)
            if abs(ln[-1][0] - end[0]) + abs(ln[-1][1] - end[1]) < abs(ln[0][0] - end[0]) + abs(ln[0][1] - end[1]):
                ln = list(reversed(ln))
            ring.extend(ln)
        lines.pop(i)
    if not close(ring[0], ring[-1]):
        ring.append(ring[0])
    return ring


# nazwa sektora "FIR·NAZWA·DOL·GORA" (PL, DE, SE…) albo ze spacjami "LKAA NL 125 305" (pakiet czeski)
_NAME_SPACED = re.compile(r"^(\S+) (.+) (\d{3}) (\d{3})$")


def split_sector_name(name: str) -> tuple[str, str]:
    """(FIR, nazwa) z nazwy sektora .ese: "EPWW·EPWWB·095·245" → ("EPWW", "EPWWB"), "LKAA NL 125 305" → ("LKAA", "NL")."""
    parts = [x.strip() for x in re.split(r"[\u00a7\u00b7]", name)]
    if len(parts) > 1:
        return parts[0], parts[1]
    if m := _NAME_SPACED.match(name.strip()):
        return m.group(1), m.group(2).strip()
    return name, name


def _circle(lat: float, lon: float, nm: float, n: int = 72) -> list[tuple[float, float]]:
    """Okrąg o promieniu nm wokół punktu jako zamknięta łamana (CIRCLE_SECTORLINE)."""
    dlat = nm / 60
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    pts = [(lat + dlat * math.cos(2 * math.pi * i / n), lon + dlon * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + pts[:1]


def read_airspace(lines: list[str], airport: Callable[[str], tuple[float, float] | None] | None = None
                  ) -> tuple[dict[str, list[tuple[float, float]]], list[dict]]:
    """Linie graniczne i sektory z sekcji [AIRSPACE], jeszcze bez sklejania w wielokąty.

    Wynik: ({nazwa linii: [(lat, lon), ...]}, [{fir, name, lower_ft, upper_ft, owners (ID stanowisk ":"), active,
    border (nazwy linii z BORDER)}]). active: warunki ACTIVE:ICAO:pas (sektor istnieje tylko przy tym pasie w użyciu),
    lista "ICAO:pas". airport: współrzędne lotniska dla CIRCLE_SECTORLINE:nazwa:ICAO:promień (bez niej takie okręgi
    pomijamy)."""
    sectorlines: dict[str, list[tuple[float, float]]] = {}
    sectors = []
    cur_line = None
    cur_sector = None
    for line in lines:
        key, _, rest = line.partition(":")
        if key == "SECTORLINE":
            cur_line = rest.strip()
            sectorlines[cur_line] = []
            cur_sector = None
            continue
        if key == "COORD":
            if cur_line is not None:
                lat_s, _, lon_s = rest.partition(":")
                lat, lon = parse_coord(lat_s), parse_coord(lon_s)
                if lat is not None and lon is not None:
                    sectorlines[cur_line].append((lat, lon))
            continue
        if key != "DISPLAY":
            # COORD należą tylko do bieżącej SECTORLINE (bloki MSAW mają własne COORD, które nie są granicą)
            cur_line = None
        if key == "CIRCLE_SECTORLINE":
            f = [x.strip() for x in rest.split(":")]
            center = None
            if len(f) >= 4 and parse_coord(f[1]) is not None and parse_coord(f[2]) is not None:
                center, radius = (parse_coord(f[1]), parse_coord(f[2])), f[3]
            elif len(f) >= 3 and airport:
                center, radius = airport(f[1].upper()), f[2]
            try:
                if center:
                    sectorlines[f[0]] = _circle(center[0], center[1], float(radius))
            except ValueError:
                pass
        elif key == "SECTOR":
            name, lower, upper = (rest.split(":") + ["0", "0"])[:3]
            fir, short = split_sector_name(name)
            cur_sector = {"fir": fir, "name": short, "lower_ft": int(lower or 0), "upper_ft": int(upper or 0),
                          "owners": "", "active": [], "border": []}
            sectors.append(cur_sector)
        elif key == "OWNER" and cur_sector:
            cur_sector["owners"] = rest.strip()
        elif key == "BORDER" and cur_sector:
            cur_sector["border"] = [b for b in rest.strip().split(":") if b]
        elif key == "ACTIVE" and cur_sector:
            icao, _, rwy = rest.strip().partition(":")
            if icao:
                cur_sector["active"].append(f"{icao.upper()}:{rwy.split(':')[0].upper()}")
    return sectorlines, sectors


def sector_ring(sectorlines: dict[str, list[tuple[float, float]]], border: list[str]) -> list[tuple[float, float]]:
    """Zamknięty pierścień (lat, lon) sektora z jego linii BORDER ([] = nie da się go złożyć)."""
    return _chain([sectorlines.get(b, []) for b in border])


def assemble(sectorlines: dict[str, list[tuple[float, float]]], sectors: list[dict]) -> list[dict]:
    """Sektory z read_airspace z geometrią (GeoJSON Polygon jako tekst, lon/lat do 5 miejsc); bez wielokąta pomijamy."""
    out = []
    for s in sectors:
        ring = sector_ring(sectorlines, s["border"])
        if len(ring) < 4:
            continue
        out.append({**s, "geometry": json.dumps({"type": "Polygon", "coordinates": [[[round(lon, 5), round(lat, 5)]
                                                                                      for lat, lon in ring]]})})
    return out


def ese_sections(text: str) -> dict[str, list[str]]:
    """Sekcje pliku .ese: {"[POSITIONS]": [wiersze], ...} bez komentarzy i pustych wierszy."""
    secs: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith(";"):
            continue
        line = line.rstrip()
        if line.startswith("["):
            current = line.strip().upper()
            secs.setdefault(current, [])
        elif current and line:
            secs[current].append(line)
    return secs


def parse_ese(text: str, airport: Callable[[str], tuple[float, float] | None] | None = None) -> dict:
    """Stanowiska, sektory (z geometrią) i linie graniczne (sectorlines: nazwa → [(lat, lon)]) pliku .ese."""
    secs = ese_sections(text)
    sectorlines, sectors = read_airspace(secs.get("[AIRSPACE]", []), airport)
    return {"positions": parse_positions(secs.get("[POSITIONS]", [])), "sectors": assemble(sectorlines, sectors),
            "sectorlines": sectorlines}


def import_ese(db: Session, path: Path) -> dict:
    """Stanowiska ATC z pliku .ese EPWW. Sektory (tabela sectors) wczytujemy z data/seed/sectors.geojson."""
    positions = parse_positions(ese_sections(read_text(path)).get("[POSITIONS]", []))
    db.execute(delete(AtcPosition))
    if positions:
        db.execute(insert(AtcPosition), positions)
    db.commit()
    return {"positions": len(positions)}


def parse_vfr_points(text: str) -> list[dict]:
    """Punkty VFR z sekcji [FREETEXT] (grupy "EPWA VFR", "EPKK VFR"…): N052.03.47.000:E020.44.35.000:EPBC VFR:A"""
    out, in_ft = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_ft = line.upper() == "[FREETEXT]"
            continue
        if not in_ft or not line or line.startswith(";"):
            continue
        parts = line.split(":")
        if len(parts) < 4 or not parts[2].upper().endswith(" VFR"):
            continue
        lat, lon = parse_coord(parts[0]), parse_coord(parts[1])
        if lat is None or lon is None:
            continue
        ad = parts[2].split()[0].upper()
        out.append({"ident": parts[3].strip(), "kind": "VFR", "name": f"{ad} VFR", "frequency": None,
                    "lat": lat, "lon": lon})
    return out
