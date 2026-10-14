"""AERODROME: listy lotnisk w FIR-ach (podzakładki EPWW, ESAA, ED, ET, LKAA, LZBB, UMKK, EYVL, UMMV, UKLV)
i stanowiska lotniska do karty lotniska zagranicznego.

Lotniska: navdata (data/import/icao.txt, tabela nav_points, source "icao") uzupełnione o data/seed/airports.csv
(tabela aerodromes, tylko EP) i o kilka lądowisk z listy PANDORY (EP_EXTRA) i z niemieckich plików .ese (ED_EXTRA).
Nazwy WIELKIMI LITERAMI bez polskich znaków, jak w PANDORZE (np. "GDANSK LECH WALESA"). Lądowiska śmigłowców przy
szpitalach i platformy morskie są pomijane.
Każde lotnisko ma rank (0 główne lotnisko miasta, 1 ze stanowiskami ATC, 2 pozostałe): przy kilku trafieniach
wyszukiwarki Enter otwiera to ważniejsze ("warszawa" → EPWA, nie EPBC), oraz alias (inne nazwy do wyszukiwania).

Przypisanie do FIR-u (Marek, 12.10.2026: "dla Niemiec ED oddzielnie i wojskowe ET oddzielnie"):
EP* → EPWW, ES* → ESAA, ED* → ED (cywilne), ET* → ET (wojskowe), LK* → LKAA, LZ* → LZBB, UMK* → UMKK,
pozostałe UM* → UMMV (Białoruś), EY* → EYVL, UK* leżące w granicy FIR UKLV z VATSpy → UKLV
(gdy granicy brak albo jest nieużywalna: UKL*)."""

import re
import time
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Aerodrome, NavPoint
from .positions import all_positions
from .radio import INFO_MID
from .vatsim import FACILITY_TYPES, controller_info, fir_boundaries, match_positions

# kolejność podzakładek AERODROME (po "0" = OVERVIEW)
FIR_KEYS = ("EPWW", "ESAA", "ED", "ET", "LKAA", "LZBB", "UMKK", "EYVL", "UMMV", "UKLV")
FIR_LABELS = {
    "EPWW": "Polska", "ESAA": "Szwecja", "ED": "Niemcy, lotniska cywilne", "ET": "Niemcy, lotniska wojskowe",
    "LKAA": "Czechy", "LZBB": "Słowacja", "UMKK": "Kaliningrad", "EYVL": "Litwa", "UMMV": "Białoruś",
    "UKLV": "Ukraina, FIR Lwów",
}
# prefiksy ICAO, od najdłuższego (UMK przed UM)
_PREFIX_FIR = (("UMK", "UMKK"), ("EP", "EPWW"), ("ES", "ESAA"), ("ED", "ED"), ("ET", "ET"), ("LK", "LKAA"),
               ("LZ", "LZBB"), ("UM", "UMMV"), ("EY", "EYVL"))

# Miasto przed nazwą z navdata, gdy navdata podaje samą dzielnicę albo patrona (EPKK "BALICE", EPGD "LECH WALESA",
# LKPR "RUZYNE", ESSA "ARLANDA"): lista wyszukuje wtedy także po mieście ("KRAKOW", "PRAHA"). Jak w AIP: MIASTO NAZWA.
CITY = {
    "EPWA": "WARSZAWA", "EPBC": "WARSZAWA", "EPMO": "WARSZAWA", "EPKK": "KRAKOW", "EPKC": "KRAKOW",
    "EPKP": "KRAKOW", "EPKT": "KATOWICE", "EPKM": "KATOWICE", "EPGD": "GDANSK", "EPOK": "GDYNIA",
    "EPPO": "POZNAN", "EPKS": "POZNAN", "EPPK": "POZNAN", "EPWR": "WROCLAW", "EPRZ": "RZESZOW",
    "EPSC": "SZCZECIN", "EPSD": "SZCZECIN", "EPZG": "ZIELONA GORA", "EPOD": "OLSZTYN", "EPKA": "KIELCE",
    "EPBA": "BIELSKO-BIALA", "EPKW": "BIELSKO-BIALA", "EPBK": "BIALYSTOK", "EPGI": "GRUDZIADZ",
    "EPLR": "LUBLIN", "EPOP": "OPOLE", "EPKN": "OPOLE", "EPNL": "NOWY SACZ", "EPRG": "RYBNIK",
    "ESSA": "STOCKHOLM", "ESSB": "STOCKHOLM", "ESGG": "GOTEBORG", "ESGP": "GOTEBORG",
    "EDDT": "BERLIN", "EDDI": "BERLIN",
    "LKPR": "PRAHA", "LKKB": "PRAHA", "LKLT": "PRAHA", "LKTB": "BRNO", "LKCM": "BRNO", "LKMT": "OSTRAVA",
    "LZIB": "BRATISLAVA", "LZTT": "POPRAD", "UMKK": "KALININGRAD", "EYKS": "KAUNAS",
}
PL_CITY = {k: v for k, v in CITY.items() if k.startswith("EP")}

# Inne nazwy tylko do wyszukiwania (nie na liście): polskie nazwy miast za granicą i nazwy w języku kraju tam,
# gdzie navdata ma angielską ("PRAGA", "LWOW", "MUENCHEN" dla MUNICH, "KOELN" dla COLOGNE-BONN).
ALIAS = {
    "LKPR": ("PRAGA", "PRAGUE"), "LKKB": ("PRAGA",), "LKLT": ("PRAGA",), "LZIB": ("BRATYSLAWA",),
    "LZKZ": ("KOSZYCE",), "UMKK": ("KROLEWIEC",), "EYVI": ("WILNO",), "EYKA": ("KOWNO",), "EYKS": ("KOWNO",),
    "UMBB": ("BRZESC",), "UMMG": ("GRODNO",), "UMGG": ("GOMEL",), "UMII": ("WITEBSK", "VITEBSK"),
    "UMOO": ("MOHYLEW", "MOGILEV"), "UMIO": ("ORSZA",), "UMMS": ("MINSK",),
    "UKLL": ("LWOW", "LVOV", "LEMBERG"), "UKMC": ("LWOW",), "UKLI": ("STANISLAWOW",), "UKLR": ("ROWNE",),
    "UKLU": ("UZHOROD", "UZHGOROD"), "UKLN": ("CZERNIOWCE",), "UKLT": ("TARNOPOL",), "UKLC": ("LUCK",),
    "UKLK": ("LUCK",), "UKLH": ("CHMIELNICKI",),
    "EDDM": ("MUENCHEN", "MONACHIUM"), "EDDK": ("KOELN", "KOLONIA"), "EDDN": ("NORYMBERGA",),
    "EDDP": ("LIPSK",), "EDDC": ("DREZNO",), "EDDV": ("HANOWER",), "EDDW": ("BREMA",),
    "ESSA": ("SZTOKHOLM",), "ESSB": ("SZTOKHOLM",), "ESGG": ("GOTHENBURG",),
}

# Główne lotnisko miasta: przy kilku trafieniach (np. "warszawa": EPBC, EPMO, EPWA) Enter otwiera to (rank 0).
# Pozostałe lotniska ze stanowiskami ATC w danych mają rank 1, reszta (VFR) rank 2.
MAIN = frozenset((
    "EPWA", "EPKK", "EPGD", "EPKT", "EPWR", "EPPO", "EPRZ", "EPSC", "EPLL", "EPLB", "EPBY", "EPZG", "EPSY",
    "ESSA", "ESGG", "ESMS", "ESPA", "ESNU",
    "EDDB", "EDDF", "EDDM", "EDDH", "EDDK", "EDDL", "EDDS", "EDDN", "EDDP", "EDDC", "EDDV", "EDDW", "EDDE",
    "EDDG", "EDDR",
    "LKPR", "LKTB", "LKMT", "LKKV", "LZIB", "LZKZ", "LZTT", "UMKK", "EYVI", "EYKA", "EYPA", "UMMS", "UKLL",
))

# lotniska wojskowe spoza EP i ET (EP: airports.csv "Military"/"Air Base", ET: wszystkie)
MIL = frozenset(("ESCF", "ESCM", "ESDF", "ESIB", "ESPE", "LKCV", "LKNA", "LKKB", "LZSL", "LZMC", "EYSA"))

# Nie lotniska, tylko lądowiska śmigłowców przy szpitalach i platformy na morzu (Szwecja ma ich kilkadziesiąt):
# pomijane na listach (otworzyć je można dalej, wpisując ICAO)
_HELI = re.compile(r"SJUKHUS|LASARETT|HELIKOPTER|KLINIK|KRANKENHAUS|HOSPITAL|HELIPORT|KRIEGERS FLAK|MEERWIND")
HELI = frozenset(("ESHG", "ESEG"))   # STOCKHOLM/GAMLA STAN, GALLIVARE/VASSARA

# Lądowiska z listy PANDORY (zdjęcie od Marka, 12.10.2026), których nie ma w navdata ani w airports.csv
EP_EXTRA = {"EPGB": "GOLUBIE", "EPGN": "GLINIANY LAS", "EPGO": "GORASZKA", "EPGW": "ULIM"}
# Lądowiska ze stanowiskami w niemieckich plikach .ese, których nie ma w navdata (bez współrzędnych): nazwa ze
# stanowiska (EDBW_I_TWR "Werneuchen Radio", EDHP_I_TWR "Pellworm Radio" w pakiecie EDWW), żeby lista ED miała wszystkie
ED_EXTRA = {"EDBW": "WERNEUCHEN", "EDHP": "PELLWORM"}

# lotniska z samego airports.csv: bez słów ogólnych ("Airfield", "Military Air Base")
_GENERIC = re.compile(r"\b(INTERNATIONAL|AIRPORT|AIRFIELD|AIRSTRIP|GLIDER FIELD|FIELD|MILITARY|NAVAL|AIR BASE|PRIVATE)\b")
_MIL_CSV = re.compile(r"\b(MILITARY|AIR BASE|NAVAL)\b")
_MIL_NAV = re.compile(r"(\bAB$|\bAIR BASE\b|\bAFB\b|\bMIL\b)")


def fold(text: str | None) -> str:
    """WIELKIE LITERY bez znaków diakrytycznych (Ł, Ø, ß też), pojedyncze spacje: "Łódź" → "LODZ".

    icao.txt jest w Latin-1, a importer czyta go jako cp1250 (jak pliki sektorowe): szwedzkie Å/å (0xC5/0xE5)
    przychodzą jako Ĺ/ĺ ("BORĹS" = BORÅS), więc tu wracają do A."""
    s = str(text or "").translate(str.maketrans({"Ĺ": "A", "ĺ": "a"}))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "đ": "d", "Đ": "D", "ı": "i",
                                   "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", s.upper()).strip()


def csv_name(name: str | None) -> str:
    """Nazwa z airports.csv do listy: "Grudziądz Lisie Kąty Airfield" → "GRUDZIADZ LISIE KATY"."""
    s = _GENERIC.sub(" ", fold(name))
    return re.sub(r"\s+", " ", s).strip(" -/") or fold(name)


def display_name(icao: str, nav_name: str | None, csv: str | None = None) -> str:
    """Nazwa z navdata (jak w PANDORZE), bez niej z airports.csv; dla części lotnisk z miastem z przodu."""
    name = fold(nav_name) if nav_name and nav_name.strip() else csv_name(csv) if csv else ""
    city = CITY.get(icao)
    if city and name and city not in name:
        name = f"{city} {name}"
    return name


def _ring_ok(ring) -> bool:
    return isinstance(ring, list) and len(ring) >= 4


def uklv_polygons() -> list[list]:
    """Zewnętrzne pierścienie granicy FIR UKLV z VATSpy ([] = granica nieużywalna)."""
    try:
        feats = fir_boundaries().get("features", [])
    except (OSError, ValueError):
        return []
    f = next((x for x in feats if x.get("properties", {}).get("id") == "UKLV"), None)
    geom = (f or {}).get("geometry") or {}
    coords = geom.get("coordinates") or []
    rings = [coords[0]] if geom.get("type") == "Polygon" and coords else \
        [p[0] for p in coords if p] if geom.get("type") == "MultiPolygon" else []
    return [r for r in rings if _ring_ok(r)]


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[j][:2]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
        j = i
    return inside


def fir_key(icao: str, lat: float | None = None, lon: float | None = None, uklv: list[list] | None = None) -> str | None:
    """Podzakładka AERODROME, do której należy lotnisko (None = żadna)."""
    i = (icao or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", i):
        return None
    for pre, key in _PREFIX_FIR:
        if i.startswith(pre):
            return key
    if i.startswith("UK"):
        rings = uklv_polygons() if uklv is None else uklv
        if rings and lat is not None and lon is not None:
            return "UKLV" if any(point_in_ring(lon, lat, r) for r in rings) else None
        return "UKLV" if i.startswith("UKL") else None
    return None


def kind_of(icao: str, nav_name: str | None, csv: Aerodrome | None) -> str | None:
    """MIL dla lotnisk wojskowych (ET*, znane bazy w innych FIR-ach, w airports.csv "Military"/"Air Base",
    w navdata "... AB"), inaczej None."""
    if icao.startswith("ET") or icao in MIL:
        return "MIL"
    if csv is not None and _MIL_CSV.search(fold(csv.name)):
        return "MIL"
    if nav_name and _MIL_NAV.search(fold(nav_name)):
        return "MIL"
    return None


def is_heliport(icao: str, name: str | None) -> bool:
    return icao in HELI or bool(_HELI.search(fold(name)))


def entry(icao: str, name: str, kind: str | None, lat, lon, atc: set[str] | frozenset = frozenset()) -> dict:
    """Wiersz listy: rank 0 = główne lotnisko miasta, 1 = ze stanowiskami ATC, 2 = pozostałe; alias = inne nazwy."""
    return {"icao": icao, "name": name, "kind": kind, "rank": 0 if icao in MAIN else 1 if icao in atc else 2,
            "alias": list(ALIAS.get(icao, ())), "lat": lat, "lon": lon}


def build_lists(nav: list[tuple[str, str | None, float, float]], csv: list[Aerodrome],
                uklv: list[list] | None = None, atc: set[str] | frozenset = frozenset()) -> dict[str, list[dict]]:
    """Wszystkie listy naraz: {klucz FIR: [{icao, name, kind, rank, alias, lat, lon}]} po ICAO, bez powtórzeń
    i bez lądowisk śmigłowców. atc: lotniska, które mają stanowiska w danych (do rank)."""
    rings = uklv_polygons() if uklv is None else uklv
    by_csv = {a.icao.upper(): a for a in csv}
    seen: dict[str, dict] = {}
    for ident, name, lat, lon in nav:
        icao = (ident or "").strip().upper()
        if icao in seen or not fir_key(icao, lat, lon, rings):
            continue
        a = by_csv.get(icao)
        seen[icao] = entry(icao, display_name(icao, name, a.name if a else None), kind_of(icao, name, a), lat, lon, atc)
    for icao, a in by_csv.items():   # lotniska tylko z airports.csv (EPGM, EPKB, EPNA…)
        if icao not in seen and fir_key(icao, a.lat, a.lon, rings):
            seen[icao] = entry(icao, display_name(icao, None, a.name), kind_of(icao, None, a), a.lat, a.lon, atc)
    for icao, name in (EP_EXTRA | ED_EXTRA).items():
        seen.setdefault(icao, entry(icao, name, None, None, None, atc))
    out: dict[str, list[dict]] = {k: [] for k in FIR_KEYS}
    for icao in sorted(seen):
        if not is_heliport(icao, seen[icao]["name"]):
            out[fir_key(icao, seen[icao]["lat"], seen[icao]["lon"], rings)].append(seen[icao])
    return out


_cache: dict = {"sig": None, "t": 0.0, "lists": None}
_TTL = 3600


def fir_lists(db: Session) -> dict[str, list[dict]]:
    """Listy z bazy, w pamięci do zmiany liczby lotnisk (ponowny import) albo godzinę."""
    sig = (db.scalar(select(func.count()).select_from(NavPoint).where(NavPoint.kind == "AD", NavPoint.source == "icao")),
           db.scalar(select(func.count()).select_from(Aerodrome)))
    if _cache["lists"] is None or _cache["sig"] != sig or time.monotonic() - _cache["t"] > _TTL:
        nav = db.execute(select(NavPoint.ident, NavPoint.name, NavPoint.lat, NavPoint.lon)
                         .where(NavPoint.kind == "AD", NavPoint.source == "icao")).all()
        _cache.update(sig=sig, t=time.monotonic(),
                      lists=build_lists(nav, list(db.scalars(select(Aerodrome))),
                                        atc=atc_airports(all_positions(db, airports=True))))
    return _cache["lists"]


def airport_entry(db: Session, icao: str) -> dict | None:
    """Lotnisko z list FIR-ów, a spoza nich z navdata albo airports.csv (lądowiska śmigłowców, inne FIR-y)."""
    icao = icao.upper()
    for lst in fir_lists(db).values():
        hit = next((a for a in lst if a["icao"] == icao), None)
        if hit:
            return hit
    nav = db.execute(select(NavPoint.name, NavPoint.lat, NavPoint.lon)
                     .where(NavPoint.kind == "AD", NavPoint.ident == icao).limit(1)).first()
    a = db.get(Aerodrome, icao)
    if nav:
        return entry(icao, display_name(icao, nav.name, a.name if a else None), kind_of(icao, nav.name, a), nav.lat, nav.lon)
    if a:
        return entry(icao, display_name(icao, None, a.name), kind_of(icao, None, a), a.lat, a.lon)
    return None


def atc_airports(positions: list[dict]) -> set[str]:
    """Lotniska, które mają w danych stanowisko APP, TWR, GND, DEL albo ATIS (np. EDDB_N_TWR → EDDB). Stanowiska
    informacji ("Radio", "Information": EDHF_I_TWR, EPBC_I_TWR) to nie ATC: w plikach niemieckich ma je ponad 400
    lądowisk i przy wyszukiwaniu wypierałyby lotniska z wieżą ("wolf" → EDVE, nie EDHF). Karty lotnisk je pokazują."""
    out = set()
    for p in positions:
        parts = (p.get("callsign") or "").upper().split("_")
        if _type("_".join(parts), parts[0]) and not INFO_MID & set(parts[1:-1]):
            out.add(parts[0])
    return out


# --- stanowiska lotniska: APP, TWR, GND, DEL, ATIS ----------------------------------------------------------------

TYPE_ORDER = ("APP", "TWR", "GND", "DEL", "ATIS")


class _Pos:
    __slots__ = ("callsign", "prefix", "frequency")

    def __init__(self, callsign: str, prefix: str | None, frequency: str):
        self.callsign, self.prefix, self.frequency = callsign, prefix, frequency


def _type(callsign: str, icao: str) -> str | None:
    parts = (callsign or "").upper().split("_")
    if len(parts) < 2 or parts[0] != icao:
        return None
    return FACILITY_TYPES.get(parts[-1])   # DEP → APP, RMP → GND jak na plakietkach lotnisk


def airport_positions(icao: str, known: list[dict], controllers: list[dict]) -> list[dict]:
    """Stanowiska lotniska w kolejności APP, TWR, GND, DEL, ATIS (w grupie alfabetycznie) ze stanem online.

    known: stanowiska z danych (plik .ese EPWW, vacs-data, pliki .ese sąsiadów); controllers: data feed VATSIM
    (kontrolerzy i ATIS). Zalogowany pod znakiem spoza danych (np. EDDB_ATIS) dostaje własny wiersz.
    Dopasowanie jak w EuroScope (match_positions): dokładny znak, potem prefiks, typ i częstotliwość."""
    icao = icao.upper()
    rows: dict[str, dict] = {}
    for p in known:
        cs = (p.get("callsign") or "").upper()
        t = _type(cs, icao)
        if t and cs not in rows:
            rows[cs] = {"type": t, "callsign": cs, "name": p.get("name") or None, "frequency": p.get("frequency") or "",
                        "known": True, "online": None}
    mine = [c for c in controllers if _type(c.get("callsign", ""), icao)]
    online = match_positions(mine, [_Pos(r["callsign"], icao, r["frequency"]) for r in rows.values()])
    taken = {v["callsign"] for v in online.values()}
    for cs, info in online.items():
        rows[cs]["online"] = info
    for c in mine:
        cs = c.get("callsign", "").upper()
        if cs in taken or cs in rows:
            continue
        rows[cs] = {"type": _type(cs, icao), "callsign": cs, "name": None, "frequency": c.get("frequency") or "",
                    "known": False, "online": controller_info(c)}
    return sorted(rows.values(), key=lambda r: (TYPE_ORDER.index(r["type"]), r["callsign"]))
