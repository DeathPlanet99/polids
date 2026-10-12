"""AERODROME › RUCH jako DMAN/AMAN i zakładka STRIP: rejestracja, punkt wejścia, ETA/ELDT, ELDT z vIFF,
endpoint /strips?dman=1 oraz czyste funkcje frontendu (sekwencja AMAN, LND/SEP, rozkładanie etykiet) w Node."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("POLIDS_DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.config import ROOT_DIR  # noqa: E402
from backend.app.routers import aerodromes  # noqa: E402
from backend.app.services import dman, viff  # noqa: E402
from backend.app.services.http_cache import UpstreamError  # noqa: E402
from backend.app.services.procedures import parse_procedures, strip_board  # noqa: E402
from backend.app.services.vatsim import airport_traffic  # noqa: E402

FRONTEND = ROOT_DIR / "frontend"
NOW = datetime(2026, 10, 12, 12, 0, 30, tzinfo=timezone.utc)
ESE = "\n".join([
    "STAR:EPWA:33:BIMPA6UxILS\xb7Z:BIMPA KEWLU MENCI WA534 WA529",
    "STAR:EPWA:33:LOGDA6UxRNP:LOGDA WA821 EMCEL WA529",
    "STAR:EPWA:11:SORIX7NxRNP:SORIX WA401 WA409",
    "STAR:EPWA:33:ILS\xb7Z:WA534 WA529",
    "SID:EPWA:29:SOXER7G:WA901 WA902 SOXER",
])
PROCS = parse_procedures(ESE)


# --- services/dman.py ------------------------------------------------------------------------------------------

def test_registration_from_remarks():
    assert dman.registration("PBN/A1B1C1D1O1S1 DOF/261006 REG/SPLVA /V/") == "SPLVA"
    assert dman.registration("reg/d-aino RMK/TCAS") == "D-AINO"
    assert dman.registration("PBN/A1 REG/OKGFR") == "OKGFR"
    assert dman.registration("/V/ TRAINING") is None and dman.registration(None) is None
    assert dman.registration("XREG/SPABC") is None   # tylko pole REG/, nie fragment innego słowa


def test_iafs_are_star_first_fixes():
    assert dman.iafs("EPWA", PROCS) == ["BIMPA", "LOGDA", "SORIX"]
    assert dman.iafs("EPKK", PROCS) == []
    # z prawdziwego pliku .ese: sześć punktów wejścia EPWA jak przyciski w oknie AMAN dla ACC
    assert dman.iafs("EPWA") == ["AGAVA", "BIMPA", "LIMVI", "LOGDA", "NEPOX", "SORIX"]


def test_entry_fix_sources():
    proc = {"name": "BIMPA6U", "fixes": ["BIMPA", "KEWLU"], "fix": "BIMPA"}
    assert dman.entry_fix("EPWA", "SOBRA Y180 RUDNO DCT BIMPA", proc, PROCS) == {"fix": "BIMPA", "source": "STAR"}
    # nazwa STAR w trasie (także dla innego pasa) i punkt wejścia przed dopiskiem po ostatnim punkcie
    assert dman.entry_fix("EPWA", "ODINA L603 LOGDA LOGDA6U", None, PROCS) == {"fix": "LOGDA", "source": "STAR"}
    assert dman.entry_fix("EPWA", "ARTIP UL980 SORIX DCT WA401", None, PROCS) == {"fix": "SORIX", "source": "IAF"}
    # bez punktu wejścia lotniska: ostatni punkt trasy
    assert dman.entry_fix("EPWA", "OKG DCT POLON T355 RUDKA", None, PROCS) == {"fix": "RUDKA", "source": "route"}
    assert dman.entry_fix("EPWA", "N0450F370 DCT EPWA", None, PROCS) is None
    assert dman.entry_fix("EPWA", None, None, PROCS) is None


def test_eta_model():
    # ostatnie 10 NM 140 kt, 30–10 NM najwyżej 220 kt, dalej GS
    assert dman.eta_seconds(10, 140) == round(10 / 140 * 3600)
    assert dman.eta_seconds(5, 300) == round(5 / 140 * 3600)
    assert dman.eta_seconds(30, 300) == round((20 / 220 + 10 / 140) * 3600)
    assert dman.eta_seconds(130, 450) == round((100 / 450 + 20 / 220 + 10 / 140) * 3600)
    # wolny samolot leci swoją prędkością (GS < 140 kt)
    assert dman.eta_seconds(20, 100) == round(20 / 100 * 3600)
    assert dman.eta_seconds(50, 40) is None and dman.eta_seconds(None, 300) is None and dman.eta_seconds(50, None) is None


def test_rel_seconds_and_choose_eldt():
    assert dman.rel_seconds("1210", NOW) == 9 * 60 + 30
    assert dman.rel_seconds("1159", NOW) == -90
    late = datetime(2026, 10, 12, 23, 55, tzinfo=timezone.utc)
    assert dman.rel_seconds("0010", late) == 15 * 60   # po północy
    assert dman.rel_seconds("", NOW) is None and dman.rel_seconds(None, NOW) is None
    # vIFF wygrywa, gdy blisko ETA z pozycji; odległy o > 20 min albo w przeszłości: ETA z pozycji
    assert dman.choose_eldt(600, "1210", NOW) == (570, "vIFF")
    assert dman.choose_eldt(600, "1240", NOW) == (600, "POS")
    assert dman.choose_eldt(600, "1155", NOW) == (600, "POS")
    # na ziemi na lotnisku odlotu (bez ETA z pozycji): sam vIFF
    assert dman.choose_eldt(None, "1305", NOW) == (64 * 60 + 30, "vIFF")
    assert dman.choose_eldt(None, None, NOW) == (None, None)


def _pilot(cs, dep, arr, route, lat=52.166, lon=20.967, gs=0, alt=360, **fp):
    return {"callsign": cs, "latitude": lat, "longitude": lon, "groundspeed": gs, "altitude": alt, "transponder": "2000",
            "flight_plan": {"aircraft_short": "A320", "departure": dep, "arrival": arr, "route": route, "altitude": "36000",
                            "flight_rules": "I", **fp}}


FEED = {"general": {"update_timestamp": "x"}, "controllers": [], "atis": [{
    "callsign": "EPWA_ATIS", "frequency": "120.455", "atis_code": "K",
    "text_atis": ["WARSAW CHOPIN INFORMATION K", "RUNWAY IN USE FOR LANDING 33, DEPARTURE RUNWAY 29"]}],
    "pilots": [
        _pilot("GATE1", "EPWA", "EDDF", "SOXER UL29", deptime="1210", remarks="PBN/A1 REG/SPLVA /V/"),
        _pilot("INB1", "EDDF", "EPWA", "SOBRA Y180 RUDNO DCT BIMPA", gs=300, alt=12000, lat=52.3, lon=20.0,
               remarks="REG/DAINO"),
        _pilot("INB2", "EHAM", "EPWA", "ARTIP UL980 SORIX", gs=420, alt=24000, lat=53.0, lon=19.5),
        _pilot("ATADEP", "EPKK", "EPWA", "DCT LIMVI", lat=50.08, lon=19.78),
        _pilot("LANDED", "EGLL", "EPWA", "DCT BIMPA", gs=12),
    ], "prefiles": []}


def test_annotate_board():
    traffic = airport_traffic(FEED, "EPWA", 52.166, 20.967)
    board = strip_board("EPWA", traffic, None, {"arr": "33", "dep": "29", "source": "ATIS"}, {}, NOW, PROCS)
    dman.annotate(board, "EPWA", NOW, {"INB2": "1215", "ATADEP": "1300"}, PROCS)
    deps = {s["callsign"]: s for s in board["departures"]}
    arrs = {s["callsign"]: s for s in board["arrivals"]}
    assert deps["GATE1"]["registration"] == "SPLVA" and "eta_s" not in deps["GATE1"]
    inb1 = arrs["INB1"]
    assert inb1["registration"] == "DAINO" and inb1["entry_fix"] == {"fix": "BIMPA", "source": "STAR"}
    assert inb1["eldt_source"] == "POS" and inb1["eldt_s"] == inb1["eta_s"] == dman.eta_seconds(inb1["dist_nm"], 300)
    assert inb1["eldt"] == (NOW + timedelta(seconds=inb1["eldt_s"])).strftime("%H%M")
    # INB2: ELDT z vIFF tylko, jeśli zgadza się z pozycją (inaczej ETA z pozycji); ATADEP: na ziemi w EPKK, sam vIFF
    inb2 = arrs["INB2"]
    assert inb2["viff_eta"] == "1215" and inb2["entry_fix"]["fix"] == "SORIX"
    assert inb2["eldt_source"] == ("vIFF" if abs(dman.rel_seconds("1215", NOW) - inb2["eta_s"]) <= 1200 else "POS")
    assert arrs["ATADEP"]["eta_s"] is None and (arrs["ATADEP"]["eldt"], arrs["ATADEP"]["eldt_source"]) == ("1300", "vIFF")
    # po lądowaniu: bez ELDT
    assert (arrs["LANDED"]["eldt_s"], arrs["LANDED"]["eldt"]) == (None, None)


# --- vIFF: ELDT przylotów i ustawienia A-CDM lotniska ------------------------------------------------------------

def test_arrival_etas(monkeypatch):
    calls = []

    async def fake_get(path, params=None, ttl=None):
        cs = params["callsign"]
        calls.append(cs)
        if cs == "NOTKNOWN":
            raise UpstreamError("404")
        return {"OK1": {"callsign": "OK1", "arrival": "EPWA", "eta": "1215", "landed": False},
                "OK2": [{"callsign": "X"}, {"callsign": "OK2", "arrival": "EPWA", "eta": "121500"}],
                "OTHERAD": {"callsign": "OTHERAD", "arrival": "EPKK", "eta": "1230"},
                "LANDED": {"callsign": "LANDED", "arrival": "EPWA", "eta": "1150", "landed": True},
                "NOETA": {"callsign": "NOETA", "arrival": "EPWA", "eta": ""}}[cs]

    monkeypatch.setattr(viff, "_get", fake_get)
    monkeypatch.setattr(viff, "_eta_miss", {})
    names = ["OK1", "OK2", "OTHERAD", "LANDED", "NOETA", "NOTKNOWN", "ok1"]
    got = asyncio.run(viff.arrival_etas("epwa", names))
    assert got == {"OK1": "1215", "OK2": "1215"}
    assert sorted(calls) == sorted(set(c.upper() for c in names))   # bez powtórzeń
    # loty bez ELDT nie są pytane ponownie (vIFF nie jest odpytywany co 30 s o nieznane loty)
    calls.clear()
    assert asyncio.run(viff.arrival_etas("EPWA", names)) == {"OK1": "1215", "OK2": "1215"}
    assert sorted(calls) == ["OK1", "OK2"]
    assert asyncio.run(viff.arrival_etas("EPWA", [])) == {}


def test_arrival_etas_wait_limit(monkeypatch):
    async def slow_get(path, params=None, ttl=None):
        await asyncio.sleep(0.5 if params["callsign"] == "SLOW" else 0)
        return {"callsign": params["callsign"], "arrival": "EPWA", "eta": "1300"}

    monkeypatch.setattr(viff, "_get", slow_get)
    monkeypatch.setattr(viff, "_eta_miss", {})

    async def run():
        fast = await viff.arrival_etas("EPWA", ["FAST", "SLOW"], wait=0.1)
        limited = await viff.arrival_etas("EPWA", [f"F{i}" for i in range(40)], limit=25)
        await asyncio.sleep(0.6)   # zapytanie w tle kończy się bez błędu
        return fast, limited

    fast, limited = asyncio.run(run())
    assert fast == {"FAST": "1300"} and len(limited) == 25


def test_cad_info(monkeypatch):
    async def fake_get(path, params=None, ttl=None):
        assert path == "/etfms/getCadAirports"
        return [{"icao": "EPWA", "rate": 34, "taxiTime": 15, "isCdm": True, "config": "11"}, {"icao": "EPKK", "rate": -1}]

    monkeypatch.setattr(viff, "_get", fake_get)
    monkeypatch.setattr(viff, "_cad", {"failed": 0.0, "task": None})
    assert asyncio.run(viff.cad_info("epwa")) == {"rate": 34, "taxi": 15}
    assert asyncio.run(viff.cad_info("EPKK")) == {"rate": None, "taxi": None}
    assert asyncio.run(viff.cad_info("EPGD")) is None

    calls = []

    async def down(path, params=None, ttl=None):
        calls.append(path)
        raise UpstreamError("timeout")

    monkeypatch.setattr(viff, "_get", down)
    assert asyncio.run(viff.cad_info("EPWA")) is None
    # po błędzie vIFF nie jest pytany ponownie przez CAD_MISS_S (każde odświeżenie RUCH nie czeka na timeout)
    assert asyncio.run(viff.cad_info("EPWA")) is None and calls == ["/etfms/getCadAirports"]


def test_cad_info_wait_limit(monkeypatch):
    import time

    state = {"delay": 0.5}

    async def slow_get(path, params=None, ttl=None):
        await asyncio.sleep(state["delay"])
        return [{"icao": "EPWA", "rate": 30, "taxiTime": 12}]

    monkeypatch.setattr(viff, "_get", slow_get)
    monkeypatch.setattr(viff, "_cad", {"failed": 0.0, "task": None})

    async def run():
        t0 = time.monotonic()
        first = await viff.cad_info("EPWA", wait=0.1)          # nie czeka dłużej niż wait
        took = time.monotonic() - t0
        skipped = await viff.cad_info("EPWA", wait=0.1)        # przekroczenie czasu = przerwa w pytaniu
        await asyncio.sleep(0.6)                               # zapytanie w tle kończy się i kasuje przerwę
        state["delay"] = 0
        return first, took, skipped, await viff.cad_info("EPWA", wait=0.1)

    first, took, skipped, later = asyncio.run(run())
    assert first is None and took < 0.4 and skipped is None
    assert later == {"rate": 30, "taxi": 12}


def test_viff_departure_taxi():
    f = viff.departure({"callsign": "X", "eobt": "1200", "taxi": 15, "cdmData": {"tsat": "121000"}})
    assert f["taxi"] == 15 and f["tsat"] == "1210"
    assert viff.departure({"callsign": "X", "taxi": 0})["taxi"] is None


# --- GET /api/aerodromes/{icao}/strips?dman=1 ---------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def feed():
        return FEED

    async def metars(icaos):
        return {i: f"{i} 121200Z 31010KT 9999 FEW030 12/05 Q1012" for i in icaos}

    async def tafs(icaos):
        return {}

    asked = []
    # ELDT z vIFF za 40 min od teraz (stała godzina zależałaby od pory uruchomienia testów)
    eta = (datetime.now(timezone.utc) + timedelta(minutes=40)).strftime("%H%M")

    async def fake_get(path, params=None, ttl=None):
        if path == "/ifps/depAirport":
            return [{"callsign": "GATE1", "arrival": "EDDF", "eobt": "1210", "taxi": 12, "cdmData": {"tsat": "121500"}}]
        if path == "/etfms/getCadAirport":
            return {"isCdm": params["icao"] == "EPWA"}
        if path == "/etfms/getCadAirports":
            return [{"icao": "EPWA", "rate": 34, "taxiTime": 15, "isCdm": True}]
        if path == "/ifps/callsign":
            asked.append(params["callsign"])
            return {"callsign": params["callsign"], "arrival": "EPWA", "eta": eta} if params["callsign"] == "ATADEP" else None
        raise AssertionError(path)

    monkeypatch.setattr(aerodromes, "get_feed", feed)
    monkeypatch.setattr(aerodromes, "get_metars", metars)
    monkeypatch.setattr(aerodromes, "get_tafs", tafs)
    monkeypatch.setattr(viff, "_get", fake_get)
    monkeypatch.setattr(viff, "_eta_miss", {})
    monkeypatch.setattr(viff, "_cad", {"failed": 0.0, "task": None})
    with TestClient(main.app) as c:
        c.asked, c.eta = asked, eta
        yield c


def test_strips_endpoint_dman(client):
    plain = client.get("/api/aerodromes/EPWA/strips").json()
    # zgodność wstecz: dotychczasowe pola zostają, nowe są dopisane; bez dman=1 nie pytamy vIFF o każdy przylot
    for k in ("icao", "name", "now", "runway_in_use", "atis", "cdm", "states", "procedures", "departures", "arrivals",
              "local", "planned", "network_error", "viff_error"):
        assert k in plain
    assert client.asked == [] and plain["cad"] is None and plain["iafs"][:2] == ["AGAVA", "BIMPA"]
    assert abs(plain["now_ts"] - datetime.now(timezone.utc).timestamp()) < 60
    arr = {s["callsign"]: s for s in plain["arrivals"]}
    assert arr["INB1"]["registration"] == "DAINO" and arr["INB1"]["entry_fix"]["fix"] == "BIMPA"
    assert arr["INB1"]["eldt_source"] == "POS" and arr["ATADEP"]["eldt"] is None
    gate = plain["departures"][0]
    assert (gate["callsign"], gate["registration"], gate["viff"]["taxi"], gate["viff"]["tsat"]) == ("GATE1", "SPLVA", 12, "1215")

    d = client.get("/api/aerodromes/EPWA/strips?dman=1").json()
    assert d["cad"] == {"rate": 34, "taxi": 15}
    # pytamy o przyloty w powietrzu i na ziemi na lotnisku odlotu, nie o te po lądowaniu
    assert sorted(client.asked) == ["ATADEP", "INB1", "INB2"]
    arr = {s["callsign"]: s for s in d["arrivals"]}
    assert (arr["ATADEP"]["eldt"], arr["ATADEP"]["eldt_source"]) == (client.eta, "vIFF")
    assert arr["LANDED"]["eldt"] is None and arr["INB2"]["eldt_source"] == "POS"
    assert client.get("/api/aerodromes/XXXX/strips?dman=1").status_code == 404
    # A-CDM z vIFF (isCdm): EPWA tak (drabiny TSAT i TTOT), EPKK nie (EOBT i ETOT)
    assert d["cdm"] is True and client.get("/api/aerodromes/EPKK/strips?dman=1").json()["cdm"] is False


def test_strips_dman_not_slower_when_viff_down(client, monkeypatch):
    """vIFF nie odpowiada (każde zapytanie po czasie kończy się błędem): ?dman=1 nie może czekać dłużej niż zwykłe paski
    (ustawienia A-CDM równolegle z odlotami, bez pytania o ELDT każdego przylotu po błędzie vIFF)."""
    import time

    calls = []

    async def dead_get(path, params=None, ttl=None):
        calls.append(path)
        await asyncio.sleep(0.6)
        raise UpstreamError("api.viffsys.com: timeout")

    monkeypatch.setattr(viff, "_get", dead_get)
    t0 = time.monotonic()
    plain = client.get("/api/aerodromes/EPWA/strips").json()
    t_plain = time.monotonic() - t0
    t0 = time.monotonic()
    d = client.get("/api/aerodromes/EPWA/strips?dman=1").json()
    t_dman = time.monotonic() - t0
    assert plain["viff_error"] and d["viff_error"] and d["cad"] is None
    assert "/ifps/callsign" not in calls
    assert t_dman < t_plain + 0.35, (t_plain, t_dman)
    # kolejne odświeżenie: ustawienia A-CDM nie są pytane ponownie po błędzie
    calls.clear()
    client.get("/api/aerodromes/EPWA/strips?dman=1")
    assert "/etfms/getCadAirports" not in calls


# --- frontend -----------------------------------------------------------------------------------------------------

NODE_SCRIPT = r"""
const core = await import(process.argv[2]);
const out = {};
// sekwencja: odstęp FLOW/140 kt na pas, kolejność wg ETA, osobno dla każdego pasa
const seq = core.sequence([
  { key: "A", runway: "33", eta: 1000 }, { key: "B", runway: "33", eta: 1010 }, { key: "C", runway: "33", eta: 2000 },
  { key: "D", runway: "29", eta: 1005 }, { key: "E", runway: "33", eta: null }], (r) => (r === "33" ? 7 : 3.5));
out.seq = Object.fromEntries([...seq].map(([k, v]) => [k, { sta: Math.round(v.sta), delay: Math.round(v.delay), order: v.order, runway: v.runway }]));
out.lnd = core.lndSep([100, 280, 460, 1900, 3000, 4000], 0);
out.lndNone = core.lndSep([], 0);
// etykiety: bez nakładania, kolejność zachowana, w granicach
const items = [{ y: 100, h: 17 }, { y: 104, h: 17 }, { y: 106, h: 17 }, { y: 300, h: 17 }, { y: 2, h: 17 }, { y: 400, h: 17 }];
const tops = core.stack(items, 0, 410, 2);
out.tops = tops;
const order = items.map((_, i) => i).sort((a, b) => items[a].y - items[b].y);
out.noOverlap = order.every((id, k) => k === 0 || tops[id] >= tops[order[k - 1]] + 17 + 2 - 1e-9);
out.inBounds = tops.every((t) => t >= 0 && t + 17 <= 410 + 1e-9);
out.untouched = tops[3] === 300;
// czas "HHMM" względem teraz, także przez północ
const at = (h, m, s = 0) => Date.UTC(2026, 9, 12, h, m, s);
out.rel = [core.relSec("1210", at(12, 0, 30)), core.relSec("0005", at(23, 55)), core.relSec("2355", at(0, 5)), core.relSec("", at(1, 0))];
out.clock = [core.clock(at(9, 53, 41)), core.clock(at(9, 53, 41), false), core.hm("1015"), core.hm(null)];
// odloty: TSAT / TOBT / EOBT, TTOT / CTOT / ETOT, spóźniony bez AOBT
const now = at(12, 0);
const t1 = core.depTimes({ viff: { tsat: "1150", tobt: "1145", ttot: "1205" }, state: "ground", groundspeed: 0 }, now);
const t2 = core.depTimes({ viff: { tsat: "1150", aobt: "1151" }, state: "ground", groundspeed: 0 }, now, 10);
const t3 = core.depTimes({ eobt: "1230", viff: null, state: "ground" }, now, 12);
const t4 = core.depTimes({ viff: { tsat: "1150", ctot: "1220" }, state: "ground", groundspeed: 8 }, now);
const t5 = core.depTimes({ viff: { tsat: "1150", atot: "1158" }, state: "air" }, now);
out.dep = [t1, t2, t3, t4, t5].map((t) => [t.tsatSrc, t.tsat, t.ttotSrc, t.ttot, t.late]);
out.etot = [t1, t2, t3, t5].map((t) => [t.etotBase, t.etot, t.offBlock]);
// lotnisko bez A-CDM: sam EOBT i ETOT (TOBT, TSAT, TTOT i CTOT z vIFF nie przesuwają etykiet), po starcie ATOT
const n1 = core.depTimes({ eobt: "1230", viff: { tsat: "1150", tobt: "1145", ttot: "1205", ctot: "1250" }, state: "ground", groundspeed: 0 }, now, 10, false);
const n2 = core.depTimes({ eobt: "1150", viff: { aobt: "1155", ctot: "1220" }, state: "ground", groundspeed: 6 }, now, 10, false);
const n3 = core.depTimes({ eobt: "1140", viff: { aobt: "1142", atot: "1155" }, state: "air" }, now, 10, false);
const n4 = core.depTimes({ eobt: null, viff: { tobt: "1210" }, state: "ground" }, now, 10, false);
out.nocdm = [n1, n2, n3, n4].map((t) => [t.tsatSrc, t.tsat, t.ttotSrc, t.ttot, t.etotBase, t.late]);
out.labels = [core.depLabels(true), core.depLabels(false), core.depLabels(undefined)];
// za dużo etykiet na wysokość: odpadają najpóźniejsze
out.over = [...core.overflow([{ t: 5, h: 10 }, { t: 1, h: 10 }, { t: 3, h: 10 }], 21, 1)];
out.overNone = core.overflow([{ t: 5, h: 10 }, { t: 1, h: 10 }], 21, 1).size;
// kolory punktów wejścia spoza listy lotniska: stałe dla nazwy, bez kolorów punktów STAR lotniska
const iafC = core.fixColors("EPGD", ["ABC", "DEF"]);
out.fixc = [core.fixColor("VAMPU", iafC), core.fixColor("VAMPU", iafC), core.fixColor("ABC", iafC) === iafC.ABC,
  core.fixColor(null, iafC) === core.OTHER_COLOR, Object.values(iafC).includes(core.fixColor("VAMPU", iafC)),
  core.EXTRA_COLORS.includes(core.fixColor("RUDKA", {}))];
out.colors = [core.fixColors("EPWA", ["AGAVA", "BIMPA", "LIMVI", "LOGDA", "NEPOX", "SORIX"]).SORIX, core.fixColors("EPKK", ["ABC", "DEF"]),
  core.fixOrder("EPWA", ["AGAVA", "BIMPA", "LIMVI", "LOGDA", "NEPOX", "SORIX"])];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("dmanjs")
    (d / "core.mjs").write_text((FRONTEND / "js" / "dmancore.js").read_text("utf-8"), "utf-8")
    (d / "run.mjs").write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(d / "run.mjs"), (d / "core.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_aman_sequence(js):
    gap33 = 7 / 140 * 3600   # 180 s
    s = js["seq"]
    assert s["A"] == {"sta": 1000, "delay": 0, "order": 1, "runway": "33"}
    assert s["B"] == {"sta": round(1000 + gap33), "delay": round(1000 + gap33 - 1010), "order": 2, "runway": "33"}
    assert s["C"] == {"sta": 2000, "delay": 0, "order": 3, "runway": "33"}   # daleko za poprzednim: bez opóźnienia
    assert s["D"] == {"sta": 1005, "delay": 0, "order": 1, "runway": "29"}   # inny pas, osobna kolejka
    assert "E" not in s                                                    # bez ETA: poza sekwencją


def test_lnd_sep(js):
    w60, w30, w10 = js["lnd"]
    assert (w60["window"], w60["lnd"], w30["lnd"], w10["lnd"]) == (60, 5, 3, 3)   # 4000 s jest już poza godziną
    # 10 i 30 min: lądowania 100, 280, 460 s → odstępy po 180 s = 7.0 NM przy 140 kt
    assert w10["sep"] == w30["sep"] == 7.0
    assert w60["sep"] == round((180 + 180 + 1440 + 1100) / 4 * 140 / 3600, 1)
    assert js["lndNone"][0] == {"window": 60, "lnd": 0, "sep": None}


def test_stack_labels(js):
    assert js["noOverlap"] and js["inBounds"] and js["untouched"]


def test_time_helpers_and_departure_times(js):
    assert js["rel"] == [570, 600, -600, None]
    assert js["clock"] == ["09:53:41", "09:53", "10:15", "–"]
    t1, t2, t3, t4, t5 = js["dep"]
    assert t1 == ["TSAT", -600, "TTOT", 300, True]          # TSAT minął 10 min temu, brak AOBT: spóźniony
    assert t2 == ["TSAT", -600, "ETOT", -540 + 600, False]  # po AOBT: ETOT = AOBT + kołowanie, nie spóźniony
    assert t3 == ["EOBT", 1800, "ETOT", 1800 + 720, False]  # bez vIFF: EOBT z planu lotu
    assert t4[2:] == ["CTOT", 1200, False]                  # kołuje (GS ≥ 3 kt): nie spóźniony
    assert t5[2:4] == ["ATOT", -120]


def test_departure_times_without_cdm(js):
    # bez A-CDM drabiny EOBT i ETOT: TOBT/TSAT/TTOT/CTOT z vIFF nie przesuwają etykiet, nigdy "spóźniony" (to pojęcie TSAT)
    n1, n2, n3, n4 = js["nocdm"]
    assert n1 == ["EOBT", 1800, "ETOT", 1800 + 600, "EOBT", False]
    assert n2 == ["EOBT", -600, "ETOT", -300 + 600, "AOBT", False]   # po AOBT: ETOT = AOBT + kołowanie, mimo CTOT
    assert n3[2:4] == ["ATOT", -300]                                  # po starcie: rzeczywisty start
    assert n4 == [None, None, None, None, None, False]                # bez EOBT nie ma czego pokazać (TOBT pominięty)
    assert js["labels"] == [{"obt": "TSAT", "tot": "TTOT"}, {"obt": "EOBT", "tot": "ETOT"}, {"obt": "EOBT", "tot": "ETOT"}]


def test_etot_and_overflow(js):
    # ETOT = (AOBT albo TSAT/TOBT/EOBT) + kołowanie: ta sama wartość w tabeli i na drabinie TTOT
    assert js["etot"] == [["TSAT", 0, False], ["AOBT", 60, True], ["EOBT", 1800 + 720, False], ["TSAT", 0, True]]
    assert js["over"] == [0] and js["overNone"] == 0


def test_fix_colors(js):
    epwa, other, order = js["colors"]
    assert epwa == "#d8902c" and set(other) == {"ABC", "DEF"} and other["ABC"] != other["DEF"]
    assert order == ["SORIX", "NEPOX", "BIMPA", "LIMVI", "AGAVA", "LOGDA"]   # jak przyciski w oknie ACC
    vampu, again, known, other, clash, extra = js["fixc"]
    assert vampu == again and vampu.startswith("#") and known and other and not clash and extra


def test_strip_tab_and_ruch_wiring():
    strip = (FRONTEND / "js" / "tabs" / "strip.js").read_text("utf-8")
    airports = json.loads(re.search(r"STRIP_AIRPORTS = (\[[^\]]+\])", strip).group(1))
    assert airports == sorted(airports) and len(airports) == 15 and "EPWA" in airports
    assert "subtabs(" in strip and "mountStrips(" in strip and 'remember("strip"' in strip and 'lsGet("strip.icao")' in strip
    dm = (FRONTEND / "js" / "dman.js").read_text("utf-8")
    assert "export function mountDman(" in dm and "strips?dman=1" in dm and 'navigate("map"' in dm
    # STRIP i AERODROME › RUCH: paski EFES tylko w zakładce STRIP; PASKI tylko dla lotnisk z podzakładką w STRIP
    assert "import { mountStrips" not in dm and "mountStrips(" not in dm
    assert "STRIP_AIRPORTS.includes(icao)" in dm and "Paski tylko dla 15 głównych lotnisk" in dm
    # widok DMAN/AMAN ↔ AMAN w adresie AERODROME (#aerodrome/EPWA/aman): kliknięcie = krok do cofnięcia
    ad = (FRONTEND / "js" / "tabs" / "aerodrome.js").read_text("utf-8")
    assert "onView?.(v, {})" in dm and "onView?.(st.view, { replace: true })" in dm and "setView(v) {" in dm
    assert "state = `${code}/${vv}`" in ad and "strips.setView(rv)" in ad
    # zgłoszenie widoku przy montowaniu i resume() (replace) nie zapisuje adresu: robią to goto()/activate() po zmianie
    # ekranu; remember z replace tutaj podmieniał wpis PRZEGLĄDU/AWOS, z którego wchodzi się do RUCH (← go pomijało)
    assert "if (!o?.replace) remember(\"aerodrome\", state);" in ad and "remember(\"aerodrome\", state, o)" not in ad
    assert "DMAN/AMAN ›" in ad
    # nieznany typ samolotu: "–", nie "null"
    assert 'R("AC TYPE", acType(s))' in dm and 'R("AC TYPE", s.aircraft' not in dm
    # lotnisko bez A-CDM: nazwy drabin, kolumny i sortowania z depLabels(data.cdm), czasy z depTimes(…, cdm)
    assert "depLabels(cdm())" in dm and "depTimes(s, now, taxiDefault(), cdm())" in dm
    assert ">${L.obt}</text>" in dm and ">${L.tot}</text>" in dm and 'sortBtns("dep", L.obt)' in dm
    assert ">TSAT</text>" not in dm and ">TTOT</text>" not in dm and 'sortBtns("dep", "TSAT")' not in dm
    # bez A-CDM bez pustych wierszy TOBT/TSAT/TTOT w szczegółach lotu; CTOT zawsze widoczny
    assert 'opt(v.tsat, "TSAT"' in dm and 'opt(v.ttot, "TTOT"' in dm and 'R("CTOT", hm(v.ctot)' in dm
    for css in ("dman.css", "strips.css"):
        assert f"css/{css}" in (FRONTEND / "index.html").read_text("utf-8")
