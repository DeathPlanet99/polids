# PolIDS

![PolIDS](frontend/icons/polids-logo.svg)

Polish Integrated Display System dla kontrolerów VATSIM PL vACC. Układ wzorowany na PANDORZE (PAŻP), zawartość zakładek
w stylu EUROCONTROL NM UI. Backend FastAPI + SQLite, frontend w czystym JS, całość w Dockerze.

| Zakładka | Co jest |
|---|---|
| RADIO | jedna strona jak w PANDORZE: u góry pasek ATIS polskich lotnisk, podglądy FIR EPWW LOW (przekrój FL095–FL335 przyciskami ◀ ▶), mały MID i HIGH z ID sektora i częstotliwością stanowiska, które go teraz obsługuje (kolejność przejmowania z om.plvacc.pl); stosy stanowisk sąsiadów z podziałem na poziomy (ESAA, EDWW, EDUU, EDMM, LKAA po lewej; UMKK, EYVL, UMMV, UKLV, LZBB po prawej); warstwy ATIS, LOW MID HIGH, APP, TWR, TWR/ATIS i FIS; kliknięcie nazwy FIR-u sąsiada otwiera wyciąg z LOA (tabele przekazań, zasady, stanowiska, PDF w OTHER › DOCS) |
| METEO | METAR/TAF, mapa QNH regionalnego na podkładzie jak mapa rejonów QNH z AIP Polska (PAŻP), WIND z wiatrem 0 ft / 3000 ft na podejściu do każdego pasa, SAT PL i SAT EUR, TURB (mapy turbulencji IMGW +0…+21 h z odtwarzaniem), SIGWX (IMGW) |
| AERODROME | podzakładki jak w PANDORZE: 0 = OVERVIEW całego FIR EPWW (jeden TL dla kraju: FL080, a FL090, gdy na którymkolwiek lotnisku QNH ≤ 995 hPa; METAR/TAF ze stanem LVP, aktywne NOTAM-y, restrykcje ECFMP i vIFF, Airport Monitor vIFF; filtr stanowiska: APP/ACC zawęża lotniska i pokazuje checklistę otwarcia, TWR przenosi do lotniska); EPWW, ESAA, ED, ET, LKAA, LZBB, UMKK, EYVL, UMMV, UKLV = listy lotnisk FIR-u (także VFR i MIL) z wyszukiwarką WYŚWIETL / WYCZYŚĆ; lotnisko zagraniczne: karta z METAR, TAF, ATIS i stanowiskami APP/TWR/GND/DEL/ATIS; lotnisko polskie: PRZEGLĄD (wiatr, pasy, ATIS, LVP, NOTAM, częstotliwości, wiatr na podejściu), AWOS (wiatr co 5 s, RVR z METAR albo symulowany) i RUCH jak DMAN/AMAN (drabiny TSAT, TTOT i ELDT, widok AMAN z punktami wejścia i odstępem FLOW) |
| AD CIV / AD MIL / AD VFR, INOP | eAIP PAŻP; INOP: om.plvacc.pl (procedury operacyjne PL vACC) |
| CALLSIGN, AIRCRAFT | callsigny, typy samolotów (WTC, RECAT-EU, wymiary, zdjęcia, przyciski producentów) |
| MAP | ruch VATSIM (odlot / przylot / tranzyt), przestrzeń na wybranym poziomie FL (ACC, TMA, CTA, CTR, FIS, sektory sąsiadów z ich plików `.ese`), przepustowość i szczegóły lotów z vIFF; ATIS linia po linii, dane kontrolera po najechaniu na plakietkę lub opis stanowiska, lista sąsiednich FIR-ów domyślnie zwinięta |
| PHRASEOLOGY | baza frazeologii EUROCONTROL |
| CHECKLIST, EMERGENCY | checklisty stanowiska, procedury awaryjne EUROCONTROL |
| SYT OP | wszystkie aktywne NOTAM-y FIR EPWW i głównych lotnisk (cv.plvacc.pl) z filtrem lokalizacji i tekstu, sortowaniem i pełną treścią |
| AUP | na razie tylko informacja, że będzie dostępny w przyszłości |
| STRIP | paski postępu lotu EFES (przeniesione z AERODROME › RUCH), podzakładka dla każdego z 15 głównych lotnisk |
| OTHER | podmenu; pierwsza podzakładka DOCS: PDF-y z `data/docs/` (w tym LOA EPWW z sąsiadami), adres `#other/docs/<plik>` (stare `#docs/<plik>` przekierowuje) |
| ? (HOME), ABOUT | HOME: samo przywitanie „Witaj, imię i nazwisko (CID)”; ABOUT: informacje o programie, skróty do zakładek i lista zmian |
| ADMIN | lista CID-ów z dostępem i odmowy logowania (tylko admin) |

Źródła danych: pliki EuroScope (sektorówka EPWW, navdata, pliki `.ese` vACC sąsiadów w `data/import/neighbours/`), VATSIM (data feed, bookings), metar.vatsim.net /
aviationweather.gov, NOTAM z cv.plvacc.pl, [vIFF](https://api.viffsys.com/docs), [ECFMP](https://ecfmp.vatsim.net), OurAirports, vacs-data, VATSpy,
[VATSIM Germany Knowledgebase](https://knowledgebase.vatsim-germany.org/shelves/centersektoren) (`data/seed/names_de.json`), LOA (`data/docs/LOA/`, wyciąg `data/seed/loa.json`).

## Uruchomienie

```bash
cp .env.example .env
docker compose up --build
```

http://localhost:1337 - pierwszy build trwa kilka minut (import sektorówki i navdata do bazy w obrazie).
Lokalnie domyślnie jest `POLIDS_AUTH_MODE=dev`, czyli formularz z dowolnym CID i ratingiem zamiast VATSIM.

Praca nad kodem (backend przeładowuje się sam, zmiany w JS/CSS po odświeżeniu):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Bez Dockera: `pip install -r requirements.txt` i
`python -m uvicorn backend.app.main:app --port 1337 --reload --reload-dir backend`.

Testy:

```bash
docker build --target test -t polids-test . && docker run --rm polids-test
```

Swagger: http://localhost:1337/docs (po zalogowaniu).

## Konfiguracja

Wszystko przez zmienne `POLIDS_*` w `.env`, opis w [.env.example](.env.example). Klucze wpisujemy tylko do `.env`,
repo jest publiczne.

Logowanie (`POLIDS_AUTH_MODE`):

- `none` - bez logowania,
- `dev` - formularz testowy, tylko na localhost,
- `vatsim` - VATSIM Connect, na serwerze wymuszone w `docker-compose.prod.yml`. Wymaga `POLIDS_VATSIM_CLIENT_ID`,
  `POLIDS_VATSIM_CLIENT_SECRET` i `POLIDS_SECRET_KEY` (min. 32 znaki), inaczej aplikacja nie wstanie.

Kto może wejść:

- administratorzy z `POLIDS_AUTH_ADMIN_CIDS` - zawsze, i tylko oni widzą zakładkę ADMIN,
- CID-y dodane przez admina w zakładce ADMIN (dodaj, usuń, opis). Usunięcie odcina dostęp od razu,
- reszta dostaje "Brak dostępu dla CID ...", a próba ląduje w ADMIN -> Odmowy dostępu, skąd można ją dodać jednym klikiem.

Lista jest w osobnej bazie `data/state/polids-state.db` (w Dockerze wolumen `state`), bo `polids.db` jest
przebudowywana z repo przy każdym obrazie. Bez logowania dostępne jest tylko `/auth/*`, `/healthz` i pliki PWA.

Do testów VATSIM Connect bez prawdziwych kont jest sandbox `https://auth-dev.vatsim.net` (konta 10000000-10000010,
hasło = CID), ustawiany przez `POLIDS_VATSIM_AUTH_URL`.

PWA: w Chrome/Edge "Zainstaluj aplikację" w pasku adresu - otwiera się jako osobne okno.

## Serwer

Docker Compose na VPS + Caddy (Let's Encrypt), obraz budowany w GitHub Actions i trzymany w GHCR, deploy po pushu
do `main`. Instrukcja: [deploy/README.md](deploy/README.md).

## Aktualizacja danych

- nowa sektorówka: podmienić pliki w `data/import/` i przebudować obraz (na serwerze wystarczy push)
- OurAirports: `python scripts/fetch_ourairports.py`
- typy samolotów: `python scripts/build_aircraft_seed.py <ścieżka do aircraft-db>`
- logo i ikony: `frontend/icons/` (paczka logo PolIDS, opcja „FIR EPWW”)
- sąsiedzi: nowszy plik `.ese` vACC sąsiada do `data/import/neighbours/` (lista w `data/import/README.md`)
- bez kodu: `data/seed/qnh_regions.json`, `lvp.json`, `runway_config.json`, `checklists.json`, `emergency.json`,
  `callsigns.csv`

## Pas preferowany

Jeśli na lotnisku nadaje ATIS, pas w użyciu bierzemy z jego tekstu. Bez ATIS-u (zasada z
[vatiris](https://github.com/minsulander/vatiris)):

1. LVP / przygotowanie LVP / IMC: kierunek z ILS, o ile wiatr w plecy nie przekracza 5 kt,
2. słaby wiatr (< 5 kt): konfiguracja z `data/seed/runway_config.json`, bez niej kierunek z ILS,
3. w pozostałych przypadkach największa składowa czołowa.

Wyposażenie pasów (ILS, RNP, VOR, NDB) czytane jest z nazw procedur w `.ese`. Na razie `runway_config.json` ma tylko EPWA.

## Znane ograniczenia

- PAŻP, om.plvacc.pl i EUROCONTROL Learning Zone blokują ramki (`X-Frame-Options`) - zostaje przycisk "otwórz w nowej karcie".
- Format NOTAM z cv.plvacc.pl nie jest udokumentowany, parser dzieli tekst po nagłówkach `A1234/26 NOTAMN`.
- Parser ATIS obsługuje typowe formaty vATIS.
- vIFF nie publikuje schematów odpowiedzi; TOBT/TSAT/TTOT są tylko, gdy ktoś prowadzi CDM na EPWA. Godziny w vIFF
  są bez daty, przejście przez północ liczone względem bieżącej godziny UTC.
- AWOS nie ma czujników - wiatr chwilowy, średnia 2 min i min/max są symulowane z METAR (oznaczenie SYM). RVR bez grupy w METAR
  jest symulowany z widzialności (zakładamy światła HI, więc w nocy na małych lotniskach może wyjść za wysoki).
- QFE liczone w przybliżeniu z QNH i elewacji.
- Rejony QNH 1-14 i obszary TMA/MTMA w `qnh_regions.json` odrysowane ze zrzutu mapy z AIP Polska (`data/import/qnh_pansa.png`,
  `scripts/trace_qnh_regions.py`, ok. 0,5 km). QNH rejonu = najniższe QNH z METAR jego lotnisk (oficjalne liczy IMGW z modelu);
  pasy 15-17 (53°N, 51°N) = najniższe QNH z lotnisk pasa. Progi LVP do sprawdzenia z INOP.
- OVERVIEW: TL liczony z QNH 15 kontrolowanych lotnisk z METAR (lotnisko bez METAR nie jest liczone); próg 995 hPa
  włącznie (OM PL vACC pisze „poniżej 995”). TA 6500 ft niesprawdzone w eAIP. Część pól vIFF (przepustowość lotniska, przyloty
  w godzinie) odczytana z dokumentacji i odpowiedzi API. NOTAM-y wg okresu B)-C), bez harmonogramu D). ECFMP/vIFF zawsze dla całego FIR.
- LOA w RADIO to ręczny wyciąg z PDF-ów (obowiązuje PDF); LOA z Lwowem jest z 2022 r. Nazwy niemieckie z Knowledgebase (08.10.2026);
  CTR bez nazwy w żadnym źródle dostaje callsign wspólny dla FIR-u (szarszy, „przyjęty”).
- Kolejność przejmowania sektorów ACC z tabel om.plvacc.pl/docs/2610/ownerships (`data/seed/ownership.json`, wklejone 06.10.2026,
  przy nowym AIRAC trzeba wkleić ponownie); B, C, D HIGH i T MID wg pliku `.ese`. TMA/CTR: najpierw APP/TWR z `.ese`, potem ACC.
- Sektory sąsiadów: z każdego pliku tylko FIR-y tego vACC (z ukraińskiego tylko Lwów), sektory zależne od pasa w jednym układzie
  na lotnisko (kierunek najbliższy wiatrowi z ok. 250°), Białoruś (UMMV) z kopii w pliku EPWW. Zasięg stanowisk bez sektora
  w żadnym pliku jest przybliżony (VATSpy albo okrąg 30/10 NM).
- Paski EFES: CFL, TAXI/GATE, NR, PRV, LP/TG puste - tych danych nie ma w feedzie VATSIM ani w vIFF.
- Rezerwacje VATSIM mają tylko CID; imię widać, gdy ta osoba jest online.
- Jeden worker uvicorna (cache w pamięci). 40 osób odświeżających co 15 s to ~23 req/s przy p95 < 100 ms;
  sufit jednego procesu to ok. 50 req/s, wyżej trzeba Redisa i kilku workerów.

## Licencje danych

- typy samolotów: [aircraft-database.com](https://aircraft-database.com) (ODC-By), progi WTC/RECAT z
  [vatger/atciss](https://github.com/vatger/atciss) (MIT)
- lotniska, pasy, pomoce: [OurAirports](https://ourairports.com/data/) (public domain)
- Leaflet (BSD-2), CARTO/OpenStreetMap (ODbL), Esri World Gray Canvas
- ATFCM/CDM: [vIFF](https://viffsys.com) (Roger Puig), tylko odczyt
- granice FIR: [vatspy-data-project](https://github.com/vatsimnetwork/vatspy-data-project) (CC BY-SA 4.0)
- callsigny i nazwy sektorów niemieckich: [VATSIM Germany Knowledgebase](https://knowledgebase.vatsim-germany.org/shelves/centersektoren)
  (tylko krótkie dane, przy każdym wpisie adres źródła)
- LOA: porozumienia PL vACC z vACC sąsiadów (`data/docs/LOA/`)
- mapa rejonów QNH: AIP Polska (PAŻP), zrzut `data/import/qnh_pansa.png`
- sektory i stanowiska FIR-ów sąsiednich: pliki `.ese` pakietów sektorowych vACC sąsiadów (`data/import/neighbours/`)
- łańcuchy sektorów i etykiety sąsiadów: [vacs-data](https://github.com/vacs-project/vacs-data)
  ([CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), commit c5aa4bd), wyciąg
  `data/seed/vacs_epww.json` na tej samej licencji
- checklisty stanowiska i wybór pasa: [vatiris](https://github.com/minsulander/vatiris) (GPL-3.0)
- checklisty EMERGENCY: EUROCONTROL HRS/TSP-004-GUI-05 wyd. 2.0, Annex A i B
- widok AWOS i mapa QNH wzorowane na [vAWOS](https://github.com/aleksandermarcingadomski-commits/vAWOS)
  (bez licencji, więc kod napisany od nowa)
- zdjęcia samolotów z Wikipedii, autor i licencja na stronie artykułu
- pliki EuroScope: pakiet sektorowy PL vACC / GNG

PDF-y w `data/docs/` (ICAO Doc 4444, Doc 9432, RECAT-EU) są chronione prawem autorskim - na serwerze tylko po
zalogowaniu, ale w publicznym repo lepiej trzymać linki niż pliki.
