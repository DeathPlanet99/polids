"""SYT OP: lista NOTAM-ów FIR EPWW obowiązujących teraz (services/notam.py: fir_notams, GET /api/notam/fir/EPWW).

Serwer NOTAM PL vACC jest podstawiony (fetch_text), więc testy działają bez sieci."""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("POLIDS_DATABASE_URL",
                      "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.services import notam  # noqa: E402
from backend.app.services.http_cache import UpstreamError  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def t(hours, now=NOW):
    """Pole B)/C) (RRMMDDggmm) przesunięte o tyle godzin względem teraz."""
    return (now + timedelta(hours=hours)).strftime("%y%m%d%H%M")


def server(now=NOW):
    """Odpowiedzi serwera NOTAM dla poszczególnych lokalizacji (jak cv.plvacc.pl: w odpowiedzi także cudze NOTAM-y)."""
    fir_area = (f"(A2001/26 NOTAMN\nQ) EPWW/QRTCA/IV/BO/W/000/095/5230N01900E010\nA) EPWW B) {t(-3, now)} C) {t(5, now)}\n"
                "D) 0800-1600\nE) TEMPO RESTRICTED AREA EP TRA 08 ACT\nF) SFC G) FL095)")
    fir_uas = (f"(B0150/26 NOTAMN\nQ) EPWW/QWULW/IV/BO/W/000/012/5212N02058E003\nA) EPWW B) {t(-1, now)} C) {t(30, now)}EST\n"
               "E) UAS FLIGHTS WILL TAKE PLACE\nF) SFC G) 1200FT AMSL)")
    fir_future = (f"(A2005/26 NOTAMN\nQ) EPWW/QRDCA/IV/BO/W/000/100/5300N01800E010\nA) EPWW B) {t(20, now)} C) {t(26, now)}\n"
                  "E) EP D 10 ACT)")
    fir_old = (f"(A1900/26 NOTAMN\nQ) EPWW/QOBCE/IV/M/AE/000/010/5230N01900E005\nA) EPWW B) {t(-90, now)} C) {t(-2, now)}\n"
               "E) CRANE)")
    wa_rwy = (f"(A1234/26 NOTAMN\nQ) EPWW/QMRLC/IV/NBO/A/000/999/5210N02058E005\nA) EPWA B) {t(-6, now)} C) {t(6, now)}\n"
              "E) RWY 11/29 CLSD DUE TO WIP)")
    wa_twy = (f"(A1240/26 NOTAMN\nQ) EPWW/QMXLC/IV/M/A/000/999/5210N02058E005\nA) EPWA B) {t(-720, now)} C) PERM\n"
              "E) TWY M BTN M1 AND M3 CLSD)")
    wa_replaced = (f"(A1230/26 NOTAMN\nQ) EPWW/QMXLC/IV/M/A/000/999/5210N02058E005\nA) EPWA B) {t(-50, now)} C) {t(50, now)}\n"
                   "E) TWY A CLSD)")
    wa_repl = (f"(A1300/26 NOTAMR A1230/26\nQ) EPWW/QMXLC/IV/M/A/000/999/5210N02058E005\nA) EPWA B) {t(-1, now)} C) {t(50, now)}\n"
               "E) TWY A CLSD BTN A1 AND A2)")
    wa_cnl = (f"(A1301/26 NOTAMC A1240/26\nQ) EPWW/QMXXX/IV/M/A/000/999/5210N02058E005\nA) EPWA B) {t(-1, now)}\n"
              "E) TWY M BTN M1 AND M3 OPEN)")
    mo = (f"(A1260/26 NOTAMN\nQ) EPWW/QMNLC/IV/NBO/A/000/999/5245N02065E005\nA) EPMO B) {t(-20, now)} C) {t(12, now)}\n"
          "E) APRON STAND 5 CLSD DUE TO MAINT)")
    small = (f"(A1270/26 NOTAMN\nQ) EPWW/QFALC/IV/NBO/A/000/999/5230N01800E005\nA) EPPR B) {t(-5, now)} C) {t(5, now)}\n"
             "E) AD CLSD)")
    foreign = (f"(B3001/26 NOTAMN\nQ) EDWW/QMRLC/IV/NBO/A/000/999/5222N01331E005\nA) EDDB B) {t(-5, now)} C) {t(5, now)}\n"
               "E) RWY 07L/25R CLSD)")
    return {
        "EPWW": "\n".join([fir_area, fir_uas, fir_future, fir_old]),
        # EPWA: własne, NOTAMR/NOTAMC, powtórzony NOTAM FIR i NOTAM innego (małego) lotniska EP oraz obcy z EDWW
        "EPWA": "\n".join([wa_rwy, wa_twy, wa_replaced, wa_repl, wa_cnl, fir_area, small, foreign]),
        "EPMO": "\n".join([mo, wa_rwy]),
    }


@pytest.fixture
def fake_server(monkeypatch):
    """Podstawiony fetch_text: odpowiedź wg kodu w adresie, EPKK i EPGD nie odpowiadają; zapisuje zapytania."""
    calls, state = [], {"now": 0, "max": 0, "down": {"EPKK", "EPGD"}, "texts": server()}

    async def fake(url, ttl, params=None, headers=None):
        icao = url.split("icao=")[1][:4]
        calls.append((icao, ttl))
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        try:
            await asyncio.sleep(0.01)
            if icao in state["down"]:
                raise UpstreamError(f"{url}: timeout")
            return state["texts"].get(icao, "")
        finally:
            state["now"] -= 1

    monkeypatch.setattr(notam, "fetch_text", fake)
    state["calls"] = calls
    return state


def test_parse_q():
    q = notam.parse_q("EPWW/QMRLC/IV/NBO/A/000/999/5210N02058E005")
    assert (q["fir"], q["qcode"], q["subject"], q["condition"]) == ("EPWW", "QMRLC", "MR", "LC")
    assert (q["subject_text"], q["condition_text"]) == ("RWY", "CLSD")
    assert (q["traffic"], q["purpose"], q["scope"], q["lower"], q["upper"]) == ("IV", "NBO", "A", 0, 999)
    assert q["coords"] == "5210N02058E005"
    # XX = tekst otwarty (bez opisu stanu), nieznany temat zostaje kodem bez opisu
    assert notam.parse_q("EPWW/QFAXX/IV/NBO/A/000/999")["condition_text"] is None
    assert notam.parse_q("EPWW/QQQQQ/IV")["subject_text"] is None
    empty = notam.parse_q(None)
    assert empty["qcode"] is None and empty["fir"] is None and empty["lower"] is None


def test_id_key_order():
    ids = ["B0001/26", "A0002/26", "A0999/25", "A0010/26", None]
    assert sorted(ids, key=notam.id_key) == ["A0999/25", "A0002/26", "A0010/26", "B0001/26", None]


def test_is_active():
    iso = lambda h: (NOW + timedelta(hours=h)).isoformat()  # noqa: E731
    assert notam.is_active({"start": iso(-1), "end": iso(1)}, NOW)
    assert not notam.is_active({"start": iso(1), "end": iso(2)}, NOW)           # dopiero będzie
    assert not notam.is_active({"start": iso(-2), "end": iso(-1)}, NOW)         # wygasł
    assert notam.is_active({"start": iso(-100), "end": None, "perm": True}, NOW)
    assert notam.is_active({}, NOW)                                             # bez B)/C) pokazujemy


def test_fir_notams(fake_server):
    d = asyncio.run(notam.fir_notams(NOW))
    ids = [n["id"] for n in d["notams"]]
    # FIR EPWW najpierw (po numerze), potem lotniska alfabetycznie; bez duplikatów (A2001/26 i A1234/26 są w
    # dwóch odpowiedziach), bez przyszłych i wygasłych, bez NOTAMC i bez NOTAM-ów anulowanych/zastąpionych
    assert ids == ["A2001/26", "B0150/26", "A1260/26", "A1270/26", "A1234/26", "A1300/26"]
    assert len(ids) == len(set(ids)) and d["count"] == 6
    assert d["inactive"] == 2                                   # A2005/26 (przyszły) i A1900/26 (wygasły)
    # obcy NOTAM (EDDB, Q) EDWW) z odpowiedzi dla EPWA nie należy do FIR EPWW
    assert "B3001/26" not in ids
    # mały polski aerodrom spoza listy głównych zostaje (cały FIR EPWW)
    assert next(n for n in d["notams"] if n["id"] == "A1270/26")["location"] == "EPPR"
    area = d["notams"][0]
    assert area["location"] == "EPWW" and area["locations"] == ["EPWW"]
    assert (area["qcode"], area["subject_text"], area["condition_text"]) == ("QRTCA", "TEMPO RESTRICTED AREA", "ACT")
    assert area["schedule"] == "0800-1600" and area["text"] == "TEMPO RESTRICTED AREA EP TRA 08 ACT"
    assert (area["lower_limit"], area["upper_limit"]) == ("SFC", "FL095")
    assert area["start"] == (NOW - timedelta(hours=3)).isoformat() and area["active"] is True
    assert area["raw"].startswith("(A2001/26 NOTAMN") and area["type"] == "N"
    uas = d["notams"][1]
    assert uas["est"] is True and uas["perm"] is False and uas["end"] == (NOW + timedelta(hours=30)).isoformat()
    repl = next(n for n in d["notams"] if n["id"] == "A1300/26")
    assert (repl["type"], repl["ref"]) == ("R", "A1230/26")
    # awaria części lokalizacji: lista z pozostałych i nieudane lokalizacje z komunikatem bez adresu
    assert {f["icao"] for f in d["failed"]} == {"EPKK", "EPGD"}
    assert all(f["error"] == "timeout" for f in d["failed"])
    assert d["error"] == "Serwer NOTAM: EPGD, EPKK: timeout"
    assert d["locations"][0] == "EPWW" and len(d["locations"]) == 16 and d["fir"] == "EPWW"


def test_fir_notams_concurrency_and_cache_ttl(fake_server):
    asyncio.run(notam.fir_notams(NOW))
    # każda lokalizacja raz, z czasem cache jak przy pojedynczym lotnisku, najwyżej FIR_CONCURRENCY naraz
    assert sorted(i for i, _ in fake_server["calls"]) == sorted(notam.FIR_LOCATIONS)
    assert {ttl for _, ttl in fake_server["calls"]} == {settings.notam_cache_seconds}
    assert 1 < fake_server["max"] <= notam.FIR_CONCURRENCY


def test_fir_notams_include_inactive(fake_server):
    d = asyncio.run(notam.fir_notams(NOW, include_inactive=True))
    flags = {n["id"]: n["active"] for n in d["notams"]}
    assert flags["A2005/26"] is False and flags["A1900/26"] is False and flags["A2001/26"] is True
    # anulowany i zastąpiony NOTAM nie wracają także z ?all
    assert "A1240/26" not in flags and "A1230/26" not in flags and "A1301/26" not in flags


def test_fir_notams_all_down(fake_server):
    fake_server["down"] = set(notam.FIR_LOCATIONS)
    d = asyncio.run(notam.fir_notams(NOW))
    assert d["notams"] == [] and len(d["failed"]) == 16
    assert d["error"].startswith("Serwer NOTAM: EPWW, EPBY") and d["error"].endswith(": timeout")


def test_fir_notams_unknown_format(fake_server):
    """Odpowiedź bez numerów i pól (nieznany format): wpis zostaje przy lokalizacji, której kod wymienia."""
    fake_server["down"] = set()
    fake_server["texts"] = {"EPWA": "EPWA RWY 11/29 CLSD\n\nSOMETHING ELSE"}
    d = asyncio.run(notam.fir_notams(NOW))
    assert [(n["id"], n["location"], n["raw"]) for n in d["notams"]] == [(None, "EPWA", "EPWA RWY 11/29 CLSD")]


@pytest.fixture
def client(monkeypatch):
    texts = server(datetime.now(timezone.utc))

    async def fake(url, ttl, params=None, headers=None):
        icao = url.split("icao=")[1][:4]
        if icao == "EPKK":
            raise UpstreamError(f"{url}: [Errno -3] Temporary failure in name resolution")
        return texts.get(icao, "")

    monkeypatch.setattr(notam, "fetch_text", fake)
    with TestClient(main.app) as c:
        yield c


def test_api_fir_epww(client):
    r = client.get("/api/notam/fir/EPWW")
    assert r.status_code == 200
    d = r.json()
    assert [n["id"] for n in d["notams"]][:2] == ["A2001/26", "B0150/26"]
    assert all(n["active"] for n in d["notams"]) and d["inactive"] == 2
    assert d["failed"] == [{"icao": "EPKK", "error": "[Errno -3] Temporary failure in name resolution"}]
    assert d["names"]["EPWW"] == "FIR WARSZAWA"
    assert client.get("/api/notam/fir/epww").status_code == 200
    every = client.get("/api/notam/fir/EPWW?all=true").json()
    assert every["count"] == d["count"] + 2 and not all(n["active"] for n in every["notams"])


def test_api_fir_other(client):
    r = client.get("/api/notam/fir/EDWW")
    assert r.status_code == 404 and "EPWW" in r.json()["detail"]


def test_api_aerodrome_notams_unchanged(client):
    """AERODROME korzysta z tego samego serwisu: pojedyncze lotnisko jak dotąd (tylko jego NOTAM-y, z polami)."""
    d = client.get("/api/notam/EPWA").json()
    assert d["icao"] == "EPWA" and [n["id"] for n in d["notams"]] == ["A1234/26", "A1240/26", "A1230/26", "A1300/26",
                                                                      "A1301/26"]
    assert set(d["notams"][0]) == {"id", "raw", "fields", "start", "end", "perm", "est", "schedule"}
    assert d["other"] == 3
    r = client.get("/api/notam/EPKK")
    assert r.status_code == 502 and "Serwer NOTAM niedostępny" in r.json()["detail"]


# --- frontend: czyste funkcje zakładki SYT OP (Node, bez przeglądarki) -------------------------------------------

NODE_SCRIPT = r"""
const s = await import(process.argv[2]);
const now = Date.parse("2026-10-12T12:00:00Z");
const iso = (h) => new Date(now + h * 3600000).toISOString();
const list = [
  {id: "A1234/26", location: "EPWA", locations: ["EPWA"], start: iso(-6), end: iso(6), perm: false, est: false, text: "RWY 11/29 CLSD", raw: "(A1234/26 NOTAMN ... RWY 11/29 CLSD)", qcode: "QMRLC", subject_text: "RWY", condition_text: "CLSD"},
  {id: "A1240/26", location: "EPWA", locations: ["EPWA"], start: iso(-700), end: null, perm: true, est: false, text: "TWY M CLSD", raw: "(A1240/26 ...)"},
  {id: "B0150/26", location: "EPWW", locations: ["EPWW"], start: iso(-1), end: iso(30), perm: false, est: true, text: "UAS FLIGHTS", raw: "(B0150/26 ...)"},
  {id: "A0999/25", location: "EPWW", locations: ["EPWW", "EPKK"], start: iso(-2), end: iso(2), perm: false, est: false, text: "AREA ACT", raw: "(A0999/25 ...)"},
  {id: "A1250/26", location: "EPKK", locations: ["EPKK"], start: iso(-240), end: null, perm: true, est: false, text: "RWY 07/25 CLSD", raw: "(A1250/26 ...)"},
];
const ids = (l) => l.map((n) => n.id);
const sorted = (k) => ids([...list].sort(s.SORT_FN[k]));
console.log(JSON.stringify({
  sort: {loc: sorted("loc"), id: sorted("id"), start: sorted("start"), end: sorted("end")},
  filter: [ids(s.filterNotams(list, {q: "rwy clsd"})), ids(s.filterNotams(list, {loc: "EPKK"})),
           ids(s.filterNotams(list, {q: "a1234"})), ids(s.filterNotams(list, {q: "qmrlc"})), ids(s.filterNotams(list, {q: "  "})),
           ids(s.filterNotams(list, {q: "clsd", loc: "EPWA"}))],
  args: ["", "EPKK", "epkk", "ALL/end", "EPGD/start", "EPGD/xyz", "../x", "EPWW/loc"].map(s.parseArg),
  build: [["", "loc"], ["EPKK", "loc"], ["", "end"], ["EPGD", "id"]].map(([l, k]) => s.argOf(l, k)),
  state: [s.notamState(list[0], now), s.notamState(list[1], now), s.notamState(list[2], now),
          s.notamState({start: iso(2), end: iso(5)}, now), s.notamState({start: iso(-5), end: iso(-1)}, now)],
}));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    import json
    import shutil
    import subprocess

    from backend.app.config import ROOT_DIR
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("sytop")
    src = ROOT_DIR / "frontend" / "js"
    (d / "api.mjs").write_text((src / "api.js").read_text("utf-8"), "utf-8")
    (d / "sytop.mjs").write_text((src / "tabs" / "sytop.js").read_text("utf-8").replace('"../api.js"', '"./api.mjs"'), "utf-8")
    (d / "run.mjs").write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(d / "run.mjs"), (d / "sytop.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_js_sort(js):
    # domyślnie FIR EPWW najpierw, potem lotniska alfabetycznie; numer: seria, rok, numer; koniec: PERM na końcu
    assert js["sort"]["loc"] == ["A0999/25", "B0150/26", "A1250/26", "A1234/26", "A1240/26"]
    assert js["sort"]["id"] == ["A0999/25", "A1234/26", "A1240/26", "A1250/26", "B0150/26"]
    assert js["sort"]["start"] == ["A1240/26", "A1250/26", "A1234/26", "A0999/25", "B0150/26"]
    assert js["sort"]["end"] == ["A0999/25", "A1234/26", "B0150/26", "A1240/26", "A1250/26"]


def test_js_filter(js):
    rwy, kk, num, qcode, blank, wa = js["filter"]
    assert rwy == ["A1234/26", "A1250/26"]                      # każde słowo musi wystąpić
    assert kk == ["A0999/25", "A1250/26"]                       # lokalizacja także z listy A) (EPWW EPKK)
    assert num == ["A1234/26"] and qcode == ["A1234/26"] and len(blank) == 5 and wa == ["A1234/26", "A1240/26"]


def test_js_hash_arg(js):
    assert js["args"] == [{"loc": "", "sort": "loc"}, {"loc": "EPKK", "sort": "loc"}, {"loc": "EPKK", "sort": "loc"},
                          {"loc": "", "sort": "end"}, {"loc": "EPGD", "sort": "start"}, {"loc": "EPGD", "sort": "loc"},
                          {"loc": "", "sort": "loc"}, {"loc": "EPWW", "sort": "loc"}]
    assert js["build"] == ["", "EPKK", "ALL/end", "EPGD/id"]


def test_js_validity_text(js):
    """Opis ważności jak w liście NOTAM w AERODROME (od/do, PERM, EST)."""
    st = js["state"]
    assert st[0] == {"cls": "active", "txt": "AKTYWNY DO 12.10 18:00Z (jeszcze 6 h 0 min)"}
    assert st[1] == {"cls": "active", "txt": "AKTYWNY od 13.09 08:00Z · PERM"}
    assert st[2] == {"cls": "active", "txt": "AKTYWNY DO 13.10 18:00Z EST (jeszcze 30 h 0 min)"}
    assert st[3]["cls"] == "future" and st[3]["txt"].startswith("AKTYWNY OD 12.10 14:00Z (za 2 h 0 min) do 12.10 17:00Z")
    assert st[4] == {"cls": "expired", "txt": "WYGASŁ 12.10 11:00Z"}


def test_sytop_tab_uses_navigation_and_escaping():
    """Lokalizacja i kolejność to kroki do cofnięcia; dane z serwera tylko przez esc(); bez nazw jak reklama."""
    from backend.app.config import ROOT_DIR
    src = (ROOT_DIR / "frontend" / "js" / "tabs" / "sytop.js").read_text("utf-8")
    assert 'remember("sytop", argOf(loc, sort))' in src and "activate(arg)" in src
    assert "/api/notam/fir/EPWW" in src and "onclick=" not in src
    assert "soon(" not in src
