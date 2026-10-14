"""MAP, sekcja Pozycje (runda 12): drzewo stanowisk i zestaw aktywny (zalogowani z symulacją i ukrywaniem).

Miejsce stanowiska w drzewie liczy backend (services/positions.py), zestaw aktywny frontend (frontend/js/postree.js,
wykonany w Node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.services.positions import sim_twins, tree_place
from backend.app.services.radio import Station

JS = Path(__file__).resolve().parents[2] / "frontend" / "js"


@pytest.mark.parametrize("p,want", [
    ({"callsign": "EPWW_DBF_CTR", "prefix": "EPWW", "facility": "CTR"}, ("EPWW", "ACC")),
    ({"callsign": "EPGD_L_APP", "prefix": "EPGD", "facility": "APP"}, ("EPWW", "EPGD")),
    ({"callsign": "EPWA_I_APP", "prefix": "EPWA", "facility": "APP"}, ("EPWW", "FIS")),
    ({"callsign": "EPBC_I_TWR", "prefix": "EPBC", "facility": "TWR"}, ("EPWW", "EPBC")),  # AFIS lotniska
    ({"callsign": "EDWW_FLG_CTR", "prefix": "EDWW", "fir": "EDWW", "facility": "CTR"}, ("EDWW", "ACC")),
    ({"callsign": "EDDB_N_TWR", "prefix": "EDDB", "fir": "EDWW", "facility": "TWR"}, ("EDWW", "EDDB")),
    ({"callsign": "EDYY_CL_CTR", "prefix": "EDYY", "fir": "EDWW", "facility": "CTR"}, ("EDUU", "ACC")),
    ({"callsign": "EDGG_BAD_CTR", "prefix": "EDGG", "fir": "EDMM", "facility": "CTR"}, ("EDGG", "ACC")),
    # stanowiska z pakietu EDGG.ese (fir EDGG) bez prefiksu EDGG: lotnisko w FIR Langen, nie "inne"
    ({"callsign": "EDDS_STG_APP", "prefix": "EDDS", "fir": "EDGG", "facility": "APP"}, ("EDGG", "EDDS")),
    ({"callsign": "ETHN_TWR", "prefix": "ETHN", "fir": "EDGG", "facility": "TWR"}, ("EDGG", "ETHN")),
    ({"callsign": "LKAA_FIC_FSS", "prefix": "LKAA", "fir": "LK", "facility": "FSS"}, ("LKAA", "FIS")),
    ({"callsign": "UMKK_CTR", "prefix": "UMKK", "fir": "UMKK", "facility": "CTR"}, ("UMKK", "ACC")),
])
def test_tree_place(p, want):
    assert tree_place(p) == want


def test_sim_twins():
    """Drugi znak do symulacji: ten sam, pod którym match_positions wpisze zalogowanego (alias_of)."""
    pos = [Station("EKDK_A_CTR", "EKDK", "128.100"), Station("EKDK_CTR", "EKDK", "136.555"),
           Station("EKCH_F_APP", "EKCH", "120.205"), Station("EKDK_UN_CTR", "EKDK", "136.555"),
           Station("EKCH_P_APP", "EKCH", "120.205"), Station("EKDK_B_CTR", "EKDK", "121.500")]
    prefer = {"EKDK_A_CTR", "EKDK_UN_CTR", "EKCH_P_APP"}
    assert sim_twins(pos, prefer) == {"EKDK_CTR": "EKDK_UN_CTR", "EKCH_F_APP": "EKCH_P_APP"}
    assert sim_twins(pos, set()) == {}


ONLINE = {
    "positions": {
        "EPWW_N_CTR": {"callsign": "EPWW_N_CTR", "frequency": "130.675", "name": "Anna"},
        "EPWA_APP": {"callsign": "EPWA_APP", "frequency": "128.805", "name": "Jan"},
        "EKDK_CTR": {"callsign": "EKDK_CTR", "frequency": "136.555", "name": "Lars"},
        # ten sam kontroler pod znakiem z pliku .ese Danii (dopasowanie match_positions)
        "EKDK_UN_CTR": {"callsign": "EKDK_CTR", "frequency": "136.555", "name": "Lars", "alias_of": "EKDK_CTR"},
    },
    "controllers": [{"callsign": "EPWW_N_CTR", "frequency": "130.675"}, {"callsign": "EPWA_APP", "frequency": "128.805"},
                    {"callsign": "EPWA_W_APP", "frequency": "135.125"}, {"callsign": "EPKK_OBS", "frequency": "199.998"}],
    "firs": {"EPWW-N": [{"callsign": "EPWW_N_CTR"}], "EKDK": [{"callsign": "EKDK_CTR"}]},
}
ATC = {"airports": [{"icao": "EPWA", "name": "Chopina", "lat": 52.17, "lon": 20.97,
                     "facilities": {"APP": [{"callsign": "EPWA_APP"}, {"callsign": "EPWA_W_APP"}]}}]}
DATA = {
    "firs": [{"id": "EPWW", "label": "EPWW", "name": "FIR Warszawa"}, {"id": "EKDK", "label": "EKDK", "name": "FIR København"}],
    "positions": [
        {"callsign": "EPWW_N_CTR", "frequency": "130.675", "fir": "EPWW", "group": "ACC", "order": 5},
        {"callsign": "EPWW_DBF_CTR", "frequency": "133.180", "fir": "EPWW", "group": "ACC", "order": 5},
        {"callsign": "EPGD_TWR", "frequency": "118.105", "fir": "EPWW", "group": "EPGD", "order": 1},
        {"callsign": "EPGD_APP", "frequency": "127.280", "fir": "EPWW", "group": "EPGD", "order": 0},
        {"callsign": "EPWA_APP", "frequency": "128.805", "fir": "EPWW", "group": "EPWA", "order": 0},
        {"callsign": "EPWA_I_APP", "frequency": "123.975", "fir": "EPWW", "group": "FIS", "order": 0},
        {"callsign": "EKDK_CTR", "frequency": "136.555", "fir": "EKDK", "group": "ACC", "order": 5, "twin": "EKDK_UN_CTR"},
        {"callsign": "EKDK_UN_CTR", "frequency": "136.555", "fir": "EKDK", "group": "ACC", "order": 5},
    ],
    "airports": {"EPGD": {"name": "Gdańsk", "lat": 54.38, "lon": 18.47}, "EPWA": {"name": "Chopina", "lat": 52.17, "lon": 20.97}},
}
# granice FIR-ów z VATSpy (/api/vatsim/firs): prefiksy FIR-ów i UIR (najdłuższy pasujący wygrywa, jak online_firs)
VATSPY = {
    "features": [{"properties": {"id": "EPWW", "prefixes": ["EPWW"]}}, {"properties": {"id": "EPWW-N", "prefixes": ["EPWW_N"]}},
                 {"properties": {"id": "EKDK", "prefixes": ["EKDK"]}}, {"properties": {"id": "LKAA", "prefixes": ["LKAA"]}}],
    "uirs": [{"id": "EUC-EN", "firs": ["EPWW", "LKAA"]}],
}

NODE_SCRIPT = r"""
const t = await import(process.argv[2]);
const [online, atc, data, vatspy] = JSON.parse(process.argv[3]);
const posOf = (cs) => data.positions.find((p) => p.callsign === cs) || null;
const freq = { posOf, firOf: (cs) => t.firsOf(cs, vatspy) };
const pub = (a) => a && { positions: a.positions, live: [...a.live].sort(), gone: [...a.gone].sort(), sims: a.sims,
  controllers: a.controllers.map((c) => c.callsign), firs: Object.keys(a.firs).sort() };
const states = (tree) => Object.fromEntries(tree.flatMap((f) => f.groups.flatMap((g) => g.items.map((it) => [it.cs, it.state]))));
const out = {};
// domyślnie: zalogowani, nic nie zmienione
const base = { base: "online", ovr: {} };
out.def = pub(t.activeOnline(online, base, freq));
out.none0 = t.activeOnline(null, base, freq);
// symulacja EPGD_TWR i ukrycie EPWW_N_CTR i EKDK_CTR (razem z aliasem EKDK_UN_CTR) oraz zalogowanego spoza listy
let sel = t.toggle(base, "EPGD_TWR", true, online, freq);
sel = t.toggle(sel, "EPWW_N_CTR", false, online, freq);
sel = t.toggle(sel, "EKDK_CTR", false, online, freq);
sel = t.toggle(sel, "EPWA_W_APP", false, online, freq);
out.sel = sel;
const act = t.activeOnline(online, sel, freq);
out.act = pub(act);
out.atc = t.activeAtc(atc, act, data.airports);
const tree = t.buildTree(data, online, act, (cs) => cs.replace(/_/g, " "));
out.firs = tree.map((f) => f.id);
out.groups = tree[0].groups.map((g) => g.id);
out.gd = tree[0].groups.find((g) => g.id === "EPGD").items.map((it) => it.cs);
out.states = states(tree);
out.count = t.countOf(tree[0].groups.flatMap((g) => g.items));
// alias ukryty razem z EKDK_CTR: włączenie aliasu to jawne "pokaż"; powrót do stanu z sieci usuwa odstępstwo
const sel2 = t.toggle(sel, "EKDK_UN_CTR", true, online, freq);
out.alias = { ovr: sel2.ovr.EKDK_UN_CTR, on: !!t.activeOnline(online, sel2, freq).positions.EKDK_UN_CTR };
out.back = t.toggle(t.toggle(sel, "EPGD_TWR", false, online, freq), "EPWW_N_CTR", true, online, freq).ovr;
// "wyczyść": nikt aktywny, potem jedno stanowisko (zalogowane: prawdziwy kontroler, offline: symulacja)
let none = { base: "none", ovr: {} };
out.empty = pub(t.activeOnline(online, none, freq));
out.emptyNoNet = pub(t.activeOnline(null, none, freq));
none = t.toggle(t.toggle(none, "EPWA_APP", true, online, freq), "EPWW_DBF_CTR", true, online, freq);
const one = t.activeOnline(online, none, freq);
out.one = { ovr: none.ovr, positions: Object.keys(one.positions).sort(), sim: one.positions.EPWW_DBF_CTR, gone: [...one.gone].sort() };
// bez sieci: same symulacje; znak spoza listy stanowisk (np. dawny zalogowany spoza listy) nie jest symulowany
out.offline = pub(t.activeOnline(null, { base: "online", ovr: { EPGD_TWR: true, EPWA_X_APP: true } }, freq));
// "wyczyść" i zalogowany spoza listy (EPWA_W_APP): prawdziwy kontroler, nie symulacja; po wylogowaniu znika
const stray = t.toggle({ base: "none", ovr: {} }, "EPWA_W_APP", true, online, freq);
const sa = t.activeOnline(online, stray, freq);
out.stray = { ovr: stray.ovr, positions: Object.keys(sa.positions), sims: sa.sims, live: [...sa.live], controllers: sa.controllers.map((c) => c.callsign),
  state: states(t.buildTree(data, online, sa)).EPWA_W_APP, atc: t.activeAtc(atc, sa, data.airports).airports.map((ap) => [ap.icao, !!ap.sim, ap.facilities]) };
const left = { ...online, controllers: online.controllers.filter((c) => c.callsign !== "EPWA_W_APP") };
const sl = t.activeOnline(left, stray, freq);
out.strayGone = { positions: Object.keys(sl.positions), sims: sl.sims, live: [...sl.live] };
// "wyczyść" i EKDK_CTR: drugi znak (alias_of EKDK_UN_CTR) włączony razem z nim; odznaczenie drugiego znaku: osobno
const ek = t.toggle({ base: "none", ovr: {} }, "EKDK_CTR", true, online, freq);
const ea = t.activeOnline(online, ek, freq), es = states(t.buildTree(data, online, ea));
const ek2 = t.toggle(ek, "EKDK_UN_CTR", false, online, freq);
out.aliasNone = { positions: Object.keys(ea.positions).sort(), states: [es.EKDK_CTR, es.EKDK_UN_CTR], firs: Object.keys(ea.firs),
  off: { ovr: ek2.ovr, positions: Object.keys(t.activeOnline(online, ek2, freq).positions).sort() } };
// symulacja EKDK_CTR bez nikogo w sieci: wpisana też pod drugim znakiem (twin), jak zalogowany; odznaczenie drugiego znaku
const off = { positions: {}, controllers: [], firs: {} };
const ts = t.toggle(base, "EKDK_CTR", true, off, freq), ta = t.activeOnline(off, ts, freq), tst = states(t.buildTree(data, off, ta));
const ts2 = t.toggle(ts, "EKDK_UN_CTR", false, off, freq);
out.twin = { ovr: ts.ovr, un: ta.positions.EKDK_UN_CTR, sims: ta.sims, states: [tst.EKDK_CTR, tst.EKDK_UN_CTR], live: [...ta.live],
  off: { ovr: ts2.ovr, positions: Object.keys(t.activeOnline(off, ts2, freq).positions) },
  taken: t.activeOnline({ ...off, positions: { EKDK_UN_CTR: { callsign: "EKDK_UN_CTR", frequency: "136.555", name: "Ole" } } }, ts, freq).positions.EKDK_UN_CTR.name };
// symulowane ACC EPWW: plakietka CTR FIR-u (VATSpy) jak zalogowany, przerywana (sim)
const fa = t.activeOnline(off, t.toggle(base, "EPWW_N_CTR", true, off, freq), freq);
out.firBadge = { sim: fa.firs, of: ["EPWW_N_CTR", "EPWW_CTR", "LKAA_CTR", "EPWA_APP", "EKDK_UN_CTR", "XXXX_CTR"].map((cs) => t.firsOf(cs, vatspy)),
  noData: t.firsOf("EPWW_N_CTR", null) };
// ta sama krótka nazwa dwóch znaków w grupie (nazwa bez CTR/FSS jak posName): z rodzajem z końca znaku, reszta bez zmian
const dd = { firs: data.firs, positions: ["EKDK_F_CTR", "EKDK_F_FSS", "EKDK_Y_CTR"].map((cs) => ({ callsign: cs, frequency: "129.475", fir: "EKDK", group: "ACC" })) };
out.dedup = t.buildTree(dd, null, null, (cs) => cs.replace(/_(CTR|FSS)$/, "").replace(/_/g, " "))[0].groups[0].items.map((it) => it.name);
// wyszukiwanie
const g = tree[0].groups.find((x) => x.id === "EPGD"), f = tree[0];
out.match = [t.matchItem(g.items[0], g, f, "gd app"), t.matchItem(g.items[0], g, f, "127.28"), t.matchItem(g.items[0], g, f, "gdańsk"),
  t.matchItem(g.items[0], g, f, "twr")];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("postree")
    (d / "postree.mjs").write_text((JS / "postree.js").read_text("utf-8"), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "postree.mjs").as_uri(), json.dumps([ONLINE, ATC, DATA, VATSPY])],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_tree_duplicate_names(js):
    # EKDK_F_CTR (EKDK.ese) i EKDK_F_FSS (vacs-data) na tej samej częstotliwości: dwa różne wiersze, nie dwa "EKDK F"
    assert js["dedup"] == ["EKDK F CTR", "EKDK F FSS", "EKDK Y"]


def test_default_is_online(js):
    # bez zmian zestaw aktywny = zalogowani; zalogowany spoza listy (EPWA_W_APP) też aktywny, obserwator pomijany
    d = js["def"]
    assert sorted(d["positions"]) == ["EKDK_CTR", "EKDK_UN_CTR", "EPWA_APP", "EPWW_N_CTR"]
    assert "EPWA_W_APP" in d["live"] and d["gone"] == [] and d["sims"] == []
    assert d["controllers"] == ["EPWW_N_CTR", "EPWA_APP", "EPWA_W_APP", "EPKK_OBS"]
    assert js["none0"] is None  # błąd VATSIM bez symulacji: podział pełny jak dotąd


def test_simulate_and_hide(js):
    assert js["sel"] == {"base": "online", "ovr": {"EPGD_TWR": True, "EPWW_N_CTR": False, "EKDK_CTR": False, "EPWA_W_APP": False}}
    a = js["act"]
    # EKDK_UN_CTR to ten sam kontroler co EKDK_CTR: znika razem z nim; symulacja z częstotliwością stanowiska
    assert sorted(a["positions"]) == ["EPGD_TWR", "EPWA_APP"]
    assert a["positions"]["EPGD_TWR"] == {"callsign": "EPGD_TWR", "frequency": "118.105", "name": "symulacja", "sim": True}
    assert a["gone"] == ["EKDK_CTR", "EPWA_W_APP", "EPWW_N_CTR"] and a["sims"] == ["EPGD_TWR"]
    # ukryci znikają z listy zalogowanych i z plakietek FIR-ów (VATSpy)
    assert a["controllers"] == ["EPWA_APP", "EPKK_OBS"] and a["firs"] == []


def test_airport_badges(js):
    # plakietki: bez ukrytego EPWA_W_APP, symulowana wieża w Gdańsku (lotnisko bez nikogo online, położenie z listy)
    aps = {ap["icao"]: ap for ap in js["atc"]["airports"]}
    assert [c["callsign"] for c in aps["EPWA"]["facilities"]["APP"]] == ["EPWA_APP"] and not aps["EPWA"].get("sim")
    assert aps["EPGD"]["sim"] and aps["EPGD"]["lat"] == 54.38 and aps["EPGD"]["facilities"]["TWR"][0]["sim"]


def test_tree(js):
    # EPWW najpierw; w FIR-ze ACC, FIS, potem lotniska; na lotnisku APP przed TWR; zalogowany spoza listy w swojej grupie
    assert js["firs"] == ["EPWW", "EKDK"] and js["groups"] == ["ACC", "FIS", "EPGD", "EPWA"]
    assert js["gd"] == ["EPGD_APP", "EPGD_TWR"]
    s = js["states"]
    assert (s["EPGD_TWR"], s["EPWW_N_CTR"], s["EPWA_APP"], s["EPWW_DBF_CTR"]) == ("sim", "hid", "on", "off")
    assert (s["EPWA_W_APP"], s["EKDK_UN_CTR"]) == ("hid", "hid")
    assert js["count"] == {"act": 2, "sim": 1, "onl": 3}


def test_toggle_back_and_alias(js):
    assert js["alias"] == {"ovr": True, "on": True}
    assert js["back"] == {"EKDK_CTR": False, "EPWA_W_APP": False}  # stan zgodny z siecią: bez odstępstwa


def test_clear_then_pick(js):
    # "wyczyść": nikogo nie rysujemy (także bez danych z sieci), potem pojedyncze stanowiska
    assert js["empty"]["positions"] == {} and js["empty"]["controllers"] == ["EPKK_OBS"] and js["empty"]["firs"] == []
    assert js["emptyNoNet"]["positions"] == {}
    o = js["one"]
    assert o["ovr"] == {"EPWA_APP": True, "EPWW_DBF_CTR": True} and o["positions"] == ["EPWA_APP", "EPWW_DBF_CTR"]
    assert o["sim"]["sim"] and "EPWW_N_CTR" in o["gone"] and "EPWA_APP" not in o["gone"]


def test_simulation_without_network(js):
    assert sorted(js["offline"]["positions"]) == ["EPGD_TWR"] and js["offline"]["sims"] == ["EPGD_TWR"]


def test_search(js):
    # słowa w znaku / nazwie, częstotliwość, nazwa lotniska; wszystkie słowa muszą pasować
    assert js["match"] == [True, True, True, False]


def test_clear_then_stray(js):
    # "wyczyść" i zalogowany spoza listy: rysowany jako prawdziwy kontroler (obsada TMA przez unmatched w MAP), bez SYM
    st = js["stray"]
    assert st["ovr"] == {"EPWA_W_APP": True} and st["positions"] == [] and st["sims"] == []
    assert st["live"] == ["EPWA_W_APP"] and st["controllers"] == ["EPWA_W_APP", "EPKK_OBS"] and st["state"] == "on"
    assert st["atc"] == [["EPWA", False, {"APP": [{"callsign": "EPWA_W_APP"}]}]]
    # po wylogowaniu nie zostaje symulacją (nie ma go w drzewie, nie dałoby się jej wyłączyć)
    assert js["strayGone"] == {"positions": [], "sims": [], "live": []}


def test_alias_follows_after_clear(js):
    # po "wyczyść" włączenie EKDK_CTR włącza też EKDK_UN_CTR (ten sam kontroler; to jego znak jest w sektorach Danii)
    a = js["aliasNone"]
    assert a["positions"] == ["EKDK_CTR", "EKDK_UN_CTR"] and a["states"] == ["on", "on"] and a["firs"] == ["EKDK"]
    assert a["off"] == {"ovr": {"EKDK_CTR": True, "EKDK_UN_CTR": False}, "positions": ["EKDK_CTR"]}


def test_simulated_twin(js):
    # symulacja EKDK_CTR: ten sam symulowany kontroler pod EKDK_UN_CTR (jak alias_of zalogowanego), jedna symulacja
    tw = js["twin"]
    assert tw["ovr"] == {"EKDK_CTR": True} and tw["sims"] == ["EKDK_CTR"] and tw["live"] == ["EKDK_CTR"]
    assert tw["un"] == {"callsign": "EKDK_CTR", "frequency": "136.555", "name": "symulacja", "sim": True, "alias_of": "EKDK_CTR"}
    assert tw["states"] == ["sim", "sim"]
    assert tw["off"] == {"ovr": {"EKDK_CTR": True, "EKDK_UN_CTR": False}, "positions": ["EKDK_CTR"]}
    assert tw["taken"] == "Ole"  # na drugim znaku ktoś naprawdę siedzi: zostaje on


def test_simulated_acc_fir_badge(js):
    f = js["firBadge"]
    assert f["sim"] == {"EPWW-N": [{"callsign": "EPWW_N_CTR", "frequency": "130.675", "name": "symulacja", "sim": True}]}
    assert f["of"] == [["EPWW-N"], ["EPWW"], ["LKAA"], [], ["EKDK"], []] and f["noData"] == []
