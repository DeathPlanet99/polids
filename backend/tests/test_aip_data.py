# Dane z AIP: pasy preferowane (AD 2.21) z oknami czasowymi i limitami wiatru, progi LVP (AD 2.22, AIP MIL AD 4.20)
# z histerezą i stanem "LVP odwołane"
import json
from datetime import datetime, timezone

from backend.app.config import settings
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


def _pick(icao, rwys, metar, now, equipment=None):
    m = parse_metar(f"{icao} 141200Z {metar} 10/05 Q1012")
    return select_runways(rwys, m.wind_dir, m.wind_speed, equipment=equipment, config=runway_config(icao),
                          gust=m.wind_gust, metar=m, now=now)


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


def test_config_now():
    assert config_now(runway_config("EPWR"), NIGHT) == {"arr": "11", "dep": "29"}
    assert config_now(runway_config("EPWR"), DAY) == {"arr": "29", "dep": "29"}
    assert config_now(runway_config("EPGD"), DAY) == {"arr": None, "dep": "29"}
    # EPWA: konfiguracja z poprzedniej wersji, oznaczona jako niezweryfikowana z AIP
    assert config_now(runway_config("EPWA"), DAY) == {"arr": "33", "dep": "29"}
    assert runway_config("EPWA")["verified"] is False
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
    assert cfg["EPWA"]["verified"] is False and "niezweryfikowane" in cfg["EPWA"]["source"]
    for icao in ("EPRA", "EPZG", "EPDE", "EPLY"):
        assert cfg[icao]["applies"] is False and cfg[icao]["reason"]
    assert lvp.rules(lvp.thresholds("EPKT")) == [
        "Przygotowanie: RVR ≤ 800 m lub podstawa ≤ 300 ft", "LVP: RVR < 550 m lub podstawa < 200 ft",
        "Odwołanie: RVR ≥ 600 m i podstawa ≥ 200 ft"]
    assert lvp.rules(lvp.thresholds("EPLL"))[0] == "Przygotowanie: AIP nie podaje"


def _ev(icao, metar):
    return lvp.evaluate(parse_metar(f"{icao} {metar}"), icao, now=datetime(2026, 10, 14, 23, 0, tzinfo=UTC))


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
