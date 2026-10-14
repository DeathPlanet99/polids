# Dane z AIP: pasy preferowane (AD 2.21) z oknami czasowymi i limitami wiatru, progi LVP (AD 2.22, AIP MIL AD 4.20)
# z histerezą i stanem "LVP odwołane"
import csv
import importlib.util
import json
from datetime import datetime, timedelta, timezone

from backend.app.config import DATA_DIR, settings
from backend.app.services import lvp
from backend.app.services.metar import parse_metar
from backend.app.services.runways import (config_now, eu_summer_time, preferential_now, runway_config, select_runways,
                                          window_active)

UTC = timezone.utc
DAY = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)     # czas letni (do 25.10.2026)
NIGHT = datetime(2026, 10, 14, 21, 30, tzinfo=UTC)
EPWR = [{"designator": "11", "heading": 116}, {"designator": "29", "heading": 296}]
EPMO = [{"designator": "08", "heading": 81}, {"designator": "26", "heading": 261}]
EPPO = [{"designator": "10", "heading": 108}, {"designator": "28", "heading": 288}]
EPWA = [{"designator": d, "heading": h} for d, h in (("11", 114.52), ("29", 294.55), ("15", 151.73), ("33", 331.75))]
EPWA_EQ = {"11": ["ILS"], "33": ["ILS"], "15": ["RNP"], "29": ["RNP"]}


def _pick(icao, rwys, metar, now, equipment=None, low_vis=None):
    m = parse_metar(f"{icao} 141200Z {metar} 10/05 Q1012")
    return select_runways(rwys, m.wind_dir, m.wind_speed, equipment=equipment, config=runway_config(icao),
                          gust=m.wind_gust, metar=m, now=now, low_vis=low_vis)


def test_eu_summer_time_and_windows():
    # 2026: czas letni od 29.03 01:00 UTC do 25.10 01:00 UTC
    assert not eu_summer_time(datetime(2026, 3, 29, 0, 59, tzinfo=UTC))
    assert eu_summer_time(datetime(2026, 3, 29, 1, 0, tzinfo=UTC))
    assert eu_summer_time(datetime(2026, 10, 25, 0, 59, tzinfo=UTC))
    assert not eu_summer_time(datetime(2026, 10, 25, 1, 0, tzinfo=UTC))
    night = "2100-0500 (2000-0400)"
    assert window_active(night, datetime(2026, 7, 1, 20, 30, tzinfo=UTC))       # lato: 2000-0400
    assert not window_active(night, datetime(2026, 1, 15, 20, 30, tzinfo=UTC))  # zima: 2100-0500
    assert window_active(night, datetime(2026, 1, 15, 4, 30, tzinfo=UTC))
    assert not window_active(night, datetime(2026, 7, 1, 4, 30, tzinfo=UTC))
    assert window_active("H24", DAY)


def test_runway_preference_epwr_day_and_night():
    # dzień: lądowania i starty 29; noc: lądowania 11, starty 29
    assert _pick("EPWR", EPWR, "00000KT CAVOK", DAY) == {"arr": "29", "dep": "29", "reason": "ARR/DEP 29 wg AIP (0400-2000 UTC)"}
    night = _pick("EPWR", EPWR, "00000KT CAVOK", NIGHT)
    assert (night["arr"], night["dep"]) == ("11", "29") and "2000-0400 UTC" in night["reason"]
    # w nocy wiatr z zachodu 10 kt: 11 ma wiatr w plecy ponad 5 kt, lądowania na drugim pasie z listy AIP (29)
    west = _pick("EPWR", EPWR, "29010KT 9999 FEW030", NIGHT)
    assert (west["arr"], west["dep"]) == ("29", "29") and "11: w plecy" in west["reason"]
    assert preferential_now(runway_config("EPWR"), NIGHT)["arr"] == ["11", "29"]
    # zimą okno nocne zaczyna się o 2100 UTC
    assert _pick("EPWR", EPWR, "00000KT CAVOK", datetime(2026, 12, 1, 20, 30, tzinfo=UTC))["arr"] == "29"


def test_runway_preference_epmo_night_limits():
    # EPMO: tylko w nocy 26, potem 08; w dzień składowa czołowa
    assert _pick("EPMO", EPMO, "08004KT 9999", NIGHT)["arr"] == "26"
    tail = _pick("EPMO", EPMO, "08008KT 9999", NIGHT)
    assert tail == {"arr": "08", "dep": "08", "reason": "ARR/DEP 08 wg AIP (2000-0400 UTC), bo 26: w plecy 8 kt > 5 kt"}
    # poryw liczy się do limitu: 26 z wiatrem 260/5 G12 dalej preferowany (wiatr czołowy), 080/3 G9 już nie
    assert _pick("EPMO", EPMO, "26005G12KT 9999", NIGHT)["arr"] == "26"
    assert _pick("EPMO", EPMO, "08003G09KT 9999", NIGHT)["arr"] == "08"
    cross = _pick("EPMO", EPMO, "35020KT 9999", NIGHT)
    assert "boczny 20 kt > 15 kt" in cross["reason"] and "największa składowa czołowa" in cross["reason"]
    assert _pick("EPMO", EPMO, "08004KT 9999", DAY) == {"arr": "08", "dep": "08", "reason": "Największa składowa czołowa"}


def test_runway_reason_whole_knots():
    # powód w całych węzłach jak tabela pasów w AERODROME ("TW 7 kt"), połówki w górę jak Math.round (round() z
    # Pythona dałby 6 z 6.5); limit porównany z wartością przed zaokrągleniem
    why = lambda metar: _pick("EPMO", EPMO, metar, NIGHT)["reason"]  # noqa: E731
    assert why("09007KT 9999") == "ARR/DEP 08 wg AIP (2000-0400 UTC), bo 26: w plecy 7 kt > 5 kt"   # 6.9 kt
    assert "26: w plecy 7 kt > 5 kt" in why("14113KT 9999")                                          # 6.5 kt
    # 5.4 kt > 5 kt: w całych węzłach byłoby "5 kt > 5 kt", więc jedno miejsce po przecinku
    assert "26: w plecy 5.4 kt > 5 kt" in why("10706KT 9999")
    assert "boczny 20 kt > 15 kt" in why("35020KT 9999")
    # z porywem: składowa z porywu, więc "z porywami" (tabela pasów pokazuje TW ze średniego wiatru: tu 26 "TW 5")
    assert why("09005G12KT 9999") == "ARR/DEP 08 wg AIP (2000-0400 UTC), bo 26: w plecy z porywami 12 kt > 5 kt"
    assert "boczny z porywami 20 kt > 15 kt" in why("35012G20KT 9999")
    assert "wiatr zmienny z porywami 12 kt > 5 kt" in why("VRB04G12KT 9999")


def test_runway_preference_not_decisive_weather():
    eq = {"11": ["ILS"], "29": ["ILS"]}
    # podstawa 400 ft < 500 ft: lądowania bez pierwszeństwa AIP, starty dalej wg AIP (wiatr 110/8: 29 ma 8 kt w plecy)
    low = _pick("EPWR", EPWR, "11008KT 9999 BKN004", DAY, eq)
    assert (low["arr"], low["dep"]) == ("11", "11")
    assert "ARR bez pierwszeństwa AIP: podstawa 400 ft < 500 ft" in low["reason"]
    assert "DEP 11 wg AIP" in low["reason"] and "29: w plecy 8 kt > 5 kt" in low["reason"]
    ts = _pick("EPWR", EPWR, "29004KT 9999 TSRA BKN030CB", DAY, eq)
    assert ts["reason"].startswith("ARR/DEP bez pierwszeństwa AIP: burza (TS)")
    # podejście bez ILS (EPMO 26 tylko RNP): przy podstawie 700 ft < 800 ft lądowania bez pierwszeństwa, starty 26
    np = _pick("EPMO", EPMO, "08002KT 9999 BKN007", NIGHT, {"08": ["ILS"], "26": ["RNP"]})
    assert "podejście bez ILS" in np["reason"] and np["dep"] == "26"


def test_runway_preference_epgd_and_eppo_phase_limits():
    # EPGD: AIP podaje tylko starty (H24 29, potem 11)
    gd = _pick("EPGD", [{"designator": "11", "heading": 113}, {"designator": "29", "heading": 293}], "11004KT CAVOK", NIGHT)
    assert (gd["arr"], gd["dep"]) == ("11", "29") and "ARR: największa składowa czołowa" in gd["reason"]
    # EPPO: limit wiatru w plecy 5 kt dla lądowań i 10 kt dla startów
    po = _pick("EPPO", EPPO, "10008KT CAVOK", DAY)
    assert (po["arr"], po["dep"]) == ("10", "28")
    # w nocy brak pierwszeństwa dla lądowań, starty dalej 28
    assert _pick("EPPO", EPPO, "10003KT CAVOK", NIGHT)["dep"] == "28"


def test_runway_preference_variable_wind():
    # wiatr zmienny (VRB): poza pierwszeństwem AIP kierunek z ILS jak przy słabym wietrze, nie "brak danych o wietrze"
    # i nie pierwszy pas z listy (EPGD 11 i EPPO 10 bez ILS: lądowania przeciwnie do startów)
    gd = _pick("EPGD", [{"designator": "11", "heading": 113}, {"designator": "29", "heading": 293}], "VRB02KT CAVOK", DAY,
               {"11": ["RNP", "VOR"], "29": ["ILS"]})
    assert (gd["arr"], gd["dep"]) == ("29", "29") and "ARR: wiatr zmienny 2 kt: kierunek wyposażony w ILS" in gd["reason"]
    po = _pick("EPPO", EPPO, "VRB02KT CAVOK", NIGHT, {"10": ["RNP", "VOR"], "28": ["ILS"]})
    assert (po["arr"], po["dep"]) == ("28", "28")
    mo = _pick("EPMO", EPMO, "VRB02KT CAVOK", DAY, {"08": ["ILS"], "26": ["RNP"]})
    assert mo["arr"] == "08" and "Brak danych" not in mo["reason"] and "zmienny 2 kt" in mo["reason"]
    # bez ILS: pierwszy pas z listy, ale z wiatrem w uzasadnieniu; bez wiatru w ogóle jak dotąd
    assert _pick("EPMO", EPMO, "VRB02KT CAVOK", DAY)["reason"] == "Wiatr zmienny 2 kt: pierwszy pas z listy"
    assert select_runways(EPMO, None, None, config=runway_config("EPMO"), now=DAY)["reason"] == \
        "Brak danych o wietrze: pierwszy pas z listy"


def test_config_now():
    assert config_now(runway_config("EPWR"), NIGHT) == {"arr": "11", "dep": "29"}
    assert config_now(runway_config("EPWR"), DAY) == {"arr": "29", "dep": "29"}
    assert config_now(runway_config("EPGD"), DAY) == {"arr": None, "dep": "29"}
    # EPWA: AD 2.21 pkt 1 (tekst AIP od Marka), H24 doloty 33, odloty 29
    assert config_now(runway_config("EPWA"), DAY) == config_now(runway_config("EPWA"), NIGHT) == {"arr": "33", "dep": "29"}
    assert "verified" not in runway_config("EPWA") and runway_config("EPWA")["source"].startswith("AIP AD 2.21")
    assert config_now(runway_config("EPKK"), DAY) == {"arr": None, "dep": None}
    # wszystkie wpisy (poza DEFAULT i uwagą) mają źródło
    cfg = json.loads((settings.seed_dir / "runway_config.json").read_text("utf-8"))
    assert all(v.get("source") for k, v in cfg.items() if k not in ("DEFAULT", "_uwaga"))


def test_lvp_thresholds_per_aerodrome():
    cfg = json.loads((settings.seed_dir / "lvp.json").read_text("utf-8"))
    civil = ["EPWA", "EPMO", "EPLL", "EPSY", "EPKK", "EPKT", "EPRZ", "EPLB", "EPWR", "EPGD", "EPPO", "EPSC", "EPBY"]
    for icao in civil + ["EPKS", "EPMB", "EPMI", "EPPW", "EPSN"]:
        e = cfg[icao]
        assert e["source"] and e["lvp"] and e["cancel"], icao
    assert cfg["EPKT"]["lvp"] == {"rvr_m": ["<", 550], "ceiling_ft": ["<", 200], "logic": "or"}
    assert cfg["EPKK"]["lvp"]["ceiling_ft"] == ["<=", 200] and "prep" not in cfg["EPKK"]
    assert cfg["EPWR"]["cancel"]["rvr_m"] == [">", 600]
    assert all(cfg[i]["scope"] == "DEP" and "vis_m" in cfg[i]["lvp"] for i in ("EPKS", "EPMB", "EPMI", "EPPW", "EPSN"))
    # EPWA i EPPO z tekstu AD 2.22 od Marka: EPWA bez fazy przygotowania, odwołanie RVR > 550 m (ściśle)
    assert not any(cfg[i].get("verified") is False for i in civil)
    assert cfg["EPWA"]["lvp"] == {"rvr_m": ["<", 550], "ceiling_ft": ["<", 200], "logic": "or"} and "prep" not in cfg["EPWA"]
    assert cfg["EPWA"]["cancel"] == {"rvr_m": [">", 550], "ceiling_ft": [">=", 200], "logic": "and"}
    assert cfg["EPPO"]["prep"] == {"rvr_m": ["<=", 800], "ceiling_ft": ["<=", 300], "logic": "or"}
    assert cfg["EPPO"]["cancel"] == {"rvr_m": [">", 550], "ceiling_ft": [">", 200], "logic": "and"}
    for icao in ("EPRA", "EPZG", "EPDE", "EPLY"):
        assert cfg[icao]["applies"] is False and cfg[icao]["reason"]
    assert lvp.rules(lvp.thresholds("EPKT")) == [
        "Przygotowanie: RVR ≤ 800 m lub podstawa ≤ 300 ft", "LVP: RVR < 550 m lub podstawa < 200 ft",
        "Odwołanie: RVR ≥ 600 m i podstawa ≥ 200 ft"]
    assert lvp.rules(lvp.thresholds("EPLL"))[0] == "Przygotowanie: AIP nie podaje"


def _ev(icao, metar, now=None):
    """Ocena METAR 'ddhhmmZ ...' z 14.10.2026; bez now: chwila 2 min po obserwacji (METAR świeży)."""
    if now is None:
        d, hh, mm = int(metar[:2]), int(metar[2:4]), int(metar[4:6])
        now = datetime(2026, 10, d, hh, mm, tzinfo=UTC) + timedelta(minutes=2)
    return lvp.evaluate(parse_metar(f"{icao} {metar}"), icao, now=now)


def test_lvp_transitions_with_cancel():
    # brak -> przygotowanie -> LVP -> LVP dalej (powyżej progu wprowadzenia, poniżej odwołania) -> odwołane -> brak
    assert _ev("EPKT", "140600Z 26004KT 9999 FEW030 08/06 Q1012")["state"] is None
    prep = _ev("EPKT", "140630Z 26004KT 1500 BR OVC003 08/07 Q1012")
    assert prep["state"] == "PREP" and prep["since"] == "06:30Z" and "podstawa 300 ft ≤ 300 ft" in prep["reasons"]
    on = _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012")
    assert on["state"] == "LVP" and on["since"] == "07:00Z" and "RVR 26 450 m < 550 m" in on["reasons"]
    held = _ev("EPKT", "140730Z 26004KT 0600 R26/0550N FG BKN003 08/08 Q1012")
    assert held["state"] == "LVP" and held["since"] == "07:00Z"
    assert held["reasons"] == ["w mocy od 07:00Z", "do odwołania: RVR 26 ≥ 600 m (jest 550 m)"]
    # ten sam METAR liczony ponownie daje ten sam wynik
    assert _ev("EPKT", "140730Z 26004KT 0600 R26/0550N FG BKN003 08/08 Q1012")["state"] == "LVP"
    off = _ev("EPKT", "140800Z 26004KT 1200 BR BKN003 08/08 Q1012")
    assert off["state"] == "CANCELLED" and (off["since"], off["until"]) == ("08:00Z", "08:30Z")
    assert off["reasons"][0].startswith("warunki odwołania")
    # przez 30 min stan odwołania, nawet gdy spełnione są warunki przygotowania
    assert _ev("EPKT", "140820Z 26004KT 1200 BR BKN003 08/08 Q1012")["state"] == "CANCELLED"
    assert _ev("EPKT", "140830Z 26004KT 9999 FEW030 10/08 Q1012")["state"] is None
    # trzy fazy z progami zawsze w odpowiedzi (dymek w interfejsie)
    assert set(off["thresholds"]) == {"prep", "lvp", "cancel"} and off["source"] == "AIP AD 2.22 pkt 5.2.1-5.2.3"


def test_lvp_cancelled_back_to_lvp_and_fresh_start():
    _ev("EPWR", "140700Z 29004KT 0300 R29/0300N FG VV001 08/08 Q1012")
    assert _ev("EPWR", "140730Z 29004KT 1500 BR BKN004 08/08 Q1012")["state"] == "CANCELLED"
    again = _ev("EPWR", "140800Z 29004KT 0300 R29/0350N FG VV001 08/08 Q1012")
    assert again["state"] == "LVP" and again["since"] == "08:00Z"
    # bez historii (np. po starcie aplikacji) RVR między progiem wprowadzenia i odwołania to tylko przygotowanie
    lvp.reset_memory()
    assert _ev("EPWR", "140830Z 29004KT 0600 R29/0575N BR BKN004 08/08 Q1012")["state"] == "PREP"
    # EPWR: odwołanie dopiero przy RVR > 600 m (ściśle)
    _ev("EPWR", "140900Z 29004KT 0300 R29/0300N FG VV001 08/08 Q1012")
    assert _ev("EPWR", "140930Z 29004KT 0600 R29/0600N BR BKN004 08/08 Q1012")["state"] == "LVP"


def test_lvp_military_visibility_departures_only():
    prep = _ev("EPKS", "140600Z 29004KT 1100 BR BKN003 08/07 Q1012")
    assert prep["state"] == "PREP" and prep["scope"] == "DEP"
    on = _ev("EPKS", "140630Z 29004KT 0800 FG BKN002 08/08 Q1012")
    assert on["state"] == "LVP" and "widzialność 800 m ≤ 800 m" in on["reasons"]
    off = _ev("EPKS", "140700Z 29004KT 0900 BR BKN002 08/08 Q1012")
    assert off["state"] == "CANCELLED"
    assert lvp.rules(lvp.thresholds("EPKS"))[1] == "LVP: widzialność ≤ 800 m (RVR 550 m)"


def test_lvp_not_applicable():
    for icao in ("EPRA", "EPBK", "EPDE"):
        r = _ev(icao, "140600Z 29004KT 0100 FG VV001 08/08 Q1012")
        assert r["applies"] is False and r["state"] is None and r["reason"] and r["rules"] == []
    assert lvp.evaluate(None, "EPKT")["applies"] is True and lvp.evaluate(None, "EPKT")["state"] is None


def test_thunderstorm_without_rain_blocks_aip_preference():
    # burza bez opadu (TS) i w pobliżu (VCTS) też jest zjawiskiem; AIP: przy burzy hałas nie decyduje
    for wx in ("TS", "VCTS"):
        assert parse_metar(f"EPWR 141200Z 29004KT 9999 {wx} SCT030CB 10/05 Q1012").weather == [wx]
        ts = _pick("EPWR", EPWR, f"29004KT 9999 {wx} SCT030CB", DAY, {"11": ["ILS"], "29": ["ILS"]})
        assert ts["reason"].startswith("ARR/DEP bez pierwszeństwa AIP: burza (TS)"), wx
    assert parse_metar("EPWR 141200Z 29004KT 9999 VCSH 10/05 Q1012").weather == ["VCSH"]


def test_runway_preference_variable_wind_over_limit():
    # VRB 7 kt: kierunku nie znamy, więc wiatr w plecy może przekroczyć 5 kt (EPMO w nocy bez pierwszeństwa AIP)
    vrb = _pick("EPMO", EPMO, "VRB07KT 9999", NIGHT)
    assert "bez pierwszeństwa AIP: wiatr zmienny 7 kt > 5 kt" in vrb["reason"]
    assert _pick("EPMO", EPMO, "VRB03KT 9999", NIGHT)["arr"] == "26"


def test_lvp_ignores_aip_noise_preference():
    # EPGD, płytka mgła: RVR 450 m (LVP), widzialność 2000 m nad progiem AIP 1900 m; lądowania i starty razem
    m = parse_metar("EPGD 140600Z 11004KT 2000 R11/0450N R29/0500N BCFG FEW002 08/08 Q1012")
    rw = [{"designator": "11", "heading": 113}, {"designator": "29", "heading": 293}]
    eq = {"11": ["ILS"], "29": ["ILS"]}
    t = datetime(2026, 10, 14, 6, 5, tzinfo=UTC)
    assert lvp.evaluate(m, "EPGD", now=t)["state"] == "LVP"
    on = select_runways(rw, m.wind_dir, m.wind_speed, equipment=eq, low_vis="LVP", config=runway_config("EPGD"),
                        metar=m, now=t)
    assert (on["arr"], on["dep"]) == ("11", "11") and on["reason"].startswith("LVP w mocy")
    # przygotowanie LVP: pas preferowany z AIP do startów zostaje
    prep = select_runways(rw, m.wind_dir, m.wind_speed, equipment=eq, low_vis="PREP", config=runway_config("EPGD"),
                          metar=m, now=t)
    assert prep["dep"] == "29"


def test_runway_preference_epwa():
    # AD 2.21: doloty 33, 11, 15, 29; odloty 29, 15, 33, 11 (H24); wiatr w plecy do 5 kt, boczny do 15 kt
    west = _pick("EPWA", EPWA, "28015KT CAVOK", NIGHT, EPWA_EQ)
    assert west == {"arr": "33", "dep": "29", "reason": "ARR 33 wg AIP (H24); DEP 29 wg AIP (H24)"}
    # wiatr z południowego wschodu: 33 i 29 z wiatrem w plecy, lądowania na 11, starty z 15
    se = _pick("EPWA", EPWA, "15020KT CAVOK", DAY, EPWA_EQ)
    assert (se["arr"], se["dep"]) == ("11", "15") and "33: w plecy" in se["reason"] and "29: w plecy" in se["reason"]
    # mgła: AIP nie daje pierwszeństwa przy widzialności < 1900 m
    fog = _pick("EPWA", EPWA, "28004KT 1200 BR OVC004", DAY, EPWA_EQ)
    assert "bez pierwszeństwa AIP: widzialność 1200 m < 1900 m" in fog["reason"]


def test_runway_preference_epwa_no_head_on():
    # starty nie pod prąd lądowań, gdy AIP daje inny pas w limicie: 190/10 lądowania 11, starty 15 (nie 29)
    s = _pick("EPWA", EPWA, "19010KT CAVOK", DAY, EPWA_EQ)
    assert (s["arr"], s["dep"]) == ("11", "15") and "DEP 15 wg AIP (H24), bo 29: przeciwnie do lądowań na 11" in s["reason"]
    # 070/10: lądowania 33, starty 33 (29 z wiatrem w plecy, 15 pod prąd lądowań)
    e = _pick("EPWA", EPWA, "07010KT CAVOK", DAY, EPWA_EQ)
    assert (e["arr"], e["dep"]) == ("33", "33") and "15: przeciwnie do lądowań na 33" in e["reason"]
    # lotnisko z jednym pasem: AIP dopuszcza starty z przeciwnego kierunku (EPPO lądowania 10, starty 28)
    assert (lambda r: (r["arr"], r["dep"]))(_pick("EPPO", EPPO, "10008KT CAVOK", DAY)) == ("10", "28")


def test_runway_preference_epwa_departures_without_ils():
    # hałas nie decyduje (widzialność < 1900 m, IMC): lądowania z ILS (przy ciszy 33 jak w AIP, nie 11 z bazy), starty
    # dalej z listy AIP, nie z ILS (33)
    for metar in ("29006KT 1500 BR BKN008", "29006KT 0600 R33/0700N R29/0700N FG BKN003", "00000KT 1500 BR OVC005"):
        r = _pick("EPWA", EPWA, metar, DAY, EPWA_EQ, "IMC")
        assert (r["arr"], r["dep"]) == ("33", "29"), metar
        assert r["reason"].endswith("ARR: warunki IMC: kierunek z ILS (wiatr w plecy ≤ 5 kt); DEP 29 z listy AIP"), metar
    se = _pick("EPWA", EPWA, "15010KT 1800 BR", DAY, EPWA_EQ, "IMC")
    assert (se["arr"], se["dep"]) == ("11", "15") and "DEP 15 z listy AIP, bo 29: w plecy 8 kt > 5 kt" in se["reason"]
    # wiatr zmienny ponad limit: kierunek z ILS pierwszy w kolejności AIP (33, nie 11 z bazy), starty z listy AIP
    for metar in ("VRB06KT CAVOK", "VRB08KT CAVOK", "VRB04KT CAVOK"):
        assert (lambda r: (r["arr"], r["dep"]))(_pick("EPWA", EPWA, metar, DAY, EPWA_EQ)) == ("33", "29"), metar
    assert _pick("EPWA", EPWA, "VRB06KT CAVOK", DAY, EPWA_EQ)["reason"] == (
        "ARR/DEP bez pierwszeństwa AIP: wiatr zmienny 6 kt > 5 kt; ARR: wiatr zmienny 6 kt: kierunek wyposażony w ILS; "
        "DEP 29 z listy AIP")
    # lotnisko z jednym pasem: starty z listy AIP tylko w kierunku lądowań (EPWR w mgle, cisza: 29 i 29)
    wr = _pick("EPWR", EPWR, "00000KT 1500 BR OVC005", NIGHT, {"11": ["ILS"], "29": ["ILS"]}, "IMC")
    assert wr["arr"] == wr["dep"]


def _lvp(icao, rwys, metar, eq, cfg=None):
    m = parse_metar(f"{icao} 141200Z {metar} 08/08 Q1012")
    return select_runways(rwys, m.wind_dir, m.wind_speed, equipment=eq, low_vis="LVP",
                          config=runway_config(icao) if cfg is None else cfg, gust=m.wind_gust, metar=m, now=DAY)


def test_lvp_runways_from_aip():
    # EPWA w LVP (AD 2.22 pkt 7.1): lądowania 33 (CAT IIIA) albo 11 (CAT II), starty 29 albo 15
    cfg, t = runway_config("EPWA"), DAY
    calm = select_runways(EPWA, None, None, equipment=EPWA_EQ, low_vis="LVP", config=cfg, now=t)
    assert calm == {"arr": "33", "dep": "29", "reason": "LVP w mocy (AIP AD 2.22): ARR 33, DEP 29; brak danych o wietrze"}
    assert _lvp("EPWA", EPWA, "VRB12KT 0400 R33/0350N FG VV002", EPWA_EQ)["reason"].endswith("; wiatr zmienny 12 kt")
    se = select_runways(EPWA, 150, 8, equipment=EPWA_EQ, low_vis="LVP", config=cfg, now=t)
    assert (se["arr"], se["dep"]) == ("11", "15")
    assert se["reason"] == "LVP w mocy (AIP AD 2.22): ARR 11, bo 33: w plecy 8 kt > 5 kt; DEP 15, bo 29: przeciwnie do lądowań na 11"
    # poryw liczy się do wiatru w plecy (150/4 G12: 33 z porywem 12 kt w plecy)
    gust = select_runways(EPWA, 150, 4, gust=12, equipment=EPWA_EQ, low_vis="LVP", config=cfg, now=t)
    assert (gust["arr"], gust["dep"]) == ("11", "15") and "33: w plecy z porywami 12 kt > 5 kt" in gust["reason"]
    # starty nie pod prąd lądowań: 060/10 lądowania 33, starty 29 mimo 6 kt w plecy (15 to przeciwny kierunek 33)
    ne = _lvp("EPWA", EPWA, "06010KT 0400 R33/0350N FG VV002", EPWA_EQ)
    assert (ne["arr"], ne["dep"]) == ("33", "29")
    assert "DEP 29 mimo wiatru w plecy 6 kt > 5 kt, bo 15: przeciwnie do lądowań na 33" in ne["reason"]
    # EPPO (AD 2.22 pkt 6.1, 6.4.4): ILS CAT II tylko RWY 28, starty z 10 albo 28; 28 zostaje mimo wiatru w plecy,
    # starty też z 28 (limit startów 10 kt, nigdy pod prąd lądowań we mgle)
    po = select_runways(EPPO, 100, 8, equipment={"28": ["ILS"]}, low_vis="LVP", config=runway_config("EPPO"), now=t)
    assert po == {"arr": "28", "dep": "28", "reason": "LVP w mocy (AIP AD 2.22): ARR 28 mimo wiatru w plecy 8 kt > 5 kt; DEP 28"}
    po = select_runways(EPPO, 100, 12, equipment={"28": ["ILS"]}, low_vis="LVP", config=runway_config("EPPO"), now=t)
    assert (po["arr"], po["dep"]) == ("28", "28") and "DEP 28 mimo wiatru w plecy 12 kt > 10 kt" in po["reason"]
    # przygotowanie LVP: listy LVP z AIP jeszcze nie obowiązują, zostają pasy preferowane AD 2.21
    prep = select_runways(EPWA, 150, 3, equipment=EPWA_EQ, low_vis="PREP", config=cfg, now=t)
    assert (prep["arr"], prep["dep"]) == ("33", "29")


def test_lvp_ils_category_and_departure_rvr():
    # RWY 11 tylko CAT II: przy RVR 250 m lądowania na 33 (CAT IIIA) mimo wiatru w plecy, starty 29 (nie 15 pod prąd)
    low = _lvp("EPWA", EPWA, "15008KT 0300 R33/0250N R11/0250N R29/0300N FG VV001", EPWA_EQ)
    assert (low["arr"], low["dep"]) == ("33", "29")
    assert low["reason"].startswith("LVP w mocy (AIP AD 2.22): ARR 33 mimo wiatru w plecy 8 kt > 5 kt, "
                                    "bo 11: CAT II: RVR 250 m < 300 m; DEP 29 mimo wiatru w plecy 7 kt > 5 kt")
    # liczy się RVR pasa: R11 400 m wystarcza do CAT II
    assert _lvp("EPWA", EPWA, "15008KT 0300 R33/0250N R11/0400N FG VV001", EPWA_EQ)["arr"] == "11"
    # bez grupy RVR widzialność
    assert _lvp("EPWA", EPWA, "15008KT 0200 FG VV001", EPWA_EQ)["arr"] == "33"
    # jedyny pas CAT II poniżej minimów: zostaje, z uwagą (EPPO 28)
    po = _lvp("EPPO", EPPO, "28004KT 0200 R28/0200N FG VV001", {"28": ["ILS"]})
    assert po["arr"] == "28" and "ARR 28 poniżej minimów CAT II: RVR 200 m < 300 m" in po["reason"]
    # AIP: brak startów przy jakimkolwiek RVR < 125 m
    stop = _lvp("EPWA", EPWA, "15008KT 0100 R33/0250N R11/0250N R29/0100N R15/0100N FG VV001", EPWA_EQ)
    assert stop["reason"].endswith("; starty niedozwolone: RVR 100 m < 125 m")
    assert _lvp("EPPO", EPPO, "10008KT 0100 R28/0100N FG VV001", {"28": ["ILS"]})["reason"].endswith(
        "; starty niedozwolone: RVR 100 m < 125 m")
    assert "niedozwolone" not in _lvp("EPWA", EPWA, "15008KT 0300 R33/0250N R29/0125N FG VV001", EPWA_EQ)["reason"]


def test_lvp_phase_without_list():
    # faza bez listy z AIP: kierunek z ILS, z właściwą etykietą (tu lądowania) i bez powtórzonego "LVP w mocy"
    cfg = {**runway_config("EPWA"), "lvp": {"arr": ["99"], "dep": ["29", "15"]}}
    r = select_runways(EPWA, 150, 8, equipment=EPWA_EQ, low_vis="LVP", config=cfg, now=DAY)
    assert (r["arr"], r["dep"]) == ("11", "15")
    assert r["reason"] == ("LVP w mocy (AIP AD 2.22): DEP 15, bo 29: przeciwnie do lądowań na 11; "
                           "ARR: kierunek z ILS (wiatr w plecy ≤ 5 kt)")
    cfg = {**runway_config("EPWA"), "lvp": {"arr": [], "dep": ["29"]}}
    assert "; ARR: kierunek z ILS" in select_runways(EPWA, 150, 8, equipment=EPWA_EQ, low_vis="LVP", config=cfg,
                                                     now=DAY)["reason"]


def test_lvp_older_metar_keeps_newer_state():
    # AERODROME ma już METAR 07:30 (LVP), OVERVIEW z własnej pamięci podręcznej jeszcze 07:00: bez fałszywego odwołania
    t = datetime(2026, 10, 14, 7, 33, tzinfo=UTC)
    assert _ev("EPKT", "140730Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012", t)["state"] == "LVP"
    old = _ev("EPKT", "140700Z 26004KT 0700 R26/0700N BR BKN003 08/08 Q1012", t)
    assert old["state"] == "LVP" and old["since"] == "07:30Z" and "RVR 26 450 m < 550 m" in old["reasons"]
    # starszy METAR nie zmienia pamięci: następny METAR liczony od LVP
    held = _ev("EPKT", "140800Z 26004KT 0600 R26/0575N BR BKN003 08/08 Q1012")
    assert held["state"] == "LVP" and held["reasons"][0] == "w mocy od 07:30Z"
    # i odwrotnie: starszy METAR z LVP nie włącza LVP, gdy nowszy go nie wymaga
    lvp.reset_memory()
    _ev("EPKT", "140730Z 26004KT 9999 FEW030 10/08 Q1012", t)
    assert _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012", t)["state"] is None


def test_lvp_stale_history_starts_fresh():
    # LVP o 07:00, potem nikt nie oglądał lotniska do 11:00: RVR 575 m to tylko przygotowanie, nie "LVP od 07:00Z"
    _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012")
    late = _ev("EPKT", "141100Z 26004KT 0600 R26/0575N BR BKN004 08/08 Q1012")
    assert late["state"] == "PREP" and late["since"] == "11:00Z"
    # przerwa do 90 min (jeden METAR pominięty) nie przerywa histerezy
    lvp.reset_memory()
    _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012")
    assert _ev("EPKT", "140830Z 26004KT 0600 R26/0575N BR BKN004 08/08 Q1012")["state"] == "LVP"


def test_lvp_cancelled_expires_by_clock():
    _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012")
    off = _ev("EPKT", "140730Z 26004KT 9999 FEW030 10/08 Q1012")
    assert off["state"] == "CANCELLED" and off["until"] == "08:00Z"
    # ten sam (stary) METAR po 08:00: odwołanie już nie trwa
    assert _ev("EPKT", "140730Z 26004KT 9999 FEW030 10/08 Q1012", datetime(2026, 10, 14, 13, 30, tzinfo=UTC))["state"] is None
    # po 30 min zostaje przygotowanie, jeśli METAR je spełnia
    lvp.reset_memory()
    _ev("EPKT", "140700Z 26004KT 0400 R26/0450N FG VV001 08/08 Q1012")
    _ev("EPKT", "140730Z 26004KT 1200 BR BKN003 08/08 Q1012")
    late = _ev("EPKT", "140730Z 26004KT 1200 BR BKN003 08/08 Q1012", datetime(2026, 10, 14, 8, 5, tzinfo=UTC))
    assert late["state"] == "PREP" and late["until"] is None


def test_lvp_military_rvr_equivalent_without_visibility():
    # EPKS: METAR bez widzialności, tylko RVR; próg widzialności 800 m liczony jako RVR 550 m
    on = _ev("EPKS", "140630Z 29004KT R29/0500N FG BKN002 08/08 Q1012")
    assert on["state"] == "LVP" and "RVR 29 500 m ≤ 550 m" in on["reasons"]
    off = _ev("EPKS", "140700Z 29004KT R29/0600N BKN002 08/08 Q1012")
    assert off["state"] == "CANCELLED" and off["reasons"][0] == "warunki odwołania: RVR 29 600 m > 550 m"
    # odwołanie z widzialnością: powód z wartością z METAR
    lvp.reset_memory()
    _ev("EPKS", "140630Z 29004KT 0800 FG BKN002 08/08 Q1012")
    assert _ev("EPKS", "140700Z 29004KT 0900 BR BKN002 08/08 Q1012")["reasons"][0] == \
        "warunki odwołania: widzialność 900 m > 800 m"


def test_runway_config_hours_all_parse():
    # zły format godzin w runway_config.json dawałby błąd 500 w /status
    cfg = json.loads((settings.seed_dir / "runway_config.json").read_text("utf-8"))
    for icao, c in cfg.items():
        for rule in (c.get("preferential") or []) if isinstance(c, dict) else []:
            for t in (DAY, NIGHT, datetime(2026, 1, 15, 4, 30, tzinfo=UTC)):
                window_active(rule.get("hours", "H24"), t)
            assert rule.get("arr") or rule.get("dep"), icao


def _seed_rows(name):
    with open(settings.seed_dir / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_seed_runways_unique_and_elevations():
    # pasy rozróżniamy po oznaczeniu (wybór pasa, AERODROME, AWOS): jedno oznaczenie na lotnisko
    seen = set()
    for r in _seed_rows("runways.csv"):
        for end in (r["le_ident"], r["he_ident"]):
            assert (r["airport_ident"], end) not in seen, (r["airport_ident"], end)
            seen.add((r["airport_ident"], end))
    # lotniska z pasami mają elewację (punkt podejścia w /api/meteo/approach-points liczony od elewacji)
    ap = {a["ident"]: a for a in _seed_rows("airports.csv")}
    assert all(ap[i]["elevation_ft"] for i, _ in seen)
    assert (ap["EPDS"]["elevation_ft"], ap["EPDS"]["elevation_source"]) == ("1644", "lotniska.dlapilota.pl")
    # EPRA: TWR cywilna i wojskowa na tej samej częstotliwości rozróżnia uwaga
    radom = [f["note"] for f in _seed_rows("frequencies.csv") if f["airport_ident"] == "EPRA" and f["service"] == "TWR"]
    assert radom == ["TWR cywilna", "TWR wojskowa (MIL)"]


def test_seed_skips_rows_without_aerodrome(tmp_path, monkeypatch, caplog):
    # lotnisko usunięte z airports.csv (np. po aktualizacji z OurAirports): bez błędu klucza obcego przy starcie
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from backend.app.database import Base, _make_engine
    from backend.app.importers.seed import seed_aerodromes
    from backend.app.models import Frequency, Runway

    for name in ("airports.csv", "runways.csv", "frequencies.csv"):
        rows = [r for r in _seed_rows(name) if name != "airports.csv" or r["ident"] != "EPKB"]
        with open(tmp_path / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    monkeypatch.setattr(settings, "seed_dir", tmp_path)
    eng = _make_engine(f"sqlite:///{(tmp_path / 'seed.db').as_posix()}")
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        seed_aerodromes(db)
        db.commit()
        assert not db.scalar(select(func.count()).select_from(Frequency).where(Frequency.aerodrome_icao == "EPKB"))
        assert db.scalar(select(func.count()).select_from(Runway).where(Runway.aerodrome_icao == "EPKT")) == 2
    assert "frequencies.csv: lotniska EPKB nie ma w airports.csv" in caplog.text
    eng.dispose()


def test_fetch_ourairports_keeps_sources_and_referenced(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("fetch_ourairports", DATA_DIR.parent / "scripts" / "fetch_ourairports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    hdr = "ident,type,name,latitude_deg,longitude_deg,elevation_ft,elevation_source,municipality,iata_code\n"
    (tmp_path / "airports.csv").write_text(
        hdr + "EPDS,small_airport,Witków,50.8,16.1,1644,lotniska.dlapilota.pl,Witków,\n"
        "EPKB,small_airport,Kazimierz Biskupi,52.3,18.1,362,lotniska.dlapilota.pl,Kazimierz Biskupi,\n"
        "EPKT,large_airport,Katowice,50.47,19.08,1008,AIP AD 2.2,Katowice,KTW\n", "utf-8")
    (tmp_path / "frequencies.csv").write_text("airport_ident,service\nEPKB,RADIO\nEPKT,TWR\n", "utf-8")
    oa = [{"ident": "EPDS", "iso_country": "PL", "type": "small_airport", "name": "Witkow", "latitude_deg": "50.8",
           "longitude_deg": "16.1", "elevation_ft": "", "municipality": "Witków", "iata_code": ""},
          {"ident": "EPKT", "iso_country": "PL", "type": "large_airport", "name": "Katowice", "latitude_deg": "50.47",
           "longitude_deg": "19.08", "elevation_ft": "995", "municipality": "Katowice", "iata_code": "KTW"}]
    monkeypatch.setattr(mod, "SEED", tmp_path)
    monkeypatch.setattr(mod, "get", lambda name: [dict(a) for a in oa] if name == "airports.csv" else [])
    mod.main()
    rows = {r["ident"]: r for r in csv.DictReader(open(tmp_path / "airports.csv", encoding="utf-8"))}
    # elewacje ze źródłem zostają (także spoza AIP), EPKB (zniknęło z OurAirports, ma częstotliwość) zostaje
    assert (rows["EPDS"]["elevation_ft"], rows["EPKT"]["elevation_ft"]) == ("1644", "1008")
    assert rows["EPKB"]["elevation_source"] == "lotniska.dlapilota.pl"
    assert "UWAGA: EPKB" in capsys.readouterr().out
