"""Prosty parser METAR wystarczający dla widoku AWOS (wiatr, widzialność, chmury, QNH, temperatura)."""

import re
from dataclasses import asdict, dataclass, field

WIND_RE = re.compile(r"^(?P<dir>\d{3}|VRB)(?P<spd>\d{2,3})(?:G(?P<gust>\d{2,3}))?(?P<unit>KT|MPS)$")
VAR_RE = re.compile(r"^(?P<from>\d{3})V(?P<to>\d{3})$")
VIS_RE = re.compile(r"^(?P<vis>\d{4})(?P<ndv>NDV)?$")
RVR_RE = re.compile(r"^R(?P<rwy>\d{2}[LCR]?)/(?P<val>[PM]?\d{4}(?:V[PM]?\d{4})?)(?:FT)?(?P<trend>[UDN])?$")
CLOUD_RE = re.compile(r"^(?P<cov>FEW|SCT|BKN|OVC|VV)(?P<hgt>\d{3}|///)(?P<type>CB|TCU|///)?$")
TEMP_RE = re.compile(r"^(?P<t>M?\d{2})/(?P<d>M?\d{2})?$")
QNH_RE = re.compile(r"^(?P<u>[QA])(?P<v>\d{4})$")
# zjawisko z opadem albo samo TS / SH (burza bez opadu "TS", w pobliżu "VCTS", "VCSH")
WX_RE = re.compile(
    r"^(?:[+-]|VC)?(?:(?:MI|PR|BC|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)+"
    r"|TS|SH)$"
)


@dataclass
class Metar:
    raw: str
    station: str | None = None
    time: str | None = None
    wind_dir: int | None = None
    wind_variable: bool = False
    wind_speed: int | None = None
    wind_gust: int | None = None
    wind_var_from: int | None = None
    wind_var_to: int | None = None
    visibility_m: int | None = None
    cavok: bool = False
    rvr: list[dict] = field(default_factory=list)
    weather: list[str] = field(default_factory=list)
    clouds: list[dict] = field(default_factory=list)
    temperature: int | None = None
    dewpoint: int | None = None
    qnh: int | None = None
    trend: str | None = None
    ceiling_ft: int | None = None
    flight_category: str | None = None

    def to_dict(self):
        return asdict(self)


def _temp(v: str | None) -> int | None:
    if not v:
        return None
    return -int(v[1:]) if v.startswith("M") else int(v)


def parse_metar(raw: str) -> Metar:
    raw = " ".join(raw.split())
    m = Metar(raw=raw)
    tokens = raw.split(" ")
    if tokens and tokens[0] in ("METAR", "SPECI"):
        tokens = tokens[1:]
    for i, tok in enumerate(tokens):
        if tok in ("BECMG", "TEMPO", "NOSIG", "RMK"):
            m.trend = " ".join(tokens[i:])
            break
        if m.station is None and re.fullmatch(r"[A-Z]{4}", tok):
            m.station = tok
            continue
        if m.time is None and re.fullmatch(r"\d{6}Z", tok):
            m.time = tok
            continue
        if (w := WIND_RE.match(tok)) and m.wind_speed is None:
            factor = 1.94384 if w["unit"] == "MPS" else 1
            m.wind_variable = w["dir"] == "VRB"
            m.wind_dir = None if m.wind_variable else int(w["dir"])
            m.wind_speed = round(int(w["spd"]) * factor)
            m.wind_gust = round(int(w["gust"]) * factor) if w["gust"] else None
            continue
        if v := VAR_RE.match(tok):
            m.wind_var_from, m.wind_var_to = int(v["from"]), int(v["to"])
            continue
        if tok == "CAVOK":
            m.cavok, m.visibility_m = True, 9999
            continue
        if (v := VIS_RE.match(tok)) and m.visibility_m is None:
            m.visibility_m = int(v["vis"])
            continue
        if r := RVR_RE.match(tok):
            m.rvr.append({"runway": r["rwy"], "value": r["val"], "trend": r["trend"]})
            continue
        if c := CLOUD_RE.match(tok):
            hgt = None if c["hgt"] == "///" else int(c["hgt"]) * 100
            m.clouds.append({"cover": c["cov"], "base_ft": hgt, "type": c["type"] if c["type"] != "///" else None})
            continue
        if tok in ("NSC", "NCD", "SKC", "CLR"):
            continue
        if t := TEMP_RE.match(tok):
            m.temperature, m.dewpoint = _temp(t["t"]), _temp(t["d"])
            continue
        if q := QNH_RE.match(tok):
            m.qnh = int(q["v"]) if q["u"] == "Q" else round(int(q["v"]) / 100 * 33.8639)
            continue
        if WX_RE.match(tok):
            m.weather.append(tok)

    ceilings = [c["base_ft"] for c in m.clouds if c["cover"] in ("BKN", "OVC", "VV") and c["base_ft"] is not None]
    m.ceiling_ft = min(ceilings) if ceilings else None
    m.flight_category = flight_category(m.visibility_m, m.ceiling_ft)
    return m


def flight_category(vis: int | None, ceiling: int | None) -> str | None:
    if vis is None and ceiling is None:
        return None
    vis = 9999 if vis is None else vis
    ceiling = 99999 if ceiling is None else ceiling
    if vis < 1500 or ceiling < 500:
        return "LIFR"
    if vis < 5000 or ceiling < 1000:
        return "IFR"
    if vis < 8000 or ceiling < 3000:
        return "MVFR"
    return "VFR"


def qfe_from_qnh(qnh: int, elevation_ft: float) -> int:
    """Przybliżenie: ~27 ft na 1 hPa przy powierzchni."""
    return round(qnh - elevation_ft / 27.0)
