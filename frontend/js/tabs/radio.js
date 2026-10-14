// RADIO: jedna strona jak w PANDORZE (runda 10, Marek): u góry pasek ATIS, po lewej duży podgląd LOW z sektoryzacją
// FIR EPWW i stosami sąsiadów (ESAA, EDWW, EDUU, EDMM, LKAA), w środku przyciski warstw i mały podgląd MID, po prawej
// duży podgląd HIGH ze stosami UMKK, EYVL, UMMV, UKLV, LZBB. Sektory ACC na podglądach (runda 12, Marek): wycinki
// obsługiwane przez to samo stanowisko (kolejność przejmowania, ownership.json) jako jeden obszar z jedną etykietą
// (stanowisko i częstotliwość), granica tylko tam, gdzie zmienia się obsługujący; sektorów bez nikogo nie rysujemy
// (ramą zostaje granica FIR EPWW). APP, TWR, ATIS i FIS bez nikogo: samo ID (runda 11, Marek: częstotliwość dopiero,
// gdy ktoś obsługuje). Stosy sąsiadów
// (runda 11): sektory graniczące z EPWW jak w VACS (bez FMP), częstotliwość tylko, gdy ktoś je obsługuje; nikt w całym
// stosie → jedna linia ADVISORY 122.800.
// Runda 11 (Marek): warstwa APP w częściach TMA wg stanowiska APP (WA APP, WA N APP, WA S APP) i z TMA proceduralnymi
// (SC/LB/SY TWR); etykiety APP i TWR obok kształtów TMA/CTR z linią do punktu; LOW z suwakiem wysokości (FL095–FL330).
// Dane: GET /api/radio/board co 30 s (geometria raz, potem tylko po zmianie wersji danych `ver`, np. po imporcie nowego
// pliku .ese). Kliknięcie nagłówka stosu sąsiada (np. EDWW BREMEN): panel z wyciągiem z LOA EPWW z tym FIR-em
// (GET /api/radio/loa) i odnośnikiem do PDF-u w OTHER › DOCS (kategoria LOA).
import { api, esc, h, lsGet, lsSet, navigate, remember } from "../api.js";
import { labelPoint } from "../coverage.js";
import { dissolve, pointIn } from "../dissolve.js";
import { displayName, loadDisplayNames, posName } from "../posname.js";
import { ADVISORY_FQ, DEFAULT_CUT, DEFAULT_LAYERS, EPWW_BBOX, LAYERS, LAYER_IDS, TOP_FT, argOf, bboxOf, bboxOrDefault, byShort,
  controllersOn, coverGrid, cutAt, cutBand, cutBounds, cutRange, flNum, flText, labelOpts, parseArg, pathAll, pathOf, placeLabels, projection,
  ringsOf, sliceAt, stackBands, utcHhmm } from "../radioboard.js";

const LS_LAYERS = "radio.layers", LS_CUT = "radio.cut";
const REFRESH_MS = 30000;
const LEVEL_FT = { MID: 35000, HIGH: 40000 };  // MID i HIGH mają jeden kształt sektorów
const MAPS = [
  { key: "LOW", layer: "low", cls: "rb-low", side: "left" },
  { key: "MID", layer: "mid", cls: "rb-midmap", side: null },
  { key: "HIGH", layer: "high", cls: "rb-high", side: "right" },
];
// miejsce stosu sąsiada w panelu: strona mapy z /api/radio/board (at) → obszar siatki panelu
const AREA = { n: "top", s: "bot", nw: "col", w: "col", sw: "col", ne: "col", e: "col", se: "col" };
const COL_ORDER = { nw: 0, w: 1, sw: 2, ne: 0, e: 1, se: 2 };
// etykiety warstw APP/TWR/FIS: krótkie ID bez rodzaju (kolor paska z lewej mówi, co to jest): WA S APP → WA S
const shortId = (id) => String(id).replace(/ (?:APP|DEP|TWR|FIS)$/, "");

function readLayers() {
  try {
    const v = JSON.parse(lsGet(LS_LAYERS));
    if (Array.isArray(v)) return new Set(v.filter((x) => LAYER_IDS.includes(x)));
  } catch { /* brak albo uszkodzony wpis */ }
  return new Set(DEFAULT_LAYERS);
}
// zapamiętany przekrój LOW wyrównany do suwaka (stary wpis z przycisków ◀ ▶ mógł mieć FL335 albo FL302)
function readCut() {
  const n = parseInt(lsGet(LS_CUT), 10);
  return n >= 95 && n <= 335 ? cutAt(n, cutRange(null)) : DEFAULT_CUT;
}

// przybliżone pole wielokąta (do wyboru większej części sektora dzielonego w poziomie, np. R-N i R-S)
const areaOf = (g) => ringsOf(g).reduce((a, r) => a + Math.abs(r.reduce((s, [x1, y1], i) => {
  const [x2, y2] = r[(i + 1) % r.length];
  return s + x1 * y2 - x2 * y1;
}, 0)) / 2, 0);

// Sektory ACC podglądu wg obsługującego (runda 12, Marek): wycinki na wysokości podglądu (accs, sliceAt) z tym samym
// stanowiskiem z kolejności przejmowania warstwy (sectors z /api/radio/board: owner) → jeden obszar (dissolve.js,
// w pamięci wg klucza: wersja danych, stanowisko, wycinki) i jeden punkt etykiety poza dziurami; wycinki bez nikogo
// pomijamy. Błąd sumy: geom null, rysujemy same wypełnienia wycinków. Wynik od największego obszaru:
// [{owner, sectors (wg ID), slices, geom, area, at: [lat, lon]}]
export function accGroups(accs, sectors, layer, ver = "") {
  const own = new Map((sectors || []).filter((s) => s.layer === layer && s.owner).map((s) => [s.letter, s]));
  const by = new Map();
  (accs || []).forEach((sl) => {
    const s = own.get(sl.letter);
    if (!s) return;
    let g = by.get(s.owner);
    if (!g) by.set(s.owner, (g = { owner: s.owner, sectors: [], slices: [] }));
    if (!g.sectors.includes(s)) g.sectors.push(s);
    g.slices.push(sl);
  });
  return [...by.values()].map((g) => {
    const u = dissolve(`rb|${ver}|${g.owner}|${g.slices.map((sl) => `${sl.name}:${sl.lo}`).sort().join(",")}`, g.slices.map((sl) => sl.geometry));
    const big = g.slices.reduce((a, sl) => (areaOf(sl.geometry) > areaOf(a.geometry) ? sl : a));
    const at = (u && (u.at ||= pointIn(u.geometry))) || labelPoint({ type: "Feature", geometry: big.geometry, properties: { fir: "EPWW", name: big.name } })?.at || null;
    return { ...g, sectors: g.sectors.sort((a, b) => a.id.localeCompare(b.id)), geom: u?.geometry || null,
      area: u ? u.area : g.slices.reduce((a, sl) => a + areaOf(sl.geometry), 0), at };
  }).sort((a, b) => b.area - a.area);
}
// etykieta sektorów ACC jednego stanowiska: nazwa stanowiska (id) i częstotliwość; x.freq jak w lblLines
export const accLabel = (g, id, x) => ({ cls: "acc", state: "on", id, k: g.owner, ls: [{ fq: x.freq(g.owner), by: "" }] });

// --- LOA EPWW z sąsiadem (data/seed/loa.json, jak do 0.11 w RADIO): tytuł, wersja, liczba przekazań i PDF, pod spodem
// gęste tabele przekazań OUT/IN, zasady (przekazanie kontroli, VFR, inne) i stanowiska wg LOA, z filtrem.
// Lista raz na sesję (plik zmienia się z nową wersją PolIDS); błąd nie zostaje w pamięci.
let loaP = null;
const loadLoa = () => (loaP ||= api("/api/radio/loa").catch((e) => { loaP = null; throw e; }));
// tylko ścieżki do plików PolIDS (/files/docs/…): adres OTHER › DOCS (#other/docs/LOA/…) i PDF w nowej karcie
const DOCS = "/files/docs/";
const pdfPath = (pdf) => (String(pdf || "").startsWith(DOCS) ? String(pdf) : "");
const arr = (x) => (Array.isArray(x) ? x : []);

function loaTransfers(rows, from, to) {
  if (!rows.length) return "";
  // kolumny sektorów tylko, gdy LOA je podaje (LKAA, LZBB: bez sektorów)
  const sec = rows.some((t) => t.from || t.to);
  const head = `<tr><th>Ruch</th><th>COP</th><th>Poziom</th>${sec ? "<th>Z sektora</th><th>Do sektora</th>" : ""}<th>Warunki</th></tr>`;
  // filtr po treści wiersza bez pola dir ("out"/"in" pasowałoby do każdego przekazania w tę stronę)
  const body = rows.map((t) => `<tr data-f="${esc(Object.entries(t).filter(([k]) => k !== "dir").map(([, v]) => v).join(" ").toLowerCase())}">`
    + `<td class="trf">${esc(t.traffic)}</td><td class="cop">${esc(t.cop)}</td><td class="lvl">${esc(t.level)}</td>`
    + `${sec ? `<td class="sec">${esc(t.from)}</td><td class="sec">${esc(t.to)}</td>` : ""}<td class="cond">${esc(t.conditions)}</td></tr>`).join("");
  return `<div class="rb-loa-sec"><h4>${esc(from)} → ${esc(to)} <small>${rows.length}</small></h4>
    <table class="rb-loa-tab"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}
const loaList = (title, items) => (arr(items).length ? `<div class="rb-loa-sec"><h4>${esc(title)} <small>${items.length}</small></h4>
  <ul>${items.map((x) => `<li data-f="${esc(String(x).toLowerCase())}">${esc(x)}</li>`).join("")}</ul></div>` : "");

// fir, title: stos sąsiada z /api/radio/board; f: wpis z loa.json albo null (brak LOA z tym FIR-em, błąd: msg,
// jeszcze się wczytuje: loading); radio(cs): znak radiowy stanowiska, gdy LOA go nie podaje
export function loaHtml(fir, title, f, radio, { msg = "", loading = false } = {}) {
  const tr = arr(f?.transfers), out = tr.filter((t) => t.dir === "out"), inn = tr.filter((t) => t.dir === "in");
  const pdf = pdfPath(f?.pdf);
  // tytuł LOA podaje już FIR sąsiada (LOA EPWW – EDWW (Bremen)); bez LOA: FIR i nazwa ze stosu
  const head = `<div class="rb-loa-h"><b class="rb-loa-tag">LOA</b>${f ? "" : `<span class="rb-loa-fir"><b>${esc(fir)}</b> ${esc(title)}</span>`}`
    + (f ? `<span class="rb-loa-t" title="${esc(f.title)}">${esc(f.title)}</span><span class="rb-loa-v" title="${esc(f.version)}">${esc(f.version)}</span>` : "")
    + `<span class="rb-loa-sp"></span>`
    + (pdf ? `<button type="button" class="rb-loa-a" data-docs="${esc(pdf.slice(DOCS.length))}" title="Otwórz PDF w OTHER › DOCS (← wraca do RADIO)">DOCS › LOA</button>`
      + `<a class="rb-loa-a" href="${esc(encodeURI(pdf))}" target="_blank" rel="noopener" title="${esc(pdf)}">PDF ↗</a>` : "")
    + `<button type="button" class="rb-loa-x" title="Zamknij (Esc)">✕</button></div>`;
  if (!f) {
    return head + `<div class="rb-loa-b"><p class="${msg ? "error" : "hint"}">${msg ? esc(msg) : loading ? "Wczytywanie LOA…"
      : `Brak LOA EPWW z ${esc(fir)} w PolIDS. Wszystkie LOA: OTHER › DOCS, kategoria LOA.`}</p></div>`;
  }
  const pos = arr(f.positions).map((p) => `<tr data-f="${esc(Object.values(p).join(" ").toLowerCase())}">`
    + `<td class="${p.side === "EP" ? "ep" : "nb"}">${p.side === "EP" ? "EPWW" : esc(fir)}</td><td class="pcs">${esc(p.callsign)}</td>`
    + `<td>${esc(p.radio || radio(p.callsign))}</td><td class="cop">${esc(p.sector)}</td><td class="lvl">${esc(p.frequency)}</td>`
    + `<td class="cond">${esc(p.note)}</td></tr>`).join("");
  return head + `<div class="rb-loa-b">
    <div class="rb-loa-tools"><input type="search" class="rb-loa-q" placeholder="filtr: COP, lotnisko, FL, sektor…" aria-label="Filtr LOA">
      <span class="rb-loa-cnt"></span><span class="rb-loa-n">przekazania: ${out.length} z EPWW · ${inn.length} do EPWW</span>
      <span class="hint" title="Wyciąg z PDF (data/seed/loa.json); w razie wątpliwości obowiązuje PDF">wyciąg z PDF, obowiązuje PDF</span></div>
    ${loaTransfers(out, "EPWW", fir)}${loaTransfers(inn, fir, "EPWW")}
    <div class="rb-loa-rules">${loaList("Przekazanie kontroli i łączności", f.silent)}${loaList("VFR", f.vfr)}${loaList("Inne zasady", f.other)}</div>
    ${pos ? `<div class="rb-loa-sec"><h4>Stanowiska i sektory wg LOA <small>${arr(f.positions).length}</small></h4>
      <table class="rb-loa-tab pos"><thead><tr><th>Strona</th><th>Stanowisko</th><th>Znak radiowy</th><th>Sektor</th><th>Częst.</th><th>Uwagi</th></tr></thead>
      <tbody>${pos}</tbody></table></div>` : ""}
  </div>`;
}

// --- stos sąsiada (runda 11, Marek): kafelki VACS sektorów graniczących z EPWW (bez FMP). Częstotliwość tylko, gdy ktoś
// obsługuje sektor (owner: pierwsze stanowisko online z kolejności VACS), ze skrótem tego stanowiska, gdy to nie
// stanowisko kafelka; nikt w całym stosie → jedna linia ADVISORY 122.800 pod nagłówkiem. Granice pionowe raz, na osi
// między pasmami (stackBands). st: stos z /api/radio/board; x: {nm(cs), freq(cs), who(cs), pos(cs), sel, net} – nazwa,
// częstotliwość i zalogowany z tablicy, opis stanowiska, FIR otwartego LOA, false = brak danych VATSIM (bez ADVISORY).
const altText = (t) => (t.lo ? (t.hi < TOP_FT ? `${flText(t.lo)}–${flText(t.hi)}` : `od ${flText(t.lo)}`) : `do ${flText(t.hi)}`);
const bdHtml = (ft) => `<div class="rb-bd" data-rt="${esc(flText(ft))}"><i>${esc(flNum(ft))}</i></div>`;
// nowy ciąg pasm (gap): krótka kreska, a pod nią zakres pasma jak w VACS (+315) – należy do pasma poniżej
const reHtml = (b) => `<div class="rb-sep"></div><div class="rb-bd rb-re" data-rt="${esc(`Nowy ciąg: ${altText(b)} (nie styka się z pasmami wyżej)`)}">`
  + `<i>${esc(b.re)}</i></div>`;
// nobody: nikt w całym stosie – tylko wtedy kafelek bez nikogo mówi o ADVISORY
function tileHtml(t, x, nobody) {
  const o = t.owner, chain = arr(t.chain);
  const rt = [`${t.label}${t.alt ? ` · ${altText(t)} (VACS ${t.alt})` : ""}`, `Stanowisko: ${x.pos(t.callsign)}`,
    o ? `Teraz: ${x.who(o) || o}` : x.net === false ? "VATSIM: brak danych" : nobody ? `Teraz nikt – ADVISORY ${ADVISORY_FQ}` : "Teraz nikt",
    ...(chain.length > 1 ? [`Kolejność: ${chain.map(x.nm).join(" › ")}`] : [])].join("\n");
  const by = o && o !== t.callsign ? `<i class="rb-by">${esc(byShort(x.nm(o)))}</i>` : "";
  return `<div class="rb-box ${o ? "on" : "off"}" data-rt="${esc(rt)}"><b class="rb-id">${esc(t.label)}</b>`
    + `${o ? `<span class="rb-ln">${by}<span class="rb-fq">${esc(x.freq(o))}</span></span>` : ""}</div>`;
}
export function nbStackHtml(st, x) {
  const tiles = arr(st.tiles), bands = stackBands(tiles);
  const owners = [...new Set(tiles.map((t) => t.owner).filter(Boolean))];
  const nobody = x.net !== false && tiles.length > 0 && !owners.length;
  const flat = !bands.some((b) => b.top || b.fl || b.re);  // bez wysokości (UMMV, UKLV): bez osi z lewej
  const tipH = [`${st.fir} ${st.title}: sektory graniczące z FIR EPWW jak w VACS (bez FMP)`,
    ...(flat ? [] : ["liczby między polami: granice pionowe (FL)"]),
    ...(bands.some((b) => b.re) ? ["kreska i zakres (np. +315): nowy ciąg pasm, który nie styka się z wyższymi"] : []),
    ...(nobody ? [`Nikt online – ADVISORY ${ADVISORY_FQ}`] : owners.map((o) => `Online: ${x.who(o) || o}`)), `Kliknij: LOA EPWW – ${st.fir}`].join("\n");
  return `<div class="rb-nb${flat ? " flat" : ""}" data-fir="${esc(st.fir)}"><div class="rb-nbh${x.sel === st.fir ? " sel" : ""}" role="button" tabindex="0" data-title="${esc(st.title)}" data-rt="${esc(tipH)}"><b>${esc(st.fir)}</b> ${esc(st.title)}</div>`
    + (nobody ? `<div class="rb-unicom">ADVISORY ${ADVISORY_FQ}</div>` : "")
    + (bands.length ? bands.map((b) => `${b.re ? reHtml(b) : ""}${b.top ? bdHtml(b.top) : ""}`
      + `<div class="rb-bxs">${b.tiles.map((t) => tileHtml(t, x, nobody)).join("")}</div>${b.fl ? bdHtml(b.fl) : ""}`).join("")
      : `<div class="rb-none">brak sektorów VACS</div>`) + "</div>";
}

// --- etykieta podglądu (sektor ACC, APP, TWR, FIS) w odmianach: 0 pełna (ID nad częstotliwością), 1 skrócona (ID
// i częstotliwość w jednej linii, gdy pełna nie mieści się przy swoim punkcie) i 2 samo ID; szczegóły zawsze w dymku.
// lb: {cls, state, id, ls: [{fq, by}], more?, extra?, k?, icao?, rt}.
// Samo ID tylko bez nikogo (runda 11, Marek): ls bez częstotliwości i stanowiska, bez linii ATIS. Obsadzona
// nigdy nie traci częstotliwości (albo nazwy stanowiska, które ją ma w innej etykiecie): samo ID czytałoby się jak „nikt”.
const bare = (lb) => !lb.extra && !lb.ls.some((l) => l.fq || l.by);
// odmiany do ułożenia (placeLabels): APP/TWR/FIS pełna i skrócona, sektor ACC (small = false) tylko pełna, bez nikogo
// samo ID
export const lblVariants = (lb, small = true) => (bare(lb) ? [2] : small ? [0, 1] : [0]);
// kolejność układania (najpierw mniejsza): sektory ACC, potem etykiety z częstotliwością (APP, TWR – też sam ATIS
// online, FIS), na końcu samo ID bez nikogo – w tłoku (Warszawa–Modlin–Łódź–Radom) miejsce przy punkcie dostaje
// częstotliwość
const PRIO = { acc: [0, 0], app: [1, 4], twr: [2, 4.5], fis: [3, 5] };
export const lblPrio = (kind, lb) => PRIO[kind][bare(lb) ? 1 : 0];
export function lblHtml(lb, v = 0) {
  const attrs = `${lb.k ? ` data-k="${esc(lb.k)}"` : ""}${lb.icao ? ` data-icao="${esc(lb.icao)}" tabindex="0" role="button"` : ""} data-rt="${esc(lb.rt)}"`;
  const cls = `rb-lbl ${lb.cls} ${lb.state}${v ? " cmp" : ""}`;
  // ID w skróconej i samym ID: z częstotliwością bez rodzaju (WA S 135.930), bez niej pełne – samo „GD” czytałoby się
  // jak lotnisko
  const cid = lb.ls[0]?.fq ? shortId(lb.id) : lb.id;
  if (v === 2) return `<div class="${cls}"${attrs}><b class="rb-id">${esc(cid)}</b></div>`;
  const fq = lb.ls[0]?.fq ? `<span class="rb-fq">${esc(lb.ls[0].fq)}</span>` : "";
  // skrócona: kto przejmuje (inny niż stanowisko etykiety) przed częstotliwością – skrótem (WA S ↑LL 124.230: TMA
  // Łódź u wieży Łódź), a bez częstotliwości (podana w innej etykiecie) pełną nazwą zamiast pustej linii
  const b0 = String(lb.ls[0]?.by || "").replace(/^↑/, "");
  const by = b0 ? `<i class="rb-by">↑${esc(fq ? shortId(b0) : b0)}</i>` : "";
  if (v === 1) return `<div class="${cls}"${attrs}><b class="rb-id">${esc(cid)}</b>${by}${fq}</div>`;
  const more = lb.more ? `<i class="rb-more">+${lb.more}</i>` : "";
  return `<div class="${cls}"${attrs}><b class="rb-id">${esc(lb.id)}</b>${lb.ls.filter((l) => l.fq || l.by).map((l) => `<span class="rb-ln">${l.by ? `<i class="rb-by">${esc(l.by)}</i>` : ""}`
    + `${l.fq ? `<span class="rb-fq">${esc(l.fq)}</span>` : ""}</span>`).join("")}${more}${lb.extra || ""}</div>`;
}

// --- linijki etykiet ({fq, by}); x = {freq, nm, pos, on}: częstotliwość (online albo z pliku), nazwa stanowiska,
// stanowiska z tablicy (pos), kontroler online. Właściciele (najwyżej dwaj) z częstotliwością, by = właściciel inny
// niż stanowisko etykiety; nikt → żadnej linijki (samo ID; stanowisko i jego częstotliwość w dymku)
export const lblLines = (nominal, owners, x) => owners.slice(0, 2).map((o) => ({ fq: x.freq(o), by: o === nominal ? "" : x.nm(o) }));
// części TMA (warstwa APP) → Map id → {ls, more}. Częstotliwość stanowiska, które przejmuje cudze części (WA APP: WA N
// i WA S, TMA Łódź i Radom), tylko w jednej etykiecie: w jego części (najpierw przy jego lotnisku), potem w TMA jego
// lotniska, potem w pierwszej; w pozostałych sama nazwa (↑WA APP), reszta w dymku. Część obsługiwana przez własne
// stanowisko zawsze z częstotliwością (WA S APP w TMA Warszawa, Łódź i Radom), jak sektory ACC
export function tmaLines(parts, x) {
  const keeper = new Map();  // znak → id części, w której etykieta ma jego częstotliwość
  [(g, cs) => cs === g.nominal && cs.slice(0, 4) === g.icao, (g, cs) => cs === g.nominal, (g, cs) => cs.slice(0, 4) === g.icao]
    .forEach((ok) => parts.forEach((g) => { const cs = g.owners[0]; if (cs && ok(g, cs) && !keeper.has(cs)) keeper.set(cs, g.id); }));
  return new Map(parts.map((g) => {
    const ls = lblLines(g.nominal, g.owners, x);
    ls.forEach((l, i) => {
      const cs = g.owners[i];
      if (!keeper.has(cs)) keeper.set(cs, g.id);
      if (keeper.get(cs) !== g.id && cs !== g.nominal) l.fq = "";
    });
    return [g.id, { ls, more: Math.max(0, g.owners.length - 2) }];
  }));
}
// wieża (TWR, TWR/ATIS): online → jej częstotliwość; przejmuje inne stanowisko (np. APP) → częstotliwość tego, kto
// przejmuje, z ↑APP (jak sektory ACC; na częstotliwości wieży nikt nie siedzi); nikt → żadnej linijki (samo ID).
// Linia ATIS (twra) tylko z ATIS online. Wynik {tw, cover, ls, atl}
export function twrLines(g, x, twra = false) {
  const tw = g.owners.find((o) => /_TWR$/.test(o)) || null, cover = tw ? null : g.owners[0] || null;
  const ls = tw ? [{ fq: x.freq(tw), by: tw === g.nominal ? "" : x.nm(tw) }]
    : cover ? [{ fq: x.freq(cover), by: `↑${byShort(x.nm(cover))}` }] : [];
  const a = twra && g.atis ? x.on(g.atis) : null;
  const atl = a ? `<span class="rb-ln rb-atl"><i class="rb-by">ATIS</i><span class="rb-fq">${esc(x.freq(g.atis))}</span>`
    + `${a.atis_code ? `<b class="rb-code">${esc(a.atis_code)}</b>` : ""}</span>` : "";
  return { tw, cover, ls, atl };
}

export default {
  mount(root) {
    const el = h(`<div class="rb">
      <div class="rb-atis" aria-label="ATIS"></div>
      <div class="rb-grid">
        ${MAPS.map((m) => m.key === "MID" ? `<div class="rb-mid">
            <div class="rb-btns">${LAYERS.map((l) => `<button type="button" data-ly="${l.id}" title="${esc(l.tip)}" aria-pressed="false">${esc(l.label)}</button>`).join("")}</div>
            <div class="rb-status"></div>
            <div class="rb-legend"><span title="Sektory ACC: jeden obszar i jedna etykieta (stanowisko, częstotliwość) na stanowisko; granica tylko tam, gdzie zmienia się obsługujący. Sektorów bez nikogo nie rysujemy (zostaje granica FIR EPWW)."><i class="rb-sw on"></i>obsadzony</span><span title="TMA, CTR i FIS EPWW bez nikogo online: samo ID, bez częstotliwości (stanowisko i jego częstotliwość w dymku); sektory ACC bez nikogo: nie rysujemy; sąsiedzi: pole bez częstotliwości, nikt w całym stosie – ADVISORY ${ADVISORY_FQ}"><i class="rb-sw off"></i>nikt (samo ID)</span><span><i class="rb-sw cov"></i>↑ przejmuje inne (TWR)</span></div>
            ${panelHtml(m)}
          </div>` : panelHtml(m)).join("")}
        <div class="rb-loa" role="dialog" aria-label="LOA" hidden></div>
      </div>
      <div class="rb-tip" hidden></div>
    </div>`);
    root.append(el);
    const $ = (s) => el.querySelector(s);
    const tip = $(".rb-tip"), loaBox = $(".rb-loa");

    let layers = readLayers(), cut = readCut();
    // poziom przekroju LOW wyrównany do suwaka (adres i pamięć przeglądarki mogą mieć dowolny FL095–FL335)
    const lowCut = () => cutAt(cut, cutRange(d?.layers?.LOW));
    let d = null, geo = null, geoVer = "", err = "", border = null, noBorder = false, timer = null, loading = false, lastLoad = 0;
    let loaFir = null;   // FIR, którego LOA jest otwarte (panel .rb-loa)
    const maps = Object.fromEntries(MAPS.map((m) => {
      const p = el.querySelector(`.rb-panel[data-map="${m.key}"]`);
      return [m.key, { ...m, el: p, svg: p.querySelector("svg"), lbls: p.querySelector(".rb-lbls"), free: p.querySelector(".rb-free") }];
    }));

    // --- nazwy i częstotliwości
    const nm = (cs) => (String(cs).startsWith("EP") ? displayName(cs).replace(/^EPWW /, "") : posName(cs));
    const on = (cs) => d?.online?.[cs];
    const freqOf = (cs) => on(cs)?.frequency || d?.pos?.[cs]?.frequency || "";
    const who = (cs) => {
      const o = on(cs);
      return o ? `${o.callsign} ${o.frequency} – ${o.name || "?"} (CID ${o.cid ?? "?"})${o.logon_time ? `, od ${utcHhmm(o.logon_time)}` : ""}` : "";
    };
    const posLine = (cs) => `${cs}${d?.pos?.[cs]?.name ? ` · ${d.pos[cs].name}` : ""} ${d?.pos?.[cs]?.frequency || ""}`.trim();
    const chainText = (ch) => ch.map(nm).join(" › ");
    // linijki etykiet (lblLines, tmaLines, twrLines): częstotliwości i nazwy z tablicy
    const lx = () => ({ freq: freqOf, nm, on, pos: d?.pos });
    const nowText = (owners) => (owners.length ? owners.map((o) => `Teraz: ${who(o) || o}`).join("\n")
      : d?.net?.ok === false ? "VATSIM: brak danych" : "Teraz nikt");

    // --- pasek ATIS
    function renderAtis() {
      const box = $(".rb-atis");
      box.hidden = !layers.has("atis");
      if (box.hidden || !d) return;
      box.innerHTML = d.atis.map((a) => {
        const o = on(a.callsign), fq = freqOf(a.callsign) || "—";
        const rt = [posLine(a.callsign), o ? `Online: ${who(a.callsign)}${o.atis_code ? `\nInformacja ${o.atis_code}` : ""}` : "ATIS offline",
          ...(o?.text_atis || []).slice(0, 6), "Kliknij: AERODROME"].join("\n");
        return `<button type="button" class="rb-at ${o ? "on" : "off"}" data-icao="${esc(a.icao)}" data-rt="${esc(rt)}">`
          + `<span class="rb-ati">${esc(a.callsign.replace(/_ATIS$/, "").replace(/_/g, " "))}</span><span class="rb-fq">${esc(fq)}</span>`
          + `${o?.atis_code ? `<b class="rb-code">${esc(o.atis_code)}</b>` : ""}</button>`;
      }).join("") || `<span class="rb-none">Brak stanowisk ATIS w pliku .ese.</span>`;
    }

    // --- stosy sąsiadów (nbStackHtml): nazwy, częstotliwości i zalogowani z tablicy
    const nbCtx = () => ({ nm, freq: freqOf, who, pos: posLine, sel: loaFir, net: d?.net?.ok !== false });
    function renderStacks() {
      if (!d) return;
      ["left", "right"].forEach((side) => {
        const m = MAPS.find((x) => x.side === side);
        const p = maps[m.key].el;
        const sts = d.stacks.filter((s) => s.map === side);
        const html = (list) => list.map((st) => nbStackHtml(st, nbCtx())).join("");
        p.querySelector(".rb-top").innerHTML = html(sts.filter((s) => AREA[s.at] === "top"));
        p.querySelector(".rb-bot").innerHTML = html(sts.filter((s) => AREA[s.at] === "bot"));
        p.querySelector(".rb-col").innerHTML = html(sts.filter((s) => AREA[s.at] === "col").sort((a, b) => COL_ORDER[a.at] - COL_ORDER[b.at]));
      });
    }

    // --- podglądy
    // dymek obszaru stanowiska: kto, które sektory warstwy i ich kolejność przejmowania
    const accTip = (g, layer) => {
      const ly = d.layers?.[layer];
      return [`${displayName(g.owner)} · ${layer}${ly?.label ? ` ${ly.label}` : ""}`, nowText([g.owner]),
        `Sektory: ${g.sectors.map((s) => s.id).join(", ")}`,
        ...g.sectors.map((s) => `${s.id}: ${s.nominal !== g.owner ? `stanowisko sektora ${nm(s.nominal)}, ` : ""}kolejność ${chainText(s.chain)}`)].join("\n");
    };
    const groupTip = (g, title, extra = [], now = null) => [title, `Stanowisko: ${posLine(g.nominal)}`,
      now || (g.owners.length || !g.topdown ? nowText(g.owners) : `Teraz top-down: ${who(g.topdown) || g.topdown}`), ...extra,
      `Kolejność: ${chainText(g.chains[0] || [])}`].join("\n");

    // etykiety podglądu: {lb (model etykiety), at: [lat, lon], kind, box?, dot?}; APP, TWR i FIS tylko na LOW;
    // kolejność układania: lblPrio. Sektory ACC: jedna etykieta na stanowisko (accGroups)
    function labelsFor(m, groups) {
      const out = [], x = lx();
      if (layers.has(m.layer)) {
        groups.forEach((g) => { if (g.at) out.push({ at: g.at, kind: "acc", lb: { ...accLabel(g, displayName(g.owner), x), rt: accTip(g, m.key) } }); });
      }
      if (m.key !== "LOW") return out;  // APP, TWR i FIS to przestrzeń dolna: na podglądzie LOW
      if (layers.has("app")) {
        // etykieta na część TMA (stanowisko części: WA APP, WA N APP, WA S APP; TMA proceduralna: wieża, np. SC TWR);
        // linijki i jedna częstotliwość przejmującego na kilka części: tmaLines
        const tma = d.tma.filter((g) => g.at), parts = tmaLines(tma, x);
        tma.forEach((g) => {
          const { ls, more } = parts.get(g.id);
          const id = nm(g.nominal), proc = /_TWR$/.test(g.nominal);
          const title = `${g.tma}${g.id !== g.tma ? ` – część ${id}` : ""} (${g.lo ? flText(g.lo) : "GND"}–${flText(g.hi)})`;
          const extra = [...(proc ? [`TMA proceduralna (bez APP): częstotliwość wieży ${id}`] : []),
            ...(g.apps.length > 1 ? [`APP w tej części: ${g.apps.map((c) => `${nm(c)} ${d.pos[c]?.frequency || ""}`).join(", ")}`] : [])];
          out.push({ at: g.at, box: g.box, kind: "app", dot: "app", lb: { cls: "app",
            state: g.owners.length ? "on" : "off", id, ls, more, rt: groupTip(g, title, extra) } });
        });
      }
      const twra = layers.has("twra");
      if (twra || layers.has("twr")) {
        d.twr.forEach((g) => {
          if (!g.at) return;
          const { tw, cover, ls, atl } = twrLines(g, x, twra);
          const a = g.atis ? on(g.atis) : null;
          const extra = g.atis ? [`ATIS: ${posLine(g.atis)}${a ? ` – online${a.atis_code ? `, informacja ${a.atis_code}` : ""}` : " – offline"}`] : [];
          const id = nm(g.nominal);
          out.push({ at: g.at, box: g.box, kind: "twr", dot: "twr", lb: { cls: "twr", state: tw ? "on" : cover ? "cov" : "off", id,
            ls, extra: atl,
            icao: g.icao, k: g.icao, rt: groupTip(g, `${g.id} · ${g.icao}`, [...extra, "Kliknij: AERODROME"],
              cover ? `Wieża offline – przejmuje ${g.owners.map((o) => who(o) || o).join(", ")}` : null) } });
        });
      }
      if (layers.has("fis")) {
        d.fis.forEach((g) => {
          if (!g.at) return;
          const ls = lblLines(g.nominal, g.owners, x), id = nm(g.nominal);
          out.push({ at: g.at, kind: "fis", dot: "fis", lb: { cls: "fis", state: g.owners.length ? "on" : "off", id,
            ls, more: Math.max(0, g.owners.length - 2),
            rt: groupTip(g, `${g.id} (do ${flText(g.hi)})`) } });
        });
      }
      return out;
    }

    function renderMap(m) {
      const p = m.el, W = p.clientWidth, H = p.clientHeight;
      if (!geo || !d || W < 40 || H < 40) return;
      const pr = p.getBoundingClientRect(), fr = m.free.getBoundingClientRect();
      const { P } = projection(border, { x: fr.left - pr.left, y: fr.top - pr.top, w: fr.width, h: fr.height }, 0.03,
        m.key === "MID" ? "bottom" : "middle");
      const ft = m.key === "LOW" ? lowCut() * 100 : LEVEL_FT[m.key];
      const groups = accGroups(sliceAt(geo.acc, ft), d.sectors, m.key, geoVer);
      const low = m.key === "LOW";
      const g = (cls, list) => `<g class="${cls}">${list.map((x) => `<path d="${pathOf(x.geometry, P)}"/>`).join("")}</g>`;
      m.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      m.svg.setAttribute("width", W);
      m.svg.setAttribute("height", H);
      m.svg.innerHTML = g("rb-firs", geo.firs)
        + `<path class="rb-land" d="${pathOf(geo.border, P)}"/>`
        + (low && layers.has("fis") ? g("rb-fisg", geo.fis) : "")
        + (low && layers.has("app") ? g("rb-tmag", geo.tma) : "")
        + (low && (layers.has("twr") || layers.has("twra")) ? g("rb-ctrg", geo.ctr) : "")
        // obszar stanowiska (bez granic między jego sektorami); po błędzie sumy same wypełnienia wycinków
        + `<g class="rb-accg">${groups.map((x) => (x.geom ? `<path data-k="${esc(x.owner)}" class="st" d="${pathAll(x.geom, P)}"/>`
          : x.slices.map((sl) => `<path data-k="${esc(x.owner)}" class="st fb" d="${pathOf(sl.geometry, P)}"/>`).join(""))).join("")}</g>`
        + `<path class="rb-border" d="${pathOf(geo.border, P)}"/>`;
      renderTitle(m, { top: fr.top - pr.top, left: fr.left - pr.left, right: pr.right - fr.right });
      // etykiety: wstawiamy wszystkie odmiany, mierzymy, układamy (ważniejsze pierwsze), zostawiamy wybraną odmianę
      const items = labelsFor(m, groups).map((it) => ({ ...it, prio: lblPrio(it.kind, it.lb) })).sort((a, b) => a.prio - b.prio);
      const vs = items.map((it) => lblVariants(it.lb, it.kind !== "acc"));
      m.lbls.innerHTML = items.map((it, i) => vs[i].map((v) => lblHtml(it.lb, v)).join("")).join("");
      const els = [...m.lbls.children];
      let ei = 0;
      const vars = vs.map((x) => els.slice(ei, (ei += x.length)));
      const sz = (e) => ({ w: e.offsetWidth, h: e.offsetHeight });
      const pts = items.map((it) => P([it.at[1], it.at[0]]));
      // przeszkody: stosy sąsiadów i tytuł podglądu (współrzędne w panelu)
      const rel = (e) => { const r = e.getBoundingClientRect(); return { x0: r.left - pr.left - 2, y0: r.top - pr.top - 2, x1: r.right - pr.left + 2, y1: r.bottom - pr.top + 2 }; };
      const obstacles = [...p.querySelectorAll(".rb-nb, .rb-title")].filter((e) => e.offsetWidth && e.offsetHeight).map(rel);
      // kształty, których etykiety nie zasłaniają (runda 11, Marek): TMA na warstwie APP, CTR na TWR i TWR/ATIS, kropki
      // lotnisk i punktów TMA; etykieta APP/TWR staje obok nich z linią do punktu, sektora ACC przesuwa się mniej
      const twrOn = layers.has("twr") || layers.has("twra");
      const shapes = low ? [...(layers.has("app") ? geo.tma : []), ...(twrOn ? geo.ctr : [])] : [];
      const shapeKind = !shapes.length ? "" : twrOn ? "twr" : "app";
      const cover = shapes.length ? coverGrid(shapes.flatMap((x) => ringsOf(x.geometry).map((r) => r.map(P))), W, H,
        { points: pts.filter((_, i) => items[i].dot && items[i].dot !== "fis") }) : null;
      // zasięg (część TMA, CTR: od ramki kształtu), waga obrysów, kara za mniejszą odmianę i kropka pod etykietą: labelOpts
      const moves = placeLabels(items.map((it, i) => {
        const [full, ...alts] = vars[i].map(sz);
        return { x: pts[i][0], y: pts[i][1], ...full, alts, ...labelOpts(it.kind, { box: it.box, on: it.lb.state !== "off",
          fq: !!it.lb.ls?.[0]?.fq, shapes: shapeKind }, P) };
      }), { x: 2, y: 2, w: W - 4, h: H - 4 }, { obstacles, cover, near: cover ? 40 : 26 });
      // przesunięta etykieta: cienka linia do jej punktu; lotniska (TWR), TMA (APP) i FIS z kropką w punkcie
      let marks = "";
      items.forEach((it, i) => {
        const { dx, dy, v } = moves[i];
        vars[i].forEach((e, j) => { if (j !== v) e.remove(); });
        const e = vars[i][v], w = e.offsetWidth, hh = e.offsetHeight, [x, y] = pts[i];
        e.style.left = `${Math.round(x + dx - w / 2)}px`;
        e.style.top = `${Math.round(y + dy - hh / 2)}px`;
        if (it.dot) marks += `<circle class="rb-dot ${it.dot}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2"/>`;
        if (Math.abs(dx) > w / 2 + 2 || Math.abs(dy) > hh / 2 + 2) {
          const ex = Math.max(x + dx - w / 2, Math.min(x, x + dx + w / 2)), ey = Math.max(y + dy - hh / 2, Math.min(y, y + dy + hh / 2));
          marks += `<path class="rb-lead" d="M${x.toFixed(1)},${y.toFixed(1)}L${ex.toFixed(1)},${ey.toFixed(1)}"/>`;
        }
      });
      if (marks) m.svg.insertAdjacentHTML("beforeend", `<g class="rb-marks">${marks}</g>`);
    }
    // tytuł w górnym rogu wolnego pola (bez stosów sąsiadów) od strony środkowej kolumny
    function renderTitle(m, free) {
      const t = m.el.querySelector(".rb-title");
      if (m.key === "LOW") Object.assign(t.style, { top: `${Math.round(free.top)}px`, right: `${Math.round(free.right)}px` });
      if (m.key === "HIGH") Object.assign(t.style, { top: `${Math.round(free.top)}px`, left: `${Math.round(free.left)}px` });
      const ly = d?.layers?.[m.key];
      // szkielet raz: suwak LOW powstały od nowa przy każdym rysowaniu zgubiłby przeciąganie i fokus
      if (!t.firstChild) {
        t.innerHTML = `<b>${m.key}</b> <span class="rb-tl"></span>` + (m.key !== "LOW" ? ""
          : `<span class="rb-lvl"><input type="range" class="rb-cut" aria-label="Wysokość przekroju LOW"><span class="rb-ticks" aria-hidden="true"></span></span>`
            + `<output class="rb-cutv"></output>`);
      }
      t.querySelector(".rb-tl").textContent = ly?.label || "";
      if (m.key !== "LOW" || !geo) return;
      // suwak wysokości (runda 11, Marek): kreski pod suwakiem = granice wycinków ACC, na których zmieniają się kształty
      const r = cutRange(ly), v = lowCut();
      const b = cutBounds(geo.acc.filter((s) => s.lo < (ly?.upper_ft ?? 33500) && s.hi > (ly?.lower_ft ?? 9500)), ly);
      const c = cutBand(b, v), inp = t.querySelector(".rb-cut");
      Object.assign(inp, { min: r.min, max: r.max, step: r.step });
      if (Number(inp.value) !== v) inp.value = v;
      inp.setAttribute("aria-valuetext", flText(v * 100));
      inp.title = `Przekrój LOW na ${flText(v * 100)}: kształty sektorów z przedziału ${flText(c.lo)}–${flText(c.hi)}`
        + `\nKreski: granice wycinków ACC (tam zmieniają się kształty); strzałki: co 500 ft`;
      t.querySelector(".rb-cutv").textContent = flText(v * 100);
      t.querySelector(".rb-ticks").innerHTML = b.filter((x) => x / 100 > r.min && x / 100 <= r.max)
        .map((x) => `<i style="left:${(((x / 100 - r.min) / Math.max(1, r.max - r.min)) * 100).toFixed(1)}%"></i>`).join("");
    }
    function renderButtons() {
      el.querySelectorAll(".rb-btns button[data-ly]").forEach((b) => {
        b.classList.toggle("on", layers.has(b.dataset.ly));
        b.setAttribute("aria-pressed", String(layers.has(b.dataset.ly)));
      });
      el.classList.toggle("noatis", !layers.has("atis"));
    }
    function renderStatus() {
      const s = $(".rb-status");
      const n = d ? controllersOn(d.online) : 0;
      const net = d?.net;
      s.innerHTML = !d ? (err ? `<span class="error">${esc(err)}</span>` : "Wczytywanie…")
        : `${net?.ok ? `VATSIM ${esc(utcHhmm(net.updated))} · ${n} na tablicy` : `<span class="error">${esc(net?.error || "VATSIM: brak danych")}</span>`}`
          + (noBorder ? ` <span class="error">Brak granicy FIR EPWW (VATSpy): podglądy bez obrysu FIR.</span>` : "")
          + (err ? ` <span class="error">${esc(err)}</span>` : "");
      s.title = d?.source ? `Kolejność przejmowania: ${d.source}` : "";
    }
    let raf = 0;
    // jeden błąd (np. dziwne dane) nie zatrzymuje reszty strony
    const safe = (f, ...a) => { try { f(...a); } catch (e) { console.error("RADIO", e); } };
    function renderAll() {
      cancelAnimationFrame(raf);
      raf = 0;
      safe(renderButtons);
      if (d) {
        safe(renderAtis);
        safe(renderStacks);
        Object.values(maps).forEach((m) => safe(renderMap, m));
      }
      safe(renderStatus);
    }
    const later = () => { if (!raf) raf = requestAnimationFrame(renderAll); };

    // --- dane
    const visible = () => el.isConnected && !!el.closest(".view.active") && document.visibilityState !== "hidden";
    async function load() {
      if (loading) return;
      loading = true;
      try {
        // geometria raz, potem tylko gdy zmieni się wersja danych (np. po imporcie nowego pliku .ese)
        const r = await api(`/api/radio/board${geo && geoVer ? `?have=${encodeURIComponent(geoVer)}` : "?geo=1"}`);
        if (r.geo) {
          geo = r.geo;
          geoVer = r.ver || "";
          const b = bboxOrDefault(bboxOf(geo.border));
          noBorder = !b;
          border = b || EPWW_BBOX;
        } else if (geo && r.ver !== geoVer) {
          geoVer = "";  // nowa wersja danych bez geometrii w odpowiedzi: pobierzemy ją zaraz (?geo=1)
          setTimeout(load, 0);
        }
        d = r;
        // poziom z adresu albo starej pamięci przeglądarki (fl302, fl335) wyrównany do suwaka: od razu też w adresie
        const was = cut;
        cut = lowCut();
        if (cut !== was) { save(); remember("radio", argOf(layers, cut), { replace: true }); }
        err = "";
        lastLoad = Date.now();
      } catch (e) {
        err = `RADIO: ${e.message}`;
      } finally {
        loading = false;
      }
      renderAll();
    }
    const tick = () => { if (visible()) load(); };
    // powrót do karty przeglądarki: dane mogą mieć do 30 s – odświeżamy od razu
    const onVis = () => { if (visible() && Date.now() - lastLoad > REFRESH_MS) load(); };
    document.addEventListener("visibilitychange", onVis);
    loadDisplayNames().then(() => { if (d) later(); });
    load();
    timer = setInterval(tick, REFRESH_MS);
    const ro = new ResizeObserver(later);
    ro.observe($(".rb-grid"));

    // --- warstwy i przekrój: zapamiętane w przeglądarce, każda zmiana to krok do cofnięcia (#radio/atis,low/fl300)
    const save = () => { lsSet(LS_LAYERS, JSON.stringify([...layers])); lsSet(LS_CUT, String(cut)); };
    const apply = (arg) => {
      const a = parseArg(arg);
      let changed = false;
      if (a.layers && argOf(a.layers, cut) !== argOf(layers, cut)) { layers = a.layers; changed = true; }
      if (a.cut && a.cut >= 95 && a.cut <= 335 && a.cut !== cut) { cut = cutAt(a.cut, cutRange(d?.layers?.LOW)); changed = true; }
      if (changed) { save(); renderAll(); }
    };
    el.querySelector(".rb-btns").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-ly]");
      if (!b) return;
      const s = new Set(layers);
      if (s.has(b.dataset.ly)) s.delete(b.dataset.ly); else s.add(b.dataset.ly);
      // TWR/ATIS zawiera TWR: włączenie jednej z nich wyłącza drugą
      if (s.has(b.dataset.ly) && b.dataset.ly === "twra") s.delete("twr");
      if (s.has(b.dataset.ly) && b.dataset.ly === "twr") s.delete("twra");
      layers = s;
      save();
      renderAll();
      remember("radio", argOf(layers, cut));
    });
    // suwak wysokości LOW: przeciąganie rysuje od razu tylko podgląd LOW; poziom do adresu (krok do cofnięcia) po
    // puszczeniu suwaka, a seria strzałek z klawiatury to jeden krok
    let rafLow = 0, cutT = 0;
    maps.LOW.el.addEventListener("input", (e) => {
      if (!e.target.matches(".rb-cut")) return;
      cut = Number(e.target.value);
      save();
      if (!rafLow) rafLow = requestAnimationFrame(() => { rafLow = 0; safe(renderMap, maps.LOW); });
    });
    maps.LOW.el.addEventListener("change", (e) => {
      if (!e.target.matches(".rb-cut")) return;
      clearTimeout(cutT);
      cutT = setTimeout(() => remember("radio", argOf(layers, cut)), 600);
    });
    // lotnisko z paska ATIS albo etykiety TWR → AERODROME (krok do cofnięcia)
    el.addEventListener("click", (e) => {
      const a = e.target.closest(".rb-at[data-icao], .rb-lbl.twr[data-icao]");
      if (a) navigate("aerodrome", a.dataset.icao);
    });
    // etykieta TWR z klawiatury (tabindex, role=button): Enter albo spacja
    el.addEventListener("keydown", (e) => {
      const a = e.target.closest?.(".rb-lbl.twr[data-icao]");
      if (a && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); navigate("aerodrome", a.dataset.icao); }
    });

    // --- LOA: nagłówek stosu sąsiada otwiera panel (drugie kliknięcie, ✕ albo Esc zamyka); bez kroku w historii
    const markLoa = () => el.querySelectorAll(".rb-nbh").forEach((x) => x.classList.toggle("sel", x.closest(".rb-nb")?.dataset.fir === loaFir));
    const radioOf = (cs) => d?.pos?.[cs]?.name || "";
    const sideOf = (hd) => (hd.closest(".rb-high") ? "right" : "left");
    function closeLoa() {
      loaFir = null;
      loaBox.hidden = true;
      loaBox.innerHTML = "";
      markLoa();
    }
    // panel po przeciwnej stronie niż kliknięty stos (stosy tej strony zostają widoczne: inny FIR jednym kliknięciem)
    async function openLoa(fir, title, side) {
      if (loaFir === fir) return closeLoa();
      loaFir = fir;
      markLoa();
      loaBox.classList.toggle("at-left", side === "right");
      loaBox.hidden = false;
      loaBox.innerHTML = loaHtml(fir, title, null, radioOf, { loading: true });
      let html;
      try {
        const data = await loadLoa();
        html = loaHtml(fir, title, data?.firs?.[fir] || null, radioOf);
      } catch (e) {
        html = loaHtml(fir, title, null, radioOf, { msg: `LOA: ${e.message}` });
      }
      if (loaFir !== fir) return;   // w międzyczasie zamknięty albo otwarty inny FIR
      loaBox.innerHTML = html;
      loaBox.querySelector(".rb-loa-b").scrollTop = 0;
    }
    el.addEventListener("click", (e) => {
      const hd = e.target.closest(".rb-nbh");
      if (hd) openLoa(hd.closest(".rb-nb").dataset.fir, hd.dataset.title || "", sideOf(hd));
    });
    el.addEventListener("keydown", (e) => {
      const hd = e.target.closest?.(".rb-nbh");
      if (hd && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openLoa(hd.closest(".rb-nb").dataset.fir, hd.dataset.title || "", sideOf(hd)); }
      if (e.key === "Escape" && !loaBox.hidden) { e.preventDefault(); closeLoa(); }
    });
    loaBox.addEventListener("click", (e) => {
      if (e.target.closest(".rb-loa-x")) return closeLoa();
      const doc = e.target.closest("button[data-docs]");
      if (doc) navigate("other", "docs/" + doc.dataset.docs);   // OTHER › DOCS z tym PDF-em
    });
    // filtr: wiersze tabel i zasady zawierające wpisany tekst; sekcje bez trafień znikają
    loaBox.addEventListener("input", (e) => {
      if (!e.target.matches(".rb-loa-q")) return;
      const t = e.target.value.trim().toLowerCase();
      const items = [...loaBox.querySelectorAll("[data-f]")];
      items.forEach((x) => { x.hidden = !!t && !x.dataset.f.includes(t); });
      loaBox.querySelectorAll(".rb-loa-sec").forEach((sec) => { sec.hidden = !!t && !sec.querySelector("[data-f]:not([hidden])"); });
      loaBox.querySelector(".rb-loa-cnt").textContent = t ? `${items.filter((x) => !x.hidden).length} z ${items.length}` : "";
    });

    // --- dymki (jeden pływający, przycięty do okna) i podświetlenie sektora pod etykietą
    let tipFor = null;
    const moveTip = (e) => {
      const r = tip.getBoundingClientRect();
      tip.style.left = `${Math.max(4, Math.min(window.innerWidth - r.width - 4, e.clientX + 14))}px`;
      tip.style.top = `${Math.max(4, e.clientY + 16 + r.height > window.innerHeight ? e.clientY - r.height - 10 : e.clientY + 16)}px`;
    };
    const hl = (lbl, onOff) => {
      const k = lbl?.dataset.k;
      const svg = lbl?.closest(".rb-panel")?.querySelector("svg");
      if (k && svg && lbl.classList.contains("acc")) svg.querySelectorAll(`[data-k="${CSS.escape(k)}"]`).forEach((p) => p.classList.toggle("hl", onOff));
    };
    el.addEventListener("mouseover", (e) => {
      const t = e.target.closest("[data-rt]");
      if (t === tipFor) return;
      hl(tipFor, false);
      tipFor = t;
      if (!t) { tip.hidden = true; return; }
      tip.textContent = t.dataset.rt;
      tip.hidden = false;
      moveTip(e);
      hl(t, true);
    });
    el.addEventListener("mousemove", (e) => { if (tipFor) moveTip(e); });
    el.addEventListener("mouseleave", () => { hl(tipFor, false); tipFor = null; tip.hidden = true; });

    return {
      activate(arg) {
        if (arg) apply(arg);
        // menu albo adres bez stanu: dopisujemy bieżące warstwy do adresu (bez nowego kroku)
        remember("radio", argOf(layers, cut));
        if (Date.now() - lastLoad > REFRESH_MS) load();
        else later();
      },
      destroy() {
        clearInterval(timer); clearTimeout(cutT); cancelAnimationFrame(rafLow); ro.disconnect();
        document.removeEventListener("visibilitychange", onVis);
      },
    };
  },
};

function panelHtml(m) {
  const frame = m.side === "left"
    ? `<div class="rb-frame"><div class="rb-col"></div><div class="rb-top"></div><div class="rb-free"></div><div class="rb-bot"></div></div>`
    : m.side === "right"
      ? `<div class="rb-frame"><div class="rb-top"></div><div class="rb-free"></div><div class="rb-bot"></div><div class="rb-col"></div></div>`
      : `<div class="rb-frame"><div class="rb-free"></div></div>`;
  return `<section class="rb-panel ${m.cls}" data-map="${m.key}"><svg class="rb-svg" aria-hidden="true"></svg>${frame}
    <div class="rb-lbls"></div><div class="rb-title"></div></section>`;
}
