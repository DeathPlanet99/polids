import { api, esc, h } from "../api.js";

const CHANGES = [
  ["14.10.2026", "0.12.2 – sektory EPWW i sąsiadów z jednego pliku data/seed/sectors.geojson, generowanego z plików .ese (MAP i RADIO biorą je stamtąd); nowy plik .ese EDGG (Langen): sektory EDGG na MAP, a w AERODROME › ED i ET wszystkie lotniska z jego stanowiskami."],
  ["14.10.2026", "MAP: przy obsadzie wg kontrolerów online sektory obsługiwane przez to samo stanowisko są jednym obszarem w jego kolorze z jedną etykietą (stanowisko i częstotliwość); granica tylko tam, gdzie zmienia się obsługujący, przestrzeni bez nikogo nie rysujemy (gdy w FIR-ze nikogo nie ma, zostają same granice FIR-ów)."],
  ["14.10.2026", "MAP: nowa sekcja Pozycje – lista stanowisk FIR EPWW i sąsiadów (ACC, FIS i lotniska, np. EPGD: GD APP, GD TWR, GD GND), domyślnie zaznaczeni zalogowani. Kliknięcie włącza stanowisko offline jak po zalogowaniu (SYM, przerywany kontur) albo ukrywa zalogowane; sektoryzacja i plakietki lotnisk rysują się wg tego wyboru. „jak online” wraca do stanu z sieci, „wyczyść” wyłącza wszystkie. Na liście są stanowiska z pliku .ese EPWW, z vacs-data i obsługujące sektory sąsiadów; zalogowani na innych lotniskach zagranicznych (np. EDDS_TWR, LOWW_TWR) mają zawsze plakietkę i nie da się ich wyłączyć."],
  ["14.10.2026", "RADIO: sektory ACC na podglądach LOW, MID i HIGH tak samo – jeden obszar i jedna etykieta na stanowisko, granice tylko tam, gdzie zmienia się obsługujący, sektorów bez nikogo nie rysujemy (zostaje granica FIR EPWW)."],
  ["14.10.2026", "AERODROME: pasy, częstotliwości i elewacje lotnisk z AIP Polska i AIP MIL (eAIP PAŻP, AIRAC 10/26), lądowiska VFR spoza AIP z lotniska.dlapilota.pl; np. EPBK ma teraz 09R/27L 1350 m i trawiasty 09L/27R zamiast błędnych 01/19 i 08/26. Częstotliwości z godzinami pracy i uwagą (np. EPRA 118.430: TWR cywilna i wojskowa): przy stanowisku z pliku .ese w dymku, częstotliwości z AIP bez stanowiska w .ese pod listą (np. EPRA MIL GND 119.155, APP 128.680)."],
  ["14.10.2026", "Pas preferowany wg AIP AD 2.21 dla EPWA, EPMO, EPWR, EPGD i EPPO: godziny (zimą i latem w UTC), limity wiatru z porywami, wyjątki (burza, LVP); w podpowiedzi powód, np. „ARR/DEP 08 wg AIP (2000-0400 UTC), bo 26: w plecy 8 kt > 5 kt”. W LVP pasy dopuszczone przez AD 2.22: EPWA lądowania 33 albo 11 (RWY 11 CAT II od RVR 300 m, RWY 33 CAT IIIA od 175 m), starty 29 albo 15; EPPO lądowania 28, starty 28 albo 10; starty nigdy pod prąd lądowań, para pasów w limicie wiatru (EPWA przy 070/10 lądowania 11, starty 15), przy RVR pasa startowego < 125 m ostrzeżenie „starty niedozwolone”. Gdy hałas nie decyduje (np. mgła), do startu dalej pas z listy AIP (EPWA ARR 33 z ILS, DEP 29)."],
  ["14.10.2026", "LVP: progi każdego lotniska z AIP (przygotowanie, wprowadzenie, odwołanie) w dymku; stany PRZYGOTOWANIE LVP, LVP W MOCY i LVP ODWOŁANE (przez 30 min od odwołania) w AERODROME, AWOS i OVERVIEW; lotniska bez LVP w AIP: „LVP nie dotyczy”; lotniska wojskowe: LVP tylko dla odlotów."],
  ["12.10.2026", "0.12.1 – ATIS: pasy do lądowania i do startu czytane osobno (np. EPWA „APPROACH RUNWAY 33 … DEPARTURES RUNWAY 29” daje ARR 33 i DEP 29); pomijane są pasy zamknięte, na żądanie, przewidywane później i wspomniane w uwagach; osobne ATIS przylotowy i odlotowy są łączone."],
  ["12.10.2026", "Menu: INOP pod AD VFR; DOCS przeniesione do OTHER jako pierwsza podzakładka (stare adresy #docs przekierowują). AERODROME: listy lotnisk wszystkich FIR-ów w jednej kolumnie, z PgUp i PgDn. Panel częstotliwości: imię i nazwisko kontrolera zawsze widoczne obok stanowiska."],
  ["12.10.2026", "AWOS: przełącznik 1 PAS / 2 PASY / AUTO pod wiatrem; w 2 PASY osobny pas w kolumnach DEP i ARR; ręczny wybór wraca do automatu po zmianie pasów w ATIS albo po 12 h. RUCH na lotniskach bez A-CDM: EOBT i ETOT zamiast TSAT i TTOT, lot ze slotem na CTOT, gdy CTOT jest później niż ETOT."],
  ["12.10.2026", "RADIO: sektory EPWW, TMA, CTR i FIS bez nikogo online pokazują samo ID, bez częstotliwości; częstotliwość pojawia się, gdy ktoś je obsługuje (wieża przejmowana przez APP: częstotliwość APP z ↑APP)."],
  ["12.10.2026", "RADIO: stosy sąsiadów jak w VACS (tylko sektory graniczące z EPWW, bez FMP), częstotliwość tylko, gdy ktoś obsługuje sektor, bez nikogo w stosie ADVISORY 122.800, każda granica FL raz; warstwa APP w częściach TMA (WA APP, WA N APP, WA S APP), TMA bez APP z częstotliwością wieży, TMA Rzeszów z KK RZ APP; etykiety APP i TWR obok obrysów TMA i CTR z linią do punktu; LOW z suwakiem wysokości."],
  ["12.10.2026", "0.12.0 – menu jak w PANDORZE: strzałka ← na górze wraca do poprzedniego ekranu (także podzakładki i wybrane lotnisko, tak samo jak przycisk Wstecz przeglądarki); „?” to HOME z samym przywitaniem, a informacje o programie są w ABOUT; nowe zakładki SYT OP, AUP, STRIP i OTHER (AUP i OTHER będą dostępne w przyszłości)."],
  ["12.10.2026", "RADIO na jednej stronie jak w PANDORZE: podgląd FIR EPWW LOW z sąsiadami EDWW, EDMM, EDUU, ESAA i LKAA, HIGH z UMKK, EYVL, UMMV, UKLV i LZBB oraz mały MID; w każdym sektorze jego ID i częstotliwość stanowiska, które go teraz obsługuje; częstotliwości sąsiadów z podziałem na poziomy; warstwy ATIS, LOW MID HIGH, APP, TWR, TWR/ATIS i FIS; kliknięcie nazwy FIR-u sąsiada otwiera wyciąg z LOA. Dawne podzakładki RADIO (lotniska, GEO z mapą zasięgu, SEKTORYZACJA, rezerwacje) zastąpiła ta jedna strona."],
  ["12.10.2026", "AERODROME: podzakładki 0 (przegląd FIR EPWW), EPWW, ESAA, ED, ET, LKAA, LZBB, UMKK, EYVL, UMMV i UKLV z listami lotnisk (także VFR i MIL) i wyszukiwarką WYŚWIETL / WYCZYŚĆ; lotnisko zagraniczne pokazuje METAR, TAF, ATIS i stanowiska APP, TWR, GND, DEL, ATIS. RUCH jak DMAN/AMAN: drabiny TSAT, TTOT i ELDT oraz widok AMAN z punktami wejścia i odstępem FLOW. Paski EFES przeniesione do zakładki STRIP."],
  ["12.10.2026", "METEO: SAT PL i SAT EUR, TURB (mapy turbulencji IMGW od +0 do +21 h) i SIGWX (IMGW). SYT OP: wszystkie aktywne NOTAM-y FIR EPWW i głównych lotnisk z filtrem i sortowaniem."],
  ["11.10.2026", "0.11.1 – AERODROME › PRZEGLĄD: na polids.pl w przeglądarce z blokadą reklam (uBlock, AdBlock, Opera, Brave) pod paskiem METAR było pusto. Ogólne reguły EasyList chowały klasy „ad-grid” i „ad-col” (ad = aerodrome) jako reklamy; wszystkie nazwy z „ad” w interfejsie zmienione na „aero”. Karta wiatru bez METAR pokazuje „–” zamiast „undefined”."],
  ["08.10.2026", "AERODROME › OVERVIEW: jeden ekran dla FIR EPWW z poziomem przejściowym (jeden dla kraju: FL080, a FL090, gdy na którymkolwiek lotnisku QNH ≤ 995 hPa), METAR wszystkich lotnisk ze stanem LVP, aktywnymi NOTAM-ami, restrykcjami ECFMP i vIFF oraz Airport Monitorem vIFF. Filtr stanowiska: APP lub ACC zawęża lotniska (np. EPWA_APP: EPWA, EPMO, EPLL, EPRA) i pokazuje checklistę otwarcia, TWR przenosi do PRZEGLĄDU lotniska."],
  ["08.10.2026", "RADIO: nowa zakładka EDUU/EDYY; u sąsiadów prawdziwe callsigny radiowe z nazwami sektorów (VATSIM Germany Knowledgebase, LOA); pasek LOA z tabelami przekazań, zasadami i PDF-em; przyciski szybkiego skoku we wszystkich FIR-ach; lotniska VFR bez ID. DOCS: nowa kategoria LOA."],
  ["08.10.2026", "METEO › QNH: nowy podkład jak mapa rejonów QNH z AIP Polska (PAŻP), rejony odrysowane na nowo, pasy awaryjne 15–17 na 53°N i 51°N, ramki TMA/MTMA i SEKTOR n z QNH jak w vAWOS, szare oznaczenia TMA/CTR z mapy AIP (przełącznik „oznaczenia TMA/CTR”). AWOS: wiatr co 5 s z płynną wskazówką, RVR z METAR, „>2000 M” albo symulacja przy słabej widzialności. MAP: ATIS linia po linii, dane kontrolera po najechaniu na plakietkę lub opis stanowiska (także ESGG GND), lista sąsiednich FIR-ów domyślnie zwinięta."],
  ["08.10.2026", "Nowa nazwa: PolIDS (Polish Integrated Display System), wcześniej vPANDORA / POLIDS. Nowe logo w menu, na tej stronie i na stronie logowania, nowe ikony aplikacji i favicon."],
  ["07.10.2026", "Dania (EKDK) też z własnego pliku .ese: sektory Kopenhagi, Bornholmu i FIS na MAP i w RADIO › GEO, kolejność przejmowania z ich pliku. Z pliku EPWW zostaje już tylko Białoruś."],
  ["07.10.2026", "Sektory sąsiadów (Niemcy, Szwecja, Litwa, Czechy, Słowacja, Lwów, Kaliningrad) z ich własnych plików .ese: MAP rysuje je dokładnie na wybranym poziomie i koloruje wg tego, kto je obsługuje (kolejność przejmowania z ich plików), a RADIO › GEO pokazuje dokładny zasięg ich stanowisk. Z pakietu ukraińskiego bierzemy tylko Lwów, bez jego starych polskich sektorów. Sektory zależne od pasa w konfiguracji zachodniej."],
  ["07.10.2026", "MAP: poprawiony kształt sektora UKLV (Lwów) – bez fałszywego trójkąta w środku; szare nazwy FIR-ów nie zasłaniają już częstotliwości sąsiada."],
  ["07.10.2026", "ADMIN: lista CID-ów z dostępem, odmowy logowania"],
  ["07.10.2026", "0.9.0 - wersja serwerowa: Docker, logowanie przez VATSIM Connect, PWA"],
  ["07.10.2026", "Poprawki pod wielu użytkowników naraz, walidacja parametrów API"],
  ["06.10.2026", "RADIO › GEO: kafelki stanowisk jak strony GEO w VACS (bez FMP), z częstotliwościami i tym, kto jest online; obok mapa zasięgu: kliknij jedną lub kilka pozycji (także dowolne stanowiska FIR-ów sąsiednich), żeby zobaczyć, co obsługują. Sąsiedzi: wszystkie stanowiska z pliku .ese i vacs-data, zasięgi z VATSpy."],
  ["06.10.2026", "RADIO › SEKTORYZACJA (przeniesiona z MAP): kolejność przejmowania sektorów ACC z om.plvacc.pl › ownerships zamiast VACS, lista stanowisk w jednej kolumnie, krótkie nazwy pozycji (PO N APP, EPWW DBF, EDMM MEI, WA TWR)."],
  ["06.10.2026", "TMA i CTR z pełną kolejnością przejmowania: najpierw APP i TWR z pliku .ese, potem ACC wg om.plvacc.pl. RADIO › SEKTORYZACJA: karta TMA i CTR, stanowiska APP/TWR w symulacji. MAP: dymek TMA, CTA i CTR pokazuje wszystkie stanowiska po kolei z częstotliwością i tym, kto jest online; warstwa CTA (np. CTA 02 nad TMA Warszawa) domyślnie włączona. RADIO › GEO: kafelki sąsiadów w kolorach FIR-ów jak w VACS. Komunikaty po polsku, gdy serwer nie odpowiada."],
  ["06.10.2026", "MAP: przestrzeń rysowana dla wybranego poziomu (FL) albo dla wszystkich poziomów, listy do wyboru pojedynczych sektorów ACC, TMA, CTR i FIR-ów sąsiednich, opis TMA/CTR po najechaniu, samoloty w kolorach odlot / przylot / tranzyt, ATIS z nazwiskiem kontrolera, bez UNICOM i bez hierarchii dziedziczenia. Naprawione podświetlanie TMA Warszawa przy EPWA APP online."],
  ["06.10.2026", "METEO: WIND (dawniej WINDY) z przełącznikiem 0 ft / 3000 ft na podejściu (punkt FAF/IAF każdego pasa), METAR i TAF alfabetycznie. AERODROME: wiatr 0 ft i 3000 ft w punktach podejścia (Open-Meteo), pasy ARR/DEP w kolorach listy lotów."],
  ["06.10.2026", "AIRCRAFT: przyciski AIRBUS, BOEING, EMBRAER, MCDONNELL, ATR, CESSNA. Cała aplikacja w czcionce Consolas."],
  ["06.10.2026", "MAP: sektoryzacja LOW/MID/HIGH wg łańcuchów VACS"],
  ["06.10.2026", "RADIO: stanowiska sąsiadów jak w profilu VACS ACC_EPWW"],
  ["06.10.2026", "AERODROME: paski EFES w zakładce RUCH"],
  ["06.10.2026", "Wygląd w stylu NM UI"],
  ["06.10.2026", "AERODROME: widok AWOS, wschód i zachód słońca"],
  ["06.10.2026", "METEO: mapa QNH regionalnego"],
  ["06.10.2026", "MAP: scenariusze i TV z vIFF, szczegóły lotu"],
  ["05.10.2026", "AERODROME: odloty z vIFF (EOBT, CTOT, CDM)"],
  ["05.10.2026", "MAP: TMA/CTR, przepustowość sektorów, karta lotniska, plakietki ATC"],
  ["05.10.2026", "EMERGENCY, CHECKLIST, PHRASEOLOGY"],
  ["05.10.2026", "AERODROME: pas w użyciu z ATIS / pas preferowany, NOTAM"],
  ["05.10.2026", "RADIO: kto jest online, rezerwacje"],
  ["05.10.2026", "MAP: ruch VATSIM, trasy, granice FIR"],
  ["05.10.2026", "Import sektorówki EPWW, navdata, callsignów i typów samolotów"],
  ["05.10.2026", "Pierwsza wersja"],
];

const TILES = [
  ["aerodrome", "AERODROME", "Przegląd FIR EPWW, listy lotnisk EPWW i sąsiadów, lotnisko na jednym ekranie: METAR, wiatr, pasy, ATIS, NOTAM, AWOS, RUCH (DMAN/AMAN)."],
  ["meteo", "METEO", "METAR i TAF lotnisk, mapa QNH regionalnego, wiatr przy ziemi i na podejściu, satelita, turbulencje, SIGWX."],
  ["map", "MAP", "Ruch VATSIM, przestrzeń na wybranym poziomie, TMA/CTR, przepustowość sektorów i status lotu z vIFF."],
  ["radio", "RADIO", "Jedna strona jak w PANDORZE: sektory ACC EPWW na podglądach LOW/MID/HIGH z częstotliwościami, warstwy APP/TWR/FIS, ATIS i stanowiska sąsiadów; nazwa FIR-u sąsiada otwiera wyciąg z LOA."],
  ["sytop", "SYT OP", "Aktywne NOTAM-y FIR EPWW i głównych lotnisk."],
  ["strip", "STRIP", "Paski postępu lotu EFES dla 15 głównych lotnisk."],
  ["callsign", "CALLSIGN", "Znaki wywoławcze linii lotniczych."],
  ["aircraft", "AIRCRAFT", "Typy samolotów wg producenta, kategorie turbulencji, zdjęcia."],
  ["checklist", "CHECKLIST", "Otwarcie i zamknięcie stanowiska, przekazanie, zmiana pasa."],
  ["emergency", "EMERGENCY", "ASSIST i checklisty sytuacji awaryjnych."],
];

export default {
  mount(root, ctx) {
    const pane = h(`<div class="pane about">
      <div class="about-head">
        <div class="logo"><img src="/static/icons/polids-logo.svg" alt="${esc(ctx.config.name || "PolIDS")} – Polish Integrated Display System"><small>VATSIM PL vACC</small></div>
        <div class="card about-info"><h3>System</h3><dl class="props info"></dl></div>
      </div>
      <div class="about-tiles">${TILES.map(([tab, name, txt]) => `<button class="about-tile${tab === "emergency" ? " red" : ""}" data-open="${tab}"><b>${name}</b><span>${esc(txt)}</span></button>`).join("")}</div>
      <div class="card about-changes"><h3>Ostatnie zmiany</h3>
        <table class="data"><tbody>${CHANGES.map(([d, t]) => `<tr><td class="mono">${esc(d)}</td><td>${esc(t)}</td></tr>`).join("")}</tbody></table></div>
    </div>`);
    root.append(pane);
    pane.querySelector(".about-tiles").addEventListener("click", (e) => {
      const b = e.target.closest("[data-open]");
      if (b) ctx.open(b.dataset.open);
    });
    const load = async () => {
      const st = await api("/api/status").catch(() => ({ counts: {} }));
      const c = st.counts;
      const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
      pane.querySelector(".info").innerHTML = row("Wersja", esc(ctx.config.version || "–"))
        + row("Cykl AIRAC", `${esc(ctx.config.airac.ident)} (od ${esc(ctx.config.airac.effective)})`)
        + row("Dane", `${c.aerodromes ?? "–"} lotnisk, ${c.aircraft_types ?? "–"} typów samolotów, ${c.callsigns ?? "–"} callsignów`)
        + row("Nawigacja", `${c.nav_points ?? "–"} punktów, ${c.airway_segments ?? "–"} odcinków dróg, ${c.sectors ?? "–"} sektorów, ${c.atc_positions ?? "–"} stanowisk ATC`)
        + row("Klucz CARTO", ctx.config.carto_api_key ? "wczytany" : "brak (mapa używa zastępczego podkładu Esri)")
        + row("Klucz OpenAIP", ctx.config.openaip_api_key ? "wczytany" : "brak")
        + row("Logowanie", ctx.me?.user ? `${esc(ctx.me.user.name || "")} (CID ${esc(ctx.me.user.cid)})${ctx.me.admin ? ", administrator" : ""}` : "wyłączone (tryb lokalny)")
        + row("API", `<a href="/docs" target="_blank">/docs</a>`);
    };
    return { activate: load };
  },
};
