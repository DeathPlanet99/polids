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


# Odczyt pasa z ATIS. Każdą wzmiankę o pasie ("RWY 33", "RUNWAYS 25L AND 25C", "RWY24", albo goły numer tuż przy
# słowie ARR/DEP/APCH) klasyfikujemy po słowach wokół niej:
#   1. do lądowania albo do startu: słowa po pasie ("33 FOR LANDING", "RWY 33 IN USE FOR ARR AND DEP", "RWY 26R LDG",
#      "RWY 24 (ARR)", "RWY 33, LANDING ONLY"), a gdy ich nie ma, najbliższe słowo przed pasem w tym samym zdaniu
#      ("EXPECT UNIFORM ARRIVALS FOR RNP ZULU APPROACH RUNWAY 33", "EXPECT ILS Z RWY 26R", "DEPARTURES RUNWAY 29",
#      "LDG RWY 23 T/O RWY 33");
#   2. ogólne "RWY 29 IN USE", "RUNWAY IN USE 33", "ACTIVE RWY 29" (lądowanie i start), gdy 1. nic nie dało;
#   3. pas bez opisu wymieniony przed pasami z klas 1-2 ("RUNWAY 33 . DEPARTURES RUNWAY 29"): to, czego brakuje.
# W klasie wygrywa pierwszy pas w tekście ("RUNWAYS 25L AND 25C FOR LANDING" → 25L). Pomijamy pas zamknięty albo
# niedostępny ("RWY 15/33 AND TWY M CLSD", "LANDING NOT PERMITTED ON RUNWAY 33"), na żądanie ("RUNWAY 30 FOR
# DEPARTURES ON REQUEST"), oczekiwany od podanej godziny ("EXPECT RUNWAY 11 FROM 1800", "EXP RWY 33 AT 1800Z"),
# z zakazem albo warunkiem ("NO INTERSECTION DEPARTURES FROM RUNWAY 15", "AIRCRAFT REQUIRING RUNWAY 33 FOR DEPARTURE").
# Pas z niesprawnym wyposażeniem ("ILS RWY 11 GP U/S") liczy się tylko ze słowem roli tuż przed nim ("ARR RWY 33 ILS
# U/S"); uwaga o ptakach albo uskoku wiatru ("BIRD ACT RWY 07") nie opisuje pasa w użyciu.
RWY = r"\d{2}[LRC]?"
# rola przed rzeczownikiem to nie rola pasa: "APPROACH PATH", "APP LIGHTS", "DEP FREQ 127.25", "DEPARTURE DELAYS"
_NOUN = r"(?! (?:PATH|LIGHTS?|LIGHTING|AREA|CONTROL|FREQ\w*|CLEARANCE|DELAYS?)\b)"
_ARR = rf"(?:ARR(?:IVAL|IVALS|IVING|S)?|LANDINGS?|LDG|APPROACH(?:ES)?|APCH|APP){_NOUN}"
_DEP = rf"(?:DEP(?:ARTURE|ARTURES|ARTING|S)?|TAKE ?-?OFFS?|TKOF|T/O){_NOUN}"
_ROLE = rf"(?:{_ARR}|{_DEP})"
_APP_TYPE = r"ILS|LOC|RNP|RNAV|GNSS|GLS|VOR|NDB|VISUAL"
# przed pasem słowo o podejściu też oznacza lądowanie: "EXPECT ILS Z RWY 33", "EXP RNP RWY 29", "LOGDA STAR FOR RWY 33"
# samo EXPECT liczy się tylko bez mocniejszego słowa: "DEPARTING AIRCRAFT EXPECT RUNWAY 29" to start
_PRE_CUE = re.compile(rf"\b(?:(?P<a>{_ARR}|{_APP_TYPE}|STARS?)|(?P<d>{_DEP}|SIDS?)|(?P<x>EXPECT(?:ED)?|EXP))\b")
_MENTION = re.compile(rf"\b(?P<pre>(?:RWY|RUNWAY)S?(?: IN USE)?:? ?)?(?P<rwy>{RWY})(?P<more>(?:(?: AND |/| & ){RWY})*)\b")
_POST_ROLE = re.compile(rf"^(?P<link>(?: IS)?(?: IN USE| ACTIVE)?(?: FOR (?:ALL )?(?:(?:{_APP_TYPE}) )?| ?\(| - | ?[,:] | ))"
                        rf"(?P<roles>{_ROLE}(?:(?: AND |/| & ){_ROLE})?)\b")
# "ARR RWY 33 DEP RWY 29": DEP po 33 to słowo przed następnym pasem, nie opis pasa 33
_NEXT_RUNWAY = re.compile(rf"^ (?:RWY|RUNWAY|IN USE\b|{RWY}\b)")
_FILL = rf"IS|TEMPORARILY|TEMPO|COMPLETELY|FULLY|ALSO|NOW|FOR|AVAILABLE|AVBL|{_ROLE}"
_OUT = r"U/S|UNSERVICEABLE|INOP|INOPERATIVE|OUT OF SERVICE|NOT AVBL|NOT AVAILABLE|UNAVAILABLE"
_TIME = r"(?:AT|FROM|AFTER) \d{4}Z?\b"
# "RWY 15/33 AND TWY M CLSD": zamknięcie dotyczy też pasa przed AND
_CLOSED = re.compile(rf"^(?: AND (?:TWYS?|TAXIWAYS?|RWYS?|RUNWAYS?|APRON) [A-Z0-9/]+)?(?: (?:{_FILL}))*? "
                     rf"(?:CLOSED|CLSD|NOT IN USE|NOT PERMITTED|PROHIBITED|{_OUT}|(?:ON|UPON)(?: PILOTS?'?S?)? REQ(?:UEST)?"
                     rf"|IF REQ(?:UIRED|UESTED)?|(?:FROM|AFTER) \d{{4}}Z?)\b")
# po EXPECT godzina przed pasem albo tuż po nim to zmiana pasa później: "RUNWAY CHANGE EXPECTED AT 1800 TO RUNWAY 33",
# "EXPECT RUNWAY 09 IN USE FROM 1500", "EXP RWY 33 AT 1800Z"
_EXPECT = re.compile(r"\bEXP(?:ECT(?:ED)?)?\b")
_LATER = re.compile(rf"^(?: (?:{_FILL}|IN USE|ACTIVE))*? {_TIME}")
# niesprawne wyposażenie pasa ("ILS RWY 11 GP U/S", "RWY 07 APP LIGHTS U/S") nie mówi, że pas jest w użyciu
# słowa rozłączne z _FILL (APP w _FILL to rola), bo powtarzalna alternatywa dwóch dopasowań cofa się wykładniczo
_EQUIP = r"ILS|LOC|LLZ|LOCALI[SZ]ER|GP|GS|GLIDE ?PATH|GLIDE ?SLOPE|DME|PAPI|ALS|EDGE|(?:(?:APP|APCH|APPROACH) )?LIGHTS?|LIGHTING"
_EQUIP_OUT = re.compile(rf"^(?: (?:{_FILL}|{_EQUIP}))*? (?:{_OUT})\b")
# zakaz albo warunek przed pasem: "NO INTERSECTION DEPARTURES FROM RUNWAY 15", "IF RUNWAY 33 IS REQUIRED",
# "AIRCRAFT REQUIRING RUNWAY 33 FOR DEPARTURE", "PILOTS REQUESTING ILS APPROACH RUNWAY 11"
_NEGATION = re.compile(r"\b(?:NO|IF|REQUIRING|REQUESTING|NEEDING|UNABLE TO ACCEPT)(?: [A-Z]+)? $")
_IN_USE_AFTER = re.compile(r"^(?: IS)? (?:IN USE|ACTIVE)\b")
_IN_USE_BEFORE = re.compile(r"\b(?:ACTIVE|IN USE:?) $")
# goły numer z jednostką to nie pas: "ARRIVALS 15 MINUTES DELAY"
_UNIT_AFTER = re.compile(r"^ (?:MIN|MINS|MINUTES|NM|KT|KTS|KNOTS|FT|FEET|M|KM|DEG|DEGREES|SEC|SECONDS|PERCENT)\b|^ ?%")
# uwaga o ptakach, uskoku wiatru albo dźwigu przy pasie (bez ACT, bo "ACT RWY 29" to też pas aktywny): między
# wskazówką a pasem ją blokuje ("RNAV DEPARTURES ONLY BIRD ACT RWY 07 THR")
_REMARK = r"BIRDS?|ACTIVITY|WS|WINDSHEAR|CRANES?"
_REMARK_RE = re.compile(rf"\b(?:{_REMARK})\b")
# słowa między wskazówką a pasem, po których pas nie jest pasem w użyciu ("AFTER LANDING VACATE RWY 33 VIA ...",
# "ILS U/S RWY 29 TKOF", "TAKE OFF NOT PERMITTED FROM RUNWAY 15")
_MOVE = r"VACATE|CROSS|CROSSING|BACKTRACK|HOLD|HOLDING|TAXI|VIA|BEHIND|CHANGE"
_BLOCKER = re.compile(rf"\b(?:{_MOVE}|CLOSED|CLSD|{_OUT}|NOT PERMITTED|PROHIBITED|NOT IN USE|{_REMARK})\b")
# pas bez opisu tuż po tych słowach to nie pas w użyciu; CLSD przed pasem kończy poprzednią pozycję ("TWY M CLSD RWY 29")
_PLACE = re.compile(rf"\b(?:{_MOVE}|{_REMARK})\b")


def _roles(words: str) -> set:
    return {"arr" if re.fullmatch(_ARR, w) else "dep" for w in re.split(r" AND | & |/(?!O\b)", words)}


def _last_words(text: str, n: int) -> str:
    return " ".join(text.rsplit(maxsplit=n)[-n:])  # rsplit od końca: bez kropek tekst może być bardzo długi


def _cue(pre_text: str):
    """Słowo przed pasem (do 6 słów). Mocniejsze od EXPECT, chyba że EXPECT po przecinku zaczyna nową pozycję:
    "EXPECT DEPARTURE DELAYS, EXPECT RUNWAY 33"."""
    lo = len(pre_text)
    for _ in range(9):  # tylko koniec tekstu: bez kropek pre_text może być bardzo długi
        lo = max(pre_text.rfind(" ", 0, lo), 0)
    cues = [c for c in _PRE_CUE.finditer(pre_text, lo) if len(pre_text[c.end():].split()) <= 6]
    if not cues:
        return None
    cue = next((c for c in reversed(cues) if not c.group("x")), None)
    if not cue or "," in pre_text[cue.end():cues[-1].start()]:
        cue = cues[-1]
    return None if _BLOCKER.search(pre_text[cue.end():]) else cue


def _post_role(after: str, bare: bool, cued: bool, postfix: bool):
    """Rola po pasie. Samo słowo po pasie (bez FOR, nawiasu, myślnika) może też zaczynać następną pozycję:
    w "ARR RWY 33 DEP RWY 29" DEP należy do 29, w "RWY 33 ARR RWY 29 DEP" ARR należy do 33. `postfix`: tekst już
    opisuje pasy słowami po nich ("RWY 33 LDG RWY 29 TKOF RWY 15 CLSD")."""
    post = _POST_ROLE.match(after)
    link = post.group("link") if post else ""
    if not post or re.search(r"FOR|\(|-", link):
        return post
    if cued or bare and re.search("[,:]", link):
        return None
    rest = after[post.end():]
    if link.strip() and not _NEXT_RUNWAY.match(rest) and not re.match(r"(?: ONLY)?(?: ?[,.;]|$)", rest):
        return None  # po przecinku albo IN USE rola musi kończyć pozycję: "RWY 29 IN USE, ARRIVALS EXPECT ILS RWY 33"
    if _NEXT_RUNWAY.match(rest):
        # następny pas ma własny opis (rolę, zamknięcie, niesprawne wyposażenie), więc rola należy do tego:
        # "RWY 29 ARR/DEP RWY 11 GP U/S"
        nxt = _MENTION.search(rest)
        described = nxt and any(p.match(rest[nxt.end():]) for p in (_POST_ROLE, _CLOSED, _EQUIP_OUT))
        if bare or not postfix and not described:
            return None
    return post


def _atis_runways(text: str, ok) -> dict:
    """Pas do lądowania i do startu z tekstu ATIS (opis klas wyżej)."""
    found = {1: {}, 2: {}}  # klasa -> {"arr"/"dep": pas}; pierwszy w tekście wygrywa
    first = None
    seg_start = 0
    postfix = False
    for m in _MENTION.finditer(text):
        before, after = text[max(0, m.start() - 12):m.start()], text[m.end():]
        rwys = [r for r in [m.group("rwy")] + re.findall(RWY, m.group("more") or "") if ok(r)]
        bare = not m.group("pre")
        sentence = _last_sentence_end(text, m.start())
        pre_text = text[max(seg_start, sentence):m.start()]
        # krótkie zdanie z uwagą ("BIRD ACTIVITY ON FINAL APPROACH RUNWAY 33") nie podaje pasa w użyciu; bez kropki uwaga
        # może należeć do poprzedniej pozycji ("CRANE 45 M AGL ARR RWY 33")
        remark = sentence and sentence >= seg_start and m.start() - sentence <= 80 and _REMARK_RE.search(pre_text)
        cue = None if remark else _cue(pre_text)
        post = _post_role(after, bare, bool(cue and not cue.group("x")), postfix)
        near = cue and pre_text[cue.end():].strip(" :") in ("", "FROM", "ON")  # "DEPARTURES FROM 29"
        if bare and (not rwys or _UNIT_AFTER.match(after) or not post and not near):
            continue  # goły numer (temperatura, widzialność) liczy się tylko tuż przy ARR/DEP
        closed = _CLOSED.match(after)
        seg_start = m.end() + (post.end() if post else closed.end() if closed else 0)  # "RWY 15/33 CLSD RWY 29"
        postfix = postfix or bool(post and _NEXT_RUNWAY.match(after[post.end():]))
        if not rwys or closed or _NEGATION.search(_last_words(pre_text[:cue.start()] if cue else pre_text, 5) + " "):
            continue
        recent = _last_words(pre_text, 6)
        if (x := list(_EXPECT.finditer(recent))) and (re.search(_TIME, recent[x[-1].end():]) or _LATER.match(after)
                                                       or re.match(rf" ?{_TIME}", pre_text)):  # "AT 1800 EXPECT RWY 33"
            continue
        in_use = "IN USE" in (m.group("pre") or "") or _IN_USE_AFTER.match(after) or _IN_USE_BEFORE.search(before)
        if _EQUIP_OUT.match(after) and not (in_use or post or near and re.fullmatch(_ROLE, cue.group())):
            cue = None  # "ILS RWY 11 GP U/S": zostaje tylko pas bez opisu (klasa 3); "ARR RWY 33 ILS U/S" to nadal ARR
        if post:
            roles, cls = _roles(post.group("roles")), 1
            pre_role = cue and not cue.group("x") and ("dep" if cue.group("d") else "arr")
            if len(rwys) > 1 and pre_role and pre_role not in roles:  # "LANDING RWY 33 AND 29 FOR TAKEOFF"
                found[1].setdefault(pre_role, rwys[0])
                rwys = rwys[-1:]
        elif cue:
            tail = pre_text[max(0, cue.start() - 20):cue.end()]
            both = re.search(rf"(?:{_ARR})(?: AND | & |/)(?:{_DEP})$|(?:{_DEP})(?: AND | & |/)(?:{_ARR})$", tail)
            roles, cls = ({"arr", "dep"} if both else {"dep" if cue.group("d") else "arr"}), 1
        elif in_use:
            roles, cls = {"arr", "dep"}, 2
        else:
            if first is None and not found[1] and not found[2] and not remark and not _PLACE.search(
                    _last_words(pre_text, 3)):  # "VACATE RWY 33", "BIRD ACT RWY 15"
                first = rwys[0]
            continue
        for role in roles:
            found[cls].setdefault(role, rwys[0])
    return {role: found[1].get(role) or found[2].get(role) or first for role in ("arr", "dep")}


def _last_sentence_end(text: str, pos: int) -> int:
    i = text.rfind(". ", 0, pos)  # tekst ma już pojedyncze spacje; kropka w 118.305 nie kończy zdania
    return i + 1 if i >= 0 else 0


def parse_atis(lines: list[str] | None, designators: list[str] | None = None, code: str | None = None) -> dict:
    """Litera ATIS i pas(y) w użyciu z tekstu ATIS z sieci VATSIM.

    Działa z typowymi formatami vATIS i tekstem pisanym ręcznie: "RWY 29 IN USE", "RUNWAY IN USE 33",
    "33 FOR LANDING, 29 FOR TAKEOFF", "ARR RWY 33 DEP RWY 29", "EXPECT ILS APPROACH RUNWAY 33. DEPARTURES RUNWAY 29".
    Brakujący pas (lądowanie albo start) kopiuje drugi; `stated` to pasy podane wprost (bez kopii), dla atis_in_use.
    Pas musi istnieć na lotnisku (jeśli podano `designators`)."""
    text = re.sub(r"\s+", " ", " ".join(x for x in lines or [] if x)).upper()
    letter = code or None
    if not letter:
        m = re.search(r"\b(?:INFORMATION|INFO|ATIS) ([A-Z])\b", text)
        letter = m.group(1) if m else None
    ok = lambda r: not designators or r in designators  # noqa: E731
    # cyfry rozstawione dla syntezy mowy ("RUNWAY 2 9") sklejamy tylko po RWY/RUNWAY, nie w temperaturze
    rw = _atis_runways(re.sub(r"\b((?:RWY|RUNWAY)S?(?: IN USE)?:? )(\d) (\d)\b", r"\1\2\3", text), ok)
    arr, dep = rw["arr"], rw["dep"]
    return {"letter": letter, "arr": arr or dep, "dep": dep or arr, "stated": rw, "text": text}


def atis_in_use(parsed: list[tuple[str, dict]]) -> dict:
    """Pas do lądowania i do startu z kilku ATIS lotniska: [(rodzaj, parse_atis(...))], rodzaj "arr" (EPWA_A_ATIS),
    "dep" (EPWA_D_ATIS) albo "both". Rolę bierzemy najpierw z ATIS tej roli (albo wspólnego), potem z innego ATIS,
    który ją podaje wprost; pas skopiowany z drugiej roli nie wygrywa z podanym. Brakującą rolę zostawiamy None."""
    out = {}
    for role in ("arr", "dep"):
        own = [i["stated"][role] for kind, i in parsed if kind in (role, "both") and i["stated"][role]]
        other = [i["stated"][role] for _, i in parsed if i["stated"][role]]
        out[role] = (own + other + [None])[0]
    return out


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
