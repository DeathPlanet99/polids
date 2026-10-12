// Punkt etykiety wewnątrz wycinka sektora (MAP: etykiety przestrzeni; RADIO: ID i częstotliwość sektora na podglądach).

// klucz kształtu: wycinki tej samej nazwy o tej samej geometrii (różne przedziały wysokości) liczymy raz
export function geomKey(f) {
  const g = f.geometry, ring = g.type === "Polygon" ? g.coordinates[0] : g.type === "MultiPolygon" ? g.coordinates[0][0] : [];
  const p = (c) => (c ? `${c[0].toFixed(4)},${c[1].toFixed(4)}` : "");
  return `${f.properties.fir}/${f.properties.name}/${ring.length}/${p(ring[0])}/${p(ring[Math.floor(ring.length / 2)])}`;
}

// Punkt etykiety wewnątrz wielokąta, możliwie daleko od krawędzi (uproszczony polylabel: siatka próbek).
// within: [lonMin, latMin, lonMax, latMax] – szukaj tylko w tej ramce (np. część sektora sąsiada blisko Polski).
const labelCache = new Map();
export function labelPoint(f, within = null) {
  // klucz wg kształtu: u sąsiadów ta sama nazwa i dolna granica bywa przy różnych wielokątach (EDMM ALB, ETSIA)
  const key = `${geomKey(f)}/${within || ""}`;
  if (labelCache.has(key)) return labelCache.get(key);
  const g = f.geometry;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  let best = null, bestD = -1;
  polys.forEach(([ring]) => {
    if (!ring?.length) return;
    const k = Math.cos((ring[0][1] * Math.PI) / 180);  // długość geogr. → te same jednostki co szerokość
    const pts = ring.map(([x, y]) => [x * k, y]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (within) {
      [x0, x1, y0, y1] = [Math.max(x0, within[0] * k), Math.min(x1, within[2] * k), Math.max(y0, within[1]), Math.min(y1, within[3])];
      if (x0 >= x1 || y0 >= y1) return;
    }
    const N = 22;
    for (let i = 0; i < N; i++) {
      for (let j = 0; j < N; j++) {
        const x = x0 + ((x1 - x0) * (i + 0.5)) / N, y = y0 + ((y1 - y0) * (j + 0.5)) / N;
        let inside = false, d = Infinity;
        for (let a = 0, b = pts.length - 1; a < pts.length; b = a++) {
          const [ax, ay] = pts[a], [bx, by] = pts[b];
          if ((ay > y) !== (by > y) && x < ((bx - ax) * (y - ay)) / (by - ay) + ax) inside = !inside;
          const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
          const t = l2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / l2)) : 0;
          d = Math.min(d, Math.hypot(x - ax - t * dx, y - ay - t * dy));
        }
        if (inside && d > bestD) { bestD = d; best = [y, x / k]; }
      }
    }
  });
  const out = best ? { at: best, d: bestD } : null;
  labelCache.set(key, out);
  return out;
}
