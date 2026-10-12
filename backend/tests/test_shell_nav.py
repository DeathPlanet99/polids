"""Powłoka aplikacji (runda 10): kolejność menu jak w PANDORZE, przycisk ← (powrót), HOME z powitaniem, ABOUT,
nowe zakładki i adres ekranu "#zakładka/arg" (arg = wszystko po pierwszym "/")."""
import json
import re
import shutil
import subprocess

import pytest

from backend.app.config import ROOT_DIR

FRONTEND = ROOT_DIR / "frontend"
INDEX = (FRONTEND / "index.html").read_text("utf-8")
APP = (FRONTEND / "js" / "app.js").read_text("utf-8")

# INOP pod AD VFR, DOCS w OTHER (runda 11)
MENU = ["home", "radio", "meteo", "aerodrome", "adciv", "admil", "advfr", "inop", "callsign", "aircraft", "map",
        "phraseology", "checklist", "sytop", "aup", "strip", "other", "about", "emergency", "admin"]
LABELS = ["?", "RADIO", "METEO", "AERODROME", "AD CIV", "AD MIL", "AD VFR", "INOP", "CALLSIGN", "AIRCRAFT", "MAP",
          "PHRASEOLOGY", "CHECKLIST", "SYT OP", "AUP", "STRIP", "OTHER", "ABOUT", "EMERGENCY", "ADMIN"]


def _menu_buttons():
    nav = INDEX[INDEX.index('<nav id="tabs">'):INDEX.index("</nav>")]
    return re.findall(r"<button([^>]*)>([^<]*)</button>", nav)


def test_menu_order():
    buttons = _menu_buttons()
    # ← na samej górze, potem zakładki w kolejności z PANDORY; EMERGENCY na czerwono na dole, ADMIN ukryty
    assert 'id="nav-back"' in buttons[0][0] and buttons[0][1] == "←" and "disabled" in buttons[0][0]
    tabs = [(re.search(r'data-tab="([^"]+)"', a).group(1), label) for a, label in buttons[1:]]
    assert [t for t, _ in tabs] == MENU
    assert [label for _, label in tabs] == LABELS
    assert 'class="red"' in buttons[-2][0] and "hidden" in buttons[-1][0]
    assert 'class="brand" href="#home"' in INDEX


def test_every_tab_has_module():
    keys = re.search(r"const TABS = \{([^}]+)\}", APP).group(1)
    keys = [k.strip() for k in keys.split(",") if k.strip()]
    assert sorted(keys) == sorted(MENU)
    for key in MENU:
        if key in ("adciv", "admil", "advfr"):
            continue  # jeden moduł tabs/aip.js
        assert re.search(rf'^import {key} from "\./tabs/{key}\.js";$', APP, re.M), key
        assert (FRONTEND / "js" / "tabs" / f"{key}.js").is_file()
    # po uruchomieniu zawsze HOME
    assert 'open("home");' in APP and 'open("about");' not in APP


def test_docs_moved_to_other():
    """DOCS nie ma już w menu: to pierwsza podzakładka OTHER (#other/docs/<plik>); stare adresy #docs i #docs/<plik>
    app.js przekierowuje na OTHER › DOCS, a DOCS › LOA w RADIO otwiera PDF w OTHER."""
    assert not (FRONTEND / "js" / "tabs" / "docs.js").exists() and "docs" not in MENU
    assert 'const MOVED = { docs: (arg) => ["other", arg ? "docs/" + arg : "docs"] };' in APP
    assert "if (Object.hasOwn(MOVED, tab)) [tab, arg] = MOVED[tab](arg);" in APP
    other = (FRONTEND / "js" / "tabs" / "other.js").read_text("utf-8")
    assert 'import { mountDocs } from "../docs.js";' in other and "soon(" not in other
    subs = re.findall(r'\{ id: "([^"]+)", label: "([^"]+)"', other)
    assert subs[0] == ("docs", "DOCS")
    assert 'remember("other", argOf("docs"))' in other and "activate(arg)" in other
    docs = (FRONTEND / "js" / "docs.js").read_text("utf-8")
    assert "export function mountDocs(" in docs and "remember(" not in docs and "frameGo(" in docs
    radio = (FRONTEND / "js" / "tabs" / "radio.js").read_text("utf-8")
    assert 'navigate("other", "docs/" + doc.dataset.docs)' in radio and 'navigate("docs"' not in radio


def test_all_css_linked():
    """Każdy arkusz z frontend/css jest podpięty w index.html (zakładki rundy 10 dopisują style bez zmian index.html)."""
    linked = re.findall(r'href="/static/css/([^"]+\.css)"', INDEX)
    files = sorted(p.name for p in (FRONTEND / "css").glob("*.css"))
    assert sorted(linked) == files
    for name in ("home.css", "aerolist.css", "dman.css", "sytop.css", "meteoimg.css"):
        assert name in linked


NODE_SCRIPT = r"""
const api = await import(process.argv[2]);
const home = await import(process.argv[3]);
const hashes = ["#aerodrome/fir/ED", "#aerodrome/EPWA", "#meteo", "#home", "", "#", "#docs/LOA/LOA%20EPWW%20EDMM%20(AIRAC%202602).pdf",
  "#map/%E2%82%AC%", "#checklist/a/a2"];
const roundtrip = [["aerodrome", "fir/ED"], ["map", "LOT 3 #1 100%"], ["other", "docs/LOA/LOA EPWW ESAA Szwecja (v2.3, 19.02.2026).pdf"],
  ["meteo", ""], ["aircraft", "maker/airbus"]];
console.log(JSON.stringify({
  parsed: hashes.map((h) => api.parseHash(h)),
  built: roundtrip.map(([t, a]) => api.hashOf(t, a)),
  back: roundtrip.map(([t, a]) => api.parseHash(new URL("http://x/" + api.hashOf(t, a)).hash)),
  hello: [null, { user: null }, { user: { cid: "1234567", name: "Jan Kowalski" } }, { user: { cid: "1234567", name: null } },
    { user: { cid: 7, name: "<b>X</b>" } }].map(home.greeting),
  // bez routera (app.js) navigate i remember nic nie robią
  noop: [api.navigate("meteo", "qnh"), api.remember("meteo", "qnh"), api.stash("aerodrome", { q: "BER" }), api.stashed("aerodrome")],
}));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("js")
    (d / "api.mjs").write_text((FRONTEND / "js" / "api.js").read_text("utf-8"), "utf-8")
    (d / "home.mjs").write_text((FRONTEND / "js" / "tabs" / "home.js").read_text("utf-8").replace('"../api.js"', '"./api.mjs"'), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "api.mjs").as_uri(), (d / "home.mjs").as_uri()],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_parse_hash(js):
    # arg = wszystko po pierwszym "/", odkodowane; zły zapis %-owy zostaje jak jest
    assert js["parsed"] == [
        {"tab": "aerodrome", "arg": "fir/ED"}, {"tab": "aerodrome", "arg": "EPWA"}, {"tab": "meteo", "arg": ""},
        {"tab": "home", "arg": ""}, {"tab": "", "arg": ""}, {"tab": "", "arg": ""},
        {"tab": "docs", "arg": "LOA/LOA EPWW EDMM (AIRAC 2602).pdf"}, {"tab": "map", "arg": "%E2%82%AC%"},
        {"tab": "checklist", "arg": "a/a2"}]


def test_hash_roundtrip(js):
    assert js["built"][0] == "#aerodrome/fir/ED" and js["built"][3] == "#meteo" and js["built"][4] == "#aircraft/maker/airbus"
    assert "#" not in js["built"][1][1:] and " " not in js["built"][2]
    # adres zbudowany przez hashOf, przepuszczony przez przeglądarkowy URL, wraca jako ten sam arg
    assert [x["arg"] for x in js["back"]] == ["fir/ED", "LOT 3 #1 100%", "docs/LOA/LOA EPWW ESAA Szwecja (v2.3, 19.02.2026).pdf",
                                              "", "maker/airbus"]


def test_home_greeting(js):
    assert js["hello"] == ["Witaj", "Witaj", "Witaj, Jan Kowalski (1234567)", "Witaj, 1234567", "Witaj, <b>X</b> (7)"]
    # HOME pokazuje powitanie przez esc() (imię z VATSIM to dane z zewnątrz)
    assert "${esc(greeting(ctx.me))}" in (FRONTEND / "js" / "tabs" / "home.js").read_text("utf-8")
    assert js["noop"] == [None, None, None, None]


def test_aerodrome_uses_navigation_api():
    """Podzakładka (0, listy FIR-ów) i wybór lotniska to kroki do cofnięcia (remember), RUCH idzie przez dman.js."""
    src = (FRONTEND / "js" / "tabs" / "aerodrome.js").read_text("utf-8")
    assert "history.replaceState" not in src and 'remember("aerodrome", a)' in src
    assert "mountDman(" in src and "mountStrips(" not in src
    assert "export function mountDman(" in (FRONTEND / "js" / "dman.js").read_text("utf-8")
