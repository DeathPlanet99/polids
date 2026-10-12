import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Aerodrome, AircraftType, AtcPosition
from ..services import dman as dman_service
from ..services import viff as viff_service
from ..services.http_cache import UpstreamError
from ..services.lvp import evaluate as evaluate_lvp
from ..services.metar import parse_metar, qfe_from_qnh
from ..services.procedures import procedures, strip_board
from ..services.runways import equipment_for, runway_config, runway_heading, select_runways, wind_components
from ..services.vatsim import airport_traffic, controller_info, get_feed, parse_atis
from ..services.weather import get_metars, get_tafs
from ..services.sun import sun_times

router = APIRouter(prefix="/api/aerodromes", tags=["aerodrome"])


def _ad(db: Session, icao: str) -> Aerodrome:
    ad = db.get(Aerodrome, icao.upper())
    if not ad:
        raise HTTPException(404, f"Nie znam lotniska {icao}")
    return ad


@router.get("")
def list_aerodromes(q: str = "", db: Session = Depends(get_db)):
    stmt = select(Aerodrome).order_by(Aerodrome.icao)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Aerodrome.icao.ilike(like), Aerodrome.name.ilike(like), Aerodrome.city.ilike(like)))
    return [{"icao": a.icao, "name": a.name, "city": a.city, "kind": a.kind, "lat": a.lat, "lon": a.lon,
             "elevation_ft": a.elevation_ft} for a in db.scalars(stmt)]


@router.get("/{icao}")
def aerodrome(icao: str, db: Session = Depends(get_db)):
    ad = _ad(db, icao)
    positions = db.scalars(select(AtcPosition).where(AtcPosition.prefix == ad.icao)
                           .order_by(AtcPosition.callsign)).all()
    return {
        "icao": ad.icao, "name": ad.name, "city": ad.city, "lat": ad.lat, "lon": ad.lon,
        "elevation_ft": ad.elevation_ft, "iata": ad.iata,
        "runways": [{"designator": r.designator, "opposite": r.opposite, "heading_true": r.heading_true,
                     "length_m": r.length_m, "width_m": r.width_m, "surface": r.surface} for r in ad.runways],
        "frequencies": [{"kind": f.kind, "description": f.description, "mhz": f.mhz} for f in ad.frequencies],
        "atc_positions": [{"callsign": p.callsign, "name": p.name, "frequency": p.frequency} for p in positions],
    }


@router.get("/{icao}/status")
async def status(icao: str, db: Session = Depends(get_db)):
    """Wszystko do widoku AERODROME: METAR (zdekodowany), TAF, składowe wiatru, pas sugerowany, QFE."""
    ad = _ad(db, icao)
    errors = []
    metar = taf = None
    try:
        metar = (await get_metars([ad.icao])).get(ad.icao)
    except UpstreamError as exc:
        errors.append(f"METAR: {exc}")
    try:
        taf = (await get_tafs([ad.icao])).get(ad.icao)
    except UpstreamError as exc:
        errors.append(f"TAF: {exc}")
    parsed = parse_metar(metar) if metar else None

    wd = parsed.wind_dir if parsed else None
    ws = parsed.wind_speed if parsed else None
    cfg = runway_config(ad.icao)
    equipment = equipment_for(ad.icao, cfg)
    rwys = [{"designator": r.designator, "heading": runway_heading(r.designator, r.heading_true),
             "preferred": r.preferred, "length_m": r.length_m, "width_m": r.width_m, "surface": r.surface,
             "equipment": equipment.get(r.designator, [])} for r in ad.runways]
    for r in rwys:
        r["headwind"], r["crosswind"] = wind_components(wd, ws, r["heading"])
    lvp = evaluate_lvp(parsed, ad.icao)
    low_vis = lvp["state"] or ("IMC" if parsed and parsed.flight_category in ("IFR", "LIFR") else None)
    pref = select_runways(rwys, wd, ws, equipment=equipment, low_vis=low_vis, config=cfg)

    # Kontrola w sieci VATSIM: ATIS (litera, pas w użyciu) i zalogowane stanowiska lotniska
    atc, atis, net_error = [], None, None
    try:
        feed = await get_feed()
        stations = feed.get("controllers", []) + feed.get("atis", [])
        mine = [c for c in stations if c.get("callsign", "").startswith(ad.icao + "_")]
        atc = [controller_info(c) for c in mine if not c["callsign"].endswith("_ATIS")]
        designators = [r["designator"] for r in rwys]
        for c in sorted((c for c in mine if c["callsign"].endswith("_ATIS")), key=lambda c: c["callsign"]):
            info = parse_atis(c.get("text_atis"), designators, c.get("atis_code"))
            kind = "dep" if "_D_ATIS" in c["callsign"] else "arr" if "_A_ATIS" in c["callsign"] else "both"
            if atis is None:
                atis = {"callsign": c["callsign"], "letter": info["letter"], "arr": None, "dep": None,
                        "frequency": c.get("frequency"), "lines": c.get("text_atis") or []}
            else:
                atis["callsign"] += " / " + c["callsign"]
                atis["letter"] = " / ".join(x for x in (atis["letter"], info["letter"]) if x)
                atis["lines"] = atis["lines"] + (c.get("text_atis") or [])
            if kind in ("arr", "both") and info["arr"]:
                atis["arr"] = atis["arr"] or info["arr"]
            if kind in ("dep", "both") and info["dep"]:
                atis["dep"] = atis["dep"] or info["dep"]
    except (UpstreamError, ValueError) as exc:
        net_error = f"VATSIM: {exc}"
    if atis and (atis["arr"] or atis["dep"]):
        in_use = {"arr": atis["arr"] or atis["dep"], "dep": atis["dep"] or atis["arr"], "source": "ATIS",
                  "reason": f"Z ATIS {atis['letter'] or ''}".strip()}
    else:
        in_use = {**pref, "source": "SUGGESTED"}

    qfe = qfe_from_qnh(parsed.qnh, ad.elevation_ft or 0) if parsed and parsed.qnh else None
    return {
        "icao": ad.icao, "name": ad.name, "elevation_ft": ad.elevation_ft,
        "metar": metar, "parsed": parsed.to_dict() if parsed else None, "taf": taf,
        "qfe": qfe, "runways": rwys, "lvp": lvp, "sun": sun_times(ad.lat, ad.lon),
        "preferred": pref, "runway_in_use": in_use, "atis": atis, "atc_online": atc, "network_error": net_error,
        # zgodność wstecz
        "suggested_runway": pref["arr"], "suggestion_reason": pref["reason"],
        "errors": errors,
    }


@router.get("/{icao}/strips")
async def strips(icao: str, dman: bool = False, db: Session = Depends(get_db)):
    """Paski postępu lotu do STRIP i AERODROME › RUCH (jak EFES): ruch VATSIM, pas w użyciu i litera ATIS (jak /status),
    vIFF (EOBT, TSAT, CTOT, stan lotu), SID/STAR z pliku .ese i kategoria turbulencji z bazy typów.

    Każdy pasek ma też pola DMAN/AMAN (services/dman.py): registration, a przylot entry_fix, eta_s, eldt_s, eldt.
    ?dman=1 (widok DMAN/AMAN) dodatkowo pyta vIFF o ELDT przylotów (/ifps/callsign) i ustawienia A-CDM lotniska."""
    ad = _ad(db, icao)

    async def from_viff():
        try:
            return await viff_service.departures(ad.icao), None
        except (UpstreamError, ValueError) as exc:
            return None, f"vIFF: {exc}"

    st, (viff, viff_error) = await asyncio.gather(status(ad.icao, db), from_viff())
    traffic, updated, net_error = {}, None, st["network_error"]
    if not net_error:
        try:
            feed = await get_feed()
            traffic = airport_traffic(feed, ad.icao, ad.lat, ad.lon)
            updated = feed.get("general", {}).get("update_timestamp")
        except (UpstreamError, ValueError) as exc:
            net_error = f"VATSIM: {exc}"
    types = {p.get("aircraft") for lst in traffic.values() for p in lst if p.get("aircraft")}
    wtc: dict[str, str] = {}
    if types:
        for t, w in db.execute(select(AircraftType.icao, AircraftType.wtc)
                               .where(AircraftType.icao.in_(types), AircraftType.wtc.is_not(None))):
            wtc.setdefault(t, w)
    use, atis = st["runway_in_use"], st["atis"]
    now = datetime.now(timezone.utc)
    board = strip_board(ad.icao, traffic, viff, use, wtc, now)
    procs = procedures().get(ad.icao, {})
    viff_etas, cad = None, None
    if dman:
        # vIFF zna ELDT lotów w powietrzu i jeszcze na lotnisku odlotu; najbliższe najpierw
        asked = sorted((s for s in board["arrivals"] if s["state"] == "air" or s["away"]),
                       key=lambda s: (s["dist_nm"] is None, s["dist_nm"] or 0))
        viff_etas, cad = await asyncio.gather(
            viff_service.arrival_etas(ad.icao, [s["callsign"] for s in asked]), viff_service.cad_info(ad.icao))
    dman_service.annotate(board, ad.icao, now, viff_etas)
    return {
        "icao": ad.icao, "name": ad.name, "elevation_ft": ad.elevation_ft, "now": now.strftime("%H%M"),
        "now_ts": round(now.timestamp(), 1), "iafs": dman_service.iafs(ad.icao), "cad": cad,
        "runway_in_use": use, "atis": {"letter": atis["letter"], "callsign": atis["callsign"]} if atis else None,
        "cdm": bool(viff and viff.get("cdm")), "states": (viff or {}).get("states") or viff_service.FLIGHT_STATES,
        "procedures": {"sid": sorted(procs.get("sid", {})), "star": sorted(procs.get("star", {}))},
        "network_error": net_error, "viff_error": viff_error, "updated": updated, **board,
    }


@router.get("/{icao}/checklist")
def checklist(icao: str):
    """Checklista otwarcia stanowiska (ta sama co w zakładce CHECKLIST)."""
    data = json.loads((settings.seed_dir / "checklists.json").read_text("utf-8"))
    chk = next(c for c in data["checklists"] if c["id"] == data.get("aerodrome", "open-position"))
    return {"icao": icao.upper(), **chk}
