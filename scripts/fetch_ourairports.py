"""Pobiera dane lotnisk i pomocy nawigacyjnych dla Polski z OurAirports (domena publiczna,
https://ourairports.com/data/) i zapisuje je do data/seed/ (airports.csv, navaids.csv).

Pasów (runways.csv) i częstotliwości (frequencies.csv) skrypt już nie nadpisuje: są przepisane ręcznie z AIP Polska
(AD 2.12/2.18, AIP MIL AD 4, AIP VFR AD 4, dla lądowisk spoza AIP lotniska.dlapilota.pl) ze źródłem w każdym wierszu,
a dane OurAirports miały błędy (np. EPBK z pasami 01/19 i 08/26, EPSW z kopią pasa EPLB, częstotliwości FIS i stare
kanały 25 kHz). Elewacje ze źródłem (kolumna elevation_source w airports.csv: AIP, lotniska.dlapilota.pl…) zostają
przy aktualizacji. Lotnisko, do którego odwołują się runways.csv albo frequencies.csv, a którego OurAirports już nie
ma (zamknięte, zmiana typu albo oznaczenia), zostaje z poprzedniego airports.csv z ostrzeżeniem do sprawdzenia.

Użycie:
    python scripts/fetch_ourairports.py [PREFIKS_ICAO=EP] [KRAJ_ISO=PL]
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/"
SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


def get(name):
    with urllib.request.urlopen(BASE + name, timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def write(name, rows, fields):
    with open(SEED / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{name}: {len(rows)}")


def read_seed(name) -> list[dict]:
    path = SEED / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def kept_elevations(old: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """Elewacje ze źródłem (AIP, lotniska.dlapilota.pl, airfield.directory) z obecnego airports.csv:
    {ICAO: (elevation_ft, elevation_source)}."""
    return {i: (r["elevation_ft"], r["elevation_source"]) for i, r in old.items() if r.get("elevation_source")}


def main(prefix="EP", country="PL"):
    old = {r["ident"]: r for r in read_seed("airports.csv")}
    keep = kept_elevations(old)
    airports = [
        a for a in get("airports.csv")
        if a["iso_country"] == country and a["ident"].startswith(prefix) and len(a["ident"]) == 4
        and a["type"] not in ("closed", "heliport")
    ]
    for a in airports:
        a["elevation_ft"], a["elevation_source"] = keep.get(a["ident"], (a["elevation_ft"], ""))
    # pasy i częstotliwości z AIP mają klucz obcy do lotniska: bez wiersza lotniska import by je pominął
    used = {r["airport_ident"] for name in ("runways.csv", "frequencies.csv") for r in read_seed(name)}
    for ident in sorted(used - {a["ident"] for a in airports}):
        if ident in old:
            airports.append(old[ident])
            print(f"UWAGA: {ident} nie ma w OurAirports, zostaje wiersz z poprzedniego airports.csv (sprawdzić w AIP)")
        else:
            print(f"UWAGA: {ident} z runways.csv / frequencies.csv bez wiersza w airports.csv (import pominie te dane)")
    write("airports.csv", airports,
          ["ident", "type", "name", "latitude_deg", "longitude_deg", "elevation_ft", "elevation_source", "municipality",
           "iata_code"])
    navaids = [n for n in get("navaids.csv") if n["iso_country"] == country]
    write("navaids.csv", navaids,
          ["ident", "name", "type", "frequency_khz", "latitude_deg", "longitude_deg", "elevation_ft",
           "magnetic_variation_deg"])


if __name__ == "__main__":
    main(*sys.argv[1:])
