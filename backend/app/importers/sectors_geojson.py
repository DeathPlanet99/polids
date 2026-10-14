"""data/seed/sectors.geojson: sektory EPWW i FIR-ów sąsiednich, z których aplikacja buduje tabele sectors
i nb_sectors (MAP, RADIO, przeglądy lotnisk).

Plik powstaje z plików .ese (sekcja [AIRSPACE]): EPWW (data/import/*.ese) i sąsiadów (data/import/neighbours/*.ese,
wybór sektorów jak w neighbours.select_sectors). Aplikacja przy starcie czyta już tylko ten plik (ponownie, gdy się
zmieni), więc ręczne poprawki w nim działają do następnego uruchomienia generatora. Stanowiska, SID/STAR i resztę
dalej bierzemy z plików .ese.

    python -m backend.app.importers.sectors_geojson          # wygeneruj plik od nowa z plików .ese
    python -m backend.app.importers.sectors_geojson --check  # czy plik pasuje do plików .ese i ustawień generatora

Format: jeden sektor (Feature) na wiersz, żeby zmiany w gicie były czytelne; "sources" = pliki .ese z sha256 treści
(po zamianie CRLF na LF, bo git może zmieniać końce wierszy), "generator" = wersja i ustawienia generatora (FIR-y
pakietów, rejon, wiatr do wyboru pasów, uproszczenie) oraz środki okręgów wokół lotnisk, "_uwaga" = opis pliku.
--check (też w Dockerfile i w teście) porównuje oba nagłówki z bieżącymi plikami i kodem."""

import csv
import hashlib
import json
import logging
import math
import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..models import NbSector, Sector
from . import neighbours
from .ese import parse_ese, sector_ring
from .neighbours import select_sectors, simplify_line, source_key
from .sct import read_text

log = logging.getLogger("polids.sectors")
IMPORT_DIR = DATA_DIR / "import"
FILENAME = "sectors.geojson"
COMMAND = "python -m backend.app.importers.sectors_geojson"
EPWW = "EPWW"  # source sektorów z pliku .ese EPWW (także kopii sektorów sąsiadów z tego pliku, np. UMMV)
NO_SECTORS = f"Brak sektorów: wygeneruj data/seed/{FILENAME} ({COMMAND})."
# Podnieś przy zmianie sposobu generowania, której nie widać w generator_settings (wybór sektorów w select_sectors,
# składanie i upraszczanie granic, zaokrąglenie): --check, test i build obrazu przypomną o ponownym wygenerowaniu pliku.
GENERATOR_VERSION = 1

NOTE = ("Sektory EPWW i FIR-ów sąsiednich: z tego pliku aplikacja przy starcie buduje tabele sectors i nb_sectors "
        "(MAP, RADIO). Generuje go " + COMMAND + " z plików .ese z data/import (EPWW) i data/import/neighbours "
        "(sąsiedzi); sources = pliki .ese i sha256 ich treści po zamianie CRLF na LF, generator = wersja i ustawienia "
        "generatora oraz środki okręgów wokół lotnisk (test i --check przypomną o ponownym wygenerowaniu po podmianie "
        "pliku .ese, zmianie ustawień albo współrzędnych lotniska). Ręczne poprawki są dozwolone: aplikacja wczytuje "
        "plik ponownie, gdy się zmieni, a nadpisze je dopiero generator. Jeden sektor na wiersz. Właściwości: source (EPWW = plik EPWW, "
        "inaczej klucz pakietu sąsiada z NB_SOURCES w backend/app/importers/neighbours.py), fir, name, lower_ft, "
        "upper_ft, owners (kolejność przejmowania: w sektorach z pliku EPWW identyfikatory stanowisk z jego sekcji "
        "[POSITIONS], u sąsiadów znaki stanowisk), active (tylko sąsiedzi: warunki ACTIVE ICAO:pas). Geometria: "
        "Polygon, lon/lat do 5 miejsc po przecinku. Granice sąsiadów są uproszczone o ok. 100 m, każda linia "
        "SECTORLINE raz, więc sąsiednie sektory mają na wspólnej granicy te same wierzchołki; okręgi wokół lotnisk "
        "(CIRCLE_SECTORLINE z kodem ICAO) mają środek z data/seed/airports.csv albo data/import/icao.txt.")


def file_sha256(path: Path) -> str:
    """sha256 treści pliku z końcami wierszy LF (ten sam wynik dla kopii z CRLF i po normalizacji w gicie)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_files(import_dir: Path = IMPORT_DIR) -> dict[str, tuple[str, Path]]:
    """Pliki .ese, z których powstaje sectors.geojson: {nazwa względem import_dir: (source, ścieżka)}, w kolejności
    zapisu. EPWW: plik .ese z data/import (przy kilku – ostatni wg nazwy, jak przy imporcie stanowisk), potem
    sąsiedzi wg nazwy pliku (przy dwóch plikach jednego pakietu – ostatni). Pliki o nieznanym kluczu pomijamy."""
    out: dict[str, tuple[str, Path]] = {}
    top = sorted(p for p in import_dir.glob("*") if p.is_file() and p.suffix.lower() == ".ese")
    if top:
        out[top[-1].name] = (EPWW, top[-1])
    nb_dir = import_dir / "neighbours"
    by_key: dict[str, Path] = {}
    for p in sorted(nb_dir.iterdir()) if nb_dir.is_dir() else []:
        if p.is_file() and p.suffix.lower() == ".ese" and (key := source_key(p)):
            by_key[key] = p
    for key, p in sorted(by_key.items(), key=lambda kv: kv[1].name):
        out[p.relative_to(import_dir).as_posix()] = (key, p)
    return out


def airport_lookup(import_dir: Path = IMPORT_DIR, seed_dir: Path | None = None) -> Callable[[str], tuple | None]:
    """Współrzędne lotniska do CIRCLE_SECTORLINE:nazwa:ICAO:promień, jak w bazie aplikacji: najpierw
    data/seed/airports.csv (tabela aerodromes), potem pierwszy wpis z navdata data/import/icao.txt."""
    coords: dict[str, tuple[float, float]] | None = None

    def load() -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        nav = import_dir / "icao.txt"
        if nav.exists():
            for line in read_text(nav).splitlines():
                p = line.split("\t")
                if not line or line.startswith(";") or len(p) < 3:
                    continue
                try:
                    out.setdefault(p[0].strip(), (float(p[1]), float(p[2])))
                except ValueError:
                    continue
        csv_path = (seed_dir or settings.seed_dir) / "airports.csv"
        if csv_path.exists():
            with open(csv_path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if not next(iter(r.values()), "").startswith("#"):
                        out[r["ident"]] = (float(r["latitude_deg"]), float(r["longitude_deg"]))
        return out

    def look(icao: str) -> tuple[float, float] | None:
        nonlocal coords
        if coords is None:
            coords = load()
        return coords.get(icao)
    return look


def _feature(props: dict, ring: list) -> dict:
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def epww_features(path: Path) -> list[dict]:
    """Wszystkie sektory pliku EPWW (też kopie sektorów sąsiadów, np. Białorusi) z pełną geometrią, jak w pliku
    (okręgi CIRCLE_SECTORLINE z kodem ICAO pomijamy, tak jak dawny import); owners = ID stanowisk z [POSITIONS]."""
    out = []
    for s in parse_ese(read_text(path))["sectors"]:
        props = {"source": EPWW, "fir": s["fir"], "name": s["name"], "lower_ft": s["lower_ft"],
                 "upper_ft": s["upper_ft"], "owners": [o for o in s["owners"].split(":") if o]}
        out.append(_feature(props, json.loads(s["geometry"])["coordinates"][0]))
    return out


def _round_ring(ring: list[tuple[float, float]]) -> list[list[float]]:
    """(lat, lon) → [lon, lat] do 5 miejsc, bez powtórzonych kolejnych punktów, zamknięty."""
    out: list[list[float]] = []
    for lat, lon in ring:
        p = [round(lon, 5), round(lat, 5)]
        if not out or p != out[-1]:
            out.append(p)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def neighbour_features(path: Path, key: str, airport: Callable[[str], tuple | None] | None = None) -> list[dict]:
    """Sektory pakietu sąsiada `key` (neighbours.select_sectors) z granicami uproszczonymi linia po linii."""
    data = parse_ese(read_text(path), airport)
    simple = {name: simplify_line(pts) for name, pts in data["sectorlines"].items()}
    out = []
    for s in select_sectors(data, key)[0]:
        ring = _round_ring(sector_ring(simple, s["border"]))
        if len(ring) < 4:  # uproszczenie zwinęło mały sektor: pełna geometria
            ring = json.loads(s["geometry"])["coordinates"][0]
        props = {"source": key, "fir": s["fir"].upper(), "name": s["name"][:60], "lower_ft": s["lower_ft"],
                 "upper_ft": s["upper_ft"], "owners": s["owners"], "active": s["active"]}
        out.append(_feature(props, ring))
    return out


def generator_settings() -> dict:
    """Wersja i ustawienia generatora, od których zależą sektory sąsiadów (backend/app/importers/neighbours.py)."""
    return {"version": GENERATOR_VERSION, "firs": {k: v["firs"] for k, v in neighbours.NB_SOURCES.items()},
            "region": list(neighbours.REGION), "wind_from": neighbours.WIND_FROM,
            "simplify_deg": neighbours.SIMPLIFY_DEG}


def build(import_dir: Path = IMPORT_DIR, seed_dir: Path | None = None) -> dict:
    """Cała zawartość sectors.geojson z plików .ese."""
    files = source_files(import_dir)
    look = airport_lookup(import_dir, seed_dir)
    centres: dict[str, list[float] | None] = {}  # lotniska okręgów CIRCLE_SECTORLINE i ich współrzędne

    def airport(icao: str) -> tuple | None:
        c = look(icao)
        centres[icao] = list(c) if c else None
        return c
    feats: list[dict] = []
    for _name, (key, path) in files.items():
        feats += epww_features(path) if key == EPWW else neighbour_features(path, key, airport)
    return {"type": "FeatureCollection", "_uwaga": NOTE,
            "generator": {**generator_settings(), "airports": dict(sorted(centres.items()))},
            "sources": {name: file_sha256(path) for name, (_key, path) in files.items()}, "features": feats}


def dumps(fc: dict) -> str:
    """Tekst pliku: nagłówek (type, _uwaga, generator i sources po jednym kluczu na wiersz), potem jeden sektor
    na wiersz."""
    def value(v) -> str:
        if not isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return "{" + ",".join(f"\n {json.dumps(k)}: {json.dumps(x, ensure_ascii=False)}" for k, x in v.items()) + \
            ("\n}" if v else "}")
    head = [f"{json.dumps(k)}: {value(v)}" for k, v in fc.items() if k != "features"]
    feats = [json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in fc.get("features", [])]
    return "{\n" + ",\n".join(head) + ',\n"features": [\n' + ",\n".join(feats) + "\n]\n}\n"


def _short(v) -> str:
    text = json.dumps(v, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def check(fc: dict, import_dir: Path = IMPORT_DIR, seed_dir: Path | None = None) -> list[str]:
    """Rozbieżności między nagłówkami pliku ("sources", "generator") a plikami .ese, ustawieniami generatora
    i współrzędnymi lotnisk okręgów (pusta lista = plik aktualny)."""
    listed = fc.get("sources") or {}
    files = source_files(import_dir)
    out = []
    for name, sha in listed.items():
        if name not in files:
            out.append(f"{name}: pliku już nie ma")
        elif file_sha256(files[name][1]) != sha:
            out.append(f"{name}: plik się zmienił (sha256)")
    out += [f"{name}: nowy plik, nie ma go w sources" for name in files if name not in listed]
    gen = fc.get("generator")
    if not isinstance(gen, dict):
        return out + ["generator: brak wersji i ustawień generatora w pliku"]
    for k, v in generator_settings().items():
        was = gen.get(k)
        if was == v:
            continue
        if isinstance(was, dict) and isinstance(v, dict):  # firs: tylko pakiety, które się różnią
            diff = sorted(x for x in set(was) | set(v) if was.get(x) != v.get(x))
            out.append(f"generator: inne {k} ({', '.join(diff)})")
        else:
            out.append(f"generator: inne {k} (w pliku {_short(was)}, w kodzie {_short(v)})")
    centres = gen.get("airports")
    look = airport_lookup(import_dir, seed_dir)
    for icao, c in (centres if isinstance(centres, dict) else {}).items():
        now = look(icao)
        if (list(now) if now else None) != c:
            out.append(f"{icao}: inne współrzędne lotniska do okręgu (w pliku {_short(c)}, teraz {_short(now)}; "
                       "data/seed/airports.csv, data/import/icao.txt)")
    return out


def geojson_path() -> Path:
    return settings.seed_dir / FILENAME


def stale(path: Path) -> list[str]:
    """check() dla pliku na dysku; nieczytelny plik to też rozbieżność."""
    try:
        return check(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError, AttributeError) as exc:
        return [f"{path.name}: {exc}"]


def _ring_ok(ring) -> bool:
    """Pierścień: lista co najmniej 4 punktów [lon, lat] z liczbami (MAP i RADIO rozpakowują pary)."""
    return isinstance(ring, list) and len(ring) >= 4 and all(
        isinstance(pt, list) and len(pt) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in pt)
        for pt in ring)


def _geometry_ok(geom: dict) -> bool:
    """Polygon albo MultiPolygon, w którym każdy wielokąt ma pierścienie z poprawnymi punktami."""
    coords = geom.get("coordinates")
    polys = [coords] if geom.get("type") == "Polygon" else coords if geom.get("type") == "MultiPolygon" else None
    return isinstance(polys, list) and bool(polys) and all(
        isinstance(poly, list) and bool(poly) and all(_ring_ok(r) for r in poly) for poly in polys)


def _rows(fc: dict) -> tuple[list[dict], list[dict], int]:
    """Wiersze tabel sectors i nb_sectors z FeatureCollection; sektory z błędnymi polami (np. literówka w ręcznej
    poprawce: punkt bez szerokości, tekst zamiast liczby) pomijamy (liczba w wyniku), żeby nie psuły MAP i RADIO."""
    epww, nb, bad = [], [], 0
    for f in fc.get("features") or []:
        try:
            p, geom = f["properties"], f["geometry"]
            if not _geometry_ok(geom):
                raise ValueError("geometria")
            owners = p.get("owners") or []
            row = {"fir": str(p["fir"]), "name": str(p["name"]), "lower_ft": int(p["lower_ft"]),
                   "upper_ft": int(p["upper_ft"]),
                   "owners": ":".join(str(o) for o in (owners.split(":") if isinstance(owners, str) else owners) if o),
                   "geometry": json.dumps({"type": geom["type"], "coordinates": geom["coordinates"]})}
            source = str(p.get("source") or EPWW).upper()
            active = p.get("active") or []
            active = active if isinstance(active, str) else " ".join(str(a) for a in active)
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
            continue
        if source == EPWW:
            epww.append(row)
        else:
            nb.append({**row, "source": source[:8], "fir": row["fir"].upper(), "name": row["name"][:60],
                       "active": active})
    return epww, nb, bad


def import_sectors_geojson(db: Session, path: Path) -> dict:
    """Tabele sectors (source EPWW) i nb_sectors (sąsiedzi) od nowa z pliku sectors.geojson, w kolejności z pliku."""
    epww, nb, bad = _rows(json.loads(path.read_text("utf-8")))
    db.execute(delete(Sector))
    db.execute(delete(NbSector))
    if epww:
        db.execute(insert(Sector), epww)
    if nb:
        db.execute(insert(NbSector), nb)
    db.commit()
    return {"sectors": len(epww), "nb_sectors": len(nb), **({"skipped": bad} if bad else {})}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = geojson_path()
    if "--check" in argv:
        problems = stale(path)
        for p in problems:
            print(p)
        print("OK" if not problems else f"Uruchom {COMMAND}, żeby odświeżyć {FILENAME}.")
        return 1 if problems else 0
    fc = build()
    path.write_text(dumps(fc), encoding="utf-8", newline="\n")
    counts: dict[str, int] = {}
    for f in fc["features"]:
        counts[f["properties"]["source"]] = counts.get(f["properties"]["source"], 0) + 1
    print(f"{path}: {len(fc['features'])} sektorów ({', '.join(f'{k} {v}' for k, v in counts.items())}), "
          f"{path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
