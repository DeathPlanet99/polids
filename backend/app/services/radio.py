"""RADIO: wspólna lista stanowisk (plik .ese + wszystkie stanowiska sąsiednich FIR-ów z vacs-data), zasięg stanowiska
na mapie (granica z VATSpy albo przybliżony okrąg wokół lotniska), rodzaje wycinków sektorów z pliku .ese,
zakładki sąsiadów oraz znaki radiowe i nazwy sektorów stanowisk sąsiadów (baza wiedzy VATSIM Germany, LOA)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

TYPES = ("CTR", "FSS", "APP", "DEP", "TWR", "GND", "DEL", "RMP", "ATIS", "FMP", "TMU", "OBS")
# Przybliżony zasięg stanowiska lotniskowego bez wycinków sektorów i bez granicy w VATSpy (NM)
CIRCLE_NM = {"APP": 30, "DEP": 30, "TWR": 10}
GROUND = ("GND", "DEL", "RMP", "ATIS")
# FIR-y sąsiadów (katalogi vacs-data): nazwa pokazywana przy stanowiskach bez nazwy w pliku .ese
FIR_NAMES = {"EDWW": "FIR Bremen", "EDMM": "FIR München", "EDGG": "FIR Langen", "EDUU": "UIR Rhein", "LK": "FIR Praha",
             "LZ": "FIR Bratislava", "ES": "FIR Sverige", "EY": "FIR Vilnius", "EK": "FIR København",
             "UMKK": "FIR Kaliningrad"}
# prefiksy, które w katalogu FIR-u vacs-data są innym organem (EDYY = Maastricht UAC nad FIR Bremen)
PREFIX_NAMES = {"EDYY": "UAC Maastricht", "EDUU": "UIR Rhein", "EDGG": "FIR Langen"}
# prefiksy bez własnej granicy w VATSpy: FIR-y, nad którymi leży stanowisko (UIR Rhein nad Langen i München,
# Maastricht UAC nad Bremen, szwedzkie ESCR/ESDK/ESPF – cała Szwecja)
NB_ALIAS = {"EDUU": ["EDGG", "EDMM"], "EDYY": ["EDWW"], "ESCR": ["ESAA"], "ESDK": ["ESAA"], "ESPF": ["ESAA"]}


@dataclass
class Station:
    """Stanowisko do dopasowania z kontrolerami w sieci (te same pola co AtcPosition)."""
    callsign: str
    prefix: str | None
    frequency: str


def facility(callsign: str) -> str:
    parts = (callsign or "").upper().split("_")
    return parts[-1] if len(parts) > 1 and parts[-1] in TYPES else ""


def merge_positions(ese: list[dict], vacs_positions: list[dict], nb: list[dict] = (),
                    nb_listed: set[str] = frozenset()) -> list[dict]:
    """Stanowiska z pliku .ese uzupełnione o stanowiska sąsiadów z vacs-data i z plików .ese sąsiadów
    (klucz: znak wywoławczy; pierwszeństwo: plik EPWW, vacs-data, pliki sąsiadów).

    Pozycje tylko z vacs-data nie mają nazwy ani ID z pliku .ese; FMP/TMU pomijamy (to nie są stanowiska ATC).
    nb: stanowiska z kolejności przejmowania sektorów sąsiadów ({callsign, name, frequency, prefix, source, fir});
    nb_listed: znaki z tych kolejności – `nb_ese` = zasięg z dokładnych sektorów sąsiada zamiast VATSpy."""
    fir_of = {p["id"]: p.get("fir_dir") for p in vacs_positions}
    out = {p["callsign"]: {**p, "fir": fir_of.get(p["callsign"]), "facility": facility(p["callsign"]),
                           "in_ese": True, "in_vacs": p["callsign"] in fir_of} for p in ese}
    # częstotliwość stanowiska z kolejności przejmowania sąsiada: z jego pliku .ese, z którego są też sektory
    # (vacs-data bywa inne: EKCH_P_APP Kastrup Final to w pliku Danii 120.205, w vacs-data 131.405)
    nb_freq = {n["callsign"]: n.get("frequency") for n in nb if n["callsign"] in nb_listed and n.get("frequency")}
    for v in vacs_positions:
        cs = v["id"]
        if cs in out or v.get("facility_type") in ("FMP", "TMU") or facility(cs) in ("FMP", "TMU"):
            continue
        out[cs] = {"callsign": cs, "name": None, "frequency": nb_freq.get(cs) or v.get("frequency") or "", "position_id": None,
                   "prefix": (v.get("prefixes") or [cs.split("_")[0]])[0], "fir": v.get("fir_dir"),
                   "facility": v.get("facility_type") or facility(cs), "in_ese": False, "in_vacs": True}
    for n in nb:
        cs = n["callsign"]
        if cs in out or cs.upper().startswith("EP") or facility(cs) in ("FMP", "TMU"):
            continue
        out[cs] = {"callsign": cs, "name": n.get("name"), "frequency": n.get("frequency") or "", "position_id": None,
                   "prefix": n.get("prefix") or cs.split("_")[0], "fir": n.get("fir"), "facility": facility(cs),
                   "in_ese": False, "in_vacs": False, "nb_source": n.get("source")}
    for p in out.values():
        p["nb_ese"] = p["callsign"] in nb_listed
        p["fir_name"] = PREFIX_NAMES.get(p.get("prefix") or "") or FIR_NAMES.get(p["fir"] or "")
    return sorted(out.values(), key=lambda p: p["callsign"])


def _vatspy_index(firs: dict) -> tuple[set[str], list[tuple[str, str]], dict[str, list[str]]]:
    ids = {f["properties"]["id"] for f in firs.get("features", [])}
    prefixes = sorted(((pre, f["properties"]["id"]) for f in firs.get("features", []) for pre in f["properties"].get("prefixes", [])),
                      key=lambda x: -len(x[0]))
    uirs = {u["id"]: [f for f in u.get("firs", []) if f in ids] for u in firs.get("uirs", [])}
    return ids, prefixes, uirs


def vatspy_range(callsign: str, firs: dict, prefix: str | None = None) -> dict | None:
    """Granica stanowiska CTR/FSS w VATSpy: EDWW_FLG_CTR → EDWW-FLG, ESMM_7_CTR → ESMM-7, LKAA_CTR → LKAA.

    Bez własnego sektora w VATSpy: cały FIR z pasującym prefiksem (exact=False, przybliżenie); UIR → jego FIR-y.
    Na końcu prefiks stanowiska z vacs-data (EKCH_FW_CTR → EKDK) i tabela NB_ALIAS (EDUU → EDGG + EDMM), zawsze
    jako przybliżenie."""
    ids, prefixes, uirs = _vatspy_index(firs)
    parts = callsign.upper().split("_")
    base = parts[:-1] if facility(callsign) else parts
    if not base:
        return None
    if (cand := "-".join(base)) in ids:
        return {"kind": "vatspy", "ids": [cand], "exact": True}
    key = "_".join(base)
    for pre, fid in prefixes:
        if key == pre or key.startswith(pre + "_"):
            return {"kind": "vatspy", "ids": [fid], "exact": key == pre}
    if uirs.get(key):
        return {"kind": "vatspy", "ids": uirs[key], "exact": True}
    if base[0] in ids:
        return {"kind": "vatspy", "ids": [base[0]], "exact": len(base) == 1}
    pre = (prefix or "").upper()
    if pre in ids:
        return {"kind": "vatspy", "ids": [pre], "exact": False}
    if alias := [a for a in NB_ALIAS.get(pre) or NB_ALIAS.get(base[0]) or [] if a in ids]:
        return {"kind": "vatspy", "ids": alias, "exact": False}
    return None


def position_range(callsign: str, firs: dict, lat: float | None = None, lon: float | None = None,
                   prefix: str | None = None) -> dict | None:
    """Zasięg stanowiska na mapie: CTR/FSS granica z VATSpy; APP/DEP/TWR przybliżony okrąg wokół lotniska
    (APP 30 NM, TWR 10 NM); GND/DEL/ATIS sam punkt lotniska. None = nie wiadomo, gdzie jest stanowisko.
    prefix: prefiks stanowiska z pliku .ese albo vacs-data (zapasowe dopasowanie do VATSpy)."""
    fac = facility(callsign)
    if fac in CIRCLE_NM and lat is not None:
        return {"kind": "circle", "nm": CIRCLE_NM[fac], "lat": lat, "lon": lon}
    if fac in GROUND and lat is not None:
        return {"kind": "point", "lat": lat, "lon": lon}
    if fac in ("CTR", "FSS", "APP", "DEP", ""):
        r = vatspy_range(callsign, firs, prefix)
        # APP bez lotniska (np. ESMM_IS_APP): FIR z VATSpy, zawsze jako przybliżenie
        return {**r, "exact": r["exact"] and fac in ("CTR", "FSS", "")} if r else None
    return None


# --- rodzaje wycinków sektorów z pliku .ese (RADIO: GET /api/radio/board, także /api/radio/sectors)
_ACC = re.compile(r"^EPWW([A-Z])(?:-[A-Z]+)?$")
_CTR = re.compile(r"^(EP[A-Z]{2})_M?CTR\d*$")
_TMA = re.compile(r"^(EP[A-Z]{2})_(?:[A-Z_]*TMA|APP_|DEP\d|DIR\d)|^CTA\d")
_FIS = re.compile(r"^FIS_|^[A-Z]{3}_FIS_|^EP[A-Z]{2}_ATZ|^EPTR\d|^EPWW-MIDSEA$")


def sector_kind(fir: str, name: str) -> str | None:
    """acc (sektory ACC EPWW), tma (TMA/APP/CTA), ctr, fis (FIS, ATZ, TRA), nb (FIR sąsiedni);
    None = wycinek techniczny sąsiada (TECH …, T-…), którego nie rysujemy."""
    fir, name = fir.upper(), name.upper()
    if fir != "EPWW":
        return None if name.startswith(("TECH ", "T-")) else "nb"
    if _ACC.match(name):
        return "acc"
    if _CTR.match(name):
        return "ctr"
    if _FIS.match(name):
        return "fis"
    return "tma"


EP_CITY = {"EPWA": "Warszawa", "EPKK": "Kraków", "EPGD": "Gdańsk", "EPPO": "Poznań", "EPKT": "Katowice", "EPWR": "Wrocław",
           "EPLL": "Łódź", "EPLB": "Lublin", "EPRZ": "Rzeszów", "EPSC": "Szczecin", "EPSY": "Olsztyn-Mazury",
           "EPBY": "Bydgoszcz", "EPZG": "Zielona Góra", "EPMO": "Modlin", "EPRA": "Radom", "EPDE": "Dęblin",
           "EPKS": "Krzesiny"}
FIS_REGION = {"WAW": "Warszawa", "GDN": "Gdańsk", "KRK": "Kraków", "POZ": "Poznań"}


def sector_group(fir: str, name: str, kind: str | None = None) -> str:
    """Nazwa zbiorcza wycinka do listy "kto co obsługuje": TMA Warszawa, CTR Kraków, FIS Gdańsk, ATZ EPBA, CTA 05,
    sąsiedzi bez numeru części (ESMM 8-3 → ESMM 8, EDWWFLG1 → EDWWFLG)."""
    kind = kind or sector_kind(fir, name)
    n = name.upper()
    if not kind:
        return n
    if kind == "nb":
        g = re.sub(r"(?:[-_ ]?PART\d*|[-_ ]\d|(?<=[A-Z])\d)$", "", n).strip() or n
        # krótkie nazwy bez kodu ICAO (LZBB "CTR", LKAA "NL") z kodem FIR-u
        return g if g[:2] == fir[:2] or re.match(r"^[ELU][A-Z]{3}[ _]", g) or len(g) > 12 else f"{fir} {g}"
    if kind == "acc":
        return f"Sektor {_ACC.match(n).group(1)}"
    if m := re.match(r"^CTA(\d+)", n):
        return f"CTA {m.group(1)}"
    if n == "EPWW-MIDSEA":
        return "FIS Gdańsk (MIDSEA)"
    if kind == "fis" and (m := re.match(r"^(?:FIS_)?([A-Z]{3})(?:_FIS)?_", n)):
        return f"FIS {FIS_REGION.get(m.group(1), m.group(1))}"
    if m := re.match(r"^(EP[A-Z]{2})_ATZ", n):
        return f"ATZ {m.group(1)}"
    if n.startswith("EPTR"):
        return f"TRA {re.sub(r'[A-Z]$', '', n)}"
    icao = n[:4]
    # TMA Poznań dzieli się na N i S z osobną kolejnością przejmowania (tma_topdown w ownership.json)
    part = m.group(1) if kind == "tma" and (m := re.match(r"^EP[A-Z]{2}_TMA_([NS])_", n)) else ""
    return f"{kind.upper()} {EP_CITY.get(icao, icao)} {part}".rstrip()


# --- dane z data/seed (loa.json, names_de.json): z pamięci, dopóki plik się nie zmieni
_SEED: dict[Path, tuple[tuple[int, int], object]] = {}


def seed_json(path: Path):
    """JSON z pliku, trzymany w pamięci do zmiany pliku (czas modyfikacji i rozmiar): poprawiony ręcznie loa.json
    widać bez restartu serwera. Brak pliku: OSError, uszkodzony JSON: ValueError."""
    st = path.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _SEED.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    data = json.loads(path.read_text("utf-8"))
    _SEED[path] = (stamp, data)
    return data


# --- zakładki sąsiadów w RADIO (klucz = FIR zakładki, ten sam co w loa.json; EDUU = zakładka "EDUU/EDYY")
# katalog FIR-u w vacs-data (pole fir stanowiska) → zakładka
TAB_BY_DIR = {"EDWW": "EDWW", "EDMM": "EDMM", "EDUU": "EDUU", "LK": "LKAA", "LZ": "LZBB", "UMKK": "UMKK", "EY": "EYVL",
              "ES": "ESAA", "EK": "EKDK"}
# organy przestrzeni górnej nad Niemcami (Rhein Radar / Karlsruhe UAC, Maastricht UAC): własna zakładka, choć w vacs-data
# i w plikach .ese sąsiadów są w katalogach EDWW (EDYY, EDUU OSE/HVL) i EDMM (EDUU SPE)
UPPER_DE = {"EDUU", "EDYY"}
EDWW_PREFIXES = {"EDWW", "EDDB", "EDAH"}
TAB_BY_PREFIX = (("LK", "LKAA"), ("LZ", "LZBB"), ("UK", "UKLV"), ("UM", "UMMV"), ("EY", "EYVL"), ("ES", "ESAA"),
                 ("EK", "EKDK"))


def neighbour_tab(p: dict) -> str | None:
    """Zakładka sąsiada: EDWW, EDMM, EDUU (EDUU/EDYY), LKAA, LZBB, UKLV, UMMV, UMKK, EYVL, ESAA, EKDK albo INNE;
    None = stanowisko polskie (EP**). Najpierw EDUU/EDYY (prefiks, katalog vacs-data albo początek znaku), potem
    katalog FIR-u z vacs-data, bez niego prefiks stanowiska."""
    cs = (p.get("callsign") or "").upper()
    head = cs.split("_")[0]
    pre = (p.get("prefix") or head).upper()
    if pre.startswith("EP") or (not p.get("prefix") and head.startswith("EP")):
        return None
    fir = p.get("fir")
    if UPPER_DE & {pre, head, fir}:
        return "EDUU"
    if fir:
        return TAB_BY_DIR.get(fir, "INNE")
    if pre.startswith("ED"):
        return "EDWW" if pre in EDWW_PREFIXES else "EDMM"
    if pre == "UMKK" or pre.startswith("RU-"):
        return "UMKK"
    return next((tab for start, tab in TAB_BY_PREFIX if pre.startswith(start)), "INNE")


# --- znaki radiowe i nazwy sektorów stanowisk sąsiadów
# środek znaku stanowiska informacji (EKDK_I_CTR, LKAA_FIC_FSS): bez znaku "… Control" i nie wyznacza go innym
INFO_MID = {"I", "FIS", "FIC", "IN", "INFO"}


def _kb_index(names_de: dict) -> dict[str, dict]:
    """names_de.json (positions): login → wpis, także loginy zastępcze (aliases: EDWW_MR1_CTR → EDWW_MRZ_CTR)."""
    out: dict[str, dict] = {}
    for cs, e in (names_de.items() if isinstance(names_de, dict) else ()):
        if isinstance(e, dict):
            out[cs.upper()] = e
    for e in list(out.values()):
        for a in e.get("aliases") or []:
            out.setdefault(str(a).upper(), e)
    return out


def _loa_index(loa_firs: dict) -> dict[str, dict]:
    """Stanowiska sąsiadów wymienione w LOA (strona NB): znak → znak radiowy, sektory, uwagi i tytuły LOA.
    Jedno stanowisko bywa kilka razy (LKAA_U_CTR: sektory NU i SU) – sektory łączymy."""
    out: dict[str, dict] = {}
    for f in (loa_firs.values() if isinstance(loa_firs, dict) else ()):
        if not isinstance(f, dict) or not isinstance(f.get("positions"), list):
            continue
        for lp in f["positions"]:
            cs = str(lp.get("callsign") or "").upper() if isinstance(lp, dict) else ""
            if not cs or lp.get("side") != "NB":
                continue
            e = out.setdefault(cs, {"radio": "", "sectors": [], "notes": [], "titles": []})
            e["radio"] = e["radio"] or str(lp.get("radio") or "")
            for key, val in (("sectors", lp.get("sector")), ("notes", lp.get("note")), ("titles", f.get("title"))):
                if val and val not in e[key]:
                    e[key].append(str(val))
    return out


def _nb_acc(p: dict) -> bool:
    """Stanowisko ACC sąsiada (CTR/FSS) bez stanowisk informacji."""
    cs = p["callsign"].upper()
    return p.get("facility") in ("CTR", "FSS") and not cs.startswith("EP") and not INFO_MID & set(cs.split("_")[1:-1])


def radio_names(positions: list[dict], names_de: dict, loa_firs: dict) -> list[dict]:
    """Znak radiowy (`radio`) i nazwa sektora (`sector`) stanowisk sąsiadów, z pierwszeństwem: baza wiedzy VATSIM
    Germany (names_de.json), LOA (loa.json, stanowiska sąsiada), nazwa z pliku .ese (tylko znak radiowy).
    Stanowisko ACC (CTR/FSS) bez żadnej nazwy dostaje znak radiowy pozostałych stanowisk CTR/FSS z tym samym prefiksem,
    gdy wszystkie mają ten sam (ESOS_7_CTR → Sweden Control). Stanowiska informacji (_I_, _FIS_, _FIC_, _IN_, _INFO_)
    w tym nie biorą udziału: ani nie dostają znaku, ani ich "… Information" nie blokuje znaku pozostałym (LKAA_WU_CTR).
    `radio_src` / `sector_src`: kb | loa | ese | prefix | None; `kb` i `loa`: szczegóły do dymku (zakres, uwagi).
    Stanowiska polskie (EP**) zostają z nazwą z pliku .ese (pola puste)."""
    kb, loa = _kb_index(names_de), _loa_index(loa_firs)
    for p in positions:
        cs = p["callsign"].upper()
        p.update({"radio": None, "radio_src": None, "sector": None, "sector_src": None, "kb": None, "loa": None})
        if cs.startswith("EP"):
            continue
        k, lo = kb.get(cs) or {}, loa.get(cs)
        if k.get("radio"):
            p.update(radio=k["radio"], radio_src="kb")
        elif lo and lo["radio"]:
            p.update(radio=lo["radio"], radio_src="loa")
        elif p.get("name"):
            p.update(radio=p["name"], radio_src="ese")
        if k.get("sector"):
            p.update(sector=k["sector"], sector_src="kb")
        elif lo and lo["sectors"]:
            p.update(sector=" / ".join(lo["sectors"]), sector_src="loa")
        if k:
            p["kb"] = {f: k[f] for f in ("url", "limits", "covers", "uwaga") if k.get(f)}
        if lo:
            p["loa"] = {"titles": lo["titles"], "notes": lo["notes"]}
    same: dict[str | None, set[str]] = {}
    for p in positions:
        if _nb_acc(p) and p["radio"]:
            same.setdefault(p.get("prefix"), set()).add(p["radio"])
    for p in positions:
        names = same.get(p.get("prefix")) or set()
        if _nb_acc(p) and not p["radio"] and len(names) == 1:
            p.update(radio=next(iter(names)), radio_src="prefix")
    return positions
