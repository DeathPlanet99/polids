"""RADIO jako jedna strona (runda 10): GET /api/radio/board, część stała z radioboard.py i czyste funkcje
frontend/js/radioboard.js (Node)."""
import json
import os
import re
import shutil
import subprocess
import tempfile

os.environ.setdefault("POLIDS_DATABASE_URL",
                      "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.config import ROOT_DIR  # noqa: E402
from backend.app.routers import vatsim as vatsim_api  # noqa: E402
from backend.app.services import radioboard as rb  # noqa: E402
from backend.app.services.http_cache import UpstreamError  # noqa: E402

FRONTEND = ROOT_DIR / "frontend"
FEED = {"general": {"update_timestamp": "2026-10-12T12:00:00Z"}, "pilots": [],
        "controllers": [
            {"callsign": "EPWW_N_CTR", "frequency": "130.675", "name": "Radar Północ", "cid": 11},
            {"callsign": "EPWA_APP", "frequency": "128.805", "name": "Okęcie", "cid": 12},
            {"callsign": "EDWW_FLG_CTR", "frequency": "136.450", "name": "Bremen", "cid": 13},
            {"callsign": "EPKK_OBS", "frequency": "199.998", "name": "Obserwator", "cid": 14}],
        "atis": [
            {"callsign": "EPWA_ATIS", "frequency": "120.455", "name": "Atis", "cid": 15, "atis_code": "K",
             "text_atis": ["WARSAW INFORMATION K"]},
            {"callsign": "EPWA_D_ATIS", "frequency": "121.130", "name": "Odloty", "cid": 16, "atis_code": "D"}]}
ONLINE = {"EPWW_N_CTR", "EPWA_APP", "EDWW_FLG_CTR"}


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def client(monkeypatch_module):
    async def feed():
        return FEED

    monkeypatch_module.setattr(vatsim_api, "get_feed", feed)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def board(client):
    r = client.get("/api/radio/board")
    assert r.status_code == 200
    return r.json()


def test_board_sectors_and_owners(board):
    own = json.loads((ROOT_DIR / "data" / "seed" / "ownership.json").read_text("utf-8"))
    expected = {ly[0] + letter for letter, by in own["sectors"].items() for ly in ("LOW", "MID", "HIGH") if by.get(ly)}
    ids = [s["id"] for s in board["sectors"]]
    assert set(ids) == expected and len(ids) == len(expected) == 30
    assert all(re.fullmatch(r"[LMH][A-Z]", i) for i in ids)  # litera warstwy + litera sektora, bez numerów
    for s in board["sectors"]:
        assert s["nominal"] == s["chain"][0] and s["layer"][0] + s["letter"] == s["id"]
        # właściciel = pierwsze stanowisko kolejności, które jest online; nikt → null (strona: samo ID sektora)
        assert s["owner"] == next((cs for cs in s["chain"] if cs in ONLINE), None)
    by = {s["id"]: s for s in board["sectors"]}
    assert by["HB"]["owner"] == "EPWW_N_CTR"
    assert any(s["owner"] is None for s in board["sectors"])
    assert board["layers"]["LOW"]["lower_ft"] < board["layers"]["MID"]["lower_ft"] < board["layers"]["HIGH"]["lower_ft"]
    # częstotliwości wszystkich stanowisk z kolejności są w pos
    assert all(cs in board["pos"] for s in board["sectors"] for cs in s["chain"])
    assert board["pos"]["EPWW_N_CTR"]["frequency"] == "130.675"
    assert board["net"] == {"ok": True, "error": None, "updated": "2026-10-12T12:00:00Z"}
    assert "geo" not in board


def test_board_overlays(board):
    tma = {g["id"]: g for g in board["tma"]}
    wa = "TMA Warszawa · EPWA_APP"
    # TMA Warszawa: APP online jako właściciel lokalny; TMA bez nikogo z APP: top-down ACC tylko w polu topdown
    assert tma[wa]["nominal"] == "EPWA_APP" and tma[wa]["owners"] == ["EPWA_APP"]
    assert tma[wa]["topdown"] is None
    assert all(not any(o.startswith("EPWW_") for o in g["owners"]) for g in board["tma"] + board["twr"] + board["fis"])
    assert any(g["topdown"] == "EPWW_N_CTR" for g in board["tma"])
    assert all(g["at"] and 48 < g["at"][0] < 56 and 13 < g["at"][1] < 25 for g in board["tma"] + board["twr"] + board["fis"])
    twr = {g["icao"]: g for g in board["twr"]}
    assert twr["EPWA"]["nominal"].startswith("EPWA_") and twr["EPWA"]["nominal"].endswith("TWR")
    assert twr["EPWA"]["atis"] == "EPWA_ATIS" and twr["EPKK"]["atis"] == "EPKK_ATIS"
    assert board["fis"] and all("FIS" in g["id"] for g in board["fis"])


def test_board_tma_parts(board):
    """Warstwa APP (runda 11, Marek): TMA podzielone na części wg stanowiska części (WA APP, WA N APP, WA S APP),
    TMA proceduralne z wieżą lotniska, EPRZ domyślnie KK RZ APP; dyrektor (WA DIR, KK DIR, KT DIR) nie jest częścią."""
    tma = {g["id"]: g for g in board["tma"]}
    assert len(tma) == len(board["tma"])  # id bez powtórek
    parts = {}
    for g in board["tma"]:
        parts.setdefault(g["tma"], []).append(g["nominal"])
        assert g["id"] == (g["tma"] if len([x for x in board["tma"] if x["tma"] == g["tma"]]) == 1
                           else f'{g["tma"]} · {g["nominal"]}')
        # punkt etykiety w ramce części, ramka [lon0, lat0, lon1, lat1]
        lon0, lat0, lon1, lat1 = g["box"]
        assert lat0 <= g["at"][0] <= lat1 and lon0 <= g["at"][1] <= lon1, g["id"]
        assert g["lo"] < g["hi"] and g["chains"] and g["nominal"] in g["chains"][0]
    assert sorted(parts["TMA Warszawa"]) == ["EPWA_APP", "EPWA_N_APP", "EPWA_S_APP"]
    assert sorted(parts["TMA Kraków"]) == ["EPKK_APP", "EPKK_E_APP"]
    assert sorted(parts["TMA Gdańsk"]) == ["EPGD_APP", "EPGD_L_APP"]
    # każde APP z pliku ma swoją część albo jest w dymku części (dyrektor)
    noms = {g["nominal"] for g in board["tma"]}
    assert {"EPPO_N_APP", "EPPO_S_APP", "EPKK_RZ_APP", "EPDE_APP"} <= noms
    assert not noms & {"EPWA_F_APP", "EPKK_KK_APP", "EPKK_KT_APP"}
    assert "EPWA_F_APP" in tma["TMA Warszawa · EPWA_APP"]["apps"] and "EPKK_KT_APP" in tma["TMA Katowice · EPKK_APP"]["apps"]
    # TMA proceduralne: wieża lotniska, bez APP
    for key, cs in (("TMA Szczecin", "EPSC_TWR"), ("TMA Lublin", "EPLB_TWR"), ("TMA Olsztyn-Mazury", "EPSY_TWR")):
        assert tma[key]["nominal"] == cs and tma[key]["apps"] == [] and tma[key]["owners"] == [], key
    # EPRZ: KK RZ APP, a po APP Krakowa wieża Rzeszów (kolejność z .ese)
    rz = tma["TMA Rzeszów"]
    assert rz["nominal"] == "EPKK_RZ_APP" and rz["icao"] == "EPRZ"
    ch = rz["chains"][0]
    assert ch.index("EPKK_RZ_APP") < ch.index("EPKK_APP") < ch.index("EPRZ_TWR")
    # WA APP online przejmuje WA N i WA S (części) oraz TMA Łódź i Radom
    for key in ("TMA Warszawa · EPWA_N_APP", "TMA Warszawa · EPWA_S_APP", "TMA Łódź", "TMA Radom"):
        assert tma[key]["owners"] == ["EPWA_APP"], key


def test_board_tma_parts_online(client, monkeypatch):
    """Inny zestaw online: sam WA N APP, wieża TMA proceduralnej, EPRZ_TWR bez APP Krakowa,
    UMMV_CTR przejmuje kafelki UMMV N i W (VACS bez CTR w controlled_by, kolejność z .ese)."""
    feed = {**FEED, "controllers": [
        {"callsign": "EPWA_N_APP", "frequency": "125.055", "name": "N", "cid": 21},
        {"callsign": "EPSC_TWR", "frequency": "121.255", "name": "SC", "cid": 22},
        {"callsign": "EPRZ_TWR", "frequency": "126.805", "name": "RZ", "cid": 23},
        {"callsign": "UMMV_CTR", "frequency": "135.700", "name": "Minsk", "cid": 24}], "atis": []}

    async def other():
        return feed

    monkeypatch.setattr(vatsim_api, "get_feed", other)
    d = client.get("/api/radio/board").json()
    tma = {g["id"]: g for g in d["tma"]}
    # sam WA N APP przejmuje całą TMA Warszawa (WA APP › WA S › WA N w kolejności części WA APP)
    for part in ("EPWA_N_APP", "EPWA_APP", "EPWA_S_APP"):
        assert tma[f"TMA Warszawa · {part}"]["owners"] == ["EPWA_N_APP"], part
    assert tma["TMA Szczecin"]["owners"] == ["EPSC_TWR"]
    assert tma["TMA Rzeszów"]["owners"] == ["EPRZ_TWR"] and tma["TMA Rzeszów"]["nominal"] == "EPKK_RZ_APP"
    ummv = {t["label"]: t for s in d["stacks"] if s["fir"] == "UMMV" for t in s["tiles"]}
    assert {k: t["owner"] for k, t in ummv.items()} == dict.fromkeys(ummv, "UMMV_CTR")
    assert ummv["UMMV W"]["chain"][:2] == ["UMMV_W_CTR", "UMMV_CTR"]
    # za UMMV_CTR w całym stosie to samo, co .ese stawia za nim w UMMV N (RU-WRC_FSS)
    tails = {tuple(t["chain"][t["chain"].index("UMMV_CTR") + 1:]) for t in ummv.values()}
    assert len(tails) == 1 and tails != {()}, tails


def test_board_atis(board):
    atis = {a["callsign"]: a for a in board["atis"]}
    assert atis["EPWA_ATIS"]["online"] and not atis["EPKK_ATIS"]["online"]
    assert board["online"]["EPWA_ATIS"]["atis_code"] == "K"
    # zalogowany ATIS spoza pliku .ese też jest na pasku, z częstotliwością z sieci
    assert atis["EPWA_D_ATIS"]["extra"] and board["pos"]["EPWA_D_ATIS"]["frequency"] == "121.130"
    assert board["online"]["EPWA_D_ATIS"]["atis_code"] == "D"
    assert [a["icao"] for a in board["atis"]] == sorted(a["icao"] for a in board["atis"])
    assert "EPKK_OBS" not in board["online"]  # tylko stanowiska z tablicy


def test_board_stacks(board):
    """Stosy sąsiadów (runda 11): kafelki VACS profilu ACC_EPWW (sektory graniczące z EPWW) bez FMP; właściciel
    kafelka = pierwsze stanowisko online z controlled_by, nikt → null (strona: bez częstotliwości, ADVISORY)."""
    st = {s["fir"]: s for s in board["stacks"]}
    assert [s["fir"] for s in board["stacks"]] == [s["fir"] for s in rb.STACKS]
    assert {s["fir"] for s in board["stacks"] if s["map"] == "left"} == {"ESAA", "EDWW", "EDUU", "EDMM", "LKAA"}
    assert {s["fir"] for s in board["stacks"] if s["map"] == "right"} == {"UMKK", "EYVL", "UMMV", "UKLV", "LZBB"}
    vacs = json.loads((ROOT_DIR / "data" / "seed" / "vacs_epww.json").read_text("utf-8"))["neighbours"]
    tiles = [t for s in board["stacks"] for t in s["tiles"]]
    assert sorted(t["label"] for t in tiles) == sorted(n["label"] for n in vacs if not n["fmp"])
    assert not any("FMP" in t["label"] or "TMU" in t["label"] for t in tiles)
    for s in board["stacks"]:
        assert s["tiles"], s["fir"]
        keys = [(-(t["hi"] or rb.TOP_FT), -(t["lo"] or 0)) for t in s["tiles"]]
        assert keys == sorted(keys)  # od najwyższego zakresu
        for t in s["tiles"]:
            assert t["chain"] and t["callsign"] and set(t) == {"label", "alt", "lo", "hi", "callsign", "chain", "owner"}
            assert t["owner"] == next((cs for cs in t["chain"] if cs in ONLINE), None)
    labels = {f: [t["label"] for t in s["tiles"]] for f, s in st.items()}
    assert labels["EYVL"] == ["EYVL U", "EYVL M", "EYVL", "EYVL E"]
    assert labels["ESAA"] == ["ESMM 9", "ESMM 6", "ESMM 8", "ESMM 7", "ESMM L"]
    assert labels["LZBB"] == ["LZBB U", "LZBB", "LZTT TWR"]
    assert labels["EDWW"][:3] == ["EDWW MRZ", "EDWW MAR", "EDWW FLG"] and set(labels["EDWW"][3:]) == {
        "EDDB N DEP", "EDDB S DEP", "EDDB N APP", "EDDB S APP"}
    assert set(labels["UKLV"]) == {"UKLV", "UKR CTR", "UKLL APP", "UKLU TWR"} and labels["UKLV"][0] == "UKLV"
    assert labels["UMMV"][-1] == "UMBB TWR" and labels["UMKK"] == ["UMKK", "UMKK APP"]
    eyvl = {t["label"]: t for t in st["EYVL"]["tiles"]}
    assert (eyvl["EYVL U"]["lo"], eyvl["EYVL U"]["hi"], eyvl["EYVL U"]["alt"]) == (36500, rb.TOP_FT, "+365")
    assert (eyvl["EYVL M"]["lo"], eyvl["EYVL M"]["hi"]) == (33500, 36500) and eyvl["EYVL E"]["lo"] == 0
    # nikt z Wilna online: żaden kafelek EYVL nie ma właściciela (strona: ADVISORY, bez częstotliwości)
    assert all(t["owner"] is None for t in st["EYVL"]["tiles"] + st["LKAA"]["tiles"])
    edww = {t["label"]: t for t in st["EDWW"]["tiles"]}
    assert edww["EDWW FLG"]["owner"] == "EDWW_FLG_CTR" and edww["EDWW MRZ"]["owner"] is None
    assert edww["EDWW MRZ"]["alt"] is None and edww["EDWW MRZ"]["lo"] is None
    # EDUU HVL przejmuje EDWW FLG (jest w controlled_by), OSE nie
    eduu = {t["label"]: t for t in st["EDUU"]["tiles"]}
    assert eduu["EDUU HVL22"]["owner"] == "EDWW_FLG_CTR" and eduu["EDUU OSE22"]["owner"] is None
    # znaki z kolejności kafelków są w pos (nazwa, częstotliwość) i w online, gdy ktoś jest zalogowany
    assert all(cs in board["pos"] for t in tiles for cs in t["chain"])
    assert "EDWW_FLG_CTR" in board["online"]


def test_board_geo(client):
    d = client.get("/api/radio/board?geo=1").json()
    g = d["geo"]
    assert set(g) == {"border", "firs", "acc", "tma", "ctr", "fis"}
    assert g["border"]["type"] == "MultiPolygon" and g["border"]["coordinates"]
    assert {f["id"] for f in g["firs"]} >= {"EDWW", "EDMM", "LKAA", "LZBB", "UKLV", "UMMV", "EYVL", "UMKK", "ESAA"}
    assert all("-" not in f["id"] and f["id"] != "EPWW" for f in g["firs"])
    letters = {s["letter"] for s in d["sectors"]}
    assert {a["letter"] for a in g["acc"]} == letters
    assert all(a["lo"] < a["hi"] and a["geometry"]["coordinates"] for a in g["acc"])
    assert g["tma"] and g["ctr"] and g["fis"]
    assert len(json.dumps(g)) < 400_000  # raz na sesję, ale bez przesady
    # geometria zakodowana raz (część stała) i wklejona do odpowiedzi: poza nią ta sama treść co bez geo=1
    r = client.get("/api/radio/board?geo=1")
    assert r.headers["content-type"] == "application/json" and r.json()["geo"] == g
    assert set(d) - {"geo"} == set(client.get("/api/radio/board").json())


def test_board_version_and_reimport(client):
    """Import kasuje tabelę sektorów i wstawia ją od nowa (te same id): część stała i geometria muszą się odświeżyć,
    a strona dostaje nową wersję (`ver`) i nową geometrię przy `have=<stara wersja>`."""
    from sqlalchemy import delete, select

    from backend.app.database import SessionLocal
    from backend.app.models import Sector

    d1 = client.get("/api/radio/board").json()
    ver = d1["ver"]
    assert ver and "geo" not in d1
    assert "geo" not in client.get(f"/api/radio/board?have={ver}").json()  # ta sama wersja: bez geometrii
    with SessionLocal() as db:
        cols = [c.name for c in Sector.__table__.columns if c.name != "id"]
        rows = [{c: getattr(s, c) for c in cols} for s in db.scalars(select(Sector).order_by(Sector.id))]
        target = next(r for r in rows if r["fir"] == "EPWW" and r["name"].startswith("EPWWB"))
        old_geom = target["geometry"]
        try:
            db.execute(delete(Sector))
            for r in rows:
                db.add(Sector(**{**r, "geometry": '{"type":"Polygon","coordinates":[[[16,52],[17,52],[17,53],[16,52]]]}'
                                 if r is target else r["geometry"]}))
            db.commit()
            d2 = client.get(f"/api/radio/board?have={ver}").json()
            assert d2["ver"] != ver and "geo" in d2
            geo_b = [a["geometry"] for a in d2["geo"]["acc"] if a["name"] == target["name"]]
            assert geo_b and geo_b[0]["coordinates"][0][0][0] == [16, 52]
        finally:
            db.execute(delete(Sector))
            for r in rows:
                db.add(Sector(**r))
            db.commit()
    d3 = client.get(f"/api/radio/board?have={d2['ver']}").json()
    assert d3["ver"] == ver and "geo" in d3 and old_geom


def test_board_upstream_error(client, monkeypatch):
    async def broken():
        raise UpstreamError("timeout")

    monkeypatch.setattr(vatsim_api, "get_feed", broken)
    r = client.get("/api/radio/board")
    assert r.status_code == 200
    d = r.json()
    assert d["net"]["ok"] is False and "VATSIM" in d["net"]["error"]
    assert all(s["owner"] is None for s in d["sectors"]) and d["online"] == {}
    assert all(t["owner"] is None for s in d["stacks"] for t in s["tiles"])
    assert len(d["sectors"]) == 30 and all(a["online"] is False for a in d["atis"])


def test_existing_radio_endpoints_kept(client):
    for path in ("/api/radio/positions", "/api/radio/online", "/api/radio/sectors", "/api/radio/loa"):
        assert client.get(path).status_code == 200, path


def test_every_loa_has_a_stack(client, board):
    """Panel LOA otwiera się z nagłówka stosu sąsiada: każde LOA z loa.json ma swój stos na stronie RADIO
    (UMKK i UMMV bez LOA: panel mówi, że go nie ma)."""
    loa = client.get("/api/radio/loa").json()["firs"]
    stacks = {s["fir"] for s in board["stacks"]}
    assert set(loa) <= stacks and stacks - set(loa) == {"UMKK", "UMMV"}
    radio = (FRONTEND / "js" / "tabs" / "radio.js").read_text("utf-8")
    assert 'api("/api/radio/loa")' in radio and 'navigate("other", "docs/"' in radio


# --- czyste funkcje radioboard.py
OWN = {"tma_topdown": {"TMA Test": {"ese_prefixes": ["EPXX_TMA"], "chain": ["EPWW_A_CTR", "EPWW_B_CTR"]}}}


def test_air_chain():
    # lokalne przed pierwszym ACC, potem ACC z tabeli top-down (gdy prefiks pasuje), potem reszta z pliku
    assert rb.air_chain("EPXX_TMA_1", ["EPXX_APP", "EPWW_C_CTR", "EPYY_APP"], OWN) == \
        ["EPXX_APP", "EPWW_A_CTR", "EPWW_B_CTR", "EPYY_APP"]
    assert rb.air_chain("EPZZ_CTR", ["EPZZ_TWR", "EPZZ_APP", "EPWW_C_CTR", "EPWW_C_CTR"], OWN) == \
        ["EPZZ_TWR", "EPZZ_APP", "EPWW_C_CTR"]
    assert rb.air_chain("X", ["", "EPXX_TWR"], {}) == ["EPXX_TWR"]


def test_first_online_and_owners():
    on = {"EPWA_ATIS": {}, "EPWA_APP": {}, "EPWW_N_CTR": {}}
    assert rb.first_online(["EPWA_ATIS", "EPWA_TWR", "EPWA_APP"], on) == "EPWA_APP"  # ATIS nie przejmuje
    assert rb.first_online(["EPKK_APP"], on) is None
    chains = [["EPWW_N_CTR"], ["EPKK_APP", "EPWA_APP"], ["EPWA_APP"], ["EPKK_APP"]]
    assert rb.owners_of_chains(chains, on) == ["EPWA_APP", "EPWW_N_CTR"]


def test_acc_sectors():
    assert rb.acc_sectors({"sectors": {"B": {"LOW": ["EPWW_N_CTR"], "HIGH": ["EPWW_C_CTR", "EPWW_N_CTR"]}}}) == [
        {"id": "LB", "letter": "B", "layer": "LOW", "chain": ["EPWW_N_CTR"], "nominal": "EPWW_N_CTR"},
        {"id": "HB", "letter": "B", "layer": "HIGH", "chain": ["EPWW_C_CTR", "EPWW_N_CTR"], "nominal": "EPWW_C_CTR"}]


def test_alt_range_and_stack_key():
    assert rb.alt_range("+365") == (36500, rb.TOP_FT) and rb.alt_range("-165") == (0, 16500)
    assert rb.alt_range("285-365") == (28500, 36500) and rb.alt_range(" 95-125 ") == (9500, 12500)
    assert rb.alt_range(None) is None and rb.alt_range("") is None and rb.alt_range("FL365") is None
    assert [rb.stack_key(x) for x in ("EYVL U", "EDDB N APP", "ESMM L", "LKMT APP", "UMBB TWR", "UKR CTR", "LZTT TWR",
                                      "EDUU OSE22", "EDMM SAS", "EKDK X", "", None)] == [
        "EYVL", "EDWW", "ESAA", "LKAA", "UMMV", "UKLV", "LZBB", "EDUU", "EDMM", None, None, None]


def test_stack_tiles():
    def n(label, alt, by, fmp=False, cs=None):
        return {"label": label, "alt": alt, "station": label, "controlled_by": by, "callsign": cs or (by[0] if by else None),
                "fmp": fmp}

    items = [n("EYVL FMP", None, [], fmp=True), n("EYVL E", "-225", ["EYVL_E_CTR", "EYVL_CTR"]),
             n("EYVL U", "+365", ["EYVL_U_CTR", "EYVL_CTR"]), n("EYVL U", "+365", ["EYVL_U_CTR", "EYVL_CTR"]),
             n("UKLL APP", None, ["UKLL_APP"]), n("UKLV", None, [], cs="UKLV_CTR"), n("EKDK X", None, ["EKDK_CTR"]),
             n("EYVL M", "335-365", ["EYVL_M_CTR", "EYVL_CTR"]), n("UMKK TMU", None, ["UMKK_TMU"], fmp=True), "x"]
    out = rb.stack_tiles(items)
    assert set(out) == {"EYVL", "UKLV"}  # FMP/TMU i FIR-y bez stosu pominięte
    assert [t["label"] for t in out["EYVL"]] == ["EYVL U", "EYVL M", "EYVL E"]  # od najwyższego, bez powtórek
    assert out["EYVL"][0] == {"label": "EYVL U", "alt": "+365", "lo": 36500, "hi": rb.TOP_FT, "callsign": "EYVL_U_CTR",
                              "chain": ["EYVL_U_CTR", "EYVL_CTR"]}
    # bez wysokości: CTR przed APP; pusta kolejność = samo stanowisko kafelka
    assert [(t["label"], t["chain"], t["lo"]) for t in out["UKLV"]] == [("UKLV", ["UKLV_CTR"], None),
                                                                       ("UKLL APP", ["UKLL_APP"], None)]
    assert rb.stack_tiles(None) == {}


def test_tma_parts_helpers():
    sl = [{"name": "EPWA_DIR11_A", "chain": ["EPWA_F_APP", "EPWA_APP", "EPWW_C_CTR"]},
          {"name": "EPWAN_TMA", "chain": ["EPWA_N_APP", "EPWA_APP", "EPWW_C_CTR"]},
          {"name": "EPWA_TMA", "chain": ["EPWA_I_APP", "EPWA_APP", "EPWW_C_CTR"]},
          {"name": "EPSC_TMA", "chain": ["EPSC_TWR", "EPWW_DBT_CTR"]},
          {"name": "EPKK_DIR07", "chain": ["EPKK_KK_APP", "EPKK_APP"]}, {"name": "EPKK_TMA", "chain": ["EPKK_KK_APP", "EPKK_APP"]}]
    # dyrektor: pierwszy tylko w wycinkach EPxx_DIRnn (KK_KK_APP jest też pierwszy w zwykłym wycinku → nie dyrektor)
    dirs = rb.directors(sl)
    assert dirs == {"EPWA_F_APP"}
    assert [rb.part_owner(s["chain"], dirs) for s in sl] == ["EPWA_APP", "EPWA_N_APP", "EPWA_APP", "EPSC_TWR",
                                                              "EPKK_KK_APP", "EPKK_KK_APP"]
    assert rb.part_owner(["EPWW_C_CTR"], dirs) is None
    # punkt etykiety części: w jej własnym obszarze (bez miejsca, które zajmuje druga część), z dala od krawędzi
    own = [{"geom": _sq(20, 52, 21, 53)}]
    other = [{"geom": _sq(20, 52, 20.5, 53)}]
    lat, lon = rb.part_anchor(own, other)
    assert 52.3 < lat < 52.7 and 20.6 < lon < 20.9
    lat, lon = rb.part_anchor(own, [])
    assert 52.4 < lat < 52.6 and 20.4 < lon < 20.6
    assert rb.part_anchor([], []) is None


def test_neighbour_tile_chains():
    """Kafelki VACS bez CTR w controlled_by (UMMV N/W, UKLV, EDDB APP): dalej lista OWNER z .ese wycinka stanowiska
    albo główny CTR FIR-u; kolejność VACS zostaje pierwsza."""
    ese = rb.first_chains([["UMMV_W_CTR", "UMMV_CTR"], ["UMMV_W_CTR", "UMMV_CTR"], ["UMMV_W_CTR", "UMBB_APP"],
                           ["EDDB_N_APP", "EDDB_S_APP", "EDWW_FLG_CTR", "EDWW_CTR"], ["", "X_CTR"], []])
    assert ese == {"UMMV_W_CTR": ["UMMV_W_CTR", "UMMV_CTR"], "EDDB_N_APP": ["EDDB_N_APP", "EDDB_S_APP", "EDWW_FLG_CTR",
                                                                           "EDWW_CTR"], "X_CTR": ["X_CTR"]}
    assert rb.tile_chain(["UMMV_W_CTR"], "UMMV_W_CTR", ese, "UMMV_CTR") == ["UMMV_W_CTR", "UMMV_CTR"]
    assert rb.tile_chain(["EDDB_N_APP", "EDDB_S_APP"], "EDDB_N_APP", ese, None) == [
        "EDDB_N_APP", "EDDB_S_APP", "EDWW_FLG_CTR", "EDWW_CTR"]
    # bez wycinka w .ese: APP/TWR przejmuje główny CTR, CTR bez niczego zostaje sam
    assert rb.tile_chain(["UKLL_APP"], "UKLL_APP", {}, "UKLV_CTR") == ["UKLL_APP", "UKLV_CTR"]
    assert rb.tile_chain(["UKR_CTR"], "UKR_CTR", {}, "UKLV_CTR") == ["UKR_CTR"]
    # controlled_by z innym CTR: bez zmian
    assert rb.tile_chain(["EYVL_U_CTR", "EYVL_CTR"], "EYVL_U_CTR", ese, "EYVL_CTR") == ["EYVL_U_CTR", "EYVL_CTR"]
    out = rb.stack_tiles([{"label": "UMMV W", "alt": None, "controlled_by": ["UMMV_W_CTR"], "callsign": "UMMV_W_CTR"},
                          {"label": "UMBB TWR", "alt": None, "controlled_by": ["UMBB_TWR"], "callsign": "UMBB_TWR"}],
                         ese, {"UMMV_CTR"})
    assert [t["chain"] for t in out["UMMV"]] == [["UMMV_W_CTR", "UMMV_CTR"], ["UMBB_TWR", "UMMV_CTR"]]
    # top-down dalej niż do głównego CTR: ogon list OWNER za stanowiskiem (UMMV_CTR → RU-WRC_FSS z wycinka UMMV N,
    # choć w wycinkach UMMV W jest ostatni); bez CTR innych stosów (UKR_CTR → LZBB_CTR z jednego wycinka przy granicy)
    nb = [["UMMV_W_CTR", "UMMV_CTR"]] * 5 + [["UMMV_N_CTR", "UMMV_CTR", "RU-WRC_FSS"], ["UMBB_TWR", "UMMV_CTR"],
                                            ["UKLV_CTR", "UKR_CTR"], ["UKLU_TWR", "UKLV_CTR", "UKR_CTR", "LZBB_CTR"]]
    ese, tails = rb.first_chains(nb), rb.chain_tails(nb)
    assert tails["UMMV_CTR"] == ["RU-WRC_FSS"] and tails["UMMV_W_CTR"] == ["UMMV_CTR"] and "RU-WRC_FSS" not in tails
    assert tails["UKR_CTR"] == ["LZBB_CTR"]
    vacs = [{"label": lb, "alt": None, "controlled_by": [cs], "callsign": cs}
            for lb, cs in (("UMMV N", "UMMV_N_CTR"), ("UMMV W", "UMMV_W_CTR"), ("UMBB TWR", "UMBB_TWR"), ("UMMV", "UMMV_CTR"),
                           ("UKR CTR", "UKR_CTR"), ("UKLV", "UKLV_CTR"))]
    out = rb.stack_tiles(vacs, ese, {"UMMV_CTR", "UKLV_CTR"}, tails)
    assert {t["label"]: t["chain"] for t in out["UMMV"]} == {
        "UMMV N": ["UMMV_N_CTR", "UMMV_CTR", "RU-WRC_FSS"], "UMMV W": ["UMMV_W_CTR", "UMMV_CTR", "RU-WRC_FSS"],
        "UMBB TWR": ["UMBB_TWR", "UMMV_CTR", "RU-WRC_FSS"], "UMMV": ["UMMV_CTR", "RU-WRC_FSS"]}
    assert {t["label"]: t["chain"] for t in out["UKLV"]} == {"UKR CTR": ["UKR_CTR"], "UKLV": ["UKLV_CTR", "UKR_CTR"]}
    # VACS z innym CTR i bez głównego CTR na końcu: bez zmian (EDMM MEI › GER › HOF nie idzie dalej ogonem HOF)
    assert rb.tile_chain(["EDMM_MEI_CTR", "EDMM_GER_CTR", "EDMM_HOF_CTR"], "EDMM_MEI_CTR", {}, None,
                         {"EDMM_HOF_CTR": ["EDMM_RDG_CTR"]}, "EDMM") == ["EDMM_MEI_CTR", "EDMM_GER_CTR", "EDMM_HOF_CTR"]


def _sq(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_label_point_simplify():
    # punkt etykiety w wielokącie wklęsłym (L) leży w środku, z dala od krawędzi
    ell = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4], [0, 0]]]}
    lat, lon = rb.label_point(ell)
    assert 0 < lon < 4 and 0 < lat < 4 and (lon < 1 or lat < 1)
    assert rb.label_point({"type": "Point", "coordinates": [1, 2]}) is None
    wiggly = {"type": "Polygon", "coordinates": [[[i / 100, 0.0001 * (i % 2)] for i in range(101)] + [[1, 1], [0, 1]]]}
    s = rb.simplify(wiggly, 0.003, 3)
    assert s["type"] == "MultiPolygon" and len(s["coordinates"][0][0]) < 10
    assert rb.geom_area(_sq(0, 0, 2, 1)) == pytest.approx(2.0)


# --- frontend: radioboard.js (Node) i porządki po starym RADIO
NODE_SCRIPT = r"""
const rb = await import(process.argv[2]);
const L = (a) => new Set(a);
const bounds = rb.cutBounds([{ lo: 9500, hi: 19500 }, { lo: 19500, hi: 28500 }, { lo: 28500, hi: 33500 }, { lo: 33500, hi: 36500 }],
  { lower_ft: 9500, upper_ft: 33500 });
const items = Array.from({ length: 12 }, (_, i) => ({ x: 100 + (i % 3) * 4, y: 100 + Math.floor(i / 3) * 3, w: 60, h: 20 }));
const moves = rb.placeLabels(items, { x: 0, y: 0, w: 400, h: 400 });
const rects = items.map((it, i) => [it.x + moves[i].dx - it.w / 2, it.y + moves[i].dy - it.h / 2, it.w, it.h]);
let overlaps = 0;
rects.forEach((a, i) => rects.slice(i + 1).forEach((b) => {
  if (a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3]) overlaps++;
}));
// przeszkody (stosy sąsiadów, tytuł) i mniejsze odmiany etykiet, gdy pełna nie mieści się przy punkcie
const obst = { x0: 0, y0: 0, x1: 90, y1: 400 };
const crowd = Array.from({ length: 20 }, (_, i) => ({ x: 120 + (i % 4) * 3, y: 150 + Math.floor(i / 4) * 3, w: 70, h: 24,
  alts: [{ w: 50, h: 12 }, { w: 16, h: 10 }] }));
const cm = rb.placeLabels(crowd, { x: 0, y: 0, w: 400, h: 400 }, { obstacles: [obst] });
const crects = crowd.map((it, i) => {
  const s = cm[i].v ? it.alts[cm[i].v - 1] : it;
  return { x0: it.x + cm[i].dx - s.w / 2, y0: it.y + cm[i].dy - s.h / 2, x1: it.x + cm[i].dx + s.w / 2, y1: it.y + cm[i].dy + s.h / 2 };
});
const hit = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
let crowdOverlaps = 0;
crects.forEach((a, i) => crects.slice(i + 1).forEach((b) => { if (hit(a, b)) crowdOverlaps++; }));
const pj = rb.projection([14, 49, 24, 55], { x: 10, y: 20, w: 300, h: 300 });
console.log(JSON.stringify({
  ids: rb.LAYER_IDS, defaults: rb.DEFAULT_LAYERS, cut: rb.DEFAULT_CUT,
  args: [rb.argOf(L(["high", "atis", "low", "mid"]), 300), rb.argOf(L([]), 250), rb.argOf(L(["fis", "xx", "twra"]), 300)],
  parsed: ["atis,low,mid,high/fl300", "none/fl250", "app,bogus", "", "bogus/flx", "twr/FL95"].map((a) => {
    const p = rb.parseArg(a); return { layers: p.layers ? [...p.layers] : null, cut: p.cut };
  }),
  fl: [rb.flNum(29500), rb.flText(9500), rb.flText(0)],
  bounds, bands: [rb.cutBand(bounds, 300), rb.cutBand(bounds, 50), rb.cutBand(bounds, 400)],
  range: [rb.cutRange({ lower_ft: 9500, upper_ft: 33500 }), rb.cutRange(null), rb.cutRange({ lower_ft: 9500, upper_ft: 9500 })],
  cutAt: [300, 302, 303, 50, 340, "x", 95].map((c) => rb.cutAt(c, rb.cutRange({ lower_ft: 9500, upper_ft: 33500 }))),
  sliceAt: rb.sliceAt([{ lo: 0, hi: 100 }, { lo: 100, hi: 200 }], 100),
  overlaps, first: moves[0],
  cover: (() => {
    // obrys CTR 20×20 px wokół punktu (100, 100): etykieta 30×10 odchodzi poza obrys, z kształtem tylko liczonym
    const ring = [[90, 90], [110, 90], [110, 110], [90, 110]];
    const gr = rb.coverGrid([ring], 300, 300, { points: [[200, 200]] });
    const it = { x: 100, y: 100, w: 30, h: 10 };
    const [m] = rb.placeLabels([it], { x: 0, y: 0, w: 300, h: 300 }, { cover: gr, near: 40 });
    const [m0] = rb.placeLabels([it], { x: 0, y: 0, w: 300, h: 300 });
    const r = { x0: 100 + m.dx - 15, y0: 100 + m.dy - 5, x1: 100 + m.dx + 15, y1: 100 + m.dy + 5 };
    // obsadzona (wv) zostaje pełna, gdy nieobsadzona przy tym samym tłoku traci częstotliwość
    const lines = Array.from({ length: 10 }, (_, i) => [[10, 2 + 32 * i], [290, 2 + 32 * i]]);  // pełna zawsze na linii
    const sq = (wv) => rb.placeLabels([{ x: 150, y: 50, w: 40, h: 30, alts: [{ w: 30, h: 12 }, { w: 12, h: 12 }], wv, max: 60 }],
      { x: 0, y: 0, w: 300, h: 300 }, { cover: rb.coverGrid(lines, 300, 300) })[0].v;
    // etykieta bez pin (sektor ACC, FIS) też nie staje na kropce lotniska ani TMA, choć jej punkt leży tuż obok
    const pinned = rb.placeLabels([{ x: 150, y: 150, w: 20, h: 8, pin: true }, { x: 152, y: 150, w: 30, h: 10, max: 70 }],
      { x: 0, y: 0, w: 300, h: 300 })[1];
    const onDot = Math.abs(152 + pinned.dx - 150) < 15 + 3 && Math.abs(150 + pinned.dy - 150) < 5 + 3;
    return { m, m0, inner: rb.coverOf(gr, { x0: 95, y0: 95, x1: 105, y1: 105 }), edge: rb.coverOf(gr, { x0: 80, y0: 95, x1: 100, y1: 105 }),
      point: rb.coverOf(gr, { x0: 195, y0: 195, x1: 205, y1: 205 }), none: rb.coverOf(null, r),
      clear: rb.coverOf(gr, r), v: [sq(1), sq(4)], onDot, pinned };
  })(),
  crowd: { overlaps: crowdOverlaps, onObstacle: crects.filter((r) => hit(r, obst)).length, hits: cm.filter((m) => m.hit).length,
    variants: [...new Set(cm.map((m) => m.v))].sort(), first: cm[0] },
  hhmm: ["2026-10-12T12:00:43.0000000Z", "2026-10-12T09:05:00Z", "not a date", null].map(rb.utcHhmm),
  counted: rb.controllersOn({ EPWW_N_CTR: { callsign: "EPWW_N_CTR" }, EKDK_UN_CTR: { callsign: "EKDK_CTR", alias_of: "EKDK_CTR" },
    EKDK_CTR: { callsign: "EKDK_CTR" }, EPWA_ATIS: { callsign: "EPWA_ATIS" }, EPWA_APP: { callsign: "EPWA_APP" } }),
  bboxes: [rb.bboxOrDefault(rb.bboxOf({ type: "MultiPolygon", coordinates: [] })), rb.bboxOrDefault([14, 49, 24, 55]), rb.EPWW_BBOX],
  stack: Object.fromEntries(Object.entries({
    // EYVL: +365, 335-365, 225-335, -225 → osie 365, 335, 225 (każda raz)
    EYVL: [[36500, 66000, "U"], [33500, 36500, "M"], [22500, 33500, "L"], [0, 22500, "E"]],
    // EDUU: SPE +315 nie styka się z OSE12/HVL12 285-365 → nowy ciąg po 285
    EDUU: [[36500, 66000, "O22"], [36500, 66000, "H22"], [31500, 66000, "SPE"], [28500, 36500, "O12"], [28500, 36500, "H12"]],
    // LKAA: -125 pod -305 (oba od ziemi) → 125 między nimi
    LKAA: [[30500, 66000, "NU"], [30500, 66000, "SU"], [0, 30500, "NL"], [0, 30500, "SL"], [0, 12500, "MT"]],
    // EDWW: bez wysokości, potem -165 i -135
    EDWW: [[null, null, "MRZ"], [null, null, "FLG"], [0, 16500, "DEP"], [0, 13500, "APP"]],
    UKLV: [[null, null, "UKLV"], [null, null, "UKLL"]],
    LOW: [[0, 24500, "X"]],  // pasmo, które nie sięga FL660: górna granica nad nim
  }).map(([k, ts]) => [k, rb.stackBands(ts.map(([lo, hi, c]) => ({ lo, hi, c })))
    .map((b) => ({ c: b.tiles.map((t) => t.c), top: b.top, fl: b.fl, gap: b.gap, re: b.re }))])),
  advisory: rb.ADVISORY_FQ, emptyBands: rb.stackBands(null),
  short: ["EDWW FLG", "ESMM 6", "LKAA", "", null].map(rb.byShort),
  proj: { a: pj.P([14, 55]), b: pj.P([24, 49]), box: pj.box },
  bottom: rb.projection([0, 0, 1, 1], { x: 0, y: 0, w: 100, h: 400 }, 0, "bottom").box,
  path: rb.pathOf({ type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1]]] }, ([x, y]) => [x * 10, y * 10]),
  bbox: rb.bboxOf({ type: "MultiPolygon", coordinates: [[[[0, 1], [2, 3], [1, 5]]], [[[-1, 0], [0, 0], [0, 1]]]] }),
}));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("rbjs")
    (d / "radioboard.mjs").write_text((FRONTEND / "js" / "radioboard.js").read_text("utf-8"), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "radioboard.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_js_layers_and_address(js):
    assert js["ids"] == ["atis", "low", "mid", "high", "app", "twr", "twra", "fis"]
    assert js["defaults"] == ["atis", "low", "mid", "high"] and js["cut"] == 300
    # stała kolejność warstw w adresie, "none" = żadna, nieznane pomijane
    assert js["args"] == ["atis,low,mid,high/fl300", "none/fl250", "twra,fis/fl300"]
    assert js["parsed"] == [
        {"layers": ["atis", "low", "mid", "high"], "cut": 300}, {"layers": [], "cut": 250},
        {"layers": ["app"], "cut": None}, {"layers": None, "cut": None}, {"layers": None, "cut": None},
        {"layers": ["twr"], "cut": 95}]
    assert js["fl"] == ["295", "FL095", "FL000"]


def test_js_cut_bands(js):
    assert js["bounds"] == [9500, 19500, 28500, 33500]  # wycinek MID (33500–36500) poza warstwą LOW
    assert js["bands"] == [{"i": 2, "lo": 28500, "hi": 33500, "n": 3}, {"i": 0, "lo": 9500, "hi": 19500, "n": 3},
                           {"i": 2, "lo": 28500, "hi": 33500, "n": 3}]
    # suwak LOW: od dolnej granicy warstwy do 500 ft pod górną, co 500 ft; poziom spoza zakresu albo między krokami
    # wyrównany (adres #radio/…/fl302, stara pamięć przeglądarki)
    assert js["range"] == [{"min": 95, "max": 330, "step": 5}, {"min": 95, "max": 330, "step": 5},
                           {"min": 95, "max": 95, "step": 5}]
    assert js["cutAt"] == [300, 300, 305, 95, 330, 95, 95]
    assert js["sliceAt"] == [{"lo": 100, "hi": 200}]  # dolna granica włącznie, górna nie


def test_js_layout_helpers(js):
    # pierwsza (najważniejsza) zostaje w miejscu
    assert js["overlaps"] == 0 and js["first"] == {"dx": 0, "dy": 0, "v": 0, "hit": False}
    # obrysy CTR/TMA (coverGrid): sam obrys i kropki punktów, nie wnętrze; etykieta odchodzi poza obrys
    cv = js["cover"]
    assert cv["inner"] == 0 and cv["edge"] > 0 and cv["point"] > 0 and cv["none"] == 0
    assert cv["m0"]["dx"] == 0 and cv["m0"]["dy"] == 0 and cv["clear"] == 0 and (cv["m"]["dx"], cv["m"]["dy"]) != (0, 0)
    assert cv["v"][0] > 0 and cv["v"][1] == 0  # wv: obsadzona etykieta zachowuje częstotliwość
    assert cv["onDot"] is False and cv["pinned"]["hit"] is False, cv["pinned"]  # kropka z pin zostaje widoczna
    # tłok: bez nakładania się i bez wchodzenia na przeszkody (stosy sąsiadów, tytuł); część etykiet w mniejszej odmianie
    c = js["crowd"]
    assert c["overlaps"] == 0 and c["onObstacle"] == 0 and c["hits"] == 0, c
    assert c["first"]["v"] == 0 and len(c["variants"]) > 1
    assert js["hhmm"] == ["12:00Z", "09:05Z", "–", "–"]  # 7 cyfr ułamka z VATSIM, zły czas nie psuje strony
    assert js["counted"] == 3  # alias_of (EKDK) liczony raz, ATIS nie
    assert js["bboxes"][0] is None and js["bboxes"][1] == [14, 49, 24, 55] and len(js["bboxes"][2]) == 4
    bd = js["stack"]
    assert bd["EYVL"] == [{"c": ["U"], "top": None, "fl": 36500, "gap": False, "re": None},
                          {"c": ["M"], "top": None, "fl": 33500, "gap": False, "re": None},
                          {"c": ["L"], "top": None, "fl": 22500, "gap": False, "re": None},
                          {"c": ["E"], "top": None, "fl": None, "gap": False, "re": None}]
    # EDUU: oś 365, 285, potem kreska i zakres nowego ciągu "+315" nad SPE – bez 315 pod SPE (oś nie czyta się wstecz)
    assert bd["EDUU"] == [{"c": ["O22", "H22"], "top": None, "fl": 36500, "gap": False, "re": None},
                          {"c": ["O12", "H12"], "top": None, "fl": 28500, "gap": False, "re": None},
                          {"c": ["SPE"], "top": None, "fl": None, "gap": True, "re": "+315"}]
    assert [(b["c"], b["fl"]) for b in bd["LKAA"]] == [(["NU", "SU"], 30500), (["NL", "SL"], 12500), (["MT"], None)]
    assert [(b["c"], b["fl"]) for b in bd["EDWW"]] == [(["MRZ", "FLG"], 16500), (["DEP"], 13500), (["APP"], None)]
    assert bd["UKLV"] == [{"c": ["UKLV", "UKLL"], "top": None, "fl": None, "gap": False, "re": None}]
    assert bd["LOW"] == [{"c": ["X"], "top": 24500, "fl": None, "gap": False, "re": None}]
    # każda granica raz w stosie
    for k, bands in bd.items():
        nums = [x for b in bands for x in (b["top"], b["fl"]) if x]
        assert len(nums) == len(set(nums)), k
    assert js["advisory"] == "122.800" and js["emptyBands"] == []
    assert js["short"] == ["FLG", "6", "LKAA", "", ""]
    a, b, box = js["proj"]["a"], js["proj"]["b"], js["proj"]["box"]
    assert a == pytest.approx([box["x"], box["y"]]) and b == pytest.approx([box["x"] + box["w"], box["y"] + box["h"]])
    assert 10 <= box["x"] and box["x"] + box["w"] <= 310 and 20 <= box["y"] and box["y"] + box["h"] <= 320
    assert js["bottom"]["y"] + js["bottom"]["h"] == pytest.approx(400)  # MID przy dolnej krawędzi
    assert js["path"] == "M0.0,0.0L10.0,0.0L10.0,10.0Z"
    assert js["bbox"] == [-1, 0, 2, 5]



LAYOUT_SCRIPT = r"""
// warstwa TWR na podglądzie LOW jak na ekranie 1366×768 (FL300): etykiety ACC i TWR z /api/radio/board ułożone przez
// placeLabels z parametrami labelOpts i odmianami lblVariants (jak tabs/radio.js); rozmiary odmian zmierzone
// w przeglądarce. Dwa zestawy: "board" – kto online wg tablicy (bez nikogo samo ID), "all" – każda wieża online
// i każdy sektor obsadzony (najwięcej częstotliwości)
import fs from "fs";
const rb = await import(process.argv[2]);
const { labelPoint } = await import(process.argv[3]);
const { board, geo } = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const { lblVariants } = await import(process.argv[5]);
const W = 487, H = 731, free = { x: 140, y: 124, w: 341, h: 470 };
const obstacles = [[6, 6, 128, 211], [6, 352, 128, 162], [6, 650, 128, 75], [181, 6, 164, 112], [153, 600, 193, 125], [203, 125, 277, 20]]
  .map(([x, y, w, h]) => ({ x0: x - 2, y0: y - 2, x1: x + w + 2, y1: y + h + 2 }));
const { P } = rb.projection(rb.bboxOrDefault(rb.bboxOf(geo.border)) || rb.EPWW_BBOX, free, 0.03);
const area = (g) => rb.ringsOf(g).reduce((a, r) => a + Math.abs(r.reduce((s, [x1, y1], i) => s + x1 * r[(i + 1) % r.length][1] - r[(i + 1) % r.length][0] * y1, 0)) / 2, 0);
const byLetter = new Map();
rb.sliceAt(geo.acc, 30000).forEach((s) => { const b = byLetter.get(s.letter); if (!b || area(s.geometry) > area(b.geometry)) byLetter.set(s.letter, s); });
// odmiany [pełna, skrócona, samo ID] (px): wieża online, przejmowana (↑APP), bez nikogo (samo „SY TWR”); sektor ACC
// pełny ze skrótem właściciela (N 130.675) i bez nikogo (samo „LB”)
const SIZE = { on: [{ w: 54, h: 23 }, { w: 67, h: 13 }, { w: 24, h: 13 }], cov: [{ w: 77, h: 23 }, { w: 90, h: 13 }, { w: 24, h: 13 }],
  off: [null, null, { w: 44, h: 12 }], acc: [{ w: 65, h: 26 }], accOff: [null, null, { w: 19, h: 12 }] };
function layout(all) {
  const items = [];
  board.sectors.filter((s) => s.layer === "LOW" && byLetter.has(s.letter)).forEach((s) => {
    const sl = byLetter.get(s.letter), at = labelPoint({ type: "Feature", geometry: sl.geometry, properties: { fir: "EPWW", name: sl.name } })?.at;
    const on = all || !!s.owner, vs = lblVariants({ ls: on ? [{ fq: "1", by: "N" }] : [] }, false);
    if (at) items.push({ name: s.id, twr: false, on, prio: 0, at, vs, sz: vs.map((v) => SIZE[on ? "acc" : "accOff"][v]), ...rb.labelOpts("acc", { shapes: "twr" }, P) });
  });
  board.twr.filter((g) => g.at).forEach((g) => {
    const tw = all || g.owners.some((o) => /_TWR$/.test(o)), st = tw ? "on" : g.owners.length ? "cov" : "off";
    const vs = lblVariants({ ls: st === "off" ? [] : [{ fq: "1", by: st === "cov" ? "↑APP" : "" }] }, true);
    items.push({ name: g.icao, twr: true, on: st !== "off", prio: tw ? 2 : 2.5, at: g.at, vs, sz: vs.map((v) => SIZE[st][v]),
      ...rb.labelOpts("twr", { box: g.box, on: st !== "off", fq: st !== "off", shapes: "twr" }, P) });
  });
  // pełna odmiana (w, h) i mniejsze (alts) jak w placeLabels na stronie
  items.sort((a, b) => a.prio - b.prio).forEach((it) => { [it.x, it.y] = P([it.at[1], it.at[0]]); Object.assign(it, it.sz[0], { alts: it.sz.slice(1) }); });
  const rings = geo.ctr.flatMap((c) => rb.ringsOf(c.geometry).map((r) => ({ icao: c.name.slice(0, 4), r: r.map(P) })));
  const cover = rb.coverGrid(rings.map((x) => x.r), W, H, { points: items.filter((it) => it.twr).map((it) => [it.x, it.y]) });
  const mv = rb.placeLabels(items, { x: 2, y: 2, w: W - 4, h: H - 4 }, { obstacles, cover, near: 40 });
  const rects = items.map((it, i) => { const s = it.sz[mv[i].v];
    return { x0: it.x + mv[i].dx - s.w / 2, y0: it.y + mv[i].dy - s.h / 2, x1: it.x + mv[i].dx + s.w / 2, y1: it.y + mv[i].dy + s.h / 2 }; });
  const inside = (r, x, y, m) => x > r.x0 - m && x < r.x1 + m && y > r.y0 - m && y < r.y1 + m;
  const twr = items.map((it, i) => i).filter((i) => items[i].twr);
  // obrys każdego CTR co 1 px: ile pod etykietami
  const ctr = {};
  rings.forEach(({ icao, r }) => r.forEach(([ax, ay], k) => {
    const [bx, by] = r[(k + 1) % r.length], n = Math.max(1, Math.floor(Math.hypot(bx - ax, by - ay)));
    for (let t = 0; t < n; t++) {
      const x = ax + ((bx - ax) * t) / n, y = ay + ((by - ay) * t) / n, c = (ctr[icao] ||= [0, 0]);
      c[1]++;
      if (rects.some((q) => inside(q, x, y, -1))) c[0]++;
    }
  }));
  const o = (a, px, py) => Math.sign((a[2] - a[0]) * (py - a[1]) - (a[3] - a[1]) * (px - a[0]));
  const leads = twr.map((i) => { const r = rects[i], it = items[i];
    return [it.x, it.y, Math.max(r.x0, Math.min(it.x, r.x1)), Math.max(r.y0, Math.min(it.y, r.y1))]; });
  const shown = (i) => items[i].vs[mv[i].v];  // numer odmiany jak w lblHtml (2 = samo ID)
  return {
    n: twr.length, off: items.filter((it) => !it.on).map((it) => it.name),
    dots: items.flatMap((it, i) => twr.filter((j) => j !== i && inside(rects[i], items[j].x, items[j].y, 1)).map((j) => `${it.name}>${items[j].name}`)),
    ctr: Object.fromEntries(Object.entries(ctr).map(([k, [a, b]]) => [k, a / b])),
    overlaps: rects.flatMap((a, i) => rects.slice(i + 1).map((b, j) => (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1 ? `${items[i].name}/${items[i + 1 + j].name}` : "")))
      .filter(Boolean),
    hits: mv.filter((m) => m.hit).length,
    // obsadzona bez częstotliwości (samo ID) i bez nikogo z odmianą z częstotliwością
    noFq: items.map((it, i) => i).filter((i) => items[i].on && shown(i) === 2).map((i) => items[i].name),
    fqOff: items.filter((it) => !it.on && it.vs.some((v) => v !== 2)).map((it) => it.name),
    far: Math.max(...leads.map((l) => Math.hypot(l[2] - l[0], l[3] - l[1]))),
    crossed: leads.flatMap((a, i) => leads.slice(i + 1).filter((b) => o(a, b[0], b[1]) * o(a, b[2], b[3]) < 0 && o(b, a[0], a[1]) * o(b, a[2], a[3]) < 0)).length,
  };
}
console.log(JSON.stringify({ board: layout(false), all: layout(true) }));
"""


def test_js_twr_layout(client, board, tmp_path):
    """Runda 11 (przegląd): etykiety TWR na podglądzie LOW 1366×768 nie zasłaniają CTR ani kropek lotnisk – geometria
    z /api/radio/board, układ placeLabels z labelOpts i lblVariants jak na stronie (zrzuty w przeglądarce sprawdzają
    resztę). Bez nikogo samo ID (runda 11, Marek), obsadzone zawsze z częstotliwością."""
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path / "js"
    shutil.copytree(FRONTEND / "js", d)
    (d / "package.json").write_text('{"type": "module"}', "utf-8")
    data = tmp_path / "board.json"
    data.write_text(json.dumps({"board": board, "geo": client.get("/api/radio/board?geo=1").json()["geo"]}), "utf-8")
    script = tmp_path / "layout.mjs"
    script.write_text(LAYOUT_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "radioboard.js").as_uri(), (d / "coverage.js").as_uri(), str(data),
                        (d / "tabs" / "radio.js").as_uri()], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    # tablica z FEED: poza EPWA_APP (przejmuje kilka wież) żadnej wieży online – większość etykiet TWR to samo ID
    assert len([x for x in res["board"]["off"] if len(x) == 4]) >= 10 and res["all"]["off"] == []
    for key, out in res.items():
        assert out["n"] >= 15, key  # wszystkie CTR z pliku .ese (EPWA … EPZG, wojskowe EPDE, EPKS)
        # żadna etykieta (TWR ani sektora) nie stoi na kropce lotniska, żaden CTR nie jest schowany pod etykietami
        assert out["dots"] == [], key
        assert max(out["ctr"].values()) <= 0.2, (key, {k: round(v, 2) for k, v in out["ctr"].items() if v > 0.2})
        # bez nakładania się; każda obsadzona (także przejmowana ↑APP) z częstotliwością, bez nikogo tylko samo ID
        assert out["overlaps"] == [] and out["hits"] == 0, key
        assert out["noFq"] == [] and out["fqOff"] == [], key
        # linie prowadzące krótkie i bez skrzyżowań
        assert out["far"] <= 90 and out["crossed"] == 0, key

LOA_SCRIPT = r"""
const radio = await import(process.argv[2]);
const firs = JSON.parse(process.argv[3]);
const radioOf = (cs) => (cs === "EDDB_N_APP" ? "Bremen Radar" : "");
const evil = { title: "<img src=x onerror=alert(1)>", version: "v<1>", pdf: "javascript:alert(1)",
  transfers: [{ dir: "out", traffic: "<b>X</b>", cop: "C\"OP", level: "FL100", from: "", to: "", conditions: "&" }],
  silent: ["<i>s</i>"], vfr: [], other: [], positions: [{ callsign: "EDDB_N_APP", radio: "", sector: "<s>", frequency: "1", side: "NB", note: "" }] };
console.log(JSON.stringify({
  edww: radio.loaHtml("EDWW", "BREMEN", firs.EDWW, radioOf),
  none: radio.loaHtml("UMKK", "KALININGRAD", null, radioOf),
  loading: radio.loaHtml("EDWW", "BREMEN", null, radioOf, { loading: true }),
  error: radio.loaHtml("EDWW", "BREMEN", null, radioOf, { msg: "LOA: <zły plik>" }),
  evil: radio.loaHtml("EDWW", "BREMEN", evil, radioOf),
}));
"""


def test_js_loa_panel(tmp_path):
    """Panel LOA w RADIO (tabs/radio.js loaHtml): tabele przekazań w obie strony, zasady, stanowiska, PDF w OTHER › DOCS
    i w nowej karcie; brak LOA, wczytywanie i błąd; dane z loa.json zawsze przez esc()."""
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path / "js"
    shutil.copytree(FRONTEND / "js", d)
    (d / "package.json").write_text('{"type": "module"}', "utf-8")
    script = tmp_path / "loa.mjs"
    script.write_text(LOA_SCRIPT, "utf-8")
    firs = json.loads((ROOT_DIR / "data" / "seed" / "loa.json").read_text("utf-8"))["firs"]
    r = subprocess.run([node, str(script), (d / "tabs" / "radio.js").as_uri(), json.dumps(firs)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    e, f = out["edww"], firs["EDWW"]
    n_out = sum(t["dir"] == "out" for t in f["transfers"])
    assert f"przekazania: {n_out} z EPWW · {len(f['transfers']) - n_out} do EPWW" in e
    assert "EPWW → EDWW" in e and "EDWW → EPWW" in e and "Stanowiska i sektory wg LOA" in e
    rows = len(f["transfers"]) + len(f["positions"]) + sum(len(f[k]) for k in ("silent", "vfr", "other"))
    assert e.count("data-f=") == rows
    assert 'data-docs="LOA/LOA EPWW EDWW Bremen, EDUU Karlsruhe (AIRAC 2602).pdf"' in e
    assert 'href="/files/docs/LOA/LOA%20EPWW%20EDWW%20Bremen,%20EDUU%20Karlsruhe%20(AIRAC%202602).pdf"' in e
    assert "<td>Bremen Radar</td>" in e  # znak radiowy z tablicy RADIO, gdy LOA go nie podaje
    assert "Brak LOA EPWW z UMKK" in out["none"] and "data-docs" not in out["none"] and "KALININGRAD" in out["none"]
    assert "Wczytywanie LOA…" in out["loading"]
    assert 'class="error">LOA: &lt;zły plik&gt;' in out["error"]
    ev = out["evil"]
    assert "<img" not in ev and "<b>X" not in ev and "<i>s" not in ev and "<s>" not in ev and 'C"OP' not in ev
    assert "javascript:" not in ev and "data-docs" not in ev  # PDF tylko z /files/docs/


NB_SCRIPT = r"""
const radio = await import(process.argv[2]);
const FQ = { EYVL_CTR: "133.305", EYVL_U_CTR: "132.280", EYVL_M_CTR: "133.430", EYVL_E_CTR: "135.380" };
const x = { nm: (cs) => cs.replace(/_CTR$/, "").replace(/_/g, " "), freq: (cs) => FQ[cs] || "", who: (cs) => `${cs} ${FQ[cs]} – Jonas`,
  pos: (cs) => `${cs} ${FQ[cs] || ""}`, sel: "EYVL" };
const tiles = (owners) => [["EYVL U", "+365", 36500, 66000, "EYVL_U_CTR"], ["EYVL M", "335-365", 33500, 36500, "EYVL_M_CTR"],
  ["EYVL", "225-335", 22500, 33500, "EYVL_CTR"], ["EYVL E", "-225", 0, 22500, "EYVL_E_CTR"]].map(([label, alt, lo, hi, cs], i) => (
  { label, alt, lo, hi, callsign: cs, chain: cs === "EYVL_CTR" ? [cs] : [cs, "EYVL_CTR"], owner: owners[i] }));
const st = (owners) => ({ fir: "EYVL", title: "VILNIUS", tiles: tiles(owners) });
console.log(JSON.stringify({
  off: radio.nbStackHtml(st([null, null, null, null]), x),
  part: radio.nbStackHtml(st(["EYVL_U_CTR", null, null, null]), x),
  top: radio.nbStackHtml(st(["EYVL_CTR", "EYVL_CTR", "EYVL_CTR", "EYVL_CTR"]), x),
  flat: radio.nbStackHtml({ fir: "UKLV", title: "LVIV", tiles: [{ label: "UKLV", alt: null, lo: null, hi: null, callsign: "UKLV_CTR",
    chain: ["UKLV_CTR"], owner: null }] }, { ...x, sel: null }),
  empty: radio.nbStackHtml({ fir: "UMKK", title: "KALININGRAD", tiles: [] }, x),
  eduu: radio.nbStackHtml({ fir: "EDUU", title: "RHEIN", tiles: [["OSE22", 36500, 66000], ["OSE12", 28500, 36500],
    ["SPE", 31500, 66000]].map(([l, lo, hi]) => ({ label: `EDUU ${l}`, alt: null, lo, hi, callsign: `EDUU_${l}_CTR`,
    chain: [`EDUU_${l}_CTR`], owner: null })) }, { ...x, sel: null }),
  nonet: radio.nbStackHtml(st([null, null, null, null]), { ...x, net: false }),
  evil: radio.nbStackHtml({ fir: "<b>", title: "<i>t</i>", tiles: [{ label: "<img src=x>", alt: null, lo: null, hi: null,
    callsign: "A_CTR", chain: ["A_CTR"], owner: "A_CTR" }] }, { ...x, freq: () => "<s>1</s>" }),
}));
"""


def test_js_neighbour_stack(tmp_path):
    """Stos sąsiada w RADIO (tabs/radio.js nbStackHtml): częstotliwość tylko na kafelkach, które ktoś obsługuje;
    nikt w stosie → jedna linia ADVISORY 122.800 i żadnej częstotliwości; granice pionowe raz; dane przez esc()."""
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path / "js"
    shutil.copytree(FRONTEND / "js", d)
    (d / "package.json").write_text('{"type": "module"}', "utf-8")
    script = tmp_path / "nb.mjs"
    script.write_text(NB_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "tabs" / "radio.js").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    freqs = re.compile(r"\d{3}\.\d{3}")
    off = out["off"]
    shown = re.sub(r'data-rt="[^"]*"', "", off)  # bez dymków: to, co widać na stosie
    # nikt z EYVL online: jedna linia ADVISORY, żadnej innej częstotliwości (także nominalnej), kafelki z samą etykietą
    assert shown.count("ADVISORY 122.800") == 1 and '<div class="rb-unicom">ADVISORY 122.800</div>' in off
    assert freqs.findall(shown) == ["122.800"]
    assert off.count('class="rb-box off"') == 4 and 'class="rb-fq"' not in off
    assert 'class="rb-nbh sel"' in off and 'data-fir="EYVL"' in off
    # każda granica raz, w kolejności od góry; bez górnej i dolnej skrajnej
    assert re.findall(r'class="rb-bd"[^>]*><i>(\d+)</i>', off) == ["365", "335", "225"]
    part = out["part"]
    assert "rb-unicom" not in part and part.count('class="rb-box on"') == 1 and part.count('class="rb-fq"') == 1
    assert '<span class="rb-fq">132.280</span>' in part and 'class="rb-by"' not in part  # własne stanowisko: bez skrótu
    top = out["top"]
    # EYVL_CTR top-down: wszystkie kafelki z jego częstotliwością, skrót przy cudzych
    assert top.count('<span class="rb-fq">133.305</span>') == 4 and top.count('<i class="rb-by">EYVL</i>') == 3
    assert "Teraz: EYVL_CTR 133.305 – Jonas" in top and top.count("Online: EYVL_CTR 133.305 – Jonas") == 1  # dymek nagłówka
    assert "Nikt online – ADVISORY 122.800" in off and "Online:" not in off
    # dymki kafelków: ADVISORY tylko, gdy nikt w całym stosie; w częściowo obsadzonym samo "Teraz nikt"
    assert off.count("Teraz nikt – ADVISORY 122.800") == 4
    assert part.count("Teraz nikt") == 3 and "ADVISORY" not in part
    # oś wysokości w dymku nagłówka tylko w stosie z osią
    assert "granice pionowe (FL)" in off and "granice pionowe" not in out["flat"] and "nowy ciąg" not in off
    # EDUU: 365, 285, kreska i "+315" nad SPE (nowy ciąg), bez 315 pod SPE
    eduu = out["eduu"]
    assert re.findall(r'class="rb-bd(?: rb-re)?"[^>]*><i>([^<]+)</i>', eduu) == ["365", "285", "+315"]
    assert eduu.index("<i>285</i>") < eduu.index('class="rb-sep"') < eduu.index("<i>+315</i>") < eduu.index(">EDUU SPE<")
    assert "nowy ciąg pasm" in eduu
    assert 'class="rb-nb flat"' in out["flat"] and "rb-bd" not in out["flat"] and 'class="rb-nbh"' in out["flat"]
    assert "brak sektorów VACS" in out["empty"] and "ADVISORY" not in out["empty"]
    # błąd VATSIM: nie wiadomo, czy ktoś jest online – bez linii ADVISORY i bez częstotliwości
    assert "rb-unicom" not in out["nonet"] and 'class="rb-fq"' not in out["nonet"]
    ev = out["evil"]
    assert "<img" not in ev and "<i>t" not in ev and "<s>" not in ev and '<b>&lt;b&gt;</b>' in ev


LBL_SCRIPT = r"""
const radio = await import(process.argv[2]);
const lb = (o) => ({ cls: "twr", state: "off", id: "SY TWR", cid: "SY TWR", ls: [], rt: "SY TWR · EPSY", k: "EPSY", icao: "EPSY", ...o });
const html = (x, small = true) => ({ v: radio.lblVariants(x, small), html: radio.lblVariants(x, small).map((v) => radio.lblHtml(x, v)) });
const atl = '<span class="rb-ln rb-atl"><i class="rb-by">ATIS</i><span class="rb-fq">120.455</span><b class="rb-code">K</b></span>';
console.log(JSON.stringify({
  off: html(lb({})),
  acc: html({ cls: "acc", state: "off", id: "LB", cid: "LB", ls: [], rt: "Sektor B", k: "B" }, false),
  accOn: html({ cls: "acc", state: "on", id: "LB", cid: "LB", ls: [{ fq: "130.675", by: "N" }], rt: "Sektor B", k: "B" }, false),
  on: html(lb({ state: "on", id: "WA TWR", cid: "WA", ls: [{ fq: "118.305", by: "" }] })),
  cov: html(lb({ state: "cov", id: "WA TWR", cid: "WA", ls: [{ fq: "118.305", by: "↑APP" }] })),
  atis: html(lb({ id: "WA TWR", cid: "WA TWR", extra: atl })),
  keep: html({ cls: "app", state: "on", id: "WA S APP", cid: "WA S APP", ls: [{ fq: "", by: "WA APP" }], rt: "TMA" }),
  evil: html(lb({ id: "<img src=x>", cid: "<img src=x>", rt: '"><b>x</b>' })),
}));
"""


def test_js_label_variants(tmp_path):
    """Etykiety podglądów (tabs/radio.js lblHtml, lblVariants; runda 11, Marek): bez nikogo jedna odmiana – samo ID,
    bez żadnej częstotliwości; obsadzona (też przejmowana ↑APP) z częstotliwością w pełnej i skróconej odmianie."""
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path / "js"
    shutil.copytree(FRONTEND / "js", d)
    (d / "package.json").write_text('{"type": "module"}', "utf-8")
    script = tmp_path / "lbl.mjs"
    script.write_text(LBL_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "tabs" / "radio.js").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    freqs = re.compile(r"\d{3}\.\d{3}")

    def shown(h):  # bez dymku: to, co widać na etykiecie
        return re.sub(r'data-rt="[^"]*"', "", h)
    # bez nikogo: samo pełne ID, bez częstotliwości i bez "—" (sektor ACC, wieża)
    assert out["off"]["v"] == [2] and out["acc"]["v"] == [2]
    for key in ("off", "acc"):
        (h,) = out[key]["html"]
        assert not freqs.search(shown(h)) and "rb-fq" not in h and "—" not in h, key
    assert out["off"]["html"][0].endswith('<b class="rb-id">SY TWR</b></div>') and 'class="rb-lbl twr off cmp"' in out["off"]["html"][0]
    assert '<b class="rb-id">LB</b></div>' in out["acc"]["html"][0] and 'data-k="B"' in out["acc"]["html"][0]
    # obsadzone: częstotliwość w pełnej i skróconej, samo ID dopiero przy braku miejsca; sektor ACC tylko pełna
    assert out["accOn"]["v"] == [0] and '<span class="rb-fq">130.675</span>' in out["accOn"]["html"][0]
    for key in ("on", "cov"):
        v, h = out[key]["v"], out[key]["html"]
        assert v == [0, 1, 2] and all('<span class="rb-fq">118.305</span>' in x for x in h[:2]), key
        assert h[2].endswith('<b class="rb-id">WA</b></div>'), key
    assert "↑APP" in out["cov"]["html"][0] and '<i class="rb-by">↑APP</i>' in out["cov"]["html"][1]
    # wieża bez nikogo z ATIS online: ID i linia ATIS; mniejsze odmiany: samo pełne ID
    a = out["atis"]
    assert a["v"] == [0, 1, 2] and "120.455" in a["html"][0] and not any(freqs.search(shown(x)) for x in a["html"][1:])
    # część TMA, której częstotliwość jest w innej etykiecie: nazwa stanowiska zamiast pustej linii
    assert out["keep"]["v"] == [0, 1, 2] and "WA APP" in out["keep"]["html"][0] and "↑WA APP" in out["keep"]["html"][1]
    assert not any(freqs.search(shown(x)) for x in out["keep"]["html"])
    ev = out["evil"]["html"][0]
    assert "<img" not in ev and "<b>x" not in ev and "&lt;img src=x&gt;" in ev


def test_frontend_imports_resolve():
    # stare moduły RADIO usunięte: nic ich nie importuje, każdy import względny wskazuje istniejący plik
    js_dir = FRONTEND / "js"
    gone = {"radiostate.js", "geo.js", "sectorsplit.js"}
    assert not any((js_dir / g).exists() for g in gone)
    for f in js_dir.rglob("*.js"):
        for spec in re.findall(r"""(?:from|import)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""", f.read_text("utf-8")):
            assert (f.parent / spec).resolve().is_file(), f"{f.relative_to(FRONTEND)}: {spec}"
    index = (FRONTEND / "index.html").read_text("utf-8")
    assert "sectors.css" not in index and not (FRONTEND / "css" / "sectors.css").exists()
    radio = (FRONTEND / "js" / "tabs" / "radio.js").read_text("utf-8")
    assert "subtabs(" not in radio and "text-shadow" not in (FRONTEND / "css" / "radio.css").read_text("utf-8")
