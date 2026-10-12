"""AERODROME (runda 10): listy lotnisk FIR-ów (podzakładki EPWW … UKLV) i karta lotniska zagranicznego.

Marek, 12.10.2026: "pozostałe zakładki to lista lotnisk w danym FIR (dla Niemiec ED oddzielnie i wojskowe ET
oddzielnie) - w tym VFR i MIL"; "dla każdego lotniska podany METAR, TAF, pozycje dla danego lotniska jak TWR, GND,
DEL, ATIS, APP". Źródła zewnętrzne (METAR, TAF, VATSIM) są podstawione."""

import os
import tempfile

os.environ.setdefault("POLIDS_DATABASE_URL",
                      "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.models import Aerodrome  # noqa: E402
from backend.app.routers import aerodromes  # noqa: E402
from backend.app.services import aerolist  # noqa: E402
from backend.app.services.aerolist import (airport_positions, build_lists, display_name, fir_key, fold,  # noqa: E402
                                           uklv_polygons)

# kwadrat zamiast granicy UKLV z VATSpy (testy bez pliku): Lwów (49.8 N 24.0 E) w środku, Kijów poza
SQUARE = [[[22.0, 48.0], [27.0, 48.0], [27.0, 52.0], [22.0, 52.0], [22.0, 48.0]]]


def test_fold_uppercase_without_diacritics():
    assert fold("Łódź  Lublinek") == "LODZ LUBLINEK" and fold("Gdańsk Lech Wałęsa") == "GDANSK LECH WALESA"
    assert fold("Jeżów Sudecki") == "JEZOW SUDECKI" and fold(None) == ""
    # icao.txt (Latin-1) czytany jako cp1250: szwedzkie Å przychodzi jako Ĺ
    assert fold("BORĹS/Borĺs sjukhus") == "BORAS/BORAS SJUKHUS" and fold("GÄLLIVARE") == "GALLIVARE"


def test_fir_key_prefixes():
    """ED (cywilne) i ET (wojskowe) osobno, UMK* to Kaliningrad, reszta UM* Białoruś."""
    cases = {"EPWA": "EPWW", "EPDE": "EPWW", "EPKS": "EPWW", "EPEL": "EPWW", "ESSA": "ESAA", "EDDB": "ED",
             "EDAH": "ED", "ETNL": "ET", "ETAR": "ET", "LKPR": "LKAA", "LZIB": "LZBB", "UMKK": "UMKK",
             "UMKB": "UMKK", "UMMS": "UMMV", "UMBB": "UMMV", "EYVI": "EYVL"}
    assert {i: fir_key(i) for i in cases} == cases
    assert fir_key("EGLL") is None and fir_key("EKCH") is None and fir_key("EP1") is None and fir_key("") is None


def test_fir_key_uklv_polygon():
    # w granicy FIR UKLV: Lwów, Iwano-Frankiwsk; poza: Kijów Boryspil, Odessa (także gdy kod zaczyna się od UKL)
    assert fir_key("UKLL", 49.81, 23.96, SQUARE) == "UKLV" and fir_key("UKMC", 49.74, 23.67, SQUARE) == "UKLV"
    assert fir_key("UKBB", 50.34, 30.89, SQUARE) is None and fir_key("UKOO", 46.43, 30.67, SQUARE) is None
    assert fir_key("UKLX", 50.34, 30.89, SQUARE) is None
    # granica nieużywalna albo brak współrzędnych: UKL*
    assert fir_key("UKLL", 49.81, 23.96, []) == "UKLV" and fir_key("UKBB", 50.34, 30.89, []) is None
    assert fir_key("UKLR") == "UKLV"
    # prawdziwa granica z VATSpy (data/seed/vatspy_firs.geojson)
    real = uklv_polygons()
    assert real, "brak granicy UKLV w vatspy_firs.geojson"
    assert fir_key("UKLL", 49.8097, 23.9583, real) == "UKLV" and fir_key("UKLU", 48.6344, 22.2631, real) == "UKLV"
    assert fir_key("UKBB", 50.3447, 30.8933, real) is None and fir_key("UKKK", 50.4019, 30.4519, real) is None


def test_build_lists_dedupe_names_kinds():
    nav = [("EPDE", "DEBLIN", 51.55, 21.89), ("EPKS", "KRZESINY", 52.33, 16.97), ("EPEL", "ELBLAG", 54.14, 19.42),
           ("EPGD", "LECH WALESA", 54.38, 18.47), ("EDDB", "BERLIN BRANDENBURG", 52.36, 13.5),
           ("EDDB", "BERLIN DUPLIKAT", 52.36, 13.5), ("ETNL", "ROSTOCK-LAAGE", 53.92, 12.28),
           ("UMKK", "KHRABROVO", 54.89, 20.6), ("UMMS", "MINSK-2", 53.88, 28.03), ("UKLL", "LVIV INTL", 49.81, 23.96),
           ("UKBB", "BORYSPIL INTL", 50.34, 30.89), ("ESSA", "ARLANDA", 59.65, 17.92), ("EGLL", "HEATHROW", 51.5, -0.45),
           ("LZ93", "Geronimo LZ", 48.0, 18.0)]
    csv = [Aerodrome(icao="EPDE", name="Deblin Military Air Base", kind="medium_airport", lat=51.55, lon=21.89),
           Aerodrome(icao="EPKS", name="Krzesiny Military Air Base", kind="medium_airport", lat=52.33, lon=16.97),
           Aerodrome(icao="EPGM", name="Giżycko-Mazury Residence", kind="small_airport", lat=54.0, lon=21.8),
           Aerodrome(icao="EPKB", name="Kazimierz Biskupi Airfield", kind="small_airport", lat=52.3, lon=18.2)]
    lists = build_lists(nav, csv, SQUARE)
    assert list(lists) == list(aerolist.FIR_KEYS)
    ep = {a["icao"]: a for a in lists["EPWW"]}
    # po ICAO; z lądowiskami z listy PANDORY, których nie ma w navdata (EPGB, EPGN, EPGO, EPGW)
    assert [a["icao"] for a in lists["EPWW"]] == ["EPDE", "EPEL", "EPGB", "EPGD", "EPGM", "EPGN", "EPGO", "EPGW",
                                                  "EPKB", "EPKS"]
    assert ep["EPGB"]["name"] == "GOLUBIE" and ep["EPGW"]["name"] == "ULIM"
    # nazwy z navdata; miasto z przodu tam, gdzie navdata podaje samą dzielnicę albo patrona
    assert ep["EPDE"]["name"] == "DEBLIN" and ep["EPGD"]["name"] == "GDANSK LECH WALESA"
    assert ep["EPKS"]["name"] == "POZNAN KRZESINY" and ep["EPEL"]["name"] == "ELBLAG"
    # tylko z airports.csv: bez słów ogólnych, wielkie litery bez polskich znaków
    assert ep["EPGM"]["name"] == "GIZYCKO-MAZURY RESIDENCE" and ep["EPKB"]["name"] == "KAZIMIERZ BISKUPI"
    # wojskowe: ET*, "Military"/"Air Base" w airports.csv
    assert ep["EPDE"]["kind"] == "MIL" and ep["EPKS"]["kind"] == "MIL" and ep["EPEL"]["kind"] is None
    assert lists["ET"] == [{"icao": "ETNL", "name": "ROSTOCK-LAAGE", "kind": "MIL", "rank": 2, "alias": [],
                            "lat": 53.92, "lon": 12.28}]
    assert [a["icao"] for a in lists["ED"]] == ["EDDB"] and lists["ED"][0]["name"] == "BERLIN BRANDENBURG"
    assert [a["icao"] for a in lists["UMKK"]] == ["UMKK"] and [a["icao"] for a in lists["UMMV"]] == ["UMMS"]
    assert [a["icao"] for a in lists["UKLV"]] == ["UKLL"] and [a["icao"] for a in lists["ESAA"]] == ["ESSA"]
    assert lists["ESAA"][0]["name"] == "STOCKHOLM ARLANDA"
    assert lists["LZBB"][0]["name"] == "GERONIMO LZ"
    assert not any(a["icao"] == "EGLL" for lst in lists.values() for a in lst)


def test_display_name():
    assert display_name("EPKK", "BALICE") == "KRAKOW BALICE" and display_name("EPWA", "CHOPIN") == "WARSZAWA CHOPIN"
    assert display_name("EPLL", "LODZ") == "LODZ" and display_name("EPSY", "OLSZTYN-MAZURY") == "OLSZTYN-MAZURY"
    assert display_name("EPKT", None, "Katowice Wojciech Korfanty International Airport") == "KATOWICE WOJCIECH KORFANTY"
    assert display_name("EDDB", "Berlin Brandenburg") == "BERLIN BRANDENBURG"


def test_heliports_mil_rank_alias():
    """Lądowiska śmigłowców przy szpitalach i platformy pominięte; MIL także poza EP i ET; rank i inne nazwy."""
    nav = [("ESSA", "ARLANDA", 59.65, 17.92), ("ESSB", "BROMMA", 59.35, 17.94),
           ("ESHC", "STOCKHOLM/SODERSJUKHUSET", 59.3, 18.0), ("ESHG", "STOCKHOLM/GAMLA STAN", 59.3, 18.1),
           ("ESEX", "KRIEGERS FLAK A", 55.0, 13.0), ("ESDF", "RONNEBY", 56.27, 15.27),
           ("EDGL", "BERUFSGENOSSENSCHAFTLICHE UNFALLKLINIK LUDWIGSHAFEN", 49.5, 8.4),
           ("EDDM", "MUNICH", 48.35, 11.79), ("LKPR", "RUZYNE", 50.1, 14.26), ("LKCV", "CASLAV", 49.94, 15.38),
           ("EPWA", "CHOPIN", 52.17, 20.97), ("EPBC", "BABICE", 52.27, 20.91), ("EPMO", "MODLIN", 52.45, 20.65)]
    lists = build_lists(nav, [], SQUARE, atc={"ESSB", "EPBC", "EPMO", "LKCV"})
    se = {a["icao"]: a for a in lists["ESAA"]}
    assert set(se) == {"ESSA", "ESSB", "ESDF"}
    assert se["ESDF"]["kind"] == "MIL" and se["ESSA"]["kind"] is None
    assert [a["icao"] for a in lists["ED"]] == ["EDDM"] and lists["ED"][0]["alias"] == ["MUENCHEN", "MONACHIUM"]
    ep = {a["icao"]: a for a in lists["EPWW"]}
    assert (ep["EPWA"]["rank"], ep["EPBC"]["rank"], ep["EPMO"]["rank"]) == (0, 1, 1)
    assert ep["EPWA"]["name"] == "WARSZAWA CHOPIN" and ep["EPGB"]["rank"] == 2
    lk = {a["icao"]: a for a in lists["LKAA"]}
    assert lk["LKPR"]["name"] == "PRAHA RUZYNE" and "PRAGA" in lk["LKPR"]["alias"] and lk["LKPR"]["rank"] == 0
    assert lk["LKCV"]["kind"] == "MIL" and lk["LKCV"]["rank"] == 1
    assert aerolist.atc_airports([{"callsign": "EDDB_N_TWR"}, {"callsign": "EPWW_CTR"}, {"callsign": "EPKK_ATIS"},
                                  {"callsign": None}]) == {"EDDB", "EPKK"}


def test_main_airports_exist_in_lists(client):
    """Tabele MAIN, ALIAS i MIL wskazują lotniska, które są na listach (literówka w kodzie nie przejdzie)."""
    lists = {k: {a["icao"]: a for a in client.get(f"/api/aerodromes/fir/{k}").json()} for k in aerolist.FIR_KEYS}
    every = {i: a for lst in lists.values() for i, a in lst.items()}
    assert set(aerolist.MAIN) <= set(every) and set(aerolist.ALIAS) <= set(every) and set(aerolist.CITY) <= set(every)
    assert set(aerolist.MIL) <= set(every) and all(every[i]["kind"] == "MIL" for i in aerolist.MIL)
    assert all(every[i]["rank"] == 0 for i in aerolist.MAIN)
    # ESAA bez szpitalnych lądowisk śmigłowców
    assert not any("SJUKHUS" in a["name"] or "LASARETT" in a["name"] for a in lists["ESAA"].values())
    assert {"ESSA", "ESGG", "ESMS"} <= set(lists["ESAA"])
    # lotniska ze stanowiskami w danych mają rank 1 (EPMO, EPBC, EPEL), bez stanowisk rank 2 (EPAR)
    ep = lists["EPWW"]
    assert (ep["EPWA"]["rank"], ep["EPMO"]["rank"], ep["EPBC"]["rank"], ep["EPEL"]["rank"], ep["EPAR"]["rank"]) == \
        (0, 1, 1, 1, 2)
    assert {"EPGB", "EPGN", "EPGO", "EPGW"} <= set(ep)


def test_airport_positions_order_and_online():
    known = [{"callsign": "EDDB_N_TWR", "frequency": "120.030", "name": None},
             {"callsign": "EDDB_S_TWR", "frequency": "118.805"},
             {"callsign": "EDDB_DEL", "frequency": "121.605"},
             {"callsign": "EDDB_A_GND", "frequency": "121.855"},
             {"callsign": "EDDB_N_DEP", "frequency": "120.340", "name": "Bremen Radar"},
             {"callsign": "EDDB_AN_APP", "frequency": "136.105", "name": "Berlin Arrival"},
             {"callsign": "EDWW_FLG_CTR", "frequency": "136.450"},   # nie lotnisko
             {"callsign": "EDDBX_TWR", "frequency": "118.000"}]      # inny prefiks
    ctrls = [{"callsign": "EDDB_TWR", "frequency": "120.030", "name": "Hans Weber", "cid": 5, "logon_time": "2026-10-12T11:00:00Z"},
             {"callsign": "EDDB_ATIS", "frequency": "123.180", "name": "Hans Weber", "atis_code": "C",
              "text_atis": ["BERLIN BRANDENBURG INFORMATION C"]},
             {"callsign": "EDDB_CTR", "frequency": "130.000"},          # CTR to nie stanowisko lotniska
             {"callsign": "EDWW_FLG_CTR", "frequency": "136.450"}]
    ps = airport_positions("eddb", known, ctrls)
    assert [(p["type"], p["callsign"]) for p in ps] == [
        ("APP", "EDDB_AN_APP"), ("APP", "EDDB_N_DEP"), ("TWR", "EDDB_N_TWR"), ("TWR", "EDDB_S_TWR"),
        ("GND", "EDDB_A_GND"), ("DEL", "EDDB_DEL"), ("ATIS", "EDDB_ATIS")]
    by = {p["callsign"]: p for p in ps}
    # EDDB_TWR na 120.030 to stanowisko EDDB_N_TWR z danych (prefiks, typ, częstotliwość jak w EuroScope)
    assert by["EDDB_N_TWR"]["online"]["name"] == "Hans Weber" and by["EDDB_N_TWR"]["online"]["callsign"] == "EDDB_TWR"
    assert by["EDDB_S_TWR"]["online"] is None and by["EDDB_DEL"]["online"] is None
    # ATIS spoza danych: własny wiersz z literą i tekstem
    atis = by["EDDB_ATIS"]
    assert not atis["known"] and atis["frequency"] == "123.180" and atis["online"]["atis_code"] == "C"
    assert atis["online"]["text_atis"] == ["BERLIN BRANDENBURG INFORMATION C"]
    assert airport_positions("LKPR", [], []) == []


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def fakes(monkeypatch):
    async def metars(icaos):
        return {i: f"{i} 121230Z 27010KT 9999 FEW030 12/05 Q1015" for i in icaos if i != "UMMS"}

    async def tafs(icaos):
        return {i: f"TAF {i} 121100Z 1212/1312 27010KT 9999 BECMG 1215/1217 21005KT" for i in icaos}

    async def feed():
        return {"general": {"update_timestamp": "2026-10-12T12:30:00Z"},
                "controllers": [{"callsign": "UKLL_TWR", "frequency": "128.000", "name": "Ivan", "cid": 7,
                                 "logon_time": "2026-10-12T12:00:00Z"}],
                "atis": [{"callsign": "UKLL_ATIS", "frequency": "126.300", "atis_code": "D", "text_atis": ["LVIV D"]}],
                "pilots": [], "prefiles": []}
    monkeypatch.setattr(aerodromes, "get_metars", metars)
    monkeypatch.setattr(aerodromes, "get_tafs", tafs)
    monkeypatch.setattr(aerodromes, "get_feed", feed)


def test_fir_lists_from_navdata(client):
    """Prawdziwe dane: EPWW z lotniskami VFR i MIL (EPEL, EPDE, EPKS), ED bez ET, UKLV z granicy VATSpy."""
    ep = client.get("/api/aerodromes/fir/EPWW").json()
    by = {a["icao"]: a for a in ep}
    assert {"EPWA", "EPKK", "EPEL", "EPDE", "EPKS", "EPGM", "EPKX"} <= set(by)
    assert by["EPDE"]["kind"] == "MIL" and by["EPKS"]["kind"] == "MIL" and by["EPEL"]["name"] == "ELBLAG"
    assert by["EPGD"]["name"] == "GDANSK LECH WALESA" and by["EPKK"]["name"] == "KRAKOW BALICE"
    assert [a["icao"] for a in ep] == sorted(a["icao"] for a in ep) and len({a["icao"] for a in ep}) == len(ep)
    lists = {k: client.get(f"/api/aerodromes/fir/{k.lower()}").json() for k in aerolist.FIR_KEYS}
    for k, lst in lists.items():
        assert lst, k
        assert all(a["name"] == a["name"].upper() and a["name"].isascii() and a["name"] for a in lst), k
    ed, et = {a["icao"] for a in lists["ED"]}, {a["icao"] for a in lists["ET"]}
    assert "EDDB" in ed and "ETNL" in et and all(i.startswith("ED") for i in ed) and all(i.startswith("ET") for i in et)
    assert all(a["kind"] == "MIL" for a in lists["ET"])
    assert {a["icao"] for a in lists["UMKK"]} >= {"UMKK"} and all(a["icao"].startswith("UMK") for a in lists["UMKK"])
    umm = {a["icao"] for a in lists["UMMV"]}
    assert "UMMS" in umm and not any(i.startswith("UMK") for i in umm)
    ukl = {a["icao"] for a in lists["UKLV"]}
    assert {"UKLL", "UKLI", "UKLU", "UKMC"} <= ukl and not ({"UKBB", "UKKK", "UKOO"} & ukl)
    assert {"ESSA", "ESGG"} <= {a["icao"] for a in lists["ESAA"]} and "LKPR" in {a["icao"] for a in lists["LKAA"]}
    assert "LZIB" in {a["icao"] for a in lists["LZBB"]} and "EYVI" in {a["icao"] for a in lists["EYVL"]}
    assert client.get("/api/aerodromes/fir/XX").status_code == 404


def test_card_foreign_airport(client, fakes):
    d = client.get("/api/aerodromes/ukll/card").json()
    assert (d["icao"], d["fir"], d["fir_label"]) == ("UKLL", "UKLV", "Ukraina, FIR Lwów") and d["name"] == "LVIV INTL"
    assert d["metar"].startswith("UKLL ") and d["parsed"]["qnh"] == 1015 and d["taf"].startswith("TAF UKLL")
    types = [p["type"] for p in d["positions"]]
    assert types == sorted(types, key=aerolist.TYPE_ORDER.index) and {"APP", "TWR", "GND", "ATIS"} <= set(types)
    by = {p["callsign"]: p for p in d["positions"]}
    assert by["UKLL_TWR"]["online"]["name"] == "Ivan" and by["UKLL_APP"]["online"] is None
    assert by["UKLL_ATIS"]["online"]["atis_code"] == "D" and d["updated"] == "2026-10-12T12:30:00Z"
    # bez METAR (NIL): pola puste, bez błędu
    m = client.get("/api/aerodromes/UMMS/card").json()
    assert m["metar"] is None and m["parsed"] is None and m["fir"] == "UMMV" and m["positions"] == []
    # polskie lotnisko spoza bazy lotnisk (tylko navdata) też ma kartę; nieznany kod: 404
    assert client.get("/api/aerodromes/EPKX/card").json()["fir"] == "EPWW"
    assert client.get("/api/aerodromes/EPWA/card").json()["name"] == "WARSZAWA CHOPIN"
    # nieznany kod: 404 z komunikatem, po którym karta (aerolist.js UNKNOWN) pokazuje jeden komunikat i nie odpytuje
    r = client.get("/api/aerodromes/ZZZZ/card")
    assert r.status_code == 404 and r.json()["detail"].startswith("Nie znam lotniska")
    # lądowisko śmigłowców nie jest na liście ESAA, ale jego karta się otwiera (z navdata)
    assert client.get("/api/aerodromes/ESHC/card").json()["name"] == "STOCKHOLM/SODERSJUKHUSET"
    assert client.get("/api/aerodromes/E%3CB%3E/card").status_code == 404



def test_card_upstream_errors(client, monkeypatch):
    """Brak METAR-u albo sieci VATSIM: karta z komunikatem błędu i stanowiskami offline zamiast błędu 500."""
    from backend.app.services.http_cache import UpstreamError

    async def down(*_a):
        raise UpstreamError("timeout (test)")

    async def tafs(icaos):
        return {}
    monkeypatch.setattr(aerodromes, "get_metars", down)
    monkeypatch.setattr(aerodromes, "get_tafs", tafs)
    monkeypatch.setattr(aerodromes, "get_feed", down)
    d = client.get("/api/aerodromes/EDDB/card").json()
    assert d["metar"] is None and d["taf"] is None and d["errors"] == ["METAR: timeout (test)"]
    assert d["network_error"] == "VATSIM: timeout (test)" and d["updated"] is None
    assert d["positions"] and all(p["online"] is None for p in d["positions"])

# --- frontend/js/aerolist.js w Node: adres zakładki, wyszukiwanie bez polskich znaków, FIR z ICAO -----------------

JS_SCRIPT = r"""
const m = await import(process.argv[2]);
const args = ["0", "OVERVIEW", "overview", "", "fir/ED", "fir/ed", "FIR/UKLV", "fir/XX", "fir/", "epwa", "EDDB", " EPKK ",
  "EP1", "EPWAX", "<b>", "fir/ED/x", null, "EPWA/awos", "epwa/RUCH", "EPWA/overview", "EPWA/xyz", "EPWA/awos/x"];
// lista jak z /api/aerodromes/fir/EPWW (fragment) i ED; it.i = kolejność listy (po ICAO)
const mk = (rows) => rows.map(([icao, name, rank, alias], i) => ({ i, icao, rank, keys: m.searchKeys(name, alias || []) }));
const ep = mk([["EPBC", "WARSZAWA BABICE", 1], ["EPKC", "KRAKOW CZYZYNY", 2], ["EPKK", "KRAKOW BALICE", 0],
  ["EPKM", "KATOWICE MUCHOWIEC", 1], ["EPKS", "POZNAN KRZESINY", 1], ["EPKT", "KATOWICE PYRZOWICE", 0],
  ["EPLL", "LODZ", 0], ["EPMO", "WARSZAWA MODLIN", 1], ["EPOD", "OLSZTYN DAJTKI", 1], ["EPPO", "POZNAN LAWICA", 0],
  ["EPRJ", "RZESZOW", 2], ["EPRZ", "RZESZOW JASIONKA", 0], ["EPSY", "OLSZTYN-MAZURY", 0], ["EPWA", "WARSZAWA CHOPIN", 0]]);
const ed = mk([["EDAV", "EBERSWALDE-FINOW", 2], ["EDDB", "BERLIN BRANDENBURG", 0], ["EDDK", "COLOGNE-BONN", 0, ["KOELN", "KOLONIA"]],
  ["EDDL", "DUESSELDORF", 0], ["EDDM", "MUNICH", 0, ["MUENCHEN", "MONACHIUM"]], ["EDDN", "NUERNBERG", 0], ["EDQM", "HOF-PLAUEN", 1]]);
const pick = (items, q) => m.bestMatch(items, m.queryForms(q))?.icao ?? null;
const qs = ["warszawa", "krakow", "Kraków", "poznan", "katowice", "rzeszow", "olsztyn", "łódź", "EPKS", "epk", "modlin", "zzzz"];
const dq = ["BER", "münchen", "munchen", "nürnberg", "nurnberg", "düsseldorf", "köln", "koln", "kolonia", "hof plauen", "EDD"];
console.log(JSON.stringify({
  arg: args.map(m.aeroArg),
  fold: ["Łódź", "gdańsk  lech wałęsa", "Jeżów Sudecki", "KRAKÓW"].map(m.fold),
  fir: ["EPDE", "EDDB", "ETNL", "UMKK", "UMKB", "UMMS", "ESSA", "LKPR", "LZIB", "EYVI", "UKLL", "UKBB", "EGLL"].map(m.firOf),
  keys: m.FIR_KEYS,
  split: ["EPWA", "EPWA/awos", "EPWA/ruch", "0"].map(m.splitArg),
  ep: Object.fromEntries(qs.map((q) => [q, pick(ep, q)])),
  ed: Object.fromEntries(dq.map((q) => [q, pick(ed, q)])),
  plural: [1, 2, 4, 5, 12, 22, 32, 33, 93, 119, 469].map((n) => n + " " + m.lotnisk(n)),
  step: [[-1, 10, 1], [-1, 10, -1], [-1, 10, 18], [0, 10, -1], [3, 10, 1], [3, 10, -1], [9, 10, 1], [2, 10, 18],
    [9, 10, -18], [5, 0, 1], [-1, 0, -1]].map(([i, n, d]) => m.stepSel(i, n, d)),
  page: [[684, 38], [708, 38], [38, 38], [0, 38], [684, 0], [684, NaN]].map(([h, r]) => m.pageRows(h, r)),
}));
"""


@pytest.fixture(scope="module")
def aerojs(tmp_path_factory):
    import json
    import shutil
    import subprocess

    from backend.app.config import ROOT_DIR
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    js = ROOT_DIR / "frontend" / "js"
    d = tmp_path_factory.mktemp("aerojs")
    (d / "api.mjs").write_text((js / "api.js").read_text("utf-8"), "utf-8")
    (d / "meteo.mjs").write_text("export const colorize = (x) => x; export const wxLines = (k, x) => x;\n", "utf-8")
    src = (js / "aerolist.js").read_text("utf-8").replace('"./api.js"', '"./api.mjs"').replace('"./tabs/meteo.js"', '"./meteo.mjs"')
    (d / "aerolist.mjs").write_text(src, "utf-8")
    (d / "run.mjs").write_text(JS_SCRIPT, "utf-8")
    r = subprocess.run([node, str(d / "run.mjs"), (d / "aerolist.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_js_aero_arg(aerojs):
    """#aerodrome/0, #aerodrome/OVERVIEW (dawny adres), #aerodrome/fir/ED, #aerodrome/EPWA; reszta nierozpoznana."""
    assert aerojs["arg"] == ["0", "0", "0", None, "fir/ED", "fir/ED", "fir/UKLV", None, None, "EPWA", "EDDB", "EPKK",
                             None, None, None, None, None, "EPWA/awos", "EPWA/ruch", "EPWA", None, None]
    # widok lotniska w adresie (PRZEGLĄD = sam ICAO): #aerodrome/EPWA/awos, #aerodrome/EPWA/ruch
    assert aerojs["split"] == [{"code": "EPWA", "view": "overview"}, {"code": "EPWA", "view": "awos"},
                               {"code": "EPWA", "view": "ruch"}, {"code": "0", "view": "overview"}]
    assert aerojs["keys"] == list(aerolist.FIR_KEYS)


def test_js_fold_and_fir_match_backend(aerojs):
    assert aerojs["fold"] == ["LODZ", "GDANSK LECH WALESA", "JEZOW SUDECKI", "KRAKOW"]
    assert aerojs["fold"] == [fold(x) for x in ["Łódź", "gdańsk  lech wałęsa", "Jeżów Sudecki", "KRAKÓW"]]
    codes = ["EPDE", "EDDB", "ETNL", "UMKK", "UMKB", "UMMS", "ESSA", "LKPR", "LZIB", "EYVI", "UKLL", "UKBB", "EGLL"]
    # bez wczytanej listy UKLV przeglądarka przypisuje UK* po prefiksie UKL (jak backend bez granicy FIR)
    assert aerojs["fir"] == [fir_key(c, uklv=[]) for c in codes]


def test_js_enter_opens_main_airport(aerojs):
    """Enter po nazwie miasta otwiera główne lotnisko, nie najmniejsze lądowisko pierwsze po ICAO."""
    assert aerojs["ep"] == {"warszawa": "EPWA", "krakow": "EPKK", "Kraków": "EPKK", "poznan": "EPPO",
                            "katowice": "EPKT", "rzeszow": "EPRZ", "olsztyn": "EPSY", "łódź": "EPLL",
                            "EPKS": "EPKS", "epk": "EPKC", "modlin": "EPMO", "zzzz": None}


def test_js_search_umlauts_and_aliases(aerojs):
    """"münchen"/"köln" przez inne nazwy, "nürnberg" i "nurnberg" do NUERNBERG z navdata, "BER" do BERLIN."""
    assert aerojs["ed"] == {"BER": "EDDB", "münchen": "EDDM", "munchen": "EDDM", "nürnberg": "EDDN",
                            "nurnberg": "EDDN", "düsseldorf": "EDDL", "köln": "EDDK", "koln": "EDDK",
                            "kolonia": "EDDK", "hof plauen": "EDQM", "EDD": "EDDB"}


def test_js_plural(aerojs):
    assert aerojs["plural"] == ["1 lotnisko", "2 lotniska", "4 lotniska", "5 lotnisk", "12 lotnisk", "22 lotniska",
                                "32 lotniska", "33 lotniska", "93 lotniska", "119 lotnisk", "469 lotnisk"]


def test_js_step_selection(aerojs):
    """Lista w jednej kolumnie: ↑ ↓ o jeden wiersz, PgUp PgDn o ekran, bez wyjścia poza listę; bez wyboru pierwszy
    wiersz (jak dotąd ↓ i ↑), pusta lista: nic."""
    assert aerojs["step"] == [0, 0, 0, 0, 4, 2, 9, 9, 0, -1, -1]
    # PgUp PgDn: wierszy w okienku bez jednego, co najmniej jeden; bez wysokości wiersza (brak --alist-row-h) o jeden
    assert aerojs["page"] == [17, 17, 1, 1, 1, 1]


def test_list_css_contract():
    """aerolist.js liczy na aerolist.css: offsetTop wierszy od okienka (position: relative na .alist-box), wysokość
    wiersza z --alist-row-h (PgUp PgDn bez wpisanej w kod liczby), licznik stałej szerokości (pisanie nie zawija paska
    i nie przesuwa listy); wiersz pod myszą ma własne tło i ramkę, inne niż wybrany."""
    import re

    from backend.app.config import ROOT_DIR
    css = (ROOT_DIR / "frontend" / "css" / "aerolist.css").read_text("utf-8")
    js = (ROOT_DIR / "frontend" / "js" / "aerolist.js").read_text("utf-8")

    def rule(sel):
        return re.search(r"(?m)^" + re.escape(sel) + r" \{([^}]*)\}", css).group(1)

    assert "position: relative" in rule(".alist-box") and re.search(r"--alist-row-h: \d+px", rule(".alist-box"))
    assert "height: var(--alist-row-h)" in rule(".alist-row") and '"--alist-row-h"' in js
    assert "|| 38" not in js
    assert re.search(r"min-width: 1\dch", rule(".alist-n"))
    hover, sel = rule(".alist-row:hover"), rule(".alist-row.sel")
    assert "var(--row-hover)" not in hover and "box-shadow" in hover and hover != sel
    assert "text-shadow" not in css
