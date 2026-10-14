// AWOS w zakładce AERODROME: ekran jak lotniskowy system obserwacji meteo na stanowisku TWR.
// Pomysł i układ za vAWOS (Aleksander Gadomski); kod napisany od nowa.
// VATSIM nie ma czujników wiatru, więc wiatr chwilowy jest SYMULOWANY z METAR: co sekundę próbka z płynnego
// błądzenia losowego wokół kierunku i prędkości z METAR, w sektorze zmienności dddVddd, z porywami do wartości G.
// Z próbek liczymy średnią 2-minutową, zakres kierunku i maksimum 10-minutowe jak prawdziwy AWOS. Ekran
// odświeżamy rzadziej (WIND_REFRESH_S), a czujnik mierzy dalej co sekundę, więc okna 2 i 10 min się nie zmieniają.
// RVR: z METAR; bez grupy RVR przy widzialności ≥ 1500 m > 2000 m, przy mniejszej symulowany z widzialności.
// Układ kolumn: 1 PAS (TDZ / MID / END jednego kierunku) albo 2 PASY (DEP i ARR, każda z własnym pasem); automatycznie
// 2 PASY, gdy ATIS (bez ATIS sugestia) ma różne pasy do lądowania i startu. Ręczny wybór układu i pasów pamiętamy dla lotniska
// do zmiany pasów w użyciu z ATIS / sugestii, najdłużej 12 h.
import { api, esc, lsGet, lsSet, lvpState, lvpTip, vatsimAtc } from "./api.js";
import { colorize, wxLines } from "./tabs/meteo.js";

const RAD = Math.PI / 180;
const has = (v) => v !== null && v !== undefined;
const norm = (d) => ((d % 360) + 360) % 360;
const sdiff = (a, b) => norm(a - b + 180) - 180; // a − b w zakresie −180…180
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const pad = (n, w) => String(n).padStart(w, "0");
const gauss = () => { let u = 0; while (!u) u = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random()); };
// proces Ornsteina-Uhlenbecka, krok 1 s: płynne błądzenie wracające do zera (tau = czas korelacji w s, sd = rozrzut)
const ou = (x, tau, sd) => x - x / tau + Math.sqrt(2 / tau) * sd * gauss();
const HIST = 600; // 10 min próbek co 1 s
const WIND_REFRESH_S = 5; // co ile sekund nowy wiatr chwilowy na ekranie (tarcza, INST, średnie, RVR)
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

// --- symulacja wiatru -------------------------------------------------------------------------------

// Wiatr z METAR jako podstawa symulacji: kierunek, prędkość, poryw, dopuszczalny sektor względem kierunku
function windBasis(p) {
  if (!p || !has(p.wind_speed)) return null;
  const spd = p.wind_speed, calm = spd === 0;
  const vrb = !calm && (p.wind_variable || !has(p.wind_dir));
  let dir = vrb || calm ? 0 : p.wind_dir;
  // bez grupy dddVddd zmiany kierunku są mniejsze niż 60° (przy słabym wietrze dopuszczamy więcej)
  let lo = spd < 3 ? -45 : -29, hi = -lo;
  const sector = !vrb && !calm && has(p.wind_var_from) && has(p.wind_var_to);
  if (sector) {
    const span = norm(p.wind_var_to - p.wind_var_from);
    let a = norm(dir - p.wind_var_from);
    if (a > span) { dir = norm(p.wind_var_from + span / 2); a = span / 2; }
    lo = -a; hi = span - a;
  }
  const sd = sector ? (hi - lo) * 0.28 : clamp(4 + 30 / Math.max(spd, 2), 5, 12);
  // porywy: częstość (na sekundę spokoju), średnia amplituda i średni wkład do prędkości, który odejmujemy,
  // żeby średnia z próbek zgadzała się z prędkością z METAR (impuls sin² ~8 s daje ok. 4 kt·s na 1 kt amplitudy)
  const gust = p.wind_gust > spd ? p.wind_gust : 0;
  const rate = spd < 4 ? 0 : gust ? 1 / 22 : 1 / 45;
  const amp = gust ? (gust - spd) * 0.75 : 0.5 * Math.min(7, spd * 0.3 + 1);
  const bias = rate ? amp * 4 / (1 / rate + 8) : 0;
  return { dir, spd, gust, vrb, calm, lo, hi, sd, rate, bias };
}

// Wspólne dla lotniska: wolne zmiany kierunku i prędkości oraz porywy. Czujniki widzą je z małym opóźnieniem
// (podmuch przechodzi wzdłuż pasa) i dokładają własne zaburzenia, więc kolumny są podobne, ale nie identyczne.
class WindField {
  constructor(b) {
    this.b = b;
    this.d = 0; this.s = 0; this.vd = Math.random() * 360; this.vv = 0; this.g = null; this.hist = [];
  }
  step() {
    const b = this.b;
    this.d = clamp(ou(this.d, 60, b.sd * 0.5), b.lo, b.hi);
    this.s = ou(this.s, 90, Math.max(0.7, b.spd * 0.1));
    if (b.vrb || b.calm) { this.vv = clamp(ou(this.vv, 15, 3), -8, 8); this.vd = norm(this.vd + this.vv); }
    // poryw: gładki impuls sin² trwający 4–10 s; przy G w METAR szczyty sięgają G
    let gust = 0, veer = 0;
    if (this.g) {
      const g = this.g, f = Math.sin(Math.PI * g.t / g.T) ** 2;
      gust = g.A * f; veer = g.veer * f;
      if (++g.t > g.T) this.g = null;
    } else if (Math.random() < b.rate) {
      const A = b.gust ? (b.gust - b.spd) * (0.5 + 0.5 * Math.random()) + b.bias : Math.random() * Math.min(7, b.spd * 0.3 + 1);
      this.g = { t: 0, T: 4 + Math.round(Math.random() * 6), A, veer: gauss() * 6 };
    }
    this.hist.push({ d: this.d, s: this.s, gust, veer, vd: this.vd });
    if (this.hist.length > 8) this.hist.shift();
  }
  at(lag) { return this.hist[Math.max(0, this.hist.length - 1 - lag)]; }
}

class Sensor {
  constructor(field, lag) { this.f = field; this.lag = lag; this.ld = 0; this.ls = 0; this.buf = []; }
  step() {
    const b = this.f.b, f = this.f.at(this.lag);
    this.ld = ou(this.ld, 12, b.sd * 0.45);
    this.ls = ou(this.ls, 10, Math.max(0.4, b.spd * 0.05));
    let s = b.spd - b.bias + f.s + f.gust + this.ls + gauss() * (0.4 + b.spd * 0.04), d;
    if (b.calm) { s = Math.abs(gauss()) * 0.45 + Math.max(0, this.ls) * 0.5; d = f.vd + gauss() * 20; }
    else if (b.vrb) d = f.vd + this.ld * 2 + gauss() * 10;
    else d = b.dir + clamp(f.d + f.veer + this.ld + gauss() * b.sd * 0.35, b.lo, b.hi);
    s = clamp(Math.round(s), 0, b.gust || b.spd + 9);
    this.buf.push({ d: Math.round(norm(d)) || 360, s });
    if (this.buf.length > HIST) this.buf.shift();
  }
  // średnia 2 min (kierunek wektorowo, prędkość skalarnie), zakres kierunku i prędkości, maksimum 10 min
  stats() {
    const last = this.buf.slice(-120);
    let x = 0, y = 0, sum = 0, smin = Infinity, smax = 0, max10 = 0, dmin = 0, dmax = 0;
    last.forEach((p) => { x += Math.sin(p.d * RAD); y += Math.cos(p.d * RAD); sum += p.s; smin = Math.min(smin, p.s); smax = Math.max(smax, p.s); });
    const dir = norm(Math.atan2(x, y) / RAD), spd = sum / last.length;
    last.forEach((p) => { const k = sdiff(p.d, dir); dmin = Math.min(dmin, k); dmax = Math.max(dmax, k); });
    this.buf.forEach((p) => { max10 = Math.max(max10, p.s); });
    return { dir, spd, from: norm(dir + dmin), to: norm(dir + dmax), range: dmax - dmin, smin, smax, max10 };
  }
}

// --- symulacja RVR ------------------------------------------------------------------------------------

// Czujniki RVR jednego pasa fizycznego (np. 11/29): przy progu pierwszego kierunku, w środku i przy drugim progu
// (TDZ pasa 29 to END pasa 11). Każda próbka to mnożnik podstawy: wspólne wolne błądzenie (ławice mgły nad
// całym lotniskiem) × własne błądzenie i stała różnica czujnika, więc TDZ / MID / END różnią się o kilka procent.
class RvrRunway {
  constructor() {
    this.x = 0;
    this.s = [0, 1, 2].map(() => ({ off: gauss() * 0.04, x: 0, hist: [] }));
  }
  step() {
    this.x = ou(this.x, 600, 0.08);
    this.s.forEach((q) => {
      q.x = ou(q.x, 180, 0.05);
      q.hist.push(Math.exp(this.x + q.x + q.off));
      if (q.hist.length > HIST) q.hist.shift();
    });
  }
}

// --- formatowanie -------------------------------------------------------------------------------------

// wiek METAR w minutach z "DDHHMMZ": dzień z bieżącego, poprzedniego albo następnego miesiąca, najbliższy teraz
// (przełom miesiąca i zegar komputera spóźniony o kilka minut nie dają ujemnego ani miesięcznego wieku)
function metarAge(time, now = Date.now()) {
  const m = /^(\d{2})(\d{2})(\d{2})Z$/.exec(time || "");
  if (!m) return null;
  const d = new Date(now);
  const t = [-1, 0, 1].map((k) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + k, +m[1], +m[2], +m[3]))
    .reduce((a, b) => (Math.abs(b - now) < Math.abs(a - now) ? b : a));
  return Math.max(0, Math.round((now - t) / 60000));
}
const ageText = (m) => (m < 60 ? `${m} min` : m < 2880 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.round(m / 1440)} dni`);

// pora dnia z godzin UTC "HH:MM" (BMCT – SR – SS – EECT)
function sunPhase(sun) {
  const now = new Date(), cur = now.getUTCHours() * 60 + now.getUTCMinutes();
  const m = (t) => (t ? +t.slice(0, 2) * 60 + +t.slice(3, 5) : null);
  const [dawn, sr, ss, dusk] = [sun.civil_dawn, sun.sunrise, sun.sunset, sun.civil_dusk].map(m);
  if (sr === null || ss === null) return sun.day ? "DZIEŃ" : "NOC";
  const inside = (a, b) => (a <= b ? cur >= a && cur < b : cur >= a || cur < b);
  if (inside(sr, ss)) return "DZIEŃ";
  if (dawn !== null && inside(dawn, sr)) return "ŚWIT";
  if (dusk !== null && inside(ss, dusk)) return "ZMIERZCH";
  return "NOC";
}

// VMC w strefie kontrolowanej lotniska (SERA.5005): widzialność ≥ 5 km i podstawa chmur ≥ 1500 ft
function vmc(p) {
  if (!p || (!has(p.visibility_m) && !p.flight_category)) return null;
  const imc = ["IFR", "LIFR"].includes(p.flight_category) || (p.visibility_m ?? 9999) < 5000 || (p.ceiling_ft ?? 99999) < 1500;
  return imc ? "IMC" : "VMC";
}

const metarWind = (p) => {
  if (!p || !has(p.wind_speed)) return "–";
  if (p.wind_speed === 0) return "00000KT";
  const dir = p.wind_variable || !has(p.wind_dir) ? "VRB" : pad(p.wind_dir, 3);
  return `${dir}${pad(p.wind_speed, 2)}${p.wind_gust ? "G" + p.wind_gust : ""}KT${has(p.wind_var_from) ? ` ${pad(p.wind_var_from, 3)}V${pad(p.wind_var_to, 3)}` : ""}`;
};
const visText = (p) => (!p || !has(p.visibility_m) ? "–" : p.cavok ? "CAVOK" : p.visibility_m >= 9999 ? "≥10 KM" : `${p.visibility_m} M`);
const cloudList = (p) => (!p ? [] : p.clouds?.length
  ? p.clouds.map((c) => `${c.cover} ${has(c.base_ft) ? c.base_ft : "///"} FT${c.type ? " " + c.type : ""}`) : [p.cavok ? "CAVOK" : "NSC"]);
const tempText = (v) => (has(v) ? (v < 0 ? "M" + Math.abs(v) : String(v)) : "–");
// RVR: P2000 → >2000, M0050 → <50, 0350V0600 → 350–600; tendencja U/D/N jako strzałka
function rvrText(r) {
  const one = (x) => (x.startsWith("P") ? ">" + +x.slice(1) : x.startsWith("M") ? "<" + +x.slice(1) : String(+x));
  return `${String(r.value || "").split("V").map(one).join("–")} M${{ U: " ↑", D: " ↓", N: " =" }[r.trend] || ""}`;
}
// oznaczenie pasa do porównań: "R29" / "29" -> "29", "6" -> "06", "11l" -> "11L"
const rwyNorm = (d) => {
  const m = /^R?(\d{1,2})([LCR]?)$/.exec(String(d ?? "").trim().toUpperCase());
  return m ? pad(+m[1], 2) + m[2] : null;
};
// wartość liczbowa grupy RVR w metrach: P2000 -> 2000, M0050 -> 50, 0350V0600 -> 475
const rvrNum = (v) => {
  const xs = String(v ?? "").split("V").map((x) => parseInt(x.replace(/^[PM]/, ""), 10)).filter(Number.isFinite);
  return xs.length ? mean(xs) : null;
};
// metry -> grupa RVR: w dół do skali raportów (co 25 m poniżej 400 m, co 50 m do 800 m, wyżej co 100 m),
// poniżej 50 m M0050, powyżej 2000 m P2000
function rvrCode(m) {
  if (m < 50) return "M0050";
  if (m > 2000) return "P2000";
  const step = m < 400 ? 25 : m < 800 ? 50 : 100;
  return pad(Math.floor(m / step) * step, 4);
}
// przeliczenie widzialności na RVR jak CMV przy światłach HI: dzień ×1,5, noc ×2 (świt i zmierzch pośrodku)
const RVR_K = { "DZIEŃ": 1.5, "ŚWIT": 1.75, "ZMIERZCH": 1.75, "NOC": 2 };
const RVR_TXT = { "DZIEŃ": "dzień", "ŚWIT": "świt, światła HI", "ZMIERZCH": "zmierzch, światła HI", "NOC": "noc, światła HI" };
// czujnik dla kierunku pasa i miejsca: [klucz pasa fizycznego, nr czujnika 0–2 od progu kierunku pierwszego w kluczu]
function rvrSlot(des, pos) {
  const d = rwyNorm(des), o = rwyNorm(recip(d)) || d, ends = [d, o].sort();
  if (!d) return [null, 0];
  return [ends.join("/"), pos === "MID" ? 1 : (pos === "END") === (d === ends[0]) ? 2 : 0];
}

// RVR dla kierunku pasa i miejsca (TDZ / MID / END) jak w prawdziwym AWOS:
// (a) grupa RVR z METAR dla tego pasa; (b) bez niej przy widzialności ≥ 1500 m (9999, CAVOK) P2000;
// (c) przy mniejszej widzialności symulacja (SYM): średnia 1 min z czujnika × podstawa z widzialności
// (albo średnia RVR innych pasów z METAR), tendencja U/D/N z różnicy średnich 5-minutowych ≥ 100 m.
// Wynik: { src: "METAR" | "P2000" | "SYM", list: [{ runway, value, trend }], tip } albo null (brak danych).
function rvrFor(p, des, pos, sims, phase) {
  const d = rwyNorm(des);
  if (!p || !d) return null;
  const met = (p.rvr || []).filter((x) => rwyNorm(x.runway) === d);
  if (met.length) {
    return { src: "METAR", list: met, tip: `RVR z METAR: ${met.map((x) => `R${x.runway}/${x.value}${x.trend || ""}`).join(" ")}` };
  }
  if (!has(p.visibility_m)) return null;
  if (p.cavok || p.visibility_m >= 1500) {
    return { src: "P2000", list: [{ runway: d, value: "P2000" }],
      tip: `RVR powyżej 2000 m (P2000): METAR bez grup RVR, ${p.cavok ? "CAVOK" : `widzialność ${p.visibility_m >= 9999 ? "10 km lub więcej" : p.visibility_m + " m"}`} (≥ 1500 m)` };
  }
  const [key, i] = rvrSlot(d, pos), q = sims?.get(key)?.s[i];
  if (!q || !q.hist.length) return null;
  const k = RVR_K[phase] || RVR_K["DZIEŃ"];
  const others = (p.rvr || []).map((x) => rvrNum(x.value)).filter(has);
  const base = others.length ? mean(others) : p.visibility_m * k;
  const value = rvrCode(base * mean(q.hist.slice(-60)));
  let trend = "";
  if (/^\d/.test(value) && q.hist.length >= 600) {
    const t = base * (mean(q.hist.slice(-300)) - mean(q.hist.slice(-600, -300)));
    trend = t >= 100 ? "U" : t <= -100 ? "D" : "N";
  }
  const from = others.length ? `ze średniej RVR innych pasów z METAR (${Math.round(base)} m)`
    : `z widzialności ${p.visibility_m} m × ${String(k).replace(".", ",")} (${RVR_TXT[phase] || "dzień: brak danych o słońcu"})`;
  return { src: "SYM", list: [{ runway: d, value, trend }],
    tip: `RVR ${pos} SYMULOWANY: METAR nie podaje RVR dla pasa ${d}; podstawa ${from}, średnia 1 min, tendencja z 10 min` };
}
const windStr = (dir, spd) => `${dir}/${pad(spd, 2)} KT`;

// Pasy pogrupowane w pary przeciwnych kierunków (11/29, 15/33…)
function runwayPairs(rwys) {
  const pairs = [];
  rwys.forEach((r) => {
    const key = Math.round(norm(r.heading) % 180 / 5) % 36;
    let g = pairs.find((q) => q.key === key);
    if (!g) pairs.push(g = { key, ends: [] });
    if (!g.ends.some((e) => e.designator === r.designator)) g.ends.push(r);
  });
  pairs.forEach((g) => g.ends.sort((a, b) => a.designator.localeCompare(b.designator, "en", { numeric: true })));
  return pairs;
}

// przeciwny kierunek pasa: 28L -> 10R, 36 -> 18
const recip = (des) => {
  const m = /^(\d{2})([LCR]?)$/.exec(des || "");
  return m ? pad(((+m[1] + 17) % 36) + 1, 2) + ({ L: "R", R: "L", C: "C" }[m[2]] || "") : null;
};

// --- tarcza wiatru (SVG 240×240) ----------------------------------------------------------------------
const C = 120, R = 94;
const pt = (deg, r) => [C + Math.sin(deg * RAD) * r, C - Math.cos(deg * RAD) * r].map((v) => +v.toFixed(1));

function dialStatic(rwys, selDes) {
  let s = `<circle class="bg" cx="${C}" cy="${C}" r="${R}"/>`;
  for (let a = 0; a < 360; a += 10) {
    const big = a % 30 === 0, [x1, y1] = pt(a, R), [x2, y2] = pt(a, R - (big ? 9 : 5));
    s += `<line class="tk${big ? " tk30" : ""}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  // pasy: wybrany szeroki, pozostałe cienkie; kreska progu i oznaczenie (poza tarczą) po stronie,
  // z której się ląduje / startuje na wybrany kierunek
  const near = [];
  let marks = "";
  runwayPairs(rwys).forEach((g) => {
    const on = g.ends.some((e) => e.designator === selDes), w = on ? 12 : 6;
    s += `<rect class="rw${on ? " on" : ""}" x="${C - w / 2}" y="${C - 72}" width="${w}" height="144" transform="rotate(${g.ends[0].heading} ${C} ${C})"/>`;
    if (!on) return;
    // pasy równoległe o tym samym kursie są w jednej parze: opisujemy tylko wybrany kierunek i jego przeciwny
    g.ends.filter((e) => e.designator === selDes || e.designator === recip(selDes)).forEach((e) => {
      const sel = e.designator === selDes, [x, y] = pt(e.heading + 180, 109), lw = e.designator.length * 7 + 8;
      near.push(e.heading + 180);
      if (sel) s += `<line class="thr" x1="${C - 8}" y1="${C + 64}" x2="${C + 8}" y2="${C + 64}" transform="rotate(${e.heading} ${C} ${C})"/>`;
      marks += `<g class="rl${sel ? " sel" : ""}"><rect x="${x - lw / 2}" y="${y - 8}" width="${lw}" height="16"/><text x="${x}" y="${y + 4}">${esc(e.designator)}</text></g>`;
    });
  });
  ["36", "09", "18", "27"].forEach((t, i) => {
    if (near.some((a) => Math.abs(sdiff(a, i * 90)) < 14)) return;
    const [x, y] = pt(i * 90, 109);
    s += `<text class="cd" x="${x}" y="${y + 4}">${t}</text>`;
  });
  return s + marks;
}
// łuk zakresu kierunku z ostatnich 2 min (zgodnie z ruchem wskazówek zegara od a do b)
function arcPath(a, b, r = 80) {
  let ext = norm(b - a);
  if (ext < 2) { a -= 1; ext = 2; }
  if (ext >= 358) return `M${C},${C - r} A${r},${r} 0 1 1 ${C},${C + r} A${r},${r} 0 1 1 ${C},${C - r}`;
  const [x1, y1] = pt(a, r), [x2, y2] = pt(a + ext, r);
  return `M${x1},${y1} A${r},${r} 0 ${ext > 180 ? 1 : 0} 1 ${x2},${y2}`;
}

const SYM = "wiatr chwilowy symulowany z METAR";
const SYM_RVR = "RVR symulowany z widzialności (METAR nie podaje RVR dla tego pasa)";
// pos: miejsce czujnika RVR (kolumny DEP / ARR pokazują RVR strefy przyziemienia)
const COLS_SPLIT = [{ k: "dep", title: "DEP", sel: "dep", lag: 0, pos: "TDZ" }, { k: "arr", title: "ARR", sel: "arr", lag: 2, pos: "TDZ" }];
const COLS_ONE = [{ k: "tdz", title: "TDZ", sel: "rwy", lag: 0, pos: "TDZ" }, { k: "mid", title: "MID", sel: "rwy", lag: 1, pos: "MID" },
  { k: "end", title: "END", sel: "rwy", lag: 3, pos: "END" }];
// czujniki wiatru wszystkich układów (opóźnienie podmuchu w s): przełączenie 1 PAS / 2 PASY nie kasuje historii 2 i 10 min
const LAGS = [...new Set([...COLS_SPLIT, ...COLS_ONE].map((c) => c.lag))];
const ROWS = [["inst", `INST <i class="aw-sym" title="${SYM}">SYM</i>`], ["avg", "2 MIN"], ["rng", "2 MIN MIN/MAX"], ["max", "10 MIN MAX"],
  ["hw", "HW / TW"], ["xw", "XW"], ["rvr", "RVR"], ["vis", "VIS"], ["cld", "CLD"], ["t", "T / TD"]];
const MODES = [["one", "1 PAS", "Jeden pas: wiatr i RVR w strefach TDZ / MID / END wybranego kierunku"],
  ["split", "2 PASY", "Dwa pasy: kolumny DEP i ARR, w każdej osobny wybór pasa (wiatr, składowe, RVR TDZ)"]];

// układ bez ręcznego wyboru: 2 PASY, gdy pas do lądowania i do startu w użyciu (ATIS albo sugestia) są różne
const autoMode = (u) => (u?.arr && u?.dep && u.arr !== u.dep ? "split" : "one");

// Pasy kolumn bez ręcznego wyboru: { rwy, arr, dep }, każdy { des, src, why } (src: ATIS / sugestia / auto).
// 1 PAS: pas do lądowania w użyciu. 2 PASY: pasy do lądowania i startu w użyciu. Gdy w użyciu jest jeden pas X
// (2 PASY wybrane ręcznie), drugi pas: z rozdziału ARR / DEP, który zawiera X, najpierw z sugestii systemu, potem
// z konfiguracji preferowanej teraz (pasy preferowane z AIP AD 2.21, EPWA ARR 33 / DEP 29), o ile drugi pas nie ma
// wiatru w plecy > 5 kt; inaczej X do lądowania, a do startu kierunek innego pasa z największą składową czołową
// z METAR. Lotnisko z jednym pasem: do startu przeciwny kierunek X (porównanie wiatru na obu końcach, starty pod
// kierunek lądowań).
function autoRunways(st) {
  const u = st?.runway_in_use || {}, rwys = st?.runways || [];
  const src = u.source === "ATIS" ? "ATIS" : "sugestia", why = `pas w użyciu (${u.source === "ATIS" ? "ATIS" : "sugestia systemu"})`;
  const x = u.arr || u.dep || rwys[0]?.designator || null, use = (des) => ({ des, src, why });
  if (u.arr && u.dep && u.arr !== u.dep) return { rwy: use(u.arr), arr: use(u.arr), dep: use(u.dep) };
  for (const [p, s, label] of [[st?.preferred, "sugestia", "sugestia systemu"], [st?.runway_config, "auto", "konfiguracja preferowana lotniska"]]) {
    if (!p?.arr || !p?.dep || p.arr === p.dep || ![p.arr, p.dep].includes(x)) continue;
    const r = rwys.find((q) => q.designator === (p.arr === x ? p.dep : p.arr));
    if (!r || (r.headwind ?? 0) < -5) continue;
    const two = (des) => (des === x ? use(des) : { des, src: s, why: `${label} ARR ${p.arr} / DEP ${p.dep}${p.reason ? ` (${p.reason})` : ""}` });
    return { rwy: use(x), arr: two(p.arr), dep: two(p.dep) };
  }
  const other = rwys.filter((r) => r.designator !== x && r.designator !== recip(x));
  const y = (other.length ? other : rwys.filter((r) => r.designator !== x))
    .sort((a, b) => (b.headwind ?? -99) - (a.headwind ?? -99) || Math.abs(a.crosswind ?? 0) - Math.abs(b.crosswind ?? 0))[0];
  const yWhy = other.length ? `drugi pas dobrany automatycznie: kierunek innego pasa z największą składową czołową (w użyciu tylko ${x})`
    : `lotnisko z jednym pasem: przeciwny kierunek ${x}`;
  return { rwy: use(x), arr: use(x), dep: y ? { des: y.designator, src: "auto", why: yWhy } : use(x) };
}

// Ręczny wybór lotniska (localStorage awos.<ICAO>): { mode: "one" | "split", rwy, arr, dep, base, t }; {} = automat.
// base: pasy w użyciu (ATIS albo sugestia), przy których wybrano (z nich też układ automatu), t: czas ostatniego kliknięcia.
const MAN_KEYS = ["mode", "rwy", "arr", "dep"];
const MAN_TTL = 12 * 3600000; // ręczny wybór najdłużej 12 h
const autoBase = (u) => `${u?.arr || "-"}/${u?.dep || "-"}`;

// Ręczny wybór po kliknięciu: k = "mode" (układ) albo "rwy" / "arr" / "dep" (pas kolumny) z wartością v, bez k = AUTO.
// Wybór taki jak automat to powrót do automatu. Zmiana układu kasuje pasy opuszczanego układu, więc zapamiętane jest
// zawsze tylko to, co widać na ekranie.
function manualPick(m, st, k, v, now = Date.now()) {
  if (!k) return {};
  const u = st?.runway_in_use, am = autoMode(u);
  const out = Object.fromEntries(MAN_KEYS.filter((x) => m?.[x]).map((x) => [x, m[x]]));
  if (k === "mode") {
    if (v !== (out.mode || am)) ["rwy", "arr", "dep"].forEach((x) => delete out[x]);
    if (v === am) delete out.mode; else out.mode = v;
  } else if (v === autoRunways(st)[k]?.des) delete out[k];
  else out[k] = v;
  return Object.keys(out).length ? { ...out, base: autoBase(u), t: now } : {};
}

// Zapamiętany ręczny wybór po wczytaniu statusu: wraca do automatu ({}), gdy zmieniły się pasy w użyciu z ATIS / sugestii
// albo minęło 12 h od kliknięcia (wybór z wczoraj nie przykrywa dzisiejszego ATIS); pas spoza listy i nieznany układ odpadają
function manualKeep(m, st, now = Date.now()) {
  if (!m || m.base !== autoBase(st?.runway_in_use) || !(now - m.t >= 0 && now - m.t < MAN_TTL)) return {};
  const ok = (k, v) => (k === "mode" ? MODES.some(([x]) => x === v) : (st?.runways || []).some((r) => r.designator === v));
  const out = Object.fromEntries(MAN_KEYS.filter((k) => ok(k, m[k])).map((k) => [k, m[k]]));
  return Object.keys(out).length ? { ...out, base: m.base, t: m.t } : {};
}

const colHtml = (c) => `<div class="aw-col" data-k="${c.k}">
  <div class="aw-h"><span>${c.title}</span><span class="rwy"></span></div>
  <div class="aw-rsel"></div>
  <div class="aw-dialbox"><svg class="aw-dial" viewBox="0 0 240 240">
    <g class="stat"></g><path class="arc"/>
    <g class="mean"><line x1="${C}" y1="${C - R + 3}" x2="${C}" y2="${C - 54}"/><polygon points="${C - 8},${C - 58} ${C + 8},${C - 58} ${C},${C - 40}"/></g>
    <circle class="inst" cx="${C}" cy="${C - R}" r="6"/>
    <g class="ctr"><rect x="${C - 38}" y="${C - 23}" width="76" height="46"/><text class="d" x="${C}" y="${C - 2}">///</text><text class="s" x="${C}" y="${C + 16}"></text></g>
  </svg></div>
  <table class="aw-rows">${ROWS.map(([k, l]) => `<tr class="r-${k}"><th>${l}</th><td></td></tr>`).join("")}</table>
</div>`;

// Tekst ATIS linijka po linijce jak w text_atis z VATSIM (element bywa też napisem z własnymi znakami nowej linii);
// puste linijki na początku i końcu pomijamy
function atisLines(lines) {
  const out = (Array.isArray(lines) ? lines : [lines]).flatMap((l) => String(l ?? "").split(/\r?\n/)).map((l) => l.trimEnd());
  while (out.length && !out[0].trim()) out.shift();
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out;
}

// Stacje ATIS lotniska z VATSIM (osobne kolumny dla EPWA_ATIS albo EPWA_A_ATIS / EPWA_D_ATIS)
function atisStations(atc, st, icao) {
  const ad = (atc?.airports || []).find((a) => a.icao === icao);
  const letterOf = (lines) => (/INFORMATION\s+([A-Z])\b/.exec(atisLines(lines).join(" ")) || [])[1] || "?";
  let list = (ad?.facilities?.ATIS || []).map((c) => ({ callsign: c.callsign, freq: c.frequency, letter: c.atis_code || letterOf(c.text_atis), lines: c.text_atis || [] }));
  if (!list.length && st?.atis) list = [{ callsign: st.atis.callsign, freq: st.atis.frequency, letter: st.atis.letter || "?", lines: st.atis.lines || [] }];
  const rank = (cs) => (/_D_ATIS$/.test(cs) ? 1 : /_A_ATIS$/.test(cs) ? 2 : 0);
  return list.sort((a, b) => rank(a.callsign) - rank(b.callsign) || a.callsign.localeCompare(b.callsign));
}

function notamValidity(n, now = Date.now()) {
  const d = (iso) => { const t = new Date(iso); return `${pad(t.getUTCDate(), 2)}.${pad(t.getUTCMonth() + 1, 2)} ${t.toISOString().slice(11, 16)}Z`; };
  const s = n.start ? Date.parse(n.start) : null, e = n.end ? Date.parse(n.end) : null;
  const end = n.perm ? "PERM" : e ? d(n.end) + (n.est ? " EST" : "") : "?";
  if (e && e < now) return ["expired", `WYGASŁ ${d(n.end)}`];
  if (s && s > now) return ["future", `OD ${d(n.start)} DO ${end}`];
  return ["active", n.perm ? "AKTYWNY · PERM" : `AKTYWNY DO ${end}`];
}

// --- widok --------------------------------------------------------------------------------------------

export function mountAwos(el, icao) {
  el.innerHTML = `<div class="awos">
    <div class="aw-top">
      <div class="aw-box aw-aero"><span class="k">AWOS<span class="aw-elev"></span></span><span class="v"><b class="icao">${esc(icao)}</b><span class="aw-nm"></span></span></div>
      <div class="aw-box aw-utc"><span class="k">UTC</span><span class="v aw-clock">--:--:--</span></div>
      <div class="aw-box aw-q"><span class="k">QNH hPa</span><span class="v qnh">–</span></div>
      <div class="aw-box aw-q"><span class="k">QFE hPa</span><span class="v qfe">–</span></div>
      <div class="aw-box aw-lvp">LVP</div>
      <div class="aw-box aw-vmc">–</div>
      <div class="aw-box aw-age"><span class="k">METAR</span><span class="v">–</span><span class="aw-sub"></span></div>
      <div class="aw-box aw-sun" hidden></div>
      <div class="aw-err"></div>
    </div>
    <div class="aw-mid">
      <div class="aw-windwrap"><div class="aw-winds"></div><div class="aw-wbar">
        <span class="aw-mode">${MODES.map(([m, l, tip]) => `<button data-mode="${m}" title="${tip}">${l}</button>`).join("")}</span>
        <button class="aw-auto" title="Automat: układ i pasy z ATIS (bez ATIS z sugestii systemu), 2 PASY przy różnych pasach do lądowania i startu.
Kliknij, żeby skasować ręczny wybór układu i pasów dla ${esc(icao)}; sam wygasa przy zmianie pasów w użyciu albo po 12 h.">AUTO</button><span class="aw-man" hidden>RĘCZNIE</span><div class="aw-simnote" title="VATSIM nie ma czujników wiatru ani RVR. Symulowany czujnik mierzy wiatr co 1 s, ekran odświeża się co ${WIND_REFRESH_S} s: INST = ostatni pomiar, 2 MIN = średnia z pomiarów z 2 min, 10 MIN MAX = największy pomiar z 10 min.
RVR: z METAR; gdy METAR nie podaje RVR dla pasa: przy widzialności ≥ 1500 m (9999, CAVOK) >2000 M, przy mniejszej RVR symulowany z widzialności (SYM). VIS, chmury i temperatura są z METAR."><b>SYM</b> ${SYM} · INST co ${WIND_REFRESH_S} s, średnia 2 min i maks. 10 min z pomiarów co 1 s</div></div></div>
      <div class="aw-atis"></div>
      <div class="aw-ovl" hidden><div class="aw-h"><span class="t"></span><button class="aw-x" title="Zamknij (Esc)">ZAMKNIJ ✕</button></div><div class="aw-ovl-body"></div></div>
    </div>
    <div class="aw-bot">
      ${[["wind", "WIND (METAR)"], ["temp", "TEMP"], ["dew", "DEW"], ["qnh", "QNH"], ["vis", "VIS"], ["cld", "CLOUDS"]]
        .map(([k, l]) => `<div class="aw-cell b-${k}"><span class="k">${l}</span><span class="v">–</span></div>`).join("")}
      <span class="aw-sp"></span>
      ${["METAR", "TAF", "NOTAM"].map((k) => `<button class="aw-btn" data-ovl="${k}">${k}</button>`).join("")}
    </div>
  </div>`;
  // wszystko w obrębie własnego .awos: el (.content) zostaje po destroy() i dostaje nowy widok, więc nasłuchy
  // i zapytania starej instancji nie mogą go dotykać
  const root = el.querySelector(".awos"), $ = (s) => root.querySelector(s);
  let st = null, stations = [], metarRaw, modeKey = "", field = null, sensors = new Map(), cols = [], auto = {}, ovl = null, notams = null;
  let dead = false, loading = 0, lastLoad = 0, loadErr = "", lastStep = 0, lastTick = 0, lastMin = -1, rvrSims = new Map();
  const letters = {}, changed = {};
  // ręczny wybór dla lotniska, zapamiętany w przeglądarce (manualPick / manualKeep); {} = automat
  const lsKey = `awos.${icao}`;
  let manual = {};
  try { const v = JSON.parse(lsGet(lsKey)); if (v && typeof v === "object" && !Array.isArray(v)) manual = v; } catch { /* brak albo zepsuta wartość */ }
  const saveManual = () => lsSet(lsKey, JSON.stringify(manual));

  const selOf = (c) => manual[c.sel] || auto[c.sel]?.des;
  const rwyOf = (des) => (st?.runways || []).find((r) => r.designator === des);
  const modeOf = () => manual.mode || autoMode(st?.runway_in_use);

  // jedna sekunda symulacji: wiatr (wspólne pole i czujniki) oraz czujniki RVR wszystkich pasów
  const stepAll = () => {
    if (field) { field.step(); sensors.forEach((s) => s.step()); }
    rvrSims.forEach((r) => r.step());
  };
  // nowe próbki od zera (nowy METAR albo przerwa): 10 min historii liczymy od razu
  const reseed = () => {
    const b = windBasis(st?.parsed);
    field = b ? new WindField(b) : null;
    sensors = new Map(field ? LAGS.map((lag) => [lag, new Sensor(field, lag)]) : []);
    rvrSims = new Map();
    (st?.runways || []).forEach((r) => { const [key] = rvrSlot(r.designator); if (key && !rvrSims.has(key)) rvrSims.set(key, new RvrRunway()); });
    for (let i = 0; i < HIST; i++) stepAll();
    lastStep = Date.now();
  };

  const buildCols = (defs) => {
    cols = defs.map((d) => ({ ...d }));
    $(".aw-winds").innerHTML = cols.map(colHtml).join("");
    cols.forEach((c) => {
      c.el = $(`.aw-col[data-k="${c.k}"]`);
      const q = (s) => c.el.querySelector(s);
      c.svg = q("svg"); c.stat = q(".stat"); c.arc = q(".arc"); c.mean = q(".mean"); c.inst = q(".inst"); c.d = q(".ctr .d"); c.s = q(".ctr .s");
      c.row = Object.fromEntries(ROWS.map(([k]) => [k, q(`.r-${k} td`)]));
      c.rvrTr = q(".r-rvr"); c.rvrTh = q(".r-rvr th");
    });
  };
  // kolumny wg układu (ręczny albo z ATIS); czujniki zostają, więc historia 2 i 10 min się nie zmienia
  const layout = () => {
    if (modeOf() === modeKey) return;
    modeKey = modeOf();
    buildCols(modeKey === "split" ? COLS_SPLIT : COLS_ONE);
  };

  // przełącznik 1 PAS / 2 PASY: zapalony układ na ekranie, podkreślony układ automatu; AUTO zapalone, gdy nic nie jest
  // zapamiętane ręcznie (zapamiętany bywa tylko wybór widocznego układu), inaczej obok napis RĘCZNIE
  const drawMode = () => {
    const u = st?.runway_in_use, am = autoMode(u), man = MAN_KEYS.some((k) => manual[k]);
    root.querySelectorAll(".aw-mode button").forEach((b) => {
      const use = !!st && b.dataset.mode === am;
      b.classList.toggle("active", b.dataset.mode === modeKey);
      b.classList.toggle("use", use);
      b.title = MODES.find(([m]) => m === b.dataset.mode)[2] + (use ? `\nUkład automatu (${u?.source === "ATIS" ? "ATIS" : "sugestia systemu"})` : "");
    });
    $(".aw-auto").classList.toggle("active", !!st && !man);
    $(".aw-man").hidden = !man;
    $(".aw-man").title = man ? `Układ albo pas wybrany ręcznie przy pasach w użyciu ARR ${u?.arr || "–"} / DEP ${u?.dep || "–"}`
      + ` (${u?.source === "ATIS" ? "ATIS" : "sugestia systemu"}), zapamiętany dla ${icao} w tej przeglądarce.\nWraca do automatu`
      + ` (teraz ${am === "split" ? "2 PASY" : "1 PAS"}) po kliknięciu AUTO, przy zmianie tych pasów albo o`
      + ` ${new Date(manual.t + MAN_TTL).toISOString().slice(11, 16)}Z (12 h od wyboru).` : "";
  };

  // części kolumny zależne od METAR i wyboru pasa (po każdym wczytaniu statusu i po kliknięciu pasa, układu albo AUTO)
  const drawColStatic = (c) => {
    const des = selOf(c), p = st?.parsed, def = auto[c.sel] || {}, man = !!manual[c.sel];
    c.el.querySelector(".rwy").innerHTML = des ? `RWY <b>${esc(des)}</b> <span class="src${man ? " man" : ""}"
      title="${esc(man ? `wybrany ręcznie; automat: ${def.des || "–"} (${def.src || "–"}), wraca kliknięcie podkreślonego pasa albo AUTO` : def.why || "")}">${man ? "ręcznie" : esc(def.src || "")}</span>` : "brak pasa";
    c.el.querySelector(".aw-rsel").innerHTML = runwayPairs(st?.runways || []).map((g) => `<span class="aw-pair">${g.ends.map((e) =>
      `<button data-rwy="${esc(e.designator)}" class="${e.designator === des ? "sel" : ""} ${e.designator === def.des ? "use" : ""}"
        title="${e.designator === def.des ? esc(def.why) : "pokaż wiatr dla tego kierunku"}">${esc(e.designator)}</button>`).join("")}</span>`).join("");
    c.stat.innerHTML = dialStatic(st?.runways || [], des);
    c.row.vis.textContent = visText(p);
    c.row.cld.innerHTML = cloudList(p).map(esc).join("<br>") || "–";
    const spread = has(p?.temperature) && has(p?.dewpoint) && p.temperature - p.dewpoint <= 2;
    c.row.t.innerHTML = p ? `${tempText(p.temperature)} / ${tempText(p.dewpoint)} °C${spread ? ` <span class="warn" title="mały spread: ryzyko mgły">Δ${p.temperature - p.dewpoint}</span>` : ""}` : "–";
  };

  // RVR kolumny (METAR, P2000 albo SYM; symulacja zmienia się razem z wiatrem)
  const drawRvr = (c) => {
    const r = rvrFor(st?.parsed, selOf(c), c.pos, rvrSims, st?.sun ? sunPhase(st.sun) : null);
    c.row.rvr.innerHTML = r ? r.list.map((x) => `R${esc(x.runway)} ${esc(rvrText(x))}`).join("<br>") : `<span class="dim">–</span>`;
    c.row.rvr.classList.toggle("warn", !!r && r.list.some((x) => !String(x.value).startsWith("P")));
    c.rvrTh.innerHTML = r?.src === "SYM" ? `RVR <i class="aw-sym" title="${SYM_RVR}">SYM</i>` : "RVR";
    c.rvrTr.title = r ? r.tip : "RVR: brak danych (METAR bez widzialności albo brak pasa)";
  };

  // wartości zmieniające się co WIND_REFRESH_S sekund
  const drawColLive = (c) => {
    const s = sensors.get(c.lag);
    drawRvr(c);
    if (!s || !s.buf.length) {
      ["inst", "avg", "rng", "max", "hw", "xw"].forEach((k) => { c.row[k].textContent = "///"; });
      c.svg.setAttribute("class", "aw-dial nodata"); c.d.textContent = "///"; c.s.textContent = "";
      return;
    }
    const b = field.b, cur = s.buf[s.buf.length - 1], a = s.stats();
    // CALM poniżej 1 kt; VRB jak w raportach ICAO: zmiany ≥ 60° przy średniej < 3 kt albo ≥ 180° przy każdej prędkości
    const spd2 = Math.round(a.spd), calm = spd2 === 0, vrb = !calm && (b.vrb || a.range >= 180 || (a.spd < 3 && a.range >= 60));
    const dir2 = (Math.round(a.dir / 10) * 10) || 360;
    const gusting = !calm && a.max10 - a.spd >= 10, gustNow = cur.s - a.spd >= 5;
    // "anim" od drugiego rysowania: wskazówki przesuwają się płynnie (awos.css), ale nie kręcą się od 0° po otwarciu
    c.svg.setAttribute("class", `aw-dial${c.anim ? " anim" : ""}${calm ? " calm" : ""}${vrb ? " vrb" : ""}${gusting ? " gst" : ""}${gustNow ? " gnow" : ""}`);
    c.row.inst.textContent = cur.s === 0 ? "CALM" : windStr(pad(cur.d, 3), cur.s);
    c.row.inst.classList.toggle("warn", gustNow);
    c.row.avg.innerHTML = calm ? "CALM" : `${windStr(vrb ? "VRB" : pad(dir2, 3), spd2)}${gusting ? ` <span class="warn">G${a.max10}</span>` : ""}`;
    c.row.rng.innerHTML = calm ? "–" : `<span>${vrb ? "VRB" : `${pad(Math.round(a.from) || 360, 3)}–${pad(Math.round(a.to) || 360, 3)}°`}</span> · <span>${a.smin}–${a.smax} KT</span>`;
    c.row.max.textContent = `${pad(a.max10, 2)} KT`;
    c.row.max.classList.toggle("warn", gusting);
    // składowe dla wybranego kierunku pasa ze średniej 2 min
    const r = rwyOf(selOf(c));
    if (r && !calm && !vrb) {
      // zaokrąglamy wartość bez znaku (jak w METEO i AERODROME), żeby -13,5 kt było TW 14 wszędzie
      const ang = (a.dir - r.heading) * RAD, rnd = (v) => Math.sign(v) * Math.round(Math.abs(v));
      const hw = rnd(a.spd * Math.cos(ang)), xw = rnd(a.spd * Math.sin(ang));
      c.row.hw.innerHTML = hw < 0 ? `<span class="${hw <= -5 ? "bad" : "warn"}">TW ${-hw} KT</span>` : `HW ${hw} KT`;
      c.row.xw.innerHTML = `<span class="${Math.abs(xw) >= 20 ? "warn" : ""}">${Math.abs(xw)} KT${xw > 0 ? " R" : xw < 0 ? " L" : ""}</span>`;
    } else {
      c.row.hw.textContent = c.row.xw.textContent = r ? (calm ? "CALM" : "VRB") : "–";
    }
    // tarcza: łuk zakresu 2 min, strzałka średniej, kropka = wiatr chwilowy
    c.arc.setAttribute("d", arcPath(a.from, a.to));
    // obrót o najkrótszy kąt (kąt narasta dalej zamiast skoku 359° -> 1°), żeby przejście CSS nie kręciło wskazówką dookoła
    const turn = (node, k, deg) => { c[k] = has(c[k]) ? c[k] + sdiff(deg, c[k]) : deg; node.style.transform = `rotate(${c[k].toFixed(1)}deg)`; };
    turn(c.mean, "meanA", a.dir); turn(c.inst, "instA", cur.d);
    c.anim = true;
    c.d.textContent = calm ? "CALM" : vrb ? "VRB" : pad(dir2, 3) + "°";
    c.s.innerHTML = calm ? "" : `${pad(spd2, 2)}${gusting ? `<tspan class="g">G${a.max10}</tspan>` : ""} KT`;
  };

  const drawAtis = () => {
    const list = stations, box = $(".aw-atis");
    const stack = cols.length + list.length > 4;
    box.className = "aw-atis" + (stack ? " stack" : "");
    box.style.flexGrow = stack ? 1 : Math.max(1, list.length);
    box.innerHTML = list.length ? list.map((a) => {
      const kind = /_D_ATIS$/.test(a.callsign) ? "DEP" : /_A_ATIS$/.test(a.callsign) ? "ARR" : "";
      const fresh = changed[a.callsign] && Date.now() - changed[a.callsign] < 180000;
      return `<div class="aw-atiscol"><div class="aw-h"><span>ATIS ${kind}</span>${fresh ? `<span class="aw-new">NOWA LITERA</span>` : ""}</div>
        <div class="aw-letter"><b>${esc(a.letter)}</b><span class="aw-stn">${a.callsign.split(" / ").map(esc).join("<br>")}<br><em>${esc(a.freq || "")}</em></span></div>
        <div class="aw-atistxt">${atisLines(a.lines).map((l) => `<div class="ln">${esc(l) || "&nbsp;"}</div>`).join("") || `<span class="dim">brak tekstu ATIS</span>`}</div></div>`;
    }).join("") : `<div class="aw-atiscol off"><div class="aw-h"><span>ATIS</span></div><div class="aw-letter"><b>–</b></div>
      <div class="aw-atistxt"><b>ATIS offline</b><br><span class="dim">${st?.network_error ? esc(st.network_error) : `Brak stacji ATIS ${esc(icao)} w sieci VATSIM.`}</span></div></div>`;
  };

  const drawTop = () => {
    const p = st.parsed, lvp = st.lvp || {}, sun = st.sun;
    $(".aw-aero .aw-nm").textContent = " " + (st.name || "");
    $(".aw-elev").textContent = has(st.elevation_ft) ? ` · ELEV ${Math.round(st.elevation_ft)} FT` : "";
    $(".qnh").textContent = p?.qnh ?? "–";
    $(".qfe").textContent = st.qfe ?? "–";
    const lv = $(".aw-lvp");
    // stan LVP z progami z AIP: w mocy, przygotowanie, odwołane (30 min), n/d = AIP nie przewiduje LVP
    const ls = lvpState(st.lvp);
    lv.className = "aw-box aw-lvp" + ({ lvp: " on", prep: " prep", cancel: " cancel", na: " na" }[ls.cls] || "");
    lv.innerHTML = { prep: "LVP<small>PRZYGOT.</small>", cancel: `LVP<small>ODWOŁ. ${esc(lvp.since || "")}</small>`,
      na: "LVP<small>N/D</small>" }[ls.cls] || "LVP";
    lv.title = [ls.label, ...(lvp.reasons || []), lvpTip(st.lvp)].filter(Boolean).join("\n");
    const v = vmc(p), vm = $(".aw-vmc");
    vm.className = "aw-box aw-vmc " + (v || "").toLowerCase();
    vm.innerHTML = `${v || "–"}${p?.flight_category ? `<small>${esc(p.flight_category)}</small>` : ""}`;
    vm.title = "VMC w CTR (SERA.5005): widzialność ≥ 5 km i podstawa chmur ≥ 1500 ft" + (p?.flight_category ? `\nkategoria METAR: ${p.flight_category}` : "");
    $(".aw-sun").innerHTML = sun ? `<span class="k">Słońce UTC · ${sunPhase(sun)}</span><span class="aw-sunrow">${[["BMCT", sun.civil_dawn, "początek zmierzchu cywilnego rano"],
      ["SR", sun.sunrise, "wschód"], ["SS", sun.sunset, "zachód"], ["EECT", sun.civil_dusk, "koniec zmierzchu cywilnego wieczorem"]]
      .map(([k, t, tip]) => `<span title="${tip}"><i>${k}</i>${t || "--:--"}</span>`).join("")}</span>` : "";
    $(".aw-sun").hidden = !sun;
    const errs = [loadErr, ...(st.errors || []), st.network_error].filter(Boolean);
    $(".aw-err").innerHTML = errs.map(esc).join("<br>");
    $(".aw-err").title = errs.join("\n");
  };
  const drawAge = () => {
    const p = st?.parsed, age = metarAge(p?.time), box = $(".aw-age");
    box.querySelector(".v").textContent = p?.time ? `${p.time.slice(2, 4)}:${p.time.slice(4, 6)}Z` : "–";
    box.querySelector(".aw-sub").textContent = age !== null ? ageText(age) + " temu" : "";
    box.classList.toggle("stale", age !== null && age > 70);
  };
  const drawBottom = () => {
    const p = st.parsed, set = (k, v) => { $(`.b-${k} .v`).textContent = v; };
    set("wind", metarWind(p)); set("temp", p ? tempText(p.temperature) + " °C" : "–"); set("dew", p ? tempText(p.dewpoint) + " °C" : "–");
    set("qnh", p?.qnh ? p.qnh + " hPa" : "–"); set("vis", visText(p)); set("cld", cloudList(p).join("  ") || "–");
    $(".b-cld .v").title = cloudList(p).join(", ");
  };

  // --- nakładka METAR / TAF / NOTAM
  // RVR bez grup w METAR: co wtedy pokazuje AWOS w kolumnach
  const rvrNone = (p) => (!has(p.visibility_m) ? "–" : p.cavok || p.visibility_m >= 1500
    ? "brak w METAR · widzialność ≥ 1500 m: AWOS >2000 M" : "brak w METAR · AWOS: RVR symulowany z widzialności (SYM)");
  const decoded = (p) => {
    const age = metarAge(p.time);
    const rows = [["Obserwacja", p.time ? `dzień ${p.time.slice(0, 2)}, ${p.time.slice(2, 4)}:${p.time.slice(4, 6)} UTC${age !== null ? ` (${ageText(age)} temu)` : ""}` : "–"],
      ["Wiatr", `${metarWind(p)}${has(p.wind_speed) ? ` · ${Math.round(p.wind_speed * 0.514)} m/s` : ""}`], ["Widzialność", visText(p)],
      ["RVR", (p.rvr || []).map((x) => `R${x.runway} ${rvrText(x)}`).join(" · ") || rvrNone(p)], ["Zjawiska", (p.weather || []).join(" ") || "–"],
      ["Chmury", cloudList(p).join(" · ")], ["Podstawa (ceiling)", has(p.ceiling_ft) ? p.ceiling_ft + " ft" : "–"],
      ["Temperatura / punkt rosy", `${tempText(p.temperature)} / ${tempText(p.dewpoint)} °C`], ["QNH / QFE", `${p.qnh ?? "–"} / ${st.qfe ?? "–"} hPa`],
      ["Trend", p.trend || "–"], ["Kategoria", p.flight_category || "–"]];
    return `<table class="aw-dec">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${esc(v)}</td></tr>`).join("")}</table>`;
  };
  const drawNotams = () => {
    if (!notams) return `<span class="dim">Ładowanie NOTAM…</span>`;
    if (notams.error) return `<span class="bad">${esc(notams.error)}</span>`;
    const list = [...notams.notams].sort((a, b) => (a.id || "").localeCompare(b.id || ""));
    return list.length ? `<div class="dim">${list.length} NOTAM · ${esc(icao)}</div>` + list.map((n) => {
      const [cls, txt] = notamValidity(n);
      return `<div class="aw-notam ${cls}"><div><b>${esc(n.id || "")}</b><span>${esc(txt)}</span>${n.schedule ? `<span class="dim">${esc(n.schedule)}</span>` : ""}</div><pre>${esc(n.raw)}</pre></div>`;
    }).join("") : `<span class="dim">Brak NOTAM-ów dla ${esc(icao)}.</span>`;
  };
  const drawOvl = () => {
    const box = $(".aw-ovl");
    box.hidden = !ovl;
    el.querySelectorAll("[data-ovl]").forEach((b) => b.classList.toggle("active", b.dataset.ovl === ovl));
    if (!ovl) return;
    box.querySelector(".t").textContent = `${ovl} · ${icao}`;
    const body = box.querySelector(".aw-ovl-body");
    if (ovl === "METAR") body.innerHTML = st?.metar ? `<div class="wx aw-raw">${colorize(st.metar)}</div>${decoded(st.parsed || {})}` : `<div class="wx">${colorize(null)}</div>`;
    else if (ovl === "TAF") body.innerHTML = `<div class="wx aw-raw">${st?.taf ? wxLines("taf", st.taf) : colorize(null)}</div>`;
    else body.innerHTML = drawNotams();
  };
  // NOTAM-y pobieramy od razu (liczba na przycisku) i odświeżamy przy otwarciu, jeśli mają ponad 10 min
  const loadNotams = async () => {
    try { notams = { ...(await api(`/api/notam/${icao}`)), t: Date.now() }; } catch (e) { notams = { error: e.message, t: Date.now() }; }
    if (dead) return;
    $("[data-ovl=NOTAM]").innerHTML = `NOTAM${notams.notams ? ` <b>${notams.notams.length}</b>` : ""}`;
    if (ovl === "NOTAM") drawOvl();
  };
  const openOvl = (k) => {
    ovl = ovl === k ? null : k;
    drawOvl();
    if (ovl === "NOTAM" && notams && Date.now() - notams.t > 600000) loadNotams();
  };

  const draw = () => {
    if (!st) {
      $(".aw-err").textContent = loadErr;
      $(".aw-winds").innerHTML = `<div class="aw-none"><b>Brak danych AWOS dla ${esc(icao)}</b><span>${esc(loadErr)}</span></div>`;
      return;
    }
    drawTop(); drawAge(); drawBottom(); drawMode();
    cols.forEach((c) => { drawColStatic(c); drawColLive(c); });
    drawAtis();
    if (ovl && ovl !== "NOTAM") drawOvl();
  };

  const load = async () => {
    loading = Date.now();
    const [s, a] = await Promise.allSettled([api(`/api/aerodromes/${icao}/status`), vatsimAtc()]);
    loading = 0;
    lastLoad = Date.now();
    if (dead) return;
    loadErr = s.status === "rejected" ? s.reason.message : "";
    if (loadErr) { if (st) drawTop(); else draw(); return; }
    st = s.value;
    stations = atisStations(a.status === "fulfilled" ? a.value : null, st, icao);
    stations.forEach((x) => {
      if (letters[x.callsign] && letters[x.callsign] !== x.letter) changed[x.callsign] = Date.now();
      letters[x.callsign] = x.letter;
    });
    auto = autoRunways(st);
    // ręczny wybór po zmianie pasów w użyciu, po 12 h albo gdy nie da się go pokazać (pasa nie ma na liście) wraca do automatu
    const keep = manualKeep(manual, st);
    if (Object.keys(keep).length !== Object.keys(manual).length) { manual = keep; saveManual(); }
    layout();
    if (st.metar !== metarRaw) { metarRaw = st.metar; reseed(); }
    draw();
  };
  // po kliknięciu układu albo AUTO: kolumny i ATIS od nowa bez nowej symulacji
  const redraw = () => {
    layout();
    drawMode();
    cols.forEach((c) => { drawColStatic(c); drawColLive(c); });
    drawAtis();
  };

  // kliknięcia: układ 1 PAS / 2 PASY, AUTO, wybór kierunku pasa (TDZ/MID/END razem, DEP i ARR osobno), nakładki
  root.addEventListener("click", (e) => {
    const mb = e.target.closest("button[data-mode]");
    if (mb || e.target.closest(".aw-auto")) {
      if (!st) return;
      // AUTO kasuje cały ręczny wybór; inny układ kasuje pasy opuszczanego układu
      manual = mb ? manualPick(manual, st, "mode", mb.dataset.mode) : {};
      saveManual();
      redraw();
      return;
    }
    const rb = e.target.closest("button[data-rwy]");
    if (rb) {
      const c = cols.find((x) => x.k === rb.closest(".aw-col")?.dataset.k);
      if (!c) return;
      manual = manualPick(manual, st, c.sel, rb.dataset.rwy);
      saveManual();
      drawMode();
      cols.filter((x) => x.sel === c.sel).forEach((x) => { drawColStatic(x); drawColLive(x); });
      return;
    }
    const ob = e.target.closest("button[data-ovl]");
    if (ob) openOvl(ob.dataset.ovl);
    if (e.target.closest(".aw-x")) { ovl = null; drawOvl(); }
  });
  const onKey = (e) => { if (e.key === "Escape" && ovl && root.offsetParent !== null) { ovl = null; drawOvl(); } };
  document.addEventListener("keydown", onKey);

  // jeden zegar co 1 s: czas UTC; co WIND_REFRESH_S s tyle sekund symulacji, ile minęło, i rysowanie wiatru;
  // dane co 60 s (tylko gdy widok jest na ekranie). Gdy widok jest ukryty albo przeglądarka wstrzymała kartę,
  // nie próbkujemy; po przerwie > 10 s nowa historia.
  const tick = () => {
    if (root.offsetParent === null) return;
    const now = new Date();
    let fresh = false;
    if (st) {
      if (now - lastTick > 10000) { reseed(); fresh = true; }
      else if (now - lastStep >= WIND_REFRESH_S * 1000 - 300) {
        for (let i = Math.min(HIST, Math.round((now - lastStep) / 1000)); i > 0; i--) stepAll();
        lastStep = now.getTime();
        fresh = true;
      }
    }
    lastTick = now.getTime();
    $(".aw-clock").textContent = now.toISOString().slice(11, 19);
    if (st) {
      if (fresh) cols.forEach(drawColLive);
      if (now.getUTCMinutes() !== lastMin) { lastMin = now.getUTCMinutes(); drawAge(); drawTop(); }
    }
    if ((!loading || now - loading > 90000) && now - lastLoad >= 60000) load();
  };
  const timer = setInterval(tick, 1000);
  load();
  tick();
  loadNotams();

  return {
    destroy() {
      dead = true;
      clearInterval(timer);
      document.removeEventListener("keydown", onKey);
    },
  };
}

// do testów symulacji wiatru i RVR oraz układu, pasów kolumn i ręcznego wyboru (node)
export const awosSim = { windBasis, WindField, Sensor, metarAge, RvrRunway, rvrFor, rvrCode, rvrSlot, rwyNorm, rvrText, atisLines,
  autoMode, autoRunways, manualPick, manualKeep };
