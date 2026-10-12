// DMAN/AMAN: czyste funkcje bez DOM (czas, sekwencja przylotów, rozkładanie etykiet na drabinie, kolory strumieni).
// Testowane w Node (backend/tests/test_dman.py). Widoki: dman.js (DMAN/AMAN) i aman.js (AMAN).

export const APPROACH_KT = 140;      // prędkość podejścia: FLOW (NM) → odstęp czasu między lądowaniami
export const DEFAULT_FLOW = 6.0;     // NM
export const SPANS = [10, 20, 30, 40, 60];

// "1015" → "10:15"; brak → "–"
export const hm = (t) => (t && /^\d{4}/.test(t) ? `${t.slice(0, 2)}:${t.slice(2, 4)}` : "–");
const pad = (n) => String(n).padStart(2, "0");
// sekundy od północy UTC dla czasu w ms
export const daySec = (ms) => (((ms / 1000) % 86400) + 86400) % 86400;
// zegar UTC z czasu w ms: "09:53:41" albo "09:53"
export function clock(ms, seconds = true) {
  const d = new Date(ms);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${seconds ? ":" + pad(d.getUTCSeconds()) : ""}`;
}
// "HHMM" (vIFF, plan lotu) jako sekundy względem teraz, w zakresie -12 h..+12 h (przejście przez północ)
export function relSec(t, nowMs) {
  if (!t || !/^\d{4}/.test(t)) return null;
  const s = (+t.slice(0, 2)) * 3600 + (+t.slice(2, 4)) * 60 - daySec(nowMs);
  return ((s + 43200) % 86400 + 86400) % 86400 - 43200;
}
// sekundy względem teraz → "HH:MM" (czas wyliczony, np. ETOT = TSAT + kołowanie)
export const hmAt = (nowMs, rel) => (rel === null || rel === undefined ? "–" : clock(nowMs + rel * 1000, false));

// --- odloty: czasy do drabin TSAT i TTOT
// TSAT-ekwiwalent: TSAT z vIFF (A-CDM), bez niego TOBT, potem EOBT (vIFF albo plan lotu); est = nie TSAT.
// TTOT-ekwiwalent: ATOT (po starcie), TTOT, CTOT, inaczej ETOT = (AOBT albo TSAT/TOBT/EOBT) + czas kołowania.
export function depTimes(s, nowMs, taxiDefault = 10) {
  const v = s.viff || {};
  const r = (t) => relSec(t, nowMs);
  const [tsatSrc, tsatT] = v.tsat ? ["TSAT", v.tsat] : v.tobt ? ["TOBT", v.tobt] : s.eobt ? ["EOBT", s.eobt] : [null, null];
  const tsat = r(tsatT);
  const taxi = v.taxi || taxiDefault;
  let ttotSrc = null, ttot = null;
  if (v.atot) [ttotSrc, ttot] = ["ATOT", r(v.atot)];
  else if (v.ttot) [ttotSrc, ttot] = ["TTOT", r(v.ttot)];
  else if (v.ctot) [ttotSrc, ttot] = ["CTOT", r(v.ctot)];
  else if (s.state !== "air") {
    const base = v.aobt ? r(v.aobt) : tsat;
    if (base !== null) [ttotSrc, ttot] = ["ETOT", base + taxi * 60];
  }
  const moving = s.state === "air" || (s.groundspeed || 0) >= 3;
  const offBlock = !!v.aobt || moving;
  // spóźniony: TSAT (prawdziwy, z A-CDM) minął ponad minutę temu, a samolot nie ruszył (brak AOBT i ruchu)
  const late = tsatSrc === "TSAT" && tsat !== null && tsat < -60 && !offBlock && !s.planned;
  return { tsat, tsatSrc, tsatT, ttot, ttotSrc, taxi, offBlock, late, ready: !!v.ready, airborne: s.state === "air" || !!v.atot };
}

// --- AMAN: sekwencja przylotów na pas (przybliżenie)
// arrs: [{key, runway, eta}] (eta w sekundach, dowolna wspólna skala); flowFor(runway) → NM.
// STA = max(ETA, poprzedni STA + FLOW / 140 kt) w kolejności ETA na każdym pasie; delay = STA − ETA (≥ 0).
export function sequence(arrs, flowFor = () => DEFAULT_FLOW, approachKt = APPROACH_KT) {
  const out = new Map();
  const byRwy = new Map();
  arrs.filter((a) => a.eta !== null && a.eta !== undefined).forEach((a) => {
    const k = a.runway || "";
    if (!byRwy.has(k)) byRwy.set(k, []);
    byRwy.get(k).push(a);
  });
  for (const [rwy, list] of byRwy) {
    list.sort((a, b) => a.eta - b.eta || String(a.key).localeCompare(String(b.key)));
    const gap = (flowFor(rwy) / approachKt) * 3600;
    let prev = null;
    list.forEach((a, i) => {
      const sta = prev === null ? a.eta : Math.max(a.eta, prev + gap);
      out.set(a.key, { sta, delay: sta - a.eta, order: i + 1, runway: rwy });
      prev = sta;
    });
  }
  return out;
}

// LND/SEP jak w tabelce AMAN: dla okien 60/30/10 min od teraz liczba lądowań (STA w oknie) i średni odstęp w NM
// między kolejnymi lądowaniami w oknie (odstęp czasu × 140 kt); mniej niż dwa lądowania → sep null.
export function lndSep(stas, now, windows = [60, 30, 10], approachKt = APPROACH_KT) {
  const s = [...stas].sort((a, b) => a - b);
  return windows.map((w) => {
    const inW = s.filter((t) => t >= now && t < now + w * 60);
    const gaps = inW.slice(1).map((t, i) => t - inW[i]);
    const sep = gaps.length ? (gaps.reduce((a, b) => a + b, 0) / gaps.length) * approachKt / 3600 : null;
    return { window: w, lnd: inW.length, sep: sep === null ? null : Math.round(sep * 10) / 10 };
  });
}

// --- rozkładanie etykiet bez nakładania: items [{y, h}] (y = pożądana góra etykiety, px); zwraca góry w tej samej
// kolejności. Etykiety zachowują kolejność; nachodzące łączą się w grupy ustawione możliwie blisko swoich miejsc
// (średnia przesunięć), grupy mieszczą się w [lo, hi] o ile wystarcza miejsca.
export function stack(items, lo = -Infinity, hi = Infinity, gap = 1) {
  const idx = items.map((_, i) => i).sort((a, b) => items[a].y - items[b].y || a - b);
  const groups = [];
  for (const i of idx) {
    const g = { ids: [i], offs: [0], size: items[i].h + gap, want: items[i].y };
    g.top = g.want;
    groups.push(g);
    for (;;) {
      const n = groups.length;
      const last = groups[n - 1];
      last.top = Math.min(Math.max(last.want, lo), hi - last.size + gap);
      if (n < 2) break;
      const prev = groups[n - 2];
      if (prev.top + prev.size <= last.top) break;
      // scalenie: przesunięcia w grupie, pożądana góra = średnia (y_k − przesunięcie_k)
      last.offs.forEach((o, k) => { prev.ids.push(last.ids[k]); prev.offs.push(prev.size + o); });
      prev.size += last.size;
      prev.want = prev.ids.reduce((a, id, k) => a + items[id].y - prev.offs[k], 0) / prev.ids.length;
      groups.pop();
    }
  }
  const out = new Array(items.length);
  groups.forEach((g) => {
    const top = Math.min(Math.max(g.want, lo), hi - g.size + gap);
    g.ids.forEach((id, k) => { out[id] = top + g.offs[k]; });
  });
  return out;
}

// --- kolory strumieni (punktów wejścia) jak przyciski w oknie AMAN dla KRL ACC
export const FIX_COLORS_EPWA = { SORIX: "#d8902c", NEPOX: "#33a83d", BIMPA: "#cfcfcf", LIMVI: "#6caee8", AGAVA: "#e0cd2c", LOGDA: "#d4423a" };
export const PALETTE = ["#d8902c", "#33a83d", "#cfcfcf", "#6caee8", "#e0cd2c", "#d4423a", "#b585e6", "#3fc4bb", "#e584b4", "#a3c24c"];
export const OTHER_COLOR = "#7d8fa3";
// {fix: kolor} dla punktów wejścia lotniska (iafs: alfabetycznie z backendu); EPWA kolorami z okna ACC
export function fixColors(icao, iafs = []) {
  if (icao === "EPWA" && iafs.every((f) => FIX_COLORS_EPWA[f])) return { ...FIX_COLORS_EPWA };
  const out = {};
  iafs.forEach((f, i) => { out[f] = PALETTE[i % PALETTE.length]; });
  return out;
}
// kolejność przycisków legendy: EPWA jak w oknie ACC, inne lotniska alfabetycznie
export function fixOrder(icao, iafs = []) {
  const ref = Object.keys(FIX_COLORS_EPWA);
  return icao === "EPWA" && iafs.every((f) => ref.includes(f)) ? ref.filter((f) => iafs.includes(f)) : [...iafs];
}
