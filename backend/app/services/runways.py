"""Składowe wiatru i wybór pasa preferowanego.

Najpierw pasy preferowane z AIP AD 2.21 (data/seed/runway_config.json, klucz preferential): okna czasowe w UTC
z czasem letnim UE, limity wiatru w plecy i bocznego z porywami oraz warunki pogody, przy których hałas nie decyduje.
Bez takiego pasa zasada jak w vatiris (minsulander/vatiris, stores/wind.ts): pas z największą składową czołową; przy
słabym wietrze (poniżej progu, domyślnie 5 kt) pas preferowany z konfiguracji lotniska, ale tylko na lotniskach bez
pasów preferowanych w AIP. Dodatkowo bierzemy pod uwagę wyposażenie pasów (ILS z pakietu sektorowego) i widzialność:
przy LVP / przygotowaniu LVP / IMC wybieramy do lądowania pas z ILS, o ile składowa w plecy nie przekracza limitu (do
startu na lotniskach z pasami preferowanymi pas z listy AIP). W LVP pasy preferowane z AD 2.21 nie obowiązują: pasy
dopuszczone w LVP przez AIP AD 2.22 (klucz lvp, np. EPWA: lądowania 33 albo 11, starty 29 albo 15, para w limicie
wiatru), a bez nich lądowania i starty w jednym kierunku, z ILS. Starty nie pod prąd lądowań (ten sam pas, przeciwny
kierunek): w LVP nigdy, poza LVP na lotniskach z kilkoma pasami (EPWA) i gdy hałas nie decyduje."""

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from ..config import DATA_DIR, settings
from .lvp import rvr_value

# najniższy RVR podejścia ILS danej kategorii (ICAO Annex 6: CAT II nie mniej niż 300 m, CAT IIIA 175 m); kategoria pasa
# z AIP AD 2.22 w runway_config.json (lvp.cat)
CAT_MIN_RVR_M = {"II": 300, "IIIA": 175}


def runway_heading(designator: str, heading_true: float | None) -> float:
    """Kurs pasa: rzeczywisty z bazy, chyba że wyraźnie nie pasuje do numeru pasa (błąd danych, np. EPKR 16/34 z kursem 16°)."""
    m = re.match(r"\d+", designator or "")
    by_number = ((int(m.group()) * 10) % 360 or 360) if m else None
    if heading_true is not None and (by_number is None or abs((heading_true - by_number + 180) % 360 - 180) <= 30):
        return heading_true
    if by_number is None:
        raise ValueError(f"Nieznany kurs pasa {designator!r}")
    return by_number


def wind_components(wind_dir: int | None, wind_speed: int | None, rwy_heading: float):
    """Zwraca (wiatr czołowy, boczny) w kt. Czołowy < 0 oznacza wiatr w plecy.
    Boczny > 0 z prawej strony, < 0 z lewej."""
    if wind_speed is None or wind_dir is None:
        return None, None
    angle = math.radians(wind_dir - rwy_heading)
    return round(wind_speed * math.cos(angle), 1), round(wind_speed * math.sin(angle), 1)


@lru_cache(maxsize=1)
def runway_equipment() -> dict[str, dict[str, list[str]]]:
    """Rodzaje podejść na każdy kierunek pasa, wyczytane z nazw procedur (STAR/APP) w plikach .ese.

    Np. "STAR:EPWA:33:ILS-Y:..." -> {"EPWA": {"33": ["ILS"]}}. Wynik uzupełnia data/seed/runway_config.json."""
    kinds = ("ILS", "LOC", "RNP", "RNAV", "VOR", "NDB", "GLS", "PAR")
    out: dict[str, dict[str, set]] = {}
    for f in sorted((DATA_DIR / "import").glob("*.ese")):
        for line in f.read_text("utf-8", errors="replace").splitlines():
            if not line.startswith("STAR:"):
                continue
            parts = line.split(":")
            if len(parts) < 4:
                continue
            ad, rwy, name = parts[1].strip(), parts[2].strip(), parts[3].upper()
            for k in kinds:
                if k in name:
                    out.setdefault(ad, {}).setdefault(rwy, set()).add(k)
    return {ad: {r: sorted(k) for r, k in rw.items()} for ad, rw in out.items()}


def runway_config(icao: str) -> dict:
    """Ustawienia pasów lotniska z data/seed/runway_config.json (preferential, tailwind_kt, crosswind_kt, not_decisive,
    arr, dep, never, limit, equipment, source). Zasada słabego wiatru z DEFAULT (limit) tylko dla lotnisk bez pasów
    preferowanych z AIP."""
    try:
        cfg = json.loads((settings.seed_dir / "runway_config.json").read_text("utf-8"))
    except FileNotFoundError:
        return {}
    own = cfg.get(icao.upper(), {})
    return dict(own) if own.get("preferential") else {**cfg.get("DEFAULT", {}), **own}


# --- czas w AIP: godziny UTC zimą, w nawiasie latem ------------------------------------------------------------------

def _last_sunday(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def eu_summer_time(t: datetime) -> bool:
    """Czas letni w UE: od ostatniej niedzieli marca 01:00 UTC do ostatniej niedzieli października 01:00 UTC
    (liczone samodzielnie, obraz Dockera nie musi mieć tzdata)."""
    t = t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
    start = datetime.combine(_last_sunday(t.year, 3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(t.year, 10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return start <= t < end


HOURS_RE = re.compile(r"^\s*(\d{4})-(\d{4})(?:\s*\((\d{4})-(\d{4})\))?\s*$")


def window_now(hours: str, t: datetime) -> str:
    """Okno z AIP ('2100-0500 (2000-0400)') w bieżącym sezonie: '2000-0400' latem, '2100-0500' zimą; 'H24' bez zmian."""
    if (hours or "").strip().upper() == "H24":
        return "H24"
    m = HOURS_RE.match(hours or "")
    if not m:
        raise ValueError(f"Nieznany format godzin {hours!r}")
    return f"{m[3]}-{m[4]}" if m[3] and eu_summer_time(t) else f"{m[1]}-{m[2]}"


def window_active(hours: str, t: datetime) -> bool:
    """Czy chwila t (UTC) mieści się w oknie z AIP; okno może przechodzić przez północ (2100-0500)."""
    win = window_now(hours, t)
    if win == "H24":
        return True
    t = t.astimezone(timezone.utc) if t.tzinfo else t
    start, end = (int(x[:2]) * 60 + int(x[2:]) for x in win.split("-"))
    now = t.hour * 60 + t.minute
    return start <= now < end if start < end else now >= start or now < end


def preferential_now(cfg: dict, t: datetime | None = None) -> dict:
    """Pasy preferowane z AIP obowiązujące w chwili t: {"arr": [...], "dep": [...], "hours": {"arr": "2000-0400", ...}}
    (listy w kolejności z AIP, puste, gdy w tej chwili brak pierwszeństwa)."""
    t = t or datetime.now(timezone.utc)
    out = {"arr": [], "dep": [], "hours": {}}
    for rule in cfg.get("preferential") or []:
        if not window_active(rule.get("hours", "H24"), t):
            continue
        for phase in ("arr", "dep"):
            if rule.get(phase) and not out[phase]:
                out[phase] = list(rule[phase])
                out["hours"][phase] = window_now(rule.get("hours", "H24"), t)
    return out


def config_now(cfg: dict, t: datetime | None = None) -> dict:
    """Pierwszy pas preferowany teraz: {"arr", "dep"} (AWOS › 2 PASY). Z AIP według okna czasowego, bez niego
    konfiguracja słabego wiatru (arr / dep z runway_config.json)."""
    if cfg.get("preferential"):
        now = preferential_now(cfg, t)
        return {"arr": (now["arr"] or [None])[0], "dep": (now["dep"] or [None])[0]}
    return {"arr": cfg.get("arr"), "dep": cfg.get("dep")}


def equipment_for(icao: str, cfg: dict | None = None) -> dict[str, list[str]]:
    cfg = runway_config(icao) if cfg is None else cfg
    eq = {r: list(k) for r, k in runway_equipment().get(icao.upper(), {}).items()}
    for r, k in (cfg.get("equipment") or {}).items():
        eq[r] = sorted(set(k))
    return eq


def _limit(cfg: dict, key: str, phase: str) -> float | None:
    v = cfg.get(key)
    return v.get(phase) if isinstance(v, dict) else v


def _kt_over(v: float, limit: float) -> str:
    """'7 kt > 5 kt': składowa w całych węzłach jak w tabeli pasów AERODROME (kt w meteo.js: Math.round z wartości
    bezwzględnej, połówki w górę, a nie round() z Pythona: 6.5 → 7, nie 6). Limit porównuje wywołujący na wartości
    przed zaokrągleniem; gdy całe węzły nie przekraczają limitu (5.4 kt > 5 kt), zostaje jedno miejsce po przecinku."""
    v = abs(v)
    whole = math.floor(v + 0.5)
    return f"{whole if whole > limit else f'{v:g}'} kt > {limit:g} kt"


def _over_limits(r: dict, wind_dir: int | None, speed: int | None, tail_max, cross_max, gusty: bool = False) -> str | None:
    """Powód odrzucenia pasa preferowanego: składowa w plecy albo boczna z porywami ponad limit AIP (None = w limicie).
    Wiatr zmienny (VRB) o prędkości ponad limit wiatru w plecy odrzuca pas, bo kierunku nie znamy. gusty: `speed`
    to poryw, więc w powodzie "z porywami" (tabela pasów w AERODROME liczy TW / L / R ze średniego wiatru)."""
    if speed is None:
        return None
    g = " z porywami" if gusty else ""
    if wind_dir is None:
        return f"wiatr zmienny{g} {speed} kt > {tail_max:g} kt" if tail_max is not None and speed > tail_max else None
    head, cross = wind_components(wind_dir, speed, r["heading"])
    if tail_max is not None and -head > tail_max:
        return f"w plecy{g} {_kt_over(head, tail_max)}"
    if cross_max is not None and abs(cross) > cross_max:
        return f"boczny{g} {_kt_over(cross, cross_max)}"
    return None


def _weather_block(nd: dict, phase: str, metar, non_precision: bool) -> str | None:
    """Warunek pogody z AIP, przy którym hałas nie decyduje (not_decisive w runway_config.json), albo None."""
    if not nd or metar is None:
        return None
    ceiling, vis = getattr(metar, "ceiling_ft", None), getattr(metar, "visibility_m", None)
    for block in (nd.get("all") or {}, nd.get(phase) or {}):
        for b, tag in ((block, ""), (block.get("non_precision") if non_precision else None, ", podejście bez ILS")):
            if not b:
                continue
            if ceiling is not None and b.get("ceiling_ft") is not None and ceiling < b["ceiling_ft"]:
                return f"podstawa {ceiling} ft < {b['ceiling_ft']} ft{tag}"
            if vis is not None and b.get("vis_m") is not None and vis < b["vis_m"]:
                return f"widzialność {vis} m < {b['vis_m']} m{tag}"
    if nd.get("ts") and any("TS" in w for w in getattr(metar, "weather", []) or []):
        return "burza (TS)"
    return None


def _same_runway(a: str | None, b: str | None) -> bool:
    """Przeciwne kierunki tego samego pasa (11 i 29, 15 i 33, 09L i 27R)."""
    ma, mb = (re.fullmatch(r"(\d{1,2})([LCR]?)", x or "") for x in (a, b))
    return bool(ma and mb) and (int(ma[1]) - int(mb[1])) % 36 == 18 and {"L": "R", "R": "L"}.get(ma[2], ma[2]) == mb[2]


def _rvr(metar, designator: str) -> tuple[str, int | None]:
    """(etykieta, m): RVR pasa `designator` ("RVR"), gdy METAR go podaje, inaczej najniższy ("RVR 33"); bez grupy RVR
    widzialność ("widzialność", jak rvr_m w lvp.json)."""
    rvr = {r["runway"]: v for r in getattr(metar, "rvr", None) or [] if (v := rvr_value(r["value"])) is not None}
    if designator in rvr:
        return "RVR", rvr[designator]
    if rvr:
        low = min(rvr, key=rvr.get)
        return f"RVR {low}", rvr[low]
    return "widzialność", getattr(metar, "visibility_m", None)


def _first_ok(prefs: list[str], check, avoid: str | None) -> tuple[str | None, list[tuple[str, str]]]:
    """Pierwszy pas z prefs w limitach (check(pas) = powód odrzucenia albo None) i pominięte przed nim [(pas, powód)].
    Przeciwny kierunek pasa lądowań `avoid` (start pod prąd lądowań) tylko wtedy, gdy żaden inny pas nie pasuje."""
    skipped = []
    for d in prefs:
        why = check(d) or (f"przeciwnie do lądowań na {avoid}" if _same_runway(d, avoid) else None)
        if not why:
            return d, skipped
        skipped.append((d, why))
    d = next((d for d in prefs if _same_runway(d, avoid) and not check(d)), None)
    return d, skipped[:prefs.index(d)] if d else skipped


def _aip_choice(cfg: dict, by: dict, wind_dir, wind_speed, gust, metar, t: datetime, base_arr: str | None) -> dict:
    """Pas preferowany z AIP dla lądowań i startów: {"arr": (pas | None, uwaga | None), "dep": (...)}; gdy starty tracą
    pierwszeństwo (pogoda, wiatr zmienny), "alt": (pas, uwaga) z listy startów AIP zamiast kierunku z ILS.

    Starty nie pod prąd lądowań (ten sam pas, przeciwny kierunek), o ile AIP daje inny pas w limicie: na lotnisku
    z kilkoma pasami (EPWA przy 190/10: lądowania 11, starty 15, nie 29) i w "alt"; na lotnisku z jednym pasem AIP
    dopuszcza ruch z obu kierunków (EPPO przy 100/08: lądowania 10, starty 28 z limitem 10 kt). `base_arr` = pas do
    lądowania, gdy AIP nie daje go dla lądowań."""
    now = preferential_now(cfg, t)
    speed = None if wind_speed is None else max(wind_speed, gust or 0)
    gusty = speed is not None and speed > wind_speed
    multi = any(a != b and not _same_runway(a, b) for a in by for b in by)
    out, arr_skipped = {}, []
    for phase in ("arr", "dep"):
        prefs = [d for d in now[phase] if d in by]
        if not prefs:
            out[phase] = (None, None)
            continue
        tail, cross = _limit(cfg, "tailwind_kt", phase), _limit(cfg, "crosswind_kt", phase)

        def check(d, tail=tail, cross=cross):
            return _over_limits(by[d], wind_dir, speed, tail, cross, gusty)

        arr = (out["arr"][0] or base_arr) if phase == "dep" else None
        np = phase == "arr" and not by[prefs[0]]["ils"]
        block = _weather_block(cfg.get("not_decisive") or {}, phase, metar, np)
        d, skipped = (None, []) if block else _first_ok(prefs, check, arr if multi else None)
        if phase == "arr":
            arr_skipped = skipped
        elif not d and sorted(skipped) == sorted(arr_skipped):   # te same pasy i powody co do lądowań: raz, jako ARR/DEP
            skipped = arr_skipped
        if d:
            hrs = now["hours"][phase]
            out[phase] = (d, f"{d} wg AIP ({'H24' if hrs == 'H24' else hrs + ' UTC'})"
                             + (f", bo {_skipped(skipped)}" if skipped else ""))
            continue
        out[phase] = (None, f"bez pierwszeństwa AIP: {block or _skipped(skipped)}")
        if phase == "dep":
            # hałas nie decyduje, ale ILS liczy się tylko przy lądowaniu: do startu pierwszy pas z listy AIP w limicie
            # wiatru (EPWA w mgle 29, nie 33 z ILS; przy wietrze zmiennym kierunku nie znamy, więc bez limitu)
            d, skipped = _first_ok(prefs, (lambda d: None) if wind_dir is None else check, arr)
            if d and not _same_runway(d, arr):
                out["alt"] = (d, f"DEP {d} z listy AIP" + (f", bo {_skipped(skipped)}" if skipped else ""))
    return out


def _skipped(items: list[tuple[str, str]]) -> str:
    """'26: w plecy 8 kt > 5 kt'; ten sam powód dla wszystkich kierunków (wiatr boczny) tylko raz, a z listy dłuższej
    niż dwa pasy (EPWA) dwa pierwsze i '…' (składowe wiatru wszystkich pasów są w tabeli pasów)."""
    whys = {w for _, w in items}
    if len(items) > 1 and len(whys) == 1:
        return whys.pop()
    return "; ".join([f"{d}: {w}" for d, w in items[:2]] + ["…"] * (len(items) > 2))


def select_runways(runways: list[dict], wind_dir: int | None, wind_speed: int | None, *,
                   equipment: dict[str, list[str]] | None = None, low_vis: str | None = None,
                   config: dict | None = None, max_tailwind_kt: float = 5.0, gust: int | None = None,
                   metar=None, now: datetime | None = None) -> dict:
    """Pas preferowany do lądowania (arr) i startu (dep) z uzasadnieniem.

    `runways`: słowniki z kluczami designator, heading (i opcjonalnie preferred). `low_vis`: "LVP", "PREP" albo
    "IMC", gdy warunki wymagają podejścia precyzyjnego (przy "LVP" pasy preferowane z AD 2.21 nie obowiązują, tylko pasy
    z AD 2.22 z config["lvp"]). `gust`, `metar` (podstawa chmur, widzialność, RVR, zjawiska) i `now` (UTC) są potrzebne
    do pasów preferowanych z AIP (config["preferential"]) i do pasów w LVP."""
    if not runways:
        return {"arr": None, "dep": None, "reason": "Brak danych o pasach"}
    cfg = config or {}
    eq = equipment or {}
    aip = bool(cfg.get("preferential"))
    # zasada słabego wiatru (vatiris) tylko bez pasów preferowanych z AIP
    limit = None if aip else cfg.get("limit", 5)
    never = set(cfg.get("never", []))
    rows = []
    for r in runways:
        if r["designator"] in never:
            continue
        head, cross = wind_components(wind_dir, wind_speed, r["heading"])
        rows.append({**r, "headwind": head, "crosswind": cross, "ils": "ILS" in eq.get(r["designator"], [])})
    rows = rows or [{**r, "headwind": None, "crosswind": None, "ils": False} for r in runways]
    by = {r["designator"]: r for r in rows}
    t = now or datetime.now(timezone.utc)
    base = _wind_choice(rows, by, wind_dir, wind_speed, cfg, limit, low_vis, max_tailwind_kt,
                        preferential_now(cfg, t) if aip else None)
    # w LVP pasy preferowane (hałas) z AD 2.21 nie obowiązują: pasy dopuszczone w LVP przez AD 2.22 (klucz lvp), a bez
    # nich kierunek z ILS, wspólny dla lądowań i startów (inaczej np. EPGD przy płytkiej mgle, RVR < 550 m i widzialności
    # 2000 m, miałby lądowania 11 i starty 29)
    if low_vis == "LVP":
        return _lvp_choice(cfg, by, base, wind_dir, wind_speed, gust, metar, max_tailwind_kt)
    if not aip:
        return base
    choice = _aip_choice(cfg, by, wind_dir, wind_speed, gust, metar, t, base["arr"])
    (arr, arr_why), (dep, dep_why) = choice["arr"], choice["dep"]
    both = arr_why and arr_why == dep_why
    if both:
        notes = [f"ARR/DEP {arr_why}"]
    else:
        notes = [f"{k} {w}" for k, w in (("ARR", arr_why), ("DEP", dep_why)) if w]
    if not notes:   # poza oknami czasowymi AIP
        return base
    if arr and dep:
        return {"arr": arr, "dep": dep, "reason": "; ".join(notes)}
    why = _lc(base["reason"])
    alt = choice.get("alt")
    if alt and alt[0] != base["dep"]:
        # starty bez pierwszeństwa AIP: pas z listy startów AIP, nie kierunek z ILS wybrany do lądowań
        rest = ([] if arr else [why if both else f"ARR: {why}"]) + [alt[1]]
        return {"arr": arr or base["arr"], "dep": alt[0], "reason": "; ".join([*notes, *rest])}
    rest = [why] if not arr and not dep else [f"{'DEP' if arr else 'ARR'}: {why}"]
    return {"arr": arr or base["arr"], "dep": dep or base["dep"], "reason": "; ".join([*notes, *rest])}


def _lc(why: str) -> str:
    """Powód w dalszej części zdania: "największa składowa czołowa", ale skróty ("LVP w mocy", "ARR 33") bez zmian."""
    return why if why[1:2].isupper() else why[:1].lower() + why[1:]


def _lvp_choice(cfg: dict, by: dict, base: dict, wind_dir, wind_speed, gust, metar, max_tailwind_kt: float) -> dict:
    """LVP z pasami dopuszczonymi przez AIP AD 2.22 (cfg["lvp"] = {"arr": [...], "dep": [...]}, w kolejności AIP).
    Lądowania tylko na pasach, których kategoria ILS (lvp.cat) dopuszcza RVR z METAR (EPWA RWY 11 CAT II od 300 m),
    starty nigdy pod prąd lądowań (ten sam pas, przeciwny kierunek: EPPO lądowania 28, starty też 28). Pierwszy pas do
    lądowania, przy którym i lądowania, i starty mają wiatr w plecy z porywami w limicie lotniska (tailwind_kt, bez
    niego max_tailwind_kt): EPWA przy 070/10 lądowania 11 i starty 15, nie 33 i 29 z wiatrem w plecy. Bez takiej pary
    osobno pierwszy pas w limicie, a gdy żaden, ten z największą składową czołową (gdy wszystkie pasy do lądowania są
    poniżej minimów, ten z najniższymi: EPWA 33). Przy RVR pasa startowego poniżej lvp.dep_min_rvr_m ostrzeżenie, że
    starty są niedozwolone. Faza bez listy: wybór `base`, starty też nie pod prąd lądowań."""
    lists = cfg.get("lvp") or {}
    cat = lists.get("cat") or {}
    speed = None if wind_speed is None else max(wind_speed, gust or 0)
    g = " z porywami" if speed is not None and speed > wind_speed else ""
    arrs, deps = ([d for d in lists.get(p) or [] if d in by] for p in ("arr", "dep"))

    def tail(d, phase):   # wiatr w plecy z porywami ponad limit (przy wietrze zmiennym i bez danych nie sprawdzamy)
        lim = _limit(cfg, "tailwind_kt", phase)
        lim = max_tailwind_kt if lim is None else lim
        tw = None if wind_dir is None or speed is None else -wind_components(wind_dir, speed, by[d]["heading"])[0]
        return f"w plecy{g} {_kt_over(tw, lim)}" if tw is not None and tw > lim else None

    def below(d):   # lądowanie poniżej minimów kategorii ILS pasa
        need = CAT_MIN_RVR_M.get(cat.get(d))
        what, v = _rvr(metar, d)
        return f"CAT {cat[d]}: {what} {v} m < {need} m" if need and v is not None and v < need else None

    def dep_for(arr):   # do startu bez przeciwnego kierunku pasa lądowań, chyba że AIP daje tylko ten
        return [d for d in deps if not _same_runway(d, arr)] or deps

    def dep_tail(arr):   # przy lądowaniach na arr żaden pas do startu nie ma wiatru w limicie
        ds = dep_for(arr)
        return f"DEP {ds[0]} {tail(ds[0], 'dep')}" if ds and all(tail(d, "dep") for d in ds) else None

    def note(label, d, skipped, phase):   # "ARR 33 mimo wiatru w plecy 8 kt > 5 kt, bo 11: CAT II: RVR 250 m < 300 m"
        over, low = tail(d, phase), phase == "arr" and below(d)
        extra = ", ".join(filter(None, [over and f"mimo wiatru {over}", low and f"poniżej minimów {low}"]))
        return " ".join(filter(None, [label, d, extra])) + (f", bo {_skipped(skipped)}" if skipped else "")

    notes, missing, arr, dep = [], [], base["arr"], base["dep"]
    if arrs:
        arr, skipped = _first_ok(arrs, lambda d: below(d) or tail(d, "arr") or dep_tail(d), None)
        if not arr:   # żadna para w limitach: pas do lądowania bez względu na starty
            arr, skipped = _first_ok(arrs, lambda d: below(d) or tail(d, "arr"), None)
        if not arr:
            pool = [d for d in arrs if not below(d)] or [min(arrs, key=lambda d: CAT_MIN_RVR_M.get(cat.get(d), 0))]
            arr = max(pool, key=lambda d: by[d]["headwind"] or 0)
            skipped = [] if below(arr) else [(d, below(d)) for d in arrs if below(d)]
        notes.append(note("ARR", arr, skipped, "arr"))
    else:
        missing.append("ARR")
    if deps:
        ds = dep_for(arr)
        dep, skipped = _first_ok(ds, lambda d: tail(d, "dep"), None)
        if not dep:
            dep, skipped = max(ds, key=lambda d: by[d]["headwind"] or 0), []
        notes.append(note("DEP", dep, skipped, "dep"))
    elif _same_runway(dep, arr):
        dep = arr
        notes.append(f"DEP {arr}")
    else:
        missing.append("DEP")
    if not notes:
        return base
    plain = all(n.count(" ") == 1 for n in notes)   # same pasy: "ARR 33, DEP 29"
    reason = f"LVP w mocy (AIP AD 2.22): {(', ' if plain else '; ').join(notes)}"
    if missing:   # faza bez listy z AIP: wybór jak bez niej (kierunek z ILS)
        reason += f"; {missing[0]}: {_lc(base['reason'].removeprefix('LVP w mocy: '))}"
    if wind_dir is None and speed != 0:   # wiatru w plecy nie sprawdzaliśmy
        reason += f"; wiatr zmienny{g} {speed} kt" if speed is not None else "; brak danych o wietrze"
    what, v = _rvr(metar, dep)
    if lists.get("dep_min_rvr_m") and v is not None and v < lists["dep_min_rvr_m"]:
        reason += f"; starty niedozwolone: {what} {v} m < {lists['dep_min_rvr_m']} m"
    return {"arr": arr, "dep": dep, "reason": reason}


def _wind_choice(rows: list[dict], by: dict, wind_dir, wind_speed, cfg: dict, limit, low_vis, max_tailwind_kt,
                 order: dict | None = None) -> dict:
    """Wybór bez pasów preferowanych z AIP: ILS przy słabej widzialności, słaby wiatr (limit, vatiris), składowa czołowa.
    `order`: pasy preferowane z AIP teraz (preferential_now); przy równej składowej czołowej (cisza, wiatr zmienny) pas
    wcześniejszy w kolejności lądowań z AIP (EPWA 33, nie 11 z bazy)."""
    tail_ok = [r for r in rows if r["headwind"] is None or r["headwind"] >= -max_tailwind_kt]
    known = wind_dir is not None and wind_speed is not None
    calm = limit is not None and (not known or wind_speed < limit)
    head = lambda r: r["headwind"] if r["headwind"] is not None else 0  # noqa: E731
    pick = lambda arr, dep, why: {"arr": arr, "dep": dep or arr, "reason": why}  # noqa: E731
    arr_order = (order or {}).get("arr") or []
    rank = lambda r: arr_order.index(r["designator"]) if r["designator"] in arr_order else len(arr_order)  # noqa: E731

    if low_vis:
        ils = [r for r in tail_ok if r["ils"]]
        if ils:
            best = max(ils, key=lambda r: (head(r), -rank(r)))["designator"]
            label = {"LVP": "LVP w mocy", "PREP": "Przygotowanie LVP", "IMC": "Warunki IMC"}.get(low_vis, low_vis)
            return pick(best, best, f"{label}: kierunek z ILS (wiatr w plecy ≤ {max_tailwind_kt:g} kt)")
    if calm:
        pref_arr = cfg.get("arr") or next((r["designator"] for r in rows if r.get("preferred")), None)
        pref_dep = cfg.get("dep") or pref_arr
        if pref_arr in by and by[pref_arr] in tail_ok:
            dep = pref_dep if pref_dep in by and by[pref_dep] in tail_ok else pref_arr
            return pick(pref_arr, dep, f"Wiatr < {limit} kt: konfiguracja preferowana lotniska")
        ils = [r for r in tail_ok if r["ils"]]
        if ils:
            best = max(ils, key=head)["designator"]
            return pick(best, best, f"Wiatr < {limit} kt: kierunek wyposażony w ILS")
    if wind_dir is None and wind_speed is not None:
        # wiatr zmienny (VRB): prędkość znana, kierunek nie; jak przy słabym wietrze kierunek z ILS (także na lotniskach
        # z pasami preferowanymi z AIP, gdzie nie ma limitu słabego wiatru, i przy VRB od limitu w górę)
        ils = [r for r in tail_ok if r["ils"]]
        if ils:
            return pick(min(ils, key=rank)["designator"], None, f"Wiatr zmienny {wind_speed} kt: kierunek wyposażony w ILS")
        return pick(rows[0]["designator"], None, f"Wiatr zmienny {wind_speed} kt: pierwszy pas z listy")
    if not known:
        return pick(rows[0]["designator"], None, "Brak danych o wietrze: pierwszy pas z listy")
    best = max(rows, key=lambda r: (head(r), -abs(r["crosswind"] or 0), r["ils"], -rank(r)))["designator"]
    return pick(best, best, "Największa składowa czołowa")


def suggest_runway(runways: list[dict], wind_dir: int | None, wind_speed: int | None,
                   calm_threshold_kt: int = 5, max_tailwind_kt: float = 5.0):
    """Zgodność wstecz: (wiersz pasa do lądowania, uzasadnienie)."""
    res = select_runways(runways, wind_dir, wind_speed, config={"limit": calm_threshold_kt},
                         max_tailwind_kt=max_tailwind_kt)
    best = next((r for r in runways if r["designator"] == res["arr"]), None)
    return best, res["reason"]
