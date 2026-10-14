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

// Miejsca na siatce przesunięć (indeksy i, j do ±m) od najbliższego w pikselach (krok sx, sy); przy równej odległości
// najpierw w pionie i w dół. Ta sama kolejność dla etykiet tego samego rozmiaru, więc zapamiętana.
const orderCache = new Map();
function gridOrder(sx, sy, m) {
  const key = `${sx}/${sy}/${m}`;
  let out = orderCache.get(key);
  if (!out) {
    out = [];
    for (let i = -m; i <= m; i++) for (let j = -m; j <= m; j++) out.push([i, j, Math.hypot(i * sx, j * sy)]);
    out.sort((a, b) => a[2] - b[2] || Math.abs(a[0]) - Math.abs(b[0]) || b[1] - a[1]);
    if (orderCache.size > 64) orderCache.clear();
    orderCache.set(key, out);
  }
  return out;
}

// Przesunięcie etykiety (MAP: etykieta stanowiska z częstotliwością), żeby nie wpadała pod plakietki, nazwy lotnisk,
// przyciski mapy i inne etykiety. box: {x, y, w, h} (środek i rozmiar w pikselach), obstacles: [{x, y, w, h, k}]
// (k: waga zasłonięcia, domyślnie 1). Zasłonięcie c: suma pól wspólnych z przeszkodami razy waga, przy czym pas
// marginesu `gap` wokół przeszkody liczy się w 1/4 (styk dwóch etykiet to mniej niż zasłonięty tekst).
// tiers: kolejne poziomy dozwolonych miejsc [{ok(dx, dy), rings, add}]: miejsca na siatce wokół (co 1/8 szerokości
// w poziomie i 1/4 wysokości w pionie, do `rings` ćwierci szerokości i połówek wysokości w każdą stronę) od
// najbliższego, tylko te z ok; add: kara za każde miejsce tego poziomu poza (0, 0) (np. etykieta poza swoim obszarem).
// Wynik {dx, dy, c}: pierwsze miejsce bez zasłonięcia, a bez takiego to z najmniejszym c (z karą; przy remisie
// wcześniejszy poziom i bliższe miejsce): późniejszy poziom wygrywa tylko, gdy jest wyraźnie tańszy. (0, 0) zawsze
// wchodzi w grę. Zasłonięcie liczone od strony przeszkód: każda dokłada się tylko do miejsc, które przykrywa.
export function nudge(box, obstacles, tiers = [{}], gap = 2) {
  const sx = Math.ceil(box.w / 8) + 1, sy = Math.ceil(box.h / 4) + 1;
  const n = 2 * Math.max(0, ...tiers.map((t) => t.rings ?? 6)), side = 2 * n + 1, hw = box.w / 2, hh = box.h / 2;
  const cost = new Float64Array(side * side);
  for (const o of obstacles) {
    const l = o.x - o.w / 2, r = o.x + o.w / 2, t = o.y - o.h / 2, b = o.y + o.h / 2, k = o.k ?? 1;
    // miejsca (i, j), w których etykieta zachodzi na przeszkodę z marginesem
    const i0 = Math.max(-n, Math.floor((l - gap - box.x - hw) / sx) + 1), i1 = Math.min(n, Math.ceil((r + gap - box.x + hw) / sx) - 1);
    const j0 = Math.max(-n, Math.floor((t - gap - box.y - hh) / sy) + 1), j1 = Math.min(n, Math.ceil((b + gap - box.y + hh) / sy) - 1);
    for (let i = i0; i <= i1; i++) {
      const x0 = box.x + i * sx - hw, x1 = x0 + box.w;
      const gx = Math.min(x1, r + gap) - Math.max(x0, l - gap), vx = Math.min(x1, r) - Math.max(x0, l);
      if (gx <= 0) continue;
      for (let j = j0; j <= j1; j++) {
        const y0 = box.y + j * sy - hh, y1 = y0 + box.h;
        const gy = Math.min(y1, b + gap) - Math.max(y0, t - gap), vy = Math.min(y1, b) - Math.max(y0, t);
        if (gy <= 0) continue;
        const v = vx > 0 && vy > 0 ? vx * vy : 0;
        cost[(i + n) * side + j + n] += (v + (gx * gy - v) / 4) * k;
      }
    }
  }
  let best = null;
  for (const { ok = () => true, rings = 6, add = 0 } of tiers) {
    for (const [i, j] of gridOrder(sx, sy, 2 * rings)) {
      const c = cost[(i + n) * side + j + n] + (i || j ? add : 0);
      if (best && c >= best.c) continue;
      if ((i || j) && !ok(i * sx, j * sy)) continue;
      best = { dx: i * sx, dy: j * sy, c };
      if (!c) return best;
    }
  }
  return best || { dx: 0, dy: 0, c: cost[n * side + n] };
}
