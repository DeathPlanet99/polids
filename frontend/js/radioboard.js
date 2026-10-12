// RADIO (jedna strona jak w PANDORZE): czyste funkcje bez DOM – adres (warstwy i przekrój LOW), rzutowanie map,
// układ etykiet bez nakładania się, grupowanie stosów sąsiadów wg zakresów wysokości. Testowane w Node (test_radio_board.py).

// Warstwy z kolumny przycisków (PANDORA: ATIS / LOW MID HIGH / APP / TWR / TWR/A / FIS; OAT pomijamy)
export const LAYERS = [
  { id: "atis", label: "ATIS", tip: "Pasek ATIS polskich lotnisk u góry (online z literą)" },
  { id: "low", label: "LOW", tip: "Sektory ACC na lewym podglądzie: LOW" },
  { id: "mid", label: "MID", tip: "Sektory ACC na małym podglądzie: MID" },
  { id: "high", label: "HIGH", tip: "Sektory ACC na prawym podglądzie: HIGH" },
  { id: "app", label: "APP", tip: "TMA i częstotliwości APP" },
  { id: "twr", label: "TWR", tip: "CTR lotnisk i częstotliwości TWR" },
  { id: "twra", label: "TWR/ATIS", tip: "CTR lotnisk: TWR i ATIS lotniska" },
  { id: "fis", label: "FIS", tip: "Rejony FIS i częstotliwości Warszawa Information" },
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

// Etykiety bez nakładania się: items [{x, y, w, h}] (x, y = środek, punkt zakotwiczenia), w kolejności priorytetu
// (najpierw ważniejsze). Każda dostaje przesunięcie {dx, dy}: kandydaci na pierścieniach coraz dalej od punktu
// (12 kierunków), pierwszy bez kolizji z już ułożonymi i w obrębie bounds {x, y, w, h}; gdy żaden nie pasuje,
// ten z najmniejszym nałożeniem. Daleko przesunięte etykiety dostają w radio.js linię do swojego punktu.
export function placeLabels(items, bounds, gap = 2, rings = 4) {
  const placed = [], out = [];
  const over = (a, b) => Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  items.forEach((it) => {
    const { w, h } = it;
    const cands = [[0, 0]];
    for (let k = 1; k <= rings; k++) {
      for (let a = 0; a < 12; a++) {
        const t = (a * Math.PI) / 6;
        cands.push([Math.round(Math.cos(t) * k * (w * 0.55 + gap)), Math.round(Math.sin(t) * k * (h + gap))]);
      }
    }
    let best = null, bestCost = Infinity;
    for (const [dx, dy] of cands) {
      const r = { x0: it.x + dx - w / 2 - gap / 2, y0: it.y + dy - h / 2 - gap / 2, x1: it.x + dx + w / 2 + gap / 2, y1: it.y + dy + h / 2 + gap / 2 };
      const outside = bounds ? Math.max(0, bounds.x - r.x0) + Math.max(0, r.x1 - (bounds.x + bounds.w))
        + Math.max(0, bounds.y - r.y0) + Math.max(0, r.y1 - (bounds.y + bounds.h)) : 0;
      const hit = placed.reduce((a, p) => a + over(r, p), 0);
      const cost = hit + outside * h * 4 + Math.hypot(dx, dy) * 0.05;
      if (cost < bestCost) { bestCost = cost; best = { dx, dy, r }; }
      if (!hit && !outside) break;  // kandydaci od najbliższego: pierwszy wolny wygrywa
    }
    placed.push(best.r);
    out.push({ dx: best.dx, dy: best.dy });
  });
  return out;
}

// Stos sąsiada: wiersze (już od najwyższej górnej granicy) w poziomy o tym samym zakresie wysokości
export function levelLines(rows) {
  const out = [];
  (rows || []).forEach((r) => {
    const last = out[out.length - 1];
    if (last && last.lo === r.lo && last.hi === r.hi) last.rows.push(r);
    else out.push({ lo: r.lo, hi: r.hi, rows: [r] });
  });
  return out;
}

// Skrót stanowiska, które przejmuje pole stosu sąsiada: bez pierwszego członu (EDWW FLG → FLG, ESMM 6 → 6),
// chyba że to cała nazwa (LKAA, UKLV)
export const byShort = (name) => {
  const w = String(name || "").split(" ");
  return w.length > 1 ? w.slice(1).join(" ") : w[0];
};
