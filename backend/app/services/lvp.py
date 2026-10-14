"""Stan LVP (Low Visibility Procedures) liczony z METAR według progów z AIP (data/seed/lvp.json).

Fazy jak w AIP AD 2.22 / AIP MIL AD 4.20: przygotowanie (PREP), wprowadzenie (LVP) i odwołanie. LVP ma histerezę:
po wprowadzeniu trwa, dopóki nie są spełnione warunki odwołania, więc wynik zależy od poprzedniego stanu. Ostatni stan
każdego lotniska trzymamy w pamięci, kluczem jest czas obserwacji METAR (ten sam METAR liczony ponownie daje ten sam
wynik, starszy METAR z innego zapytania dostaje stan z nowszego). Po odwołaniu stan CANCELLED ("LVP ODWOŁANE") przez
30 min od godziny odwołania, chyba że wrócą warunki wprowadzenia. Po starcie aplikacji albo po przerwie w historii
dłuższej niż 90 min (nikt nie oglądał lotniska) wartości między progiem wprowadzenia i odwołania LVP nie włączają."""

import json
import operator
import re
from datetime import datetime, timedelta, timezone

from ..config import settings
from .metar import Metar

CANCEL_HOLD = timedelta(minutes=30)
MAX_GAP = timedelta(minutes=90)   # dłuższa przerwa między METAR w pamięci: liczymy od nowa, jak po starcie aplikacji
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
SYM = {"<": "<", "<=": "≤", ">": ">", ">=": "≥"}
LABEL = {"rvr_m": "RVR", "vis_m": "widzialność", "ceiling_ft": "podstawa"}
UNIT = {"rvr_m": "m", "vis_m": "m", "ceiling_ft": "ft"}
PHASES = (("prep", "Przygotowanie"), ("lvp", "LVP"), ("cancel", "Odwołanie"))

# icao -> {"obs": czas METAR, "prev": (stan, od kiedy) przed tym METAR, "cur": (stan, od kiedy) po nim, "reasons": [...]}
_memory: dict[str, dict] = {}


def reset_memory():
    """Zapomina historię (testy)."""
    _memory.clear()


def thresholds(icao: str) -> dict:
    """Wpis lotniska z lvp.json, bez wpisu DEFAULT (AIP nie przewiduje LVP)."""
    cfg = json.loads((settings.seed_dir / "lvp.json").read_text("utf-8"))
    return cfg.get(icao.upper(), cfg["DEFAULT"])


def rvr_value(v: str) -> int | None:
    """'P2000' -> 2000, 'M0050' -> 50, '0550V0800' -> 550 (bierzemy wartość najmniejszą)."""
    nums = [int(x) for x in re.findall(r"\d{4}", v or "")]
    return min(nums) if nums else None


def _cond_text(key: str, spec, eq: int | None = None) -> str:
    op, val = spec
    extra = f" (RVR {eq} m)" if eq else ""
    return f"{LABEL[key]} {SYM[op]} {val} {UNIT[key]}{extra}"


def phase_text(phase: dict | None) -> str:
    """'RVR < 550 m lub podstawa < 200 ft' (do dymków w interfejsie)."""
    if not phase:
        return "AIP nie podaje"
    parts = [_cond_text(k, phase[k], phase.get("rvr_eq_m") if k == "vis_m" else None) for k in LABEL if k in phase]
    return (" i " if phase.get("logic") == "and" else " lub ").join(parts)


def rules(cfg: dict) -> list[str]:
    """Progi trzech faz do dymka: ['Przygotowanie: …', 'LVP: …', 'Odwołanie: …']."""
    return [f"{name}: {phase_text(cfg.get(key))}" for key, name in PHASES]


def _values(key: str, cond: dict, m: Metar) -> list[tuple[str, int | None, int]]:
    """Wartości z METAR do porównania z progiem: [(etykieta, wartość, próg)]; pusta lista = brak danych.
    Podstawa None = brak chmur BKN/OVC/VV (nieograniczona)."""
    op, limit = cond[key]
    rvrs = [(f"RVR {r['runway']}", v) for r in m.rvr if (v := rvr_value(r["value"])) is not None]
    if key == "rvr_m":
        if rvrs:
            return [(lbl, v, limit) for lbl, v in rvrs]
        return [("widzialność", m.visibility_m, limit)] if m.visibility_m is not None else []
    if key == "vis_m":
        if m.visibility_m is not None:
            return [("widzialność", m.visibility_m, limit)]
        eq = cond.get("rvr_eq_m")
        return [(lbl, v, eq) for lbl, v in rvrs] if eq else []
    return [("podstawa", m.ceiling_ft, limit)]


def _check(phase: dict, m: Metar) -> dict:
    """Ocena fazy: {"met": bool, "hits": [spełnione warunki], "missing": [niespełnione, z wartością z METAR]}.
    Warunek pogorszenia (<, <=) wystarczy dla jednego RVR, poprawy (>, >=) musi być spełniony dla wszystkich."""
    results, hits, missing = [], [], []
    for key in LABEL:
        if key not in phase:
            continue
        op, _ = phase[key]
        fn, worse, unit = OPS[op], op in ("<", "<="), UNIT[key]
        vals = _values(key, phase, m)
        if not vals:
            results.append(False)
            missing.append(f"{_cond_text(key, phase[key])} (brak w METAR)")
            continue
        ok = [v is None and not worse or v is not None and fn(v, lim) for _, v, lim in vals]
        met = any(ok) if worse else all(ok)
        results.append(met)
        for (lbl, v, lim), good in zip(vals, ok):
            if worse and good:
                hits.append(f"{lbl} {v} {unit} {SYM[op]} {lim} {unit}")
            elif not worse and not good:
                missing.append(f"{lbl} {SYM[op]} {lim} {unit} (jest {v} {unit})")
        if not worse and met:   # wartości faktycznie porównane (np. RVR zamiast widzialności, której brak w METAR)
            hits += [f"{lbl} {v} {unit} {SYM[op]} {lim} {unit}" if v is not None else f"{lbl} {SYM[op]} {lim} {unit}"
                     for lbl, v, lim in vals]
    met = all(results) if phase.get("logic") == "and" else any(results)
    return {"met": bool(results) and met, "hits": hits, "missing": missing}


def obs_time(code: str | None, now: datetime | None = None) -> datetime | None:
    """Czas obserwacji METAR 'ddhhmmZ' jako datetime UTC (miesiąc z bieżącej daty, dzień z przyszłości = poprzedni
    miesiąc)."""
    if not code or not re.fullmatch(r"\d{6}Z", code):
        return None
    now = now or datetime.now(timezone.utc)
    d, hh, mm = int(code[:2]), int(code[2:4]), int(code[4:6])
    y, mo = now.year, now.month
    for _ in range(3):
        try:
            t = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
        except ValueError:
            t = None
        if t is not None and t <= now + timedelta(days=1):
            return t
        y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
    return None


def transition(cfg: dict, m: Metar, prev: tuple[str | None, datetime | None], obs: datetime | None):
    """Nowy stan po METAR m przy stanie poprzednim prev = (stan, od kiedy). Zwraca (stan, od kiedy, powody)."""
    state, since = prev
    act = _check(cfg["lvp"], m)
    if act["met"]:
        keep = state == "LVP" and since is not None
        return "LVP", since if keep else obs, act["hits"]
    if state == "LVP":
        cancel = _check(cfg["cancel"], m) if cfg.get("cancel") else {"met": True, "hits": [], "missing": []}
        if not cancel["met"]:
            return "LVP", since, [f"do odwołania: {x}" for x in cancel["missing"]]
        return "CANCELLED", obs, [f"warunki odwołania: {' i '.join(cancel['hits'])}" if cancel["hits"]
                                  else "warunki wprowadzenia ustały"]
    prep = _check(cfg["prep"], m) if cfg.get("prep") else None
    if state == "CANCELLED" and since is not None and obs is not None and obs - since < CANCEL_HOLD:
        extra = [f"przygotowanie: {x}" for x in prep["hits"]] if prep and prep["met"] else []
        return "CANCELLED", since, ["warunki odwołania spełnione", *extra]
    if prep and prep["met"]:
        return "PREP", since if state == "PREP" and since is not None else obs, prep["hits"]
    return None, None, []


def _hhmm(t: datetime | None) -> str | None:
    return t.strftime("%H:%MZ") if t else None


def evaluate(m: Metar | None, icao: str, now: datetime | None = None) -> dict:
    """Stan LVP lotniska: {"state": "LVP" | "PREP" | "CANCELLED" | None, "applies", "reasons", "since", "until",
    "scope", "source", "verified", "note", "rules", "thresholds"}. applies False (AIP nie przewiduje LVP): state None
    i reason. since: od kiedy LVP albo kiedy odwołane, until: koniec 30 min stanu CANCELLED."""
    code = icao.upper()
    cfg = thresholds(code)
    base = {"state": None, "applies": cfg.get("applies", True), "reasons": [], "since": None, "until": None,
            "scope": cfg.get("scope"), "source": cfg.get("source"), "verified": cfg.get("verified", True),
            "note": cfg.get("note"), "reason": cfg.get("reason")}
    if not base["applies"]:
        return {**base, "rules": [], "thresholds": None}
    base["rules"] = rules(cfg)
    base["thresholds"] = {k: cfg.get(k) for k, _ in PHASES}
    if m is None:
        return base
    obs = obs_time(m.time, now)
    mem = _memory.get(code)
    if mem is not None and obs is not None and obs < mem["obs"]:
        # starszy METAR (OVERVIEW i AERODROME pytają osobno, każde zapytanie ma własną pamięć podręczną): stan
        # z nowszego METAR bez przejścia, inaczej LVP z nowszego dałoby tu fałszywe "LVP ODWOŁANE"
        (state, since), reasons, ref = mem["cur"], mem["reasons"], mem["obs"]
    else:
        if mem is None or obs is None or obs - mem["obs"] > MAX_GAP:
            prev = (None, None)
        elif obs > mem["obs"]:
            prev = mem["cur"]
        else:   # ten sam METAR liczony ponownie
            prev = mem["prev"]
        state, since, reasons = transition(cfg, m, prev, obs)
        if obs is not None:
            _memory[code] = {"obs": obs, "prev": prev, "cur": (state, since), "reasons": reasons}
        ref = obs
    # 30 min odwołania liczone od teraz, nie tylko od następnego METAR (stary METAR, przerwa w danych)
    if state == "CANCELLED" and since is not None and (now or datetime.now(timezone.utc)) >= since + CANCEL_HOLD:
        state, since, reasons = transition(cfg, m, (None, None), obs)
    if state == "LVP" and since is not None and ref is not None and since < ref:
        reasons = [f"w mocy od {_hhmm(since)}", *reasons]
    return {**base, "state": state, "reasons": reasons, "since": _hhmm(since) if state else None,
            "until": _hhmm(since + CANCEL_HOLD) if state == "CANCELLED" and since else None}
