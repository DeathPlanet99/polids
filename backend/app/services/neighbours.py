"""Sektory i stanowiska FIR-ów sąsiednich z ich własnych plików .ese (tabele nb_sectors, nb_positions).

FIR-y, które mają własny plik, zastępują kopie ich sektorów z pliku EPWW (wycinki "nb" wzdłuż granicy);
Białoruś (UMMV) zostaje z pliku EPWW."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..importers.neighbours import NB_SOURCES
from ..models import NbPosition, NbSector


def covered_firs(db: Session) -> set[str]:
    """FIR-y z sektorami z plików sąsiadów (ich kopie z pliku EPWW pomijamy)."""
    return {f.upper() for f in db.scalars(select(NbSector.fir).distinct())}


def nb_positions(db: Session, airports: bool = False) -> list[dict]:
    """Stanowiska z plików sąsiadów (jedno na znak, pliki w kolejności NB_SOURCES): tylko te z kolejności przejmowania
    sektorów sąsiada z tego samego pliku (RADIO, MAP, dopasowanie zalogowanych), z airports=True wszystkie zapisane –
    także stanowiska lotnisk bez sektora (EDDF_TWR, EDDF_GND) do kart lotnisk w AERODROME."""
    order = {k: i for i, k in enumerate(NB_SOURCES)}
    listed: dict[str, set[str]] = {}
    if not airports:
        for src, owners in db.execute(select(NbSector.source, NbSector.owners)):
            listed.setdefault(src, set()).update(o for o in (owners or "").split(":") if o)
    out: dict[str, dict] = {}
    for p in sorted(db.scalars(select(NbPosition)), key=lambda p: (order.get(p.source, 99), p.callsign)):
        if not airports and p.callsign not in listed.get(p.source, ()):
            continue
        out.setdefault(p.callsign, {"callsign": p.callsign, "name": p.name, "frequency": p.frequency or "",
                                    "prefix": p.prefix, "source": p.source, "fir": NB_SOURCES.get(p.source, {}).get("vacs")})
    return list(out.values())


def nb_sectors(db: Session, level_ft: int | None = None) -> list[NbSector]:
    stmt = select(NbSector).order_by(NbSector.id)
    if level_ft is not None:
        stmt = stmt.where(NbSector.lower_ft <= level_ft, NbSector.upper_ft > level_ft)
    return list(db.scalars(stmt))


def owners_of(s: NbSector) -> list[str]:
    return [o for o in (s.owners or "").split(":") if o]


def listed_callsigns(db: Session) -> set[str]:
    """Znaki stanowisk z list OWNER sektorów sąsiadów (te pozycje mają dokładny zasięg z plików sąsiadów)."""
    return {cs for s in db.scalars(select(NbSector.owners)) for cs in (s or "").split(":") if cs}


def nb_owners(db: Session) -> set[str]:
    """Stanowiska sąsiadów z ich list OWNER (bez EP**): do dopasowania zalogowanych jak w EuroScope sąsiada."""
    return {cs for cs in listed_callsigns(db) if not cs.upper().startswith("EP")}
