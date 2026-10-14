# data/import

Tu wrzuć pliki z pakietu sektorowego EuroScope (PL vACC / GNG). Aplikacja wczyta je przy starcie
(i ponownie, gdy plik się zmieni). Pliki są trzymane w repozytorium, więc po sklonowaniu aplikacja ma od razu pełne dane.

| Plik | Co daje |
|------|---------|
| `*.sct` / `*.sct2` | VOR, NDB, FIX z sektorówki (mapa, trasy) |
| `*.ese` | stanowiska ATC i częstotliwości, SID/STAR, punkty VFR; sektory EPWW przez `data/seed/sectors.geojson` (niżej) |
| `airway.txt` | drogi lotnicze (rozwijanie tras, warstwa na mapie) |
| `isec.txt` | punkty nawigacyjne z całego świata (trasy) |
| `icao.txt` | lotniska z całego świata (trasy) |
| `ICAO_Airlines.txt` | baza callsignów (zakładka CALLSIGN) |
| `GRpluginOperatorInfo.txt` | oznaczenie operatorów CARGO (`C`) i wojskowych (`Mil`) w zakładce CALLSIGN |
| `ICAO_Aircraft.json` | typy samolotów z WTC / RECAT-EU / wymiarami (zakładka AIRCRAFT) |
| `qnh_pansa.png` | zrzut mapy rejonów QNH z AIP Polska (PAŻP): wzór dla `scripts/trace_qnh_regions.py --image` (aplikacja go nie czyta) |

## neighbours/

Pliki `.ese` vACC sąsiadów: z nich rysujemy sektory sąsiednich FIR-ów (MAP, RADIO) i bierzemy stanowiska ich lotnisk
(TWR, GND, DEL, ATIS, APP na kartach lotnisk w AERODROME). Nazwa pliku musi zawierać klucz pakietu; z każdego pliku
bierzemy tylko FIR-y i lotniska tego pakietu, nigdy sektorów EPWW:

| Plik | Klucz | FIR-y | Źródło (nazwa oryginalna) |
|------|-------|-------|---------------------------|
| `EDWW.ese` | EDWW | EDWW (z EDYY i EDUU HVL/OSE nad nim) | EDWW-EDWW_20261002090404-261001-0002 |
| `EDMM.ese` | EDMM | EDMM (z EDUU nad nim) | EDMM-AeroNav_20261001160608-261001-0001 |
| `EDGG.ese` | EDGG | EDGG (z EDUU nad nim); w rejonie Polski tylko pas na wschód od 10°E | EDGG-FULL_20261006174129-261001-0002 (AIRAC 2610) |
| `ESAA.ese` | ESAA | ESAA | ESAA-Sweden_20261001222500-261001-0003 |
| `EKDK.ese` | EKDK | EKDK | EKDK-Copenhagen_20261002185052-261001-0001 |
| `EYVL.ese` | EYVL | EYVL | EYVL-Installer_20261001170439-261001-0001 |
| `LKAA.ese` | LKAA | LKAA | SF_-_LKAA.ESE |
| `LZBB.ese` | LZBB | LZBB | LZBB-Full_20261001171141-261001-0001 |
| `UKBV.ese` | UKBV | tylko UKLV (Lwów); polskie sektory w tym pakiecie są nieaktualne | UKBV-Ukraine-TopSky-Beta-Pack_20251129175113-251201-0002 (AIRAC 2512) |
| `ULLL.ese` | ULLL | tylko UMKK (Kaliningrad) | ULLL-Galaxy-radar_20260614213420-260601-0001 (AIRAC 2606) |

Nowszy pakiet: podmień plik (nazwa może być oryginalna, byle zawierała klucz) i uruchom generator `sectors.geojson` (niżej).
Usunięcie pliku i generator usuwają sektory tego sąsiada, a w ich miejsce wracają kopie z naszego pliku `.ese`.
Białorusi (UMMV) nie ma: dodanie jej wymaga pliku i wpisu w `NB_SOURCES` (`backend/app/importers/neighbours.py`).

Ponowny import wszystkiego: `python -m backend.app.importers.seed --force`

## Sektory: data/seed/sectors.geojson

Sektory EPWW i sąsiadów aplikacja wczytuje z `data/seed/sectors.geojson` (tabele `sectors` i `nb_sectors`), nie z sekcji
`[AIRSPACE]` plików `.ese`; stanowiska, SID/STAR i resztę dalej bierze z plików `.ese`. Plik generuje:

```bash
python -m backend.app.importers.sectors_geojson          # od nowa z data/import/*.ese i data/import/neighbours/*.ese
python -m backend.app.importers.sectors_geojson --check  # czy plik pasuje do plików .ese i generatora
```

Jeden sektor na wiersz: `source` (`EPWW` albo klucz pakietu sąsiada), `fir`, `name`, `lower_ft`, `upper_ft`, `owners`
(kolejność przejmowania: w sektorach z pliku EPWW identyfikatory stanowisk z jego `[POSITIONS]`, u sąsiadów znaki stanowisk),
`active` (sąsiedzi: warunki `ACTIVE` ICAO:pas). Sektory sąsiadów: tylko w rejonie Polski, granice uproszczone o ok. 100 m
linia po linii (`SECTORLINE`), więc sąsiednie sektory mają na wspólnej granicy te same wierzchołki. Plik można poprawiać
ręcznie: aplikacja wczytuje go ponownie przy starcie, gdy się zmienił, a nadpisze go dopiero generator (sektor
z błędną geometrią, np. punktem bez szerokości, jest pomijany). Nagłówek pliku: `sources` (pliki `.ese` i sha256 ich
treści) i `generator` (wersja i ustawienia generatora: FIR-y pakietów, rejon, wiatr do wyboru pasów, uproszczenie, oraz
środki okręgów wokół lotnisk z `data/seed/airports.csv` albo `icao.txt`). Gdy nie pasują do plików i kodu (np. po
podmianie pliku `.ese`), `--check` i test `backend/tests/test_sectors_geojson.py` przypominają o ponownym wygenerowaniu,
aplikacja przy starcie zapisuje ostrzeżenie w logu, a build obrazu Dockera się przerywa (działający kontener zostaje).
