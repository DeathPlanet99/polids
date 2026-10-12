"""AERODROME › RUCH jako DMAN/AMAN: dane do drabin czasu (TSAT i TTOT, na lotnisku bez A-CDM EOBT i ETOT, ELDT)
i do sekwencji przylotów AMAN.

Uzupełnia paski z procedures.strip_board o pola potrzebne DMAN/AMAN (dotychczasowe pola zostają bez zmian):
- registration: znaki rejestracyjne z uwag planu lotu (REG/SPLVA), u wszystkich lotów;
- entry_fix: punkt wejścia przylotu (pierwszy punkt STAR, nazwa STAR z trasy albo ostatnie punkty trasy);
- eta_s: ETA z pozycji w sekundach od teraz (model niżej);
- eldt_s, eldt, eldt_source: ELDT z vIFF, jeśli jest wiarygodny, inaczej ETA z pozycji ("vIFF" albo "POS").

Model ETA z pozycji (przybliżenie, opisane też w dymkach interfejsu): odległość po ortodromie do punktu odniesienia
lotniska i prędkość względem ziemi z sieci VATSIM; odcinek 30–10 NM najwyżej 220 kt (zniżanie i wytracanie
prędkości), ostatnie 10 NM z prędkością podejścia 140 kt (wolniejszy samolot leci swoją prędkością)."""

import re
from datetime import datetime, timedelta

from .procedures import FIX, PROC_NAME, procedures, route_points
from .viff import hhmm

APPROACH_KT = 140   # prędkość na ostatnich FINAL_NM
FINAL_NM = 10
TMA_KT = 220        # najwyżej tyle między TMA_NM a FINAL_NM
TMA_NM = 30
# ELDT z vIFF bierzemy, gdy nie jest w przeszłości i (dla samolotu w powietrzu) różni się od ETA z pozycji
# najwyżej o tyle minut; inaczej to raczej czas z planu lotu sprzed startu
VIFF_MAX_DIFF_MIN = 20
REG = re.compile(r"(?:^|\s)REG/([A-Z0-9-]{2,10})(?=\s|/|$)")


def registration(remarks: str | None) -> str | None:
    """Znaki rejestracyjne z uwag planu lotu: 'PBN/A1B1 DOF/261006 REG/SPLVA /V/' → 'SPLVA'."""
    m = REG.search(str(remarks or "").upper())
    return m.group(1) if m else None


def iafs(icao: str, procs: dict | None = None) -> list[str]:
    """Punkty wejścia lotniska = pierwsze punkty STAR z pliku .ese (wszystkie pasy), alfabetycznie.
    EPWA: AGAVA BIMPA LIMVI LOGDA NEPOX SORIX."""
    stars = (procs if procs is not None else procedures()).get(icao.upper(), {}).get("star", {})
    return sorted({p["fixes"][0] for lst in stars.values() for p in lst if p.get("fixes")})


def entry_fix(icao: str, route: str | None, proc: dict | None = None, procs: dict | None = None) -> dict | None:
    """Punkt wejścia przylotu (strumień w AMAN): {"fix", "source"}.

    1. dopasowany STAR (pierwszy punkt procedury), 2. nazwa STAR albo punkt wejścia lotniska w ostatnich trzech
    elementach trasy, 3. ostatni punkt nawigacyjny trasy (source "route"); bez trasy None."""
    if proc and (proc.get("fixes") or proc.get("fix")):
        return {"fix": (proc.get("fixes") or [proc["fix"]])[0], "source": "STAR"}
    starts = set(iafs(icao, procs))
    pts = route_points(route, (icao.upper(),))
    for tok in reversed(pts[-3:]):
        m = PROC_NAME.match(tok)
        if m and (not starts or m.group(1) in starts):
            return {"fix": m.group(1), "source": "STAR"}
        if tok in starts:
            return {"fix": tok, "source": "IAF"}
    last = next((t for t in reversed(pts) if FIX.match(t)), None)
    return {"fix": last, "source": "route"} if last else None


def eta_seconds(dist_nm: float | None, gs: float | None) -> int | None:
    """Czas do lądowania w sekundach z odległości (NM) i prędkości względem ziemi (kt); poniżej 50 kt None."""
    if dist_nm is None or not gs or gs < 50:
        return None
    d = max(float(dist_nm), 0.0)
    final = min(d, FINAL_NM)
    tma = min(max(d - FINAL_NM, 0.0), TMA_NM - FINAL_NM)
    rest = max(d - TMA_NM, 0.0)
    hours = rest / gs + tma / min(gs, TMA_KT) + final / min(gs, APPROACH_KT)
    return round(hours * 3600)


def rel_seconds(t: str | None, now: datetime) -> int | None:
    """'HHMM' jako sekundy względem teraz, w zakresie -12 h..+12 h (przejście przez północ)."""
    t = hhmm(t)
    if t is None:
        return None
    s = int(t[:2]) * 3600 + int(t[2:]) * 60 - (now.hour * 3600 + now.minute * 60 + now.second)
    return (s + 43200) % 86400 - 43200


def choose_eldt(eta_s: int | None, viff_eta: str | None, now: datetime) -> tuple[int | None, str | None]:
    """(ELDT w sekundach od teraz, źródło): vIFF, jeśli wiarygodny, inaczej ETA z pozycji ("POS")."""
    v = rel_seconds(viff_eta, now)
    if v is not None and v >= -60 and (eta_s is None or abs(v - eta_s) <= VIFF_MAX_DIFF_MIN * 60):
        return v, "vIFF"
    if eta_s is not None:
        return eta_s, "POS"
    return None, None


def annotate(board: dict, icao: str, now: datetime, viff_etas: dict | None = None, procs: dict | None = None) -> dict:
    """Dopisuje do pasków tablicy pola DMAN/AMAN (w miejscu; zwraca tę samą tablicę)."""
    viff_etas = viff_etas or {}
    for key in ("departures", "arrivals", "local", "planned"):
        for s in board.get(key, []):
            s["registration"] = registration(s.get("remarks"))
            if s.get("kind") != "arr":
                continue
            s["entry_fix"] = entry_fix(icao, s.get("route"), s.get("proc"), procs)
            # po lądowaniu (na ziemi przy lotnisku) bez ETA; na ziemi daleko = jeszcze na lotnisku odlotu
            landed = s.get("state") == "ground" and not s.get("away") and not s.get("planned")
            eta_s = eta_seconds(s.get("dist_nm"), s.get("groundspeed")) if s.get("state") == "air" else None
            v = viff_etas.get(s.get("callsign"))
            s["eta_s"], s["viff_eta"] = eta_s, v
            s["eldt_s"], s["eldt_source"] = (None, None) if landed else choose_eldt(eta_s, v, now)
            s["eldt"] = (now + timedelta(seconds=s["eldt_s"])).strftime("%H%M") if s["eldt_s"] is not None else None
    return board
