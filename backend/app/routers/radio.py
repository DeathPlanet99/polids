import hashlib
import json
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..database import get_db
from ..importers.sectors_geojson import NO_SECTORS
from ..models import Aerodrome, AtcPosition, ImportLog, NavPoint, NbSector, Sector
from ..services import radioboard
from ..services.http_cache import UpstreamError
from ..services.neighbours import covered_firs, nb_owners, nb_sectors, owners_of
from ..services.positions import all_positions
from ..services.radio import (Station, neighbour_tab, position_range, radio_names, sector_group, sector_kind,
                              seed_json)
from ..services.vatsim import bookings_by_callsign, controller_info, fir_boundaries, match_positions, online_firs
# data feed i rezerwacje przez moduł /api/vatsim: jeden cache i jedno miejsce podmiany (testy, serwer demo)
from . import vatsim as vatsim_api

router = APIRouter(prefix="/api/radio", tags=["radio"])
log = logging.getLogger("polids.radio")


def _seed_or_empty(name: str) -> dict:
    """Plik z data/seed do uzupełnienia listy stanowisk; brak albo błąd pliku nie psuje listy (tylko wpis w logu)."""
    try:
        data = seed_json(settings.seed_dir / name)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        log.warning("RADIO: pomijam data/seed/%s (%s)", name, exc)
        return {}


def _coords(db: Session, icaos: set[str]) -> dict[str, tuple[float, float]]:
    """Współrzędne lotnisk: najpierw baza lotnisk, potem punkty AD z navdata (lotniska zagraniczne)."""
    out = {a.icao: (a.lat, a.lon) for a in db.scalars(select(Aerodrome).where(Aerodrome.icao.in_(icaos)))}
    for p in db.scalars(select(NavPoint).where(NavPoint.kind == "AD", NavPoint.ident.in_(icaos - set(out)))):
        out.setdefault(p.ident, (p.lat, p.lon))
    return out


# Strona RADIO (od 0.12 jedna strona jak w PANDORZE) pobiera tylko /board i /loa (panel LOA po kliknięciu stosu sąsiada).
# /positions, /online, /bookings i /sectors to dawne źródła podzakładek GEO, SEKTORYZACJA i list (do 0.11); zostają
# celowo jako API JSON: pełna lista stanowisk ze znakami radiowymi, dopasowanie zalogowanych, rezerwacje i wycinki .ese
# w jednym miejscu (narzędzia, diagnostyka; test_radio.py). Frontend z nich nie korzysta.


@router.get("/positions")
def positions(db: Session = Depends(get_db)):
    """Stanowiska do RADIO: plik .ese + wszystkie stanowiska sąsiednich FIR-ów z vacs-data (bez FMP/TMU).

    `in_ese=false`: stanowisko tylko z vacs-data (bez nazwy, pokazujemy `fir_name`). `range`: zasięg na mapie
    (granica VATSpy, okrąg wokół lotniska albo punkt lotniska). `nb_tab`: zakładka sąsiada (None = Polska).
    `radio`, `sector`: znak radiowy i nazwa sektora sąsiada (baza wiedzy VATSIM Germany, LOA, plik .ese)."""
    merged = all_positions(db)
    coords = _coords(db, {p["callsign"].split("_")[0] for p in merged})
    firs = fir_boundaries()
    for p in merged:
        lat, lon = coords.get(p["callsign"].split("_")[0], (None, None))
        p["range"] = position_range(p["callsign"], firs, lat, lon, p.get("prefix"))
        p["nb_tab"] = neighbour_tab(p)
    return radio_names(merged, _seed_or_empty("names_de.json").get("positions") or {},
                       _seed_or_empty("loa.json").get("firs") or {})


@router.get("/loa")
def loa():
    """Wyciąg z LOA EPWW z sąsiadami (data/seed/loa.json, PDF-y w OTHER › DOCS): klucz = FIR zakładki sąsiada
    (EDWW, EDUU, EDMM, LKAA, LZBB, UKLV, EYVL, ESAA), w każdym tytuł, wersja, PDF, stanowiska, przekazania
    (dir out = EPWW → sąsiad, in = sąsiad → EPWW) i zasady (silent, vfr, other)."""
    try:
        data = seed_json(settings.seed_dir / "loa.json")
    except FileNotFoundError as exc:
        raise HTTPException(404, "LOA: brak pliku data/seed/loa.json") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"LOA: nie da się odczytać pliku data/seed/loa.json ({exc})") from exc
    firs = data.get("firs") if isinstance(data, dict) else None
    if not isinstance(firs, dict):
        raise HTTPException(500, "LOA: plik data/seed/loa.json nie ma sekcji firs")
    return {"firs": {k: v for k, v in firs.items() if isinstance(v, dict)}, "source": data.get("_zrodlo", "")}


async def _feed() -> dict:
    try:
        return await vatsim_api.get_feed()
    except (UpstreamError, ValueError) as exc:
        raise HTTPException(502, f"VATSIM data feed niedostępny: {exc}") from exc


@router.get("/online")
async def online(db: Session = Depends(get_db)):
    """Zalogowani kontrolerzy dopasowani do stanowisk RADIO (plik .ese i vacs-data): najpierw dokładny znak,
    potem jak w EuroScope (prefiks, typ i częstotliwość). `controllers`: wszyscy zalogowani z EP** i z prefiksów
    znanych stanowisk (także ci, których nie udało się dopasować)."""
    data = await _feed()
    ctrls = data.get("controllers", []) + data.get("atis", [])
    merged = all_positions(db)
    stations = [Station(p["callsign"], p["prefix"], p["frequency"] or "") for p in merged]
    prefixes = {p["callsign"].split("_")[0] for p in merged}
    near = [c for c in ctrls if (cs := c.get("callsign", "")).split("_")[0] in prefixes or cs.startswith("EP")]
    return {"positions": match_positions(ctrls, stations, nb_owners(db)), "firs": online_firs(ctrls),
            "controllers": [controller_info(c) for c in near if not c.get("callsign", "").endswith("_OBS")],
            "updated": data.get("general", {}).get("update_timestamp")}


@router.get("/bookings")
async def bookings(db: Session = Depends(get_db)):
    """Rezerwacje (atc-bookings.vatsim.net) stanowisk RADIO: EP** i znane stanowiska sąsiadów, trwające i dzisiejsze."""
    try:
        data = await vatsim_api.get_bookings()
    except (UpstreamError, ValueError) as exc:
        raise HTTPException(502, f"VATSIM ATC bookings niedostępne: {exc}") from exc
    known = {p["callsign"] for p in all_positions(db)}
    result = {cs: b for cs, b in bookings_by_callsign(data).items() if cs in known or cs.startswith("EP")}
    try:
        feed = await vatsim_api.get_feed()
        names = {c.get("cid"): c.get("name") for c in feed.get("controllers", []) + feed.get("pilots", [])}
    except (UpstreamError, ValueError):
        names = {}
    for lst in result.values():
        for b in lst:
            b["name"] = names.get(b["cid"])
    return result


@router.get("/sectors")
def sectors(db: Session = Depends(get_db)):
    """Wszystkie wycinki sektorów (EPWW i sąsiedzi, data/seed/sectors.geojson) do mapy zasięgu: rodzaj
    (acc/tma/ctr/fis/nb), nazwa zbiorcza i lista OWNER przełożona na znaki stanowisk (kolejność przejmowania)."""
    by_id = {p.position_id: p.callsign for p in db.scalars(select(AtcPosition))}
    covered = covered_firs(db)
    feats = []
    for s in db.scalars(select(Sector).order_by(Sector.id)):
        kind = sector_kind(s.fir, s.name)
        if not kind or s.lower_ft >= 66000 or (kind == "nb" and s.fir.upper() in covered):
            continue
        ids = [o for o in s.owners.split(":") if o]
        feats.append({"type": "Feature", "geometry": json.loads(s.geometry), "properties": {
            "fir": s.fir, "name": s.name, "kind": kind, "group": sector_group(s.fir, s.name, kind),
            "lower_ft": s.lower_ft, "upper_ft": s.upper_ft, "owners": [by_id[o] for o in ids if o in by_id],
            "owner_ids": ids, "source": "EPWW"}})
    # sąsiedzi z ich własnych plików .ese (bez wycinków technicznych i powyżej FL660 – pominięte przy imporcie)
    for s in nb_sectors(db):
        fir = s.fir.upper()
        feats.append({"type": "Feature", "geometry": json.loads(s.geometry), "properties": {
            "fir": fir, "name": s.name, "kind": "nb", "group": sector_group(fir, s.name, "nb"),
            "lower_ft": s.lower_ft, "upper_ft": s.upper_ft, "owners": owners_of(s), "owner_ids": [],
            "source": s.source}})
    return {"type": "FeatureCollection", "features": feats,
            "note": None if feats else NO_SECTORS}


# --- RADIO jako jedna strona (PANDORA): GET /api/radio/board
_BOARD: dict = {}
_BOARD_LOCK = threading.Lock()
# tło podglądów: granice FIR-ów z VATSpy w tej ramce (lon min, lat min, lon max, lat max)
BOARD_FRAME = (4.0, 44.0, 34.0, 61.0)


def _in_frame(geom: dict) -> bool:
    pts = [pt for r in radioboard.rings(geom) for pt in r]
    if not pts:
        return False
    x0, y0, x1, y1 = BOARD_FRAME
    return min(p[0] for p in pts) < x1 and max(p[0] for p in pts) > x0 and min(p[1] for p in pts) < y1 \
        and max(p[1] for p in pts) > y0


def _file_stamp(path) -> tuple:
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None, None


def _board_stamp(db: Session, positions: dict) -> tuple:
    """Stan danych tablicy (ok. 2 ms): import plików (ImportLog), zawartość tabel sektorów (liczba, granice pionowe,
    długość list OWNER i geometrii – import kasuje tabelę i wstawia ją od nowa z tymi samymi id; sektory sąsiadów
    tylko z listami OWNER, do kolejności kafelków), stanowiska, ownership.json, kafelki sąsiadów z VACS
    (vacs_epww.json) i granice VATSpy. Zmiana czegokolwiek = część stała liczona od nowa i nowa wersja `ver` (strona
    pobiera wtedy geometrię jeszcze raz)."""
    def tbl(*cols):  # liczba wierszy i sumy kolumn jednej tabeli
        return tuple(db.execute(select(func.count(), *[func.total(c) for c in cols])).one())

    return (
        tbl(ImportLog.mtime), db.scalar(select(func.max(ImportLog.mtime))),
        tbl(Sector.id, Sector.lower_ft, Sector.upper_ft, func.length(Sector.owners), func.length(Sector.geometry)),
        tbl(NbSector.id, func.length(NbSector.owners)),
        tbl(func.length(AtcPosition.callsign) + func.length(AtcPosition.position_id)),
        db.scalar(select(func.count(Aerodrome.icao))), len(positions),
        _file_stamp(settings.seed_dir / "ownership.json"), _file_stamp(settings.seed_dir / "vacs_epww.json"),
        _file_stamp(settings.seed_dir / "vatspy_firs.geojson"))


def _board_static(db: Session, positions: dict) -> dict:
    """Część tablicy niezależna od sieci; liczona od nowa po zmianie danych (import), ownership.json albo vacs_epww.json.
    Zimne liczenie trwa ok. 1 s – endpoint woła tę funkcję w wątku (run_in_threadpool), żeby nie blokować serwera."""
    stamp = _board_stamp(db, positions)
    if _BOARD.get("stamp") == stamp:
        return _BOARD["data"]
    with _BOARD_LOCK:  # dwa pierwsze zapytania naraz: liczy jedno, drugie czeka na wynik
        if _BOARD.get("stamp") == stamp:
            return _BOARD["data"]
        data = _build_static(db, positions)
        data["ver"] = hashlib.sha1(repr(stamp).encode()).hexdigest()[:12]
        # geometria podglądów (ok. 240 KB JSON) zakodowana raz na wersję danych: odpowiedź ?geo=1 tylko ją wkleja
        data["geo_json"] = _dumps(data["geo"])
        _BOARD.update(stamp=stamp, data=data)
        return data


def _dumps(obj) -> str:
    """JSON jak w JSONResponse FastAPI (jsonable_encoder, UTF-8, bez spacji)."""
    return json.dumps(jsonable_encoder(obj), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _build_static(db: Session, positions: dict) -> dict:
    own = seed_json(settings.seed_dir / "ownership.json")
    # stosy sąsiadów: kafelki profilu VACS ACC_EPWW (sektory graniczące z EPWW); brak pliku = puste stosy
    vacs = _seed_or_empty("vacs_epww.json").get("neighbours")
    by_id = {p.position_id: p.callsign for p in db.scalars(select(AtcPosition))}
    covered = covered_firs(db)
    # listy OWNER sektorów sąsiadów z ich plików .ese (bez geometrii)
    epww, nb_chains = [], [[o for o in (x or "").split(":") if o]
                           for x in db.scalars(select(NbSector.owners).order_by(NbSector.id))]
    for s in db.scalars(select(Sector).order_by(Sector.id)):
        owners = [by_id[o] for o in s.owners.split(":") if o in by_id]
        if s.fir.upper() == "EPWW":
            epww.append(radioboard.make_slice(s.fir, s.name, s.lower_ft, s.upper_ft, owners, json.loads(s.geometry)))
        elif s.fir.upper() not in covered:  # FIR bez własnego pliku .ese (Białoruś): listy OWNER z pliku EPWW
            nb_chains.append(owners)
    coords = {a.icao: (a.lat, a.lon) for a in db.scalars(select(Aerodrome).where(Aerodrome.icao.like("EP%")))}
    firs = fir_boundaries()
    border = next((f["geometry"] for f in firs.get("features", []) if f["properties"]["id"] == "EPWW"), None) or {}
    # same FIR-y (bez sektorów VATSpy typu ESMM-7), żeby tło było lekkie
    frame = [{"id": f["properties"]["id"], "geometry": f["geometry"]} for f in firs.get("features", [])
             if "-" not in f["properties"]["id"] and f["properties"]["id"] != "EPWW" and _in_frame(f["geometry"])]
    return radioboard.build_static(own=own, epww_slices=epww, vacs_tiles=vacs if isinstance(vacs, list) else [],
                                   positions=positions, coords=coords, border_geom=border, firs_geo=frame,
                                   nb_chains=nb_chains)


def _board_data(db: Session) -> tuple[list[dict], dict, dict, set]:
    merged = all_positions(db)
    positions = {p["callsign"]: p for p in merged}
    return merged, positions, _board_static(db, positions), nb_owners(db)


@router.get("/board")
async def board(geo: bool = False, have: str = "", db: Session = Depends(get_db)):
    """Wszystko dla strony RADIO w jednym zapytaniu: sektory ACC EPWW (LOW/MID/HIGH) z właścicielem wg kolejności
    przejmowania (ownership.json), TMA/APP, CTR/TWR, FIS, ATIS polskich lotnisk i stosy sąsiadów (ESAA, EDWW, EDUU,
    EDMM, LKAA po lewej; UMKK, EYVL, UMMV, UKLV, LZBB po prawej): kafelki sektorów graniczących z EPWW jak w VACS
    (bez FMP) z wysokością VACS (alt, lo/hi w stopach) i właścicielem (owner); TMA w częściach wg stanowiska APP
    (WA APP, WA N APP, WA S APP), TMA proceduralne z wieżą lotniska.
    owner/owners = pierwsze stanowisko online z kolejności (null/[] = nikt: na stronie sektora ACC nie ma, sektory
    jednego stanowiska to jeden obszar; TMA, CTR i FIS EPWW samo ID, kafelek sąsiada bez częstotliwości).
    `ver`: wersja danych z importu. `geo=1` albo `have=<ver>` inne niż bieżąca: także geometria podglądów (granica
    EPWW, FIR-y w tle, wycinki ACC, TMA, CTR, FIS) – strona trzyma ją, dopóki `ver` się nie zmieni (np. po imporcie).
    Błąd VATSIM nie psuje tablicy: `net.ok=false` i komunikat, nikt online (EPWW: same ID; stosy sąsiadów bez
    częstotliwości i bez ADVISORY, bo nie wiadomo, czy ktoś tam jest)."""
    # pozycje i część stała: praca CPU i bazy (zimna ok. 1 s) w wątku, żeby nie wstrzymać innych zapytań
    merged, positions, static, prefer = await run_in_threadpool(_board_data, db)
    net = {"ok": True, "error": None, "updated": None}
    online, feed_atis = {}, []
    try:
        data = await vatsim_api.get_feed()
        ctrls = data.get("controllers", []) + data.get("atis", [])
        stations = [Station(p["callsign"], p["prefix"], p["frequency"] or "") for p in merged]
        online = match_positions(ctrls, stations, prefer)
        feed_atis = data.get("atis", [])
        net["updated"] = data.get("general", {}).get("update_timestamp")
    except (UpstreamError, ValueError) as exc:
        net.update(ok=False, error=f"VATSIM data feed niedostępny: {exc}")
    out = radioboard.board(static, online, positions, feed_atis)
    out["net"] = net
    out["ver"] = static["ver"]
    # kodowanie w wątku (jsonable_encoder to czysty Python); geometria już zakodowana w części stałej – bez tego
    # odpowiedź z geo=1 wstrzymywała serwer na 50–140 ms przy każdym otwarciu RADIO
    body = await run_in_threadpool(_dumps, out)
    if geo or (have and have != static["ver"]):
        body = body[:-1] + ',"geo":' + static["geo_json"] + "}"
    return Response(body.encode("utf-8"), media_type="application/json")
