"""Wypełnianie bazy: dane startowe z data/seed/ (wersjonowane w repo) oraz pliki
z data/import/ (pliki sektorowe i navdata EuroScope).

Uruchamiane automatycznie przy starcie aplikacji. Ręcznie:
    python -m backend.app.importers.seed            # import nowych/zmienionych plików
    python -m backend.app.importers.seed --force    # wszystko od nowa
"""

import csv
import logging
import sys
import time
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..database import SCHEMA_VERSION, Base, SessionLocal, engine
from ..models import AircraftType, Aerodrome, Callsign, Document, Frequency, ImportLog, NavPoint, NbPosition, NbSector, Runway
from .aircraft_json import import_aircraft_json
from .callsigns import import_gr_operator_info, import_icao_airlines
from .ese import import_ese
from .neighbours import import_nb_ese, source_key
from .navdata import import_airways, import_icao_airports, import_isec
from .sct import import_sct

log = logging.getLogger("polids.seed")
IMPORT_DIR = DATA_DIR / "import"
NB_DIR = IMPORT_DIR / "neighbours"  # pliki .ese FIR-ów sąsiednich (osobny import, nie nadpisują EPWW)

# (dopasowanie końcówki nazwy pliku, funkcja importu); kolejność ma znaczenie
IMPORTERS = [
    ("isec.txt", import_isec),
    ("icao.txt", import_icao_airports),
    ("airway.txt", import_airways),
    (".sct", import_sct),
    (".sct2", import_sct),
    (".ese", import_ese),
    ("icao_airlines.txt", import_icao_airlines),
    ("grpluginoperatorinfo.txt", import_gr_operator_info),
    ("icao_aircraft.json", import_aircraft_json),
]


def _csv(name: str):
    path = settings.seed_dir / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if not next(iter(r.values()), "").startswith("#")]


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def seed_aerodromes(db: Session):
    """Lotniska (airports.csv z OurAirports, elewacja z AIP albo z lotniska.dlapilota.pl), pasy i częstotliwości z AIP
    (runways.csv w metrach, frequencies.csv z godzinami i uwagą, ze źródłem w każdym wierszu) oraz pomoce nawigacyjne
    (navaids.csv). Pasy i częstotliwości lotniska, którego nie ma w airports.csv (np. po aktualizacji z OurAirports),
    pomijamy z ostrzeżeniem: klucz obcy nie może zatrzymać startu aplikacji."""
    known = set()
    for a in _csv("airports.csv"):
        db.add(Aerodrome(icao=a["ident"], name=a["name"], kind=a["type"], lat=float(a["latitude_deg"]),
                         lon=float(a["longitude_deg"]), elevation_ft=_f(a["elevation_ft"]),
                         city=a["municipality"] or None, iata=a["iata_code"] or None))
        known.add(a["ident"])
    db.flush()

    def orphan(name: str, r: dict) -> bool:
        if r["airport_ident"] in known:
            return False
        log.warning("%s: lotniska %s nie ma w airports.csv, pomijam wiersz", name, r["airport_ident"])
        return True

    for r in _csv("runways.csv"):
        if orphan("runways.csv", r):
            continue
        common = {"aerodrome_icao": r["airport_ident"], "surface": r["surface"] or None,
                  "length_m": _f(r["length_m"]), "width_m": _f(r["width_m"])}
        for end, other in (("le", "he"), ("he", "le")):
            if r[f"{end}_ident"]:
                db.add(Runway(designator=r[f"{end}_ident"], opposite=r[f"{other}_ident"] or None,
                              heading_true=_f(r[f"{end}_heading_degT"]), lat=_f(r[f"{end}_latitude_deg"]),
                              lon=_f(r[f"{end}_longitude_deg"]), **common))
    for fq in _csv("frequencies.csv"):
        if orphan("frequencies.csv", fq):
            continue
        db.add(Frequency(aerodrome_icao=fq["airport_ident"], kind=fq["service"], description=fq["callsign"] or None,
                         mhz=fq["frequency_mhz"], hours=fq["hours"] or None, note=fq.get("note") or None,
                         source=fq["source"] or None))
    for n in _csv("navaids.csv"):
        khz = _f(n["frequency_khz"])
        freq = None if khz is None else (f"{khz / 1000:.3f}" if n["type"] in ("VOR", "VOR-DME", "VORTAC", "DME")
                                         else f"{khz:g}")
        db.add(NavPoint(ident=n["ident"], kind=n["type"], name=n["name"], frequency=freq,
                        lat=float(n["latitude_deg"]), lon=float(n["longitude_deg"]), source="ourairports"))


def seed_aircraft(db: Session):
    ints = {"engine_count"}
    floats = {"wingspan", "length", "height", "mtow", "mlw", "ceiling_ft", "vmo_kt", "mmo"}
    for r in _csv("aircraft.csv"):
        vals = {}
        for k, v in r.items():
            if k in ints:
                vals[k] = int(v) if v else None
            elif k in floats:
                vals[k] = _f(v)
            else:
                vals[k] = v or None
        db.add(AircraftType(source="aircraft-database.com", **vals))


def seed_callsigns(db: Session):
    for r in _csv("callsigns.csv"):
        db.merge(Callsign(source="seed", **r))


def register_docs(db: Session):
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    known = set(db.scalars(select(Document.filename)))
    for p in sorted(settings.docs_dir.glob("**/*.pdf")):
        rel = p.relative_to(settings.docs_dir).as_posix()
        if rel in known:
            continue
        stem = p.stem.upper()
        cat = "ICAO" if stem.startswith(("DOC", "ICAO")) else ("EUROCONTROL" if "EUROCONTROL" in stem else "INNE")
        if "/" in rel:
            cat = rel.split("/")[0].upper()
        db.add(Document(title=p.stem.replace("_", " ").replace("-", " "), category=cat, filename=rel))
    # usuń wpisy, których pliki zniknęły
    for d in db.scalars(select(Document)).all():
        if not (settings.docs_dir / d.filename).exists():
            db.delete(d)
    db.commit()


def _import_file(db: Session, p: Path, fn, name: str, force: bool) -> dict | None:
    """Import jednego pliku, jeśli zmienił się od ostatniego razu (ImportLog wg nazwy `name`)."""
    mtime = p.stat().st_mtime
    logged = db.get(ImportLog, name)
    if logged and logged.mtime == mtime and not force:
        return None
    t0 = time.time()
    try:
        res = fn(db, p)
    except Exception as exc:  # jeden zły plik nie może zablokować startu aplikacji
        db.rollback()
        log.exception("Import %s nieudany", name)
        res = {"error": str(exc)}
    log.info("Import %s: %s (%.1fs)", name, res, time.time() - t0)
    # nieudany import zapisujemy z mtime=0, żeby przy kolejnym starcie spróbować ponownie
    db.merge(ImportLog(filename=name, mtime=0 if "error" in res else mtime, result=str(res)))
    db.commit()
    return {"file": name, **res}


def import_neighbours(db: Session, force: bool = False) -> list[dict]:
    """Pliki .ese sąsiadów z data/import/neighbours/; sektory pakietów, których pliku już nie ma, usuwamy."""
    files = sorted(p for p in NB_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".ese") if NB_DIR.is_dir() else []
    results = [r for p in files if (r := _import_file(db, p, import_nb_ese, f"neighbours/{p.name}", force))]
    keys = {k for p in files if (k := source_key(p))}
    db.execute(delete(NbSector).where(NbSector.source.not_in(keys)))
    db.execute(delete(NbPosition).where(NbPosition.source.not_in(keys)))
    # zapomnij usunięte pliki: przywrócony plik (z tą samą datą modyfikacji) wczyta się ponownie
    db.execute(delete(ImportLog).where(ImportLog.filename.like("neighbours/%"),
                                       ImportLog.filename.not_in([f"neighbours/{p.name}" for p in files])))
    db.commit()
    return results


def import_user_files(db: Session, force: bool = False) -> list[dict]:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    files = [p for p in IMPORT_DIR.iterdir() if p.is_file()]
    for suffix, fn in IMPORTERS:
        for p in sorted(files):
            if not p.name.lower().endswith(suffix):
                continue
            if suffix == ".sct" and p.name.lower().endswith(".sct2"):
                continue
            if res := _import_file(db, p, fn, p.name, force):
                results.append(res)
    # sąsiedzi po lotniskach i navdata (współrzędne okręgów CIRCLE_SECTORLINE)
    return results + import_neighbours(db, force)


def _check_schema():
    """Baza ze starszej wersji aplikacji (inne kolumny) jest usuwana i budowana od nowa."""
    with engine.connect() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar() if engine.dialect.name == "sqlite" else None
    if version is not None and version != SCHEMA_VERSION:
        if version:
            log.info("Zmiana struktury bazy (%s -> %s): przebudowa bazy", version, SCHEMA_VERSION)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_db(force: bool = False) -> list[dict]:
    _check_schema()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if force:
            for model in (Runway, Frequency, Aerodrome, AircraftType, Callsign, ImportLog):
                db.execute(delete(model))
            db.execute(delete(NavPoint).where(NavPoint.source == "ourairports"))
            db.commit()
        if not db.scalar(select(func.count()).select_from(Aerodrome)):
            seed_aerodromes(db)
        if not db.scalar(select(func.count()).select_from(AircraftType)):
            seed_aircraft(db)
        if not db.scalar(select(func.count()).select_from(Callsign)):
            seed_callsigns(db)
        db.commit()
        results = import_user_files(db, force=force)
        register_docs(db)
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for r in init_db(force="--force" in sys.argv):
        print(r)
