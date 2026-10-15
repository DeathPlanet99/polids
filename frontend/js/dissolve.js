// Granice tylko tam, gdzie zmienia się obsługujący (runda 12, Marek): wycinki sektorów obsługiwane przez to samo
// stanowisko łączymy w jeden wielokąt (MAP: obsada wg kontrolerów online, RADIO: podglądy LOW/MID/HIGH).
// Suma wielokątów: polygon-clipping 0.15.7 (MIT, frontend/vendor/polygon-clipping, ładowany znacznikiem <script>
// przed app.js jako globalThis.polygonClipping). Wynik w pamięci wg klucza (stanowisko + zestaw wycinków), więc
// odświeżenie co minutę bez zmiany obsady nic nie liczy. Błąd sumy albo brak biblioteki: null – wtedy rysujemy
// same wypełnienia wycinków, bez konturów między nimi.

const cache = new Map();
const MAX = 600;  // wpisów w pamięci (najstarsze wypadają)

// pole i obwód pierścienia [[lon, lat], …] w stopniach, długość × cos(szerokości) (jednostki jak szerokość)
export function ringStats(r) {
  if (!r || r.length < 3) return { area: 0, perim: 0 };
  const k = Math.cos((r[0][1] * Math.PI) / 180);
  let s = 0, p = 0;
  for (let i = 0; i < r.length; i++) {
    const [x1, y1] = r[i], [x2, y2] = r[(i + 1) % r.length];
    s += x1 * y2 - x2 * y1;
    p += Math.hypot((x2 - x1) * k, y2 - y1);
  }
  return { area: (Math.abs(s) * k) / 2, perim: p };
}

// Szczeliny między wycinkami: generator (sectors_geojson) upraszcza każdą linię SECTORLINE sąsiadów raz (ok. 100 m),
// więc wycinki ze wspólną linią mają te same wierzchołki; zostają miejsca, gdzie plik .ese opisuje wspólną granicę
// dwiema różnymi liniami (różnica kilku metrów), i po sumie powstają tam wąskie dziury i kliny wzdłuż dawnych granic
// wewnętrznych. Usuwamy dziury węższe niż SLIVER (szerokość ≈ 2 × pole / obwód,
// ok. 400 m) albo mniejsze niż MIN_HOLE (ok. 0,15 km²) oraz kliny wchodzące w obszar od krawędzi (cutSlits);
// prawdziwe dziury (TMA w środku sektora ACC na FL100) zostają. Tak samo okruchy (osobne części) mniejsze niż MIN_HOLE.
export const SLIVER = 0.004, MIN_HOLE = 2e-5, SLIT = 0.0015;
const thin = (r) => { const { area, perim } = ringStats(r); return (2 * area) / Math.max(perim, 1e-12) < SLIVER; };
const keepRing = (r) => ringStats(r).area >= MIN_HOLE && !thin(r);

// Klin od krawędzi: pierścień wchodzi w obszar wzdłuż dawnej granicy i wraca tuż obok (wierzchołek bliżej niż SLIT od
// niesąsiedniej krawędzi, a pętla między nimi wąska). Wycinamy pętlę: zostaje rzut wierzchołka na tę krawędź.
// Pierścień zamknięty (pierwszy = ostatni) jak w GeoJSON; odległości w stopniach z długością × cos(szerokości).
export function cutSlits(ring) {
  if (!ring || ring.length < 5) return ring;
  const k = Math.cos((ring[0][1] * Math.PI) / 180);
  let r = ring.slice(0, -1);
  for (let pass = 0; pass < 50; pass++) {
    const n = r.length, P = r.map(([x, y]) => [x * k, y]);
    let cut = null;
    for (let i = 0; i < n && !cut; i++) {
      const [px, py] = P[i];
      for (let j = 0; j < n && !cut; j++) {
        const j1 = (j + 1) % n;
        if (j === i || j1 === i) continue;
        const [ax, ay] = P[j], [bx, by] = P[j1];
        if (px < Math.min(ax, bx) - SLIT || px > Math.max(ax, bx) + SLIT || py < Math.min(ay, by) - SLIT || py > Math.max(ay, by) + SLIT) continue;
        const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
        const t = l2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) : 0;
        if (Math.hypot(px - ax - t * dx, py - ay - t * dy) >= SLIT) continue;
        const q = [r[j][0] + (r[j1][0] - r[j][0]) * t, r[j][1] + (r[j1][1] - r[j][1]) * t];
        // pętla: od wierzchołka i do krawędzi j (albo od krawędzi do wierzchołka), krótszą stroną pierścienia
        const fwd = (j - i + n) % n, loop = fwd <= n / 2
          ? [...Array(fwd + 1)].map((_, m) => r[(i + m) % n]).concat([q])
          : [q, ...[...Array((i - j1 + n) % n + 1)].map((_, m) => r[(j1 + m) % n])];
        // tylko klin sięgający w głąb (inaczej po wycięciu ten sam punkt znalazłby się znowu)
        if (loop.length < 3 || !loop.some(([x, y]) => Math.hypot((x - r[i][0]) * k, y - r[i][1]) > 3 * SLIT) || !thin([...loop, loop[0]])) continue;
        cut = fwd <= n / 2 ? { from: (i + 1) % n, count: fwd, q, after: i } : { from: j1, count: (i - j1 + n) % n, q, after: j };
      }
    }
    if (!cut) break;
    const drop = new Set([...Array(cut.count)].map((_, m) => (cut.from + m) % n));
    const out = [];
    r.forEach((p, m) => { if (!drop.has(m)) out.push(p); if (m === cut.after) out.push(cut.q); });
    if (out.length < 3) return ring;
    r = out;
  }
  return [...r, r[0]];
}
export function cleanUnion(mp) {
  return (mp || []).filter((poly) => poly?.[0] && ringStats(poly[0]).area >= MIN_HOLE)
    .map(([outer, ...holes]) => [cutSlits(outer), ...holes.filter(keepRing).map(cutSlits).filter(keepRing)]);
}

// współrzędne wielokątów do sumy: Polygon i MultiPolygon z GeoJSON, bez pustych i bez pierścieni < 4 punktów
const polysOf = (g) => (g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [])
  .map((p) => (p || []).filter((r) => Array.isArray(r) && r.length >= 4))
  .filter((p) => p.length);

// suma geometrii GeoJSON → współrzędne MultiPolygon (bez szczelin); wyjątek z polygon-clipping przechodzi dalej
export function unionOf(geoms, lib = globalThis.polygonClipping) {
  if (!lib?.union) throw new Error("brak biblioteki polygon-clipping");
  const polys = (geoms || []).flatMap(polysOf);
  if (!polys.length) return [];
  return cleanUnion(lib.union(...polys));
}

// Jeden wielokąt z kilku geometrii, w pamięci wg klucza. Wynik: {geometry: MultiPolygon, area} albo null (błąd sumy).
export function dissolve(key, geoms, lib = globalThis.polygonClipping) {
  if (cache.has(key)) return cache.get(key);
  let out = null;
  try {
    const mp = unionOf(geoms, lib);
    if (mp.length) out = { geometry: { type: "MultiPolygon", coordinates: mp }, area: mp.reduce((a, p) => a + ringStats(p[0]).area, 0) };
  } catch (e) {
    console.warn("dissolve:", key, e?.message || e);
  }
  cache.set(key, out);
  if (cache.size > MAX) cache.delete(cache.keys().next().value);
  return out;
}

// Duże części wielokąta: co najmniej MAIN_PART największej (ta zawsze). Etykieta sąsiada szukana w pobliżu Polski nie
// trafia wtedy na mały oderwany kawałek (Bornholm w obszarze EKDK na wszystkich poziomach), gdy reszta leży dalej.
export const MAIN_PART = 0.1;
export function mainParts(g, share = MAIN_PART) {
  const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
  const areas = polys.map((p) => ringStats(p?.[0]).area), top = Math.max(0, ...areas);
  return { type: "MultiPolygon", coordinates: polys.filter((_, i) => areas[i] >= share * top) };
}

// Punkt etykiety wewnątrz wielokąta z dziurami, możliwie daleko od krawędzi (próbki na siatce w każdej części, jak
// labelPoint w coverage.js, ale z dziurami: etykieta sektora ACC nie wypada w TMA, którą obsługuje ktoś inny).
// within: [lonMin, latMin, lonMax, latMax] – szukaj tylko w tej ramce. Wynik [lat, lon] albo null.
export function pointIn(g, within = null, n = 24) {
  const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
  let best = null, bestD = -1;
  polys.forEach((rings) => {
    if (!rings?.[0]?.length) return;
    const k = Math.cos((rings[0][0][1] * Math.PI) / 180);
    const rs = rings.map((r) => r.map(([x, y]) => [x * k, y]));
    const xs = rs[0].map((p) => p[0]), ys = rs[0].map((p) => p[1]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (within) {
      [x0, x1, y0, y1] = [Math.max(x0, within[0] * k), Math.min(x1, within[2] * k), Math.max(y0, within[1]), Math.min(y1, within[3])];
      if (x0 >= x1 || y0 >= y1) return;
    }
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const x = x0 + ((x1 - x0) * (i + 0.5)) / n, y = y0 + ((y1 - y0) * (j + 0.5)) / n;
        let d = Infinity, inside = false;
        rs.forEach((pts, ri) => {
          let inRing = false;
          for (let a = 0, b = pts.length - 1; a < pts.length; b = a++) {
            const [ax, ay] = pts[a], [bx, by] = pts[b];
            if ((ay > y) !== (by > y) && x < ((bx - ax) * (y - ay)) / (by - ay) + ax) inRing = !inRing;
            const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
            const t = l2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / l2)) : 0;
            d = Math.min(d, Math.hypot(x - ax - t * dx, y - ay - t * dy));
          }
          if (ri === 0) inside = inRing;
          else if (inRing) inside = false;  // w dziurze
        });
        if (inside && d > bestD) { bestD = d; best = [y, x / k]; }
      }
    }
  });
  return best;
}
