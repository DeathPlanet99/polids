import os
import tempfile

os.environ.setdefault("POLIDS_DATABASE_URL",
                      "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main  # noqa: E402
from backend.app.routers import vatsim as vatsim_api  # noqa: E402
from backend.app.services.http_cache import UpstreamError  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.services.radio import (merge_positions, neighbour_tab, position_range, radio_names,  # noqa: E402
                                        sector_group, sector_kind, seed_json)
from backend.app.services.vatsim import fir_boundaries  # noqa: E402

FEED = {"general": {"update_timestamp": "2026-10-06T12:00:00Z"}, "pilots": [], "atis": [],
        "controllers": [
            {"callsign": "EDWW_EMS_CTR", "frequency": "125.025", "name": "Tylko VACS", "cid": 1},  # brak w .ese
            {"callsign": "EDDB_TWR", "frequency": "120.030", "name": "Jak w EuroScope", "cid": 2},  # → EDDB_N_TWR
            {"callsign": "EPWA_APP", "frequency": "128.805", "name": "Jan", "cid": 3},
            {"callsign": "EPKK_OBS", "frequency": "199.998", "name": "Obserwator", "cid": 4},
            {"callsign": "KZNY_CTR", "frequency": "125.000", "name": "Daleko", "cid": 5}]}


@pytest.fixture(scope="module")
def client(monkeypatch_module):
    async def feed():
        return FEED

    async def bookings():
        return [{"callsign": "EDWW_EMS_CTR", "cid": 1, "type": "booking", "start": "2000-01-01 10:00:00",
                 "end": "2100-01-01 12:00:00"},
                {"callsign": "KZNY_CTR", "cid": 5, "start": "2000-01-01 10:00:00", "end": "2100-01-01 12:00:00"}]

    monkeypatch_module.setattr(vatsim_api, "get_feed", feed)
    monkeypatch_module.setattr(vatsim_api, "get_bookings", bookings)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_merge_positions_adds_vacs_only():
    ese = [{"callsign": "EDWW_FLG_CTR", "name": "Bremen Radar", "frequency": "136.450", "position_id": "EDWF",
            "prefix": "EDWW"}]
    vacs = [{"id": "EDWW_FLG_CTR", "fir_dir": "EDWW", "prefixes": ["EDWW"], "frequency": "136.450",
             "facility_type": "CTR"},
            {"id": "EDDH_TWR", "fir_dir": "EDWW", "prefixes": ["EDDH"], "frequency": "126.855", "facility_type": "TWR"},
            {"id": "EDWW_FMP", "fir_dir": "EDWW", "prefixes": ["EDWW"], "frequency": "199.998", "facility_type": "FMP"}]
    out = {p["callsign"]: p for p in merge_positions(ese, vacs)}
    assert set(out) == {"EDWW_FLG_CTR", "EDDH_TWR"}  # FMP pominięte
    assert out["EDWW_FLG_CTR"]["in_ese"] and out["EDWW_FLG_CTR"]["in_vacs"] and out["EDWW_FLG_CTR"]["name"]
    assert out["EDDH_TWR"] == {**out["EDDH_TWR"], "name": None, "in_ese": False, "fir": "EDWW", "fir_name": "FIR Bremen",
                               "prefix": "EDDH", "facility": "TWR", "position_id": None}


def test_position_range():
    firs = fir_boundaries()
    assert position_range("EDWW_FLG_CTR", firs) == {"kind": "vatspy", "ids": ["EDWW-FLG"], "exact": True}
    assert position_range("ESMM_7_CTR", firs)["ids"] == ["ESMM-7"]
    assert position_range("EYVL_E_CTR", firs)["ids"] == ["EYVL-E"]
    # bez własnego sektora w VATSpy: cały FIR jako przybliżenie
    assert position_range("ESMM_6_CTR", firs) == {"kind": "vatspy", "ids": ["ESMM"], "exact": False}
    assert position_range("LKAA_CTR", firs) == {"kind": "vatspy", "ids": ["LKAA"], "exact": True}
    assert position_range("RU-NWC_FSS", firs)["ids"] == ["ULLL", "UMKK"]  # UIR → jego FIR-y
    # bez granicy w VATSpy: prefiks z vacs-data albo FIR-y z NB_ALIAS (zawsze przybliżenie)
    assert position_range("EDYY_BB_CTR", firs) == {"kind": "vatspy", "ids": ["EDWW"], "exact": False}
    assert position_range("EDUU_ALP_CTR", firs, prefix="EDUU") == {"kind": "vatspy", "ids": ["EDGG", "EDMM"],
                                                                 "exact": False}
    assert position_range("EKCH_FW_CTR", firs, prefix="EKDK") == {"kind": "vatspy", "ids": ["EKDK"], "exact": False}
    assert position_range("ESCR_CTR", firs, prefix="ESCR")["ids"] == ["ESAA"]
    assert position_range("ESDK_CTR", firs)["ids"] == ["ESAA"]
    assert position_range("XXXX_CTR", firs, prefix="XXXX") is None
    assert position_range("EDDH_TWR", firs, 53.6, 10.0) == {"kind": "circle", "nm": 10, "lat": 53.6, "lon": 10.0}
    assert position_range("EDDH_APP", firs, 53.6, 10.0)["nm"] == 30
    assert position_range("EDDB_DEL", firs, 52.4, 13.5)["kind"] == "point"
    assert position_range("ESMM_IS_APP", firs) == {"kind": "vatspy", "ids": ["ESMM"], "exact": False}


def test_sector_kind_and_group():
    cases = {("EPWW", "EPWWB"): ("acc", "Sektor B"), ("EPWW", "EPWWR-N"): ("acc", "Sektor R"),
             ("EPWW", "EPWA_APP_N_A"): ("tma", "TMA Warszawa"), ("EPWW", "EPWA_DIR11_A"): ("tma", "TMA Warszawa"),
             ("EPWW", "EPKK_DEP07"): ("tma", "TMA Kraków"), ("EPWW", "CTA01_N"): ("tma", "CTA 01"),
             ("EPWW", "EPPO_TMA_N_A"): ("tma", "TMA Poznań N"), ("EPWW", "EPPO_TMA_S_G"): ("tma", "TMA Poznań S"),
             ("EPWW", "EPPO_EPGD_TMA-CMN"): ("tma", "TMA Poznań"),
             ("EPWW", "WAW_FIS_E_LB5"): ("fis", "FIS Warszawa"),
             ("EPWW", "EPKK_CTR07"): ("ctr", "CTR Kraków"), ("EPWW", "EPDE_MCTR"): ("ctr", "CTR Dęblin"),
             ("EPWW", "FIS_GDN_W"): ("fis", "FIS Gdańsk"), ("EPWW", "EPBA_ATZ"): ("fis", "ATZ EPBA"),
             ("EPWW", "EPWW-MIDSEA"): ("fis", "FIS Gdańsk (MIDSEA)"), ("EDWW", "EDWWFLG1"): ("nb", "EDWWFLG"),
             ("ESAA", "ESMM 8-3"): ("nb", "ESMM 8"), ("LZBB", "CTR"): ("nb", "LZBB CTR"),
             ("EDMM", "EDUUSPE12"): ("nb", "EDUUSPE12")}
    for (fir, name), want in cases.items():
        assert (sector_kind(fir, name), sector_group(fir, name)) == want, name
    assert sector_kind("ESAA", "TECH ESMM IS") is None


def test_radio_positions(client):
    ps = {p["callsign"]: p for p in client.get("/api/radio/positions").json()}
    assert ps["EPWA_APP"]["name"] == "Warszawa Approach" and ps["EPWA_APP"]["in_ese"]
    ems = ps["EDWW_EMS_CTR"]  # tylko w vacs-data
    assert not ems["in_ese"] and ems["name"] is None and ems["fir_name"] == "FIR Bremen"
    assert ems["range"] == {"kind": "vatspy", "ids": ["EDWW-EMS"], "exact": True}
    assert ps["EDDH_TWR"]["range"]["kind"] == "circle" and ps["EDDH_TWR"]["range"]["lat"] > 53
    assert not any(p["facility"] in ("FMP", "TMU") for p in ps.values())
    # stanowiska sąsiadów bez granicy w VATSpy: przybliżenie z prefiksu vacs-data / NB_ALIAS
    assert ps["EKCH_FW_CTR"]["range"] == {"kind": "vatspy", "ids": ["EKDK"], "exact": False}
    assert ps["EDUU_SPE_CTR"]["range"]["ids"] == ["EDGG", "EDMM"] and ps["EDYY_BB_CTR"]["range"]["ids"] == ["EDWW"]
    no_range = [cs for cs, p in ps.items() if not p["range"] and p["facility"] in ("CTR", "FSS")]
    assert not no_range, no_range
    assert len(ps) > 500  # 199 z .ese + ponad 300 sąsiadów tylko z vacs-data


def test_radio_online_matches_vacs_positions(client):
    on = client.get("/api/radio/online").json()
    assert on["positions"]["EDWW_EMS_CTR"]["name"] == "Tylko VACS"  # dokładny znak
    assert on["positions"]["EDDB_N_TWR"]["callsign"] == "EDDB_TWR"  # prefiks + typ + częstotliwość
    assert on["positions"]["EPWA_APP"]["cid"] == 3
    cs = {c["callsign"] for c in on["controllers"]}
    assert "EDDB_TWR" in cs and "EPKK_OBS" not in cs and "KZNY_CTR" not in cs
    assert on["firs"]["EDWW-EMS"][0]["name"] == "Tylko VACS"


def test_radio_online_upstream_error(client, monkeypatch):
    async def down():
        raise UpstreamError("timeout")

    monkeypatch.setattr(vatsim_api, "get_feed", down)
    r = client.get("/api/radio/online")
    assert r.status_code == 502 and "VATSIM" in r.json()["detail"]


def test_radio_bookings_only_known_positions(client):
    b = client.get("/api/radio/bookings").json()
    assert list(b) == ["EDWW_EMS_CTR"] and b["EDWW_EMS_CTR"][0]["name"] == "Tylko VACS"


def test_radio_sectors(client):
    gj = client.get("/api/radio/sectors").json()
    by = {(f["properties"]["fir"], f["properties"]["name"]): f["properties"] for f in gj["features"]}
    flg = by[("EDWW", "EDWWFLG1")]
    assert flg["kind"] == "nb" and flg["owners"][0] == "EDWW_FLG_CTR"
    assert by[("EPWW", "EPWWB")]["kind"] == "acc" and by[("EPWW", "EPWA_CTR")]["owners"][0] == "EPWA_TWR"
    assert by[("EPWW", "EPWA_APP_N_A")]["group"] == "TMA Warszawa"
    assert by[("EPWW", "EPPO_TMA_N_A")]["group"] == "TMA Poznań N"
    assert not any(f["properties"]["name"].startswith("TECH ") for f in gj["features"])


def test_neighbour_tab():
    """Zakładki sąsiadów: EDUU i EDYY osobno (choć w vacs-data i plikach sąsiadów są w katalogach EDWW/EDMM),
    dalej katalog FIR-u z vacs-data, bez niego prefiks; Polska = None, reszta = INNE."""
    cases = {("EDUU_O12_CTR", "EDUU", "EDUU"): "EDUU", ("EDYY_BB_CTR", "EDYY", "EDWW"): "EDUU",
             ("EDYY_MH_CTR", "EDYY", "EDWW"): "EDUU", ("EDUU_SPE_CTR", "EDUU", None): "EDUU",
             ("EDWW_FLG_CTR", "EDWW", "EDWW"): "EDWW", ("EDDB_N_TWR", "EDDB", "EDWW"): "EDWW",
             ("EDGG_BAD_CTR", "EDGG", "EDMM"): "EDMM", ("EDDM_TWR", "EDDM", "EDMM"): "EDMM",
             ("EDDB_TWR", "EDDB", None): "EDWW", ("EDDP_TWR", "EDDP", None): "EDMM",
             ("LKAA_N_CTR", "LKAA", "LK"): "LKAA", ("LZBB_CTR", "LZBB", None): "LZBB", ("UKLV_CTR", "UKLV", None): "UKLV",
             ("UMMS_CTR", "UMMS", None): "UMMV", ("UMKK_CTR", "UMKK", "UMKK"): "UMKK", ("RU-NWC_FSS", "RU-NWC", None): "UMKK",
             ("EYVL_CTR", "EYVL", "EY"): "EYVL", ("ESMM_6_CTR", "ESMM", "ES"): "ESAA", ("EKCH_FW_CTR", "EKDK", "EK"): "EKDK",
             ("EURN_FSS", "EURN", None): "INNE", ("XXXX_CTR", "XXXX", "XX"): "INNE",
             ("EPWA_APP", "EPWA", None): None, ("EPWW_C_CTR", None, None): None}
    for (cs, pre, fir), want in cases.items():
        assert neighbour_tab({"callsign": cs, "prefix": pre, "fir": fir}) == want, cs


def test_radio_names_priority():
    """Znak radiowy i sektor: baza wiedzy VATSIM Germany > LOA (strona sąsiada) > plik .ese; loginy zastępcze;
    stanowiska ACC bez nazwy – znak pozostałych CTR z tym prefiksem (bez stanowisk informacji)."""
    kb = {"EDWW_MRZ_CTR": {"radio": "Bremen Radar", "sector": "Müritz (MRZ)", "freq": "124.175", "url": "https://kb/x",
                           "aliases": ["EDWW_MR1_CTR"], "limits": "GND-FL245"}}
    loa = {"EDWW": {"title": "LOA EPWW – EDWW (Bremen)", "positions": [
        {"callsign": "EDWW_MRZ_CTR", "radio": "", "sector": "Mueritz (MRZ)", "side": "NB", "note": ""},
        {"callsign": "EPSC_TWR", "radio": "Szczecin Tower", "sector": "EPSC TMA", "side": "EP", "note": ""}]},
           "LKAA": {"title": "LOA EPWW – LKAA (Praha)", "positions": [
               {"callsign": "LKAA_U_CTR", "radio": "Praha Radar", "sector": "NU", "side": "NB", "note": "FL305-FL660"},
               {"callsign": "LKAA_U_CTR", "radio": "Praha Radar", "sector": "SU", "side": "NB", "note": ""}]}}
    ps = [{"callsign": "EDWW_MRZ_CTR", "prefix": "EDWW", "facility": "CTR", "name": "Bremen Radar"},
          {"callsign": "EDWW_MR1_CTR", "prefix": "EDWW", "facility": "CTR", "name": None},
          {"callsign": "LKAA_U_CTR", "prefix": "LKAA", "facility": "CTR", "name": None},
          {"callsign": "LKAA_W_CTR", "prefix": "LKAA", "facility": "CTR", "name": "Praha Radar"},
          {"callsign": "LKAA_WU_CTR", "prefix": "LKAA", "facility": "CTR", "name": None},
          {"callsign": "LKAA_I_CTR", "prefix": "LKAA", "facility": "CTR", "name": None},
          {"callsign": "LKAA_FIC_FSS", "prefix": "LKAA", "facility": "FSS", "name": "Praha Information"},
          {"callsign": "LKPR_TWR", "prefix": "LKPR", "facility": "TWR", "name": None},
          {"callsign": "EPSC_TWR", "prefix": "EPSC", "facility": "TWR", "name": "Szczecin Tower"}]
    out = {p["callsign"]: p for p in radio_names(ps, kb, loa)}
    mrz = out["EDWW_MRZ_CTR"]
    assert (mrz["radio"], mrz["radio_src"], mrz["sector"], mrz["sector_src"]) == ("Bremen Radar", "kb", "Müritz (MRZ)", "kb")
    assert mrz["kb"] == {"url": "https://kb/x", "limits": "GND-FL245"} and mrz["loa"]["titles"] == ["LOA EPWW – EDWW (Bremen)"]
    assert out["EDWW_MR1_CTR"]["sector"] == "Müritz (MRZ)"  # login zastępczy
    u = out["LKAA_U_CTR"]
    assert (u["radio"], u["radio_src"], u["sector"], u["sector_src"]) == ("Praha Radar", "loa", "NU / SU", "loa")
    assert u["loa"]["notes"] == ["FL305-FL660"]
    assert (out["LKAA_W_CTR"]["radio"], out["LKAA_W_CTR"]["radio_src"], out["LKAA_W_CTR"]["sector"]) == ("Praha Radar", "ese", None)
    # "Praha Information" (LKAA_FIC_FSS) nie blokuje znaku "Praha Radar" pozostałym stanowiskom LKAA
    assert (out["LKAA_WU_CTR"]["radio"], out["LKAA_WU_CTR"]["radio_src"]) == ("Praha Radar", "prefix")
    assert (out["LKAA_FIC_FSS"]["radio"], out["LKAA_FIC_FSS"]["radio_src"]) == ("Praha Information", "ese")
    assert out["LKAA_I_CTR"]["radio"] is None and out["LKPR_TWR"]["radio"] is None  # informacja / lotnisko: bez zgadywania
    ep = out["EPSC_TWR"]  # Polska: bez zmian (nazwa z pliku .ese)
    assert ep["radio"] is None and ep["sector"] is None and ep["name"] == "Szczecin Tower"
    # ręcznie zepsute pliki: bez wyjątku, zostaje nazwa z pliku .ese
    junk = radio_names([{"callsign": "EDWW_FLG_CTR", "prefix": "EDWW", "facility": "CTR", "name": "Bremen Radar"}],
                       ["x"], {"EDWW": {"positions": {"a": 1}}, "LKAA": {"positions": ["x", None]}, "X": []})
    assert (junk[0]["radio"], junk[0]["radio_src"], junk[0]["sector"]) == ("Bremen Radar", "ese", None)


def test_seed_json_reloads_changed_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"a": 1}', "utf-8")
    assert seed_json(f) == {"a": 1}
    assert seed_json(f) is seed_json(f)  # z pamięci
    f.write_text('{"a": 22}', "utf-8")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert seed_json(f) == {"a": 22}
    f.unlink()
    with pytest.raises(OSError):
        seed_json(f)


def test_radio_positions_names_and_tabs(client):
    ps = {p["callsign"]: p for p in client.get("/api/radio/positions").json()}
    flg = ps["EDWW_FLG_CTR"]
    assert (flg["radio"], flg["sector"], flg["radio_src"], flg["nb_tab"]) == ("Bremen Radar", "Fläming (FLG)", "kb", "EDWW")
    assert flg["kb"]["url"].startswith("https://knowledgebase.vatsim-germany.org/")
    # EDUU i EDYY we własnej zakładce, także stanowiska z pliku .ese sąsiada (EDYY_MH_CTR) i tylko z vacs-data
    upper = [cs for cs, p in ps.items() if cs.split("_")[0] in ("EDUU", "EDYY")]
    assert len(upper) > 20 and all(ps[cs]["nb_tab"] == "EDUU" for cs in upper)
    assert ps["EDYY_CL_CTR"]["radio"] == "Maastricht Radar" and ps["EDUU_SPE_CTR"]["sector"] == "Spree (SPE)"
    assert not [cs for cs, p in ps.items() if p["nb_tab"] in ("EDWW", "EDMM") and cs.split("_")[0] in ("EDUU", "EDYY")]
    # LOA: sektor czeski, znak radiowy z LOA albo pliku .ese
    assert ps["LKAA_N_CTR"]["radio"] == "Praha Radar" and ps["LKAA_N_CTR"]["sector"] == "NL"
    assert ps["LKAA_N_CTR"]["sector_src"] == "loa" and ps["LKAA_N_CTR"]["nb_tab"] == "LKAA"
    assert (ps["LKAA_WU_CTR"]["radio"], ps["LKAA_WU_CTR"]["radio_src"]) == ("Praha Radar", "prefix")
    assert ps["EPWA_APP"]["nb_tab"] is None and ps["EPWA_APP"]["radio"] is None
    assert ps["EURN_FSS"]["nb_tab"] == "INNE"


def test_radio_loa(client):
    d = client.get("/api/radio/loa").json()
    assert set(d["firs"]) >= {"EDWW", "EDUU", "EDMM", "LKAA", "LZBB", "UKLV", "EYVL", "ESAA"}
    for key, f in d["firs"].items():
        assert f["title"] and f["pdf"].startswith("/files/docs/LOA/") and f["transfers"], key
    assert {t["dir"] for t in d["firs"]["LKAA"]["transfers"]} == {"in", "out"}
    assert client.get(d["firs"]["EDWW"]["pdf"]).status_code == 200  # PDF przez /files/docs


def test_radio_loa_errors(client, monkeypatch, tmp_path):
    """Brak albo uszkodzony loa.json: czytelny błąd LOA po polsku; lista stanowisk działa dalej (bez nazw z LOA)."""
    client.get("/api/radio/positions")  # dane z vacs-data i VATSpy już w pamięci
    monkeypatch.setattr(settings, "seed_dir", tmp_path)
    r = client.get("/api/radio/loa")
    assert r.status_code == 404 and "LOA" in r.json()["detail"]
    (tmp_path / "loa.json").write_text("{zepsuty", "utf-8")
    (tmp_path / "names_de.json").write_text("[]", "utf-8")
    r = client.get("/api/radio/loa")
    assert r.status_code == 500 and "loa.json" in r.json()["detail"]
    (tmp_path / "loa.json").write_text('{"_uwaga": "bez firs"}', "utf-8")
    assert client.get("/api/radio/loa").status_code == 500
    ps = {p["callsign"]: p for p in client.get("/api/radio/positions").json()}
    assert ps["EDWW_FLG_CTR"]["nb_tab"] == "EDWW" and ps["EDWW_FLG_CTR"]["radio"] == "Bremen Radar"  # nazwa z .ese
    assert ps["EDWW_FLG_CTR"]["radio_src"] == "ese" and ps["EDWW_FLG_CTR"]["sector"] is None


def test_positions_all_tree(client):
    """MAP, sekcja Pozycje: wszystkie stanowiska z FIR-em i grupą drzewa, FIR-y w kolejności i położenie lotnisk."""
    d = client.get("/api/nav/positions/all").json()
    ps = {p["callsign"]: p for p in d["positions"]}
    assert len(ps) > 500 and not any(p["facility"] in ("FMP", "TMU") for p in ps.values())
    assert [f["id"] for f in d["firs"]][:5] == ["EPWW", "EDWW", "EDMM", "EDUU", "EDGG"]
    assert {f["id"]: f["label"] for f in d["firs"]}["EDUU"] == "EDUU/EDYY"
    # EPWW: ACC, FIS, lotniska; na lotnisku APP przed TWR przed GND
    assert (ps["EPWW_DBF_CTR"]["fir"], ps["EPWW_DBF_CTR"]["group"]) == ("EPWW", "ACC")
    assert (ps["EPGD_L_APP"]["fir"], ps["EPGD_L_APP"]["group"]) == ("EPWW", "EPGD")
    assert ps["EPGD_APP"]["order"] < ps["EPGD_TWR"]["order"] < ps["EPGD_GND"]["order"] < ps["EPGD_ATIS"]["order"]
    assert ps["EPWA_I_APP"]["group"] == "FIS" and ps["EPBC_I_TWR"]["group"] == "EPBC"  # FIS osobno, AFIS przy lotnisku
    # sąsiedzi: tak jak zakładki RADIO, EDGG osobno
    assert (ps["EDWW_FLG_CTR"]["fir"], ps["EDWW_FLG_CTR"]["group"]) == ("EDWW", "ACC")
    assert ps["EDYY_CL_CTR"]["fir"] == "EDUU" and ps["EDGG_BAD_CTR"]["fir"] == "EDGG"
    assert (ps["EDDB_N_TWR"]["fir"], ps["EDDB_N_TWR"]["group"]) == ("EDWW", "EDDB")
    assert d["airports"]["EPGD"]["lat"] == pytest.approx(54.38, abs=0.05)
