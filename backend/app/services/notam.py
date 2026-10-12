"""NOTAM-y z serwera PL vACC (cv.plvacc.pl) w formacie ICAO.

get_notams(icao) – NOTAM-y jednego lotniska (AERODROME, OVERVIEW).
fir_notams(now) – SYT OP: wszystkie NOTAM-y FIR EPWW obowiązujące teraz (sam FIR i główne lotniska, bez duplikatów)."""

import asyncio
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings
from .http_cache import UpstreamError, fetch_text, peek

NOTAM_START = re.compile(r"\n+(?=\s*\(?[A-Z]\d{4}/\d{2}\s+NOTAM[NRC]\b)")
FIELD = re.compile(r"\b([QABCDEFG])\)\s*")


def notam_url(icao: str) -> str:
    return f"{settings.notam_server.rstrip('/')}/{settings.notam_prefix}{icao.upper()}{settings.notam_suffix}"


def split_notams(text: str) -> list[dict]:
    """Dzieli tekst na pojedyncze NOTAM-y i wyciąga pola Q)…G). Format odpowiedzi serwera
    nie jest udokumentowany, więc przy nieznanym formacie zwracamy całość jako jeden wpis."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text).strip()
    if not text:
        return []
    parts = [p.strip() for p in NOTAM_START.split(text) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for p in parts:
        ident = re.match(r"\(?([A-Z]\d{4}/\d{2})", p)
        fields = {}
        pieces = FIELD.split(p)
        for key, val in zip(pieces[1::2], pieces[2::2]):
            fields[key] = val.strip().rstrip(")").strip()
        out.append({"id": ident.group(1) if ident else None, "raw": p, "fields": fields, **validity(fields)})
    return out


def notam_time(v: str | None) -> datetime | None:
    """Pole B)/C) w formacie RRMMDDggmm (UTC)."""
    m = re.match(r"\s*(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", v or "")
    if not m:
        return None
    try:
        return datetime(2000 + int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), tzinfo=timezone.utc)
    except ValueError:
        return None


def validity(fields: dict) -> dict:
    """Okres ważności: start (B), koniec (C), PERM, EST (szacowany koniec) i harmonogram (D)."""
    c = (fields.get("C") or "").upper()
    start, end = notam_time(fields.get("B")), notam_time(c)
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "perm": "PERM" in c, "est": "EST" in c, "schedule": fields.get("D") or None}


def for_aerodrome(notams: list[dict], icao: str) -> list[dict]:
    """Tylko NOTAM-y, których pole A) wymienia dane lotnisko (serwer zwraca też NOTAM-y innych lotnisk i FIR).
    NOTAM bez pola A) zostaje tylko wtedy, gdy w jego treści pada kod lotniska."""
    icao = icao.upper()
    out = []
    for n in notams:
        a = n["fields"].get("A")
        if a is not None:
            if icao in re.findall(r"[A-Z]{4}", a):
                out.append(n)
        elif re.search(rf"\b{icao}\b", n["raw"]):
            out.append(n)
    return out


async def get_notams(icao: str) -> dict:
    url = notam_url(icao)
    text = await fetch_text(url, settings.notam_cache_seconds)
    allnotams = split_notams(text)
    notams = for_aerodrome(allnotams, icao)
    return {"icao": icao.upper(), "source": url, "notams": notams, "other": len(allnotams) - len(notams)}


# --- SYT OP: NOTAM-y całego FIR EPWW ---------------------------------------------------------------------------------

FIR = "EPWW"
# główne lotniska FIR EPWW – ta sama lista co w AERODROME i OVERVIEW (services/overview.py)
FIR_AERODROMES = ["EPBY", "EPGD", "EPKK", "EPKT", "EPLB", "EPLL", "EPMO", "EPPO", "EPRA", "EPRZ", "EPSC", "EPSY",
                  "EPWA", "EPWR", "EPZG"]
FIR_LOCATIONS = [FIR, *FIR_AERODROMES]
FIR_CONCURRENCY = 4          # najwyżej tyle równoczesnych zapytań do serwera NOTAM (16 lokalizacji)
HEADER = re.compile(r"\(?\s*([A-Z]\d{4}/\d{2})\s+NOTAM([NRC])\b(?:\s+([A-Z]\d{4}/\d{2}))?")
ID = re.compile(r"([A-Z])(\d{4})/(\d{2})")
ICAO4 = re.compile(r"\b[A-Z]{4}\b")

# Q) kod NOTAM: litery 2–3 = czego dotyczy, 4–5 = stan (ICAO Doc 8126); skróty jak w treści NOTAM-ów
Q_SUBJECT = {
    "AC": "CTR", "AD": "ADIZ", "AE": "CTA", "AF": "FIR", "AH": "UTA", "AN": "RNAV RTE", "AP": "REP POINT",
    "AR": "ATS RTE", "AT": "TMA", "AU": "UIR", "AX": "INTERSECTION", "AZ": "ATZ",
    "CA": "A/G FAC", "CB": "ADS-B", "CC": "ADS-C", "CD": "CPDLC", "CE": "EN-ROUTE RADAR", "CM": "SMR", "CP": "PAR",
    "CS": "SSR", "CT": "TAR",
    "FA": "AD", "FF": "RFF", "FG": "GND MOV CTL", "FH": "HELI AREA", "FI": "DE-ICING", "FL": "LDI", "FM": "MET SER",
    "FP": "HELIPORT", "FU": "FUEL", "FW": "WDI", "FZ": "CUSTOMS",
    "GA": "GNSS AD", "GW": "GNSS AREA",
    "IC": "ILS", "ID": "ILS DME", "IG": "GP", "II": "IM", "IL": "LOC", "IM": "MM", "IN": "LOC", "IO": "OM",
    "IS": "ILS CAT I", "IT": "ILS CAT II", "IU": "ILS CAT III", "IW": "MLS",
    "LA": "ALS", "LB": "AD BCN", "LC": "RCLL", "LE": "REDL", "LF": "SFL", "LH": "HIRL", "LI": "RENL", "LL": "LIRL",
    "LM": "MIRL", "LP": "PAPI", "LR": "LDG AREA LGT", "LS": "STWL", "LT": "THR LGT", "LV": "VASIS", "LW": "HELI LGT",
    "LX": "TWY CL LGT", "LY": "TWY EDGE LGT", "LZ": "TDZ LGT",
    "MA": "MOV AREA", "MB": "BEARING STRENGTH", "MC": "CWY", "MD": "DECLARED DIST", "MG": "TGS", "MH": "ARST GEAR",
    "MK": "PRKG AREA", "MM": "MARKINGS", "MN": "APRON", "MO": "STOPBAR", "MP": "ACFT STANDS", "MR": "RWY", "MS": "SWY",
    "MT": "THR", "MU": "TURN BAY", "MW": "STRIP", "MX": "TWY", "MY": "RAPID EXIT TWY",
    "NA": "ALL RDO NAV", "NB": "NDB", "ND": "DME", "NL": "L", "NM": "VOR/DME", "NN": "TACAN", "NT": "VORTAC",
    "NV": "VOR", "NX": "DF",
    "OA": "AIS", "OB": "OBST", "OE": "ACFT ENTRY RQMNTS", "OL": "OBST LGT", "OR": "RCC",
    "PA": "STAR", "PB": "VFR ARR", "PC": "CONTINGENCY PROC", "PD": "SID", "PE": "VFR DEP", "PF": "FLOW CTL PROC",
    "PH": "HLDG PROC", "PI": "INSTR APCH PROC", "PK": "VFR APCH PROC", "PL": "FPL PROCESSING", "PM": "AD OPR MINIMA",
    "PN": "NOISE ABATEMENT", "PO": "OCA/OCH", "PR": "RDO FAILURE PROC", "PT": "TA/TL", "PU": "MISSED APCH PROC",
    "PX": "MNM HLDG ALT", "PZ": "ADIZ PROC",
    "RA": "AIRSPACE RESERVATION", "RD": "DANGER AREA", "RM": "MIL OPS AREA", "RO": "OVERFLYING", "RP": "PROHIBITED AREA",
    "RR": "RESTRICTED AREA", "RT": "TEMPO RESTRICTED AREA",
    "SA": "ATIS", "SB": "ARO", "SC": "ACC", "SE": "FIS", "SF": "AFIS", "SL": "FLOW CTL CENTRE", "SP": "APP",
    "SS": "FSS", "ST": "TWR", "SU": "UAC", "SV": "VOLMET",
    "WA": "AIR DISPLAY", "WB": "AEROBATICS", "WC": "CAPTIVE BALLOON/KITE", "WD": "DEMOLITION OF EXPLOSIVES",
    "WE": "EXERCISES", "WF": "AIR REFUELLING", "WG": "GLIDER FLYING", "WH": "BLASTING", "WJ": "TOWING",
    "WL": "FREE BALLOON", "WM": "MISSILE/GUN FIRING", "WP": "PJE", "WR": "RADIOACTIVE MATERIALS", "WS": "GAS BURNING",
    "WT": "MASS MOVEMENT OF ACFT", "WU": "UAS", "WV": "FORMATION FLT", "WW": "VOLCANIC ACT", "WY": "AERIAL SURVEY",
    "WZ": "MODEL FLYING",
}
Q_CONDITION = {
    "AC": "WITHDRAWN FOR MAINT", "AD": "AVBL DAY OPS", "AF": "FLT CHECKED", "AG": "GND CHECKED ONLY",
    "AH": "HR SER CHANGED", "AK": "RESUMED NORMAL OPS", "AL": "OPR SUBJ PREVIOUS COND", "AM": "MIL OPS ONLY",
    "AN": "AVBL NIGHT OPS", "AO": "OPR", "AP": "AVBL PPR", "AR": "AVBL ON REQ", "AS": "U/S", "AU": "NOT AVBL",
    "AW": "COMPLETELY WITHDRAWN", "AX": "SHUTDOWN CNL",
    "CA": "ACT", "CC": "COMPLETED", "CD": "DEACT", "CE": "ERECTED", "CF": "FREQ CHANGED", "CG": "DOWNGRADED",
    "CH": "CHANGED", "CI": "IDENT CHANGED", "CL": "REALIGNED", "CM": "DISPLACED", "CN": "CNL", "CO": "OPR",
    "CP": "REDUCED POWER", "CR": "TEMPO REPLACED", "CS": "INSTALLED", "CT": "ON TEST",
    "HA": "BRAKING ACTION", "HB": "FRICTION", "HC": "COMPACTED SNOW", "HD": "DRY SNOW", "HE": "WATER",
    "HF": "FREE OF SNOW AND ICE", "HG": "GRASS CUTTING", "HH": "HAZARD", "HI": "ICE", "HJ": "LAUNCH PLANNED",
    "HK": "BIRD MIGRATION", "HL": "SNOW CLR COMPLETED", "HM": "MARKED", "HN": "WET SNOW/SLUSH", "HO": "OBSCURED BY SNOW",
    "HP": "SNOW CLR IN PROGRESS", "HQ": "OPS CNL", "HR": "STANDING WATER", "HS": "SANDING", "HT": "SIGNAL AREA ONLY",
    "HU": "LAUNCH IN PROGRESS", "HV": "WORK COMPLETED", "HW": "WIP", "HX": "BIRD CONCENTRATION", "HY": "SNOW BANKS",
    "HZ": "FROZEN RUTS",
    "LA": "AUX PWR", "LB": "RESERVED FOR BASED ACFT", "LC": "CLSD", "LD": "UNSAFE", "LE": "WO AUX PWR",
    "LF": "INTERFERENCE", "LG": "WO IDENT", "LH": "U/S FOR HEAVIER ACFT", "LI": "CLSD IFR", "LK": "FIXED LGT",
    "LL": "USABLE LEN/WID", "LN": "CLSD NIGHT", "LP": "PROHIBITED", "LR": "RWY/TWY ONLY", "LS": "SUBJ INTERRUPTION",
    "LT": "LIMITED", "LV": "CLSD VFR", "LW": "WILL TAKE PLACE", "LX": "CTN ADZ",
    "XX": "",
}


def parse_q(q: str | None) -> dict:
    """Pole Q): FIR/QKKSS/ruch/cel/zasięg/dolna/górna/współrzędne, np. EPWW/QMRLC/IV/NBO/A/000/999/5210N02058E005.
    Brakujące albo nieczytelne części zostają puste (None)."""
    parts = [p.strip() for p in (q or "").split("/")]
    parts += [""] * (8 - len(parts))
    code = parts[1].upper() if re.fullmatch(r"Q[A-Z]{4}", parts[1].upper()) else None
    subject, condition = (code[1:3], code[3:5]) if code else (None, None)
    return {"fir": parts[0].upper() if re.fullmatch(r"[A-Za-z]{4}", parts[0]) else None, "qcode": code,
            "subject": subject, "condition": condition,
            "subject_text": Q_SUBJECT.get(subject) if subject else None,
            "condition_text": (Q_CONDITION.get(condition) or None) if condition else None,
            "traffic": parts[2] or None, "purpose": parts[3] or None, "scope": parts[4] or None,
            "lower": _fl(parts[5]), "upper": _fl(parts[6]), "coords": parts[7] or None}


def _fl(v: str) -> int | None:
    """Dolna/górna granica z Q) w setkach stóp (000–999)."""
    return int(v) if re.fullmatch(r"\d{3}", v) else None


def is_active(n: dict, now: datetime) -> bool:
    """NOTAM obowiązujący teraz: zaczął się (B) i jeszcze się nie skończył (C albo PERM); bez B)/C) – pokazujemy.
    Te same zasady co AERODROME › OVERVIEW (services/overview.py, notam_active)."""
    start = datetime.fromisoformat(n["start"]) if n.get("start") else None
    end = datetime.fromisoformat(n["end"]) if n.get("end") else None
    if start and start > now:
        return False
    return not (end and end < now and not n.get("perm"))


def id_key(nid: str | None) -> tuple:
    """Kolejność numerów: seria, rok, numer (A0999/25 < A0001/26 < B0001/26); bez numeru na końcu."""
    m = ID.fullmatch(nid or "")
    return (0, m[1], int(m[3]), int(m[2])) if m else (1, "", 0, 0)


def in_fir(n: dict, fir: str = FIR) -> bool:
    """NOTAM dotyczy FIR EPWW: Q) zaczyna się od EPWW albo A) wymienia FIR lub lokalizację EP**."""
    q = parse_q(n["fields"].get("Q"))
    codes = ICAO4.findall(n["fields"].get("A") or "")
    return q["fir"] == fir or any(c == fir or c.startswith(fir[:2]) for c in codes)


def canonical_fields(raw: str) -> dict:
    """Pola Q)…G) w kolejności ICAO (Q A B C D E, potem F G). Znaczniki w treści E), np. "E) FLW AREAS ACT: A) EP TRA 08
    B) EP TRA 09", zostają w E) i nie nadpisują pól A)/B) (split_notams, wspólne z AERODROME, bierze każdy znacznik).
    Po E) liczą się tylko ostatnie F) i G). Końcowy nawias NOTAM-u "(… )" zdejmowany raz, nie z treści."""
    marks = list(FIELD.finditer(raw))
    chosen, last = [], -1
    for i, m in enumerate(marks):
        k = "QABCDEFG".index(m[1])
        if k <= last:
            continue
        chosen.append(m)
        last = k
        if m[1] == "E":
            rest = marks[i + 1:]
            f = [x for x in rest if x[1] == "F"][-1:]
            g = [x for x in rest if x[1] == "G" and (not f or x.start() > f[0].start())][-1:]
            chosen += f + g
            break
    fields = {}
    for m, nxt in zip(chosen, chosen[1:] + [None]):
        val = raw[m.end():nxt.start() if nxt else len(raw)].strip()
        if nxt is None and raw.lstrip().startswith("(") and val.endswith(")"):
            val = val[:-1].rstrip()
        fields[m[1]] = val
    return fields


def _canonical(n: dict) -> dict:
    fields = canonical_fields(n["raw"])
    return {**n, "fields": fields, **validity(fields)} if fields else n


def fir_item(n: dict, icao: str, now: datetime) -> dict:
    """Jeden NOTAM do listy SYT OP: numer, lokalizacja z A), dekodowany Q), ważność B)/C)/D), granice F)/G), treść E)."""
    f = n["fields"]
    codes = ICAO4.findall(f.get("A") or "") or [icao]
    head = HEADER.match(n["raw"])
    q = parse_q(f.get("Q"))
    return {"id": n["id"], "type": head[2] if head else None, "ref": head[3] if head else None,
            "location": codes[0], "locations": codes, **q,
            "start": n["start"], "end": n["end"], "perm": n["perm"], "est": n["est"], "schedule": n["schedule"],
            "lower_limit": f.get("F") or None, "upper_limit": f.get("G") or None,
            "text": f.get("E") or None, "raw": n["raw"], "active": is_active(n, now)}


# --- błędy serwera NOTAM: krótko, po polsku i bez adresu (z kodem lokalizacji), żeby te same błędy różnych lokalizacji
# sklejały się w jedną linię: "EPGD, EPKK – HTTP 503" zamiast 16 różnych komunikatów httpx z adresem i linkiem MDN
TIMEOUT_TEXT = "brak odpowiedzi (timeout)"
STALE_TEXT = "brak odpowiedzi – starsze dane"
FIR_FAIL_SECONDS = 90       # lokalizacja bez odpowiedzi: tyle sekund bez ponownych zapytań (wynik z pamięci)
_fir_failed: dict[str, tuple[float, str]] = {}      # ICAO → (time.monotonic() błędu, opis)


def _error_text(exc: BaseException) -> str:
    cause = exc.__cause__ if isinstance(exc, UpstreamError) and exc.__cause__ is not None else exc
    if isinstance(cause, httpx.TimeoutException):
        return TIMEOUT_TEXT
    if isinstance(cause, httpx.HTTPStatusError):
        return f"HTTP {cause.response.status_code}"
    if isinstance(cause, httpx.ConnectError):
        return "brak połączenia"
    if isinstance(cause, (httpx.NetworkError, httpx.ProtocolError)):
        return "połączenie przerwane"
    msg = str(exc)
    if msg.startswith("http"):              # UpstreamError: "<adres>: <komunikat>"
        msg = msg.split(": ", 1)[1] if ": " in msg else ""
    msg = re.sub(r"'?https?://\S+", "", msg.strip().split("\n")[0])
    msg = re.sub(r"\s+", " ", msg).strip(" :")
    if len(msg) > 80:
        msg = msg[:79].rstrip() + "…"
    return msg or f"błąd ({type(cause).__name__})"


class _StillDown(Exception):
    """Lokalizacja nie odpowiedziała przed chwilą (FIR_FAIL_SECONDS) i nie ma jej w cache: ten sam błąd bez zapytania."""


def fir_deadline() -> float:
    """Najdłuższe czekanie na całą listę: dwie tury zapytań. Gdy serwer NOTAM wisi, odpowiedź przychodzi po ok. 20 s
    (a nie 4 tury × http_timeout); lokalizacje bez odpowiedzi do tego czasu – TIMEOUT_TEXT."""
    return 2 * settings.http_timeout + 1


async def _fetch_location(icao: str, sem: asyncio.Semaphore) -> tuple[list[dict], float]:
    """NOTAM-y lokalizacji i wiek danych w sekundach (ile temu pobrane z serwera NOTAM).

    fetch_text przy błędzie serwera oddaje po cichu starsze dane z cache: wtedy wiek >= ttl i lokalizacja trafia do
    `stale`. Lokalizacja, która niedawno nie odpowiedziała, przez FIR_FAIL_SECONDS nie jest pytana ponownie."""
    url, ttl = notam_url(icao), settings.notam_cache_seconds
    hit, recent = peek(url), _fir_failed.get(icao)
    if recent and time.monotonic() - recent[0] < FIR_FAIL_SECONDS and not (hit and hit[0] < ttl):
        if hit is None:
            raise _StillDown(recent[1])
        return split_notams(hit[1]), hit[0]
    async with sem:
        text = await fetch_text(url, ttl)
    hit = peek(url)
    age = hit[0] if hit else 0.0
    if age >= ttl:
        _fir_failed[icao] = (time.monotonic(), STALE_TEXT)
    else:
        _fir_failed.pop(icao, None)
    return split_notams(text), age


def clear_fir_failures():
    _fir_failed.clear()


async def fir_notams(now: datetime | None = None, include_inactive: bool = False,
                     locations: list[str] | None = None) -> dict:
    """SYT OP: NOTAM-y FIR EPWW i głównych lotnisk z serwera PL vACC (cache jak przy pojedynczym lotnisku),
    pobierane równolegle (najwyżej FIR_CONCURRENCY naraz, całość najwyżej fir_deadline()), bez duplikatów (po numerze),
    tylko obowiązujące teraz. Serwer zwraca w odpowiedzi także NOTAM-y innych lokalizacji: zostają te z FIR EPWW
    (Q) EPWW albo A) EP**). NOTAMC usuwa anulowany NOTAM, NOTAMR – zastąpiony.

    Awaria części lokalizacji: lista z pozostałych + `failed` (bez danych) i `stale` (serwer nie odpowiedział, dane
    z wcześniejszego pobrania). `updated` – czas pobrania najstarszych danych z serwera NOTAM (nie czas odpowiedzi)."""
    now = now or datetime.now(timezone.utc)
    locations = locations or FIR_LOCATIONS
    ttl = settings.notam_cache_seconds
    sem = asyncio.Semaphore(FIR_CONCURRENCY)

    async def one(icao):
        try:
            notams, age = await _fetch_location(icao, sem)
            return icao, notams, age, None
        except _StillDown as exc:
            return icao, None, 0.0, str(exc)
        except (UpstreamError, ValueError) as exc:
            error = _error_text(exc)
            _fir_failed[icao] = (time.monotonic(), error)
            return icao, None, 0.0, error

    tasks = [asyncio.ensure_future(one(i)) for i in locations]
    done, pending = await asyncio.wait(tasks, timeout=fir_deadline())
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results = []
    for icao, task in zip(locations, tasks):
        if task in done:
            results.append(task.result())
        else:
            _fir_failed[icao] = (time.monotonic(), TIMEOUT_TEXT)
            results.append((icao, None, 0.0, TIMEOUT_TEXT))

    seen, items, gone, failed, stale, ages = set(), [], set(), [], [], []
    for icao, notams, age, error in results:
        if error is not None:
            failed.append({"icao": icao, "error": error})
            continue
        ages.append(age)
        if age >= ttl:
            stale.append({"icao": icao, "since": (now - timedelta(seconds=age)).isoformat(timespec="seconds")})
        notams = [_canonical(n) for n in notams]
        own = {id(n) for n in for_aerodrome(notams, icao)}
        for n in notams:
            if id(n) not in own and not (n["fields"].get("A") is not None and in_fir(n)):
                continue
            key = n["id"] or re.sub(r"\s+", " ", n["raw"])
            if key in seen:
                continue
            seen.add(key)
            item = fir_item(n, icao, now)
            if item["type"] in ("R", "C") and item["ref"]:
                gone.add(item["ref"])           # zastąpiony albo anulowany NOTAM nie obowiązuje
            if item["type"] == "C":
                continue
            items.append(item)
    items = [n for n in items if n["id"] not in gone]
    inactive = sum(1 for n in items if not n["active"])
    if not include_inactive:
        items = [n for n in items if n["active"]]
    # domyślna kolejność: najpierw sam FIR EPWW, potem lotniska alfabetycznie, w każdej grupie po numerze
    items.sort(key=lambda n: (n["location"] != FIR, n["location"], id_key(n["id"])))
    errors = {}
    for f in failed:
        errors.setdefault(f["error"], []).append(f["icao"])
    error = "; ".join(f"{', '.join(locs)}: {msg}" for msg, locs in errors.items())
    updated = (now - timedelta(seconds=max(ages))).isoformat(timespec="seconds") if ages else None
    return {"fir": FIR, "locations": locations, "now": now.isoformat(timespec="seconds"), "updated": updated,
            "source": settings.notam_server, "notams": items, "count": len(items), "inactive": inactive,
            "failed": failed, "stale": stale, "error": f"Serwer NOTAM: {error}" if failed else None}


# --- krótkie nazwy lotnisk jak w PANDORZE ("GDANSK LECH WALESA"), a nie "Kraków John Paul II International Airport"
_GENERIC = re.compile(r"\b(INTERNATIONAL|AIRPORT|AIRFIELD|AIRSTRIP|FIELD|MILITARY|AIR BASE)\b")
# miasto przed nazwą z navdata, gdy navdata podaje samą dzielnicę albo patrona (EPKK "BALICE") – jak w AERODROME
CITY = {"EPWA": "WARSZAWA", "EPMO": "WARSZAWA", "EPKK": "KRAKOW", "EPKT": "KATOWICE", "EPGD": "GDANSK",
        "EPPO": "POZNAN", "EPWR": "WROCLAW", "EPRZ": "RZESZOW", "EPSC": "SZCZECIN", "EPZG": "ZIELONA GORA"}


def fold(text: str | None) -> str:
    """WIELKIE LITERY bez znaków diakrytycznych: "Łódź" → "LODZ"."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(str.maketrans({"ł": "l", "Ł": "L", "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", s.upper()).strip()


def short_name(icao: str, nav_name: str | None, csv_name: str | None = None) -> str:
    """Nazwa z navdata (jak w PANDORZE), dla części lotnisk z miastem z przodu ("KRAKOW BALICE"); bez navdata z
    airports.csv bez słów ogólnych ("Pruszcz Gdański Air Base" → "PRUSZCZ GDANSKI"; tam miasto zwykle już jest)."""
    if not (nav_name and nav_name.strip()):
        return re.sub(r"\s+", " ", _GENERIC.sub(" ", fold(csv_name))).strip(" -/") or fold(csv_name)
    name, city = fold(nav_name), CITY.get(icao.upper())
    return f"{city} {name}" if city and city not in name else name
