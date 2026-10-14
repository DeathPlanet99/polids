from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Aerodrome, NavPoint
from ..services.http_cache import UpstreamError
from ..services.neighbours import nb_owners
from ..services.positions import online_stations
from ..services.route import RouteResolver
from ..services.vatsim import (airport_atc, airport_traffic, bookings_by_callsign, controller_info, fir_boundaries,
                               get_bookings, get_feed, match_positions, online_firs, pilot_info)

router = APIRouter(prefix="/api/vatsim", tags=["vatsim"])


async def _feed() -> dict:
    try:
        return await get_feed()
    except (UpstreamError, ValueError) as exc:
        raise HTTPException(502, f"VATSIM data feed niedostępny: {exc}") from exc


@router.get("/online")
async def online(db: Session = Depends(get_db)):
    """Zalogowani kontrolerzy: dopasowani do stanowisk (`positions`) i wszyscy z FIR-ów wokół EPWW (`firs`).

    Stanowiska: najpierw plik .ese EPWW (w kolejności z pliku), potem pozostałe stanowiska sąsiadów z vacs-data
    i z plików .ese sąsiadów (właściciele sektorów sąsiadów na mapie)."""
    data = await _feed()
    ctrls = data.get("controllers", []) + data.get("atis", [])
    return {"positions": match_positions(ctrls, online_stations(db), nb_owners(db)), "firs": online_firs(ctrls),
            "controllers": [controller_info(c) for c in ctrls if c.get("callsign", "").startswith("EP")],
            "updated": data.get("general", {}).get("update_timestamp")}


@router.get("/atc")
async def atc(db: Session = Depends(get_db)):
    """Kontrolerzy online do plakietek na mapie: lotniska (DEL/GND/TWR/APP/ATIS) z pozycją i FIR-y (CTR/FSS)."""
    data = await _feed()
    ctrls = data.get("controllers", []) + data.get("atis", [])
    groups = airport_atc(ctrls)
    ads = {a.icao: a for a in db.scalars(select(Aerodrome).where(Aerodrome.icao.in_(groups)))}
    pts = {p.ident: p for p in db.scalars(select(NavPoint).where(NavPoint.kind == "AD", NavPoint.ident.in_(groups)))}
    airports = []
    for icao, facilities in sorted(groups.items()):
        a = ads.get(icao) or pts.get(icao)
        if a:  # prefiksów spoza bazy lotnisk (np. 3-literowych z USA) nie umiemy postawić na mapie
            airports.append({"icao": icao, "name": a.name, "lat": a.lat, "lon": a.lon, "facilities": facilities})
    return {"airports": airports, "firs": online_firs(ctrls),
            "updated": data.get("general", {}).get("update_timestamp")}


@router.get("/bookings")
async def bookings(prefix: str = "EP", hours: int | None = Query(None, ge=1, le=168)):
    """Rezerwacje stanowisk z atc-bookings.vatsim.net: trwające i zaczynające się jeszcze dziś (doba UTC),
    albo w ciągu `hours` godzin, jeśli podano."""
    try:
        data = await get_bookings()
    except (UpstreamError, ValueError) as exc:
        raise HTTPException(502, f"VATSIM ATC bookings niedostępne: {exc}") from exc
    until = datetime.now(timezone.utc) + timedelta(hours=hours) if hours else None
    result = bookings_by_callsign(data, prefix, until=until)
    # API rezerwacji podaje tylko CID; imię i nazwisko znamy, jeśli ta osoba jest teraz zalogowana w sieci
    try:
        feed = await get_feed()
        names = {c.get("cid"): c.get("name") for c in feed.get("controllers", []) + feed.get("pilots", [])}
    except (UpstreamError, ValueError):
        names = {}
    for lst in result.values():
        for b in lst:
            b["name"] = names.get(b["cid"])
    return result


@router.get("/airport/{icao}")
async def airport(icao: str, db: Session = Depends(get_db)):
    """Bieżące loty z/do lotniska (piloci w sieci z planem lotu i prefile)."""
    data = await _feed()
    ad = db.get(Aerodrome, icao.upper())
    return {"icao": icao.upper(), **airport_traffic(data, icao, ad.lat if ad else None, ad.lon if ad else None),
            "updated": data.get("general", {}).get("update_timestamp")}


@router.get("/firs")
def firs():
    """Granice FIR z projektu VATSpy (wycinek Europy środkowej)."""
    return fir_boundaries()


@router.get("/pilots")
async def pilots(bbox: str = "46,8,58,30"):
    """Samoloty w obszarze bbox = południe,zachód,północ,wschód."""
    try:
        s, w, n, e = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(400, "bbox = poludnie,zachod,polnoc,wschod") from exc
    data = await _feed()
    return [pilot_info(p) for p in data.get("pilots", [])
            if p.get("latitude") is not None and s <= p["latitude"] <= n and w <= p["longitude"] <= e]


@router.get("/pilots/{callsign}/route")
async def pilot_route(callsign: str, db: Session = Depends(get_db)):
    """Trasa z planu lotu pilota rozpisana na punkty (lotnisko odlotu, trasa, lotnisko docelowe)."""
    data = await _feed()
    p = next((x for x in data.get("pilots", []) if x.get("callsign") == callsign.upper()), None)
    if not p:
        raise HTTPException(404, f"{callsign} nie jest online")
    info = pilot_info(p)
    if not info["route"] and not info["departure"]:
        return {**info, "points": [], "distance_nm": 0, "warnings": ["Brak planu lotu"]}
    text = " ".join(x for x in (info["departure"], info["route"], info["arrival"]) if x)
    return {**info, **RouteResolver(db).resolve(text)}
