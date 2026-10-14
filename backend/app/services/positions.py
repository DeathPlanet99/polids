"""Wszystkie stanowiska do RADIO i dopasowania kontrolerów online: plik .ese EPWW, vacs-data, pliki .ese sąsiadów."""

import json
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Aerodrome, AtcPosition, NavPoint
from .neighbours import listed_callsigns, nb_positions
from .radio import INFO_MID, merge_positions, neighbour_tab


@lru_cache(maxsize=1)
def vacs_positions() -> list[dict]:
    try:
        return json.loads((settings.seed_dir / "vacs_epww.json").read_text("utf-8")).get("neighbour_positions", [])
    except (OSError, ValueError):
        return []


def all_positions(db: Session) -> list[dict]:
    ese = [{"callsign": p.callsign, "name": p.name, "frequency": p.frequency, "position_id": p.position_id,
            "prefix": p.prefix} for p in db.scalars(select(AtcPosition))]
    return merge_positions(ese, vacs_positions(), nb_positions(db), listed_callsigns(db))


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
    fir = "EPWW" if tab is None else "EDGG" if pre == "EDGG" or parts[0] == "EDGG" else tab
    fac = p.get("facility") or ""
    if fac in ("CTR", "FSS", "APP") and INFO_MID & set(parts[1:-1]):
        return fir, "FIS"
    if fac in ("CTR", "FSS"):
        return fir, "ACC"
    return fir, parts[0]


def position_tree(db: Session) -> dict:
    """Wszystkie stanowiska (all_positions: znak, nazwa, częstotliwość, rodzaj) z polami drzewa: fir, group, order
    (kolejność w grupie lotniska); firs: FIR-y w kolejności drzewa z nazwami; airports: nazwa i położenie lotnisk
    z grup (plakietki symulowanych stanowisk)."""
    out = []
    for p in all_positions(db):
        fir, group = tree_place(p)
        out.append({k: p.get(k) for k in ("callsign", "name", "frequency", "facility")}
                   | {"fir": fir, "group": group, "order": TREE_FACILITY_ORDER.get(p.get("facility") or "", 5)})
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
