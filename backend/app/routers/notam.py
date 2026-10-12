from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Aerodrome
from ..services.http_cache import UpstreamError
from ..services.notam import FIR, fir_notams, get_notams, notam_url

router = APIRouter(prefix="/api/notam", tags=["notam"])


@router.get("/fir/{fir}")
async def fir(fir: str, db: Session = Depends(get_db),
              include_all: bool = Query(False, alias="all",
                                        description="także NOTAM-y nieobowiązujące teraz (przyszłe, wygasłe)")):
    """SYT OP: NOTAM-y FIR EPWW i głównych lotnisk obowiązujące teraz, bez duplikatów. Awaria części lokalizacji
    nie psuje odpowiedzi: lista z pozostałych, a nieudane w `failed` (i opis w `error`)."""
    if fir.upper() != FIR:
        raise HTTPException(404, f"Lista NOTAM jest dostępna tylko dla FIR {FIR}")
    data = await fir_notams(include_inactive=include_all)
    codes = sorted({c for n in data["notams"] for c in n["locations"]} | set(data["locations"]))
    names = {a.icao: a.name for a in db.scalars(select(Aerodrome).where(Aerodrome.icao.in_(codes)))}
    names[FIR] = "FIR WARSZAWA"
    return {**data, "names": names}


@router.get("/{icao}")
async def notams(icao: str):
    if len(icao) != 4 or not icao.isalpha():
        raise HTTPException(400, "Kod ICAO musi mieć 4 litery")
    try:
        return await get_notams(icao)
    except UpstreamError as exc:
        raise HTTPException(502, f"Serwer NOTAM niedostępny ({notam_url(icao)}): {exc}") from exc
