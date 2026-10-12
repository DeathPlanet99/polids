"""Dane z sieci VATSIM: kto jest online (data feed v3), rezerwacje stanowisk (ATC bookings),
samoloty w powietrzu i granice FIR z projektu VATSpy.

Dokumentacja API: https://vatsim.dev/services/apis"""

import json
import math
import re
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from ..config import settings
from .http_cache import fetch_text

DATA_FEED = "https://data.vatsim.net/v3/vatsim-data.json"
BOOKINGS = "https://atc-bookings.vatsim.net/api/booking"


_parsed: dict[str, tuple[str, object]] = {}


async def _json(url: str, ttl: int):
    """JSON z cache; ten sam tekst (trafienie w cache) nie jest parsowany ponownie (data feed ma kilka MB)."""
    text = await fetch_text(url, ttl)
    hit = _parsed.get(url)
    if hit and hit[0] is text:
        return hit[1]
    data = json.loads(text)
    _parsed[url] = (text, data)
    return data


async def get_feed() -> dict:
    return await _json(DATA_FEED, 30)


async def get_bookings() -> list[dict]:
    data = await _json(BOOKINGS, 300)
    return data if isinstance(data, list) else data.get("data", [])


def parse_time(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def controller_info(c: dict) -> dict:
    return {"callsign": c.get("callsign", ""), "frequency": c.get("frequency", ""), "name": c.get("name"),
            "cid": c.get("cid"), "rating": c.get("rating"), "logon_time": c.get("logon_time"),
            "text_atis": c.get("text_atis"), "atis_code": c.get("atis_code")}


def match_positions(controllers: list[dict], positions: list, prefer: Collection[str] = ()) -> dict[str, dict]:
    """Dopasowanie zalogowanych kontrolerów do stanowisk z pliku .ese (klucz: callsign stanowiska).

    Najpierw dokładny callsign, potem jak w EuroScope: prefiks, typ (końcówka) i częstotliwość.
    prefer: stanowiska z kolejności przejmowania sektorów sąsiadów. Kontroler dopasowany do innego stanowiska
    (np. EKDK_CTR z vacs-data) jest też wpisany pod stanowiskiem z `prefer` o tym samym prefiksie, typie
    i częstotliwości (EKDK_UN_CTR 136.555 w pliku Danii), z polem `alias_of`: tak rozpozna go EuroScope sąsiada."""
    by_cs = {p.callsign: p for p in positions}
    # indeks (prefiks, typ) → stanowiska w kolejności listy: pierwsze pasujące wygrywa, jak przy przeglądaniu całej listy
    by_key: dict[tuple[str, str], list[tuple[int, object]]] = {}
    for i, p in enumerate(positions):
        if p.prefix:
            by_key.setdefault((p.prefix, p.callsign.split("_")[-1]), []).append((i, p))
    online: dict[str, dict] = {}
    alias: dict[str, dict] = {}  # wpisy pod znakiem sąsiada mają najniższy priorytet: dopisujemy je na końcu
    for c in controllers:
        cs, freq = c.get("callsign", ""), c.get("frequency", "")
        parts = cs.split("_")
        cands = [ip for n in range(1, len(parts)) for ip in by_key.get(("_".join(parts[:n]), parts[-1]), ())
                 if freq[:7] == (ip[1].frequency or "")[:7]]
        if cs in by_cs:
            hit = cs
            online[cs] = controller_info(c)
        elif cands:
            hit = min(cands, key=lambda ip: ip[0])[1].callsign
            online.setdefault(hit, controller_info(c))
        else:
            continue
        twins = [ip for ip in cands if ip[1].callsign in prefer]
        if hit not in prefer and twins:
            alias.setdefault(min(twins, key=lambda ip: ip[0])[1].callsign, controller_info(c) | {"alias_of": hit})
    for cs, info in alias.items():
        if online.get(info["alias_of"], {}).get("callsign") != info["callsign"]:
            # stanowisko `hit` zajął inny kontroler: ten jest tylko pod znakiem sąsiada, bez alias_of
            info = {k: v for k, v in info.items() if k != "alias_of"}
        online.setdefault(cs, info)
    return online


def end_of_utc_day(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)


def bookings_by_callsign(bookings: list[dict], prefix: str = "", now: datetime | None = None,
                         until: datetime | None = None) -> dict[str, list[dict]]:
    """Rezerwacje trwające teraz albo zaczynające się przed `until` (domyślnie: do końca bieżącej doby UTC)."""
    now = now or datetime.now(timezone.utc)
    until = until or end_of_utc_day(now)
    out: dict[str, list[dict]] = {}
    for b in bookings:
        cs = (b.get("callsign") or "").upper()
        start, end = parse_time(b.get("start")), parse_time(b.get("end"))
        if not cs.startswith(prefix.upper()) or not start or not end or end < now or start >= until:
            continue
        out.setdefault(cs, []).append({"callsign": cs, "cid": b.get("cid"), "type": b.get("type"),
                                        "start": start.isoformat(), "end": end.isoformat(),
                                        "active": start <= now <= end})
    for lst in out.values():
        lst.sort(key=lambda x: x["start"])
    return out


@lru_cache(maxsize=1)
def fir_boundaries() -> dict:
    return json.loads((settings.seed_dir / "vatspy_firs.geojson").read_text("utf-8"))


def online_firs(controllers: list[dict]) -> dict[str, list[dict]]:
    """Które granice FIR (id z VATSpy) mają zalogowanego kontrolera CTR/FSS. Najdłuższy pasujący prefiks wygrywa."""
    data = fir_boundaries()
    prefixes = []
    for f in data["features"]:
        for pre in f["properties"]["prefixes"]:
            prefixes.append((pre, [f["properties"]["id"]]))
    for u in data.get("uirs", []):
        prefixes.append((u["id"], [f["properties"]["id"] for f in data["features"]
                                   if any(f["properties"]["id"].split("-")[0] == x for x in u["firs"])]))
    prefixes.sort(key=lambda x: -len(x[0]))
    out: dict[str, list[dict]] = {}
    for c in controllers:
        cs = c.get("callsign", "")
        if cs.split("_")[-1] not in ("CTR", "FSS"):
            continue
        for pre, ids in prefixes:
            if cs == pre or cs.startswith(pre + "_"):
                for i in ids:
                    out.setdefault(i, []).append(controller_info(c))
                break
    return out


# Końcówka znaku -> rodzaj stanowiska na plakietce lotniska (jak w VATSIM Radar: D, G, T, A oraz APP)
FACILITY_TYPES = {"DEL": "DEL", "GND": "GND", "RMP": "GND", "TWR": "TWR", "APP": "APP", "DEP": "APP", "ATIS": "ATIS"}


def airport_atc(controllers: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Stanowiska lotniskowe i zbliżania pogrupowane wg prefiksu znaku i rodzaju.

    Np. EPWA_N_APP i EPWA_TWR -> {"EPWA": {"APP": [...], "TWR": [...]}}. CTR/FSS i obserwatorów pomijamy."""
    out: dict[str, dict[str, list[dict]]] = {}
    for c in controllers:
        parts = c.get("callsign", "").upper().split("_")
        kind = FACILITY_TYPES.get(parts[-1]) if len(parts) > 1 else None
        if kind:
            out.setdefault(parts[0], {}).setdefault(kind, []).append(controller_info(c))
    return out


# Pełny typ w planie lotu ICAO: "B738/M-SDE2E3FGHIRWXY/LB1" (typ/kategoria turbulencji-wyposażenie).
# Myślnik jest wymagany: w formacie FAA "B738/L" litera po ukośniku to wyposażenie, nie turbulencja.
_FP_WAKE = re.compile(r"^(?:\d+/)?[A-Z0-9]{2,4}/([LMHJ])-")


def fp_wake(aircraft: str | None) -> str | None:
    """Kategoria turbulencji (L/M/H/J) z pełnego typu statku powietrznego w planie lotu."""
    m = _FP_WAKE.match((aircraft or "").strip().upper())
    return m.group(1) if m else None


def assigned_squawk(fp: dict | None) -> str | None:
    """Kod SSR przydzielony przez ATC (assigned_transponder); '0000' i puste = brak przydziału."""
    v = str((fp or {}).get("assigned_transponder") or "").strip()
    return v if re.fullmatch(r"[0-7]{4}", v) and v != "0000" else None


def level_ft(value) -> int | None:
    """Poziom z planu lotu w stopach: '37000', 'FL370', 'F370', 'A045', '370' (setki stóp)."""
    s = str(value or "").strip().upper()
    if m := re.fullmatch(r"(?:FL|F|A)(\d{2,3})", s):
        return int(m.group(1)) * 100
    if s.isdigit():
        return int(s) * 100 if int(s) < 1000 else int(s)
    return None


def pilot_info(p: dict) -> dict:
    fp = p.get("flight_plan") or {}
    return {"callsign": p.get("callsign"), "cid": p.get("cid"), "name": p.get("name"),
            "lat": p.get("latitude"), "lon": p.get("longitude"), "altitude": p.get("altitude"),
            "groundspeed": p.get("groundspeed"), "heading": p.get("heading"), "squawk": p.get("transponder"),
            "aircraft": fp.get("aircraft_short"), "departure": fp.get("departure"), "arrival": fp.get("arrival"),
            "route": fp.get("route"), "rfl": fp.get("altitude"), "rules": fp.get("flight_rules"),
            # paski postępu lotu (AERODROME › RUCH)
            "deptime": fp.get("deptime"), "aircraft_icao": fp.get("aircraft"), "wake": fp_wake(fp.get("aircraft")),
            "assigned_squawk": assigned_squawk(fp), "remarks": fp.get("remarks")}


RWY = r"(\d{2}[LRC]?)"
_ATIS_PATTERNS = [  # (wzorzec, do czego się odnosi); "both" uzupełnia tylko to, czego nie dały konkretniejsze
    (rf"\b{RWY} FOR (?:LANDING|ARRIVALS?)", "arr"),
    (rf"\b{RWY} FOR (?:TAKE ?-?OFF|DEPARTURES?)", "dep"),
    (rf"\b(?:ARR(?:IVAL)?S?|LANDING|LDG)(?: RWY| RUNWAY)?(?: IN USE)?:? {RWY}\b", "arr"),
    (rf"\b(?:DEP(?:ARTURE)?S?|TAKE ?-?OFF|TKOF)(?: RWY| RUNWAY)?(?: IN USE)?:? {RWY}\b", "dep"),
    (rf"\b(?:RWY|RUNWAY)S? (?:IN USE )?{RWY} IN USE\b", "both"),
    (rf"\b(?:RWY|RUNWAY)S? IN USE:? {RWY}\b", "both"),
]
_ATIS_FALLBACK = rf"\b(?:RWY|RUNWAY) {RWY}\b"  # tylko gdy nic konkretniejszego nie pasuje
# Słowa przed "RUNWAY 33" w tym samym zdaniu mówiące, do czego jest pas, np. vATIS EPWA:
# "EXPECT UNIFORM ARRIVALS FOR RNP ZULU APPROACH RUNWAY 33 . DEPARTURES RUNWAY 29 ."
_ARR_CUE = r"ARR(?:IVAL|IVALS|IVING|S)?|LANDINGS?|LDG|APPROACH(?:ES)?|APCH|APP"
_DEP_CUE = r"DEP(?:ARTURE|ARTURES|ARTING|S)?|TAKE ?-?OFFS?|TKOF"
_CUE = re.compile(rf"\b(?:(?P<arr>{_ARR_CUE})|(?P<dep>{_DEP_CUE}))\b")
_CUE_BOTH = re.compile(rf"\b(?:(?:{_ARR_CUE}) (?:AND|&) (?:{_DEP_CUE})|(?:{_DEP_CUE}) (?:AND|&) (?:{_ARR_CUE}))\b")
_MENTION = re.compile(rf"\b(?:RWY|RUNWAY)S?(?: IN USE)?:? {RWY}\b")
# zdanie o pasie zamkniętym albo niedostępnym nie wskazuje pasa w użyciu
_UNUSABLE = re.compile(r"\b(?:CLOSED|CLSD|NOT AVBL|NOT AVAILABLE|UNAVAILABLE|U/S|OUT OF SERVICE|WIP|WORK IN PROGRESS|"
                       r"NOT IN USE)\b")


def _cue_runways(text: str, ok) -> dict:
    """Pas po słowie ARRIVAL/APPROACH/LANDING albo DEPARTURE/TAKEOFF w tym samym zdaniu (najbliższe słowo przed pasem)."""
    out = {"arr": None, "dep": None}
    for clause in re.split(r"\.(?=\s|$)|[,;]", text):  # kropka w 118.305 nie kończy zdania
        if _UNUSABLE.search(clause):
            continue
        start = 0
        for m in _MENTION.finditer(clause):
            segment, start = clause[start:m.start()], m.end()
            cues = list(_CUE.finditer(segment))
            if not cues or not ok(m.group(1)):
                continue
            kinds = ("arr", "dep") if _CUE_BOTH.search(segment) else ("arr" if cues[-1].group("arr") else "dep",)
            for kind in kinds:
                out[kind] = out[kind] or m.group(1)
    return out


def parse_atis(lines: list[str] | None, designators: list[str] | None = None, code: str | None = None) -> dict:
    """Litera ATIS i pas(y) w użyciu z tekstu ATIS z sieci VATSIM.

    Działa z typowymi formatami vATIS: "RWY 29 IN USE", "RUNWAY IN USE 33", "33 FOR LANDING, 29 FOR TAKEOFF",
    "ARR RWY 33 DEP RWY 29", "EXPECT ILS APPROACH RUNWAY 33. DEPARTURES RUNWAY 29". Pas do lądowania i do startu
    wskazany wprost ma pierwszeństwo przed ogólnym "RWY 29 IN USE". Pas musi istnieć na lotnisku (jeśli podano
    listę `designators`)."""
    text = re.sub(r"\s+", " ", " ".join(lines or [])).upper()
    letter = code or None
    if not letter:
        m = re.search(r"\b(?:INFORMATION|INFO|ATIS) ([A-Z])\b", text)
        letter = m.group(1) if m else None
    ok = lambda r: not designators or r in designators  # noqa: E731
    found = {"arr": None, "dep": None}

    def take(kinds: tuple, rwy: str) -> None:
        for kind in kinds:
            found[kind] = found[kind] or rwy

    for pattern, kind in _ATIS_PATTERNS:
        if kind == "both":
            continue
        for m in re.finditer(pattern, text):
            if ok(m.group(1)):
                take((kind,), m.group(1))
    for kind, rwy in _cue_runways(text, ok).items():
        if rwy:
            take((kind,), rwy)
    for pattern, kind in _ATIS_PATTERNS:
        if kind != "both":
            continue
        for m in re.finditer(pattern, text):
            if ok(m.group(1)):
                take(("arr", "dep"), m.group(1))
    arr, dep = found["arr"], found["dep"]
    if not arr and not dep:
        arr = next((m.group(1) for m in re.finditer(_ATIS_FALLBACK, text) if ok(m.group(1))), None)
    return {"letter": letter, "arr": arr or dep, "dep": dep or arr, "text": text}


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def airport_traffic(feed: dict, icao: str, lat: float | None = None, lon: float | None = None) -> dict:
    """Loty z planem do/z lotniska: odloty, przyloty i plany złożone przed połączeniem (prefile)."""
    icao = icao.upper()
    out = {"departures": [], "arrivals": [], "prefiles": []}
    for p in feed.get("pilots", []):
        fp = p.get("flight_plan") or {}
        dep, arr = fp.get("departure"), fp.get("arrival")
        if icao not in (dep, arr):
            continue
        info = pilot_info(p)
        info["dist_nm"] = (round(distance_nm(lat, lon, info["lat"], info["lon"]))
                           if lat is not None and info["lat"] is not None else None)
        gs = info["groundspeed"] or 0
        info["state"] = "ground" if gs < 50 else "air"
        if arr == icao and dep != icao:
            info["eta_min"] = round(info["dist_nm"] / gs * 60) if info["dist_nm"] is not None and gs >= 50 else None
            out["arrivals"].append(info)
        else:
            out["departures"].append(info)
    for p in feed.get("prefiles", []):
        fp = p.get("flight_plan") or {}
        if icao in (fp.get("departure"), fp.get("arrival")):
            out["prefiles"].append({"callsign": p.get("callsign"), "name": p.get("name"), "cid": p.get("cid"),
                                    "aircraft": fp.get("aircraft_short"), "departure": fp.get("departure"),
                                    "arrival": fp.get("arrival"), "deptime": fp.get("deptime"),
                                    "rfl": fp.get("altitude"), "rules": fp.get("flight_rules"),
                                    "route": fp.get("route"), "aircraft_icao": fp.get("aircraft"),
                                    "wake": fp_wake(fp.get("aircraft")), "remarks": fp.get("remarks")})
    out["arrivals"].sort(key=lambda x: (x["dist_nm"] is None, x["dist_nm"] or 0))
    out["departures"].sort(key=lambda x: (x["state"] != "ground", x["dist_nm"] or 0))
    out["prefiles"].sort(key=lambda x: x.get("deptime") or "")
    return out
