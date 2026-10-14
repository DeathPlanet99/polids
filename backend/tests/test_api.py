import os
import tempfile

os.environ["POLIDS_DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.routers import aerodromes, meteo  # noqa: E402

METAR = "EPWA 051630Z 28015KT 9999 FEW030 12/05 Q1012 NOSIG"


@pytest.fixture(scope="module")
def client():
    async def fake_metars(icaos):
        return {i: METAR.replace("EPWA", i) for i in icaos}

    async def fake_tafs(icaos):
        return {i: f"TAF {i} 051100Z 0512/0618 28012KT 9999 FEW030" for i in icaos}

    for mod in (aerodromes, meteo):
        mod.get_metars, mod.get_tafs = fake_metars, fake_tafs

    async def fake_feed():
        return {"general": {}, "controllers": [{"callsign": "EPWA_TWR", "frequency": "118.305", "name": "Ola", "cid": 3}],
                "atis": [{"callsign": "EPWA_ATIS", "frequency": "120.455", "atis_code": "B",
                          "text_atis": ["WARSAW CHOPIN INFORMATION B", "RWY 33 FOR LANDING 29 FOR TAKEOFF"]}],
                "pilots": [], "prefiles": []}
    aerodromes.get_feed = fake_feed
    with TestClient(main.app) as c:
        yield c


def test_frontend_and_config(client):
    assert settings.name in client.get("/").text
    cfg = client.get("/api/config").json()
    assert cfg["links"]["inop"].startswith("https://om.plvacc.pl")
    assert "learningzone.eurocontrol.int" in cfg["links"]["phraseology"] and "carto_api_key" in cfg
    assert "inop_hide_footer_px" not in cfg


def test_aerodrome_status(client):
    st = client.get("/api/aerodromes/EPWA/status").json()
    assert st["parsed"]["qnh"] == 1012
    # AIP AD 2.21: doloty 33, odloty 29 (wiatr 280/15 w limicie bocznego 15 kt)
    assert st["suggested_runway"] == "33" and (st["preferred"]["arr"], st["preferred"]["dep"]) == ("33", "29")
    # kontrola online z ATIS: pas w użyciu z ATIS, litera B
    assert st["runway_in_use"] == {"arr": "33", "dep": "29", "source": "ATIS", "reason": "Z ATIS B"}
    # konfiguracja preferowana z runway_config.json (AWOS › 2 PASY przy ATIS z jednym pasem), AIP AD 2.21 H24
    assert st["runway_config"] == {"arr": "33", "dep": "29"}
    assert st["lvp"]["applies"] and len(st["lvp"]["rules"]) == 3
    assert st["atis"]["letter"] == "B" and st["atc_online"][0]["callsign"] == "EPWA_TWR"
    assert "ILS" in next(r for r in st["runways"] if r["designator"] == "33")["equipment"]
    chk = client.get("/api/aerodromes/EPWA/checklist").json()
    assert chk["id"] == "open-position" and chk["items"]
    assert client.get("/api/aerodromes/XXXX/status").status_code == 404


def test_seed_from_aip(client):
    """Import danych z data/seed: pasy w metrach ze źródłem, częstotliwości z godzinami, elewacje z AIP."""
    bk = client.get("/api/aerodromes/EPBK").json()
    rw = {r["designator"]: r for r in bk["runways"]}
    assert set(rw) == {"09R", "27L", "09L", "27R"}
    assert (rw["09R"]["length_m"], rw["09R"]["width_m"], rw["09R"]["surface"]) == (1350, 50, "CONC/ASPH")
    assert (rw["27R"]["length_m"], rw["27R"]["surface"]) == (840, "GRASS")
    assert bk["frequencies"][0] == {"kind": "RADIO", "description": "BIAŁYSTOK RADIO", "mhz": "123.205", "hours": "AUP",
                                    "note": None, "source": "AIP VFR AD 4.9"}
    ra = client.get("/api/aerodromes/EPRA").json()["frequencies"]
    assert [f["note"] for f in ra if f["kind"] == "TWR"] == ["TWR cywilna", "TWR wojskowa (MIL)"]
    # EPSW: pas 07/25 z OurAirports nie istnieje
    assert not any(r["designator"] in ("07", "25") for r in client.get("/api/aerodromes/EPSW").json()["runways"])
    mo = client.get("/api/aerodromes/EPMO").json()
    assert mo["elevation_ft"] == 344 and {r["length_m"] for r in mo["runways"]} == {2500}
    ks = client.get("/api/aerodromes/EPKS").json()
    assert any(f["kind"] == "GND" and f["hours"].startswith("MON-THU") for f in ks["frequencies"])
    # lotnisko bez LVP w AIP
    st = client.get("/api/aerodromes/EPRA/status").json()
    assert st["lvp"]["applies"] is False and st["lvp"]["state"] is None


def test_status_cancelled_lvp_runway_choice_imc(client, monkeypatch):
    """Po odwołaniu LVP (CANCELLED) pas wybierany jak przy IMC z kategorii METAR, nie jak w LVP."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    states = []
    for ago, wx in ((20, "0400 R26/0450N FG VV001"), (5, "1200 BR BKN003")):
        metar = f"EPKT {now - timedelta(minutes=ago):%d%H%MZ} 08004KT {wx} 08/08 Q1012"

        async def fake_metars(icaos, metar=metar):
            return {i: metar for i in icaos}
        monkeypatch.setattr(aerodromes, "get_metars", fake_metars)
        st = client.get("/api/aerodromes/EPKT/status").json()
        states.append((st["lvp"]["state"], st["preferred"]["arr"], st["preferred"]["reason"].split(":")[0]))
    assert states == [("LVP", "26", "LVP w mocy"), ("CANCELLED", "26", "Warunki IMC")]


def test_meteo_and_qnh(client):
    m = client.get("/api/meteo/metar?ids=EPWA,EPKK").json()
    assert m[1]["parsed"]["station"] == "EPKK"
    q = client.get("/api/meteo/qnh-regions").json()
    assert all(r["qnh"] == 1012 for r in q["regions"])
    assert len(q["regions"]) == 17 and q["regions"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_aircraft_and_callsigns(client):
    a = client.get("/api/aircraft?q=B738").json()
    assert a and a[0]["recat"] == "D" and a[0]["wingspan"] > 30
    assert client.get("/api/callsigns?q=LOT").json()[0]["icao"] == "LOT"
    mil = client.get("/api/callsigns?category=MIL&q=PLF").json()
    assert mil and mil[0]["category"] == "MIL"


def test_route_without_navdata(client):
    r = client.get("/api/nav/route?route=EPWA DCT EPKK").json()
    assert [p["ident"] for p in r["points"]] == ["EPWA", "EPKK"] and 120 < r["distance_nm"] < 140


def test_checklists_and_emergency(client):
    ids = [c["id"] for c in client.get("/api/checklists").json()["checklists"]]
    assert ids == ["open-position", "close-position", "handover-takeover", "rwy-change"]
    procs = client.get("/api/emergency").json()["procedures"]
    assert len(procs) == 18 and procs[1]["id"] == "A06" and any(p["id"] == "RCF" for p in procs)


def test_docs_read_only(client):
    assert client.get("/api/docs").status_code == 200
    assert client.post("/api/docs").status_code == 405


def test_vatsim_endpoints(client):
    from backend.app.routers import vatsim

    async def feed():
        return {"general": {}, "controllers": [{"callsign": "EPWA_APP", "frequency": "128.805", "name": "Jan", "cid": 1}],
                "atis": [], "pilots": [{"callsign": "LOT1", "latitude": 52.1, "longitude": 20.9, "heading": 90,
                                        "flight_plan": {"departure": "EPWA", "arrival": "EPKK", "route": "DCT"}}]}
    vatsim.get_feed = feed
    on = client.get("/api/vatsim/online").json()
    assert on["positions"]["EPWA_APP"]["name"] == "Jan"
    assert client.get("/api/vatsim/pilots?bbox=50,15,55,25").json()[0]["callsign"] == "LOT1"
    r = client.get("/api/vatsim/pilots/LOT1/route").json()
    assert [p["ident"] for p in r["points"]] == ["EPWA", "EPKK"]
    assert any(f["properties"]["id"] == "EPWW" for f in client.get("/api/vatsim/firs").json()["features"])
    ad = client.get("/api/vatsim/airport/EPWA").json()
    assert [p["callsign"] for p in ad["departures"]] == ["LOT1"] and ad["arrivals"] == []


def test_vatsim_atc_badges(client):
    from backend.app.routers import vatsim

    async def feed():
        return {"general": {}, "pilots": [],
                "controllers": [{"callsign": "EPWA_APP", "frequency": "128.805", "name": "Jan", "cid": 1},
                                {"callsign": "EPWA_GND", "frequency": "121.905", "name": "Ola", "cid": 2},
                                {"callsign": "XXX_TWR", "frequency": "118.000", "name": "?", "cid": 3},
                                {"callsign": "EDWW_FLG_CTR", "frequency": "136.450", "name": "Max", "cid": 4}],
                "atis": [{"callsign": "EPWA_ATIS", "frequency": "120.455", "atis_code": "K", "text_atis": ["INFO K"]}]}
    vatsim.get_feed = feed
    r = client.get("/api/vatsim/atc").json()
    assert [a["icao"] for a in r["airports"]] == ["EPWA"]  # XXX nie ma w bazie lotnisk
    ep = r["airports"][0]
    assert sorted(ep["facilities"]) == ["APP", "ATIS", "GND"] and ep["lat"] > 52
    assert ep["facilities"]["ATIS"][0]["atis_code"] == "K"
    assert r["firs"]["EDWW-FLG"][0]["name"] == "Max"


def test_airspace_lines_and_embed_check(client):
    tma = client.get("/api/nav/airspace?kind=tma").json()
    names = {f["properties"]["name"] for f in tma["features"]}
    assert "EPWA TMA OUT" in names and all("TMA" in n for n in names)
    ctr = client.get("/api/nav/airspace?kind=ctr").json()
    assert ctr["features"] and ctr["features"][0]["geometry"]["type"] == "MultiLineString"
    assert client.get("/api/nav/airspace?kind=xyz").status_code == 422
    assert client.get("/api/embed-check/nieznany").status_code == 404


def test_viff_endpoints(client, monkeypatch):
    from backend.app.services import viff
    from backend.app.services.http_cache import UpstreamError

    flight = {"callsign": "LOT3827", "arrival": "EPGD", "eobt": "1520", "ctot": "1545", "atfcmStatus": "SAM",
              "cdmSts": "", "aobt": "", "atot": "", "atfcmData": {"mostPenalisingRegulation": "EPWWB15"},
              "cdmData": {"tobt": "152000", "tsat": "153000", "ttot": "154500", "depInfo": "29/LIMVI2G"}}

    async def fake_get(path, params=None, ttl=None):
        return {"/ifps/depAirport": [flight], "/etfms/getCadAirport": {"isCdm": True},
                "/etfms/trafficVolumes": [], "/etfms/airspaces": {}}[path]

    monkeypatch.setattr(viff, "_get", fake_get)
    d = client.get("/api/viff/departures/epwa").json()
    assert d["cdm"] is True and d["flights"][0]["state"] == "SI"
    assert (d["flights"][0]["tsat"], d["flights"][0]["regulation"]) == ("1530", "EPWWB15")
    assert client.get("/api/viff/departures/EPWA1").status_code == 400
    assert client.get("/api/viff/sectors").json()["sectors"] == []

    async def down(path, params=None, ttl=None):
        raise UpstreamError("timeout")

    monkeypatch.setattr(viff, "_get", down)
    r = client.get("/api/viff/departures/EPWA")
    assert r.status_code == 502 and "vIFF" in r.json()["detail"]


def test_vacs_sector_chains(client):
    r = client.get("/api/nav/vacs")
    assert r.status_code == 200
    d = r.json()
    assert set(d["layers"]) == {"LOW", "MID", "HIGH"}
    assert sorted(d["sectors"]) == list("BCDEFGJNRT")
    assert d["sectors"]["B"]["HIGH"][0] == "EPWW_N_CTR"
    assert all(cs in d["acc_positions"] for s in d["sectors"].values() for chain in s.values() for cs in chain)
    esmm6 = next(n for n in d["neighbours"] if n["label"] == "ESMM 6")
    assert esmm6["alt"] == "+365" and esmm6["callsign"] == "ESMM_6_CTR"


def test_bad_input_is_400_or_422_not_500(client):
    for ids in ("../../etc/passwd", "EPWA,\x00", "EP WA'--", "A" * 5):
        assert client.get("/api/meteo/metar", params={"ids": ids}).status_code == 400, ids
        assert client.get("/api/meteo/taf", params={"ids": ids}).status_code == 400, ids
    r = client.get("/api/meteo/metar", params={"ids": "epwa,,EPWA, EPKK"})
    assert [m["icao"] for m in r.json()] == ["EPWA", "EPKK"]
    # wcześniej 500 (overflow INTEGER w SQLite), limit=-1 w SQLite = bez limitu
    huge = "99999999999999999999"
    for url in (f"/api/aircraft/{huge}", f"/api/aircraft/{huge}/photo", "/api/aircraft/0",
                f"/api/nav/points?bbox=49,14,55,24&limit={huge}", "/api/nav/points?bbox=49,14,55,24&limit=-1",
                "/api/nav/airways?bbox=49,14,55,24&limit=-1", f"/api/nav/sectors?level_ft={huge}",
                "/api/aircraft?limit=-1", "/api/callsigns?limit=0"):
        assert client.get(url).status_code == 422, url


def test_nav_sectors_note_only_without_file(client):
    # FIR bez sektorów z pliku EPWW (EDGG ma tylko sektory z własnego .ese): pusta lista bez "wygeneruj plik"
    assert client.get("/api/nav/sectors", params={"fir": "EPWW"}).json()["features"]
    edgg = client.get("/api/nav/sectors", params={"fir": "EDGG"}).json()
    assert edgg["features"] == [] and "note" not in edgg


def test_many_concurrent_users_do_not_deadlock(client, monkeypatch):
    # regresja: przy limicie puli 5+10 >15 równoczesnych zapytań blokowało event loop (deadlock)
    import asyncio

    import httpx

    from backend.app.database import engine

    assert engine.pool._max_overflow == -1

    async def slow_metars(icaos):
        await asyncio.sleep(0.3)
        return {i: METAR.replace("EPWA", i) for i in icaos}
    monkeypatch.setattr(aerodromes, "get_metars", slow_metars)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            rs = await asyncio.wait_for(asyncio.gather(*(c.get("/api/aerodromes/EPWA/status") for _ in range(40))), 30)
        return [r.status_code for r in rs]

    assert asyncio.run(run()) == [200] * 40
def test_ownership_chains(client):
    d = client.get("/api/nav/ownership").json()
    assert d["source"] in ("ese", "om")
    assert sorted(d["sectors"]) == list("BCDEFGJNRT")
    assert all(set(s) == {"LOW", "MID", "HIGH"} for s in d["sectors"].values())
    assert all(cs in d["acc_positions"] for s in d["sectors"].values() for chain in s.values() for cs in chain)
