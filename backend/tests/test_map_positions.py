"""MAP, sekcja Pozycje (runda 12): drzewo stanowisk i zestaw aktywny (zalogowani z symulacją i ukrywaniem).

Miejsce stanowiska w drzewie liczy backend (services/positions.py), zestaw aktywny frontend (frontend/js/postree.js,
wykonany w Node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.services.positions import tree_place

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
    ({"callsign": "LKAA_FIC_FSS", "prefix": "LKAA", "fir": "LK", "facility": "FSS"}, ("LKAA", "FIS")),
    ({"callsign": "UMKK_CTR", "prefix": "UMKK", "fir": "UMKK", "facility": "CTR"}, ("UMKK", "ACC")),
])
def test_tree_place(p, want):
    assert tree_place(p) == want


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
        {"callsign": "EKDK_CTR", "frequency": "136.555", "fir": "EKDK", "group": "ACC", "order": 5},
        {"callsign": "EKDK_UN_CTR", "frequency": "136.555", "fir": "EKDK", "group": "ACC", "order": 5},
    ],
    "airports": {"EPGD": {"name": "Gdańsk", "lat": 54.38, "lon": 18.47}, "EPWA": {"name": "Chopina", "lat": 52.17, "lon": 20.97}},
}

NODE_SCRIPT = r"""
const t = await import(process.argv[2]);
const [online, atc, data] = JSON.parse(process.argv[3]);
const freq = (cs) => data.positions.find((p) => p.callsign === cs)?.frequency || "";
const pub = (a) => a && { positions: a.positions, live: [...a.live].sort(), gone: [...a.gone].sort(), sims: a.sims,
  controllers: a.controllers.map((c) => c.callsign), firs: Object.keys(a.firs).sort() };
const states = (tree) => Object.fromEntries(tree.flatMap((f) => f.groups.flatMap((g) => g.items.map((it) => [it.cs, it.state]))));
const out = {};
// domyślnie: zalogowani, nic nie zmienione
const base = { base: "online", ovr: {} };
out.def = pub(t.activeOnline(online, base, freq));
out.none0 = t.activeOnline(null, base, freq);
// symulacja EPGD_TWR i ukrycie EPWW_N_CTR i EKDK_CTR (razem z aliasem EKDK_UN_CTR) oraz zalogowanego spoza listy
let sel = t.toggle(base, "EPGD_TWR", true, online);
sel = t.toggle(sel, "EPWW_N_CTR", false, online);
sel = t.toggle(sel, "EKDK_CTR", false, online);
sel = t.toggle(sel, "EPWA_W_APP", false, online);
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
const sel2 = t.toggle(sel, "EKDK_UN_CTR", true, online);
out.alias = { ovr: sel2.ovr.EKDK_UN_CTR, on: !!t.activeOnline(online, sel2, freq).positions.EKDK_UN_CTR };
out.back = t.toggle(t.toggle(sel, "EPGD_TWR", false, online), "EPWW_N_CTR", true, online).ovr;
// "wyczyść": nikt aktywny, potem jedno stanowisko (zalogowane: prawdziwy kontroler, offline: symulacja)
let none = { base: "none", ovr: {} };
out.empty = pub(t.activeOnline(online, none, freq));
out.emptyNoNet = pub(t.activeOnline(null, none, freq));
none = t.toggle(t.toggle(none, "EPWA_APP", true, online), "EPWW_DBF_CTR", true, online);
const one = t.activeOnline(online, none, freq);
out.one = { ovr: none.ovr, positions: Object.keys(one.positions).sort(), sim: one.positions.EPWW_DBF_CTR, gone: [...one.gone].sort() };
// bez sieci: same symulacje
out.offline = pub(t.activeOnline(null, { base: "online", ovr: { EPGD_TWR: true } }, freq));
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
    r = subprocess.run([node, str(script), (d / "postree.mjs").as_uri(), json.dumps([ONLINE, ATC, DATA])],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


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
