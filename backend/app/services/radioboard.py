"""RADIO jako jedna strona, jak w systemie PANDORA (GET /api/radio/board).

Trzy podglądy FIR EPWW: sektory ACC w warstwach LOW, MID i HIGH (data/seed/ownership.json), a do tego warstwy
APP (TMA), TWR (CTR lotnisk), FIS i pasek ATIS. Każda etykieta ma kolejność przejmowania (chain); właściciel to
pierwsze stanowisko z tej kolejności, które jest teraz online (dopasowanie z /api/radio/online, z aliasami sąsiadów).
Sąsiedzi: stosy stanowisk z sektorów przy granicy Polski (pliki .ese sąsiadów, Białoruś z pliku EPWW) z granicami
pionowymi, ułożone wokół podglądów tak jak leżą na mapie.

Część zależna tylko od danych z importu (wycinki, stosy, punkty etykiet, geometria) jest liczona raz i trzymana
w pamięci do zmiany bazy albo ownership.json; przy każdym odświeżeniu liczymy tylko, kto jest online."""

import json
import math
import re
from collections import Counter

from .radio import facility, sector_group, sector_kind

LAYERS = ("LOW", "MID", "HIGH")
TOP_FT = 66000  # FL660: górna granica podglądów (Kaliningrad ma w pliku 99900)

# Sąsiedzi wokół podglądów (Marek, runda 10): lewy podgląd LOW i prawy HIGH; at = strona mapy, po której leży FIR.
STACKS = (
    {"fir": "ESAA", "map": "left", "at": "n", "title": "SVERIGE"},
    {"fir": "EDWW", "map": "left", "at": "nw", "title": "BREMEN"},
    {"fir": "EDUU", "map": "left", "at": "w", "title": "RHEIN"},
    {"fir": "EDMM", "map": "left", "at": "sw", "title": "MÜNCHEN"},
    {"fir": "LKAA", "map": "left", "at": "s", "title": "PRAHA"},
    {"fir": "UMKK", "map": "right", "at": "n", "title": "KALININGRAD"},
    {"fir": "EYVL", "map": "right", "at": "ne", "title": "VILNIUS"},
    {"fir": "UMMV", "map": "right", "at": "e", "title": "MINSK"},
    {"fir": "UKLV", "map": "right", "at": "se", "title": "LVIV"},
    {"fir": "LZBB", "map": "right", "at": "s", "title": "BRATISLAVA"},
)
STACK_FIRS = {s["fir"] for s in STACKS}
NEAR_KM = 12  # wycinek sąsiada "przy granicy": jakiś punkt jego obrysu najwyżej tyle od granicy FIR EPWW
# stanowiska, które nie tworzą wiersza w stosie sąsiada (ziemia, ATIS, FMP)
NO_ROW = {"GND", "DEL", "RMP", "ATIS", "FMP", "TMU", "OBS"}
APP_TYPES = {"APP", "DEP"}
INFO = re.compile(r"_(?:I|IE|IW|IS|IN|FIS)_[A-Z]+$")  # stanowiska informacji (EPWA_I_APP, EPGD_IE_APP)
ACC_CS = re.compile(r"^EPWW_[A-Z0-9_]*(?:CTR|FSS)$")
FIS_NAME = re.compile(r"^FIS_|^[A-Z]{3}_FIS_|^EPWW-MIDSEA$")
EP_ICAO = re.compile(r"EP[A-Z]{2}")


# --- kolejność przejmowania (jak frontend/js/chains.js)
def tma_topdown(name: str, own: dict) -> dict | None:
    for key, v in (own.get("tma_topdown") or {}).items():
        if any(name.startswith(p) for p in v.get("ese_prefixes") or ()):
            return {"name": key, **v}
    return None


def air_chain(name: str, owners: list[str], own: dict) -> list[str]:
    """Kolejność dla wycinka spoza sektorów ACC: stanowiska z listy OWNER przed pierwszym ACC, potem ACC – dla TMA
    z tabeli top-down om.plvacc.pl (tma_topdown), gdy prefiks wycinka pasuje – i to, co plik stawia za ACC."""
    lst = [o for o in owners if o]
    first = next((i for i, cs in enumerate(lst) if ACC_CS.match(cs)), len(lst))
    local, rest = lst[:first], lst[first:]
    tail = [cs for cs in rest if not ACC_CS.match(cs)]
    t = tma_topdown(name, own)
    acc_part = t["chain"] if t and t.get("chain") else [cs for cs in rest if ACC_CS.match(cs)]
    out = list(local)
    for cs in [*acc_part, *tail]:
        if cs not in out:
            out.append(cs)
    return out


def is_on(cs: str, online: dict) -> bool:
    return cs in online and facility(cs) != "ATIS"


def first_online(chain, online: dict) -> str | None:
    return next((cs for cs in chain if is_on(cs, online)), None)


def owners_of_chains(chains, online: dict) -> list[str]:
    """Właściciele kilku wycinków jednej etykiety: różne pierwsze stanowiska online, od najczęstszego."""
    cnt = Counter(o for ch in chains if (o := first_online(ch, online)))
    return [cs for cs, _ in cnt.most_common()]


# --- geometria
def rings(geom: dict) -> list[list]:
    """Zewnętrzne pierścienie Polygon/MultiPolygon."""
    if not geom:
        return []
    if geom.get("type") == "Polygon":
        return [geom["coordinates"][0]] if geom.get("coordinates") else []
    if geom.get("type") == "MultiPolygon":
        return [p[0] for p in geom.get("coordinates") or [] if p]
    return []


def ring_area(ring) -> float:
    if len(ring) < 3:
        return 0.0
    k = math.cos(math.radians(ring[0][1]))
    s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))
    return abs(s) * k / 2


def geom_area(geom: dict) -> float:
    return sum(ring_area(r) for r in rings(geom))


def label_point(geom: dict, n: int = 22) -> list[float] | None:
    """[lat, lon] wewnątrz wielokąta, możliwie daleko od krawędzi (próbki na siatce, jak labelPoint w coverage.js)."""
    best, best_d = None, -1.0
    for ring in rings(geom):
        if len(ring) < 3:
            continue
        k = math.cos(math.radians(ring[0][1]))
        pts = [(x * k, y) for x, y in ring]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        for i in range(n):
            for j in range(n):
                x, y = x0 + (x1 - x0) * (i + 0.5) / n, y0 + (y1 - y0) * (j + 0.5) / n
                inside, d = False, math.inf
                for a in range(len(pts)):
                    ax, ay = pts[a]
                    bx, by = pts[a - 1]
                    if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
                        inside = not inside
                    dx, dy = bx - ax, by - ay
                    l2 = dx * dx + dy * dy
                    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2)) if l2 else 0.0
                    d = min(d, math.hypot(x - ax - t * dx, y - ay - t * dy))
                if inside and d > best_d:
                    best_d, best = d, [round(y, 4), round(x / k, 4)]
    return best


KX, KY = 111.32 * math.cos(math.radians(52)), 110.57  # km na stopień w rejonie Polski


def _densify(ring, step_km: float = 2.0):
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        n = max(1, int(math.hypot((x2 - x1) * KX, (y2 - y1) * KY) / step_km))
        for i in range(n):
            yield (x1 + (x2 - x1) * i / n) * KX, (y1 + (y2 - y1) * i / n) * KY


class Border:
    """Granica FIR EPWW jako punkty co ~2 km w siatce: szybkie "czy wycinek sąsiada dotyka Polski"."""

    def __init__(self, geom: dict, near_km: float = NEAR_KM):
        self.near = near_km
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for ring in rings(geom):
            for x, y in _densify(ring):
                self.cells.setdefault((int(x // near_km), int(y // near_km)), []).append((x, y))

    def touches(self, geom: dict) -> bool:
        r2 = self.near * self.near
        for ring in rings(geom):
            for x, y in _densify(ring):
                cx, cy = int(x // self.near), int(y // self.near)
                for i in (cx - 1, cx, cx + 1):
                    for j in (cy - 1, cy, cy + 1):
                        if any((x - px) ** 2 + (y - py) ** 2 <= r2 for px, py in self.cells.get((i, j), ())):
                            return True
        return False


def simplify(geom: dict, tol: float = 0.003, nd: int = 3) -> dict:
    """Uproszczona geometria do rysowania podglądów (Douglas–Peucker, zaokrąglenie do ~100 m)."""
    def dp(pts):
        if len(pts) < 3:
            return pts
        keep = [False] * len(pts)
        keep[0] = keep[-1] = True
        stack = [(0, len(pts) - 1)]
        while stack:
            a, b = stack.pop()
            (ax, ay), (bx, by) = pts[a], pts[b]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            best, bi = -1.0, -1
            for i in range(a + 1, b):
                px, py = pts[i]
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2)) if l2 else 0.0
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
                if d > best:
                    best, bi = d, i
            if best > tol:
                keep[bi] = True
                stack += [(a, bi), (bi, b)]
        return [p for p, k in zip(pts, keep) if k]

    out = []
    for ring in rings(geom):
        r = dp([[round(x, nd), round(y, nd)] for x, y in ring])
        if len(r) >= 3:
            out.append([r])
    return {"type": "MultiPolygon", "coordinates": out}


# --- część stała: z bazy po imporcie i z ownership.json
def make_slice(fir: str, name: str, lo: int, hi: int, owners: list[str], geom: dict) -> dict:
    return {"fir": fir, "name": name, "lo": lo, "hi": min(hi, TOP_FT), "owners": owners, "geom": geom}


def acc_sectors(own: dict) -> list[dict]:
    """Sektory ACC × warstwy: id jak w PANDORZE bez numeru (litera warstwy + litera sektora: LB, MC, HN),
    nominal = pierwsze stanowisko kolejności (stanowisko sektora)."""
    out = []
    for letter in sorted(own.get("sectors") or {}):
        for ly in LAYERS:
            chain = (own["sectors"][letter] or {}).get(ly) or []
            if chain:
                out.append({"id": ly[0] + letter, "letter": letter, "layer": ly, "chain": chain, "nominal": chain[0]})
    return out


def _icao_of(names: list[str]) -> str | None:
    cnt = Counter(m.group() for n in names if (m := EP_ICAO.match(n)))
    return cnt.most_common(1)[0][0] if cnt else None


def _largest(slices: list[dict]) -> dict:
    return max(slices, key=lambda s: geom_area(s["geom"]))


def tma_groups(slices: list[dict], own: dict) -> list[dict]:
    """Warstwa APP: TMA z kolejnością APP; wspólne części kilku TMA (EPPO_EPGD_…) i CTA tylko rysujemy."""
    by: dict[str, list[dict]] = {}
    for s in slices:
        if len(set(EP_ICAO.findall(s["name"]))) > 1 or s["name"].startswith("CTA"):
            continue
        t = tma_topdown(s["name"], own)
        group = sector_group("EPWW", s["name"], "tma")
        key = t["name"] if t and t["name"].replace("TMA ", "") not in group else group
        by.setdefault(key, []).append({**s, "chain": air_chain(s["name"], s["owners"], own)})
    out = []
    for key, sl in by.items():
        apps: Counter = Counter()
        for s in sl:
            for cs in s["chain"]:
                if ACC_CS.match(cs):
                    break
                if facility(cs) in APP_TYPES and not INFO.search(cs):
                    apps[cs] += 1
        if not apps:
            continue  # TMA bez APP (Szczecin, Lublin, Mazury): przejmuje ACC, nie ma czego pokazać w warstwie APP
        icao = _icao_of([s["name"] for s in sl])
        ranked = [cs for cs, _ in apps.most_common()]
        nominal = f"{icao}_APP" if f"{icao}_APP" in apps else ranked[0]
        big = _largest(sl)
        out.append({"id": key, "icao": icao, "at": label_point(big["geom"]), "nominal": nominal, "apps": ranked,
                    "chains": _uniq_chains(sl), "lo": min(s["lo"] for s in sl), "hi": max(s["hi"] for s in sl)})
    return sorted(out, key=lambda g: g["id"])


def _uniq_chains(sl: list[dict]) -> list[list[str]]:
    """Różne kolejności wycinków, od najczęstszej."""
    cnt = Counter(tuple(s["chain"]) for s in sl)
    return [list(c) for c, _ in cnt.most_common()]


def ctr_groups(slices: list[dict], own: dict, coords: dict, positions: dict) -> list[dict]:
    """Warstwa TWR: CTR lotnisk (wieża = pierwsze stanowisko TWR z listy OWNER), ATIS lotniska do TWR/ATIS."""
    by: dict[str, list[dict]] = {}
    for s in slices:
        by.setdefault(sector_group("EPWW", s["name"], "ctr"), []).append({**s, "chain": air_chain(s["name"], s["owners"], own)})
    out = []
    for key, sl in by.items():
        icao = _icao_of([s["name"] for s in sl])
        chains = _uniq_chains(sl)
        nominal = next((cs for ch in chains for cs in ch if facility(cs) == "TWR"), chains[0][0] if chains[0] else None)
        if not nominal:
            continue
        at = list(coords[icao]) if icao in coords else label_point(_largest(sl)["geom"])
        atis = f"{icao}_ATIS" if f"{icao}_ATIS" in positions else None
        out.append({"id": key, "icao": icao, "at": at, "nominal": nominal, "chains": chains, "atis": atis,
                    "hi": max(s["hi"] for s in sl)})
    return sorted(out, key=lambda g: g["id"])


def fis_groups(slices: list[dict], own: dict) -> list[dict]:
    """Warstwa FIS: rejony FIS (wycinki FIS_…, MIDSEA) wg stanowiska, które jest w nich pierwsze."""
    by: dict[str, list[dict]] = {}
    for s in slices:
        if not FIS_NAME.match(s["name"]) or not s["owners"]:
            continue
        by.setdefault(s["owners"][0], []).append({**s, "chain": air_chain(s["name"], s["owners"], own)})
    out = []
    for nominal, sl in by.items():
        big = _largest(sl)
        out.append({"id": sector_group("EPWW", big["name"], "fis"), "nominal": nominal, "at": label_point(big["geom"]),
                    "chains": _uniq_chains(sl), "hi": max(s["hi"] for s in sl)})
    return sorted(out, key=lambda g: (g["id"], g["nominal"]))


def stack_of(fir: str, name: str) -> str | None:
    """Stos sąsiada dla wycinka: EDUU (Rhein) osobno, choć jego sektory są w plikach EDWW i EDMM."""
    if name.upper().startswith("EDUU"):
        return "EDUU"
    fir = fir.upper()
    return fir if fir in STACK_FIRS else None


def _row_ok(r: dict) -> bool:
    """Wieża tylko z przestrzenią ponad FL095 (LZTT TWR – Tatry), APP/DEP sięgające co najmniej FL095 (bez samych
    podejść końcowych, np. EDDB AN APP do FL055); CTR/FSS (także informacja) zawsze."""
    fac = facility(r["callsign"])
    if fac == "TWR":
        return r["hi"] > 9500
    return fac in ("CTR", "FSS", "") or r["hi"] >= 9500


def stack_rows(slices: list[dict], border: Border) -> dict[str, list[dict]]:
    """Wiersze stosów sąsiadów: stanowisko pierwsze w kolejności wycinka przy granicy Polski, z zakresem wysokości
    (najniższa dolna i najwyższa górna granica tych wycinków). Wieże tylko, gdy ich przestrzeń sięga ponad FL095
    (np. LZTT TWR – Tatry); stanowiska naziemne pomijamy."""
    rows: dict[str, dict[str, dict]] = {}
    for s in slices:
        key = stack_of(s["fir"], s["name"])
        if not key or not s["owners"] or s["lo"] >= TOP_FT:
            continue
        primary = s["owners"][0]
        if facility(primary) in NO_ROW or not border.touches(s["geom"]):
            continue
        r = rows.setdefault(key, {}).setdefault(primary, {"callsign": primary, "lo": s["lo"], "hi": s["hi"],
                                                          "sectors": [], "chains": Counter()})
        r["lo"], r["hi"] = min(r["lo"], s["lo"]), max(r["hi"], s["hi"])
        if s["name"] not in r["sectors"]:
            r["sectors"].append(s["name"])
        r["chains"][tuple(s["owners"])] += 1
    out = {}
    for key, by in rows.items():
        lst = [{**r, "chains": [list(c) for c, _ in r["chains"].most_common()]} for r in by.values() if _row_ok(r)]
        out[key] = sorted(lst, key=lambda r: (-r["hi"], -r["lo"], r["callsign"]))
    return out


def build_static(*, own: dict, epww_slices: list[dict], nb_slices: list[dict], positions: dict, coords: dict,
                 border_geom: dict, firs_geo: list[dict]) -> dict:
    """Wszystko, co nie zależy od tego, kto jest online.

    epww_slices: wycinki z pliku EPWW ({fir, name, lo, hi, owners (znaki), geom}); nb_slices: wycinki sąsiadów
    (pliki sąsiadów + FIR-y bez własnego pliku z pliku EPWW); positions: znak → {frequency, name, ...};
    coords: ICAO → (lat, lon); border_geom: granica FIR EPWW (VATSpy); firs_geo: [{id, geometry}] do tła."""
    kinds: dict[str, list[dict]] = {"acc": [], "tma": [], "ctr": [], "fis": []}
    for s in epww_slices:
        k = sector_kind(s["fir"], s["name"])
        if s["fir"].upper() == "EPWW" and k in kinds:
            kinds[k].append(s)
    border = Border(border_geom)
    rows = stack_rows(nb_slices, border)
    stacks = [{**st, "rows": rows.get(st["fir"], [])} for st in STACKS]
    acc_geo, seen = [], set()
    for s in kinds["acc"]:
        m = re.match(r"^EPWW([A-Z])", s["name"])
        if m and (own.get("sectors") or {}).get(m.group(1)):
            acc_geo.append({"letter": m.group(1), "name": s["name"], "lo": s["lo"], "hi": s["hi"],
                            "geometry": simplify(s["geom"], 0.002, 4)})

    def outlines(sl, **extra):
        out = []
        for s in sl:
            g = simplify(s["geom"])
            key = json.dumps(g["coordinates"])
            if key not in seen:
                seen.add(key)
                out.append({"name": s["name"], "geometry": g, **{k: f(s) for k, f in extra.items()}})
        return out

    fis_sl = [s for s in kinds["fis"] if FIS_NAME.match(s["name"])]
    geo = {"border": simplify(border_geom, 0.002), "firs": [{"id": f["id"], "geometry": simplify(f["geometry"], 0.02, 2)}
                                                           for f in firs_geo],
           "acc": acc_geo, "tma": outlines(kinds["tma"]), "ctr": outlines(kinds["ctr"]),
           "fis": outlines(fis_sl, owner=lambda s: s["owners"][0] if s["owners"] else None)}
    atis = sorted(cs for cs in positions if cs.startswith("EP") and facility(cs) == "ATIS")
    return {"layers": own.get("layers") or {}, "source": own.get("source_label") or own.get("source") or "",
            "acc_positions": sorted(own.get("acc_positions") or {}),
            "sectors": acc_sectors(own), "tma": tma_groups(kinds["tma"], own),
            "twr": ctr_groups(kinds["ctr"], own, coords, positions), "fis": fis_groups(fis_sl, own),
            "atis": atis, "stacks": stacks, "geo": geo}


# --- część zmienna: kto jest online
def _referenced(static: dict) -> set[str]:
    refs: set[str] = set(static["atis"])
    for s in static["sectors"]:
        refs.update(s["chain"])
    for g in static["tma"] + static["twr"] + static["fis"]:
        refs.add(g["nominal"])
        refs.update(cs for ch in g["chains"] for cs in ch)
        if g.get("atis"):
            refs.add(g["atis"])
    for st in static["stacks"]:
        for r in st["rows"]:
            refs.add(r["callsign"])
            refs.update(cs for ch in r["chains"] for cs in ch)
    return refs


def board(static: dict, online: dict, positions: dict, feed_atis: list[dict] = ()) -> dict:
    """Stan tablicy dla zestawu zalogowanych (online: znak stanowiska → kontroler, jak /api/radio/online)."""
    sectors = [{**s, "owner": first_online(s["chain"], online)} for s in static["sectors"]]
    # APP, TWR, FIS: właściciel spośród stanowisk tej przestrzeni (APP, TWR, informacja); sektor ACC, który przejmuje
    # ją top-down, osobno (topdown) – ACC widać już na podglądach LOW/MID/HIGH
    radar = set(static.get("acc_positions") or ())

    def local(g: dict) -> dict:
        own_ = owners_of_chains([[cs for cs in ch if cs not in radar] for ch in g["chains"]], online)
        top = None if own_ else first_online(g["chains"][0] if g["chains"] else [], online)
        return {**g, "owners": own_, "topdown": top}

    tma = [local(g) for g in static["tma"]]
    twr = [local(g) for g in static["twr"]]
    fis = [local(g) for g in static["fis"]]
    stacks = []
    for st in static["stacks"]:
        rows = []
        for r in st["rows"]:
            own_on = is_on(r["callsign"], online)
            owners = [r["callsign"]] if own_on else owners_of_chains(r["chains"], online)
            rows.append({**r, "owners": owners})
        stacks.append({**st, "rows": rows})
    # ATIS: polskie stanowiska ATIS z pliku .ese oraz zalogowane EP**_ATIS spoza niego (np. EPWA_D_ATIS)
    atis = [{"callsign": cs, "icao": cs[:4], "online": cs in online} for cs in static["atis"]]
    known = {online[a["callsign"]]["callsign"] for a in atis if a["online"]}
    extra = {}
    for c in feed_atis:
        cs = str(c.get("callsign") or "").upper()
        if cs.startswith("EP") and cs.endswith("_ATIS") and cs not in known and cs not in static["atis"]:
            extra[cs] = {"callsign": cs, "icao": cs[:4], "online": True, "extra": True}
    atis = sorted(atis + list(extra.values()), key=lambda a: (a["icao"], a["callsign"]))
    refs = _referenced(static) | set(extra)
    pos = {}
    for cs in sorted(refs):
        p = positions.get(cs) or {}
        pos[cs] = {"frequency": p.get("frequency") or "", "name": p.get("radio") or p.get("name")}
    for cs, a in extra.items():
        info = next((c for c in feed_atis if str(c.get("callsign") or "").upper() == cs), {})
        pos[cs]["frequency"] = info.get("frequency") or ""
        online = {**online, cs: {"callsign": cs, "frequency": info.get("frequency"), "name": info.get("name"),
                                 "cid": info.get("cid"), "logon_time": info.get("logon_time"),
                                 "atis_code": info.get("atis_code"), "text_atis": info.get("text_atis")}}
    return {"layers": static["layers"], "source": static["source"], "sectors": sectors, "tma": tma, "twr": twr,
            "fis": fis, "atis": atis, "stacks": stacks, "pos": pos,
            "online": {cs: v for cs, v in online.items() if cs in refs or cs in extra}}
