import json
from datetime import date

from backend.app.importers.ese import parse_ese, parse_vfr_points
from backend.app.importers.navdata import parse_airways
from backend.app.importers.sct import parse_coord, parse_line_groups, parse_sct
from backend.app.services.airac import current_airac
from backend.app.services.metar import parse_metar, qfe_from_qnh
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.app.importers.callsigns import parse_gr_operator_info
from backend.app.services.lvp import evaluate as evaluate_lvp
from backend.app.services.notam import for_aerodrome, split_notams
from backend.app.services.vatsim import (airport_atc, airport_traffic, bookings_by_callsign, match_positions, online_firs,
                                        parse_atis)
from backend.app.services.runways import runway_equipment, select_runways, suggest_runway, wind_components


def test_metar_basic():
    m = parse_metar("EPWA 051630Z 29012G25KT 250V320 6000 -RA SCT012 BKN025CB 12/09 Q1008 NOSIG")
    assert (m.station, m.wind_dir, m.wind_speed, m.wind_gust) == ("EPWA", 290, 12, 25)
    assert (m.wind_var_from, m.wind_var_to) == (250, 320)
    assert m.visibility_m == 6000 and m.weather == ["-RA"]
    assert m.clouds[1] == {"cover": "BKN", "base_ft": 2500, "type": "CB"}
    assert m.ceiling_ft == 2500 and m.flight_category == "MVFR"
    assert (m.temperature, m.dewpoint, m.qnh) == (12, 9, 1008)
    assert m.trend == "NOSIG"


def test_metar_cavok_negative_temp_rvr():
    m = parse_metar("METAR EPKK 051630Z VRB02KT CAVOK M03/M05 Q1021")
    assert m.wind_variable and m.wind_dir is None and m.cavok
    assert (m.temperature, m.dewpoint) == (-3, -5) and m.flight_category == "VFR"
    m = parse_metar("EPGD 051630Z 00000KT 0300 R29/0550N FG VV001 05/05 Q1015")
    assert m.rvr[0]["runway"] == "29" and m.flight_category == "LIFR"


def test_qfe():
    assert qfe_from_qnh(1013, 362) == 1000


def test_wind_components_and_suggestion():
    head, cross = wind_components(290, 20, 295)
    assert head > 19 and abs(cross) < 2
    head, cross = wind_components(90, 10, 0)
    assert abs(head) < 0.01 and cross == 10
    rwys = [{"designator": "11", "heading": 115, "preferred": 0}, {"designator": "29", "heading": 295, "preferred": 0},
            {"designator": "15", "heading": 152, "preferred": 0}, {"designator": "33", "heading": 332, "preferred": 1}]
    best, _ = suggest_runway(rwys, 280, 15)
    assert best["designator"] == "29"
    best, reason = suggest_runway(rwys, 120, 2)
    assert best["designator"] == "33" and "preferowan" in reason


def test_runway_selection_equipment_and_lvp():
    rwys = [{"designator": d, "heading": h} for d, h in (("11", 115), ("29", 295), ("15", 152), ("33", 332))]
    eq = {"11": ["ILS", "RNP"], "33": ["ILS", "RNP"], "29": ["RNP"], "15": ["RNP"]}
    cfg = {"arr": "33", "dep": "29", "limit": 5}
    assert select_runways(rwys, 120, 2, equipment=eq, config=cfg) == {
        "arr": "33", "dep": "29", "reason": "Wiatr < 5 kt: konfiguracja preferowana lotniska"}
    # wiatr z zachodu: największa składowa czołowa na 29, ale przy LVP wybieramy kierunek z ILS (33, w plecy ≤ 5 kt)
    assert select_runways(rwys, 290, 12, equipment=eq, config=cfg)["arr"] == "29"
    lvp = select_runways(rwys, 290, 12, equipment=eq, config=cfg, low_vis="LVP")
    assert lvp["arr"] == "33" and "ILS" in lvp["reason"]
    # słaby wiatr bez konfiguracji: kierunek z ILS
    assert select_runways(rwys[:2], 200, 3, equipment=eq)["arr"] == "11"
    assert select_runways(rwys, 290, 12, config={"never": ["29"]})["arr"] != "29"
    # wyposażenie z pakietu sektorowego
    assert "ILS" in runway_equipment()["EPWA"]["33"]


def test_parse_atis():
    rw = ["11", "29", "15", "33"]
    a = parse_atis(["WARSAW CHOPIN INFORMATION K TIME 1630", "RUNWAY IN USE FOR LANDING 33, DEPARTURE RUNWAY 29"], rw)
    assert (a["letter"], a["arr"], a["dep"]) == ("K", "33", "29")
    assert parse_atis(["EPWA INFO C RWY 29 IN USE"], rw)["arr"] == "29"
    assert parse_atis(["33 FOR LANDING 29 FOR TAKEOFF"], rw, "D")["letter"] == "D"
    assert parse_atis(["RWY 07 IN USE"], rw)["arr"] is None


def test_parse_atis_approach_runway():
    # vATIS EPWA 12.10.2026 (zgłoszenie Marka): pas do lądowania tylko w zdaniu o podejściu
    rw = ["11", "29", "15", "33"]
    lines = ["THIS IS WARSAW INFORMATION GOLF . TIME 1530 .", "EXPECT UNIFORM ARRIVALS FOR RNP ZULU APPROACH RUNWAY 33 .",
             "DEPARTURES RUNWAY 29 .", "TRANSITION LEVEL 8 0 .", "FOR ATC CLEARANCE CONTACT TOWER ON 118.305 .",
             "END OF INFORMATION GOLF ."]
    a = parse_atis(lines, rw, "G")
    assert (a["letter"], a["arr"], a["dep"]) == ("G", "33", "29")
    assert parse_atis(["EXPECT ILS APPROACH RWY 33, RUNWAY 29 IN USE FOR DEPARTURE"], rw)["arr"] == "33"
    a = parse_atis(["EXPECT RNP APPROACH RUNWAY 33 DEPARTURES RUNWAY 29 TRANSITION LEVEL 80"], rw)
    assert (a["arr"], a["dep"]) == ("33", "29")
    a = parse_atis(["ARRIVALS AND DEPARTURES RUNWAY 29"], rw)
    assert (a["arr"], a["dep"]) == ("29", "29")
    # pas zamknięty nie jest pasem w użyciu
    a = parse_atis(["RUNWAY 15 CLOSED FOR LANDING . RUNWAY IN USE 33"], rw)
    assert (a["arr"], a["dep"]) == ("33", "33")


def test_airac():
    assert current_airac(date(2026, 10, 5))["ident"] == "2610"
    assert current_airac(date(2026, 10, 5))["effective"] == "2026-10-01"
    assert current_airac(date(2026, 1, 22))["ident"] == "2601"


def test_sct_coords_and_points():
    assert parse_coord("N052.10.17.000") == 52.171389
    assert parse_coord("W001.30.00.000") == -1.5
    pts = parse_sct("[VOR]\nWAR 113.450 N052.10.17.000 E020.57.45.000\n[FIXES]\nSOXER N052.02.37.800 E019.56.05.900\n")
    assert {p["ident"] for p in pts} == {"WAR", "SOXER"}


def test_airways_both_directions():
    row = "ABAKU\t51.676944\t19.081389\t14\tN871\tB\tOKENO\t51.569722\t18.593889\t\tN\tPOLON\t51.8\t19.656111\t\tY"
    segs = list(parse_airways(row))
    assert {(s["from_ident"], s["to_ident"]) for s in segs} == {("ABAKU", "OKENO"), ("ABAKU", "POLON")}


def test_ese_sector_polygon():
    text = """[POSITIONS]
EPWW_S_CTR:Warszawa Radar:123.625:SWW:S:EPWW:CTR:::0000:0000
[AIRSPACE]
SECTORLINE:1
COORD:N052.00.00.000:E019.00.00.000
COORD:N052.00.00.000:E020.00.00.000
SECTORLINE:2
COORD:N053.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E020.00.00.000
SECTORLINE:3
COORD:N053.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E019.00.00.000

SECTOR:EPWW·TEST·000·095:00000:09500
OWNER:SWW
BORDER:1:2:3
"""
    data = parse_ese(text)
    assert data["positions"][0]["position_id"] == "SWW"
    s = data["sectors"][0]
    assert (s["fir"], s["name"], s["upper_ft"], s["owners"]) == ("EPWW", "TEST", 9500, "SWW")
    assert '"Polygon"' in s["geometry"] and s["geometry"].count("[") == 2 + 4


def test_ese_sector_skips_zero_length_lines():
    # UKLV_FIR: linie 861 i 862 to dwa razy ten sam punkt; doklejone na końcu rysowały trójkąt przez cały sektor
    text = """[AIRSPACE]
SECTORLINE:1
COORD:N052.00.00.000:E019.00.00.000
COORD:N052.00.00.000:E020.00.00.000
SECTORLINE:2
COORD:N053.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E020.00.00.000
SECTORLINE:3
COORD:N053.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E019.00.00.000
SECTORLINE:9
COORD:N051.00.00.000:E025.00.00.000
COORD:N051.00.00.000:E025.00.00.000

SECTOR:UKLV·TEST·165·660:16500:66000
OWNER:UKLV
BORDER:1:9:2:3:9
"""
    ring = json.loads(parse_ese(text)["sectors"][0]["geometry"])["coordinates"][0]
    assert [25, 51] not in ring and len(ring) == 4 and ring[0] == ring[-1]


def test_notam_split():
    text = "(A1234/26 NOTAMN\nQ) EPWW/QMRLC/IV/NBO/A/000/999/5210N02058E005\nA) EPWA B) 2610050600 C) 2610051800\nE) RWY 11/29 CLSD)\n" \
           "(A1235/26 NOTAMN\nA) EPWA B) 2610050600 C) PERM\nE) TWY A CLSD)"
    n = split_notams(text)
    assert [x["id"] for x in n] == ["A1234/26", "A1235/26"]
    assert n[0]["fields"]["E"] == "RWY 11/29 CLSD"
    assert (n[0]["start"], n[0]["end"], n[0]["perm"]) == ("2026-10-05T06:00:00+00:00", "2026-10-05T18:00:00+00:00", False)
    assert n[1]["perm"] is True and n[1]["end"] is None


def test_notam_only_for_aerodrome():
    text = "(A1234/26 NOTAMN\nA) EPWA B) 2610050600 C) PERM\nE) RWY 11/29 CLSD)\n" \
           "(A1250/26 NOTAMN\nA) EPKK B) 2610050600 C) PERM\nE) RWY 07/25 CLSD)\n" \
           "(A1260/26 NOTAMN\nA) EPWA EPMO B) 2610050600 C) PERM\nE) NAV WARNING)"
    assert [n["id"] for n in for_aerodrome(split_notams(text), "EPWA")] == ["A1234/26", "A1260/26"]


def test_lvp():
    assert evaluate_lvp(parse_metar("EPWA 051630Z 31004KT 0400 R29/0350N FG VV002 08/08 Q1009"), "EPWA")["state"] == "LVP"
    prep = evaluate_lvp(parse_metar("EPWA 051630Z 31004KT 0700 BR OVC006 08/08 Q1009"), "EPWA")
    assert prep["state"] == "PREP" and "widzialność 700 m" in prep["reasons"][0]
    assert evaluate_lvp(parse_metar("EPWA 051630Z 31004KT CAVOK 08/02 Q1009"), "EPWA")["state"] is None


def _pos(cs, freq, prefix):
    return SimpleNamespace(callsign=cs, frequency=freq, prefix=prefix, position_id=cs)


def test_vatsim_matching_and_bookings():
    positions = [_pos("EPWA_APP", "128.805", "EPWA"), _pos("EPWW_C_CTR", "133.475", "EPWW")]
    ctrls = [{"callsign": "EPWA_APP", "frequency": "128.805", "name": "Jan", "cid": 1},
             {"callsign": "EPWW_C1_CTR", "frequency": "133.475", "name": "Anna", "cid": 2},
             {"callsign": "EDWW_FLG_CTR", "frequency": "136.450", "name": "Max", "cid": 3}]
    online = match_positions(ctrls, positions)
    assert online["EPWA_APP"]["name"] == "Jan" and online["EPWW_C_CTR"]["callsign"] == "EPWW_C1_CTR"
    assert "EDWW-FLG" in online_firs(ctrls)
    now = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    books = [{"cid": 5, "callsign": "EPWA_TWR", "start": fmt(now + timedelta(hours=1)), "end": fmt(now + timedelta(hours=2))},
             {"cid": 6, "callsign": "EPWA_GND", "start": fmt(now - timedelta(hours=3)), "end": fmt(now - timedelta(hours=1))},
             {"cid": 7, "callsign": "EDDB_TWR", "start": fmt(now), "end": fmt(now + timedelta(hours=1))}]
    out = bookings_by_callsign(books, "EP", now=now)
    assert list(out) == ["EPWA_TWR"] and out["EPWA_TWR"][0]["active"] is False
    # tylko bieżąca doba UTC: rezerwacja na jutro rano nie wchodzi
    tomorrow = [{"cid": 8, "callsign": "EPKK_APP", "start": fmt(now + timedelta(hours=8)), "end": fmt(now + timedelta(hours=9))}]
    assert bookings_by_callsign(tomorrow, "EP", now=now) == {}


def test_airport_atc_groups():
    ctrls = [{"callsign": "EPWA_N_APP"}, {"callsign": "EPWA_DEP"}, {"callsign": "EPWA_TWR"}, {"callsign": "EPKK_ATIS"},
             {"callsign": "EPWW_C_CTR"}, {"callsign": "EPWA_OBS"}, {"callsign": "EPGD_RMP"}, {"callsign": "SOMEONE"}]
    out = airport_atc(ctrls)
    assert sorted(out) == ["EPGD", "EPKK", "EPWA"]
    assert [c["callsign"] for c in out["EPWA"]["APP"]] == ["EPWA_N_APP", "EPWA_DEP"] and "TWR" in out["EPWA"]
    assert list(out["EPGD"]) == ["GND"] and list(out["EPKK"]) == ["ATIS"]


def test_airport_traffic():
    feed = {"pilots": [
        {"callsign": "LOT1", "latitude": 52.5, "longitude": 21.0, "groundspeed": 300, "altitude": 9000,
         "flight_plan": {"departure": "EGLL", "arrival": "EPWA"}},
        {"callsign": "LOT2", "latitude": 52.166, "longitude": 20.967, "groundspeed": 0, "altitude": 360,
         "flight_plan": {"departure": "EPWA", "arrival": "EPKK"}},
        {"callsign": "DLH1", "latitude": 50.0, "longitude": 8.0, "groundspeed": 400, "flight_plan": {"departure": "EDDF", "arrival": "EDDM"}}],
        "prefiles": [{"callsign": "LOT3", "flight_plan": {"departure": "EPWA", "arrival": "EPGD", "deptime": "1800"}}]}
    t = airport_traffic(feed, "EPWA", 52.166, 20.967)
    assert [p["callsign"] for p in t["arrivals"]] == ["LOT1"] and 15 < t["arrivals"][0]["dist_nm"] < 30
    assert t["arrivals"][0]["eta_min"] is not None
    assert t["departures"][0]["state"] == "ground" and t["prefiles"][0]["callsign"] == "LOT3"


def test_vfr_points():
    text = "[FREETEXT]\nN052.03.47.000:E020.44.35.000:EPBC VFR:A\nN052.0.0.0:E020.0.0.0:EPWA STANDS:1\n[GROUND]\n"
    pts = parse_vfr_points(text)
    assert len(pts) == 1 and pts[0]["ident"] == "A" and pts[0]["kind"] == "VFR" and abs(pts[0]["lat"] - 52.063) < 0.01


def test_gr_operator_info():
    assert parse_gr_operator_info("DHK\tC\nPLF\tMil\nXXX\t?\n") == {"DHK": "CARGO", "PLF": "MIL"}


def test_sct_line_groups_and_frame_policy():
    import httpx

    from backend.app.services.embed import frame_policy
    lines = ["EPWA TMA OUT          N052.00.00.000 E020.00.00.000 N052.10.00.000 E020.00.00.000 COLOR_TMA",
             "                      N052.10.00.000 E020.00.00.000 N052.10.00.000 E020.30.00.000 COLOR_TMA",
             "ZZ_CTR ALL            N050.00.00.000 E019.00.00.000 N050.05.00.000 E019.05.00.000"]
    g = parse_line_groups(lines)
    assert list(g) == ["EPWA TMA OUT", "ZZ_CTR ALL"] and len(g["EPWA TMA OUT"]) == 2
    assert g["EPWA TMA OUT"][1] == [[52.166667, 20.0], [52.166667, 20.5]]
    assert frame_policy(httpx.Headers({"X-Frame-Options": "SAMEORIGIN"}))[0] is False
    assert frame_policy(httpx.Headers({"Content-Security-Policy": "frame-ancestors 'self'"}))[0] is False
    assert frame_policy(httpx.Headers({"Content-Security-Policy": "frame-ancestors *"}))[0] is True
    assert frame_policy(httpx.Headers({}))[0] is True


def test_viff_departure_states_and_sector_load():
    from backend.app.services.viff import departure, hhmm, sector_load

    assert (hhmm("152000"), hhmm("1520"), hhmm(""), hhmm(None)) == ("1520", "1520", None, None)
    base = {"callsign": "X", "eobt": "1500", "ctot": "", "aobt": "", "atot": "", "atfcmStatus": "", "cdmSts": ""}
    assert departure(base)["state"] == "FI"
    assert departure({**base, "ctot": "1530", "atfcmStatus": "SAM"})["state"] == "SI"
    reg = departure({**base, "ctot": "1530", "taxi": 15})
    assert reg["delay"] == 15  # CTOT 15:30 - (EOBT 15:00 + 15 min kołowania)
    susp = departure({**base, "atfcmStatus": "FLS-NRA"})
    assert (susp["state"], susp["suspension"]) == ("SU", "FLS-NRA")
    assert departure({**base, "aobt": "1505", "atfcmStatus": "ATC_ACTIV"})["state"] == "AA"
    assert departure({**base, "atot": "1512"})["state"] == "TA"
    assert departure({**base, "cdmSts": "REA"})["ready"] is True

    # 23:50Z: lot wchodzi 23:55 i wychodzi 00:10 (przez północ), drugi 00:30-00:40, trzeci już wyszedł
    now = datetime(2026, 10, 5, 23, 50, tzinfo=timezone.utc)
    buckets = [{"hour": "23", "entriesCapacity": 30, "peakCapacity": 999, "entriesCount": 2, "peakCount": 1, "active": False,
                "flights": [["AAA1", "2355", "0010", "2355", "0010", True], ["CCC3", "2330", "2340", "2330", "2340", True]]},
               {"hour": "00", "entriesCapacity": 30, "peakCapacity": 999, "entriesCount": 1, "peakCount": 1, "active": False,
                "flights": [["BBB2", "0025", "0035", "0030", "0040", False]]}]
    s = sector_load("EP-BH", buckets, now)
    assert [f["callsign"] for f in s["flights"]] == ["AAA1", "BBB2"]
    assert [w["entries"] for w in s["windows"]] == [1, 0, 1]   # 23:50-00:10, 00:10-00:30, 00:30-00:50
    assert s["occupancy"][5] == 1 and s["occupancy"][19] == 1 and s["occupancy"][20] == 0
    assert s["peak_60"] == 1 and s["hours"][0]["entries_cap"] == 30 and s["hours"][0]["peak_cap"] is None
    assert s["flights"][1]["entry"] == "0030"


def test_runway_heading_ignores_bad_true_heading():
    from backend.app.services.runways import runway_heading
    assert runway_heading("33", 326.0) == 326.0
    assert runway_heading("01", 359.0) == 359.0
    # błąd w danych OurAirports (EPKR 16/34 z kursem 16° i 34°): kurs z numeru pasa
    assert runway_heading("16", 16.0) == 160
    assert runway_heading("34", 34.0) == 340
    assert runway_heading("09ES", None) == 90
