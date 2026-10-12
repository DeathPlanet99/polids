// RADIO (jedna strona jak w PANDORZE): czyste funkcje bez DOM – adres (warstwy i przekrój LOW), rzutowanie map,
// układ etykiet bez nakładania się, pasma wysokości w stosach sąsiadów. Testowane w Node (test_radio_board.py).

// Warstwy z kolumny przycisków (PANDORA: ATIS / LOW MID HIGH / APP / TWR / TWR/A / FIS; OAT pomijamy)
export const LAYERS = [
  { id: "atis", label: "ATIS", tip: "Pasek ATIS polskich lotnisk u góry (online z literą)" },
  { id: "low", label: "LOW", tip: "Sektory ACC na lewym podglądzie: LOW" },
  { id: "mid", label: "MID", tip: "Sektory ACC na małym podglądzie: MID" },
  { id: "high", label: "HIGH", tip: "Sektory ACC na prawym podglądzie: HIGH" },
  { id: "app", label: "APP", tip: "TMA i częstotliwości APP (na podglądzie LOW)" },
  { id: "twr", label: "TWR", tip: "CTR lotnisk i częstotliwości TWR (na podglądzie LOW)" },
  { id: "twra", label: "TWR/ATIS", tip: "CTR lotnisk: TWR i ATIS lotniska (na podglądzie LOW)" },
  { id: "fis", label: "FIS", tip: "Rejony FIS i częstotliwości Warszawa Information (na podglądzie LOW)" },
];
export const LAYER_IDS = LAYERS.map((l) => l.id);
export const DEFAULT_LAYERS = ["atis", "low", "mid", "high"];
export const DEFAULT_CUT = 300;  // przekrój LOW (FL): na FL300 są wszystkie sektory LOW (F od FL285, T od FL195)
export const CUT_STEP = 5;       // suwak wysokości LOW co 500 ft (granice wycinków w pliku .ese są co 500 ft)

// adres "#radio/<warstwy>/fl<przekrój>": warstwy po przecinku w stałej kolejności, "none" = żadna
export function argOf(layers, cut) {
  const ids = LAYER_IDS.filter((id) => layers.has(id));
  return `${ids.length ? ids.join(",") : "none"}/fl${cut}`;
}
// {layers: Set | null, cut: liczba | null}; null = brak w adresie (zostaje bieżący stan)
export function parseArg(arg) {
  const [ly = "", c = ""] = String(arg || "").split("/");
  let layers = null;
  if (ly === "none") layers = new Set();
  else if (ly) {
    const ids = ly.split(",").filter((x) => LAYER_IDS.includes(x));
    if (ids.length) layers = new Set(ids);
  }
  const m = /^fl(\d{2,3})$/i.exec(c);
  return { layers, cut: m ? Number(m[1]) : null };
}

export const flNum = (ft) => String(Math.round((Number(ft) || 0) / 100));
export const flText = (ft) => "FL" + String(Math.round((Number(ft) || 0) / 100)).padStart(3, "0");

// Przedziały warstwy LOW, w których kształty sektorów ACC są stałe: granice wycinków LOW (ft, rosnąco)
export function cutBounds(acc, layer) {
  const lo = layer?.lower_ft ?? 9500, hi = layer?.upper_ft ?? 33500;
  const b = new Set([lo, hi]);
  (acc || []).forEach((s) => [s.lo, s.hi].forEach((x) => { if (x > lo && x < hi) b.add(x); }));
  return [...b].sort((x, y) => x - y);
}
// przedział zawierający przekrój cut (FL) i sąsiednie: {i, lo, hi, n}
export function cutBand(bounds, cut) {
  const ft = cut * 100;
  let i = bounds.findLastIndex((x) => x <= ft);
  i = Math.max(0, Math.min(i, bounds.length - 2));
  return { i, lo: bounds[i], hi: bounds[i + 1], n: bounds.length - 1 };
}
// Suwak wysokości LOW (FL): od dolnej granicy warstwy do ostatniego poziomu pod górną (FL095–FL330; na FL335 są
// już kształty MID), co CUT_STEP; cutAt wyrównuje dowolny poziom (adres, pamięć przeglądarki) do suwaka
export function cutRange(layer) {
  const min = Math.round((layer?.lower_ft ?? 9500) / 100), max = Math.round((layer?.upper_ft ?? 33500) / 100) - CUT_STEP;
  return { min, max: Math.max(min, max), step: CUT_STEP };
}
export const cutAt = (cut, r) => Math.max(r.min, Math.min(r.max, r.min + Math.round(((Number(cut) || 0) - r.min) / r.step) * r.step));

// wycinki ACC na podglądzie: przekrój poziomem ft (dolna granica włącznie)
export const sliceAt = (acc, ft) => (acc || []).filter((s) => s.lo <= ft && s.hi > ft);

// --- geometria
export function ringsOf(g) {
  if (!g) return [];
  if (g.type === "Polygon") return g.coordinates?.length ? [g.coordinates[0]] : [];
  if (g.type === "MultiPolygon") return (g.coordinates || []).filter((p) => p?.length).map((p) => p[0]);
  return [];
}
export function bboxOf(g) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  ringsOf(g).forEach((r) => r.forEach(([x, y]) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }));
  return [x0, y0, x1, y1];
}
// ramka FIR EPWW, gdy brak granicy z VATSpy (bbox bez punktów: ±Infinity)
export const EPWW_BBOX = [14.1, 49.0, 24.2, 55.0];
export const bboxOrDefault = (b) => (b && b.length === 4 && b.every(Number.isFinite) && b[2] > b[0] && b[3] > b[1] ? b : null);
// czas z VATSIM (np. "2026-10-12T12:00:43.0000000Z": 7 cyfr ułamka) jako "HH:MMZ"; nieczytelny → "–"
export function utcHhmm(iso) {
  const t = Date.parse(String(iso || "").replace(/(\.\d{3})\d+/, "$1"));
  return Number.isNaN(t) ? "–" : new Date(t).toISOString().slice(11, 16) + "Z";
}
// liczba kontrolerów na tablicy: online = stanowisko → kontroler; ten sam kontroler pod znakiem sąsiada (alias_of)
// i ATIS-y nie liczą się osobno
export const controllersOn = (online) => new Set(Object.entries(online || {})
  .filter(([cs, o]) => !/_ATIS$/.test(cs) && !/_ATIS$/.test(String(o?.callsign || "")))
  .map(([cs, o]) => String(o?.callsign || cs))).size;
// Rzut równoodległościowy (długość × cos 52°) dopasowany do prostokąta rect {x, y, w, h}: bbox [lon0, lat0, lon1, lat1]
// mieści się w nim w całości, wyśrodkowany w poziomie. Wynik: P([lon, lat]) → [x, y] w pikselach panelu.
// valign: "middle" (domyślnie) albo "bottom" (mały podgląd MID przy dolnej krawędzi, jak w PANDORZE).
export function projection(bbox, rect, pad = 0.03, valign = "middle") {
  const [x0, y0, x1, y1] = bbox;
  const k = Math.cos((52 * Math.PI) / 180);
  const w = Math.max(1e-6, (x1 - x0) * k), hh = Math.max(1e-6, y1 - y0);
  const s = Math.max(0.0001, Math.min(rect.w / w, rect.h / hh) * (1 - 2 * pad));
  const ox = rect.x + (rect.w - w * s) / 2;
  const oy = valign === "bottom" ? rect.y + rect.h - hh * s - rect.h * pad : rect.y + (rect.h - hh * s) / 2;
  const P = ([lon, lat]) => [ox + (lon - x0) * k * s, oy + (y1 - lat) * s];
  return { P, s, box: { x: ox, y: oy, w: w * s, h: hh * s } };
}
export function pathOf(g, P) {
  return ringsOf(g).map((r) => r.length > 2 ? "M" + r.map((c) => P(c).map((v) => v.toFixed(1)).join(",")).join("L") + "Z" : "").join("");
}

// Siatka kształtów warstwy (CTR, TMA) w pikselach panelu do układu etykiet: komórki `cell` px, przez które biegnie
// obrys (rings: [[x, y], …] już w pikselach) albo leży punkt (points: kropki lotnisk i TMA), z sumą prefiksową – coverOf
// liczy w stałym czasie, ile pola prostokąta zasłania obrysy. Liczy się obrys, nie wnętrze: etykieta w pustym środku
// dużej TMA nie chowa jej kształtu, etykieta na małym CTR – tak.
export function coverGrid(rings, w, h, { cell = 3, points = [] } = {}) {
  const nx = Math.max(1, Math.ceil(w / cell)), ny = Math.max(1, Math.ceil(h / cell)), g = new Uint8Array(nx * ny);
  const mark = (x, y) => {
    const i = Math.floor(x / cell), j = Math.floor(y / cell);
    if (i >= 0 && j >= 0 && i < nx && j < ny) g[j * nx + i] = 1;
  };
  (rings || []).forEach((r) => {
    (r || []).forEach(([ax, ay], k) => {
      const [bx, by] = r[(k + 1) % r.length];
      const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / (cell / 2)));
      for (let t = 0; t <= n; t++) mark(ax + ((bx - ax) * t) / n, ay + ((by - ay) * t) / n);
    });
  });
  (points || []).forEach(([x, y]) => {
    for (let dy = -4; dy <= 4; dy += cell / 2) for (let dx = -4; dx <= 4; dx += cell / 2) mark(x + dx, y + dy);
  });
  const S = new Int32Array((nx + 1) * (ny + 1));
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) S[(j + 1) * (nx + 1) + i + 1] = g[j * nx + i] + S[j * (nx + 1) + i + 1] + S[(j + 1) * (nx + 1) + i] - S[j * (nx + 1) + i];
  }
  return { cell, nx, ny, S };
}
// pole (px²) prostokąta {x0, y0, x1, y1}, które zasłania obrysy i punkty z coverGrid (null = nic)
export function coverOf(gr, r) {
  if (!gr) return 0;
  const { cell, nx, ny, S } = gr, cl = (v, n) => Math.max(0, Math.min(n, v));
  const i0 = cl(Math.round(r.x0 / cell), nx), i1 = cl(Math.round(r.x1 / cell), nx);
  const j0 = cl(Math.round(r.y0 / cell), ny), j1 = cl(Math.round(r.y1 / cell), ny);
  if (i1 <= i0 || j1 <= j0) return 0;
  const W = nx + 1;
  return (S[j1 * W + i1] - S[j0 * W + i1] - S[j1 * W + i0] + S[j0 * W + i0]) * cell * cell;
}
// czy odcinek (x0, y0)–(x1, y1) przecina prostokąt r (Liang–Barsky)
function crosses(x0, y0, x1, y1, r) {
  let t0 = 0, t1 = 1;
  const dx = x1 - x0, dy = y1 - y0;
  for (const [p, q] of [[-dx, x0 - r.x0], [dx, r.x1 - x0], [-dy, y0 - r.y0], [dy, r.y1 - y0]]) {
    if (p === 0) { if (q < 0) return false; continue; }
    const t = q / p;
    if (p < 0) t0 = Math.max(t0, t); else t1 = Math.min(t1, t);
    if (t0 > t1) return false;
  }
  return true;
}
// czy odcinki a i b ([x0, y0, x1, y1]) przecinają się (bez stykania końcami)
function cross2(a, b) {
  const o = (x0, y0, x1, y1, px, py) => Math.sign((x1 - x0) * (py - y0) - (y1 - y0) * (px - x0));
  return o(...a, b[0], b[1]) * o(...a, b[2], b[3]) < 0 && o(...b, a[0], a[1]) * o(...b, a[2], a[3]) < 0;
}
// odległość punktu (px, py) od odcinka s
function distSeg(px, py, [x0, y0, x1, y1]) {
  const dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / l2)) : 0;
  return Math.hypot(px - x0 - t * dx, py - y0 - t * dy);
}
// zasięg etykiety kształtu (część TMA, CTR): pół przekątnej ramki box [lon0, lat0, lon1, lat1] w pikselach + 20 px,
// w granicach lo–hi; bez ramki null
export function boxReach(box, P, lo, hi) {
  if (box?.length !== 4) return null;
  const [a, b] = [P([box[0], box[3]]), P([box[2], box[1]])];
  return Math.max(lo, Math.min(hi, Math.hypot(b[0] - a[0], b[1] - a[1]) / 2 + 20));
}
// Parametry etykiety warstwy do placeLabels (wspólne dla strony i testu): kind acc / app / twr / fis; box = ramka
// kształtu (część TMA, CTR), on = obsadzona, fq = z częstotliwością, shapes = obrysy na podglądzie ("twr": CTR,
// "app": TMA, "" = żadne). ACC: do 70 px od punktu, schodzi z CTR i małych TMA, gdy jest miejsce; część TMA: do pół
// przekątnej swojej ramki (50–160 px); CTR: 60–90 px (CTR na 1366 px ma kilkanaście px, a w środku kraju leży pięć
// lotnisk), obrys najważniejszy – mały CTR nie znika pod etykietą; punkt TMA i lotniska zostaje widoczny (pin);
// częstotliwość obsadzonych i podanych ważniejsza niż odległość (wv)
export function labelOpts(kind, { box = null, on = false, fq = false, shapes = "" } = {}, P = null) {
  if (kind === "acc") return { max: 70, wc: { twr: 1, app: 0.5 }[shapes] ?? 0.15 };
  const r = P ? (kind === "twr" ? boxReach(box, P, 60, 90) : boxReach(box, P, 50, 160)) : null;
  return { ...(r ? { max: r, near: r } : {}), wc: kind === "twr" ? 6 : 1.5, wv: on || fq ? 4 : 2, pin: kind !== "fis" };
}

// Etykiety bez nakładania się. items [{x, y, w, h, alts?: [{w, h}], near?, max?, wc?, wv?, pin?}] w kolejności priorytetu
// (najpierw ważniejsze); x, y = punkt zakotwiczenia, w, h = rozmiar pełnej etykiety, alts = mniejsze odmiany (skrócona,
// samo ID), pin = punkt to kropka na mapie (lotnisko, TMA), której żadna etykieta nie zasłania.
// Kandydaci leżą na pierścieniach coraz dalej od punktu (coraz więcej kierunków); dozwolony jest tylko kandydat bez
// kolizji z już ułożonymi etykietami i przeszkodami (obstacles: stosy sąsiadów, tytuł podglądu; {x0, y0, x1, y1}),
// w obrębie bounds {x, y, w, h} i bez kropki z pin (swojej i cudzej) pod sobą. Spośród nich wygrywa najtańszy:
// odległość etykiety od punktu (px) + pole zasłoniętych obrysów warstwy (cover z coverGrid, waga wc etykiety,
// domyślnie 1) × WC + kara za mniejszą odmianę (× wv) + kara WX za linię prowadzącą przez inną etykietę, przez inną
// linię albo tuż obok cudzej kropki i za etykietę na cudzej linii. Bez kształtów etykieta zostaje na punkcie, gdy jest
// tam miejsce; z kształtami (CTR, TMA) odchodzi obok nich, w wolne miejsce, z cienką linią do punktu. Pełna etykieta
// może odejść od punktu najwyżej o `near` px (krawędź etykiety od punktu; it.near nadpisuje), mniejsza odmiana albo
// jedyna (samo ID bez nikogo) o `max` px (domyślnie `far`); gdy nic nie pasuje, najmniejsza odmiana w miejscu najmniejszego
// nałożenia. Układ zachłanny; gdy etykieta z wv > 1 zmalała (np. straciła częstotliwość) albo coś zasłania, kolejna
// runda układa ją przed innymi (w tłoku, np. Warszawa–Modlin–Łódź–Radom na 1366 px, najpierw ta bez miejsca), a zostaje
// runda o najmniejszym koszcie. Wynik: [{dx, dy, v, hit}] – przesunięcie środka, numer odmiany (0 = pełna) i czy coś
// zasłania.
const WC = 0.5, WV = [0, 25, 100], WX = 40, HIT = 500, ROUNDS = 4;
export function placeLabels(items, bounds, { gap = 2, obstacles = [], near = 26, far = 90, rings = 14, cover = null } = {}) {
  const pins = items.map((it, i) => (it.pin ? [it.x, it.y, i] : null)).filter(Boolean);
  const over = (a, b) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const rectAt = (it, s, dx, dy) => ({ x0: it.x + dx - s.w / 2 - gap / 2, y0: it.y + dy - s.h / 2 - gap / 2,
    x1: it.x + dx + s.w / 2 + gap / 2, y1: it.y + dy + s.h / 2 + gap / 2 });
  const outside = (r) => (bounds ? Math.max(0, bounds.x - r.x0) + Math.max(0, r.x1 - (bounds.x + bounds.w))
    + Math.max(0, bounds.y - r.y0) + Math.max(0, r.y1 - (bounds.y + bounds.h)) : 0);
  // odległość punktu zakotwiczenia od etykiety (0 = punkt pod etykietą)
  const reach = (s, dx, dy) => Math.hypot(Math.max(0, Math.abs(dx) - s.w / 2), Math.max(0, Math.abs(dy) - s.h / 2));
  // kropki pod prostokątem (z promieniem kropki)
  const dots = (r) => pins.filter(([x, y]) => x > r.x0 - 3 && x < r.x1 + 3 && y > r.y0 - 3 && y < r.y1 + 3);
  // linia od punktu do najbliższego miejsca etykiety
  const leadOf = (it, r) => [it.x, it.y, Math.max(r.x0, Math.min(it.x, r.x1)), Math.max(r.y0, Math.min(it.y, r.y1))];
  // kandydaci etykiety: pierścienie wokół punktu dla odmiany s w zasięgu limit
  function* ring(s, limit) {
    const sx = s.w * 0.3 + gap, sy = s.h * 0.55 + gap;
    for (let k = 0; k <= rings; k++) {
      const n = k ? 8 * k : 1;
      let any = false;
      for (let a = 0; a < n; a++) {
        const t = (a * 2 * Math.PI) / n;
        const dx = Math.round(Math.cos(t) * k * sx), dy = Math.round(Math.sin(t) * k * sy), d = reach(s, dx, dy);
        if (d > limit) continue;
        any = true;
        yield [dx, dy, d];
      }
      if (!any && k > 1) return;  // cały pierścień poza zasięgiem
    }
  }
  // jedna runda w kolejności order: wybór i koszt każdej etykiety
  function once(order) {
    const placed = obstacles.map((o) => ({ ...o })), leads = [], out = [];
    let total = 0;
    order.forEach((idx) => {
      const it = items[idx], sizes = [{ w: it.w, h: it.h }, ...(it.alts || [])], wc = WC * (it.wc ?? 1);
      let pick = null, best = Infinity;
      sizes.forEach((s, v) => {
        const pv = WV[Math.min(v, WV.length - 1)] * (it.wv ?? 1);
        for (const [dx, dy, d] of ring(s, v === 0 && sizes.length > 1 ? (it.near ?? near) : (it.max ?? far))) {
          if (d + pv >= best) continue;  // już droższy niż najlepszy
          const r = rectAt(it, s, dx, dy);
          if (outside(r) || placed.some((p) => over(r, p) > 0)) continue;
          if (dots(r).length) continue;  // kropka lotniska i TMA (swoja albo cudza) zostaje widoczna
          let cost = d + pv + coverOf(cover, r) * wc;
          if (cost >= best) continue;
          cost += leads.reduce((acc, l) => acc + (crosses(...l, r) ? WX : 0), 0);
          if (d > 0) {
            const l = leadOf(it, r);
            cost += placed.reduce((acc, p) => acc + (crosses(...l, p) ? WX : 0), 0)
              + leads.reduce((acc, m) => acc + (cross2(l, m) ? WX : 0), 0)
              + pins.reduce((acc, [x, y, i]) => acc + (i !== idx && distSeg(x, y, l) < 6 ? WX : 0), 0);
          }
          if (cost < best) { best = cost; pick = { dx, dy, v, r, d, hit: false }; }
        }
      });
      if (!pick) {
        // nic wolnego: najmniejsza odmiana tam, gdzie zasłania najmniej (także kropek)
        const v = sizes.length - 1, s = sizes[v];
        for (const [dx, dy, d] of ring(s, it.max ?? far)) {
          const r = rectAt(it, s, dx, dy);
          const cost = placed.reduce((acc, p) => acc + over(r, p), 0) + outside(r) * s.h * 4 + dots(r).length * s.w * 2
            + Math.hypot(dx, dy) * 0.05;
          if (cost < best) { best = cost; pick = { dx, dy, v, r, d, hit: true }; }
        }
        best += HIT;
      }
      placed.push(pick.r);
      if (pick.d > 0) leads.push(leadOf(it, pick.r));
      out[idx] = { dx: pick.dx, dy: pick.dy, v: pick.v, hit: pick.hit };
      total += best;
    });
    return { out, total };
  }
  let order = items.map((_, i) => i), front = [], win = null;
  for (let round = 0; round < ROUNDS; round++) {
    const res = once(order);
    if (!win || res.total < win.total) win = res;
    const bad = order.filter((i) => (res.out[i].hit || (res.out[i].v > 0 && (items[i].wv ?? 1) > 1)) && !front.includes(i));
    if (!bad.length) break;
    front = [...front, ...bad];
    order = [...front, ...order.filter((i) => !front.includes(i))];
  }
  return win.out;
}

// FL660: górna granica stosów sąsiadów – ta sama co TOP_FT w backend/app/services/radioboard.py (zmieniać razem)
export const TOP_FT = 66000;
// Stos sąsiada: kafelki VACS (już od najwyższego zakresu; lo/hi w stopach, null = bez wysokości) w pasma o tym samym
// zakresie, ułożone w ciągi stykających się pasm. Każda granica raz: fl = poziom pod pasmem (ft), top = nad pasmem
// (tylko na początku pierwszego ciągu, gdy pasmo nie sięga FL660). Pasmo styka się z poprzednim, gdy zaczyna się
// dokładnie na jego dolnej granicy (EYVL M 335-365 pod EYVL U +365) albo gdy poprzednie sięga ziemi, a to jest niżej
// (ESMM L -195 pod ESMM 8 -365); inaczej nowy ciąg (gap: EDUU SPE +315 po OSE12/HVL12 285-365): kreska i zakres
// pasma jak w VACS (re: "+315", "-165", "165-245") nad nim zamiast granic, żeby oś nigdy nie czytała się wstecz
// (365, 285, kreska, +315 SPE – bez 315 pod SPE).
const reText = (lo, hi) => (hi >= TOP_FT ? `+${flNum(lo)}` : lo ? `${flNum(lo)}-${flNum(hi)}` : `-${flNum(hi)}`);
export function stackBands(tiles) {
  const bands = [];
  (tiles || []).forEach((t) => {
    const lo = t.lo ?? 0, hi = t.hi ?? TOP_FT;
    const b = bands.find((x) => x.lo === lo && x.hi === hi);
    if (b) b.tiles.push(t);
    else bands.push({ lo, hi, tiles: [t], top: null, fl: null, gap: false, re: null });
  });
  const out = [];
  let cur = null;
  // dolna granica pasma kończącego ciąg; po nowym ciągu jest już w jego zakresie (re)
  const end = (b) => (b.re ? null : b.lo || null);
  while (bands.length) {
    const i = cur?.lo ? bands.findIndex((b) => b.hi === cur.lo) : -1;
    const b = bands.splice(Math.max(0, i), 1)[0];
    const joins = !!cur && (b.hi === cur.lo || (cur.lo === 0 && b.hi < cur.hi));
    if (cur) cur.fl = joins ? b.hi : end(cur);
    b.gap = !!cur && !joins;
    if (b.gap) b.re = reText(b.lo, b.hi);
    else if (!joins && b.hi < TOP_FT) b.top = b.hi;
    out.push(b);
    cur = b;
  }
  if (cur) cur.fl = end(cur);
  return out;
}
// UNICOM: sąsiad bez nikogo online w sektorach przy granicy (jedna linia pod nagłówkiem stosu)
export const ADVISORY_FQ = "122.800";

// Skrót stanowiska, które przejmuje pole stosu sąsiada: bez pierwszego członu (EDWW FLG → FLG, ESMM 6 → 6),
// chyba że to cała nazwa (LKAA, UKLV)
export const byShort = (name) => {
  const w = String(name || "").split(" ");
  return w.length > 1 ? w.slice(1).join(" ") : w[0];
};
