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
// przekrój dla przedziału i: 5 FL nad dolną granicą (FL295–335 → FL300)
export const cutOf = (bounds, i) => Math.round(bounds[i] / 100) + 5;

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

// Etykiety bez nakładania się. items [{x, y, w, h, alts?: [{w, h}], max?: px}] w kolejności priorytetu (najpierw
// ważniejsze); x, y = punkt zakotwiczenia, w, h = rozmiar pełnej etykiety, alts = mniejsze odmiany (skrócona, samo ID).
// Kandydaci leżą na pierścieniach coraz dalej od punktu (coraz więcej kierunków); wygrywa pierwszy bez kolizji
// z już ułożonymi etykietami i przeszkodami (obstacles: stosy sąsiadów, tytuł podglądu; {x0, y0, x1, y1}) i w obrębie
// bounds {x, y, w, h}. Pełna etykieta może odejść od punktu najwyżej o `near` px (krawędź etykiety od punktu),
// mniejsza odmiana o `max` px (domyślnie `far`); gdy nic nie pasuje, najmniejsza odmiana w miejscu najmniejszego
// nałożenia. Wynik: [{dx, dy, v, hit}] – przesunięcie środka, numer odmiany (0 = pełna) i czy coś zasłania.
export function placeLabels(items, bounds, { gap = 2, obstacles = [], near = 26, far = 90, rings = 14 } = {}) {
  const placed = obstacles.map((o) => ({ ...o })), out = [];
  const over = (a, b) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const rectAt = (it, s, dx, dy) => ({ x0: it.x + dx - s.w / 2 - gap / 2, y0: it.y + dy - s.h / 2 - gap / 2,
    x1: it.x + dx + s.w / 2 + gap / 2, y1: it.y + dy + s.h / 2 + gap / 2 });
  const outside = (r) => (bounds ? Math.max(0, bounds.x - r.x0) + Math.max(0, r.x1 - (bounds.x + bounds.w))
    + Math.max(0, bounds.y - r.y0) + Math.max(0, r.y1 - (bounds.y + bounds.h)) : 0);
  // odległość punktu zakotwiczenia od etykiety (0 = punkt pod etykietą)
  const reach = (s, dx, dy) => Math.hypot(Math.max(0, Math.abs(dx) - s.w / 2), Math.max(0, Math.abs(dy) - s.h / 2));
  items.forEach((it) => {
    const sizes = [{ w: it.w, h: it.h }, ...(it.alts || [])];
    let pick = null;
    sizes.forEach((s, v) => {
      if (pick) return;
      const limit = v === 0 && sizes.length > 1 ? near : (it.max ?? far);
      const sx = s.w * 0.3 + gap, sy = s.h * 0.55 + gap;
      for (let k = 0; k <= rings && !pick; k++) {
        const n = k ? 8 * k : 1;
        let any = false;
        for (let a = 0; a < n; a++) {
          const t = (a * 2 * Math.PI) / n;
          const dx = Math.round(Math.cos(t) * k * sx), dy = Math.round(Math.sin(t) * k * sy);
          if (reach(s, dx, dy) > limit) continue;
          any = true;
          const r = rectAt(it, s, dx, dy);
          if (!outside(r) && !placed.some((p) => over(r, p) > 0)) { pick = { dx, dy, v, r, hit: false }; break; }
        }
        if (!any && k > 1) break;  // cały pierścień poza zasięgiem
      }
    });
    if (!pick) {
      // nic wolnego: najmniejsza odmiana tam, gdzie zasłania najmniej
      const v = sizes.length - 1, s = sizes[v];
      const sx = s.w * 0.3 + gap, sy = s.h * 0.55 + gap;
      let best = Infinity;
      for (let k = 0; k <= rings; k++) {
        const n = k ? 8 * k : 1;
        for (let a = 0; a < n; a++) {
          const t = (a * 2 * Math.PI) / n;
          const dx = Math.round(Math.cos(t) * k * sx), dy = Math.round(Math.sin(t) * k * sy);
          if (reach(s, dx, dy) > (it.max ?? far)) continue;
          const r = rectAt(it, s, dx, dy);
          const cost = placed.reduce((acc, p) => acc + over(r, p), 0) + outside(r) * s.h * 4 + Math.hypot(dx, dy) * 0.05;
          if (cost < best) { best = cost; pick = { dx, dy, v, r, hit: true }; }
        }
      }
    }
    placed.push(pick.r);
    out.push({ dx: pick.dx, dy: pick.dy, v: pick.v, hit: pick.hit });
  });
  return out;
}

// Stos sąsiada: kafelki VACS (już od najwyższego zakresu; lo/hi w stopach, null = bez wysokości) w pasma o tym samym
// zakresie, ułożone w ciągi stykających się pasm. Każda granica raz: fl = poziom pod pasmem (ft), top = nad pasmem
// (tylko na początku ciągu, gdy pasmo nie sięga FL660). Pasmo styka się z poprzednim, gdy zaczyna się dokładnie na jego
// dolnej granicy (EYVL M 335-365 pod EYVL U +365) albo gdy poprzednie sięga ziemi, a to jest niżej (ESMM L -195 pod
// ESMM 8 -365); inaczej nowy ciąg (gap: EDUU SPE +315 po OSE12/HVL12 285-365).
export const TOP_FT = 66000;
export function stackBands(tiles) {
  const bands = [];
  (tiles || []).forEach((t) => {
    const lo = t.lo ?? 0, hi = t.hi ?? TOP_FT;
    const b = bands.find((x) => x.lo === lo && x.hi === hi);
    if (b) b.tiles.push(t);
    else bands.push({ lo, hi, tiles: [t], top: null, fl: null, gap: false });
  });
  const out = [];
  let cur = null;
  while (bands.length) {
    const i = cur?.lo ? bands.findIndex((b) => b.hi === cur.lo) : -1;
    const b = bands.splice(Math.max(0, i), 1)[0];
    const joins = !!cur && (b.hi === cur.lo || (cur.lo === 0 && b.hi < cur.hi));
    if (cur) cur.fl = joins ? b.hi : cur.lo || null;
    b.gap = !!cur && !joins;
    if (!joins && b.hi < TOP_FT) b.top = b.hi;
    out.push(b);
    cur = b;
  }
  if (cur) cur.fl = cur.lo || null;
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
