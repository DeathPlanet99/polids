// AERODROME › RUCH › AMAN: okno sekwencji przylotów na wzór AMAN Zbliżania Warszawskiego (RWY, FLOW, drabina HH:MM
// z bieżącą godziną na dole, etykiety-strzałki z linią do czasu STA, tabela LND/SEP) i przyciski strumieni z okna ACC.
// Sekwencja to przybliżenie (opis w dymkach): ETA = ELDT z vIFF albo z pozycji, STA = max(ETA, poprzedni STA + FLOW/140 kt).
// Boczne panele są budowane raz (suwak, przyciski strumieni, pola); odświeżenie i wybór lotu zmieniają tylko wartości,
// więc przeciąganie suwaka i kliknięcie przycisku przy otwartym polu FLOW nie giną.
import { esc, lsGet, lsSet } from "./api.js";
import { APPROACH_KT, DEFAULT_FLOW, OTHER_COLOR, clock, fixColor, fixColors, fixOrder, hm, lndSep, overflow, sequence, stack } from "./dmancore.js";

const OTHER = "INNE";
const SEQ_HINT = "Sekwencja (przybliżenie): ETA = ELDT z vIFF albo z pozycji (odległość po ortodromie / GS, ostatnie 10 NM 140 kt); "
  + "na każdym pasie w kolejności ETA: STA = max(ETA, poprzedni STA + FLOW / 140 kt). Pasek pod odległością: zielony = na czas, "
  + "czerwony = samolot musi stracić tyle czasu (długość ∝ STA − ETA).";
const num = (v) => Math.round(v * 2) / 2;
const flowKey = (icao, rwy) => `dman.flow.${icao}.${rwy || "-"}`;

export function flowOf(icao, rwy) {
  const v = parseFloat(lsGet(flowKey(icao, rwy)));
  return Number.isFinite(v) && v >= 2 && v <= 20 ? v : DEFAULT_FLOW;
}

// przyloty do sekwencji: w powietrzu, z ELDT/ETA; eta w sekundach epoki (zegar serwera)
export function amanFlights(data) {
  if (!data) return [];
  return data.arrivals.filter((s) => s.state === "air" && s.eldt_s !== null && s.eldt_s !== undefined)
    .map((s) => ({ s, key: s.kind + ":" + s.callsign, runway: s.runway || data.runway_in_use?.arr || "", eta: data.now_ts + s.eldt_s }));
}
export const amanSequence = (data, icao) => sequence(amanFlights(data), (rwy) => flowOf(icao, rwy));

export function createAman(host, { icao, st, nowMs, getData }) {
  host.innerHTML = `<div class="am">
    <aside class="am-side am-l">
      <div class="am-slot rwy0"></div>
      <div class="am-fixes" title="Strumienie przylotów wg punktu wejścia (kolor obramowania etykiety). Kliknięcie: etykiety tego strumienia jako cienkie linie."></div>
      <div class="am-legend"><span><s class="ok"></s>na czas</span><span><s class="late"></s>musi stracić czas</span></div>
      <div class="am-fill"></div>
      <div class="am-slot flow0"></div>
    </aside>
    <div class="am-main" title="${esc(SEQ_HINT)}"><svg class="am-svg"></svg></div>
    <aside class="am-side am-r">
      <div class="am-slot top1"></div>
      <label class="am-hor" title="Horyzont drabiny: ile minut od teraz (u dołu) do góry">HORYZONT <input type="range" min="20" max="120" step="5"><span></span></label>
      <div class="am-slot info"></div>
      <div class="am-rest"></div>
      <div class="am-slot lnds"></div>
      <div class="am-slot flow1"></div>
    </aside>
  </div>`;
  const el = host.querySelector(".am");
  const svg = el.querySelector(".am-svg");
  const slot = (k) => el.querySelector(".am-slot." + k);
  let hor = +lsGet("dman.aman.hor");
  if (!(hor >= 20 && hor <= 120)) hor = 45;
  const horIn = el.querySelector(".am-hor input");
  horIn.value = hor;
  horIn.nextElementSibling.textContent = hor + "'";
  let thin = new Set();
  try { thin = new Set(JSON.parse(lsGet(`dman.thin.${icao}`) || "[]")); } catch { /* stara wartość */ }
  let editing = false, seq = new Map(), flights = [], fixKeys = "";

  // EPWA: strumienie = sześć punktów wejścia z okna ACC, reszta INNE. Inne lotniska: punkt wejścia z STAR albo z trasy,
  // każdy ze stałym kolorem (punkty STAR z pliku .ese z palety, pozostałe z koloru wyliczonego z nazwy punktu).
  const strict = icao === "EPWA";
  const colorsFor = (d) => fixColors(icao, d?.iafs || []);
  const fixOf = (s, colors) => {
    const f = s.entry_fix?.fix;
    return f && (colors[f] || !strict) ? f : OTHER;
  };
  const colorOf = (fix, colors) => (fix === OTHER ? OTHER_COLOR : fixColor(fix, colors));
  const runways = (d) => {
    const out = [];
    const add = (r) => { if (r && !out.includes(r)) out.push(r); };
    add(d.runway_in_use?.arr);
    flights.forEach((f) => add(f.runway));
    return out.length ? out : [""];
  };

  const box = (cls, label, val, attrs = "", title = "") => `<div class="am-box ${cls}"${attrs}${title ? ` title="${esc(title)}"` : ""}><i>${label}</i><b>${esc(val)}</b></div>`;
  const flowBox = (rwy) => box("flow", "FLOW", flowOf(icao, rwy).toFixed(1), ` data-rwy="${esc(rwy)}"`,
    `FLOW pasa ${rwy || "?"}: odstęp między lądowaniami w NM (domyślnie ${DEFAULT_FLOW.toFixed(1)}). Kliknij, żeby zmienić. ${SEQ_HINT}`);
  const setHtml = (node, html) => { if (node.innerHTML !== html) node.innerHTML = html; };

  const lndTable = (rwy) => {
    const stas = [...seq.values()].filter((x) => x.runway === rwy).map((x) => x.sta);
    const rows = lndSep(stas, nowMs() / 1000);
    return `<table class="am-lnd" title="${esc(`Pas ${rwy || "?"}: LND = lądowania w najbliższych 60/30/10 min (wg STA), SEP = średni odstęp w NM między kolejnymi lądowaniami (odstęp czasu × ${APPROACH_KT} kt).`)}">
      <tr><th>${esc(rwy || "–")}</th>${rows.map((r) => `<th>${r.window}</th>`).join("")}</tr>
      <tr><th>LND</th>${rows.map((r) => `<td>${r.lnd}</td>`).join("")}</tr>
      <tr><th>SEP</th>${rows.map((r) => `<td>${r.sep === null ? "–" : r.sep.toFixed(1)}</td>`).join("")}</tr></table>`;
  };

  const infoHtml = (d) => {
    const s = st.sel && [...d.arrivals, ...d.planned].find((x) => x.kind + ":" + x.callsign === st.sel);
    if (!s || s.kind !== "arr") return "";
    const q = seq.get(st.sel);
    const L = (k, v) => `<div><i>${k}</i><b>${esc(v ?? "–")}</b></div>`;
    return `<div class="am-info">${L("CALLSIGN", s.callsign)}${L("TYPE", s.aircraft)}${L("FROM", s.departure)}
      ${L("IAF", s.entry_fix?.fix)}${L("STAR", s.proc?.name)}${L("ELDT", s.eldt ? hm(s.eldt) + (s.eldt_source === "vIFF" ? " vIFF" : " POZ.") : null)}
      ${L("STA", q ? clock(q.sta * 1000, false) : null)}${L("DELAY", q ? `${Math.round(q.delay / 6) / 10} min` : null)}
      ${L("DIST", s.dist_nm !== null && s.dist_nm !== undefined ? s.dist_nm + " NM" : null)}${L("GS", s.groundspeed ? s.groundspeed + " kt" : null)}
      ${L("ALT", s.state === "air" && s.altitude ? s.altitude + " ft" : null)}${L("RWY", s.runway)}</div>`;
  };
  // przyloty bez sekwencji: jeszcze na lotnisku odlotu albo planowane (ELDT z vIFF, jeśli jest)
  const restHtml = (d) => {
    const rest = [...d.arrivals.filter((x) => x.away), ...d.planned.filter((x) => x.kind === "arr")];
    return `<div class="h">BEZ SEKWENCJI · ${rest.length}</div>${rest.map((x) => `<div class="r${st.sel === x.kind + ":" + x.callsign ? " sel" : ""}" data-k="${esc(x.kind + ":" + x.callsign)}"
      title="${esc(`${x.callsign} z ${x.departure || "?"}: ${x.planned ? "lot planowany" : "na ziemi na lotnisku odlotu"}${x.eldt ? `, ELDT ${hm(x.eldt)} (vIFF)` : ""}`)}">
      <span>${esc(x.callsign)}</span><span>${esc(x.departure || "")}</span><span>${x.eldt ? hm(x.eldt) : "–"}</span></div>`).join("")
      || `<div class="r none">Brak</div>`}`;
  };

  // przyciski strumieni: przebudowa tylko, gdy zmienia się zestaw punktów; inaczej tylko stan (pełne / cienkie)
  const fixButtons = (d, colors) => {
    const order = fixOrder(icao, d.iafs || []);
    const extra = strict ? [] : [...new Set(flights.map((f) => fixOf(f.s, colors)))].filter((f) => f !== OTHER && !order.includes(f)).sort();
    const keys = [...order, ...extra, OTHER];
    const wrap = el.querySelector(".am-fixes");
    if (keys.join(" ") !== fixKeys) {
      fixKeys = keys.join(" ");
      wrap.innerHTML = keys.map((f) => `<button data-fix="${esc(f)}" style="--fc:${colorOf(f, colors)}"${extra.includes(f) ? ' data-src="route"' : ""}>${esc(f)}</button>`).join("");
    }
    wrap.querySelectorAll("button[data-fix]").forEach((b) => {
      const f = b.dataset.fix, off = thin.has(f);
      b.className = off ? "thin" : "full";
      const what = f === OTHER ? (strict ? "Inne wejścia (spoza sześciu punktów okna ACC) i nieznane" : "Przyloty bez punktu wejścia w trasie")
        : b.dataset.src ? `Wejście ${f} (ostatni punkt trasy albo STAR spoza pliku .ese)` : `Strumień ${f}`;
      b.title = `${what}: ${off ? "tylko cienka linia ze znakiem" : "pełne etykiety"}. Kliknij, żeby przełączyć.`;
    });
  };

  const sides = (d) => {
    const colors = colorsFor(d);
    const rw = runways(d);
    const src = d.runway_in_use?.source === "ATIS" ? `z ATIS ${d.atis?.letter || ""}` : "sugestia systemu (brak ATIS)";
    setHtml(slot("rwy0"), box("rwy", "RWY", rw[0] || "–", "", `Pas do lądowania ${src}`));
    fixButtons(d, colors);
    const n = flights.length;
    setHtml(slot("top1"), rw[1] ? box("rwy", "RWY", rw[1], "", "Drugi pas do lądowania") : box("cnt", "PRZYLOTY", n, "", `Przyloty ${icao} w sekwencji: ${n}`));
    if (!editing) {
      setHtml(slot("flow0"), flowBox(rw[0]));
      setHtml(slot("flow1"), rw[1] ? flowBox(rw[1]) : "");
    }
    setHtml(slot("info"), infoHtml(d));
    const rest = el.querySelector(".am-rest");
    const top = rest.scrollTop;
    setHtml(rest, restHtml(d));
    rest.scrollTop = top;
    setHtml(slot("lnds"), rw.map(lndTable).join(""));
  };

  const draw = () => {
    const d = getData();
    if (!d) return;
    const W = svg.clientWidth, H = svg.clientHeight;
    if (W < 200 || H < 120) return;
    const now = nowMs();
    const nowS = now / 1000;
    const colors = colorsFor(d);
    const LW = 86, top = 8, bot = H - 52;
    const ppm = (bot - top) / hor;
    const y = (abs) => bot - ((abs - nowS) / 60) * ppm;
    let s = `<rect class="lad" x="1" y="0" width="${LW - 2}" height="${bot}"/>
      <line class="ladl" x1="1" y1="0" x2="1" y2="${bot}"/><line class="ladl" x1="${LW - 1}" y1="0" x2="${LW - 1}" y2="${bot}"/>`;
    // podziałka co minutę zegara UTC, opis co 5 min
    const m0 = Math.ceil(nowS / 60), m1 = Math.floor((nowS + hor * 60) / 60);
    for (let m = m0; m <= m1; m++) {
      const yy = num(y(m * 60));
      if (yy < top - 2) continue;
      const big = m % 5 === 0;
      const len = big ? 9 : 5;
      s += `<line class="tk" x1="1" y1="${yy}" x2="${1 + len}" y2="${yy}"/><line class="tk" x1="${LW - 1 - len}" y1="${yy}" x2="${LW - 1}" y2="${yy}"/>`;
      if (big) s += `<text class="hh" x="${LW / 2}" y="${yy + 4}" text-anchor="middle">${clock(m * 60000, false)}</text>`;
    }
    s += `<line class="nowl" x1="1" y1="${bot}" x2="${LW - 1}" y2="${bot}"/>
      <rect class="tbox" x="1" y="${bot + 1}" width="${LW - 2}" height="${H - bot - 3}"/>
      <text class="tnow" x="${LW / 2}" y="${bot + 32}" text-anchor="middle">${clock(now, false).replace(":", " ")}</text>`;
    // etykiety: pełne (strzałka) albo cienka linia ze znakiem dla wyłączonych strumieni
    const rw = runways(d);
    const LH = 14, GAP = 3;
    const list = flights.map((f) => {
      const q = seq.get(f.key);
      const fix = fixOf(f.s, colors);
      return { f, q, fix, color: colorOf(fix, colors), thin: thin.has(fix), ri: Math.max(0, rw.indexOf(f.runway)) };
    }).filter((t) => t.q);
    let shown = list.filter((t) => t.q.sta <= nowS + hor * 60);
    const beyond = list.length - shown.length;
    // za dużo etykiet na wysokość okna: najpierw niższe etykiety (16 px), potem najpóźniejsze odpadają i są liczone w "▲"
    const hi = bot - 2;
    let lo = top - 4, TH = 22, drop = new Set();
    const need = (th) => shown.reduce((a, t) => a + (t.thin ? LH : th) + GAP, 0) - GAP;
    // "▲ n" ma u góry własny pas nad etykietami, na prawo od linii prowadzących
    if (beyond) lo = top + 12;
    if (need(TH) > hi - lo) TH = 16;
    if (need(TH) > hi - lo) {
      lo = top + 12;
      drop = overflow(shown.map((t) => ({ t: t.q.sta, h: t.thin ? LH : TH })), hi - lo, GAP);
      shown = shown.filter((_, i) => !drop.has(i));
    }
    const later = beyond + drop.size;
    const cmp = TH < 22;
    const items = shown.map((t) => { const h = t.thin ? LH : TH; return { y: Math.max(y(t.q.sta), top) - h / 2, h }; });
    const tops = stack(items, lo, hi, GAP);
    const tagW = Math.max(200, Math.min(290, W - LW - 120));
    shown.forEach((t, i) => {
      const h = items[i].h, yT = tops[i], mid = yT + h / 2, y0 = Math.min(Math.max(y(t.q.sta), top), bot);
      const tip = LW + 46 + t.ri * Math.min(150, Math.max(0, W - LW - 46 - tagW - 30));
      const x0 = tip + (cmp ? 20 : 26), x1 = x0 + tagW;
      const k = t.f.key;
      const sel = st.sel === k;
      const delayMin = t.q.delay / 60;
      const title = `${t.f.s.callsign} ${t.f.s.aircraft || ""} z ${t.f.s.departure || "?"} · wejście ${t.fix === OTHER ? t.f.s.entry_fix?.fix || "?" : t.fix}
STA ${clock(t.q.sta * 1000, false)} · ETA ${clock(t.f.eta * 1000, false)} (${t.f.s.eldt_source === "vIFF" ? "ELDT vIFF" : "z pozycji"}) · ${delayMin >= 0.5 ? `stracić ${delayMin.toFixed(1)} min` : "na czas"}
${t.f.s.dist_nm ?? "?"} NM · GS ${t.f.s.groundspeed ?? "?"} kt · RWY ${t.f.runway || "?"} · nr ${t.q.order}
Klik: szczegóły · dwuklik: mapa`;
      if (t.thin) {
        const cx = (x0 + x1) / 2;
        s += `<g class="am-thin${sel ? " sel" : ""}" data-k="${esc(k)}" style="--fc:${t.color}"><title>${esc(title)}</title>
          <rect class="hit" x="${x0}" y="${num(yT)}" width="${tagW}" height="${h}"/>
          <line x1="${x0 + 20}" y1="${num(mid)}" x2="${cx - 48}" y2="${num(mid)}"/><line x1="${cx + 48}" y1="${num(mid)}" x2="${x1 - 20}" y2="${num(mid)}"/>
          <text x="${cx}" y="${num(mid + 4)}" text-anchor="middle">${esc(t.f.s.callsign)}</text></g>`;
        return;
      }
      s += `<path class="am-lead" style="--fc:${t.color}" d="M${LW} ${num(y0)}h6L${num(tip - 8)} ${num(mid)}H${tip}"/>`;
      const bar = delayMin >= 0.5 ? Math.min(tagW * 0.35, 6 + delayMin * 8) : 16;
      const base = yT + (cmp ? 12 : 16);
      s += `<g class="am-tag${sel ? " sel" : ""}${cmp ? " cmp" : ""}" data-k="${esc(k)}" style="--fc:${t.color}"><title>${esc(title)}</title>
        <path class="bg" d="M${tip} ${num(mid)}L${x0} ${num(yT)}H${x1}V${num(yT + h)}H${x0}Z"/>
        <text class="cs" x="${x0 + 6}" y="${num(base)}">${esc(t.f.s.callsign)}</text>
        <text class="sta" x="${x0 + tagW * 0.62}" y="${num(base - 0.5)}" text-anchor="end">${clock(t.q.sta * 1000, false)}</text>
        <text class="nm" x="${x1 - 6}" y="${num(base)}" text-anchor="end">${esc(t.f.s.dist_nm ?? "")}</text>
        <rect class="dl ${delayMin >= 0.5 ? "late" : "ok"}" x="${num(x1 - 2 - bar)}" y="${num(yT + h - (cmp ? 2.5 : 3.5))}" width="${num(bar)}" height="${cmp ? 2 : 3}"/></g>`;
    });
    if (later) {
      const why = [beyond ? `${beyond} później niż ${hor}' (zmień HORYZONT)` : "", drop.size ? `${drop.size} nie mieści się w oknie (najpóźniejsze)` : ""].filter(Boolean);
      s += `<text class="more" x="${LW + 46 + (cmp ? 20 : 26) + 6}" y="${top + 8}"><title>${esc(why.join("; "))}</title>▲ ${later} ${beyond && !drop.size ? `później niż ${hor}'` : "więcej"}</text>`;
    }
    if (!list.length) s += `<text class="empty" x="${LW + (W - LW) / 2}" y="${(top + bot) / 2}" text-anchor="middle">${d.network_error ? "Brak danych z sieci VATSIM" : "Brak przylotów w powietrzu"}</text>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = s;
  };

  const render = (d) => {
    flights = amanFlights(d);
    seq = sequence(flights, (rwy) => flowOf(icao, rwy));
    sides(d);
    draw();
  };

  // --- zdarzenia własne okna AMAN (wybór lotu i dwuklik obsługuje dman.js)
  const editFlow = (b) => {
    const rwy = b.dataset.rwy;
    editing = true;
    const v = b.querySelector("b");
    v.innerHTML = `<input type="number" min="2" max="20" step="0.5" value="${flowOf(icao, rwy).toFixed(1)}">`;
    const inp = v.querySelector("input");
    inp.focus();
    inp.select();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      const x = parseFloat(String(inp.value).replace(",", "."));
      if (save && Number.isFinite(x)) lsSet(flowKey(icao, rwy), String(Math.min(20, Math.max(2, Math.round(x * 2) / 2))));
      editing = false;
      render(getData());
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); else if (e.key === "Escape") finish(false); });
    inp.addEventListener("blur", () => finish(true));
  };
  el.addEventListener("click", (e) => {
    const fb = e.target.closest(".am-box.flow");
    if (fb && !editing) return editFlow(fb);
    const fx = e.target.closest("button[data-fix]");
    if (fx) {
      const f = fx.dataset.fix;
      if (thin.has(f)) thin.delete(f); else thin.add(f);
      lsSet(`dman.thin.${icao}`, JSON.stringify([...thin]));
      render(getData());
    }
  });
  horIn.addEventListener("input", () => {
    hor = +horIn.value;
    lsSet("dman.aman.hor", String(hor));
    horIn.nextElementSibling.textContent = hor + "'";
    draw();
  });

  return {
    render: (d) => { if (d) render(d); },
    tick: () => draw(),
    destroy: () => { el.remove(); },
  };
}
