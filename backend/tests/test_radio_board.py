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
        # właściciel = pierwsze stanowisko kolejności, które jest online; nikt → null (częstotliwość nominalna)
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
    # TMA Warszawa: APP online jako właściciel lokalny; TMA bez nikogo z APP: top-down ACC tylko w polu topdown
    assert tma["TMA Warszawa"]["nominal"] == "EPWA_APP" and tma["TMA Warszawa"]["owners"] == ["EPWA_APP"]
    assert tma["TMA Warszawa"]["topdown"] is None
    assert all(not any(o.startswith("EPWW_") for o in g["owners"]) for g in board["tma"] + board["twr"] + board["fis"])
    assert any(g["topdown"] == "EPWW_N_CTR" for g in board["tma"])
    assert all(g["at"] and 48 < g["at"][0] < 56 and 13 < g["at"][1] < 25 for g in board["tma"] + board["twr"] + board["fis"])
    twr = {g["icao"]: g for g in board["twr"]}
    assert twr["EPWA"]["nominal"].startswith("EPWA_") and twr["EPWA"]["nominal"].endswith("TWR")
    assert twr["EPWA"]["atis"] == "EPWA_ATIS" and twr["EPKK"]["atis"] == "EPKK_ATIS"
    assert board["fis"] and all("FIS" in g["id"] for g in board["fis"])


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
    st = {s["fir"]: s for s in board["stacks"]}
    assert [s["fir"] for s in board["stacks"]] == [s["fir"] for s in rb.STACKS]
    assert {s["fir"] for s in board["stacks"] if s["map"] == "left"} == {"ESAA", "EDWW", "EDUU", "EDMM", "LKAA"}
    assert {s["fir"] for s in board["stacks"] if s["map"] == "right"} == {"UMKK", "EYVL", "UMMV", "UKLV", "LZBB"}
    for s in board["stacks"]:
        assert s["rows"], s["fir"]
        keys = [(-r["hi"], -r["lo"], r["callsign"]) for r in s["rows"]]
        assert keys == sorted(keys)  # od najwyższej górnej granicy
        for r in s["rows"]:
            assert r["lo"] < r["hi"] <= rb.TOP_FT and r["sectors"] and r["chains"]
            assert r["callsign"].split("_")[-1] not in rb.NO_ROW
    flg = next(r for r in st["EDWW"]["rows"] if r["callsign"] == "EDWW_FLG_CTR")
    assert flg["owners"] == ["EDWW_FLG_CTR"] and flg["lo"] >= 9500
    # stanowisko offline: przejmuje pierwsze online z kolejności (EDDB DEP → EDWW FLG), albo nikt
    dep = next((r for r in st["EDWW"]["rows"] if r["callsign"].startswith("EDDB_") and "EDWW_FLG_CTR" in r["chains"][0]), None)
    if dep:
        assert dep["owners"] == ["EDWW_FLG_CTR"]
    assert all(r["owners"] == [] for r in st["LKAA"]["rows"])
    assert any(r["callsign"].startswith("UMMV") for r in st["UMMV"]["rows"])  # Białoruś z pliku EPWW
    assert any(r["callsign"].startswith("EDUU") for r in st["EDUU"]["rows"])


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


def test_board_upstream_error(client, monkeypatch):
    async def broken():
        raise UpstreamError("timeout")

    monkeypatch.setattr(vatsim_api, "get_feed", broken)
    r = client.get("/api/radio/board")
    assert r.status_code == 200
    d = r.json()
    assert d["net"]["ok"] is False and "VATSIM" in d["net"]["error"]
    assert all(s["owner"] is None for s in d["sectors"]) and d["online"] == {}
    assert all(r["owners"] == [] for s in d["stacks"] for r in s["rows"])
    assert len(d["sectors"]) == 30 and all(a["online"] is False for a in d["atis"])


def test_existing_radio_endpoints_kept(client):
    for path in ("/api/radio/positions", "/api/radio/online", "/api/radio/sectors", "/api/radio/loa"):
        assert client.get(path).status_code == 200, path


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


def test_stack_of_and_row_filter():
    assert rb.stack_of("EDWW", "EDUUOST1") == "EDUU" and rb.stack_of("edmm", "EDMMALB") == "EDMM"
    assert rb.stack_of("EKDK", "EKDKX") is None
    assert rb.acc_sectors({"sectors": {"B": {"LOW": ["EPWW_N_CTR"], "HIGH": ["EPWW_C_CTR", "EPWW_N_CTR"]}}}) == [
        {"id": "LB", "letter": "B", "layer": "LOW", "chain": ["EPWW_N_CTR"], "nominal": "EPWW_N_CTR"},
        {"id": "HB", "letter": "B", "layer": "HIGH", "chain": ["EPWW_C_CTR", "EPWW_N_CTR"], "nominal": "EPWW_C_CTR"}]
    assert rb._row_ok({"callsign": "LZTT_TWR", "hi": 11500}) and not rb._row_ok({"callsign": "EDDB_TWR", "hi": 5500})
    assert not rb._row_ok({"callsign": "EDDB_AN_APP", "hi": 5500}) and rb._row_ok({"callsign": "EDDB_N_APP", "hi": 10500})
    assert rb._row_ok({"callsign": "EYVL_FIS_CTR", "hi": 0}) and rb._row_ok({"callsign": "UMKK_FSS", "hi": 1000})


def _sq(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_border_label_point_simplify():
    border = rb.Border(_sq(14, 49, 24, 55))
    assert border.touches(_sq(10, 50, 14.1, 51))          # nachodzi na granicę
    assert border.touches(_sq(10, 50, 13.9, 51))          # ~7 km od granicy
    assert not border.touches(_sq(10, 50, 13.5, 51))      # ~34 km
    assert not border.touches(_sq(16, 51, 17, 52))        # w środku, daleko od krawędzi
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
const pj = rb.projection([14, 49, 24, 55], { x: 10, y: 20, w: 300, h: 300 });
console.log(JSON.stringify({
  ids: rb.LAYER_IDS, defaults: rb.DEFAULT_LAYERS, cut: rb.DEFAULT_CUT,
  args: [rb.argOf(L(["high", "atis", "low", "mid"]), 300), rb.argOf(L([]), 250), rb.argOf(L(["fis", "xx", "twra"]), 300)],
  parsed: ["atis,low,mid,high/fl300", "none/fl250", "app,bogus", "", "bogus/flx", "twr/FL95"].map((a) => {
    const p = rb.parseArg(a); return { layers: p.layers ? [...p.layers] : null, cut: p.cut };
  }),
  fl: [rb.flNum(29500), rb.flText(9500), rb.flText(0)],
  bounds, bands: [rb.cutBand(bounds, 300), rb.cutBand(bounds, 50), rb.cutBand(bounds, 400)],
  cutOf: [rb.cutOf(bounds, 0), rb.cutOf(bounds, 2)],
  sliceAt: rb.sliceAt([{ lo: 0, hi: 100 }, { lo: 100, hi: 200 }], 100),
  overlaps, first: moves[0],
  levels: rb.levelLines([{ lo: 1, hi: 5, c: "a" }, { lo: 1, hi: 5, c: "b" }, { lo: 0, hi: 5, c: "c" }]).map((l) => l.rows.map((r) => r.c)),
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
    assert js["cutOf"] == [100, 290]
    assert js["sliceAt"] == [{"lo": 100, "hi": 200}]  # dolna granica włącznie, górna nie


def test_js_layout_helpers(js):
    assert js["overlaps"] == 0 and js["first"] == {"dx": 0, "dy": 0}  # pierwsza (najważniejsza) zostaje w miejscu
    assert js["levels"] == [["a", "b"], ["c"]]
    assert js["short"] == ["FLG", "6", "LKAA", "", ""]
    a, b, box = js["proj"]["a"], js["proj"]["b"], js["proj"]["box"]
    assert a == pytest.approx([box["x"], box["y"]]) and b == pytest.approx([box["x"] + box["w"], box["y"] + box["h"]])
    assert 10 <= box["x"] and box["x"] + box["w"] <= 310 and 20 <= box["y"] and box["y"] + box["h"] <= 320
    assert js["bottom"]["y"] + js["bottom"]["h"] == pytest.approx(400)  # MID przy dolnej krawędzi
    assert js["path"] == "M0.0,0.0L10.0,0.0L10.0,10.0Z"
    assert js["bbox"] == [-1, 0, 2, 5]


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
