"""vIFF (VATSIM IFPS/ETFMS/CDM, https://api.viffsys.com, autor Roger Puig).

Używamy wyłącznie odczytu (GET):
- /ifps/depAirport?airport=ICAO: odloty z EOBT, CTOT, statusem ATFCM i danymi A-CDM (TOBT, TSAT, TTOT),
- /etfms/getCadAirport?icao=ICAO: czy lotnisko ma A-CDM,
- /etfms/trafficVolumes i /etfms/airspaces: sektory (traffic volumes) z przepustowością i godzinowym ruchem,
- /etfms/scenarios: scenariusze (konfiguracje sektorów), które włączają zestawy traffic volumes,
- /ifps/callsign?callsign=X: jeden lot (plan, CDM, ATFCM, historia).

Odczyt działa bez klucza. Jeśli autor vIFF wyda klucz, ustaw POLIDS_VIFF_API_KEY (idzie w nagłówku x-api-key).
Endpointów zapisujących (POST) aplikacja nigdy nie wywołuje: zmieniają wspólny stan sieci.
Schematów odpowiedzi nie ma w dokumentacji API, więc nazwy pól poniżej pochodzą z obserwacji odpowiedzi."""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings
from .http_cache import UpstreamError, fetch_text

# Stan lotu jak w liście lotów NM (kody flight state z komunikatów EFD), wyliczany z pól vIFF.
# vIFF ustawia ATC_ACTIV w chwili AOBT, więc to jest "AA"; lot w powietrzu bez ATC_ACTIV = "TA".
FLIGHT_STATES = {
    "FI": "FI: plan złożony (filed), bez slotu",
    "SI": "SI: slot wydany (slot issued, CTOT)",
    "SU": "SU: lot zawieszony (suspended, FLS)",
    "AA": "AA: aktywowany przez ATC (ATC activated, AOBT)",
    "TA": "TA: aktywowany po starcie (tact activated, ATOT)",
}


def hhmm(value) -> str | None:
    """'1520' albo '152000' → '1520'; puste → None (vIFF podaje godziny UTC bez daty)."""
    s = str(value or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else None


def _mins(t: str | None) -> int | None:
    return int(t[:2]) * 60 + int(t[2:]) if t else None


def flight_state(f: dict) -> str:
    atfcm = (f.get("atfcmStatus") or "").upper()
    cdm = (f.get("cdmSts") or "").upper()
    if atfcm.startswith("FLS") or cdm.startswith("FLS"):
        return "SU"
    if atfcm == "ATC_ACTIV":
        return "AA"
    if hhmm(f.get("atot")) or cdm in ("AIRB", "COMPLY"):
        return "TA"
    if hhmm(f.get("ctot")) and atfcm != "SLC":
        return "SI"
    return "FI"


def departure(f: dict) -> dict:
    cdm = f.get("cdmData") or {}
    atfcm = f.get("atfcmData") or {}
    suspension = next((s for s in (f.get("atfcmStatus"), f.get("cdmSts")) if (s or "").upper().startswith("FLS")), None)
    eobt, ctot = hhmm(f.get("eobt")), hhmm(f.get("ctot")) or hhmm(cdm.get("ctot"))
    taxi = f.get("taxi") if isinstance(f.get("taxi"), int) else 0
    # opóźnienie ATFM = CTOT - ETOT, ETOT = EOBT + czas kołowania (jak kolumna Delay w liście lotów NM)
    delay = ((_mins(ctot) - _mins(eobt) - taxi + 720) % 1440 - 720) if ctot and eobt else None
    return {
        "callsign": f.get("callsign"), "arrival": f.get("arrival"),
        "eobt": eobt, "ctot": ctot, "delay": delay,
        "tobt": hhmm(cdm.get("tobt")) or hhmm(f.get("tobt")), "tsat": hhmm(cdm.get("tsat")),
        "ttot": hhmm(cdm.get("ttot")), "aobt": hhmm(f.get("aobt")), "atot": hhmm(f.get("atot")),
        "state": flight_state(f), "suspension": suspension,
        "ready": (f.get("cdmSts") or "").upper() == "REA" or bool(atfcm.get("isRea")),
        "regulation": atfcm.get("mostPenalisingRegulation") or None,
        "airspace": f.get("mostPenalizingAirspace") or None,
        "dep_info": cdm.get("depInfo") or None,
        "atfcm_status": f.get("atfcmStatus") or None, "cdm_status": f.get("cdmSts") or None,
        # czas kołowania z vIFF (minuty) do EST TAXI OUT i ETOT w DMAN
        "taxi": f.get("taxi") if isinstance(f.get("taxi"), int) and f.get("taxi") > 0 else None,
    }


async def _get(path: str, params: dict | None = None, ttl: int | None = None):
    headers = {"x-api-key": settings.viff_api_key.strip()} if settings.viff_api_key.strip() else None
    text = await fetch_text(settings.viff_url.rstrip("/") + path, ttl if ttl is not None else settings.viff_cache_seconds,
                            params=params, headers=headers)
    return json.loads(text) if text.strip() else None


async def is_cdm_airport(icao: str) -> bool:
    icao = icao.upper()
    fallback = icao in {a.strip().upper() for a in settings.viff_cdm_airports.split(",") if a.strip()}
    try:
        cad = await _get("/etfms/getCadAirport", {"icao": icao}, ttl=3600)
    except Exception:  # noqa: BLE001 - brak konfiguracji lotniska w vIFF to nie błąd
        return fallback
    return bool(cad.get("isCdm")) if isinstance(cad, dict) and "isCdm" in cad else fallback


async def departures(icao: str) -> dict:
    icao = icao.upper()
    data = await _get("/ifps/depAirport", {"airport": icao})
    flights = [departure(f) for f in (data if isinstance(data, list) else []) if f.get("callsign")]
    flights.sort(key=lambda f: (bool(f["atot"]), f["eobt"] or "9999", f["callsign"]))
    return {"icao": icao, "cdm": await is_cdm_airport(icao), "flights": flights, "states": FLIGHT_STATES}


# Ustawienia A-CDM lotnisk (/etfms/getCadAirports) to tylko dodatek do DMAN: czekamy na nie najwyżej CAD_WAIT_S,
# zapytanie, które nie zdążyło, kończy się w tle (i trafia do cache), a po błędzie albo przekroczeniu czasu nie pytamy
# przez CAD_MISS_S. Dzięki temu awaria vIFF nie wydłuża odświeżania widoku RUCH.
CAD_WAIT_S = 4.0
CAD_MISS_S = 300
_cad: dict = {"failed": 0.0, "task": None}


async def _cad_airports():
    try:
        data = await _get("/etfms/getCadAirports", ttl=3600)
    except Exception:  # noqa: BLE001 - błąd sieci, 404 albo odpowiedź nie-JSON
        _cad["failed"] = time.monotonic()
        return None
    _cad["failed"] = 0.0
    return data


async def cad_info(icao: str, wait: float = CAD_WAIT_S) -> dict | None:
    """Ustawienia A-CDM lotniska z /etfms/getCadAirports (jedno zapytanie na wszystkie lotniska, cache 1 h):
    przepustowość odlotów (rate na godzinę) i domyślny czas kołowania; None bez danych, po błędzie i po `wait` s."""
    if _cad["failed"] and time.monotonic() - _cad["failed"] < CAD_MISS_S:
        return None
    task = _cad["task"]
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = _cad["task"] = asyncio.ensure_future(_cad_airports())
    try:
        data = await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        _cad["failed"] = time.monotonic()   # odpowiedź, która przyjdzie w tle, kasuje ten znacznik
        return None
    num = lambda v: v if isinstance(v, int) and v > 0 else None  # noqa: E731
    for a in data if isinstance(data, list) else []:
        if isinstance(a, dict) and str(a.get("icao") or "").upper() == icao.upper():
            return {"rate": num(a.get("rate")), "taxi": num(a.get("taxiTime"))}
    return None


# ELDT przylotów z /ifps/callsign (pole eta). Loty, których vIFF nie zna, nie są pytane ponownie przez ETA_MISS_S,
# a zapytania, które nie zdążyły w czasie odpowiedzi, kończą się w tle i trafiają do cache na następne odświeżenie.
ETA_TTL_S = 120
ETA_MISS_S = 600
_eta_miss: dict[str, float] = {}
_eta_bg: set = set()


async def arrival_etas(icao: str, callsigns: list[str], wait: float = 4.0, limit: int = 25) -> dict[str, str]:
    """{callsign: 'HHMM'} ELDT z vIFF dla przylotów do `icao` (najwyżej `limit` lotów, 5 zapytań naraz)."""
    icao = icao.upper()
    now = time.monotonic()
    for cs in [c for c, t in _eta_miss.items() if now - t > ETA_MISS_S]:
        del _eta_miss[cs]
    todo = [cs for cs in dict.fromkeys(c.upper() for c in callsigns if c) if cs not in _eta_miss][:limit]
    if not todo:
        return {}
    sem = asyncio.Semaphore(5)

    async def one(cs: str):
        async with sem:
            try:
                data = await _get("/ifps/callsign", {"callsign": cs}, ttl=ETA_TTL_S)
            except Exception:  # noqa: BLE001 - 404 (lot nieznany), błąd sieci albo odpowiedź nie-JSON
                data = None
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict) and str(d.get("callsign", "")).upper() == cs), None)
        ok = (isinstance(data, dict) and str(data.get("callsign") or "").upper() == cs
              and str(data.get("arrival") or icao).upper() == icao and not data.get("landed"))
        eta = hhmm(data.get("eta")) if ok else None
        if eta is None:
            _eta_miss[cs] = time.monotonic()
        return cs, eta

    tasks = [asyncio.ensure_future(one(cs)) for cs in todo]
    done, pending = await asyncio.wait(tasks, timeout=wait)
    for t in pending:
        _eta_bg.add(t)
        t.add_done_callback(_eta_bg.discard)
    return {cs: eta for t in done for cs, eta in [t.result()] if eta}


# --- sektory: ruch i przepustowość na najbliższą godzinę

def _minutes(value: str | None, now_min: int) -> int | None:
    """Godzina 'HHMM' jako minuty względem teraz, w zakresie -720..719 (przejście przez północ)."""
    t = hhmm(value)
    if t is None:
        return None
    return (int(t[:2]) * 60 + int(t[2:]) - now_min + 720) % 1440 - 720


def _cap(value) -> int | None:
    """999 / -1 / brak = bez limitu."""
    return value if isinstance(value, int) and 0 < value < 999 else None


def sector_load(tv: str, buckets: list[dict], now: datetime, horizon: int = 60) -> dict:
    """Ruch w sektorze od teraz do +horizon minut: wejścia w oknach 20-minutowych, zajętość minuta po minucie
    i godzinowe liczniki vIFF (bieżąca i następna godzina) z przepustowością.

    Krotka lotu w vIFF: [callsign, wejście plan., wyjście plan., wejście wylicz., wyjście wylicz., flaga];
    znaczenie nie jest udokumentowane, bierzemy czasy wyliczone, a gdy ich brak, planowane."""
    now_min = now.hour * 60 + now.minute
    flights: dict[str, dict] = {}
    for b in buckets:
        for t in b.get("flights") or []:
            if not isinstance(t, list) or len(t) < 3:
                continue
            entry = _minutes(t[3] if len(t) > 3 and hhmm(t[3]) else t[1], now_min)
            exit_ = _minutes(t[4] if len(t) > 4 and hhmm(t[4]) else t[2], now_min)
            if entry is None:
                continue
            if exit_ is None or exit_ < entry:
                exit_ = entry + 1
            flights[t[0]] = {"callsign": t[0], "entry": entry, "exit": exit_, "airborne": bool(t[5]) if len(t) > 5 else None}
    ahead = sorted((f for f in flights.values() if f["exit"] > 0 and f["entry"] < horizon), key=lambda f: f["entry"])
    occupancy = [sum(1 for f in ahead if f["entry"] <= m < f["exit"]) for m in range(horizon + 1)]
    windows = [{"from": m, "to": m + 20, "entries": sum(1 for f in ahead if m <= f["entry"] < m + 20)}
               for m in range(0, horizon, 20)]
    hours = [{"hour": b.get("hour"), "entries": b.get("entriesCount"), "entries_cap": _cap(b.get("entriesCapacity")),
              "peak": b.get("peakCount"), "peak_cap": _cap(b.get("peakCapacity")), "active": b.get("active")}
             for b in buckets]
    clock = lambda m: (now.replace(second=0, microsecond=0) + timedelta(minutes=m)).strftime("%H%M")  # noqa: E731
    return {"id": tv, "hours": hours, "windows": windows, "occupancy": occupancy,
            "entries_60": sum(1 for f in ahead if 0 <= f["entry"] < horizon), "peak_60": max(occupancy),
            "flights": [{"callsign": f["callsign"], "entry": clock(f["entry"]), "exit": clock(f["exit"]),
                         "airborne": f["airborne"]} for f in ahead]}


# --- scenariusze: zestawy traffic volumes włączane, gdy są online (albo zarezerwowane) określone stanowiska

def _hm(value) -> str | None:
    """Ściśle 'HHMM' (hhmm() wziąłby też rok z daty '2026-10-05')."""
    return value if isinstance(value, str) and re.fullmatch(r"\d{4}", value) else None


def _list(value) -> list[str]:
    items = value if isinstance(value, list) else str(value or "").split(",")
    return [str(v).strip() for v in items if str(v).strip()]


def _times(items) -> list[dict]:
    """[[1, [], "1800", "2000"], ...] → [{"from": "1800", "to": "2000", "date": None}]; znaczenie pozostałych pól
    (dzień tygodnia?) nie jest znane, więc pokazujemy tylko godziny i ewentualną datę."""
    out = []
    for t in items if isinstance(items, list) else []:
        vals = t if isinstance(t, list) else list(t.values()) if isinstance(t, dict) else []
        hours = [v for v in vals if _hm(v)]
        date = next((v for v in vals if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)), None)
        if len(hours) >= 2:
            out.append({"from": hours[0], "to": hours[1], "date": date})
    return out


def scenario(s: dict) -> dict:
    """trafficVolumes: [[tv, przepustowość zajętości, wejść na godzinę], ...]; 999 = bez limitu."""
    tvs = [{"id": t[0], "occupancy_cap": _cap(t[1]) if len(t) > 1 else None, "entries_cap": _cap(t[2]) if len(t) > 2 else None}
           for t in s.get("trafficVolumes") or [] if isinstance(t, list) and t and isinstance(t[0], str)]
    return {"id": s.get("id"), "active": bool(s.get("isActive") or s.get("active")), "tvs": tvs,
            "positions": _list(s.get("positions")), "anypositions": _list(s.get("anypositions")),
            "nopositions": _list(s.get("nopositions")), "start": _hm(s.get("start")), "end": _hm(s.get("end")),
            "times": _times(s.get("times")) + _times(s.get("oneTimes")), "description": s.get("description") or "",
            "booked": bool(s.get("useBookedPositions"))}


async def scenarios(prefix: str | None = None) -> list[dict]:
    """Wszystkie scenariusze z prefiksem (active=false = także nieaktywne); aktywne na początku."""
    prefix = (prefix or settings.viff_sector_prefix).upper()
    data = await _get("/etfms/scenarios", {"filter": prefix, "active": "false"})
    out = [scenario(s) for s in (data if isinstance(data, list) else []) if isinstance(s, dict) and s.get("id")]
    out.sort(key=lambda s: (not s["active"], s["id"]))
    return out


async def sectors(prefix: str | None = None, now: datetime | None = None) -> dict:
    prefix = (prefix or settings.viff_sector_prefix).upper()
    now = now or datetime.now(timezone.utc)
    today, tomorrow = now.date().isoformat(), (now + timedelta(days=1)).date().isoformat()
    try:  # definicje (obszar, status) są dodatkiem; bez nich wykres nadal działa
        defs = await _get("/etfms/trafficVolumes", {"filter": prefix, "active": "false", "activeAndDisabled": "true"}, ttl=600)
    except (UpstreamError, ValueError):
        defs = []
    load = await _get("/etfms/airspaces", {"filter": prefix, "fromNow": "true", "date": today}) or {}
    # następna godzina po 23Z jest już jutro
    nxt = await _get("/etfms/airspaces", {"filter": prefix, "date": tomorrow}) if now.hour == 23 else {}
    try:  # scenariusze też są dodatkiem: każdy błąd = pusta lista
        scns = await scenarios(prefix)
    except Exception:  # noqa: BLE001
        scns = []
    cur_h, next_h = f"{now.hour:02d}", f"{(now.hour + 1) % 24:02d}"
    info = {d["id"]: d for d in (defs if isinstance(defs, list) else []) if isinstance(d, dict) and d.get("id")}
    in_scn = {}  # TV → scenariusze, w których występuje
    for s in scns:
        for t in s["tvs"]:
            in_scn.setdefault(t["id"], []).append((s, t))
    ids = sorted({i for i in [*info, *(load or {}), *in_scn] if i.startswith(prefix + "-")})
    out = []
    for tv in ids:
        buckets = [b for b in (load or {}).get(tv, []) if b.get("hour") == cur_h]
        buckets += [b for b in ((nxt or {}) if now.hour == 23 else (load or {})).get(tv, []) if b.get("hour") == next_h]
        d = info.get(tv, {})
        item = sector_load(tv, buckets, now)
        item.update({"volumes": [v for v in (d.get("volumes") or "").split(",") if v], "status": d.get("status"),
                     "entries_cap": _cap(d.get("entries")), "occupancy_cap": _cap(d.get("occupancy")),
                     "restrictions": d.get("restrictions") or []})
        if item["entries_cap"] is None and item["hours"]:
            item["entries_cap"] = item["hours"][0]["entries_cap"]
        if item["occupancy_cap"] is None and item["hours"]:
            item["occupancy_cap"] = item["hours"][0]["peak_cap"]
        for _s, t in in_scn.get(tv, []):  # przepustowość ze scenariusza, gdy definicja TV jej nie podaje
            item["entries_cap"] = item["entries_cap"] or t["entries_cap"]
            item["occupancy_cap"] = item["occupancy_cap"] or t["occupancy_cap"]
        item["scenarios"] = [s["id"] for s, _t in in_scn.get(tv, [])]
        # aktywny = status 1 w vIFF albo należy do aktywnego scenariusza
        item["active"] = item["status"] == 1 or any(s["active"] for s, _t in in_scn.get(tv, []))
        out.append(item)
    out.sort(key=lambda s: (not s["active"], s["id"]))
    return {"prefix": prefix, "now": now.strftime("%H%M"), "sectors": out, "scenarios": scns}


# --- jeden lot: plan, CDM, ATFCM, historia i wejścia w sektory (jak szczegóły lotu w NM UI)

BAND = {"H": "górny", "M": "środkowy", "L": "dolny"}
FIR_PART = {"ALLFIR": "cały FIR", "NFIR": "FIR północ", "SFIR": "FIR południe"}


def tv_label(tv: str) -> str:
    """Opis traffic volume po polsku, ta sama logika co tvLabel w viffchart.js."""
    code = re.sub(r"^EP-", "", tv)
    if m := re.fullmatch(r"([A-Z])([HML])", code):
        return f"sektor {m[1]} {BAND[m[2]]}"
    if m := re.fullmatch(r"([A-Z]{2})TMA(D?)", code):
        return f"TMA EP{m[1]}{' (D)' if m[2] else ''}"
    if m := re.match(r"(ALLFIR|NFIR|SFIR)(.*)", code):
        return FIR_PART[m[1]] + (" " + m[2] if m[2] else "")
    return "kombinacja sektorów"


def _level(value) -> str | None:
    """'22000' / 'F220' / 'FL220' → 'FL220'; poniżej 6000 ft w stopach."""
    s = str(value or "").strip().upper()
    if m := re.fullmatch(r"FL?(\d{3})", s):
        return "FL" + m[1]
    if s.isdigit() and int(s) > 0:
        n = int(s)
        return f"FL{n // 100:03d}" if n >= 6000 else f"{n} ft"
    return s or None


def _int(value) -> int | None:
    m = re.search(r"\d+", str(value if value is not None else ""))
    return int(m[0]) if m else None


def _duration(value) -> int | None:
    """Czas lotu w minutach: 44 albo '0044' (HHMM)."""
    if isinstance(value, int):
        return value
    s = str(value or "").strip()
    return int(s[:2]) * 60 + int(s[2:]) if re.fullmatch(r"\d{4}", s) else (int(s) if s.isdigit() else None)


def history(text) -> list[dict]:
    """'2026-10-05 19:58 -> Flight processed ...|2026-10-05 20:56 -> Detected in movement...|' → [{time, text}]."""
    out = []
    for part in str(text or "").split("|"):
        part = part.strip()
        if part:
            time, sep, msg = part.partition(" -> ")
            out.append({"time": time.strip(), "text": msg.strip()} if sep else {"time": "", "text": part})
    return out


def flight_profile(callsign: str, days: list, now: datetime, yesterday: dict | None = None) -> list[dict]:
    """Wejścia lotu w traffic volumes z całodobowych danych /etfms/airspaces (days[0] = dziś, days[1] = jutro,
    yesterday = wczoraj, potrzebne tuż po północy).

    Krotka: [callsign, wejście plan., wyjście plan., wejście wylicz., wyjście wylicz., flaga]. Lot przechodzący przez
    pełną godzinę jest w dwóch koszykach, więc usuwamy duplikaty. Godziny są bez daty, więc dobę wejścia bierzemy
    z godziny koszyka (w koszyku 00 wejście 2355 jest z poprzedniej doby). Z jutra bierzemy tylko wejścia przed 12Z,
    a z wczoraj tylko po 12Z, żeby nie złapać innego lotu o tym samym znaku."""
    now_abs = now.hour * 60 + now.minute
    found: dict[tuple, tuple[int, dict]] = {}
    for day, data in [*([(-1, yesterday)] if yesterday is not None else []), *enumerate(days)]:
        for tv, buckets in (data.items() if isinstance(data, dict) else []):
            for b in buckets if isinstance(buckets, list) else []:
                hour = _int(b.get("hour")) if isinstance(b, dict) else None
                for t in (b.get("flights") if isinstance(b, dict) else None) or []:
                    if not isinstance(t, list) or len(t) < 3 or str(t[0]).upper() != callsign:
                        continue
                    p_in, p_out = hhmm(t[1]), hhmm(t[2])
                    c_in, c_out = (hhmm(t[3]) if len(t) > 3 else None), (hhmm(t[4]) if len(t) > 4 else None)
                    entry, exit_ = c_in or p_in, c_out or p_out
                    if not entry:
                        continue
                    e_abs = day * 1440 + _mins(entry)
                    if hour is not None and hour < 24:  # najbliżej godziny koszyka (przejście przez północ)
                        e_abs += round((day * 1440 + hour * 60 - e_abs) / 1440) * 1440
                    if (day == 1 and e_abs >= 1440 + 720) or (day == -1 and e_abs < -720):
                        continue
                    # wyjście po wejściu, także po północy
                    x_abs = e_abs + (((_mins(exit_) - _mins(entry)) % 1440 or 1) if exit_ else 1)
                    state = "past" if x_abs <= now_abs else "now" if e_abs <= now_abs else "next"
                    found[(tv, e_abs)] = (e_abs, {"tv": tv, "label": tv_label(tv), "planned_entry": p_in, "planned_exit": p_out,
                                                  "entry": entry, "exit": exit_, "state": state})
    return [f for _k, f in sorted(found.values(), key=lambda x: (x[0], x[1]["tv"]))]


def _not_found(exc: UpstreamError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code in (400, 404)


async def flight(callsign: str, now: datetime | None = None) -> dict | None:
    """Lot z /ifps/callsign + wejścia w sektory; None, gdy vIFF nie zna lotu."""
    cs = callsign.upper()
    now = now or datetime.now(timezone.utc)
    try:
        data = await _get("/ifps/callsign", {"callsign": cs}, ttl=30)
    except UpstreamError as exc:
        if _not_found(exc):
            return None
        raise
    except ValueError:  # odpowiedź nie jest JSON-em (np. tekst "not found")
        return None
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict) and str(d.get("callsign", "")).upper() == cs), None)
    if not isinstance(data, dict) or str(data.get("callsign") or "").upper() != cs:  # inny znak = nie ten lot
        return None
    fp = data.get("flightPlanning") if isinstance(data.get("flightPlanning"), dict) else {}
    cdm = data.get("cdmData") if isinstance(data.get("cdmData"), dict) else {}
    hist = history(data.get("history"))
    item15 = next((m[1] for h in hist if (m := re.search(r"ITEM15:\s*(.+)$", h["text"]))), None)
    out = departure(data)
    out.update({
        "cid": data.get("cid"), "departure": data.get("departure") or None, "arrival": data.get("arrival") or None,
        "aircraft": fp.get("aircraft_short") or None, "rfl": _level(fp.get("cruise_altitude")), "tas": _int(fp.get("cruise_tas")),
        "route": fp.get("route") or item15, "enroute_min": _duration(fp.get("enrTime")), "fp_valid": fp.get("isValid"),
        "eta": hhmm(data.get("eta")), "on_time": data.get("onTime") or None, "actual_airspace": data.get("actualAirspace") or None,
        "landed": bool(data.get("landed")), "is_cdm": bool(data.get("isCdm")), "revised_ctot": hhmm(data.get("latestRevisedCtot")),
        "ctot_reason": cdm.get("reason") or None, "history": hist, "timestamp": data.get("timeStamp"), "states": FLIGHT_STATES,
    })
    prefix = settings.viff_sector_prefix.upper()
    try:  # wejścia w sektory są dodatkiem: bez nich szczegóły lotu nadal działają
        day = lambda d: _get("/etfms/airspaces", {"filter": prefix, "date": (now + timedelta(days=d)).date().isoformat()}, ttl=60)  # noqa: E731
        days = [await day(0)]
        if now.hour == 23:  # lot po północy jest już w jutrzejszych danych
            days.append(await day(1))
        yesterday = None
        if now.hour == 0:  # sektory sprzed północy są we wczorajszych danych
            try:
                yesterday = await day(-1)
            except (UpstreamError, ValueError):
                yesterday = None
        out["sectors"], out["sectors_error"] = flight_profile(cs, days, now, yesterday), None
    except (UpstreamError, ValueError) as exc:
        out["sectors"], out["sectors_error"] = [], str(exc)
    return out
