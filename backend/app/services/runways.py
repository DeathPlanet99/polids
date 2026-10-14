"""Składowe wiatru i wybór pasa preferowanego.

Najpierw pasy preferowane z AIP AD 2.21 (data/seed/runway_config.json, klucz preferential): okna czasowe w UTC
z czasem letnim UE, limity wiatru w plecy i bocznego z porywami oraz warunki pogody, przy których hałas nie decyduje.
Bez takiego pasa zasada jak w vatiris (minsulander/vatiris, stores/wind.ts): pas z największą składową czołową; przy
słabym wietrze (poniżej progu, domyślnie 5 kt) pas preferowany z konfiguracji lotniska, ale tylko na lotniskach bez
pasów preferowanych w AIP. Dodatkowo bierzemy pod uwagę wyposażenie pasów (ILS z pakietu sektorowego) i widzialność:
przy LVP / przygotowaniu LVP / IMC wybieramy pas z ILS, o ile składowa w plecy nie przekracza limitu. W LVP pasy
preferowane z AIP nie obowiązują (lądowania i starty w jednym kierunku, z ILS), chyba że AIP AD 2.22 wymienia pasy
dopuszczone w LVP (klucz lvp, np. EPWA: lądowania 33 albo 11, starty 29 albo 15)."""

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from ..config import DATA_DIR, settings


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


def _aip_choice(cfg: dict, by: dict, wind_dir, wind_speed, gust, metar, t: datetime) -> dict:
    """Pas preferowany z AIP dla lądowań i startów: {"arr": (pas | None, uwaga | None), "dep": (...)}."""
    now = preferential_now(cfg, t)
    speed = None if wind_speed is None else max(wind_speed, gust or 0)
    gusty = speed is not None and speed > wind_speed
    out = {}
    for phase in ("arr", "dep"):
        prefs = [d for d in now[phase] if d in by]
        if not prefs:
            out[phase] = (None, None)
            continue
        np = phase == "arr" and not by[prefs[0]]["ils"]
        block = _weather_block(cfg.get("not_decisive") or {}, phase, metar, np)
        if block:
            out[phase] = (None, f"bez pierwszeństwa AIP: {block}")
            continue
        skipped = []
        tail, cross = _limit(cfg, "tailwind_kt", phase), _limit(cfg, "crosswind_kt", phase)
        for d in prefs:
            why = _over_limits(by[d], wind_dir, speed, tail, cross, gusty)
            if not why:
                hrs = now["hours"][phase]
                out[phase] = (d, f"{d} wg AIP ({'H24' if hrs == 'H24' else hrs + ' UTC'})"
                                 + (f", bo {_skipped(skipped)}" if skipped else ""))
                break
            skipped.append((d, why))
        else:
            out[phase] = (None, f"bez pierwszeństwa AIP: {_skipped(skipped)}")
    return out


def _skipped(items: list[tuple[str, str]]) -> str:
    """'26: w plecy 8 kt > 5 kt'; ten sam powód dla wszystkich kierunków (wiatr boczny) tylko raz."""
    whys = {w for _, w in items}
    return whys.pop() if len(items) > 1 and len(whys) == 1 else "; ".join(f"{d}: {w}" for d, w in items)


def select_runways(runways: list[dict], wind_dir: int | None, wind_speed: int | None, *,
                   equipment: dict[str, list[str]] | None = None, low_vis: str | None = None,
                   config: dict | None = None, max_tailwind_kt: float = 5.0, gust: int | None = None,
                   metar=None, now: datetime | None = None) -> dict:
    """Pas preferowany do lądowania (arr) i startu (dep) z uzasadnieniem.

    `runways`: słowniki z kluczami designator, heading (i opcjonalnie preferred). `low_vis`: "LVP", "PREP" albo
    "IMC", gdy warunki wymagają podejścia precyzyjnego (przy "LVP" bez pasów preferowanych z AIP). `gust`, `metar`
    (podstawa chmur, widzialność, zjawiska) i `now` (UTC) są potrzebne do pasów preferowanych z AIP
    (config["preferential"])."""
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
    base = _wind_choice(rows, by, wind_dir, wind_speed, cfg, limit, low_vis, max_tailwind_kt)
    # w LVP decyduje kierunek z ILS, wspólny dla lądowań i startów: pasy preferowane (hałas) z AIP nie obowiązują,
    # inaczej np. EPGD przy płytkiej mgle (RVR < 550 m, widzialność 2000 m) miałby lądowania 11 i starty 29
    if low_vis == "LVP":
        return _lvp_choice(cfg.get("lvp") or {}, by, base, max_tailwind_kt)
    if not aip:
        return base
    choice = _aip_choice(cfg, by, wind_dir, wind_speed, gust, metar, now or datetime.now(timezone.utc))
    (arr, arr_why), (dep, dep_why) = choice["arr"], choice["dep"]
    if arr_why and arr_why == dep_why:
        notes = [f"ARR/DEP {arr_why}"]
    else:
        notes = [f"{k} {w}" for k, w in (("ARR", arr_why), ("DEP", dep_why)) if w]
    if not notes:   # poza oknami czasowymi AIP
        return base
    if arr and dep:
        return {"arr": arr, "dep": dep, "reason": "; ".join(notes)}
    # dalej w tym samym zdaniu: "największa składowa czołowa", ale skróty ("LVP w mocy") bez zmian
    why = base["reason"] if base["reason"][1:2].isupper() else base["reason"][:1].lower() + base["reason"][1:]
    rest = [why] if not arr and not dep else [f"{'DEP' if arr else 'ARR'}: {why}"]
    return {"arr": arr or base["arr"], "dep": dep or base["dep"], "reason": "; ".join([*notes, *rest])}


def _lvp_choice(lists: dict, by: dict, base: dict, max_tailwind_kt: float) -> dict:
    """LVP z pasami dopuszczonymi przez AIP AD 2.22 (lists = {"arr": [...], "dep": [...]}, w kolejności AIP): pierwszy
    pas z wiatrem w plecy w limicie, a gdy żaden, ten z największą składową czołową. Faza bez listy: wybór `base`."""
    out, notes, skips = {}, [], False
    for phase, label in (("arr", "ARR"), ("dep", "DEP")):
        cands = [by[d] for d in lists.get(phase) or [] if d in by]
        if not cands:
            out[phase] = base[phase]
            continue
        skipped = []
        for r in cands:
            if r["headwind"] is None or -r["headwind"] <= max_tailwind_kt:
                pick, why = r, f"{label} {r['designator']}" + (f", bo {_skipped(skipped)}" if skipped else "")
                break
            skipped.append((r["designator"], f"w plecy {_kt_over(r['headwind'], max_tailwind_kt)}"))
        else:
            pick = max(cands, key=lambda r: r["headwind"])
            why = f"{label} {pick['designator']} mimo wiatru w plecy {_kt_over(pick['headwind'], max_tailwind_kt)}"
        out[phase] = pick["designator"]
        notes.append(why)
        skips = skips or bool(skipped)
    if not notes:
        return base
    reason = f"LVP w mocy (AIP AD 2.22): {('; ' if skips else ', ').join(notes)}"
    if len(notes) == 1:   # druga faza bez listy z AIP: kierunek z ILS jak dotąd
        reason += f"; {'DEP' if 'arr' in lists else 'ARR'}: {base['reason']}"
    return {"arr": out["arr"], "dep": out["dep"], "reason": reason}


def _wind_choice(rows: list[dict], by: dict, wind_dir, wind_speed, cfg: dict, limit, low_vis, max_tailwind_kt) -> dict:
    """Wybór bez pasów preferowanych z AIP: ILS przy słabej widzialności, słaby wiatr (limit, vatiris), składowa czołowa."""
    tail_ok = [r for r in rows if r["headwind"] is None or r["headwind"] >= -max_tailwind_kt]
    known = wind_dir is not None and wind_speed is not None
    calm = limit is not None and (not known or wind_speed < limit)
    head = lambda r: r["headwind"] if r["headwind"] is not None else 0  # noqa: E731
    pick = lambda arr, dep, why: {"arr": arr, "dep": dep or arr, "reason": why}  # noqa: E731

    if low_vis:
        ils = [r for r in tail_ok if r["ils"]]
        if ils:
            best = max(ils, key=head)["designator"]
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
            return pick(ils[0]["designator"], None, f"Wiatr zmienny {wind_speed} kt: kierunek wyposażony w ILS")
        return pick(rows[0]["designator"], None, f"Wiatr zmienny {wind_speed} kt: pierwszy pas z listy")
    if not known:
        return pick(rows[0]["designator"], None, "Brak danych o wietrze: pierwszy pas z listy")
    best = max(rows, key=lambda r: (head(r), -abs(r["crosswind"] or 0), r["ils"]))["designator"]
    return pick(best, best, "Największa składowa czołowa")


def suggest_runway(runways: list[dict], wind_dir: int | None, wind_speed: int | None,
                   calm_threshold_kt: int = 5, max_tailwind_kt: float = 5.0):
    """Zgodność wstecz: (wiersz pasa do lądowania, uzasadnienie)."""
    res = select_runways(runways, wind_dir, wind_speed, config={"limit": calm_threshold_kt},
                         max_tailwind_kt=max_tailwind_kt)
    best = next((r for r in runways if r["designator"] == res["arr"]), None)
    return best, res["reason"]
