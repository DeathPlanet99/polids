"""data/seed/sectors.geojson: generator z plików .ese, import przy starcie i zgodność z plikami .ese w repozytorium.

Marek: "data/seed/sectors.geojson tutaj trzeba będzie przerzucić sektory najlepiej i potem niech z tego bierze dane"."""
import json
import os

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.config import DATA_DIR, settings
from backend.app.database import Base
from backend.app.importers import sectors_geojson as sg
from backend.app.importers.ese import parse_ese
from backend.app.importers.neighbours import select_sectors
from backend.app.importers.sct import read_text
from backend.app.models import ImportLog, NbSector, Sector
from backend.app.routers import nav

GEOJSON = settings.seed_dir / sg.FILENAME
REFRESH = f"uruchom {sg.COMMAND}, żeby odświeżyć sectors.geojson"

# plik EPWW: dwa sektory ze wspólną linią 2; plik sąsiada (LKAA): dwa sektory ze wspólną, poszarpaną granicą S
EPWW_TEXT = """[POSITIONS]
EPWW_C_CTR:Warszawa Radar:133.475:CL:C:EPWW:CTR:::0000:0000
[AIRSPACE]
SECTORLINE:1
COORD:N052.00.00.000:E019.00.00.000
COORD:N052.00.00.000:E020.00.00.000
SECTORLINE:2
COORD:N052.00.00.000:E020.00.00.000
COORD:N053.00.00.000:E020.00.00.000
SECTORLINE:3
COORD:N053.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E019.00.00.000
SECTORLINE:4
COORD:N052.00.00.000:E020.00.00.000
COORD:N052.00.00.000:E021.00.00.000
COORD:N053.00.00.000:E020.00.00.000

SECTOR:EPWW·EPWWC·285·660:28500:66000
OWNER:CL
BORDER:1:2:3

SECTOR:EPWW·EPWWD·285·660:28500:66000
OWNER:CL
BORDER:4:2
"""


def _dms(v: float, pos: str, neg: str) -> str:
    ms = round(abs(v) * 3_600_000)
    return f"{pos if v >= 0 else neg}{ms // 3_600_000:03d}.{ms // 60_000 % 60:02d}.{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _coord(lat: float, lon: float) -> str:
    return f"COORD:{_dms(lat, 'N', 'S')}:{_dms(lon, 'E', 'W')}"


# granica S: 50 punktów z drganiami 0,0002° (poniżej progu uproszczenia) i jednym wybrzuszeniem 0,01° w połowie
_S = [(50 + i / 50, 15 + (0.01 if i == 25 else 0.0002 * (i % 2))) for i in range(51)]
NB_TEXT = ("[POSITIONS]\nLKAA_W_CTR:Praha Radar:127.825:W:W:LKAA:CTR:::0000:0000\n"
           "LKAA_E_CTR:Praha Radar:132.890:E:E:LKAA:CTR:::0000:0000\n[AIRSPACE]\nSECTORLINE:S\n"
           + "\n".join(_coord(lat, lon) for lat, lon in _S) + "\nSECTORLINE:W\n"
           + "\n".join(_coord(*p) for p in [(51, 15), (51, 14), (50, 14), (50, 15)]) + "\nSECTORLINE:E\n"
           + "\n".join(_coord(*p) for p in [(50, 15), (50, 16), (51, 16), (51, 15)])
           + "\n\nSECTOR:LKAA NW 000 245:00000:24500\nOWNER:W:E\nBORDER:S:W\n"
           + "\nSECTOR:LKAA NE 000 245:00000:24500\nOWNER:E:W\nBORDER:E:S\n")


@pytest.fixture
def import_dir(tmp_path):
    d = tmp_path / "import"
    (d / "neighbours").mkdir(parents=True)
    (d / "EPWW-Test.ese").write_text(EPWW_TEXT, encoding="utf-8")
    (d / "neighbours" / "SF_-_LKAA.ese").write_text(NB_TEXT, encoding="utf-8")
    (d / "neighbours" / "XXXX.ese").write_text(NB_TEXT, encoding="utf-8")  # nieznany pakiet: pomijany
    return d


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_committed_geojson_matches_ese_files():
    """sources w data/seed/sectors.geojson = pliki .ese z repozytorium (sha256): po podmianie pliku .ese trzeba
    wygenerować plik od nowa."""
    if not GEOJSON.exists():
        pytest.fail(f"brak pliku data/seed/{sg.FILENAME}: {REFRESH}")
    problems = sg.check(json.loads(GEOJSON.read_text("utf-8")))
    assert not problems, f"data/seed/{sg.FILENAME} nie pasuje do plików .ese ({'; '.join(problems)}): {REFRESH}"


def test_committed_geojson_format():
    """Jeden sektor na wiersz, _uwaga i sources na początku, współrzędne do 5 miejsc, rozsądny rozmiar."""
    text = GEOJSON.read_text("utf-8")
    fc = json.loads(text)
    assert fc["type"] == "FeatureCollection" and "sectors_geojson" in fc["_uwaga"]
    assert "EPWW-Sector.ese" in fc["sources"] and "neighbours/EDGG.ese" in fc["sources"]
    lines = [ln for ln in text.splitlines() if ln.startswith('{"type":"Feature"')]
    assert len(lines) == len(fc["features"]) > 1000
    assert os.path.getsize(GEOJSON) < 4_000_000
    pts = [p for f in fc["features"] for r in f["geometry"]["coordinates"] for p in r]
    assert all(round(x, 5) == x and round(y, 5) == y for x, y in pts)
    sources = {f["properties"]["source"] for f in fc["features"]}
    assert {"EPWW", "EDGG", "EDWW", "EDMM", "LKAA", "UKBV"} <= sources


def test_check_detects_changed_new_and_removed_files(import_dir):
    fc = sg.build(import_dir, seed_dir=import_dir)
    assert list(fc["sources"]) == ["EPWW-Test.ese", "neighbours/SF_-_LKAA.ese"] and sg.check(fc, import_dir) == []
    # te same dane z końcami wierszy CRLF (np. po zmianie normalizacji w gicie): bez zmian
    p = import_dir / "neighbours" / "SF_-_LKAA.ese"
    p.write_bytes(NB_TEXT.replace("\n", "\r\n").encode())
    assert sg.check(fc, import_dir) == []
    p.write_text(NB_TEXT.replace("LKAA NE", "LKAA NX"), encoding="utf-8")
    assert sg.check(fc, import_dir) == ["neighbours/SF_-_LKAA.ese: plik się zmienił (sha256)"]
    (import_dir / "neighbours" / "EYVL.ese").write_text(NB_TEXT, encoding="utf-8")
    p.unlink()
    assert sg.check(fc, import_dir) == ["neighbours/SF_-_LKAA.ese: pliku już nie ma",
                                        "neighbours/EYVL.ese: nowy plik, nie ma go w sources"]


def test_check_command_message(monkeypatch, tmp_path, capsys):
    """--check: kod 1 i polecenie do odświeżenia pliku, gdy sources się nie zgadzają."""
    fc = json.loads(GEOJSON.read_text("utf-8"))
    fc["sources"]["EPWW-Sector.ese"] = "0" * 64
    monkeypatch.setattr(settings, "seed_dir", tmp_path)
    (tmp_path / sg.FILENAME).write_text(sg.dumps(fc), encoding="utf-8")
    assert sg.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "EPWW-Sector.ese: plik się zmienił" in out and sg.COMMAND in out


def test_build_properties_and_shared_borders(import_dir):
    fc = sg.build(import_dir, seed_dir=import_dir)
    by = {f["properties"]["name"]: f for f in fc["features"]}
    assert list(by) == ["EPWWC", "EPWWD", "NW", "NE"]
    c = by["EPWWC"]["properties"]
    assert c == {"source": "EPWW", "fir": "EPWW", "name": "EPWWC", "lower_ft": 28500, "upper_ft": 66000, "owners": ["CL"]}
    nw = by["NW"]["properties"]
    assert nw == {"source": "LKAA", "fir": "LKAA", "name": "NW", "lower_ft": 0, "upper_ft": 24500,
                  "owners": ["LKAA_W_CTR", "LKAA_E_CTR"], "active": []}
    # wspólna granica sąsiadów uproszczona raz: oba sektory mają na niej te same wierzchołki (końce i wybrzuszenie
    # z punktami obok), drgania poniżej progu znikają
    rw, re_ = (by[n]["geometry"]["coordinates"][0] for n in ("NW", "NE"))
    common = set(map(tuple, rw)) & set(map(tuple, re_))
    assert common == {(15.0, 50.0), (15.0, 50.48), (15.01, 50.5), (15.0, 50.52), (15.0, 51.0)}
    assert len(rw) == len(re_) == 8
    # EPWW bez uproszczenia, wspólna linia 2 w obu sektorach
    assert [20.0, 53.0] in by["EPWWC"]["geometry"]["coordinates"][0]
    assert [20.0, 53.0] in by["EPWWD"]["geometry"]["coordinates"][0]


def test_dumps_one_feature_per_line(import_dir):
    fc = sg.build(import_dir, seed_dir=import_dir)
    text = sg.dumps(fc)
    assert json.loads(text) == fc
    assert sum(1 for ln in text.splitlines() if ln.startswith('{"type":"Feature"')) == 4


def test_import_geojson_with_hand_edit(db, tmp_path):
    """Import do tabel w kolejności z pliku; ręcznie poprawiony sektor (owners jako tekst, inny poziom) wchodzi,
    sektor z błędną geometrią jest pomijany."""
    fc = sg.build(tmp_path / "brak")  # bez plików .ese: pusty
    assert fc["features"] == [] and fc["sources"] == {}
    sq = {"type": "Polygon", "coordinates": [[[14, 52], [15, 52], [15, 53], [14, 53], [14, 52]]]}
    fc["features"] = [
        {"type": "Feature", "properties": {"source": "EPWW", "fir": "EPWW", "name": "EPWWB", "lower_ft": 9500,
                                           "upper_ft": 24500, "owners": "BL:CL"}, "geometry": sq},
        {"type": "Feature", "properties": {"source": "edww", "fir": "edww", "name": "EDWWFLG1", "lower_ft": 16500,
                                           "upper_ft": 28500, "owners": ["EDWW_FLG_CTR"], "active": ["EDDH:23"]},
         "geometry": sq},
        {"type": "Feature", "properties": {"source": "EPWW", "fir": "EPWW", "name": "ZLY", "lower_ft": 0,
                                           "upper_ft": 1000, "owners": []}, "geometry": {"type": "Point"}},
    ]
    path = tmp_path / "sectors.geojson"
    path.write_text(sg.dumps(fc), encoding="utf-8")
    assert sg.import_sectors_geojson(db, path) == {"sectors": 1, "nb_sectors": 1, "skipped": 1}
    s = db.scalars(select(Sector)).one()
    assert (s.fir, s.name, s.lower_ft, s.upper_ft, s.owners) == ("EPWW", "EPWWB", 9500, 24500, "BL:CL")
    n = db.scalars(select(NbSector)).one()
    assert (n.source, n.fir, n.owners, n.active) == ("EDWW", "EDWW", "EDWW_FLG_CTR", "EDDH:23")
    assert json.loads(n.geometry) == sq


def test_startup_reimports_changed_geojson(db, tmp_path, monkeypatch):
    """Przy starcie: import, gdy plik się zmienił (ImportLog wg daty modyfikacji); ręczna poprawka zostaje po
    ponownym starcie; bez pliku tabele sektorów są puste."""
    from backend.app.importers import seed
    monkeypatch.setattr(settings, "seed_dir", tmp_path)
    sq = {"type": "Polygon", "coordinates": [[[14, 52], [15, 52], [15, 53], [14, 52]]]}
    feat = lambda lo: {"type": "Feature", "geometry": sq, "properties": {  # noqa: E731
        "source": "EPWW", "fir": "EPWW", "name": "EPWWB", "lower_ft": lo, "upper_ft": 24500, "owners": ["BL"]}}
    path = tmp_path / sg.FILENAME
    path.write_text(sg.dumps({"type": "FeatureCollection", "features": [feat(9500)]}), encoding="utf-8")
    assert seed.import_sectors(db)[0]["sectors"] == 1
    assert seed.import_sectors(db) == []  # bez zmian: bez importu
    path.write_text(sg.dumps({"type": "FeatureCollection", "features": [feat(8500)]}), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert seed.import_sectors(db)[0]["sectors"] == 1
    assert db.scalars(select(Sector.lower_ft)).one() == 8500
    assert seed.import_sectors(db) == [] and db.scalars(select(Sector.lower_ft)).one() == 8500
    path.unlink()
    assert seed.import_sectors(db) == []
    assert db.scalar(select(func.count()).select_from(Sector)) == 0
    assert db.get(ImportLog, f"seed/{sg.FILENAME}") is None


def test_nav_sectors_without_data_is_empty(db):
    """Dawny zastępczy kształt z sectors.geojson zniknął: bez sektorów pusta lista z wyjaśnieniem."""
    r = nav.sectors(fir="EPWW", level_ft=None, db=db)
    assert r["features"] == [] and "sectors.geojson" in r["note"]
    assert "note" not in nav.sectors(fir="EPWW", level_ft=99000, db=db)


def test_generator_reproduces_old_import():
    """Generator na plikach z repozytorium daje te same sektory, co dawny import z .ese: EPWW identycznie
    (z geometrią), sąsiedzi z tymi samymi polami i kształtem w granicach uproszczenia (ok. 100 m)."""
    if not (DATA_DIR / "import" / "EPWW-Sector.ese").exists():
        pytest.skip("brak plików .ese")
    fc = sg.build()
    epww = [f for f in fc["features"] if f["properties"]["source"] == "EPWW"]
    old = parse_ese(read_text(DATA_DIR / "import" / "EPWW-Sector.ese"))["sectors"]
    assert [(f["properties"]["fir"], f["properties"]["name"], f["properties"]["lower_ft"], f["properties"]["upper_ft"],
             ":".join(f["properties"]["owners"]), f["geometry"]) for f in epww] == \
        [(s["fir"], s["name"], s["lower_ft"], s["upper_ft"], s["owners"], json.loads(s["geometry"])) for s in old]
    look = sg.airport_lookup()
    for name, (key, path) in sg.source_files().items():
        if key == sg.EPWW:
            continue
        mine = [f for f in fc["features"] if f["properties"]["source"] == key]
        ref = select_sectors(parse_ese(read_text(path), look), key)[0]
        assert [(f["properties"]["name"], f["properties"]["owners"], f["properties"]["active"]) for f in mine] == \
            [(s["name"][:60], s["owners"], s["active"]) for s in ref], name
        for f, s in zip(mine, ref):
            a, b = f["geometry"]["coordinates"][0], json.loads(s["geometry"])["coordinates"][0]
            # każdy wierzchołek nowej granicy leży na starej (i odwrotnie) z dokładnością do uproszczenia
            assert _hausdorff(a, b) < 0.0015, (name, s["name"])


def _hausdorff(a: list, b: list) -> float:
    def seg(p, q, r):
        dx, dy = r[0] - q[0], r[1] - q[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if not l2 else max(0.0, min(1.0, ((p[0] - q[0]) * dx + (p[1] - q[1]) * dy) / l2))
        return ((p[0] - q[0] - t * dx) ** 2 + (p[1] - q[1] - t * dy) ** 2) ** 0.5

    def one(x, y):
        return max(min(seg(p, q, r) for q, r in zip(y, y[1:])) for p in x)
    return max(one(a, b), one(b, a))
