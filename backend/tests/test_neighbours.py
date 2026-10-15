"""Sektory FIR-ów sąsiednich z ich własnych plików .ese (data/import/neighbours)."""
import json
import random
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.config import DATA_DIR
from backend.app.database import Base
from backend.app.importers.ese import parse_ese, split_sector_name
from backend.app.importers.neighbours import (NB_SOURCES, pack_positions, pick_runways, rwy_active, select_sectors,
                                              simplify_line, source_key)
from backend.app.importers.sct import read_text
from backend.app.models import AtcPosition, NbPosition, NbSector, Sector
from backend.app.routers.nav import slices
from backend.app.services.neighbours import nb_positions
from backend.app.services.vatsim import controller_info, match_positions

NB_DIR = DATA_DIR / "import" / "neighbours"

# mały plik sąsiada: dwa FIR-y (własny i cudzy), sektor EPWW (nieaktualna kopia), wariant pasa, techniczny,
# okrąg z ICAO i współrzędnych, blok MSAW z własnymi COORD; ID "C" znaczy tu coś innego niż w pliku EPWW
NB_TEXT = """[POSITIONS]
LKAA_N_CTR:Praha Radar:127.825:C:N:LKAA:CTR:::1460:1467
LKAA_CTR:Praha Radar:132.890:L::LKAA:CTR:::1460:1467
LKPR_APP:Praha Approach:120.530:PRA::LKPR:APP:::1460:1467
EPWW_C_CTR:Warszawa Radar:133.475:WC:C:EPWW:CTR:::0000:0000
LKPR_TWR:Praha Tower:118.105:PRT::LKPR:TWR:::1460:1467
LZIB_TWR:Bratislava Tower:118.300:IBT::LZIB:TWR:::0000:0000
EUC-ME_FSS:Eurocontrol Central:135.125:ECM::EUC-ME:FSS:::0000:0000
[AIRSPACE]
SECTORLINE:1
COORD:N050.00.00.000:E015.00.00.000
COORD:N050.00.00.000:E016.00.00.000
SECTORLINE:2
DISPLAY:LKAA NL 125 305:LKAA NL 125 305:LKAA NU 305 660
COORD:N050.00.00.000:E016.00.00.000
COORD:N051.00.00.000:E016.00.00.000
COORD:N051.00.00.000:E015.00.00.000
COORD:N050.00.00.000:E015.00.00.000
CIRCLE_SECTORLINE:LKAA LKBE_ATZ 000 040:LKBE:3
CIRCLE_SECTORLINE:RING:N050.30.00.000:E015.30.00.000:5

SECTOR:LKAA NL 125 305:12500:30500
OWNER:C:L
BORDER:1:2
MSAW:LKAA NL 125 305:5000
COORD:N060.00.00.000:E030.00.00.000
COORD:N061.00.00.000:E031.00.00.000

SECTOR:LKAA LKPR24_TMA 000 125:00000:12500
OWNER:PRA:C
ACTIVE:LKPR:24
BORDER:1:2

SECTOR:LKAA LKPR06_TMA 000 125:00000:12500
OWNER:PRA:C
ACTIVE:LKPR:06
BORDER:1:2

SECTOR:LKAA LKPR30_TMA 000 125:00000:12500
OWNER:PRA:C
ACTIVE:LKPR:30
BORDER:1:2

SECTOR:LKAA LKKB_CTR_24-30 000 020:00000:02000
OWNER:PRA
ACTIVE:LKPR:24
ACTIVE:LKPR:30
BORDER:1:2

SECTOR:LKAA PHA_CTA_DISPLAY 000 095:00000:09500
ALTOWNER:Praha:PRA
BORDER:1:2

SECTOR:LKAA TECH LOW 000 095:00000:09500
OWNER:ECM
BORDER:1:2

SECTOR:LKAA LKBE_ATZ 000 040:00000:04000
OWNER:PRA
BORDER:LKAA LKBE_ATZ 000 040

SECTOR:LKAA RING 000 050:00000:05000
OWNER:L
BORDER:RING

SECTOR:EPWW EPWWT 000 660:00000:66000
OWNER:WC
BORDER:1:2

SECTOR:LZBB WEST 080 305:08000:30500
OWNER:C
BORDER:1:2
"""


def test_split_sector_name():
    assert split_sector_name("EPWW·EPWWB·095·245") == ("EPWW", "EPWWB")
    assert split_sector_name("ESAA·ESMM 8-1·195·365") == ("ESAA", "ESMM 8-1")
    assert split_sector_name("LKAA LKKB_CTR_06-12 000 020") == ("LKAA", "LKKB_CTR_06-12")
    assert split_sector_name("LKAA NL 125 305") == ("LKAA", "NL")
    assert split_sector_name("XYZ") == ("XYZ", "XYZ")


def test_parse_circles_active_and_msaw():
    data = parse_ese(NB_TEXT, airport=lambda icao: (49.94, 14.03) if icao == "LKBE" else None)
    by = {(s["fir"], s["name"]): s for s in data["sectors"]}
    nl = json.loads(by[("LKAA", "NL")]["geometry"])["coordinates"][0]
    # COORD z bloku MSAW (60°N) nie należą do granicy sektora
    assert max(p[1] for p in nl) <= 51 and len(nl) == 5
    assert by[("LKAA", "LKPR24_TMA")]["active"] == ["LKPR:24"] and by[("LKAA", "NL")]["active"] == []
    atz = json.loads(by[("LKAA", "LKBE_ATZ")]["geometry"])["coordinates"][0]
    assert len(atz) > 60 and abs(sum(p[1] for p in atz[:-1]) / (len(atz) - 1) - 49.94) < 0.01
    ring = json.loads(by[("LKAA", "RING")]["geometry"])["coordinates"][0]
    assert abs(max(p[1] for p in ring) - (50.5 + 5 / 60)) < 0.001
    # okręgu z ICAO bez współrzędnych lotniska nie da się narysować: sektor pominięty
    assert ("LKAA", "LKBE_ATZ") not in {(s["fir"], s["name"]) for s in parse_ese(NB_TEXT)["sectors"]}


def test_select_sectors_takes_only_home_fir_and_resolves_owners_per_file():
    sectors, positions = select_sectors(parse_ese(NB_TEXT, airport=lambda icao: (49.94, 14.03)), "LKAA")
    names = {s["name"] for s in sectors}
    # bez kopii EPWW, bez cudzego FIR-u (LZBB), bez wycinka technicznego, bez sektora bez OWNER (tylko ALTOWNER)
    # i tylko jeden układ pasów LKPR (24; 06 i 30 to układy alternatywne), sektor wspólny 24-30 zostaje
    assert names == {"NL", "LKPR24_TMA", "LKKB_CTR_24-30", "LKBE_ATZ", "RING"}
    nl = next(s for s in sectors if s["name"] == "NL")
    # ID "C" i "L" z tego pliku → znaki z tego pliku (w pliku EPWW "CL" to EPWW_C_CTR)
    assert nl["owners"] == ["LKAA_N_CTR", "LKAA_CTR"]
    assert {p["callsign"] for p in positions} == {"LKAA_N_CTR", "LKAA_CTR", "LKPR_APP"}  # bez EP**


def test_pack_positions_airports_and_owners():
    """Do tabeli nb_positions: stanowiska lotnisk i FIR-u pakietu (LKPR_TWR bez żadnego sektora – karta lotniska LKPR)
    i stanowiska z list OWNER sektorów FIR-u pakietu (EUC-ME_FSS z wycinka technicznego); bez EP** i bez cudzych
    lotnisk (LZIB_TWR, kopia z pakietu słowackiego)."""
    data = parse_ese(NB_TEXT, airport=lambda icao: (49.94, 14.03))
    got = {p["callsign"]: p for p in pack_positions(data, "LKAA")}
    assert set(got) == {"LKAA_N_CTR", "LKAA_CTR", "LKPR_APP", "LKPR_TWR", "EUC-ME_FSS"}
    assert got["LKPR_TWR"]["frequency"] == "118.105" and got["LKPR_TWR"]["prefix"] == "LKPR"
    assert all(src.get("airports") for src in NB_SOURCES.values())


def test_select_sectors_outside_region_and_above_fl660():
    text = NB_TEXT.replace("N050.00.00.000:E015", "N040.00.00.000:E005").replace("N051.00.00.000:E015", "N041.00.00.000:E005")
    text = text.replace("N050.00.00.000:E016", "N040.00.00.000:E006").replace("N051.00.00.000:E016", "N041.00.00.000:E006")
    assert not [s for s in select_sectors(parse_ese(text), "LKAA")[0] if s["name"] == "NL"]
    high = NB_TEXT.replace("SECTOR:LKAA NL 125 305:12500:30500", "SECTOR:LKAA NL 660 999:66000:99900")
    assert "NL" not in {s["name"] for s in select_sectors(parse_ese(high), "LKAA")[0]}


def test_pick_runways_one_direction_per_airport():
    sec = lambda *a: {"active": list(a)}  # noqa: E731
    chosen = pick_runways([sec("LKPR:06"), sec("LKPR:12"), sec("LKPR:24", "LKPR:30"), sec("EDDH:05", "EDDH:15"),
                           sec("EDDH:23"), sec("EDDH:33"), sec("EDDB:24L", "EDDB:24R"), sec("EDDB:06L"), sec("EDDN:10"),
                           sec("EDDN:28"), sec("ESSA:01L"), sec("ESSA:19R")])
    assert chosen == {"LKPR": 24, "EDDH": 23, "EDDB": 24, "EDDN": 28, "ESSA": 19}
    assert rwy_active([], chosen) and rwy_active(["EDDB:24R"], chosen) and rwy_active(["EDDB:24L"], chosen)
    assert not rwy_active(["LKPR:30"], chosen) and rwy_active(["LKPR:30", "LKPR:24"], chosen)
    assert rwy_active(["XXXX:H1"], chosen)  # pas bez numeru: zawsze


def test_source_key():
    from pathlib import Path
    assert source_key(Path("EDMM-AeroNav_20261001160608-261001-0001.ese")) == "EDMM"
    assert source_key(Path("SF_-_LKAA.ESE")) == "LKAA" and source_key(Path("UKBV.ese")) == "UKBV"
    assert source_key(Path("EPWW-Sector.ese")) is None


def test_simplify_line_keeps_ends_and_closed_circles():
    line = [(52 + (0.00001 if i % 2 else 0), 20 + i / 100) for i in range(101)] + [(53, 21), (53, 20)]
    out = simplify_line(line)
    assert out[0] == line[0] and out[-1] == line[-1] and 3 <= len(out) < 8 and (53, 21) in out
    from backend.app.importers.ese import _circle
    ring = simplify_line(_circle(52.0, 20.0, 5))
    assert ring[0] == ring[-1] and 8 <= len(ring) < 40


def test_match_positions_index_same_as_full_scan():
    """Indeks (prefiks, typ) daje to samo, co przeglądanie całej listy stanowisk po kolei (wersja do rundy 9)."""
    def old(controllers, positions):
        by_cs = {p.callsign: p for p in positions}
        online = {}
        for c in controllers:
            cs, freq = c.get("callsign", ""), c.get("frequency", "")
            if cs in by_cs:
                online[cs] = controller_info(c)
                continue
            for p in positions:
                if (p.prefix and cs.startswith(p.prefix + "_") and cs.split("_")[-1] == p.callsign.split("_")[-1]
                        and freq[:7] == p.frequency[:7]):
                    online.setdefault(p.callsign, controller_info(c))
                    break
        return online

    rnd = random.Random(7)
    pre = ["EPWA", "EPKK", "EDWW", "EDDB", "RU-NWC", "EPWW"]
    mid = ["", "N", "S", "FLG", "I", "X"]
    typ = ["APP", "TWR", "CTR", "FSS", "GND"]
    freqs = ["128.805", "120.030", "133.475", "125.055"]
    mk = lambda: "_".join(x for x in (rnd.choice(pre), rnd.choice(mid), rnd.choice(typ)) if x)  # noqa: E731
    positions = [SimpleNamespace(callsign=cs, prefix=cs.split("_")[0], frequency=rnd.choice(freqs))
                 for cs in dict.fromkeys(mk() for _ in range(60))]
    ctrls = [{"callsign": mk(), "frequency": rnd.choice(freqs), "cid": i} for i in range(300)]
    assert match_positions(ctrls, positions) == old(ctrls, positions)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


SQUARE = json.dumps({"type": "Polygon", "coordinates": [[[14, 52], [15, 52], [15, 53], [14, 53], [14, 52]]]})


def test_slices_replace_epww_copies_of_covered_firs(db):
    db.execute(insert(AtcPosition), [{"callsign": "EDWW_FLG_CTR", "name": "Bremen", "frequency": "136.450",
                                      "position_id": "EDWF", "prefix": "EDWW"},
                                     {"callsign": "UMMV_CTR", "name": "Minsk Control", "frequency": "128.350",
                                      "position_id": "UMV", "prefix": "UMMV"}])
    db.execute(insert(Sector), [{"fir": f, "name": n, "lower_ft": 16500, "upper_ft": 28500, "owners": o, "geometry": SQUARE}
                                for f, n, o in [("EDWW", "EDWWFLG1", "EDWF"), ("UMMV", "UMMV_W", "UMV")]])
    db.execute(insert(NbSector), [{"source": "EDWW", "fir": "EDWW", "name": "EDWWFLG1", "lower_ft": 16500, "upper_ft": 28500,
                                   "owners": "EDWW_FLG_CTR:EDWW_BOR_CTR", "active": "", "geometry": SQUARE}])
    db.execute(insert(NbPosition), [{"source": "EDWW", "callsign": "EDWW_BOR_CTR", "name": "Bremen", "frequency": "123.225",
                                     "prefix": "EDWW"}])
    db.commit()
    feats = [f["properties"] for f in slices(level_ft=20000, db=db)["features"]]
    edww = [p for p in feats if p["fir"] == "EDWW"]
    # kopia z pliku EPWW zastąpiona sektorem z pliku sąsiada; UMMV (bez własnego pliku) zostaje z pliku EPWW
    assert [p["source"] for p in edww] == ["EDWW"] and edww[0]["owner_callsigns"] == ["EDWW_FLG_CTR", "EDWW_BOR_CTR"]
    assert edww[0]["id"] == f"nb{1}" and edww[0]["kind"] == "nb" and edww[0]["group_label"] == "EDWW Bremen"
    assert [p["source"] for p in feats if p["fir"] == "UMMV"] == ["EPWW"]


def test_nb_positions_only_sector_owners_unless_airports(db):
    """RADIO, MAP i dopasowanie zalogowanych: tylko stanowiska z list OWNER sektorów sąsiada z tego samego pliku
    (jak przed zapisywaniem stanowisk lotnisk); karta lotniska (airports=True): wszystkie, po jednym na znak."""
    db.execute(insert(NbSector), [{"source": "EDWW", "fir": "EDWW", "name": "EDWWFLG1", "lower_ft": 16500,
                                   "upper_ft": 28500, "owners": "EDWW_FLG_CTR", "active": "", "geometry": SQUARE},
                                  {"source": "EDGG", "fir": "EDGG", "name": "HEF1", "lower_ft": 0, "upper_ft": 24500,
                                   "owners": "EDGG_HEF_CTR:EDDF_APP", "active": "", "geometry": SQUARE}])
    db.execute(insert(NbPosition), [{"source": src, "callsign": cs, "name": cs, "frequency": f, "prefix": cs[:4]}
                                    for src, cs, f in [("EDWW", "EDWW_FLG_CTR", "136.450"), ("EDWW", "EDDH_TWR", "118.505"),
                                                       ("EDWW", "EDDF_APP", "120.800"), ("EDGG", "EDGG_HEF_CTR", "123.250"),
                                                       ("EDGG", "EDDF_APP", "120.805"), ("EDGG", "EDDF_TWR", "119.900")]])
    db.commit()
    by = {p["callsign"]: p for p in nb_positions(db)}
    # EDDF_APP z pliku EDGG (tam jest w kolejności przejmowania), choć pakiet EDWW jest wcześniej w NB_SOURCES
    assert set(by) == {"EDWW_FLG_CTR", "EDGG_HEF_CTR", "EDDF_APP"} and by["EDDF_APP"]["source"] == "EDGG"
    assert by["EDDF_APP"]["frequency"] == "120.805" and by["EDGG_HEF_CTR"]["fir"] == "EDGG"
    allp = {p["callsign"]: p for p in nb_positions(db, airports=True)}
    assert set(allp) == {"EDWW_FLG_CTR", "EDDH_TWR", "EDDF_APP", "EDGG_HEF_CTR", "EDDF_TWR"}
    assert allp["EDDF_APP"]["source"] == "EDWW"  # bez filtra: pierwszy plik w kolejności NB_SOURCES


@pytest.mark.skipif(not NB_DIR.is_dir(), reason="brak plików sąsiadów")
def test_real_neighbour_files():
    """Pliki sąsiadów z repozytorium: każdy pakiet daje sektory swojego FIR-u w rejonie Polski, z właścicielami
    rozwiązanymi w obrębie pliku, bez sektorów EPWW (pakiet ukraiński ma nieaktualne polskie sektory)."""
    files = {source_key(p): p for p in NB_DIR.glob("*.ese")}
    assert set(files) == set(NB_SOURCES)
    known = {"EDWW": "EDWW_FLG_CTR", "EDMM": "EDMM_MEI_CTR", "EDGG": "EDGG_HEF_CTR", "ESAA": "ESMM_7_CTR", "EYVL": "EYVL_CTR",
             "EKDK": "EKDK_C_CTR", "LKAA": "LKAA_N_CTR", "LZBB": "LZBB_CTR", "UKBV": "UKLV_CTR", "ULLL": "UMKK_CTR"}
    for key, path in files.items():
        sectors, positions = select_sectors(parse_ese(read_text(path)), key)
        assert sectors, key
        assert {s["fir"].upper() for s in sectors} == set(NB_SOURCES[key]["firs"]), key
        assert any(known[key] in s["owners"] for s in sectors), key
        assert not any(cs.startswith("EP") for s in sectors for cs in s["owners"][:1]), key
        assert not any(p["callsign"].startswith("EP") for p in positions), key
        assert all(s["owners"] for s in sectors), key
    lk = {s["name"] for s in select_sectors(parse_ese(read_text(files["LKAA"])), "LKAA")[0]}
    assert any(n.startswith("LKPR24_") for n in lk) and not any(n.startswith(("LKPR30_", "LKPR06_", "LKPR12_")) for n in lk)


def test_neighbour_file_removed_and_restored(db, tmp_path, monkeypatch):
    """Stanowiska usuniętego pliku sąsiada znikają z bazy, a przywrócony (z tą samą datą modyfikacji) wczytuje się
    ponownie (sektory sąsiadów są w sectors.geojson)."""
    import shutil

    from sqlalchemy import func, select

    from backend.app.importers import seed
    monkeypatch.setattr(seed, "NB_DIR", tmp_path)
    f = tmp_path / "SF_-_LKAA.ESE"
    f.write_text(NB_TEXT, encoding="utf-8")
    count = lambda: db.scalar(select(func.count()).select_from(NbPosition))  # noqa: E731
    seed.import_neighbours(db)
    n, st = count(), f.stat().st_mtime
    assert n > 0
    shutil.move(f, tmp_path.parent / f.name)
    seed.import_neighbours(db)
    assert count() == 0
    shutil.move(tmp_path.parent / f.name, f)
    assert f.stat().st_mtime == st  # ten sam plik, ta sama data modyfikacji
    seed.import_neighbours(db)
    assert count() == n


def test_match_positions_neighbour_twin():
    """Znak z vacs-data (EKDK_CTR) bez sektorów w pliku Danii: kontroler wpisany też pod stanowiskiem z kolejności
    przejmowania Danii o tym samym prefiksie, typie i częstotliwości (EKDK_UN_CTR), jak rozpozna go EuroScope."""
    from backend.app.services.radio import Station
    pos = [Station("EKDK_A_CTR", "EKDK", "128.100"), Station("EKDK_CTR", "EKDK", "136.555"),
           Station("EKCH_F_APP", "EKCH", "120.205"), Station("EKDK_UN_CTR", "EKDK", "136.555"),
           Station("EKCH_P_APP", "EKCH", "120.205")]
    prefer = {"EKDK_A_CTR", "EKDK_UN_CTR", "EKCH_P_APP"}
    on = lambda cs, f: match_positions([{"callsign": cs, "frequency": f}], pos, prefer)  # noqa: E731
    r = on("EKDK_CTR", "136.555")
    assert set(r) == {"EKDK_CTR", "EKDK_UN_CTR"} and r["EKDK_UN_CTR"]["alias_of"] == "EKDK_CTR"
    assert "alias_of" not in r["EKDK_CTR"] and r["EKDK_UN_CTR"]["callsign"] == "EKDK_CTR"
    assert set(on("EKDK_U_CTR", "136.555")) == {"EKDK_CTR", "EKDK_UN_CTR"}  # zastępczy znak: też oba
    assert on("EKCH_F_APP", "120.205")["EKCH_P_APP"]["alias_of"] == "EKCH_F_APP"
    assert set(on("EKDK_UN_CTR", "136.555")) == {"EKDK_UN_CTR"}  # już stanowisko sąsiada: bez kopii
    assert set(on("EKDK_A_CTR", "128.100")) == {"EKDK_A_CTR"}
    assert set(on("EKDK_CTR", "121.500")) == {"EKDK_CTR"}  # inna częstotliwość: brak bliźniaka
    assert set(match_positions([{"callsign": "EKDK_CTR", "frequency": "136.555"}], pos)) == {"EKDK_CTR"}
    # wpis pod znakiem sąsiada nie zajmuje stanowiska, na którym ktoś naprawdę siedzi (też gdy przyszedł później)
    r = match_positions([{"callsign": "EKDK_CTR", "frequency": "136.555", "cid": 1},
                         {"callsign": "EKDK_UN_CTR", "frequency": "136.555", "cid": 2}], pos, prefer)
    assert r["EKDK_UN_CTR"]["cid"] == 2 and "alias_of" not in r["EKDK_UN_CTR"]
    # EKDK_CTR zajęty przez kogoś innego (dokładny znak): drugi kontroler jest pod EKDK_UN_CTR jako osobna osoba
    for feed in ([{"callsign": "EKDK_1_CTR", "frequency": "136.555", "cid": 1}, {"callsign": "EKDK_CTR", "frequency": "118.000", "cid": 2}],
                 [{"callsign": "EKDK_CTR", "frequency": "118.000", "cid": 2}, {"callsign": "EKDK_1_CTR", "frequency": "136.555", "cid": 1}]):
        r = match_positions(feed, pos, prefer)
        assert r["EKDK_CTR"]["cid"] == 2 and r["EKDK_UN_CTR"]["cid"] == 1 and "alias_of" not in r["EKDK_UN_CTR"]


def test_merge_positions_frequency_from_neighbour_file():
    """Stanowisko z kolejności przejmowania sąsiada ma częstotliwość z jego pliku .ese, nie z vacs-data."""
    from backend.app.services.radio import merge_positions
    vacs = [{"id": "EKCH_P_APP", "frequency": "131.405", "fir_dir": "EK", "facility_type": "APP", "prefixes": ["EKCH"]},
            {"id": "EKCH_W_APP", "frequency": "119.805", "fir_dir": "EK", "facility_type": "APP", "prefixes": ["EKCH"]}]
    nb = [{"callsign": "EKCH_P_APP", "name": "Kastrup Final", "frequency": "120.205", "prefix": "EKCH", "source": "EKDK",
           "fir": "EK"}]
    by = {p["callsign"]: p for p in merge_positions([], vacs, nb, {"EKCH_P_APP"})}
    assert by["EKCH_P_APP"]["frequency"] == "120.205" and by["EKCH_W_APP"]["frequency"] == "119.805"
    assert {p["callsign"]: p["frequency"] for p in merge_positions([], vacs, nb)}["EKCH_P_APP"] == "131.405"
