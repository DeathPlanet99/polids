"""Pobiera dane lotnisk i pomocy nawigacyjnych dla Polski z OurAirports (domena publiczna,
https://ourairports.com/data/) i zapisuje je do data/seed/ (airports.csv, navaids.csv).

Pasów (runways.csv) i częstotliwości (frequencies.csv) skrypt już nie nadpisuje: są przepisane ręcznie z AIP Polska
(AD 2.12/2.18, AIP MIL AD 4, AIP VFR AD 4, dla lądowisk spoza AIP lotniska.dlapilota.pl) ze źródłem w każdym wierszu,
a dane OurAirports miały błędy (np. EPBK z pasami 01/19 i 08/26, EPSW z kopią pasa EPLB, częstotliwości FIS i stare
kanały 25 kHz). Elewacje z AIP (kolumna elevation_source w airports.csv) zostają przy aktualizacji.

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


def aip_elevations() -> dict[str, tuple[str, str]]:
    """Elewacje z AIP z obecnego airports.csv: {ICAO: (elevation_ft, elevation_source)}."""
    path = SEED / "airports.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["ident"]: (r["elevation_ft"], r["elevation_source"]) for r in csv.DictReader(f)
                if (r.get("elevation_source") or "").startswith("AIP")}


def main(prefix="EP", country="PL"):
    keep = aip_elevations()
    airports = [
        a for a in get("airports.csv")
        if a["iso_country"] == country and a["ident"].startswith(prefix) and len(a["ident"]) == 4
        and a["type"] not in ("closed", "heliport")
    ]
    for a in airports:
        a["elevation_ft"], a["elevation_source"] = keep.get(a["ident"], (a["elevation_ft"], ""))
    write("airports.csv", airports,
          ["ident", "type", "name", "latitude_deg", "longitude_deg", "elevation_ft", "elevation_source", "municipality",
           "iata_code"])
    navaids = [n for n in get("navaids.csv") if n["iso_country"] == country]
    write("navaids.csv", navaids,
          ["ident", "name", "type", "frequency_khz", "latitude_deg", "longitude_deg", "elevation_ft",
           "magnetic_variation_deg"])


if __name__ == "__main__":
    main(*sys.argv[1:])
