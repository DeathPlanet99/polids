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
