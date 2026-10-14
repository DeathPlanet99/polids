import json
import re
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..database import get_db
from ..importers.ese import parse_vfr_points
from ..importers.sectors_geojson import NO_SECTORS
from ..importers.sct import parse_line_groups, read_text, sections
from ..models import AirwaySegment, AtcPosition, NavPoint, Sector
from ..services.http_cache import UpstreamError
from ..services.neighbours import covered_firs, nb_positions, nb_sectors, owners_of
from ..services.positions import position_tree
from ..services.route import RouteResolver
from ..services.vatsim import get_feed, match_positions

router = APIRouter(prefix="/api/nav", tags=["map"])

# Granice TMA i CTR z pliku .sct, tak jak rysuje je EuroScope: TMA = "EPxx TMA OUT/IN" z [ARTCC] i [ARTCC LOW],
# CTR = "ZZ_CTR ALL" z [ARTCC LOW].
AIRSPACE_KINDS = {
    "tma": lambda name: " TMA " in f" {name} " or "MTMA" in name,
    "ctr": lambda name: "CTR" in name.split()[0] and not name.startswith("AoR"),
}


@lru_cache(maxsize=1)
def sct_line_groups() -> dict[str, list]:
    out: dict[str, list] = {}
    for f in sorted((DATA_DIR / "import").glob("*.sct")):
        secs = sections(read_text(f))
        for key in ("[ARTCC]", "[ARTCC LOW]"):
            for name, segs in parse_line_groups(secs.get(key, [])).items():
                out.setdefault(name, []).extend(segs)
    return out


@router.get("/airspace")
def airspace(kind: str = Query("tma", pattern="^(tma|ctr)$")):
    """Linie granic TMA albo CTR z pliku .sct jako GeoJSON (MultiLineString, współrzędne lon/lat).

    `inner` = linie wewnętrzne podziału TMA (np. "EPBY TMA IN"), rysowane cieniej."""
    feats = []
    for name, segs in sct_line_groups().items():
        if not AIRSPACE_KINDS[kind](name):
            continue
        feats.append({"type": "Feature", "properties": {"name": name, "inner": name.endswith(" IN")},
                      "geometry": {"type": "MultiLineString",
                                   "coordinates": [[[a[1], a[0]], [b[1], b[0]]] for a, b in segs]}})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/route")
def route(route: str = Query(..., min_length=3), db: Session = Depends(get_db)):
    return RouteResolver(db).resolve(route)


def _bbox(bbox: str):
    try:
        s, w, n, e = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(400, "bbox = poludnie,zachod,polnoc,wschod") from exc
    if (n - s) * (e - w) > 400:
        raise HTTPException(400, "Za duży obszar, przybliż mapę")
    return s, w, n, e


@lru_cache(maxsize=1)
def vfr_points() -> list[dict]:
    out = []
    for f in sorted((DATA_DIR / "import").glob("*.ese")):
        out += parse_vfr_points(read_text(f))
    return out


@router.get("/points")
def points(bbox: str, kinds: str = "VOR,NDB,VOR-DME,DME,VORTAC,NDB-DME,FIX", limit: int = Query(3000, ge=1, le=20000),
           db: Session = Depends(get_db)):
    """Punkty nawigacyjne w obszarze. Rodzaj VFR = punkty meldowania VFR z pliku .ese (sekcja FREETEXT)."""
    s, w, n, e = _bbox(bbox)
    if "VFR" in kinds.split(","):
        vfr = [p for p in vfr_points() if s <= p["lat"] <= n and w <= p["lon"] <= e]
        rest = ",".join(k for k in kinds.split(",") if k != "VFR")
        return vfr + (points(bbox, rest, limit, db) if rest else [])
    stmt = (select(NavPoint)
            .where(NavPoint.lat.between(s, n), NavPoint.lon.between(w, e), NavPoint.kind.in_(kinds.split(",")))
            .limit(limit))
    # Ten sam punkt bywa w kilku źródłach (np. NDB "NO" z pliku .sct i "N" z OurAirports w tym samym miejscu).
    # Pierwszeństwo ma plik sektorowy (aktualny AIRAC), brakującą nazwę/częstotliwość bierzemy z OurAirports.
    prio = {"sct": 0, "ourairports": 1}
    family = lambda k: "NDB" if "NDB" in k else "VOR" if k in ("VOR", "VOR-DME", "DME", "VORTAC") else k  # noqa: E731
    seen: dict[tuple, dict] = {}
    out = []
    for p in sorted(db.scalars(stmt), key=lambda p: prio.get(p.source, 2)):
        keys = [(p.ident, round(p.lat, 1), round(p.lon, 1))]
        if family(p.kind) in ("VOR", "NDB"):
            keys.append((family(p.kind), round(p.lat, 2), round(p.lon, 2)))
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit:
            hit["name"] = hit["name"] or p.name
            hit["frequency"] = hit["frequency"] or p.frequency
            continue
        item = {"ident": p.ident, "kind": p.kind, "name": p.name, "frequency": p.frequency, "lat": p.lat, "lon": p.lon}
        for k in keys:
            seen[k] = item
        out.append(item)
    return out


@router.get("/airways")
def airways(bbox: str, limit: int = Query(5000, ge=1, le=20000), db: Session = Depends(get_db)):
    s, w, n, e = _bbox(bbox)
    stmt = (select(AirwaySegment)
            .where(AirwaySegment.from_lat.between(s, n), AirwaySegment.from_lon.between(w, e))
            .limit(limit))
    return [{"airway": a.airway, "level": a.level, "from": a.from_ident, "to": a.to_ident,
             "coords": [[a.from_lat, a.from_lon], [a.to_lat, a.to_lon]]} for a in db.scalars(stmt)]


def _positions(db: Session) -> dict[str, AtcPosition]:
    return {p.position_id: p for p in db.scalars(select(AtcPosition))}


@router.get("/positions")
def positions(db: Session = Depends(get_db)):
    """Stanowiska ATC z pliku .ese (zakładka RADIO)."""
    return [{"callsign": p.callsign, "name": p.name, "frequency": p.frequency, "position_id": p.position_id,
             "prefix": p.prefix} for p in db.scalars(select(AtcPosition).order_by(AtcPosition.callsign))]


@router.get("/positions/all")
def positions_all(db: Session = Depends(get_db)):
    """Wszystkie stanowiska (plik .ese EPWW, vacs-data, pliki .ese sąsiadów) do drzewa "Pozycje" w MAP:
    fir (EPWW albo FIR sąsiada), group (ACC, FIS albo ICAO lotniska), order (APP, TWR, GND, DEL, ATIS);
    firs: FIR-y w kolejności drzewa; airports: nazwa i położenie lotnisk z grup."""
    return position_tree(db)


@router.get("/sectors")
def sectors(fir: str = "EPWW", level_ft: int | None = Query(None, ge=-2000, le=100000), db: Session = Depends(get_db)):
    """Sektory jednego FIR-u z pliku EPWW (data/seed/sectors.geojson, source EPWW) jako GeoJSON. Bez sektorów
    (brak pliku) pusta lista z `note`."""
    stmt = select(Sector).where(Sector.fir == fir.upper())
    if level_ft is not None:
        stmt = stmt.where(Sector.lower_ft <= level_ft, Sector.upper_ft > level_ft)
    rows = db.scalars(stmt).all()
    pos = _positions(db)
    feats = []
    for s in rows:
        owners = [o for o in s.owners.split(":") if o]
        first = pos.get(owners[0]) if owners else None
        feats.append({"type": "Feature", "geometry": json.loads(s.geometry), "properties": {
            "name": s.name, "lower_ft": s.lower_ft, "upper_ft": s.upper_ft, "owners": owners,
            "callsign": first.callsign if first else None, "frequency": first.frequency if first else None,
        }})
    out = {"type": "FeatureCollection", "features": feats}
    if not feats and level_ft is None:
        out["note"] = NO_SECTORS
    return out


@lru_cache(maxsize=1)
def _vacs() -> dict:
    return json.loads((settings.seed_dir / "vacs_epww.json").read_text("utf-8"))


@router.get("/vacs")
def vacs():
    """Wyciąg z vacs-data (CC BY-NC-SA 4.0): łańcuchy dziedziczenia sektorów EPWW w warstwach LOW/MID/HIGH,
    stanowiska ACC i etykiety stanowisk sąsiadów z profilu ACC_EPWW."""
    return _vacs()


@lru_cache(maxsize=1)
def _ownership() -> dict:
    return json.loads((settings.seed_dir / "ownership.json").read_text("utf-8"))


@router.get("/ownership")
def ownership():
    """Kolejność przejmowania sektorów ACC EPWW w warstwach LOW/MID/HIGH (pole `source` mówi, skąd jest:
    tymczasowo listy OWNER z pliku .ese, docelowo tabela z om.plvacc.pl/docs/2610/ownerships)."""
    return _ownership()


@router.get("/sectors/online")
async def sectors_online(fir: str = "EPWW", db: Session = Depends(get_db)):
    """Aktualna sektoryzacja: dla każdego sektora pierwsze zalogowane stanowisko z listy OWNER."""
    try:
        data = await get_feed()
    except (UpstreamError, ValueError) as exc:
        raise HTTPException(502, f"VATSIM data feed niedostępny: {exc}") from exc
    positions = db.scalars(select(AtcPosition)).all()
    online = match_positions(data.get("controllers", []), positions)
    by_id = {p.position_id: online[p.callsign] | {"position": p.callsign}
             for p in positions if p.callsign in online}
    result = {}
    for s in db.scalars(select(Sector).where(Sector.fir == fir.upper())):
        for o in s.owners.split(":"):
            if o in by_id:
                result[s.name] = {"position_id": o, **by_id[o]}
                break
    return {"online_positions": list(by_id.values()), "sector_owner": result,
            "updated": data.get("general", {}).get("update_timestamp")}


# --- MAP: warstwy przestrzeni wg poziomu. Rodzaj przestrzeni rozpoznajemy po nazwie wycinka sektora z pliku .ese.
# Granice pionowe też z pliku .ese: eAIP PANSA (ENR 2.1) nie był osiągalny tam, gdzie powstawał ten kod
# (docs.pansa.pl zablokowany), więc limity nie zostały z nim uzgodnione.
# TMA to nie tylko "*_TMA*": TMA Warszawa jest w pliku .ese jako EPWA_APP_* i EPWA_DIR*, TMA Kraków/Katowice
# zawiera też wycinki EPKK_DEP07 / EPKT_DIR08 (stąd w rundzie 7 TMA Warszawa nie podświetlała się przy EPWA_APP).
_ACC = re.compile(r"^EPWW([A-Z])(?:-[A-Z]+)?$")
_CTR = re.compile(r"^(EP[A-Z]{2})_M?CTR\d*$")
_TMA = re.compile(r"^(EP[A-Z]{2})_(?:[A-Z_]*TMA|APP_|DEP\d|DIR\d)")
_ATZ = re.compile(r"^(EP[A-Z]{2})_ATZ")
_TRA = re.compile(r"^EPTR\d+[A-Z]?$")
_FIS = re.compile(r"^(?:FIS_([A-Z]{3})|([A-Z]{3})_FIS)_")
_CTA = re.compile(r"^(CTA\d+)")
# TMA Poznań dzieli się na N i S z osobną kolejnością przejmowania (klucze tma_topdown w ownership.json)
_TMA_PART = re.compile(r"^EP[A-Z]{2}_TMA_([NS])_")
_TECH = re.compile(r"^(?:TECH |T-)")  # wycinki techniczne sąsiadów (np. TECH ESMM IS na FL998)

EP_CITY = {
    "EPWA": "Warszawa", "EPKK": "Kraków", "EPGD": "Gdańsk", "EPPO": "Poznań", "EPKT": "Katowice", "EPWR": "Wrocław",
    "EPLL": "Łódź", "EPLB": "Lublin", "EPRZ": "Rzeszów", "EPSC": "Szczecin", "EPSY": "Olsztyn-Mazury",
    "EPBY": "Bydgoszcz", "EPZG": "Zielona Góra", "EPMO": "Modlin", "EPRA": "Radom", "EPDE": "Dęblin",
    "EPKS": "Poznań-Krzesiny", "EPBA": "Bielsko-Biała", "EPBC": "Warszawa-Babice", "EPGL": "Gliwice",
    "EPKA": "Kielce-Masłów", "EPKM": "Katowice-Muchowiec", "EPKP": "Kraków-Pobiednik", "EPKR": "Krosno",
    "EPLR": "Lublin-Radawiec", "EPML": "Mielec", "EPNT": "Nowy Targ", "EPOD": "Olsztyn-Dajtki", "EPPL": "Płock",
    "EPPT": "Piotrków Trybunalski", "EPZR": "Żar",
}
FIS_REGION = {"WAW": "Warszawa", "GDN": "Gdańsk", "KRK": "Kraków", "POZ": "Poznań"}
NB_FIR = {"EDGG": "Langen", "EDMM": "München", "EDWW": "Bremen", "EKDK": "København", "ESAA": "Sverige",
          "EYVL": "Vilnius", "LKAA": "Praha", "LZBB": "Bratislava", "UKLV": "Lviv", "UMKK": "Kaliningrad",
          "UMMV": "Minsk"}


def slice_kind(fir: str, name: str) -> tuple[str, str] | None:
    """Rodzaj przestrzeni i grupa wycinka sektora .ese: (acc, litera) | (tma|ctr|atz, ICAO) | (fis, rejon)
    | (cta, CTAnn) | (nb, FIR sąsiedni). None = wycinek techniczny, nie rysujemy go."""
    fir, name = fir.upper(), name.upper()
    if fir != "EPWW":
        return None if _TECH.match(name) else ("nb", fir)
    if m := _ACC.match(name):
        return "acc", m.group(1)
    if name == "EPWW-MIDSEA":  # FIS nad Bałtykiem (Gdańsk Information)
        return "fis", "GDN"
    if m := _CTR.match(name):
        return "ctr", m.group(1)
    if m := _TMA.match(name):
        return "tma", m.group(1)
    if m := _ATZ.match(name):
        return "atz", m.group(1)
    if _TRA.match(name):
        return "atz", name
    if m := _FIS.match(name):
        return "fis", m.group(1) or m.group(2)
    if m := _CTA.match(name):
        return "cta", m.group(1)
    return "oth", name


def group_label(kind: str, group: str, name: str = "") -> str:
    """Nazwa grupy do list wyboru i dymków: TMA Warszawa, TMA Poznań N (EPPO_TMA_N_*), CTR Kraków, Sektor B,
    FIS Gdańsk, CTA 02, EDWW Bremen. name = nazwa wycinka (potrzebna tylko do części N/S TMA Poznań)."""
    city = EP_CITY.get(group, group)
    if kind == "acc":
        return f"Sektor {group}"
    if kind == "tma":
        part = m.group(1) if (m := _TMA_PART.match(name.upper())) else ""
        return f"TMA {city} {part}".rstrip()
    if kind == "ctr":
        return f"CTR {city}"
    if kind == "cta" and (m := re.match(r"^CTA(\d+)$", group)):
        return f"CTA {m.group(1)}"
    if kind == "atz":
        return f"TRA {group}" if _TRA.match(group) else f"ATZ {city}"
    if kind == "fis":
        return f"FIS {FIS_REGION.get(group, group)}"
    if kind == "nb":
        return f"{group} {NB_FIR.get(group, '')}".strip()
    return group


def slice_label(fir: str, name: str) -> str:
    """Nazwa wycinka sąsiada z FIR-em, gdy nazwa go nie zawiera (LZBB "CTR" → "LZBB CTR")."""
    return name if fir == "EPWW" or name[:2] == fir[:2] else f"{fir} {name}"


@router.get("/slices")
def slices(level_ft: int | None = None, db: Session = Depends(get_db)):
    """Wszystkie wycinki sektorów (EPWW i sąsiedzi, data/seed/sectors.geojson z plików .ese) z rodzajem przestrzeni
    do warstw MAP.

    kind: acc | tma | ctr | fis | cta | atz | nb (FIR sąsiedni) | oth; group: litera sektora ACC, ICAO lotniska,
    rejon FIS, numer CTA albo FIR; group_label: nazwa przestrzeni (TMA Poznań N i S osobno, jak w tabeli om).
    Z level_ft tylko wycinki, których granice obejmują ten poziom (dolna <= poziom < górna).
    Bez sektorów (brak pliku sectors.geojson) pusta lista z `note`."""
    stmt = select(Sector).order_by(Sector.id)
    if level_ft is not None:
        stmt = stmt.where(Sector.lower_ft <= level_ft, Sector.upper_ft > level_ft)
    pos = _positions(db)
    covered = covered_firs(db)
    feats = []
    for s in db.scalars(stmt):
        kg = slice_kind(s.fir, s.name)
        if not kg:
            continue
        kind, group = kg
        if kind == "nb" and group in covered:  # FIR z własnym plikiem .ese: jego sektory niżej
            continue
        owners = [o for o in s.owners.split(":") if o]
        first = pos.get(owners[0]) if owners else None
        feats.append({"type": "Feature", "geometry": json.loads(s.geometry), "properties": {
            "id": s.id, "fir": s.fir, "name": s.name, "label": slice_label(s.fir, s.name), "kind": kind,
            "group": group, "group_label": group_label(kind, group, s.name),
            "lower_ft": s.lower_ft, "upper_ft": s.upper_ft,
            "owners": owners, "owner_callsigns": [pos[o].callsign for o in owners if o in pos],
            "callsign": first.callsign if first else None, "frequency": first.frequency if first else None,
            "source": "EPWW", "active": [],
        }})
    # sąsiedzi z ich własnych plików .ese: owners to już znaki stanowisk (ID są ważne tylko w obrębie jednego pliku)
    freq = {p["callsign"]: p["frequency"] for p in nb_positions(db)}
    for s in nb_sectors(db, level_ft):
        fir = s.fir.upper()
        owners = owners_of(s)
        first = owners[0] if owners else None
        feats.append({"type": "Feature", "geometry": json.loads(s.geometry), "properties": {
            "id": f"nb{s.id}", "fir": fir, "name": s.name, "label": slice_label(fir, s.name), "kind": "nb",
            "group": fir, "group_label": group_label("nb", fir, s.name),
            "lower_ft": s.lower_ft, "upper_ft": s.upper_ft, "owners": owners, "owner_callsigns": owners,
            "callsign": first, "frequency": freq.get(first) if first else None,
            "source": s.source, "active": (s.active or "").split(),
        }})
    note = None if feats or level_ft is not None else NO_SECTORS
    return {"type": "FeatureCollection", "features": feats, "limits_source": "ese", "note": note}
