import { BASEMAPS, LIGHT_BASEMAPS, api, atcPositions, atisHtml, esc, h, hhmm, lsGet, lsSet, vatsimAtc, vatsimOnline } from "../api.js";
import { chartHtml, delayClass, listRow, loadRatio, stateChip, tvGroup, tvLabel } from "../viffchart.js";
import { FACILITIES, PLANE_PATH, SymbolMarker, aircraftMarker, airportBadge, altLabel, atcPanes, atcRows, atcTooltip, colorFor, controllerRows, drawFirs, fl,
  loadFirs, loadVacs, sliceOwner, symbolFor, symbolSvg, vacsLayer } from "../airspace.js";
import { airspaceChain } from "../chains.js";
import { labelPoint } from "../coverage.js";
import { displayName, loadDisplayNames } from "../posname.js";

// Kolory zależne od podkładu (ciemny / jasny)
const THEME = {
  dark: { ad: "#c8ced4", vor: "#3ecf6e", ndb: "#c58cff", fix: "#8c969e", vfr: "#ffb020", route: "#ff4dd2", flight: "#ff9f1a", halo: "#000", airway: "#5f6b78",
    tma: "#7f95ff", ctr: "#ff6b6b", fis: "#35c4b0", cta: "#c9b26b", atz: "#e08ad6", nb: "#8d99a6", idle: "#5c665c" },
  light: { ad: "#33393f", vor: "#0a7a36", ndb: "#7b2cbf", fix: "#5c656c", vfr: "#c26a00", route: "#b0007c", flight: "#d4380d", halo: "#fff", airway: "#8b96a0",
    tma: "#3550c8", ctr: "#d62828", fis: "#0b8a78", cta: "#8a7426", atz: "#a03a96", nb: "#56616c", idle: "#8f989f" },
};
const KIND_COLOR = { aerodrome: "ad", vor: "vor", ndb: "ndb", fix: "fix", vfr: "vfr" };
const sw = (cls, label, checked = false, sym = "") => `<label class="sw"><input type="checkbox" class="${cls}" ${checked ? "checked" : ""}>
  <span class="sw-sym">${sym}</span><span>${label}</span></label>`;
const plane = (cls = "") => `<svg class="sym" width="18" height="18" viewBox="-10 -10 20 20"><path d="${PLANE_PATH}" class="acsym ${cls}"/></svg>`;
const PLANE_SVG = plane();
// localStorage przez lsGet/lsSet (tryb prywatny: bez zapisu). Odczyt tylko wartości tego samego typu co domyślna
// (obiekt, tablica, liczba, tekst…; przy domyślnym null także tekst): uszkodzony wpis (np. null w map.air) nie psuje MAP.
const typeOf = (x) => (x === null ? "null" : Array.isArray(x) ? "array" : typeof x);
const store = {
  get: (k, d) => {
    let v;
    try { v = JSON.parse(lsGet(k)); } catch { return d; }
    return typeOf(v) === typeOf(d) || (d === null && typeof v === "string") ? v : d;
  },
  set: (k, v) => lsSet(k, JSON.stringify(v)),
};
// liczebnik z rzeczownikiem po polsku: 1 punkt, 2 punkty, 5 punktów
const plural = (n, one, few, many) => `${n} ${n === 1 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many}`;
const lg = (sym, label, cls = "") => `<div class="lgx-i ${cls}"><span class="lgx-s">${sym}</span><span>${label}</span></div>`;

// Warstwy przestrzeni (wycinki sektorów z pliku .ese, /api/nav/slices): przełącznik i lista wyboru na każdy rodzaj.
// kinds = rodzaje wycinków z backendu; head = nagłówek grupy na liście wyboru.
// CTA osobno i domyślnie włączone: CTA 02 (FL225–FL245 nad TMA Warszawa, WA APP) zamyka dziurę nad TMA.
const AIR = [
  { id: "acc", label: "ACC EPWW", kinds: ["acc"], on: true, sym: `<span class="swatch sec"></span>` },
  { id: "tma", label: "TMA", kinds: ["tma"], on: true, sym: `<span class="swatch tma"></span>` },
  { id: "cta", label: "CTA", kinds: ["cta"], on: true, sym: `<span class="swatch cta"></span>` },
  { id: "ctr", label: "CTR", kinds: ["ctr"], on: true, sym: `<span class="swatch ctr"></span>` },
  { id: "oth", label: "FIS, ATZ/TRA", kinds: ["fis", "atz", "oth"], on: false, sym: `<span class="swatch fis"></span>` },
  { id: "nb", label: "sąsiednie FIR", kinds: ["nb"], on: true, sym: `<span class="swatch nb"></span>` },
];
const AIR_TAG = { acc: "ACC", tma: "TMA", cta: "CTA", ctr: "CTR", oth: "FIS/ATZ", nb: "sąsiedzi" };
const PICK_OF = Object.fromEntries(AIR.flatMap((a) => a.kinds.map((k) => [k, a.id])));
// kolejność w dymku (z góry): ACC, TMA, CTR, CTA, FIS, ATZ, sąsiedzi
const KIND_ORDER = ["acc", "tma", "ctr", "cta", "fis", "atz", "oth", "nb"];
const KIND_TAG = { acc: "ACC", tma: "TMA", ctr: "CTR", fis: "FIS", cta: "CTA", atz: "ATZ", oth: "INNE", nb: "FIR" };
const OTH_HEAD = { fis: "FIS", atz: "ATZ / TRA", oth: "inne" };
// przestrzenie z kolejnością przejmowania z chains.js (APP/TWR z .ese, potem ACC): w dymku cała kolejność
const CHAIN_KINDS = ["tma", "cta", "ctr"];
const LEVELS = [15, 50, 100, 200, 300, 370];

// Legenda w zakładkach (zwinięta domyślnie razem z całą sekcją)
const LEGEND = [
  ["air", "Przestrzeń", () => [
    lg(`<span class="swatch sec"></span>`, "sektor ACC obsadzony (kolor stanowiska)"), lg(`<span class="swatch idle"></span>`, "sektor bez kontrolera (sam kontur)"),
    lg(`<span class="swatch own"></span>`, "TMA, CTA, CTR obsadzona (kolor APP, TWR albo ACC)"),
    lg(`<span class="swatch tma"></span>`, "TMA bez kontrolera (sam kontur)"), lg(`<span class="swatch cta"></span>`, "CTA bez kontrolera (sam kontur)"),
    lg(`<span class="swatch ctr"></span>`, "CTR bez kontrolera (sam kontur)"),
    lg(`<span class="swatch fis"></span>`, "FIS (wypełniony, gdy online)"),
    lg(`<span class="swatch atz"></span>`, "ATZ / TRA"), lg(`<span class="swatch nb"></span>`, "sektor FIR-u sąsiedniego"),
    lg(`<span class="swatch fir"></span>`, "granica FIR (VATSpy)"), lg(`<span class="lg-freq">133.475</span>`, "częstotliwość obsługującego", "w"),
    `<p class="lgx-note">Przestrzenie i ich granice pionowe z pliku .ese: na mapie tylko te, które obejmują wybrany poziom
      (dolna ≤ FL &lt; górna), albo wszystkie przy „wszystkie poziomy". Najedź na mapę: lista wszystkich widocznych przestrzeni w tym miejscu.
      Sektor ACC: pierwsze stanowisko online z kolejności przejmowania (<span class="own-src">…</span>) w warstwie LOW (do FL335), MID (do FL365)
      albo HIGH. TMA, CTA i CTR: najpierw APP / TWR z listy OWNER pliku .ese, potem ACC (TMA: top-down z tabeli om.plvacc.pl,
      reszta: lista OWNER .ese). W dymku cała kolejność z częstotliwościami: obsługujący na zielonym tle, zalogowani na zielono,
      niezalogowani szarzy. FIS i ATZ: lista OWNER z pliku .ese. Sąsiedzi: sektory i listy OWNER z plików .ese
      sąsiednich vACC (data/import/neighbours; sektory zależne od pasa w konfiguracji zachodniej), UMMV (Białoruś) z pliku EPWW.</p>`].join("")],
  ["traffic", "Ruch", () => [
    lg(plane("dep"), "odlot z lotniska EP**"), lg(plane("arr"), "przylot na lotnisko EP**"),
    lg(plane("trn"), "tranzyt / bez planu lotu"), lg(plane("sel"), "wybrany lot"),
    lg(`<span class="lg-line lg-flight"></span>`, "trasa wybranego samolotu"), lg(`<span class="lg-line lg-route"></span>`, "trasa wpisana ręcznie"),
    `<p class="lgx-note">Lot krajowy (EP → EP) jest odlotem na ziemi przy lotnisku startu, do 40 NM od niego i przy wznoszeniu,
      a przylotem przy zniżaniu i na drugiej połowie trasy (wznoszenie / zniżanie z porównania z poprzednim odczytem sprzed 30 s).</p>`].join("")],
  ["pts", "Punkty", () => [
    lg(`<i data-sym="aerodrome"></i>`, "lotnisko"), lg(`<i data-sym="vor"></i>`, "VOR / DME"), lg(`<i data-sym="ndb"></i>`, "NDB"),
    lg(`<i data-sym="vfr"></i>`, "punkt VFR"), lg(`<i data-sym="fix"></i>`, "punkt FIX"), lg(`<span class="swatch awy"></span>`, "droga lotnicza")].join("")],
  ["atc", "ATC", (ctx) => `${FACILITIES.map(([k, l, n]) => lg(`<span class="ab ab-${k.toLowerCase()}">${l}</span>`, n)).join("")}
    ${lg(`<span class="ab ab-ctr">CTR</span>`, "kontrola obszaru (FIR)")}
    ${lg(`<span class="atcb nb"><b class="ab-icao">ESGG</b><span class="ab ab-gnd">G</span></span>`, "lotnisko sąsiada z kontrolerem", "w")}
    <p class="lgx-note">Najedź na plakietkę, żeby zobaczyć, kto jest online (także kto wystawił ATIS, tekst linijka po linijce).
      Najedź na etykietę stanowiska z częstotliwością (sektor, TMA, sąsiad): dane kontrolera. Oficjalna mapa sektorów:
      <a href="${esc(ctx.config.links.sectors)}" target="_blank" rel="noopener">plvacc.pl/acc-sectors ↗</a></p>`],
  ["viff", "vIFF", () => [
    lg(`<span class="swatch viff"></span>`, "obszar TV z wykresu"), lg(`<span class="vs-chip on">AKTYWNY</span>`, "scenariusz / TV aktywny"),
    lg(`<span class="vf-bar"><i class="ok" style="width:60%"></i></span>`, "obciążenie &lt; 90 %"), lg(`<span class="vf-bar"><i class="near" style="width:95%"></i></span>`, "90–100 %"),
    lg(`<span class="vf-bar"><i class="over" style="width:100%"></i></span>`, "ponad przepustowość"), lg(`<span class="vf-bar"><i class="nolim" style="width:40%"></i></span>`, "bez limitu"),
    lg(`<span class="fs fs-fi">FI</span>`, "plan złożony"), lg(`<span class="fs fs-si">SI</span>`, "slot (CTOT)"), lg(`<span class="fs fs-su">SU</span>`, "zawieszony (FLS)"),
    lg(`<span class="fs fs-aa">AA</span>`, "aktywowany (AOBT)"), lg(`<span class="fs fs-ta">TA</span>`, "w powietrzu (ATOT)"),
    lg(`<span class="fd-ctot d15">+10</span><span class="fd-ctot d30">+20</span><span class="fd-ctot d45">+40</span><span class="fd-ctot d60">+50</span>`, "opóźnienie CTOT w minutach (jak w liście lotów NM)", "w"),
  ].join("")],
];

// --- geometria: prostokąt otaczający i punkt w wielokącie (GeoJSON Polygon / MultiPolygon, współrzędne lon/lat)
const bboxOf = (g) => {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  const polys = g.type === "MultiPolygon" ? g.coordinates : [g.coordinates];
  polys.forEach((p) => p[0].forEach(([x, y]) => { b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y); }));
  return b;
};
const inRing = (x, y, ring) => {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};
const inGeom = (x, y, g) => (g.type === "MultiPolygon" ? g.coordinates : [g.coordinates])
  .some((p) => inRing(x, y, p[0]) && !p.slice(1).some((hole) => inRing(x, y, hole)));
const distNm = (la1, lo1, la2, lo2) => {
  const r = Math.PI / 180, a = Math.sin((la2 - la1) * r / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin((lo2 - lo1) * r / 2) ** 2;
  return 3440.065 * 2 * Math.asin(Math.sqrt(a));
};
const range = (lo, hi) => `${altLabel(lo)}–${altLabel(hi)}`;

// Lot względem FIR EPWW (kolor ikony): dep = odlot z lotniska EP** (niebieski jak paski odlotów w AERODROME),
// arr = przylot na EP** (żółty jak paski przylotów), trn = tranzyt albo bez planu (biały).
// Lot krajowy EP → EP: odlot na ziemi przy lotnisku startu, do 40 NM od niego i przy wznoszeniu; przylot przy zniżaniu
// i na drugiej połowie trasy. vs = zmiana wysokości od poprzedniego odczytu (ok. 30 s).
const EP_AD = /^EP[A-Z]{2}$/;
function relation(p, adIdx) {
  const dep = EP_AD.test(p.departure || ""), arr = EP_AD.test(p.arrival || "");
  if (!dep || !arr) return dep ? "dep" : arr ? "arr" : "trn";
  if (p.departure === p.arrival) return "dep";  // krąg, lot lokalny
  const a = adIdx.get(p.departure), b = adIdx.get(p.arrival);
  const da = a ? distNm(p.lat, p.lon, a.lat, a.lon) : null, db = b ? distNm(p.lat, p.lon, b.lat, b.lon) : null;
  const half = da !== null && db !== null ? (da <= db ? "dep" : "arr") : "dep";
  if ((p.groundspeed || 0) < 50) return half;
  if (da !== null && da <= 40 && !(p.vs <= -300)) return "dep";
  if (p.vs >= 300) return "dep";
  if (p.vs <= -300) return "arr";
  return half;
}
const REL_TEXT = { dep: (p) => `odlot z ${p.departure}`, arr: (p) => `przylot na ${p.arrival}`, trn: () => "tranzyt" };

// Widok MAP: duża mapa z panelem bocznym
function mainMap(ctx) {
  const AIRST = store.get("map.air", {});  // przełączniki przestrzeni, poziom
  const airOn = (id, d) => (typeOf(AIRST[id]) === typeOf(d) ? AIRST[id] : d);
  const pane = h(`<div class="pane fill"><div class="mapwrap">
    <div class="mapside">
      <div class="ms-head"><b>MAPA</b><span class="hint">AIRAC ${esc(ctx.config.airac?.ident || "")}</span>
        <button class="btn ms-hide" title="Schowaj panel">«</button></div>
      <section class="ms-card" data-sec="traffic">
        <h4>Ruch VATSIM</h4>
        ${sw("traffic", "samoloty", true, PLANE_SVG)}
        <div class="ac-key" title="kolor samolotu: odlot z lotniska EP**, przylot na EP**, tranzyt"><span>${plane("dep")}odlot</span><span>${plane("arr")}przylot</span><span>${plane("trn")}tranzyt</span></div>
        ${sw("traffic-detail", "etykiety z FL, typem i GS")}
        ${sw("atc", "kontrolerzy online: plakietki lotnisk (u sąsiadów mniejsze)", true, `<span class="ab ab-twr">T</span>`)}
        <div class="atc-info hint"></div>
        <div class="traffic-info hint"></div>
      </section>
      <section class="ms-card as-card" data-sec="air">
        <h4>Przestrzeń</h4>
        <div class="as-lvl">
          <span class="flbox">FL<input type="number" class="field fl" value="${esc(airOn("fl", 300))}" min="0" max="660" step="5"></span>
          <label class="sw"><input type="checkbox" class="allfl" ${airOn("all", false) ? "checked" : ""}><span>wszystkie poziomy</span></label>
        </div>
        <div class="as-pre">${LEVELS.map((v) => `<button data-fl="${v}" title="FL${String(v).padStart(3, "0")}">${String(v).padStart(3, "0")}</button>`).join("")}</div>
        ${AIR.map((a) => `<div class="as-row">${sw("air-" + a.id, a.label, airOn(a.id, a.on), a.sym)}
          <button class="pk-btn" data-air="${a.id}" title="Wybierz sektory do pokazania">wszystkie ▾</button></div>
          <div class="pk" data-air="${a.id}" hidden></div>`).join("")}
        ${sw("online", "obsada wg kontrolerów online", airOn("online", true), `<span class="swatch on"></span>`)}
        ${sw("firs", "granice FIR-ów (VATSpy)", airOn("firs", true), `<span class="swatch fir"></span>`)}
        <div class="sector-info hint"></div>
      </section>
      <section class="ms-card" data-sec="route">
        <h4>Trasa</h4>
        <textarea class="field route" rows="3" placeholder="np. EPKK OKENO N871 POLON L980 VAMPU EPGD"></textarea>
        <div class="ms-row"><button class="btn primary show-route">Pokaż trasę</button><button class="btn clear-route">Wyczyść</button></div>
        <div class="route-info hint"></div>
      </section>
      <section class="ms-card vf-card" data-sec="viff">
        <h4>Przepustowość sektorów (vIFF)</h4>
        ${sw("viff", "ruch i przepustowość na godzinę naprzód", true, `<span class="swatch viff"></span>`)}
        <div class="vs-list"></div>
        <div class="vs-sub">Wszystkie sektory (traffic volumes)</div>
        <select class="field viff-tv"><option value="">wybierz sektor…</option></select>
        <div class="viff-info hint"></div>
      </section>
      <section class="ms-card" data-sec="points">
        <h4>Punkty i drogi</h4>
        ${sw("aeros", "lotniska", true, `<i data-sym="aerodrome"></i>`)}
        ${sw("navaids", "VOR / DME", false, `<i data-sym="vor"></i>`)}
        ${sw("ndbs", "NDB", false, `<i data-sym="ndb"></i>`)}
        ${sw("vfrs", "punkty VFR <small>(zoom ≥ 8)</small>", false, `<i data-sym="vfr"></i>`)}
        ${sw("fixes", "punkty FIX <small>(zoom ≥ 8)</small>", false, `<i data-sym="fix"></i>`)}
        ${sw("airways", "drogi lotnicze <small>(zoom ≥ 7)</small>", false, `<span class="swatch awy"></span>`)}
        ${sw("labels", "nazwy punktów", true)}
      </section>
      <section class="ms-card" data-sec="base">
        <h4>Podkład</h4>
        <div class="seg">${[["dark", "ciemny"], ["light", "jasny"], ["white", "biały"], ["osm", "OSM"]]
          .map(([v, l]) => `<button data-base="${v}" class="${v === "dark" ? "on" : ""}">${l}</button>`).join("")}</div>
        ${sw("openaip", "nakładka lotnicza OpenAIP")}
        <div class="hint openaip-note"></div>
      </section>
      <section class="ms-card lgx" data-sec="legend">
        <h4>Legenda</h4>
        <div class="lgx-tabs">${LEGEND.map(([k, l]) => `<button data-lg="${k}">${l}</button>`).join("")}</div>
        ${LEGEND.map(([k, , body]) => `<div class="lgx-body" data-lg="${k}">${body(ctx)}</div>`).join("")}
      </section>
    </div>
    <button class="btn ms-show" title="Pokaż panel">»</button>
    <div class="map"></div>
  </div></div>`);
  const $ = (s) => pane.querySelector(s);
  const mapEl = $(".map");
  const map = L.map(mapEl, { preferCanvas: true, zoomSnap: 0.5 }).setView([52.0, 19.3], 6, { animate: false });
  const key = ctx.config.carto_api_key;
  let baseName = lsGet("map.base") || "dark";  // wspólny z mapą sektoryzacji w RADIO
  let base = null;
  let theme = THEME.dark;
  const layers = {
    firs: L.layerGroup().addTo(map), air: L.layerGroup().addTo(map), airways: L.layerGroup(), airLbl: L.layerGroup().addTo(map),
    ads: L.layerGroup().addTo(map), points: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map), atc: L.layerGroup().addTo(map),
    traffic: L.layerGroup().addTo(map), flight: L.layerGroup().addTo(map), viff: L.layerGroup().addTo(map),
  };
  let openaip = null;
  const panes = atcPanes(map);
  const timers = [];
  // odświeżanie w tle tylko, gdy zakładka MAP jest widoczna (powrót do zakładki odświeża zaległe dane)
  const live = () => document.body.contains(pane) && pane.offsetParent !== null;
  const every = (ms, fn) => timers.push(setInterval(() => live() && fn(), ms));

  const paintLegend = () => pane.querySelectorAll("i[data-sym]").forEach((i) => {
    i.outerHTML = `<i data-sym="${i.dataset.sym}">${symbolSvg(i.dataset.sym, THEME.dark[KIND_COLOR[i.dataset.sym]])}</i>`;
  });
  paintLegend();
  loadVacs().then((v) => pane.querySelectorAll(".own-src").forEach((el) => { el.textContent = v?.source_label || v?.source || "?"; }))
    .catch(() => pane.querySelectorAll(".own-src").forEach((el) => { el.textContent = "niedostępna, lista OWNER z .ese"; }));

  // Sekcje panelu zwijane kliknięciem w nagłówek (stan w localStorage); legenda domyślnie zwinięta
  const secs = store.get("map.sections", {});
  pane.querySelectorAll(".ms-card[data-sec]").forEach((c) => {
    const k = c.dataset.sec;
    c.classList.toggle("shut", typeof secs[k] === "boolean" ? secs[k] : k === "legend");
    const h4 = c.querySelector("h4");
    h4.classList.add("ms-tog");
    h4.title = "Zwiń / rozwiń";
    h4.addEventListener("click", () => { secs[k] = c.classList.toggle("shut"); store.set("map.sections", secs); });
  });
  const legendTab = (k) => {
    k = LEGEND.some(([x]) => x === k) ? k : LEGEND[0][0];
    pane.querySelectorAll(".lgx [data-lg]").forEach((el) => el.classList.toggle("on", el.dataset.lg === k));
    store.set("map.legend.tab", k);
  };
  $(".lgx-tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-lg]"); if (b) legendTab(b.dataset.lg); });
  legendTab(store.get("map.legend.tab", "air"));

  const setBase = (name) => {
    baseName = BASEMAPS[name] ? name : "dark";
    lsSet("map.base", baseName);
    if (base) map.removeLayer(base);
    base = BASEMAPS[baseName](L, key).addTo(map);
    base.bringToBack?.();
    const light = LIGHT_BASEMAPS.includes(baseName);
    theme = light ? THEME.light : THEME.dark;
    mapEl.classList.toggle("light", light);
    mapEl.classList.toggle("white", baseName === "white");
    pane.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.base === baseName));
    drawAds(); loadVisible(); redrawFlight(); redrawRoute(); drawAirspace();
  };
  $(".seg").addEventListener("click", (e) => { const b = e.target.closest("button[data-base]"); if (b) setBase(b.dataset.base); });
  $(".openaip").addEventListener("change", (e) => {
    const k = ctx.config.openaip_api_key;
    if (!k) {
      e.target.checked = false;
      $(".openaip-note").textContent = "Brak klucza: załóż darmowe konto na openaip.net i wpisz POLIDS_OPENAIP_API_KEY w pliku .env";
      return;
    }
    if (e.target.checked) {
      openaip = L.tileLayer(`https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=${encodeURIComponent(k)}`,
        { attribution: "© openAIP", maxZoom: 14, opacity: 0.9 }).addTo(map);
    } else if (openaip) { map.removeLayer(openaip); }
  });
  $(".ms-hide").addEventListener("click", () => { pane.querySelector(".mapwrap").classList.add("collapsed"); setTimeout(() => map.invalidateSize(), 50); });
  $(".ms-show").addEventListener("click", () => { pane.querySelector(".mapwrap").classList.remove("collapsed"); setTimeout(() => map.invalidateSize(), 50); });

  // Punkt jako symbol EuroScope z nazwą obok
  const pointMarker = (p, { color, label = true, scale = 1.6, weight = 1.6, permanent = true, cls = "lbl", popup = true, tooltip = true } = {}) => {
    const sym = symbolFor(p.kind);
    const m = new SymbolMarker([p.lat, p.lon], { symbol: sym, color: color || theme[KIND_COLOR[sym]], scale, weight, radius: 7 * scale / 1.6 });
    const full = `${esc(p.ident)} ${esc(p.kind)}${p.frequency ? " " + esc(p.frequency) : ""}${p.name ? " " + esc(p.name) : ""}`;
    if (label) m.bindTooltip(esc(p.ident), { permanent, direction: "right", offset: [6 * scale / 1.6, 0], className: cls });
    else if (tooltip) m.bindTooltip(full);
    if (label && popup) m.bindPopup(full);
    return m;
  };

  // --- dymek lotniska: ICAO, nazwa, METAR, kontrolerzy online i ATIS (dane ładowane po najechaniu)
  let atcData = { airports: [] };
  const metarCache = {};
  const metarFor = (icao) => {
    const hit = metarCache[icao];
    if (hit && Date.now() - hit.t < 120000) return hit.p;
    const p = api(`/api/meteo/metar?ids=${icao}`).then((r) => r[0]?.raw || null).catch(() => null);
    metarCache[icao] = { t: Date.now(), p };
    return p;
  };
  const who = (c) => `${esc(c.name || "?")} <span class="muted">CID ${esc(c.cid ?? "?")}</span>`;
  const adCard = (icao, name, metar) => {
    const ap = atcData.airports.find((a) => a.icao === icao);
    const fac = ap?.facilities || {};
    // ATIS też w tabeli kontroli: kto go wystawił (imię i nazwisko, CID, od kiedy)
    const ctrls = FACILITIES.filter(([k]) => fac[k]).flatMap(([k, l]) => fac[k].map((c) => ({ ...c, k, l })));
    const atis = fac.ATIS || [];
    return `<div class="aeroc-h"><b>${esc(icao)}</b><span>${esc(name || "")}</span></div>
      <div class="aeroc-sec">METAR</div><div class="aeroc-metar">${metar === undefined ? '<span class="muted">ładowanie…</span>' : metar ? esc(metar) : '<span class="muted">brak</span>'}</div>
      <div class="aeroc-sec">Kontrola</div>${ctrls.length ? `<table>${ctrls.map((c) => `<tr><td><span class="ab ab-${c.k.toLowerCase()}">${c.l}</span></td>
        <td class="cs">${esc(c.callsign)}</td><td class="fq">${esc(c.frequency)}</td><td>${who(c)}</td><td class="muted">od ${hhmm(c.logon_time)}</td></tr>`).join("")}</table>`
        : '<div class="muted">brak zalogowanego kontrolera</div>'}
      ${atis.map((a) => `<div class="aeroc-sec">ATIS ${esc(a.atis_code || "")} <span class="muted">${esc(a.callsign)} ${esc(a.frequency)}</span></div>
        <div class="aeroc-who">wystawił: ${who(a)} · od ${hhmm(a.logon_time)}</div>
        <div class="aeroc-atis">${atisHtml(a.text_atis)}</div>`).join("")}`;
  };
  // Karta nad lotniskiem, a gdy nad nim się nie mieści (lotnisko przy górnej krawędzi mapy, długi ATIS linijka po linijce):
  // pod plakietką. W poziomie przesunięta w granice mapy (z lewej panel boczny), wtedy bez strzałki.
  // Po doładowaniu METAR-u karta rośnie, więc liczymy od nowa.
  const placeCard = (m) => {
    const tt = m.getTooltip(), el = tt?.getElement();
    if (!el || !m.isTooltipOpen()) return;
    const p = map.latLngToContainerPoint(m.getLatLng()), { x: W, y: H } = map.getSize(), w = el.offsetWidth, hh = el.offsetHeight;
    const down = p.y - hh - 16 < 0 && H - p.y > p.y;
    // z lewej nie pod przyciskami zoomu (+/−), które przykrywały nazwę lotniska w karcie (ESGG przy górnej krawędzi)
    const zc = map.zoomControl?.getContainer(), x0 = zc ? zc.offsetLeft + zc.offsetWidth + 4 : 4;
    const dx = Math.round(Math.max(x0 - (p.x - w / 2), Math.min(0, W - 4 - (p.x + w / 2))));
    const dir = down ? "bottom" : "top", off = [dx, down ? 30 : -8];
    el.classList.toggle("aeroc-shift", dx !== 0);
    if (tt.options.direction === dir && String(tt.options.offset) === String(off)) return;
    Object.assign(tt.options, { direction: dir, offset: off });
    tt.update();
  };
  // Dymek podpinamy do markera; METAR doładowuje się przy pierwszym najechaniu
  const bindAdCard = (m, icao, name) => {
    m.bindTooltip(() => adCard(icao, name, metarCache[icao]?.v), { direction: "top", offset: [0, -8], className: "atc-tip aero-tip", opacity: 1, pane: panes.tip });
    m.on("tooltipopen", async () => {
      hideTip();
      placeCard(m);
      const v = await metarFor(icao);
      if (metarCache[icao]) metarCache[icao].v = v;
      if (m.isTooltipOpen()) { m.setTooltipContent(adCard(icao, name, v)); placeCard(m); }
    });
    return m;
  };

  // --- lotniska (lotnisko z plakietką ATC nie dostaje osobnej nazwy: ICAO jest na plakietce)
  let ads = [];
  let adIdx = new Map();  // wszystkie lotniska wg ICAO (odlot / przylot samolotów)
  let atcAds = new Set();
  const drawAds = () => {
    layers.ads.clearLayers();
    const z = map.getZoom();
    ads.forEach((a) => {
      const big = a.kind === "large_airport" || a.kind === "medium_airport";
      if (!big && z < 8 && !atcAds.has(a.icao)) return;
      const scale = big ? 1.8 : 1.4;
      const m = pointMarker({ ident: a.icao, kind: "AD", lat: a.lat, lon: a.lon, name: a.name }, { label: false, tooltip: false, scale, popup: false })
        .on("click", () => ctx.open("aerodrome", a.icao)).addTo(layers.ads);
      bindAdCard(m, a.icao, a.city || a.name);
      // nazwa jako osobna stała etykieta (marker ma już dymek z kartą lotniska)
      if (!atcAds.has(a.icao) && (z >= 7 || (big && z >= 6))) {
        L.tooltip([a.lat, a.lon], { content: esc(a.icao), permanent: true, direction: "right", offset: [6 * scale / 1.6, 0], className: "lbl", interactive: false })
          .addTo(layers.ads);
      }
    });
  };
  api("/api/aerodromes").then((list) => {
    adIdx = new Map(list.map((a) => [a.icao, a]));
    ads = list.filter((a) => a.kind !== "small_airport" || a.icao.startsWith("EP"));
    loadAtc();
    drawTraffic();
    trafficInfo();
  }).catch(() => loadAtc());
  $(".aeros").addEventListener("change", (e) => (e.target.checked ? layers.ads.addTo(map) : map.removeLayer(layers.ads)));

  // --- Przestrzeń: wszystkie wycinki sektorów z pliku .ese (ACC EPWW, TMA, CTR, FIS/CTA/ATZ, sąsiedzi) z granicami pionowymi.
  // Na mapie tylko wycinki obejmujące wybrany poziom (albo wszystkie), z wybranych list; obsada wg kontrolerów online.
  // Granice pionowe z .ese: eAIP PANSA nie był osiągalny przy pisaniu tego kodu, więc nie został z nim uzgodniony.
  let slicesP = null, slices = null, sliceErr = null;
  const loadSlices = () => (slicesP ||= api("/api/nav/slices").then((gj) => {
    gj.features.forEach((f) => {
      const pr = f.properties;
      pr.pick = PICK_OF[pr.kind] || "oth";
      // klucz na liście wyboru: litera sektora ACC, nazwa TMA (TMA Poznań N i S osobno), ICAO lotniska (CTR), numer CTA,
      // rodzaj:grupa (FIS…), FIR/nazwa (sąsiedzi)
      pr.key = pr.kind === "nb" ? `${pr.fir}/${pr.name}` : pr.kind === "tma" ? pr.group_label
        : ["acc", "ctr", "cta"].includes(pr.kind) ? pr.group : `${pr.kind}:${pr.group}`;
      f.bb = bboxOf(f.geometry);
      f.gk = `${pr.fir}|${pr.name}|${JSON.stringify(f.geometry.coordinates)}`;  // ten sam kształt na kilku poziomach
    });
    return gj;
  }).catch((e) => { slicesP = null; throw e; }));
  const air = Object.fromEntries(AIR.map((a) => [a.id, airOn(a.id, a.on)]));
  // wybór na listach: null = wszystkie (także nowe po aktualizacji pliku .ese), inaczej zbiór kluczy
  const pickSaved = store.get("map.pick", {});
  const pick = Object.fromEntries(AIR.map((a) => [a.id, Array.isArray(pickSaved[a.id]) ? new Set(pickSaved[a.id]) : null]));
  // wybór TMA zapisany przed rundą 8b (klucz = ICAO lotniska): dopisz nazwy TMA tego lotniska (EPPO → TMA Poznań, N, S)
  const migratePick = () => {
    if (!slices?.features.length) return;  // bez wycinków (błąd sieci, brak .ese) nie ruszamy zapisanego wyboru
    const tma = slices.features.filter((f) => f.properties.pick === "tma").map((f) => f.properties);
    const known = new Set(tma.map((pr) => pr.key));
    [...(pick.tma || [])].filter((k) => !known.has(k)).forEach((k) => {
      tma.filter((pr) => pr.group === k).forEach((pr) => pick.tma.add(pr.key));
      pick.tma.delete(k);
    });
  };
  const saveAir = () => {
    store.set("map.air", { ...air, fl: parseInt($(".fl").value || "0", 10), all: $(".allfl").checked, online: $(".online").checked, firs: $(".firs").checked });
    store.set("map.pick", Object.fromEntries(AIR.map((a) => [a.id, pick[a.id] ? [...pick[a.id]] : null])));
  };
  const level = () => ($(".allfl").checked ? null : Math.max(0, Math.min(660, parseInt($(".fl").value || "0", 10))) * 100);
  const atLevel = (pr, lv) => lv === null || (pr.lower_ft <= lv && pr.upper_ft > lv);

  // stan sieci do rysowania (odświeżany co minutę)
  const net = { online: null, err: null, positions: [], posBy: {}, vacs: null, firs: null, firsAll: null, ctxo: null };
  // zalogowani spoza stanowisk .ese (np. EPWA_W_APP na innej częstotliwości): APP/DEP i TWR wg ICAO lotniska
  const unmatched = (o) => {
    const used = new Set(Object.values(o?.positions || {}).map((c) => c.callsign));
    const out = {};
    (o?.controllers || []).forEach((c) => {
      const parts = String(c.callsign || "").toUpperCase().split("_"), type = parts[parts.length - 1];
      if (used.has(c.callsign) || parts.length < 2 || (parts.length > 2 && /^I[A-Z]?$/.test(parts[1]))) return;  // Information = FIS
      const k = type === "APP" || type === "DEP" ? "APP" : type === "TWR" ? "TWR" : null;
      if (k) ((out[parts[0]] ||= {})[k] ||= []).push(c);
    });
    return out;
  };
  // stanowisko z kolejności przejmowania → zalogowany kontroler (klucze online to znaki z pliku .ese; ACC też przez ese_id)
  const onlOf = (cs, { byId, onl, vacs }) => onl[cs] || onl[byId[vacs?.acc_positions?.[cs]?.ese_id]?.callsign] || null;
  // częstotliwość stanowiska z kolejności: z pliku .ese, dla ACC z tabeli kolejności przejmowania
  const freqOf = (cs) => net.posBy[cs]?.frequency || net.vacs?.acc_positions?.[cs]?.frequency || "";

  // Właściciel jednego wycinka. Wynik: {c: obsługujący (w TMA / CTA / CTR tylko APP, TWR…: etykieta), top: ACC top-down,
  // key: znak do koloru, who: znak obsługującego z kolejności (kolor TMA / CTA / CTR), ext: obsługujący spoza stanowisk .ese,
  // ch: kolejność z chains.js (TMA, CTA, CTR)}
  const ownerOf = (pr, ctxo) => {
    const ch = CHAIN_KINDS.includes(pr.kind) ? airspaceChain(pr.name, pr.owner_callsigns, net.vacs) : null;
    if (!ctxo) return { c: null, top: null, key: null, who: ch?.chain[0] || null, ch };
    const { byId, onl, vacs, extra } = ctxo;
    if (ch) {
      // TMA, CTA i CTR: pierwsze zalogowane APP / TWR z kolejności (każde APP TMA Warszawa: WA APP, N, S, F…);
      // potem zalogowany spoza stanowisk .ese (np. EPWA_W_APP na innej częstotliwości); na końcu ACC top-down
      const on = (cs) => onlOf(cs, ctxo);
      const loc = ch.local.find(on);
      if (loc) return { c: on(loc), top: null, key: loc, who: loc, ch };
      const icao = (ch.local[0] || pr.name).slice(0, 4);
      const x = (pr.kind === "ctr" ? ["TWR", "APP"] : ["APP"]).map((t) => extra[icao]?.[t]?.[0]).find(Boolean);
      if (x) return { c: x, top: null, key: x.callsign, who: x.callsign, ext: true, ch };
      const acc = ch.acc.find(on);
      return { c: null, top: acc ? on(acc) : null, key: null, who: acc || null, ch };
    }
    // sąsiedzi: pierwsze zalogowane stanowisko z listy OWNER (znaki; z plików .ese sąsiadów albo z pliku EPWW)
    if (pr.kind === "nb") {
      const nb = (pr.owner_callsigns || []).find((x) => onl[x]);
      return { c: nb ? onl[nb] : null, top: null, key: nb || null };
    }
    const o = sliceOwner(pr, byId, onl, vacs);
    const cs = o.c?.callsign || "";
    if (pr.kind === "atz" && !pr.group.startsWith("EPTR") && o.c && !cs.startsWith(pr.group + "_")) return { c: null, top: o.c, key: null };
    return { c: o.c, top: null, key: o.key };
  };
  const styleOf = (pr, own, all) => {
    const k = pr.kind, t = theme, f = all ? 0.6 : 1;  // wszystkie poziomy: nakładające się wypełnienia słabiej
    if (k === "acc") {
      if (!net.useOnline) { const c = colorFor(pr.callsign || pr.name); return { color: c, weight: 1.1, fillColor: c, fillOpacity: 0.12 * f }; }
      if (!own.c) return { color: t.idle, weight: 1, opacity: 0.9, fill: false };
      const c = colorFor(own.key || own.c.callsign);
      return { color: c, weight: 1.3, fillColor: c, fillOpacity: 0.17 * f };
    }
    if (k === "nb") {
      if (!own.c) return { color: t.nb, weight: 1, opacity: 0.75, dashArray: "3 4", fill: false };
      const c = colorFor(own.key || own.c.callsign);
      return { color: c, weight: 1.2, fillColor: c, fillOpacity: 0.15 * f };
    }
    const col = t[k] || t.cta;
    const base = { tma: { weight: 1.4, opacity: 0.95 }, ctr: { weight: 1.4, opacity: 0.95, dashArray: "6 3" }, fis: { weight: 1, opacity: 0.8, dashArray: "2 4" },
      cta: { weight: 1.2, opacity: 0.9, dashArray: "8 4" }, atz: { weight: 1.1, opacity: 0.9 } }[k] || { weight: 1, opacity: 0.8 };
    // obsadzona TMA / CTA / CTR (APP / TWR albo ACC top-down, jak w RADIO): kontur w kolorze obsługującego stanowiska
    // (ACC: stały kolor z POSITION_COLORS, ten sam co sektora ACC obok), wypełnienie (own) w osobnej warstwie asFill
    if (own.who && (own.c || own.top) && CHAIN_KINDS.includes(k)) return { ...base, color: colorFor(own.who), weight: 1.8, opacity: 1, fill: false, own: true };
    const fillOpacity = { atz: 0.2 }[k] || 0.1;
    return { color: col, ...base, fillColor: col, fill: !!own.c && !CHAIN_KINDS.includes(k), fillOpacity: fillOpacity * f };
  };

  let vis = [];  // widoczne wycinki do dymka po najechaniu: {f, pr, own}
  // etykiety przestrzeni pod samolotami i plakietkami (własna warstwa poniżej markerPane), żeby nie zasłaniały znaków lotów
  map.createPane("asLbl").style.zIndex = 590;
  // Wypełnienia obsadzonych TMA / CTA / CTR: pełne kolory na własnej kanwie pod resztą przestrzeni (overlayPane = 400),
  // przezroczystość całej warstwy. Nakładające się wycinki (konfiguracje pasów EPWA_DIR*, wszystkie poziomy) się nie sumują.
  const fillPane = map.createPane("asFill");
  fillPane.style.zIndex = 399;
  fillPane.style.pointerEvents = "none";
  // shift: etykieta stanowiska (APP / TWR na TMA, CTA, sąsiad), rozsuwana przez declutter; prio: większy wycinek zostaje na środku.
  // c: kontroler online obsługujący przestrzeń: po najechaniu na etykietę jego dane zamiast listy przestrzeni (hoverAt)
  let lblCtl = [];
  const lbl = (at, html, shift = 0, c = null) => L.marker(at, { interactive: false, pane: "asLbl", shift,
    icon: L.divIcon({ className: "maplabel", iconSize: null, html: `<div${c ? ` class="ctl" data-ctl="${lblCtl.push(c) - 1}"` : ""}>${html}</div>` }) })
    .addTo(layers.airLbl);
  // Etykiety stanowisk nachodzące na wcześniejsze (np. WA APP i WA DIR na środku TMA Warszawa) przesuwamy w pionie
  // (w dół, w górę, coraz dalej); etykiety sektorów ACC zostają na miejscu. W pikselach, więc od nowa po zoomie.
  const declutter = () => {
    const boxes = [], ms = [];
    layers.airLbl.eachLayer((m) => ms.push(m));
    ms.sort((a, b) => !!a.options.shift - !!b.options.shift || (b.options.shift || 0) - (a.options.shift || 0)).forEach((m) => {
      const el = m.getElement()?.firstElementChild;
      if (!el) return;
      el.style.marginTop = "";
      const p = map.latLngToLayerPoint(m.getLatLng()), w = el.offsetWidth, hh = el.offsetHeight;
      if (!w) return;  // mapa ukryta (inna zakładka): przeliczy activate
      const hit = (dy) => boxes.some((b) => Math.abs(b.x - p.x) < (b.w + w) / 2 + 2 && Math.abs(b.y - p.y - dy) < (b.h + hh) / 2 + 2);
      let dy = 0;
      for (let i = 1; m.options.shift && hit(dy) && i <= 8; i++) dy = (i % 2 ? 1 : -1) * Math.ceil(i / 2) * (hh + 2);
      if (hit(dy)) dy = 0;
      if (dy) el.style.marginTop = `${dy}px`;
      boxes.push({ x: p.x, y: p.y + dy, w, h: hh });
    });
  };
  map.on("zoomend", declutter);
  const area = (bb) => (bb[2] - bb[0]) * (bb[3] - bb[1]);
  const center = (f) => L.latLngBounds([f.bb[1], f.bb[0]], [f.bb[3], f.bb[2]]).getCenter();
  // sąsiedzi: etykieta stanowiska na wycinku z największą częścią w pobliżu Polski (sektory ESMM sięgają daleko na północ)
  const NEAR = [13, 48.5, 25.5, 56];
  const nearArea = (bb) => Math.max(0, Math.min(bb[2], NEAR[2]) - Math.max(bb[0], NEAR[0])) * Math.max(0, Math.min(bb[3], NEAR[3]) - Math.max(bb[1], NEAR[1]));
  function drawAirspace() {
    layers.air.clearLayers();
    layers.airLbl.clearLayers();
    hideTip();
    vis = [];
    lblCtl = [];
    if (!slices) { renderInfo(level()); return; }
    const lv = level(), all = lv === null;
    net.useOnline = $(".online").checked && !!net.online;
    const onl = net.useOnline ? net.online.positions : null;
    const ctxo = onl ? { byId: Object.fromEntries(net.positions.map((p) => [p.position_id, p])), onl, vacs: net.vacs, extra: unmatched(net.online) } : null;
    net.ctxo = ctxo;
    const shown = slices.features.filter((f) => {
      const pr = f.properties;
      return air[pr.pick] && (!pick[pr.pick] || pick[pr.pick].has(pr.key)) && atLevel(pr, lv);
    });
    shown.forEach((f) => vis.push({ f, pr: f.properties, own: ownerOf(f.properties, ctxo) }));
    // rysowanie od spodu: FIS, ATZ, sąsiedzi, ACC, CTA, TMA, CTR; ten sam kształt na kilku poziomach tylko raz (obsadzony wygrywa)
    const order = { fis: 0, atz: 1, oth: 0, nb: 2, acc: 3, cta: 4, tma: 4, ctr: 5 };
    const drawn = new Map();
    // CTR w dwóch konfiguracjach pasów (EPKK_CTR07 / CTR25) rysujemy raz, żeby wypełnienie się nie dublowało
    [...vis].sort((a, b) => order[a.pr.kind] - order[b.pr.kind] || !!(a.own.c || a.own.top) - !!(b.own.c || b.own.top))
      .forEach((v) => drawn.set(v.pr.kind === "ctr" && /CTR\d+$/.test(v.pr.name) ? "ctr|" + v.pr.group : v.f.gk, v));
    fillPane.style.opacity = (all ? 0.68 : 1) * (theme === THEME.light ? 0.42 : 0.32);  // jasny podkład: mocniej
    drawn.forEach((v) => {
      const st = styleOf(v.pr, v.own, all);
      L.geoJSON(v.f, { interactive: false, style: st }).addTo(layers.air);
      if (st.own) L.geoJSON(v.f, { interactive: false, pane: "asFill", style: { stroke: false, fill: true, fillColor: st.color, fillOpacity: 1 } }).addTo(layers.air);
    });
    // etykiety: sektor ACC (nazwa + częstotliwość obsługującego), obsadzona TMA / CTA i sektor sąsiada (stanowisko + częstotliwość;
    // jedna na stanowisko APP: na największym wycinku TMA, a bez TMA na tym poziomie na CTA, np. WA APP na CTA 02 na FL230;
    // u sąsiada jedna na stanowisko w FIR-ze)
    const best = new Map(), rank = (v) => (v.pr.kind === "cta" ? 0 : 1);
    vis.forEach((v) => {
      const pr = v.pr, c = v.own.c;
      const k = pr.kind === "acc" ? "acc:" + pr.name : (pr.kind === "tma" || pr.kind === "cta") && c ? "app:" + c.callsign
        : pr.kind === "nb" && c ? `nb:${pr.group}:${c.callsign}` : null;
      const b = k && best.get(k);
      const size = (x) => (x.pr.kind === "nb" ? nearArea(x.f.bb) || area(x.f.bb) / 1e3 : area(x.f.bb));
      if (k && (!b || rank(v) > rank(b) || (rank(v) === rank(b) && size(v) > size(b)))) best.set(k, v);
    });
    best.forEach((v) => {
      const pr = v.pr, c = v.own.c;
      if (pr.kind === "acc") {
        const fq = net.useOnline ? c?.frequency : pr.frequency;
        lbl(center(v.f), `<small>${esc(pr.name)}</small>${fq ? `<br><span class="freq${c ? " on" : ""}">${esc(fq)}</span>` : ""}`, 0, c);
      } else {
        // sąsiad: punkt wewnątrz wielokąta (środek ramki wypada poza wklęsłe sektory)
        const at = (pr.kind === "nb" && (labelPoint(v.f, NEAR) || labelPoint(v.f))?.at) || center(v.f);
        lbl(at, `<small>${esc(displayName(c.callsign))}</small><br><span class="freq on">${esc(c.frequency)}</span>`, area(v.f.bb), c);
      }
    });
    declutter();
    renderInfo(lv);
    pane.querySelectorAll(".pk:not([hidden])").forEach((el) => renderPicker(el.dataset.air));
  }

  // --- dymek po najechaniu: wszystkie widoczne przestrzenie w tym miejscu (Leaflet podaje zdarzenia tylko górnej warstwie,
  // więc szukamy wycinków zawierających punkt kursora)
  // Własny element w kontenerze mapy zamiast L.tooltip: szeroki dymek Leafleta ("auto") wychodził poza mapę pod panel boczny.
  // Obok kursora (z prawej, a gdy się nie mieści, z lewej), a gdy na bok za szeroki: pod albo nad kursorem; zawsze w granicach mapy.
  const tipEl = L.DomUtil.create("div", "leaflet-tooltip atc-tip as-tip", mapEl);
  tipEl.hidden = true;
  let tipOn = false, raf = 0, lastEv = null;
  function hideTip() { if (tipOn) { tipEl.hidden = true; tipOn = false; } }
  const showTip = (pt, html) => {
    tipEl.innerHTML = html;
    tipEl.hidden = false;
    tipOn = true;
    const { x: W, y: H } = map.getSize(), w = tipEl.offsetWidth, th = tipEl.offsetHeight, g = 16, m = 4;
    let x = pt.x + g <= W - m - w ? pt.x + g : pt.x - g - w >= m ? pt.x - g - w : null, y;
    if (x !== null) y = Math.max(m, Math.min(pt.y - th / 2, H - m - th));
    else {
      x = Math.max(m, Math.min(pt.x - w / 2, W - m - w));
      y = pt.y + g + th <= H - m ? pt.y + g : Math.max(m, pt.y - g - th);
    }
    tipEl.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  };
  const whoHtml = (v) => {
    const { c, top, ch } = v.own, pr = v.pr;
    if (!net.useOnline) {
      const cs = ch?.chain[0] || pr.callsign;
      return cs ? `<span class="muted">${esc(displayName(cs))} ${esc((ch ? freqOf(cs) : "") || pr.frequency || "")} (podział pełny)</span>` : "";
    }
    // kto i od kiedy online (pełne dane kontrolera: najechanie na etykietę stanowiska albo plakietkę lotniska)
    const nm = (x) => `<span class="muted">${esc(x.name || "")}${x.logon_time ? ` · od ${hhmm(x.logon_time)}` : ""}</span>`;
    if (c) return `<span class="fq">${esc(c.frequency)}</span> <b>${esc(displayName(c.callsign))}</b> ${nm(c)}`;
    if (top) return `<span class="muted">top-down:</span> <span class="fq">${esc(top.frequency)}</span> <b>${esc(displayName(top.callsign))}</b> ${nm(top)}`;
    return `<span class="muted">brak kontrolera</span>`;
  };
  // TMA / CTA / CTR: wszystkie stanowiska w kolejności przejmowania (chains.js, jak w RADIO) z częstotliwością,
  // a zalogowane z nazwiskiem kontrolera. Obsługujący na zielonym tle, zalogowani na zielono, niezalogowani szarzy;
  // przed częścią ACC skąd jest jej kolejność (om = tabela top-down om.plvacc.pl, .ese = lista OWNER pliku sektorowego).
  const uniq = (xs) => xs.filter((x, i) => xs.indexOf(x) === i);
  const chainHtml = (r) => {
    const local = uniq(r.chs.flatMap((ch) => ch.local)), acc = uniq(r.chs.flatMap((ch) => ch.acc)).filter((cs) => !local.includes(cs));
    // wiersz z kilku wycinków może mieć oba źródła (np. EPWA_DIR* bez prefiksu w tabeli om): "om (TMA Warszawa) / .ese"
    const tma = r.chs.find((ch) => ch.tma)?.tma;
    const src = uniq(r.chs.map((ch) => ch.src)).sort().reverse().map((x) => (x === "om" ? `om${tma ? ` (${tma})` : ""}` : ".ese")).join(" / ");
    const who = r.own.who;
    const chip = (cs, c) => {
      const st = cs === who ? " own" : !net.useOnline ? "" : c ? " on" : " off";
      return `<span class="asc${st}"><b>${esc(displayName(cs))}</b> ${esc(c?.frequency || freqOf(cs))}${c ? ` <i>${esc(c.name || "")}</i>` : ""}</span>`;
    };
    const on = (cs) => (net.useOnline && net.ctxo ? onlOf(cs, net.ctxo) : null);
    return `<div class="as-ch">${local.map((cs) => chip(cs, on(cs))).join("")}${r.own.ext ? chip(who, r.own.c) : ""}${acc.length
      ? `<span class="asc-src">ACC wg ${esc(src)}:</span>${acc.map((cs) => chip(cs, on(cs))).join("")}` : ""}</div>`;
  };
  const hoverHtml = (hits, lv) => {
    // TMA / CTA / CTR: wycinki tej samej przestrzeni z tym samym obsługującym (np. konfiguracje pasów EPWA_DIR11_A, DIR15_A…)
    // w jednym wierszu z łącznym zakresem pionowym; reszta: sąsiednie pasma tego samego wycinka z tym samym obsługującym.
    // Sortowanie także wg obsługującego: wycinki innego stanowiska (np. EPWA_APP_S_D między EPWA_DIR11_D i DIR11_A) nie rozbijają wiersza.
    const rows = [], nk = (pr) => (CHAIN_KINDS.includes(pr.kind) ? `${pr.kind}:${pr.key}` : pr.kind + ":" + pr.name);
    hits.map((v) => ({ ...v, w: whoHtml(v) }))
      .sort((a, b) => KIND_ORDER.indexOf(a.pr.kind) - KIND_ORDER.indexOf(b.pr.kind) || nk(a.pr).localeCompare(nk(b.pr)) || a.w.localeCompare(b.w)
        || a.pr.lower_ft - b.pr.lower_ft)
      .forEach((v) => {
        const w = v.w, last = rows[rows.length - 1];
        if (last && nk(last.pr) === nk(v.pr) && last.w === w && v.pr.lower_ft <= last.hi) {
          last.hi = Math.max(last.hi, v.pr.upper_ft);
          if (!last.names.includes(v.pr.name)) last.names.push(v.pr.name);
          if (v.own.ch) last.chs.push(v.own.ch);
          return;
        }
        rows.push({ pr: v.pr, own: v.own, w, lo: v.pr.lower_ft, hi: v.pr.upper_ft, names: [v.pr.name], chs: v.own.ch ? [v.own.ch] : [] });
      });
    // jak przekrój pionowy: od najwyższej przestrzeni do najniższej
    rows.sort((a, b) => b.hi - a.hi || b.lo - a.lo || KIND_ORDER.indexOf(a.pr.kind) - KIND_ORDER.indexOf(b.pr.kind));
    const names = (r) => esc(r.names.length > 2 ? `${r.names[0]} +${r.names.length - 1}` : r.names.join(", "));
    const nm = (r) => (r.pr.kind === "nb" ? `<b>${esc(r.pr.label)}</b>` : `<b>${esc(r.pr.group_label)}</b> <span class="muted">${names(r)}</span>`);
    return `<div class="atc-tip-h"><b>${lv === null ? "wszystkie poziomy" : fl(lv)}</b> ${plural(rows.length, "przestrzeń", "przestrzenie", "przestrzeni")} w tym miejscu</div>
      <table>${rows.map((r) => `<tr><td><span class="as-k as-${r.pr.kind}">${KIND_TAG[r.pr.kind]}</span></td><td>${nm(r)}</td>
        <td class="lv">${range(r.lo, r.hi)}</td><td>${r.w}</td></tr>${r.chs.length ? `<tr class="as-chr"><td></td><td colspan="3">${chainHtml(r)}</td></tr>` : ""}`).join("")}</table>`;
  };
  // Dymek etykiety stanowiska: kontroler (znak, częstotliwość, imię i nazwisko, CID, rating, od kiedy, opis) i ATIS
  // lotniska z prefiksu znaku (ESGG_GND → ATIS ESGG), każda linijka ATIS osobno
  const ctlCard = (c) => {
    const icao = String(c.callsign || "").split("_")[0];
    const ap = atcData.airports.find((a) => a.icao === icao);
    const atis = (ap?.facilities?.ATIS || []).filter((a) => a.callsign !== c.callsign);
    return atcTooltip(displayName(c.callsign), ap ? ads.find((a) => a.icao === icao)?.city || ap.name : "",
      controllerRows(c, { info: true }) + atcRows(atis, "ATIS", "A"));
  };
  const hoverAt = () => {
    raf = 0;
    const e = lastEv;
    // etykieta stanowiska online (np. ESGG GND 121.705 u sąsiada, sektor ACC, APP na TMA): dane kontrolera
    const lab = e && !e.buttons && e.target.closest?.(".maplabel .ctl");
    if (lab && lblCtl[+lab.dataset.ctl]) { showTip(map.mouseEventToContainerPoint(e), ctlCard(lblCtl[+lab.dataset.ctl])); return; }
    // nad plakietką, samolotem, dymkiem, panelem albo symbolem lotniska (kanwa z kursorem "interactive") dymka nie pokazujemy
    if (!e || !vis.length || e.buttons || e.target.closest?.(".leaflet-marker-icon, .leaflet-tooltip, .leaflet-control, .leaflet-popup")
      || mapEl.querySelector("canvas.leaflet-interactive")) { hideTip(); return; }
    const ll = map.mouseEventToLatLng(e), x = ll.lng, y = ll.lat;
    const hits = vis.filter((v) => x >= v.f.bb[0] && x <= v.f.bb[2] && y >= v.f.bb[1] && y <= v.f.bb[3] && inGeom(x, y, v.f.geometry));
    if (!hits.length) { hideTip(); return; }
    showTip(map.mouseEventToContainerPoint(e), hoverHtml(hits, level()));
  };
  mapEl.addEventListener("mousemove", (e) => { lastEv = e; if (!raf) raf = requestAnimationFrame(hoverAt); });
  mapEl.addEventListener("mouseleave", () => { lastEv = null; hideTip(); });
  map.on("movestart zoomstart", hideTip);

  // --- listy wyboru sektorów (zamiast wszystkich ACC / TMA / CTR / sąsiadów)
  const itemsOf = (id) => {
    const m = new Map(), lv = level();
    // sąsiedzi: kto obsadza wycinek (pierwszy zalogowany z listy OWNER, jak kolor na mapie)
    const onl = id === "nb" && $(".online").checked ? net.online?.positions : null;
    (slices?.features || []).filter((f) => f.properties.pick === id).forEach((f) => {
      const pr = f.properties;
      let it = m.get(pr.key);
      if (!it) {
        it = { key: pr.key, lo: pr.lower_ft, hi: pr.upper_ft, here: false, on: new Set(), who: "",
          head: id === "nb" ? pr.group_label : id === "oth" ? OTH_HEAD[pr.kind] : "",
          label: id === "nb" ? pr.label : pr.group_label, sub: ["tma", "ctr"].includes(id) ? pr.group : "" };
        m.set(pr.key, it);
      }
      it.lo = Math.min(it.lo, pr.lower_ft);
      it.hi = Math.max(it.hi, pr.upper_ft);
      it.here ||= atLevel(pr, lv);
      const cs = onl && (pr.owner_callsigns || []).find((x) => onl[x]);
      // who: imię i nazwisko kontrolera, tylko do wyszukiwania na liście
      if (cs) { it.on.add(displayName(onl[cs].callsign)); it.who += ` ${onl[cs].name || ""}`; }
    });
    return [...m.values()].sort((a, b) => a.head.localeCompare(b.head, "pl") || a.label.localeCompare(b.label, "pl", { numeric: true }));
  };
  const pickBtn = (id) => {
    const items = itemsOf(id), n = items.length, sel = pick[id];
    const b = pane.querySelector(`.pk-btn[data-air="${id}"]`);
    const k = sel ? items.filter((it) => sel.has(it.key)).length : n;
    b.textContent = `${!sel ? "wszystkie" : !k ? "żaden" : `${k} z ${n}`} ▾`;
    b.classList.toggle("sub", !!sel);
  };
  // Lista sąsiednich FIR-ów domyślnie zwinięta: same nazwy FIR-ów (wybór, kto online), kliknięcie rozwija sektory FIR-u.
  // Rozwinięte FIR-y pamiętamy tylko do końca sesji przeglądarki (sessionStorage); przy wyszukiwaniu pasujące są rozwinięte.
  const FOLD = ["nb"];
  const folds = (() => {
    try { const f = JSON.parse(sessionStorage.getItem("map.pk.open")); return f && typeof f === "object" && !Array.isArray(f) ? f : {}; } catch { return {}; }
  })();
  const isOpen = (id, head) => Array.isArray(folds[id]) && folds[id].includes(head);
  const toggleFold = (id, head) => {
    const s = new Set(Array.isArray(folds[id]) ? folds[id] : []);
    if (!s.delete(head)) s.add(head);
    folds[id] = [...s];
    try { sessionStorage.setItem("map.pk.open", JSON.stringify(folds)); } catch { /* tryb prywatny */ }
  };
  function renderPicker(id) {
    const el = pane.querySelector(`.pk[data-air="${id}"]`);
    const items = itemsOf(id), sel = pick[id], q = (el.querySelector(".pk-q")?.value || "").trim().toLowerCase();
    const has = (k) => !sel || sel.has(k);
    const lv = level();
    const groups = new Map();
    items.forEach((it) => { if (!groups.has(it.head)) groups.set(it.head, []); groups.get(it.head).push(it); });
    const scroll = el.querySelector(".pk-list")?.scrollTop || 0;
    const fold = FOLD.includes(id);
    el.innerHTML = `<div class="pk-bar">${items.length > 12 ? `<input class="field pk-q" placeholder="szukaj…" value="${esc(q)}">` : ""}
        <button data-pk="all">wszystkie</button><button data-pk="none">żaden</button></div>
      <div class="pk-list">${!items.length ? `<div class="pk-none">${sliceErr ? esc(sliceErr) : "brak sektorów w pliku .ese"}</div>` : [...groups].map(([head, its]) => {
        const vis2 = its.filter((it) => !q || `${it.label} ${it.sub} ${it.key} ${head} ${[...it.on].join(" ")}${it.who}`.toLowerCase().includes(q));
        if (!vis2.length) return "";
        const n = its.filter((it) => has(it.key)).length;
        const box = `<input type="checkbox" data-g="${esc(head)}" ${n === its.length ? "checked" : ""} ${n && n < its.length ? 'data-mixed="1"' : ""}>`;
        const open = !fold || !!q || isOpen(id, head);
        const on = [...new Set(its.flatMap((it) => [...it.on]))];
        const row = !head ? "" : !fold ? `<label class="pk-g">${box}<b>${esc(head)}</b><small>${n}/${its.length}</small></label>`
          : `<div class="pk-g fold${open ? " open" : ""}" data-fold="${esc(head)}" title="${q ? "" : open ? "Zwiń sektory FIR-u" : "Rozwiń sektory FIR-u"}">
            <span class="pk-car">${open ? "▾" : "▸"}</span>${box}<b>${esc(head)}</b>${on.length
              ? `<span class="pk-on" title="online: ${esc(on.join(", "))}">● ${on.length}</span>` : ""}<small>${n}/${its.length}</small></div>`;
        return `${row}${!open ? "" : vis2.map((it) => `<label class="pk-i${it.here ? "" : " off"}"${it.here ? "" : ` title="nie ma go na ${lv === null ? "" : fl(lv)}"`}>
            <input type="checkbox" data-k="${esc(it.key)}" ${has(it.key) ? "checked" : ""}><span>${esc(it.label)}${it.sub ? ` <i>${esc(it.sub)}</i>` : ""}${it.on.size
              ? ` <i class="pk-who">${esc([...it.on].join(", "))}</i>` : ""}</span>
            <small>${range(it.lo, it.hi)}</small></label>`).join("")}`;
      }).join("")}</div>`;
    el.querySelectorAll("input[data-mixed]").forEach((i) => { i.indeterminate = true; });
    el.querySelector(".pk-list").scrollTop = scroll;
    pickBtn(id);
  }
  const setPick = (id, keys) => {
    const known = new Set(itemsOf(id).map((it) => it.key));
    if (keys) keys = new Set([...keys].filter((k) => known.has(k)));
    pick[id] = keys === null || keys.size >= known.size ? null : keys;
    saveAir();
    drawAirspace();
    renderPicker(id);
  };
  pane.querySelectorAll(".pk").forEach((el) => {
    const id = el.dataset.air;
    el.addEventListener("change", (e) => {
      const i = e.target;
      if (i.classList.contains("pk-q")) return;
      const items = itemsOf(id), keys = new Set(pick[id] || items.map((it) => it.key));
      if (i.dataset.k) { if (i.checked) keys.add(i.dataset.k); else keys.delete(i.dataset.k); }
      if (i.dataset.g !== undefined) items.filter((it) => it.head === i.dataset.g).forEach((it) => (i.checked ? keys.add(it.key) : keys.delete(it.key)));
      setPick(id, keys);
    });
    el.addEventListener("input", (e) => {
      if (!e.target.classList.contains("pk-q")) return;
      renderPicker(id);
      const q = el.querySelector(".pk-q");
      q.focus();
      q.setSelectionRange(q.value.length, q.value.length);
    });
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-pk]");
      if (b) setPick(id, b.dataset.pk === "all" ? null : new Set());
      // nagłówek FIR-u (poza polem wyboru): rozwiń / zwiń jego sektory
      const g = e.target.closest(".pk-g[data-fold]");
      if (g && !e.target.matches("input") && !(el.querySelector(".pk-q")?.value || "").trim()) { toggleFold(id, g.dataset.fold); renderPicker(id); }
    });
  });
  pane.querySelectorAll(".pk-btn").forEach((b) => b.addEventListener("click", () => {
    const el = pane.querySelector(`.pk[data-air="${b.dataset.air}"]`);
    const open = el.hidden;
    pane.querySelectorAll(".pk").forEach((x) => { x.hidden = true; });
    pane.querySelectorAll(".pk-btn").forEach((x) => x.classList.remove("open"));
    el.hidden = !open;
    b.classList.toggle("open", open);
    if (open) renderPicker(b.dataset.air);
  }));

  // TMA / CTA / CTR włączone, ale na wybranym poziomie żadnej: podpowiedź, na których poziomach z przycisków są
  // (nie rysujemy ich poza poziomem). Uwzględnia wybór na listach.
  const offLevel = (lv, cnt) => {
    if (lv === null) return "";
    const ids = ["tma", "cta", "ctr"].filter((id) => air[id] && !cnt[id]);
    const fs = slices.features.map((f) => f.properties).filter((pr) => ids.includes(pr.pick) && (!pick[pr.pick] || pick[pr.pick].has(pr.key)));
    if (!fs.length) return "";
    const at = LEVELS.filter((v) => fs.some((pr) => atLevel(pr, v * 100))).map((v) => String(v).padStart(3, "0"));
    const where = fs.every((pr) => pr.upper_ft <= lv) ? "leżą niżej" : fs.every((pr) => pr.lower_ft > lv) ? "leżą wyżej" : "są na innych poziomach";
    const tags = ids.filter((id) => fs.some((pr) => pr.pick === id)).map((id) => AIR_TAG[id]).join("/");
    return `${tags} ${where}: wybierz ${at.length ? (at.length > 1 ? `${at[0]}–${at[at.length - 1]}` : at[0]) + " albo " : ""}„wszystkie poziomy”`;
  };
  // opis pod przełącznikami: źródło kolejności przejmowania, warstwa, liczba wycinków, kto jest online
  function renderInfo(lv) {
    const el = $(".sector-info");
    if (sliceErr) { el.innerHTML = `<span class="error">Sektory z pliku .ese: ${esc(sliceErr)}</span>`; return; }
    if (!slices) { el.textContent = "ładowanie sektorów…"; return; }
    if (!slices.features.length) { el.textContent = slices.note || "brak sektorów"; return; }
    const cnt = {};
    vis.forEach((v) => { cnt[v.pr.pick] = (cnt[v.pr.pick] || 0) + 1; });
    const lay = lv === null ? null : vacsLayer(lv);
    const src = net.vacs?.source_label || net.vacs?.source;
    const parts = [];
    if (air.acc) {
      parts.push(src ? `ACC: kolejność przejmowania wg ${esc(src)}, ${lay ? `warstwa <b>${lay}</b> (${esc(net.vacs.layers?.[lay]?.label || "")})` : "warstwa wg dolnej granicy wycinka"}.`
        : "ACC: kolejność przejmowania niedostępna, lista OWNER z pliku .ese.");
    }
    parts.push(`${lv === null ? "Wszystkie poziomy" : "Na " + fl(lv)}: ${AIR.filter((a) => air[a.id]).map((a) => `${AIR_TAG[a.id]} ${cnt[a.id] || 0}`).join(" · ") || "żadna warstwa"}.`);
    const off = offLevel(lv, cnt);
    if (off) parts.push(`<span class="as-note">${off}</span>`);
    if (net.err) parts.push(`<span class="error">${esc(net.err)}. Rysuję sam podział sektorów bez obsady.</span>`);
    else if (net.online && $(".online").checked) {
      // także zalogowani spoza stanowisk .ese (np. EPWA_W_APP na innej częstotliwości), bo też obsadzają TMA / CTR
      const ep = [...Object.values(net.online.positions || {}), ...(net.online.controllers || [])].map((c) => c.callsign)
        .filter((c) => c && c.startsWith("EP") && !/_(ATIS|DEL|GND)$/.test(c));
      parts.push(`Online: ${ep.length ? [...new Set(ep)].map((c) => esc(displayName(c))).join(", ") : "brak kontrolerów EP"}`);
    }
    el.innerHTML = parts.join("<br>");
  }

  // --- dane: wycinki .ese (raz), FIR-y z VATSpy, kto jest online, stanowiska i kolejność przejmowania (co minutę)
  const loadSectors = async () => {
    const [gj, firs, onl, positions, vacs] = await Promise.all([
      loadSlices().then((x) => { sliceErr = null; return x; }).catch((e) => { sliceErr = e.message; return null; }),
      $(".firs").checked ? loadFirs().catch(() => null) : null,
      vatsimOnline().then((x) => { net.err = null; return x; }).catch((e) => { net.err = e.message; return null; }),
      atcPositions().catch(() => []),
      loadVacs().catch(() => null),
    ]);
    slices = gj;
    migratePick();
    Object.assign(net, { online: onl, positions, vacs, posBy: Object.fromEntries(positions.map((p) => [p.callsign, p])) });
    layers.firs.clearLayers();
    // podświetlenie obsady FIR-ów (VATSpy) tylko w FIR EPWW; FIR-y sąsiednie jako same kontury (ich sektory: warstwa sąsiadów)
    const epwwOnline = Object.fromEntries(Object.entries(onl?.firs || {}).filter(([id]) => id.startsWith("EPWW")));
    if (firs && $(".firs").checked) drawFirs(layers.firs, firs, epwwOnline, { panes });
    drawAirspace();
    AIR.forEach((a) => pickBtn(a.id));
  };
  // przełączniki i poziom
  const syncFl = () => {
    const all = $(".allfl").checked;
    $(".fl").disabled = all;
    pane.querySelectorAll(".as-pre button").forEach((b) => b.classList.toggle("on", !all && +b.dataset.fl === parseInt($(".fl").value || "0", 10)));
  };
  let flT = 0;
  const onLevel = () => { clearTimeout(flT); flT = setTimeout(() => { syncFl(); saveAir(); drawAirspace(); }, 200); };
  $(".fl").addEventListener("input", onLevel);
  $(".fl").addEventListener("change", onLevel);
  $(".allfl").addEventListener("change", onLevel);
  $(".as-pre").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-fl]");
    if (!b) return;
    $(".fl").value = b.dataset.fl;
    $(".allfl").checked = false;
    onLevel();
  });
  AIR.forEach((a) => $(".air-" + a.id).addEventListener("change", (e) => { air[a.id] = e.target.checked; saveAir(); drawAirspace(); }));
  $(".online").addEventListener("change", () => { saveAir(); drawAirspace(); });
  $(".firs").addEventListener("change", () => { saveAir(); loadSectors(); });
  syncFl();
  every(60000, loadSectors);
  // nazwy stanowisk jak w RADIO (etykiety VACS): po wczytaniu przerysuj etykiety i dymki
  loadDisplayNames().then((ok) => ok && drawAirspace());

  // --- vIFF: ruch vs przepustowość sektorów (traffic volumes) na godzinę naprzód.
  // W panelu najpierw scenariusze (aktywne na górze) z ich TV, pod nimi lista wszystkich TV; wykres wybranego TV
  // z listą lotów w lewym dolnym rogu mapy, obrys TV na mapie.
  let viffData = null;
  let viffSel = store.get("map.viff.sel", null);
  let pilots = [], pilotIdx = new Map();
  let chartSel = null;  // lot, do którego tabela wykresu była już przewinięta
  const chart = L.control({ position: "bottomleft" });
  chart.onAdd = () => {
    const el = L.DomUtil.create("div", "viff-chart");
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
    el.addEventListener("click", (e) => {
      if (e.target.closest(".vf-close")) { selectTv(""); return; }
      const r = e.target.closest("tr[data-cs]");
      if (r) showFlight(r.dataset.cs);
    });
    return el;
  };
  chart.addTo(map);
  const chartEl = chart.getContainer();
  let tmaP = null;
  const tmaLines = () => (tmaP ||= api("/api/nav/airspace?kind=tma").catch(() => { tmaP = null; return null; }));
  // obrys: litera sektora EPWW (EPWW-BH → sektory EPWWB na wszystkich poziomach) albo TMA lotniska (EPWA-TMA)
  const drawTvArea = async (tv) => {
    layers.viff.clearLayers();
    if (!tv) return;
    const [secs, tma] = await Promise.all([loadSlices().catch(() => null), tmaLines()]);
    const feats = [];
    tv.volumes.forEach((v) => {
      const [area, part = ""] = v.split("-");
      if (area === "EPWW" && /^[A-Z]/.test(part)) {
        feats.push(...(secs?.features || []).filter((f) => f.properties.kind === "acc" && f.properties.group === part[0]));
      } else if (/TMA/.test(part)) {
        // kontur TMA z .sct i wycinki TMA lotniska z .ese (TMA Warszawa to EPWA_APP_* i EPWA_DIR*)
        feats.push(...(tma?.features || []).filter((f) => f.properties.name.startsWith(area + " ") && !f.properties.inner));
        feats.push(...(secs?.features || []).filter((f) => f.properties.kind === "tma" && f.properties.group === area));
      }
    });
    if (viffSel !== tv.id) return;
    L.geoJSON({ type: "FeatureCollection", features: feats }, { interactive: false,
      style: { color: "#ffd400", weight: 2.6, opacity: 0.95, fillColor: "#ffd400", fillOpacity: 0.025 } }).addTo(layers.viff);
  };
  // warunki aktywacji scenariusza (vIFF sam decyduje, czy jest aktywny; pokazujemy tylko warunki)
  const scnCond = (s) => {
    const b = (xs, sep) => xs.map((x) => `<b>${esc(x)}</b>`).join(sep);
    const out = [];
    if (s.positions.length) out.push(`gdy online: ${b(s.positions, " + ")}`);
    if (s.anypositions.length) out.push(`gdy online: ${b(s.anypositions, " lub ")}`);
    if (s.nopositions.length) out.push(`i offline: ${b(s.nopositions, ", ")}`);
    if (s.booked) out.push("liczą się też rezerwacje");
    if (s.start && s.end && !(s.start === "0000" && s.end === "2359")) out.push(`godz. <b>${esc(s.start)}–${esc(s.end)}</b>`);
    s.times.forEach((t) => out.push(`${t.date ? esc(t.date) + " " : ""}godz. <b>${esc(t.from)}–${esc(t.to)}</b>`));
    return out.join(" · ") || "bez warunków";
  };
  const scnHtml = (s, byId) => `<div class="vs-scn ${s.active ? "on" : ""}"${s.description ? ` title="${esc(s.description)}"` : ""}>
      <div class="vs-top"><b>${esc(s.id)}</b>${s.active ? `<span class="vs-chip on">AKTYWNY</span>` : ""}<span class="vs-n">${s.tvs.length} TV</span></div>
      <div class="vs-cond">${scnCond(s)}</div>
      ${s.tvs.map((t) => listRow(byId[t.id], viffSel, t.id)).join("")}</div>`;
  const renderChart = () => {
    const tv = (viffData?.sectors || []).find((t) => t.id === viffSel);
    const show = !!tv && $(".viff").checked;
    const keep = chartEl.querySelector(".vf-tblwrap")?.scrollTop || 0;
    chartEl.style.display = show ? "" : "none";
    chartEl.innerHTML = show ? chartHtml(tv, viffData.now, (cs) => pilotIdx.get(cs), selected) : "";
    const w = chartEl.querySelector(".vf-tblwrap");
    if (w) w.scrollTop = keep;
    // nowo wybrany lot spoza widocznej części tabeli: przewiń do jego wiersza
    const r = selected !== chartSel && w?.querySelector("tr.on");
    if (r && (r.offsetTop < w.scrollTop + 20 || r.offsetTop + r.offsetHeight > w.scrollTop + w.clientHeight)) w.scrollTop = r.offsetTop - w.clientHeight / 2;
    if (show) chartSel = selected;
    fitFd();
    return show ? tv : null;
  };
  const drawViff = () => {
    const list = viffData?.sectors || [], scns = viffData?.scenarios || [];
    const byId = Object.fromEntries(list.map((t) => [t.id, t]));
    const act = scns.filter((s) => s.active), off = scns.filter((s) => !s.active);
    $(".vs-list").innerHTML = !viffData || !$(".viff").checked ? "" : !scns.length ? `<div class="vs-none">vIFF nie podał scenariuszy</div>`
      : `<div class="vs-sub">Scenariusze <span>aktywne: ${act.length} z ${scns.length}</span></div>
      ${act.map((s) => scnHtml(s, byId)).join("") || `<div class="vs-none">żaden scenariusz nie jest teraz aktywny</div>`}
      ${off.length ? `<details class="vs-more" ${store.get("map.viff.off", false) ? "open" : ""}><summary>Nieaktywne scenariusze (${off.length})</summary>
        ${off.map((s) => scnHtml(s, byId)).join("")}</details>` : ""}`;
    // wszystkie TV: aktywne (status 1 albo w aktywnym scenariuszu) i nieaktywne
    const groups = { aktywne: [], nieaktywne: [] };
    list.forEach((t) => groups[tvGroup(t)].push(t));
    $(".viff-tv").innerHTML = `<option value="">wybierz sektor…</option>` + Object.entries(groups).filter(([, ts]) => ts.length).map(([g, ts]) =>
      `<optgroup label="${g} (${ts.length})">${ts.map((t) => `<option value="${esc(t.id)}" ${t.id === viffSel ? "selected" : ""}>${esc(t.id)} · ${esc(tvLabel(t.id))}</option>`).join("")}</optgroup>`).join("");
    drawTvArea(renderChart());
  };
  const selectTv = (id) => {
    viffSel = id;
    store.set("map.viff.sel", id);
    drawViff();
  };
  const loadViff = async () => {
    $(".vf-card").classList.toggle("off", !$(".viff").checked);  // wyłączony vIFF: bez scenariuszy i listy TV
    if (!$(".viff").checked) { chartEl.style.display = "none"; layers.viff.clearLayers(); $(".vs-list").innerHTML = ""; $(".viff-info").textContent = ""; fitFd(); return; }
    try {
      viffData = await api("/api/viff/sectors");
      // pierwszy raz: pierwszy TV aktywnego scenariusza, a bez niego najbardziej obciążony
      if (viffSel === null && viffData.sectors.length) {
        viffSel = viffData.scenarios?.find((x) => x.active)?.tvs[0]?.id || [...viffData.sectors].sort((a, b) => loadRatio(b) - loadRatio(a))[0].id;
      }
      $(".viff-info").textContent = `${viffData.sectors.length} TV · ${plural(viffData.scenarios?.length || 0, "scenariusz", "scenariusze", "scenariuszy")} · stan ${viffData.now.slice(0, 2)}:${viffData.now.slice(2)}Z`;
    } catch (e) {
      viffData = null;
      $(".viff-info").innerHTML = `<span class="error">${esc(e.message)}</span>`;
    }
    drawViff();
  };
  $(".viff").addEventListener("change", loadViff);
  $(".viff-tv").addEventListener("change", (e) => selectTv(e.target.value));
  $(".vs-list").addEventListener("click", (e) => { const b = e.target.closest("[data-tv]"); if (b) selectTv(b.dataset.tv); });
  $(".vs-list").addEventListener("toggle", (e) => { if (e.target.matches?.(".vs-more")) store.set("map.viff.off", e.target.open); }, true);
  every(60000, () => $(".viff").checked && loadViff());

  // --- kontrolerzy online: plakietki lotnisk (D/G/T/A/APP) jak w VATSIM Radar; CTR rysuje drawFirs
  // Plakietki: lotniska w FIR EPWW ze wszystkimi stanowiskami; u sąsiadów mniejsza, szara plakietka (np. "ESGG G"),
  // gdy jest tam kontroler (DEL / GND / TWR / APP): sam ATIS bez kontrolera pomijamy, żeby nie zaśmiecać mapy.
  // Sąsiedzi tylko w obszarze wokół Polski (ten sam co samoloty), a nie z całego świata.
  const isEp = (icao) => icao.startsWith("EP");
  const nearPl = (ap) => ap.lat >= 44 && ap.lat <= 60 && ap.lon >= 5 && ap.lon <= 35;
  const badgeFacilities = (ap) => (isEp(ap.icao) || (nearPl(ap) && FACILITIES.some(([k]) => k !== "ATIS" && ap.facilities[k])) ? ap.facilities : null);
  const loadAtc = async () => {
    layers.atc.clearLayers();
    try {
      atcData = await vatsimAtc();
    } catch (e) {
      atcData = { airports: [] }; atcAds = new Set();
      $(".atc-info").innerHTML = `<span class="error">${esc(e.message)}</span>`;
      drawAds();
      return;
    }
    const shown = $(".atc").checked ? atcData.airports.map((ap) => ({ ...ap, facilities: badgeFacilities(ap) })).filter((ap) => ap.facilities) : [];
    const known = new Set(ads.map((a) => a.icao));
    atcAds = new Set(shown.map((a) => a.icao));
    shown.forEach((ap) => {
      // lotniska spoza listy (zagraniczne) dostają sam symbol pod plakietką
      if (!known.has(ap.icao)) pointMarker({ ident: ap.icao, kind: "AD", lat: ap.lat, lon: ap.lon, name: ap.name }, { label: false, tooltip: false, scale: 1.6 }).addTo(layers.atc);
      const m = airportBadge(ap, panes, { cls: isEp(ap.icao) ? "" : "nb" }).addTo(layers.atc);
      m.unbindTooltip();
      bindAdCard(m, ap.icao, ads.find((a) => a.icao === ap.icao)?.city || ap.name);
      if (isEp(ap.icao)) m.on("click", () => ctx.open("aerodrome", ap.icao));
    });
    $(".atc-info").textContent = $(".atc").checked ? `${plural(shown.length, "lotnisko", "lotniska", "lotnisk")} z kontrolerem · ${hhmm(new Date().toISOString())}` : "";
    drawAds();
  };
  $(".atc").addEventListener("change", loadAtc);
  every(30000, loadAtc);

  // --- trasa: gruba linia z obwódką (czytelna na każdym podkładzie), punkty jako symbole z nazwą w ramce
  const drawRoute = (layer, pts, color) => {
    const ll = pts.map((p) => [p.lat, p.lon]);
    L.polyline(ll, { color: theme.halo, weight: 9, opacity: 0.85, lineJoin: "round", interactive: false }).addTo(layer);
    const line = L.polyline(ll, { color, weight: 4, opacity: 1, lineJoin: "round" }).addTo(layer);
    pts.forEach((p, i) => {
      const first = i === 0 || i === pts.length - 1;
      pointMarker(p, { color, scale: first ? 2.2 : 1.9, weight: 2.4, cls: "rtlbl" }).addTo(layer);
    });
    return line;
  };
  let route = null;
  const redrawRoute = () => {
    layers.route.clearLayers();
    if (route) drawRoute(layers.route, route.points, theme.route);
  };

  // --- samoloty z VATSIM; kliknięcie otwiera szczegóły lotu (jak "Flight details" w NM UI) w prawym górnym rogu mapy:
  // VATSIM (pozycja, plan, trasa na mapie) + vIFF (czasy, slot, regulacja, CDM, wejścia w sektory, historia)
  let selected = null, flight = null;
  const redrawFlight = () => {
    layers.flight.clearLayers();
    if (!flight) return;
    if (flight.points.length) drawRoute(layers.flight, flight.points, theme.flight);
  };
  const fd = { cs: null, vat: undefined, viff: undefined, vatErr: null, viffErr: null };
  const fdOpen = store.get("map.fd.open", {});
  const fdCtl = L.control({ position: "topright" });
  fdCtl.onAdd = () => {
    const el = L.DomUtil.create("div", "fd-panel");
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
    el.addEventListener("click", (e) => {
      if (e.target.closest(".fd-close")) { closeFlight(); return; }
      const r = e.target.closest("tr[data-tv]");  // wejście w sektor → wykres tego TV
      if (!r) return;
      selectTv(r.dataset.tv);
      if (!$(".viff").checked) { $(".viff").checked = true; loadViff(); }
    });
    el.addEventListener("toggle", (e) => {
      if (!e.target.matches?.("details[data-x]")) return;
      fdOpen[e.target.dataset.x] = e.target.open;
      store.set("map.fd.open", fdOpen);
    }, true);
    return el;
  };
  fdCtl.addTo(map);
  const fdEl = fdCtl.getContainer();
  fdEl.style.display = "none";
  const chartH = () => (chartEl.style.display === "none" ? 0 : chartEl.offsetHeight);
  // na wąskiej mapie panel lotu (prawy górny róg) kończy się nad wykresem TV (lewy dolny róg), żeby się nie nakładały
  const fitFd = () => {
    const { x: w, y: hgt } = map.getSize(), ch = chartH();
    const clash = ch && w < chartEl.offsetWidth + 420 + 40;
    fdEl.style.maxHeight = Math.max(200, hgt - 20 - (clash ? ch + 34 : 0)) + "px";
  };
  map.on("resize", fitFd);
  const t4 = (v) => (v ? esc(v) : "–");
  const kv = (k, v, tip = "") => `<span class="k"${tip ? ` title="${esc(tip)}"` : ""}>${k}</span><span class="v">${v}</span>`;
  const alt = (a) => (a === null || a === undefined ? "–" : a >= 6000 ? fl(a) : `${a} ft`);
  const histTime = (t) => {
    const [d, hm] = (t || "").split(" ");
    return !hm ? esc(t) : d === new Date().toISOString().slice(0, 10) ? esc(hm) : `${esc(d.slice(8, 10))}.${esc(d.slice(5, 7))} ${esc(hm)}`;
  };
  const renderFd = () => {
    const { cs, vat: p, viff: v } = fd;
    if (!cs) { fdEl.style.display = "none"; fdEl.innerHTML = ""; return; }
    const keep = fdEl.dataset.cs === cs ? fdEl.querySelector(".fd-body")?.scrollTop || 0 : 0;
    fdEl.dataset.cs = cs;
    fdEl.style.display = "";
    fitFd();
    const adep = v?.departure || p?.departure, ades = v?.arrival || p?.arrival, type = v?.aircraft || p?.aircraft;
    const head = `<div class="fd-bar"><span>Szczegóły lotu</span><button class="fd-close" title="Zamknij">✕</button></div>
      <div class="fd-head"><div class="fd-l1"><b class="fd-cs">${esc(cs)}</b>${v ? stateChip(v, v.states) : ""}${v?.landed ? ` <span class="fs fs-fi">wylądował</span>` : ""}</div>
        <div class="fd-od"><b>${esc(adep || "????")}</b><span class="ar">→</span><b>${esc(ades || "????")}</b>${type ? `<span class="fd-tag">${esc(type)}</span>` : ""}${p?.rules ? `<span class="fd-tag">${p.rules === "V" ? "VFR" : "IFR"}</span>` : ""}</div>
        ${p ? `<div class="fd-who">${esc(p.name || "")} · CID ${esc(p.cid)}</div>` : v?.cid ? `<div class="fd-who">CID ${esc(v.cid)}</div>` : ""}</div>`;
    const vat = p === undefined ? `<div class="fd-note">Ładowanie danych VATSIM…</div>`
      : p ? `<div class="fd-grid g4">${kv("Poziom", alt(p.altitude))}${kv("GS", `${esc(p.groundspeed ?? "–")} kt`)}${kv("SQ", t4(p.squawk))}${kv("HDG", p.heading ?? "–")}
        ${kv("RFL", t4(p.rfl ? (p.rfl >= 6000 ? fl(p.rfl) : p.rfl) : ""), "z planu lotu VATSIM")}</div>`
      : `<div class="fd-note">${esc(fd.vatErr)}</div>`;
    let vf;
    if (v === undefined) vf = `<div class="fd-note">Ładowanie danych vIFF…</div>`;
    else if (!v) vf = `<div class="fd-note">${esc(fd.viffErr)}</div>`;
    else {
      const dtip = [v.delay !== null && v.delay !== undefined && `opóźnienie ATFM ${v.delay} min (CTOT − EOBT − kołowanie)`, v.ctot_reason && `powód: ${v.ctot_reason}`,
        v.revised_ctot && `ostatni zmieniony CTOT ${v.revised_ctot}`].filter(Boolean).join(" · ");
      const ctot = v.ctot ? `<span class="fd-ctot ${delayClass(v.delay)}">${esc(v.ctot)}${v.delay ? ` ${v.delay > 0 ? "+" : ""}${v.delay}` : ""}</span>` : "–";
      const eet = v.enroute_min !== null && v.enroute_min !== undefined ? `${Math.floor(v.enroute_min / 60)}:${String(v.enroute_min % 60).padStart(2, "0")}` : "–";
      vf = `<div class="fd-grid g4">
          ${kv("EOBT", t4(v.eobt))}${kv("TOBT", t4(v.tobt))}${kv("TSAT", t4(v.tsat))}${kv("CTOT", ctot, dtip || "bez slotu")}
          ${kv("AOBT", t4(v.aobt))}${kv("ATOT", t4(v.atot), "rzeczywisty start")}${kv("TTOT", t4(v.ttot), "planowany start (A-CDM)")}${kv("ETA", t4(v.eta))}
          ${kv("RFL", t4(v.rfl))}${kv("TAS", v.tas ? v.tas + " kt" : "–")}${kv("EET", eet, "czas lotu z planu")}${kv("Na czas", t4(v.on_time), "pole onTime z vIFF (odchyłka od planu)")}
        </div>
        <div class="fd-grid g2">
          ${kv("Regulacja", v.regulation ? `<b class="fd-reg">${esc(v.regulation)}</b>` : "brak")}${kv("Najbardziej karząca", t4(v.airspace), "most penalising airspace")}
          ${kv("Przestrzeń teraz", t4(v.actual_airspace))}${kv("Status CDM", esc([v.cdm_status, v.is_cdm && "A-CDM"].filter(Boolean).join(" · ") || "–"), "status CDM vIFF · A-CDM = lotnisko odlotu z A-CDM")}
          ${kv("Status ATFCM", t4(v.atfcm_status))}${kv("Pas / SID", t4(v.dep_info))}
        </div>`;
    }
    const route = v?.route || p?.route;
    const rt = `<div class="fd-sec">Trasa (pole 15)${!v?.route && p?.route ? " · plan VATSIM" : ""}</div>
      <div class="fd-route">${esc(route || "brak trasy")}</div>
      ${flight?.points?.length ? `<div class="fd-meta">na mapie: ${plural(flight.points.length, "punkt", "punkty", "punktów")} · ${flight.distance_nm} NM${p ? " (trasa z planu VATSIM)" : " (trasa z vIFF)"}</div>` : ""}
      ${flight?.warnings?.length ? `<div class="fd-warn">${flight.warnings.map(esc).join("<br>")}</div>` : ""}`;
    const secs = v?.sectors || [], hist = v?.history || [];
    const sec = !v ? "" : `<details class="fd-x" data-x="sectors" ${fdOpen.sectors ? "open" : ""}><summary>Wejścia w sektory (vIFF) <span>${secs.length}</span></summary>
      ${secs.length ? `<table class="fd-tbl"><thead><tr><th rowspan="2">TV</th><th rowspan="2">Opis</th><th colspan="2">Planowane</th><th colspan="2">Wyliczone</th></tr>
        <tr><th>wej.</th><th>wyj.</th><th>wej.</th><th>wyj.</th></tr></thead><tbody>
        ${secs.map((x) => `<tr data-tv="${esc(x.tv)}" class="${esc(x.state)}" title="${x.state === "now" ? "lot jest teraz w tym sektorze · " : ""}kliknij: wykres sektora">
          <td class="cs">${esc(x.tv)}</td><td class="d">${esc(x.label)}</td><td class="t">${t4(x.planned_entry)}</td><td class="t">${t4(x.planned_exit)}</td>
          <td class="t">${t4(x.entry)}</td><td class="t">${t4(x.exit)}</td></tr>`).join("")}</tbody></table>`
        : `<div class="fd-note">${v.sectors_error ? "vIFF: " + esc(v.sectors_error) : "Brak wejść w sektory EP w danych na dziś."}</div>`}</details>
      <details class="fd-x" data-x="history" ${fdOpen.history ? "open" : ""}><summary>Historia vIFF <span>${hist.length}</span></summary>
      ${hist.length ? `<ol class="fd-hist">${hist.map((x) => `<li><span class="t" title="${esc(x.time)}">${histTime(x.time)}</span><span>${esc(x.text)}</span></li>`).join("")}</ol>`
        : `<div class="fd-note">brak wpisów</div>`}</details>`;
    fdEl.innerHTML = `${head}<div class="fd-body"><div class="fd-sec">Pozycja (VATSIM)</div>${vat}<div class="fd-sec">Lot (vIFF)</div>${vf}${rt}${sec}</div>`;
    fdEl.querySelector(".fd-body").scrollTop = keep;
  };
  const fitFlight = (f) => {
    // z prawej miejsce na panel szczegółów lotu, z dołu na wykres TV
    if (map.getSize().x < 40) return;  // ukryta mapa: Leaflet policzyłby zoom NaN
    const pad = { paddingTopLeft: [50, 50], paddingBottomRight: [50 + (fdEl.offsetWidth || 0), 50 + chartH()], maxZoom: 9 };
    if (f.points.length) map.fitBounds(L.latLngBounds(f.points.map((x) => [x.lat, x.lon])), pad);
    else if (f.lat !== null && f.lat !== undefined) map.setView([f.lat, f.lon], Math.max(map.getZoom(), 8), { animate: false });
  };
  const showFlight = async (cs) => {
    cs = String(cs).trim().toUpperCase();
    selected = cs;
    flight = null;
    layers.flight.clearLayers();
    Object.assign(fd, { cs, vat: undefined, viff: undefined, vatErr: null, viffErr: null });
    renderFd();
    drawTraffic();
    if (viffData) renderChart();  // podświetlenie lotu w tabeli wykresu
    const vatP = api(`/api/vatsim/pilots/${encodeURIComponent(cs)}/route`).then((f) => {
      if (selected !== cs) return;
      fd.vat = flight = f;
      redrawFlight();
      fitFlight(f);
      renderFd();
    }).catch((e) => {
      if (selected !== cs) return;
      fd.vat = null;
      fd.vatErr = /nie jest online/.test(e.message) ? "Nie ma go teraz w sieci VATSIM." : e.message;
      renderFd();
    });
    const viffP = (/^[A-Z0-9]{2,10}$/.test(cs) ? api(`/api/viff/flight/${encodeURIComponent(cs)}`) : Promise.reject(new Error("vIFF: znak wywoławczy spoza formatu ICAO")))
      .then((v) => { if (selected === cs) { fd.viff = v; renderFd(); } })
      .catch((e) => { if (selected === cs) { fd.viff = null; fd.viffErr = e.message; renderFd(); } });
    await Promise.all([vatP, viffP]);
    // pilota nie ma w sieci, ale vIFF zna trasę: rozpisujemy ją na punkty i rysujemy
    if (selected !== cs || fd.vat || !fd.viff?.route) return;
    try {
      const r = await api(`/api/nav/route?route=${encodeURIComponent([fd.viff.departure, fd.viff.route, fd.viff.arrival].filter(Boolean).join(" "))}`);
      if (selected !== cs || !r.points.length) return;
      flight = r;
      redrawFlight();
      fitFlight(r);
      renderFd();
    } catch { /* trasa nierozpoznana */ }
  };
  const refreshFd = () => {
    const cs = fd.cs;
    if (!cs || !fd.viff) return;
    api(`/api/viff/flight/${encodeURIComponent(cs)}`).then((v) => { if (fd.cs === cs) { fd.viff = v; renderFd(); } })
      .catch(() => { /* zostają poprzednie dane */ });
  };
  every(60000, refreshFd);
  const closeFlight = () => {
    selected = null; flight = null; fd.cs = null;
    layers.flight.clearLayers();
    renderFd();
    drawTraffic();
    if (viffData) renderChart();
  };
  let trafficAt = "";
  // liczba samolotów wg relacji (po wczytaniu listy lotnisk liczona od nowa: loty krajowe potrzebują ich współrzędnych)
  const trafficInfo = () => {
    if (!trafficAt) return;
    const n = { dep: 0, arr: 0, trn: 0 };
    pilots.forEach((p) => { n[relation(p, adIdx)]++; });
    $(".traffic-info").textContent = `${plural(pilots.length, "samolot", "samoloty", "samolotów")} w Europie Środkowej (odloty EP ${n.dep}, przyloty EP ${n.arr}, `
      + `tranzyt ${n.trn}) · ${trafficAt}. Kliknij samolot: szczegóły lotu i trasa.`;
  };
  const drawTraffic = () => {
    layers.traffic.clearLayers();
    if (!$(".traffic").checked || map.getSize().x < 40) return;
    const z = map.getZoom(), b = map.getBounds().pad(0.2);
    pilots.filter((p) => b.contains([p.lat, p.lon])).forEach((p) => {
      const rel = relation(p, adIdx);
      const m = aircraftMarker(p, { label: z >= 6, detail: $(".traffic-detail").checked || z >= 9, cls: rel })
        .bindTooltip(`${esc(p.callsign)} ${esc(p.aircraft || "")} ${esc(p.departure || "")}→${esc(p.arrival || "")} ${fl(p.altitude)} · ${esc(REL_TEXT[rel](p))}`)
        .on("click", () => showFlight(p.callsign)).addTo(layers.traffic);
      if (p.callsign === selected) m.getElement()?.classList.add("sel");
    });
  };
  const loadTraffic = async () => {
    if (!$(".traffic").checked) { layers.traffic.clearLayers(); $(".traffic-info").textContent = ""; return; }
    const prev = new Map(pilots.map((p) => [p.callsign, p.altitude]));  // poprzedni odczyt: wznoszenie / zniżanie
    try {
      pilots = await api("/api/vatsim/pilots?bbox=44,5,60,35");
      pilots.forEach((p) => { const a = prev.get(p.callsign); p.vs = a === undefined || a === null || p.altitude === null ? null : p.altitude - a; });
      trafficAt = hhmm(new Date().toISOString());
      trafficInfo();
    } catch (e) { pilots = []; $(".traffic-info").innerHTML = `<span class="error">${esc(e.message)}</span>`; }
    pilotIdx = new Map(pilots.map((p) => [p.callsign, p]));
    const q = fd.vat && pilotIdx.get(fd.cs);  // otwarty panel lotu: świeża pozycja z VATSIM
    if (q) { ["lat", "lon", "altitude", "groundspeed", "heading", "squawk"].forEach((k) => { fd.vat[k] = q[k]; }); renderFd(); }
    drawTraffic();
    if (viffData) renderChart();  // ADEP/ADES/typ w tabeli lotów wykresu
  };
  $(".traffic").addEventListener("change", loadTraffic);
  $(".traffic-detail").addEventListener("change", drawTraffic);
  map.on("moveend", drawTraffic);
  every(30000, () => $(".traffic").checked && loadTraffic());

  // --- punkty i drogi w widocznym obszarze
  const bbox = () => { const b = map.getBounds(); return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()].map((x) => x.toFixed(3)).join(","); };
  let seq = 0;
  const loadVisible = async () => {
    const z = map.getZoom(), my = ++seq;
    const kinds = [];
    if ($(".navaids").checked && z >= 5) kinds.push("VOR", "VOR-DME", "DME", "VORTAC");
    if ($(".ndbs").checked && z >= 5) kinds.push("NDB", "NDB-DME");
    if ($(".vfrs").checked && z >= 8) kinds.push("VFR");
    if ($(".fixes").checked && z >= 8) kinds.push("FIX");
    let pts = [], segs = [];
    try { if (kinds.length) pts = await api(`/api/nav/points?bbox=${bbox()}&kinds=${kinds.join(",")}`); } catch { /* zbyt duży obszar */ }
    try { if ($(".airways").checked && z >= 7) segs = await api(`/api/nav/airways?bbox=${bbox()}`); } catch { /* zbyt duży obszar */ }
    if (my !== seq) return;
    layers.points.clearLayers();
    layers.airways.clearLayers();
    if ($(".airways").checked) layers.airways.addTo(map); else map.removeLayer(layers.airways);
    segs.forEach((s) => L.polyline(s.coords, { color: theme.airway, weight: 1 }).bindTooltip(esc(s.airway)).addTo(layers.airways));
    const labels = $(".labels").checked;
    pts.forEach((p) => {
      const nav = p.kind !== "FIX" && p.kind !== "VFR";
      const vor = symbolFor(p.kind) === "vor";
      pointMarker(p, { label: labels && (vor ? z >= 6 : nav ? z >= 7 : z >= 9), scale: nav ? 1.7 : 1.4 }).addTo(layers.points);
    });
  };
  map.on("moveend", loadVisible);
  map.on("zoomend", drawAds);
  ["navaids", "ndbs", "vfrs", "fixes", "airways", "labels"].forEach((c) => $("." + c).addEventListener("change", loadVisible));

  // --- trasa wpisana ręcznie
  $(".show-route").addEventListener("click", async () => {
    const info = $(".route-info");
    try {
      const r = await api(`/api/nav/route?route=${encodeURIComponent($(".route").value)}`);
      if (!r.points.length) { info.innerHTML = `<span class="error">Nie rozpoznano żadnego punktu.</span>`; return; }
      route = r;
      redrawRoute();
      if (map.getSize().x >= 40) map.fitBounds(L.latLngBounds(r.points.map((p) => [p.lat, p.lon])), { padding: [40, 40] });
      info.innerHTML = `${r.points.length} punktów · ${r.distance_nm} NM<br><span class="mono">${r.points.map((p) => esc(p.ident)).join(" ")}</span>` +
        (r.warnings.length ? `<div class="error">${r.warnings.map(esc).join("<br>")}</div>` : "");
    } catch (e) { info.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
  });
  $(".clear-route").addEventListener("click", () => { route = null; layers.route.clearLayers(); $(".route-info").textContent = ""; });

  setBase(baseName);
  loadSectors();
  loadTraffic();
  loadViff();
  let shown = Date.now();
  return {
    el: pane,
    activate: (arg) => setTimeout(() => {
      map.invalidateSize();
      declutter();
      loadVisible();
      drawTraffic();
      // po dłuższym pobycie w innej zakładce (w tle mapa się nie odświeża)
      if (Date.now() - shown > 30000) { loadSectors(); loadTraffic(); loadAtc(); if ($(".viff").checked) loadViff(); }
      shown = Date.now();
      if (arg && arg !== selected) showFlight(decodeURIComponent(arg));
    }, 50),
    destroy: () => { timers.forEach(clearInterval); map.remove(); },
  };
}

// MAP: jedna mapa (sektoryzacja warstw LOW/MID/HIGH przeniesiona do RADIO). #map/ZNAK otwiera szczegóły lotu.
export default {
  mount(root, ctx) {
    const main = mainMap(ctx);
    root.append(main.el);
    return { activate: (arg) => main.activate(arg), destroy: () => main.destroy() };
  },
};
