"""AERODROME › AWOS: układ kolumn 1 PAS / 2 PASY, pasy kolumn bez ręcznego wyboru i ręczny wybór (awos.js, Node)."""
import json
import shutil
import subprocess

import pytest

from backend.app.config import ROOT_DIR

JS = ROOT_DIR / "frontend" / "js"

# EPWA 11/29 i 15/33, EPKK 07/25; składowe czołowe jak z METAR (status lotniska liczy je na serwerze)
NODE_SCRIPT = r"""
const { awosSim: m } = await import(process.argv[2]);
const RAD = Math.PI / 180;
const rw = (des, hdg, wd, ws) => ({ designator: des, heading: hdg,
  headwind: wd === null ? null : +(ws * Math.cos((wd - hdg) * RAD)).toFixed(1),
  crosswind: wd === null ? null : +(ws * Math.sin((wd - hdg) * RAD)).toFixed(1) });
const epwa = (wd, ws) => [rw("11", 115, wd, ws), rw("29", 295, wd, ws), rw("15", 152, wd, ws), rw("33", 332, wd, ws)];
const CFG = { arr: "33", dep: "29" };
const st = (u, runways, preferred = {}, runway_config = CFG) => ({ runway_in_use: u, runways, preferred, runway_config });
const pick = (s) => { const a = m.autoRunways(s); return { mode: m.autoMode(s.runway_in_use),
  ...Object.fromEntries(Object.entries(a).map(([k, v]) => [k, [v.des, v.src]])), why: a.dep.why }; };
const cases = {
  atisSplit: st({ arr: "33", dep: "29", source: "ATIS" }, epwa(310, 14), { arr: "33", dep: "33" }),
  atisOneLvp: st({ arr: "29", dep: "29", source: "ATIS" }, epwa(310, 14), { arr: "33", dep: "33", reason: "Warunki IMC: kierunek z ILS" }),
  atisOneCalm: st({ arr: "29", dep: "29", source: "ATIS" }, epwa(300, 3), { arr: "33", dep: "29", reason: "ARR 33 wg AIP (H24); DEP 29 wg AIP (H24)" }),
  atisEast: st({ arr: "11", dep: "11", source: "ATIS" }, epwa(130, 15), { arr: "11", dep: "11" }),
  cfgTail: st({ arr: "29", dep: "29", source: "ATIS" }, epwa(150, 15), { arr: "15", dep: "15" }),
  noCfg: st({ arr: "29", dep: "29", source: "ATIS" }, epwa(300, 12), {}, { arr: null, dep: null }),
  sugSplit: st({ arr: "33", dep: "29", source: "SUGGESTED" }, epwa(300, 3), { arr: "33", dep: "29" }),
  epkk: st({ arr: "25", dep: "25", source: "SUGGESTED" }, [rw("07", 78, 240, 8), rw("25", 258, 240, 8)], { arr: "25", dep: "25" }, {}),
  noWind: st({ arr: "29", dep: "29", source: "ATIS" }, epwa(null, null), {}, {}),
  empty: st({ arr: null, dep: null, source: "SUGGESTED" }, [], {}, {}),
};
// ręczny wybór: EPWA z ATIS 33/29 (automat 2 PASY) i z ATIS 29 (automat 1 PAS)
const T = Date.UTC(2026, 9, 12, 16, 0), H = 3600000, split = cases.atisSplit, one = cases.atisOneCalm;
const clicks = (s, list, m0 = {}) => { let x = m0; return list.map(([k, v]) => (x = m.manualPick(x, s, k, v, T))); };
const dep11 = m.manualPick({}, split, "dep", "11", T);
const splitArr15 = clicks(one, [["mode", "split"], ["arr", "15"]])[1];
const manual = {
  repro: clicks(split, [["mode", "one"], ["rwy", "29"], ["mode", "split"]]),
  leaveSplit: clicks(one, [["mode", "split"], ["arr", "15"], ["mode", "one"]]),
  dep11, depAuto: m.manualPick({}, split, "dep", "29", T), depBack: m.manualPick(dep11, split, "dep", "29", T),
  sameMode: m.manualPick(dep11, split, "mode", "split", T + H), autoBtn: m.manualPick(splitArr15, one, null, null, T),
  keepFresh: m.manualKeep(dep11, split, T + 11 * H), keepSug: m.manualKeep(dep11, cases.sugSplit, T),
  keepAtis: m.manualKeep(dep11, one, T + 60000), keepAtis2: m.manualKeep(splitArr15, split, T + 60000),
  keepOld: m.manualKeep(dep11, split, T + 12 * H), keepLegacy: m.manualKeep({ mode: "one", rwy: "29" }, split, T),
  keepBad: m.manualKeep({ ...dep11, arr: "99", mode: "xyz", foo: 1 }, split, T),
  keepNone: m.manualKeep({ dep: "99", base: "33/29", t: T }, split, T), keepNull: m.manualKeep(null, split, T),
};
console.log(JSON.stringify({
  ...Object.fromEntries(Object.entries(cases).map(([k, v]) => [k, pick(v)])),
  bare: [m.autoMode(undefined), m.autoMode({}), m.autoMode({ arr: "33" }), m.autoMode({ arr: "33", dep: "29" })],
  nostatus: m.autoRunways(null).rwy, T, H, manual,
}));
"""


@pytest.fixture(scope="module")
def awos(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("awos")
    (d / "api.mjs").write_text((JS / "api.js").read_text("utf-8"), "utf-8")
    (d / "meteo.mjs").write_text("export const colorize = (x) => x; export const wxLines = (k, x) => x;\n", "utf-8")
    src = (JS / "awos.js").read_text("utf-8").replace('"./api.js"', '"./api.mjs"').replace('"./tabs/meteo.js"', '"./meteo.mjs"')
    (d / "awos.mjs").write_text(src, "utf-8")
    (d / "run.mjs").write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(d / "run.mjs"), (d / "awos.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_auto_mode(awos):
    """2 PASY tylko przy różnych pasach do lądowania i startu (ATIS albo sugestia), inaczej 1 PAS."""
    assert awos["bare"] == ["one", "one", "one", "split"]
    assert awos["atisSplit"]["mode"] == "split" and awos["sugSplit"]["mode"] == "split"
    assert all(awos[k]["mode"] == "one" for k in ("atisOneLvp", "atisOneCalm", "atisEast", "epkk", "empty"))


def test_split_from_atis(awos):
    """ATIS z dwoma pasami: DEP i ARR z ATIS, 1 PAS pokazuje pas do lądowania."""
    a = awos["atisSplit"]
    assert (a["arr"], a["dep"], a["rwy"]) == (["33", "ATIS"], ["29", "ATIS"], ["33", "ATIS"])
    s = awos["sugSplit"]
    assert (s["arr"], s["dep"]) == (["33", "sugestia"], ["29", "sugestia"])


def test_second_runway_when_atis_has_one(awos):
    """ATIS z jednym pasem (2 PASY wybrane ręcznie): pas z ATIS zostaje, drugi z rozdziału, który go zawiera."""
    # sugestia systemu (pasy preferowane z AIP AD 2.21): ARR 33 / DEP 29
    c = awos["atisOneCalm"]
    assert (c["arr"], c["dep"], c["rwy"]) == (["33", "sugestia"], ["29", "ATIS"], ["29", "ATIS"])
    # sugestia bez rozdziału (jeden pas do lądowania i startu): konfiguracja preferowana EPWA z runway_config.json
    lvp = awos["atisOneLvp"]
    assert (lvp["arr"], lvp["dep"]) == (["33", "auto"], ["29", "ATIS"])
    # pas spoza konfiguracji: do lądowania pas z ATIS, do startu inny pas z największą składową czołową
    e = awos["atisEast"]
    assert (e["arr"], e["dep"]) == (["11", "ATIS"], ["15", "auto"]) and "czołową" in e["why"]
    # drugi pas konfiguracji z wiatrem w plecy > 5 kt odpada
    t = awos["cfgTail"]
    assert (t["arr"], t["dep"]) == (["29", "ATIS"], ["15", "auto"])
    n = awos["noCfg"]
    assert (n["arr"], n["dep"]) == (["29", "ATIS"], ["33", "auto"])
    # bez wiatru: pierwszy kierunek innego pasa z listy
    assert awos["noWind"]["dep"] == ["15", "auto"]


def test_single_runway_and_no_data(awos):
    """Lotnisko z jednym pasem: w 2 PASY druga kolumna to przeciwny kierunek; bez pasów nic się nie wywraca."""
    k = awos["epkk"]
    assert (k["arr"], k["dep"], k["rwy"]) == (["25", "sugestia"], ["07", "auto"], ["25", "sugestia"])
    assert "jednym pasem" in k["why"]
    assert awos["empty"]["rwy"] == [None, "sugestia"] and awos["empty"]["dep"] == [None, "sugestia"]
    assert awos["nostatus"]["des"] is None


def test_manual_pick_follows_screen(awos):
    """Ręczny wybór: zapamiętane jest tylko to, co na ekranie; zmiana układu kasuje pasy opuszczanego układu."""
    m, T = awos["manual"], awos["T"]
    base = {"base": "33/29", "t": T}
    # ATIS 33/29 (automat 2 PASY): 1 PAS, RWY 29, z powrotem 2 PASY = automat (AUTO, bez zapamiętanego RWY 29)
    assert m["repro"] == [{"mode": "one", **base}, {"mode": "one", "rwy": "29", **base}, {}]
    # ATIS 29 (automat 1 PAS): ręcznie 2 PASY z ARR 15, powrót do 1 PAS kasuje też ARR 15
    one = {"base": "29/29", "t": T}
    assert m["leaveSplit"] == [{"mode": "split", **one}, {"mode": "split", "arr": "15", **one}, {}]
    assert m["autoBtn"] == {}
    # pas jak w automacie to powrót do automatu; kliknięcie widocznego układu zostawia pasy i odnawia czas
    assert m["dep11"] == {"dep": "11", **base}
    assert m["depAuto"] == {} and m["depBack"] == {}
    assert m["sameMode"] == {"dep": "11", "base": "33/29", "t": T + awos["H"]}


def test_manual_keep_lapses(awos):
    """Ręczny wybór wraca do automatu przy zmianie pasów w użyciu (ATIS / sugestia) i po 12 h; zły pas odpada."""
    m = awos["manual"]
    assert m["keepFresh"] == m["dep11"]
    # te same pasy z sugestii zamiast ATIS: wybór zostaje
    assert m["keepSug"] == m["dep11"]
    # ATIS 33/29 -> 29 i 29 -> 33/29: wybór z poprzedniego ATIS nie przykrywa nowego
    assert m["keepAtis"] == {} and m["keepAtis2"] == {}
    assert m["keepOld"] == {}
    # zapis bez pasów automatu i czasu (wersja sprzed wygasania) wraca do automatu
    assert m["keepLegacy"] == {}
    assert m["keepBad"] == m["dep11"]
    assert m["keepNone"] == {} and m["keepNull"] == {}
