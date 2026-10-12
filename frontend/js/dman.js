// AERODROME › RUCH: DMAN/AMAN na wzór ekranu z EPWA (lista odlotów wg TSAT, drabina TSAT, szczegóły lotu, drabiny TTOT
// i ELDT, lista przylotów wg ETA) oraz okno AMAN (aman.js). Paski EFES są w zakładce STRIP (strips.js).
// Lotnisko bez A-CDM (data.cdm false): wszędzie EOBT zamiast TSAT i ETOT zamiast TTOT (depLabels, depTimes w dmancore.js);
// zanim pierwsza odpowiedź powie, czy lotnisko ma A-CDM, nazwy tych czasów są puste.
// mountDman(el, icao, { onUpdate, view, onView }) → { resume(), setView(v), destroy() } (jak mountStrips plus widok).
// Widok "dman" (DMAN/AMAN) albo "aman": opts.view wybiera go z adresu (#aerodrome/EPWA/aman), inaczej ostatnio używany
// (lsGet). onView(v, { replace }) zgłasza widok gospodarzowi, który zapisuje go w adresie: kliknięcie przycisku widoku
// to krok do cofnięcia (←), a przy montowaniu i resume() widok tylko uzupełnia bieżący wpis (replace).
// Dane: GET /api/aerodromes/{icao}/strips?dman=1 (paski + ELDT z vIFF, punkt wejścia, rejestracja; services/dman.py).
// Czasy na drabinach liczymy od zegara serwera (now_ts), a etykiety przesuwają się co 2 s.
import { api, esc, lsGet, lsSet, navigate } from "./api.js";
import { SPANS, clock, depLabels, depTimes, hm, hmAt, overflow, relSec, stack } from "./dmancore.js";
import { amanSequence, createAman } from "./aman.js";
import { STRIP_AIRPORTS } from "./tabs/strip.js";

const REFRESH_MS = 30000;
const LADDER_MS = 2000;
const TAG_H = 17;
const VIEWS = ["dman", "aman"];
const STAND = "–";   // stanowiska nie ma w danych VATSIM ani vIFF
const RWY_SRC = { ATIS: "z ATIS", SUGGESTED: "sugestia systemu (brak pasa z ATIS)" };
const SRC_TXT = {
  TSAT: "TSAT z vIFF (A-CDM)", TOBT: "brak TSAT: TOBT z vIFF", EOBT: "brak TSAT: EOBT (vIFF albo plan lotu)",
  ATOT: "ATOT: rzeczywisty start (vIFF)", TTOT: "TTOT z vIFF (A-CDM)", CTOT: "brak TTOT: CTOT (slot ATFM) z vIFF",
  ETOT: "brak TTOT: ETOT = off-block (AOBT, TSAT, TOBT albo EOBT) + czas kołowania",
  vIFF: "ELDT z vIFF", POS: "ETA z pozycji: odległość po ortodromie / GS, ostatnie 10 NM 140 kt, 30–10 NM najwyżej 220 kt",
};
// lotnisko bez A-CDM: EOBT i ETOT to właściwe wartości drabin, nie zastępstwo za TSAT i TTOT
const SRC_NOCDM = {
  EOBT: "EOBT z vIFF albo z planu lotu (lotnisko bez A-CDM)", ETOT: "ETOT = off-block (AOBT albo EOBT) + czas kołowania",
  CTOT: "CTOT (slot ATFM) z vIFF, później niż ETOT albo bez ETOT",
};

const dash = (v) => (v === null || v === undefined || v === "" ? "–" : String(v));
const keyOf = (s) => s.kind + ":" + s.callsign;
const num = (v) => Math.round(v * 2) / 2;

// skala drabiny: span minut w przód (do góry), ćwierć tego w przeszłość (pod linią 00, przygaszone)
function geom(H, span) {
  const past = Math.max(2, span * 0.25);
  const top = 10, bot = H - 8;
  const ppm = (bot - top) / (span + past);
  const y = (sec) => top + (span - sec / 60) * ppm;
  return { top, bot, past, span, ppm, y, yNow: y(0) };
}

const svgText = (x, y, s, cls, anchor = "start") => `<text x="${num(x)}" y="${num(y)}" class="${cls}" text-anchor="${anchor}">${esc(s)}</text>`;

// oś z podziałką: minuty względem teraz (-10, -20 … jak na ekranie DMAN, 00 = teraz) i pod nimi godzina UTC.
// Zwraca [tło i oś, opisy]; opisy rysujemy na końcu na czarnym tle, żeby linie prowadzące etykiet ich nie przekreślały.
function scaleSvg(g, now, { W, axisX, labelX, barX, barW, winTitle, labelBg = false }) {
  let s = `<rect class="pastbg" x="0" y="${num(g.yNow)}" width="${W}" height="${num(g.bot + 8 - g.yNow)}"/>`;
  s += `<rect class="win" x="${barX}" y="${num(g.y(300))}" width="${barW}" height="${num(g.y(-300) - g.y(300))}"><title>${esc(winTitle)}</title></rect>`;
  s += `<line class="axis" x1="${axisX}" y1="0" x2="${axisX}" y2="${num(g.bot + 8)}"/>`;
  let labels = "";
  const step = g.span <= 10 ? 2 : g.span <= 20 ? 5 : 10;
  for (let m = Math.ceil(-g.past); m <= Math.floor(g.span); m++) {
    const y = num(g.y(m * 60));
    const len = m % 5 === 0 ? 8 : 4;
    s += `<line class="tick" x1="${axisX - len}" y1="${y}" x2="${axisX}" y2="${y}"/>`;
    if (m > 0 && m % step === 0) {
      labels += (labelBg ? `<rect class="lblbg" x="${labelX - 30}" y="${y - 7}" width="31" height="24"/>` : "")
        + svgText(labelX, y + 4, "-" + m, "lbl", "end") + svgText(labelX, y + 14, clock(now + m * 60000, false), "lbl2", "end");
    }
  }
  labels += svgText(labelX, num(g.yNow) + 4, "00", "lbl zero", "end");
  return [s + `<line class="now" x1="0" y1="${num(g.yNow)}" x2="${W}" y2="${num(g.yNow)}"/>`, labels];
}

// etykiety jednej strony osi: bez nakładania (stack), krótka linia prowadząca do dokładnego czasu na osi
// Znacznik "▲ n" ma u góry własny pas (14 px), więc nie nachodzi na najwyższą etykietę; etykiety, których jest więcej,
// niż mieści wysokość drabiny, odpadają od najpóźniejszej i też są liczone w "▲ n".
function tagsSvg(list, g, { axisX, x0, x1, right, sel, empty }) {
  const w = x1 - x0;
  let shown = list.filter((t) => t.rel !== null && t.rel / 60 <= g.span && t.rel / 60 >= -g.past);
  const beyond = list.filter((t) => t.rel !== null && t.rel / 60 > g.span).length;
  const hi = g.bot + 6;
  const fit = (lo) => overflow(shown.map((t) => ({ t: t.rel, h: TAG_H })), hi - lo, 2);
  let drop = fit(2);
  const lo = beyond || drop.size ? 14 : 2;
  if (lo !== 2) drop = fit(lo);
  shown = shown.filter((_, i) => !drop.has(i));
  const later = beyond + drop.size;
  const tops = stack(shown.map((t) => ({ y: g.y(t.rel) - TAG_H / 2, h: TAG_H })), lo, hi, 2);
  let s = "";
  const wide = w >= 150;
  const nar = w < 92 ? "nar2" : w < 104 ? "nar" : "";   // wąska drabina (okno ≤ 1300 px): mniejsze pismo, callsign nie dotyka czasu
  shown.forEach((t, i) => {
    const y0 = g.y(t.rel), top = tops[i], mid = top + TAG_H / 2;
    const ex = right ? x0 : x1;   // krawędź etykiety od strony osi
    const dir = right ? 1 : -1;
    s += `<path class="lead${t.cls.includes("arr") ? " arr" : ""}" d="M${axisX} ${num(y0)}h${dir * 7}L${num(ex - dir * 5)} ${num(mid)}H${ex}"/>`;
    const cls = ["dm-tag", ...t.cls, sel === t.key ? "sel" : "", t.rel < 0 ? "past" : "", nar].join(" ");
    s += `<g class="${cls}" data-k="${esc(t.key)}"><title>${esc(t.title)}</title>
      <rect class="bg" x="${x0}" y="${num(top)}" width="${w}" height="${TAG_H}"/>
      ${t.csCls ? `<rect class="csbg ${t.csCls}" x="${x0}" y="${num(top)}" width="${Math.min(66, w / 2)}" height="${TAG_H}"/>` : ""}
      ${svgText(x0 + 4, top + 12.5, t.cs, "cs")}
      ${wide ? svgText(x1 - 44, top + 12.5, t.mid, "mid", "end") : ""}
      ${svgText(x1 - 4, top + 12.5, t.time, "tm" + (t.est ? " est" : ""), "end")}</g>`;
  });
  if (later) {
    const why = [beyond ? `${beyond} później niż za ${g.span} min (zmień zakres przyciskami 10'–60')` : "",
      drop.size ? `${drop.size} nie mieści się na wysokości drabiny (najpóźniejsze)` : ""].filter(Boolean).join("; ");
    s += `<g class="more"><title>${esc(why)}</title>${svgText(right ? x0 + 2 : x1 - 2, 10, `▲ ${later}`, "more", right ? "start" : "end")}</g>`;
  }
  if (!shown.length) {
    // wąska drabina (np. okno 1366 px): komunikat w dwóch wierszach, żeby nie wychodził poza krawędź
    const words = empty.split(" ");
    const lines = empty.length * 6.3 > w - 4 ? [words.slice(0, 2).join(" "), words.slice(2).join(" ")] : [empty];
    lines.forEach((ln, i) => { s += svgText((x0 + x1) / 2, g.y(g.span * 30) + i * 13, ln, "empty", "middle"); });
  }
  return s;
}

export function mountDman(el, icao, { onUpdate, view, onView } = {}) {
  el.innerHTML = `<div class="dm">
    <div class="dm-top">
      <button data-view="dman" title="Odloty i przyloty: drabiny TSAT, TTOT i ELDT (lotnisko bez A-CDM: EOBT i ETOT)">DMAN/AMAN</button>
      <button data-view="aman" title="Sekwencja przylotów na pas (AMAN)">AMAN</button>
      <button data-act="reload" title="Pobierz dane teraz (same odświeżają się co 30 s)">ODŚWIEŻ</button>
      <button data-act="brighter" title="Jaśniejszy ekran">JAŚNIEJ</button>
      <button data-act="darker" title="Ciemniejszy ekran">CIEMNIEJ</button>
      <span class="dm-msg">Ładowanie…</span>
    </div>
    <div class="dm-body"></div>
    <div class="dm-note"><span>Ładowanie…</span></div>
  </div>`;
  const root = el.querySelector(".dm");
  const body = root.querySelector(".dm-body");
  const msg = root.querySelector(".dm-msg");
  const note = root.querySelector(".dm-note");
  const intOr = (v, d, ok) => (ok(+v) ? +v : d);
  const st = {
    view: VIEWS.includes(view) ? view : lsGet("dman.view") === "aman" ? "aman" : "dman",
    sel: null,
    span: { dep: intOr(lsGet("dman.span.dep"), 30, (v) => SPANS.includes(v)), arr: intOr(lsGet("dman.span.arr"), 30, (v) => SPANS.includes(v)) },
    sort: { dep: lsGet("dman.sort.dep") || "t:1", arr: lsGet("dman.sort.arr") || "t:1" },
    bright: intOr(lsGet("dman.bright"), 0, (v) => Number.isInteger(v) && v >= -4 && v <= 4),
  };
  let data = null, offset = 0, byKey = new Map(), timer = null, clockTimer = null, dead = false, seq = 0, ticks = 0, aman = null;
  let firstList = { dep: true, arr: true }, lastClick = { k: null, t: 0 };
  const nowMs = () => Date.now() + offset;
  const $ = (s) => body.querySelector(s);

  // --- zbiory lotów
  const deps = () => (data ? [...data.departures.filter((s) => !s.away), ...data.planned.filter((s) => s.kind === "dep")] : []);
  const arrs = () => (data ? [...data.arrivals.filter((s) => !(s.state === "ground" && !s.away)), ...data.planned.filter((s) => s.kind === "arr")] : []);
  const eldtRel = (s, now) => (s.eldt_s === null || s.eldt_s === undefined ? null : data.now_ts + s.eldt_s - now / 1000);
  const taxiDefault = () => data?.cad?.taxi || 10;
  // A-CDM lotniska: TSAT i TTOT, bez niego EOBT i ETOT; przed pierwszymi danymi null (nazwy puste, depLabels)
  const cdm = () => (data ? !!data.cdm : null);
  const lbl = () => depLabels(cdm());
  const times = (s, now) => depTimes(s, now, taxiDefault(), cdm());
  const srcTxt = (k) => (!cdm() && SRC_NOCDM[k]) || SRC_TXT[k];

  // --- widok DMAN/AMAN
  const buildA = () => {
    body.innerHTML = `<div class="dm-a">
      <section class="dm-list dm-deps"><div class="dm-rows"><div class="dm-in"></div></div>
        <div class="dm-foot" data-g="dep"><button data-sort="cs">CSGN</button><button data-sort="t"></button></div></section>
      <section class="dm-lad dm-l1"><svg class="dm-svg"></svg></section>
      <section class="dm-mid">
        <table class="dm-info"></table>
        <div class="dm-acts"><button data-act="map" title="Pokaż lot na mapie (MAP)">MAPA</button>
          ${STRIP_AIRPORTS.includes(icao) ? `<button data-act="strip" title="Paski EFES tego lotniska (zakładka STRIP)">PASKI</button>`
    : `<button data-act="strip" disabled title="Paski tylko dla 15 głównych lotnisk (zakładka STRIP)">PASKI</button>`}</div>
        <div class="dm-spans"><div class="sp l" data-g="dep">${SPANS.map((m) => `<button data-span="${m}">${m}'</button>`).join("")}</div>
          <div class="sp r" data-g="arr">${SPANS.map((m) => `<button data-span="${m}">${m}'</button>`).join("")}</div></div>
        <div class="dm-rwys"></div>
        <div class="dm-clock" title="Czas UTC">--:--:--</div>
      </section>
      <section class="dm-lad dm-l2"><svg class="dm-svg"></svg></section>
      <section class="dm-list dm-arrs"><div class="dm-rows"><div class="dm-in"></div></div>
        <div class="dm-foot" data-g="arr"><button data-sort="cs">CSGN</button><button data-sort="t">ETA</button></div></section>
    </div>`;
    firstList = { dep: true, arr: true };
    sortAll();
    renderSpans();
  };

  const sortBtns = (g, label) => {
    const [by, dir] = st.sort[g].split(":");
    const a = dir === "1" ? "▲" : "▼";
    body.querySelectorAll(`.dm-foot[data-g="${g}"] button`).forEach((b) => {
      const on = b.dataset.sort === by;
      b.classList.toggle("on", on);
      const name = b.dataset.sort === "cs" ? "CSGN" : label;
      b.textContent = on && name ? `${a} ${name} ${a}` : name;
      b.title = on ? `Sortowanie ${name}; ponowne kliknięcie odwraca kolejność (▲: późniejsze u góry, jak na drabinie)` : `Sortuj wg ${name}`;
      b.style.visibility = name ? "" : "hidden";
    });
  };
  // przed pierwszymi danymi przycisk czasu odlotów jest ukryty (jeszcze nie wiadomo: TSAT czy EOBT)
  const sortAll = () => { sortBtns("dep", lbl().obt); sortBtns("arr", "ETA"); };
  // lista w kolumnie: ▲ = późniejsze u góry (jak drabina), wtedy przyklejona do dołu; ▼ odwrotnie
  const fillList = (g, rows, emptyTxt) => {
    const box = $(`.dm-${g === "dep" ? "deps" : "arrs"} .dm-rows`);
    const up = st.sort[g].endsWith(":1");
    const fromBottom = box.scrollHeight - box.scrollTop;
    box.firstElementChild.innerHTML = rows.join("") || `<div class="dm-empty">${esc(emptyTxt)}</div>`;
    box.classList.toggle("up", up);
    if (firstList[g]) { box.scrollTop = up ? box.scrollHeight : 0; firstList[g] = false; } else if (up) box.scrollTop = box.scrollHeight - fromBottom;
  };
  const sorter = (g, timeOf) => {
    const [by, dir] = st.sort[g].split(":");
    const sign = dir === "1" ? -1 : 1;   // ▲: późniejsze u góry, czyli malejąco w DOM
    return (a, b) => {
      if (by === "cs") return sign * a.s.callsign.localeCompare(b.s.callsign);
      const ta = timeOf(a), tb = timeOf(b);
      return sign * ((ta ?? 1e9) - (tb ?? 1e9)) || a.s.callsign.localeCompare(b.s.callsign);
    };
  };

  const depTitle = (s, t) => {
    const v = s.viff || {};
    // etykieta na CTOT: zamiast powtórki CTOT pokazujemy ETOT, z którym go porównano (bez A-CDM)
    return [`${s.callsign}  ${s.aircraft || ""} → ${s.arrival || "?"}`, `${t.tsatSrc ? srcTxt(t.tsatSrc) : cdm() ? "brak TSAT, TOBT i EOBT" : "brak EOBT"}`,
      t.ttotSrc ? `${t.ttotSrc} ${hmAt(nowMs(), t.ttot)} (${srcTxt(t.ttotSrc)})` : "",
      t.ttotSrc === "CTOT" ? [t.etot !== null && !cdm() ? `ETOT ${hmAt(nowMs(), t.etot)}` : "", v.regulation].filter(Boolean).join(" · ")
        : v.ctot ? `CTOT ${hm(v.ctot)}${v.regulation ? " · " + v.regulation : ""}` : "",
      t.late ? "Spóźniony: TSAT minął, brak AOBT i ruchu." : "", s.planned ? "Lot planowany: pilot jeszcze nie połączony." : "",
      "Klik: szczegóły · dwuklik: mapa"].filter(Boolean).join("\n");
  };
  const arrTitle = (s, rel) => [`${s.callsign}  ${s.aircraft || ""} z ${s.departure || "?"}`,
    rel !== null ? `ELDT ${hm(s.eldt)} (${SRC_TXT[s.eldt_source] || ""})` : "Brak ELDT/ETA (samolot nie leci)",
    s.dist_nm !== null && s.dist_nm !== undefined ? `${s.dist_nm} NM · GS ${dash(s.groundspeed)} kt` : "",
    s.entry_fix ? `Wejście: ${s.entry_fix.fix}${s.proc ? " · STAR " + s.proc.name : ""}` : "", "Klik: szczegóły · dwuklik: mapa"].filter(Boolean).join("\n");

  const renderLists = (now) => {
    sortAll();
    if (!data) return;   // sortowanie kliknięte przed pierwszymi danymi: same przyciski
    const L = lbl();
    const D = deps().filter((s) => s.state !== "air" && !s.viff?.atot).map((s) => ({ s, t: times(s, now) }));
    D.sort(sorter("dep", (x) => x.t.tsat));
    fillList("dep", D.map(({ s, t }) => `<div class="dm-row${t.late ? " late" : ""}${s.planned ? " plan" : ""}${st.sel === keyOf(s) ? " sel" : ""}" data-k="${esc(keyOf(s))}" title="${esc(depTitle(s, t))}">
      <span class="c">${esc(s.callsign)}</span><span class="m">${STAND}</span><span class="t${t.tsatSrc && t.tsatSrc !== L.obt ? " est" : ""}">${hm(t.tsatT)}</span><span class="r">${esc(dash(s.runway))}</span></div>`),
    data.network_error ? "Brak danych z sieci VATSIM" : "Brak odlotów");
    const A = arrs().map((s) => ({ s, rel: eldtRel(s, now) }));
    A.sort(sorter("arr", (x) => x.rel));
    fillList("arr", A.map(({ s, rel }) => `<div class="dm-row arr${s.state !== "air" ? " plan" : ""}${st.sel === keyOf(s) ? " sel" : ""}" data-k="${esc(keyOf(s))}" title="${esc(arrTitle(s, rel))}">
      <span class="c">${esc(s.callsign)}</span><span class="m">${STAND}</span><span class="t${s.eldt_source === "vIFF" ? "" : " est"}">${hm(s.eldt)}</span></div>`),
    data.network_error ? "Brak danych z sieci VATSIM" : "Brak przylotów");
  };

  const renderLadders = (now) => {
    if (!data || st.view !== "dman") return;
    const l1 = $(".dm-l1 svg"), l2 = $(".dm-l2 svg");
    if (!l1 || !l2) return;
    const L = lbl();
    // drabina TSAT (bez A-CDM EOBT): oś po lewej, etykiety na prawo (callsign, stanowisko, czas)
    if (l1.clientWidth >= 40 && l1.clientHeight >= 60) {
      const W = l1.clientWidth, H = l1.clientHeight;
      const g = geom(H, st.span.dep);
      const list = deps().filter((s) => s.state !== "air" && !s.viff?.atot).map((s) => {
        const t = times(s, now);
        return { key: keyOf(s), rel: t.tsat, cs: s.callsign, mid: STAND, time: hm(t.tsatT), est: t.tsatSrc !== L.obt,
          cls: ["dep", s.planned ? "plan" : "", t.offBlock ? "off" : ""], csCls: t.late ? "late" : t.ready ? "rdy" : "", title: depTitle(s, t) };
      });
      const axisX = W < 180 ? 40 : 46;
      const tx0 = axisX + (W < 180 ? 10 : 22);
      l1.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const [base, labels] = scaleSvg(g, now, { W, axisX, labelX: axisX - 10, barX: 6, barW: axisX - 8, winTitle: `Okno ${L.obt} −5/+5 min wokół teraz` });
      l1.innerHTML = `<text class="wm" x="${axisX + 12}" y="${Math.min(120, H * 0.18)}" style="font-size:${Math.round(Math.min(110, (W - 60) / 2.6))}px">${L.obt}</text>`
        + base + labels
        + tagsSvg(list, g, { axisX, x0: tx0, x1: Math.min(W - 6, tx0 + 250), right: true, sel: st.sel, empty: "Brak odlotów w tym oknie" });
    }
    // drabiny TTOT (bez A-CDM ETOT; na lewo od wspólnej osi) i ELDT (na prawo, żółte). Callsign na brązowym tle: etykieta
    // stoi na CTOT (A-CDM: brak TTOT; bez A-CDM: CTOT później niż ETOT); po starcie etykieta na ATOT, nigdy brązowa
    if (l2.clientWidth >= 80 && l2.clientHeight >= 60) {
      const W = l2.clientWidth, H = l2.clientHeight;
      const g = geom(H, st.span.arr);
      const axisX = Math.round(W * 0.53);
      const dl = deps().map((s) => {
        const t = times(s, now);
        return { key: keyOf(s), rel: t.ttot, cs: s.callsign, mid: s.aircraft || "–", time: hmAt(now, t.ttot), est: t.ttotSrc !== "ATOT" && t.ttotSrc !== L.tot,
          cls: ["dep", s.planned ? "plan" : "", t.offBlock ? "off" : ""], csCls: t.ttotSrc === "CTOT" ? "ctot" : "", title: depTitle(s, t) };
      }).filter((t) => t.rel !== null);
      const al = arrs().map((s) => {
        const rel = eldtRel(s, now);
        return { key: keyOf(s), rel, cs: s.callsign, mid: STAND, time: hm(s.eldt), est: s.eldt_source !== "vIFF", cls: ["arr", s.state !== "air" ? "plan" : ""],
          csCls: "", title: arrTitle(s, rel) };
      }).filter((t) => t.rel !== null);
      const wmSize = Math.round(Math.min(110, (axisX - 40) / 2.6));
      const [base, labels] = scaleSvg(g, now, { W, axisX, labelX: axisX - 12, barX: axisX - 34, barW: 32, winTitle: "−5/+5 min wokół teraz", labelBg: true });
      l2.setAttribute("viewBox", `0 0 ${W} ${H}`);
      l2.innerHTML = `<text class="wm" x="${axisX - 50}" y="${Math.min(120, H * 0.18)}" text-anchor="end" style="font-size:${wmSize}px">${L.tot}</text>
        <text class="wm" x="${axisX + 18}" y="${Math.min(120, H * 0.18)}" style="font-size:${Math.round(Math.min(110, (W - axisX - 24) / 2.6))}px">ELDT</text>`
        + base
        + tagsSvg(dl, g, { axisX, x0: Math.max(4, axisX - 50 - 250), x1: axisX - 50, right: false, sel: st.sel, empty: "Brak odlotów w tym oknie" })
        + tagsSvg(al, g, { axisX, x0: axisX + 20, x1: Math.min(W - 6, axisX + 20 + 240), right: true, sel: st.sel, empty: "Brak przylotów w tym oknie" })
        + labels;
    }
  };

  const acType = (s) => (s.aircraft ? s.aircraft + (s.wake ? "/" + s.wake : "") : "");
  const renderInfo = (now) => {
    const tbl = $(".dm-info");
    if (!tbl) return;
    const s = st.sel && byKey.get(st.sel);
    const R = (k, v, cls = "", h = "") => `<tr class="${cls}"${h ? ` title="${esc(h)}"` : ""}><th>${k}</th><td>${esc(dash(v))}</td></tr>`;
    let rows;
    if (!s) {
      rows = R("CALLSIGN", "", "hd") + `<tr><td colspan="2" class="dm-hint">Kliknij lot na liście albo na drabinie.</td></tr>`;
    } else if (s.kind === "arr") {
      const seqd = amanSequence(data, icao).get(keyOf(s));
      const rel = eldtRel(s, now);
      rows = R("CALLSIGN", s.callsign, "hd") + R("REGISTRATION", s.registration, "", "REG/ z uwag planu lotu")
        + R("FROM", s.departure) + R("TO", s.arrival) + R("AC TYPE", acType(s))
        + R("STAR/IAF", [s.proc?.name, s.entry_fix?.fix].filter(Boolean).join(" / "), "", s.entry_fix ? `Punkt wejścia ${s.entry_fix.fix}: ${{ STAR: "z procedury STAR", IAF: "punkt wejścia lotniska w trasie", route: "ostatni punkt trasy" }[s.entry_fix.source] || ""}` : "")
        + R("STAND ARR", STAND, "", "Stanowiska nie ma w danych VATSIM.")
        + R("STATUS ARR", s.planned ? (s.planned === "prefile" ? "PREFILE" : "OFFLINE") : s.state === "air" ? "AIRB" : s.away ? "NA ZIEMI (ADEP)" : "NA ZIEMI")
        + R("ELDT", s.eldt ? `${hm(s.eldt)} ${s.eldt_source === "vIFF" ? "vIFF" : "POZ."}` : "", "hl", SRC_TXT[s.eldt_source] || "")
        + R("ETA (POZ.)", s.eta_s !== null && s.eta_s !== undefined ? hmAt(nowMs(), data.now_ts + s.eta_s - now / 1000) : "", "", SRC_TXT.POS)
        + R("ELDT vIFF", hm(s.viff_eta), "", "Pole eta z vIFF (/ifps/callsign)")
        + R("STA (AMAN)", seqd ? clock(seqd.sta * 1000, false) : "", "", "Sekwencja AMAN: STA = max(ETA, poprzedni STA + FLOW/140 kt)")
        + R("DELAY", seqd ? `${Math.round(seqd.delay / 6) / 10} min` : "", "", "STA − ETA: tyle samolot musi stracić")
        + R("DIST", s.dist_nm !== null && s.dist_nm !== undefined ? `${s.dist_nm} NM` : "", "", "Odległość po ortodromie do lotniska")
        + R("GS", s.groundspeed ? `${s.groundspeed} kt` : "") + R("ALT", s.state === "air" && s.altitude ? `${s.altitude} ft` : "")
        + R("RFL", s.rfl_ft ? `FL${String(Math.round(s.rfl_ft / 100)).padStart(3, "0")}` : "")
        + R("ETA (MIN)", rel !== null ? `${Math.round(rel / 60)} min` : "")
        + R("RWY ARR", s.runway, "hl");
    } else {
      const v = s.viff || {};
      const r = (x) => relSec(x, now);
      const t = times(s, now);
      const c = cdm();
      // bez A-CDM wyróżnione EOBT i ten z wierszy ATOT, CTOT, ETOT, na którym stoi etykieta drabiny ETOT (depTimes);
      // TOBT, TSAT, VAR TAXI TIME i TTOT tylko, gdy vIFF je podaje (bez pustych wierszy i bez wyróżnienia)
      const opt = (on, ...a) => (c || on ? R(...a) : "");
      const lad = (k) => !c && t.ttotSrc === k;
      const LAD = "to samo co etykieta na drabinie ETOT";
      const reg = v.regulation ? `Regulacja ${v.regulation}${v.delay > 0 ? `, opóźnienie ${v.delay} min` : ""}` : "";
      const ctotTip = [reg, lad("CTOT") ? `${t.etot === null ? "brak ETOT" : "później niż ETOT"}: ${LAD}`
        : lad("ETOT") && v.ctot ? "nie później niż ETOT: etykieta drabiny ETOT stoi na ETOT" : ""];
      const taxiSrc = v.taxi ? "Czas kołowania lotu z vIFF" : data.cad?.taxi ? `Domyślny czas kołowania lotniska z vIFF${c ? " (A-CDM)" : ""}`
        : "Brak czasu kołowania w vIFF: przyjęte 10 min";
      const vtt = v.ttot && v.tsat ? Math.round((r(v.ttot) - r(v.tsat)) / 60) : null;
      const status = s.planned ? (s.planned === "prefile" ? "PREFILE" : "OFFLINE") : s.state === "air" ? "AIRB"
        : (s.groundspeed || 0) >= 3 ? "TAXI" : v.aobt ? "OFFBLK" : v.ready ? "RDY" : v.state || "–";
      rows = R("CALLSIGN", s.callsign, "hd") + R("REGISTRATION", s.registration, "", "REG/ z uwag planu lotu")
        + R("FROM", s.departure) + R("TO", s.arrival) + R("AC TYPE", acType(s))
        + R("STAND DEP", STAND, "", "Stanowiska nie ma w danych VATSIM.") + R("DEICING PAD", "") + R("DEICING REQ", "") + R("DEICING TIME", "")
        + R("MIN TURNARND", "")
        + R("STATUS DEP", status, "hl", v.state ? data.states?.[v.state] || v.state : "Brak lotu w vIFF")
        + R("SCHED DEP", hm(s.deptime), "", "Godzina odlotu z planu lotu VATSIM") + R("SOBT", hm(s.deptime), "", "Plan lotu VATSIM (deptime)")
        + R("EOBT", hm(s.eobt), c ? "" : "hl", s.eobt_source === "vIFF" ? "EOBT z vIFF (IFPS)" : "EOBT z planu lotu VATSIM")
        + opt(v.tobt, "TOBT", hm(v.tobt)) + R("ACT RDY TIME", v.ready ? "REA" : "", "", "vIFF podaje tylko znacznik REA (gotowy), bez godziny")
        + opt(v.tsat, "TSAT", hm(v.tsat), c ? "hl" : "") + R("AOBT", hm(v.aobt))
        + R("EST TAXI OUT", `${t.taxi} min`, v.taxi || data.cad?.taxi ? "" : "est", taxiSrc)
        + opt(vtt !== null, "VAR TAXI TIME", vtt !== null ? `${vtt} min` : "", "", "TTOT − TSAT")
        + R("CTOT", hm(v.ctot), (c ? v.ctot : lad("CTOT")) ? "hl" : "", ctotTip.filter(Boolean).join("; "))
        + R("ETOT", t.etot !== null ? hmAt(now, t.etot) : "", !c ? (lad("ETOT") ? "hl" : "") : t.etotBase === "TSAT" || t.etotBase === "AOBT" ? "" : "est",
          t.etot !== null ? `ETOT = ${t.etotBase} + kołowanie ${t.taxi} min (${c ? "to samo co etykieta ETOT na drabinie TTOT, gdy brak TTOT i CTOT"
            : lad("ETOT") ? LAD : `${LAD}, gdy brak ATOT i późniejszego CTOT`})` : "")
        + opt(v.ttot, "TTOT", hm(v.ttot), c ? "hl" : "") + R("ATOT", hm(v.atot), lad("ATOT") ? "hl" : "", lad("ATOT") ? `Rzeczywisty start (vIFF): ${LAD}` : "")
        + R("RWY DEP", s.runway, "hl", s.runway_source === "CDM" ? "Pas z wtyczki CDM (vIFF)" : RWY_SRC[s.runway_source] || "");
    }
    tbl.innerHTML = rows;
    const online = s && !s.planned;
    body.querySelector('[data-act="map"]').disabled = !online;
  };

  const renderRwys = () => {
    const box = $(".dm-rwys");
    if (!box || !data) return;
    const u = data.runway_in_use || {};
    const src = u.source === "ATIS" ? `z ATIS ${data.atis?.letter || ""}`.trim() : RWY_SRC.SUGGESTED;
    const rate = data.cad?.rate;
    const rateTxt = rate ? ` R${rate}: przepustowość odlotów ${rate}/h (${cdm() ? "vIFF A-CDM" : "ustawienia lotniska w vIFF, lotnisko bez A-CDM"}).` : "";
    box.innerHTML = `<div class="dm-rwy dep" title="${esc(`Pas startowy w użyciu (${src}).${rateTxt}`)}"><i>DEP</i><b>${esc(dash(u.dep))}</b>${rate ? `<small>R${esc(rate)}</small>` : ""}</div>
      <div class="dm-rwy arr" title="${esc(`Pas do lądowania w użyciu (${src}).`)}"><i>ARR</i><b>${esc(dash(u.arr))}</b></div>
      ${u.source === "ATIS" ? "" : `<div class="dm-sugg">sugestia, brak ATIS</div>`}`;
  };

  const renderSpans = () => {
    const L = lbl();
    body.querySelectorAll(".dm-spans .sp").forEach((row) => {
      row.title = row.dataset.g === "dep" ? `Zakres drabiny ${L.obt || "odlotów"} (lewej)` : `Zakres drabin ${L.tot || "odlotów"} i ELDT (prawych)`;
      row.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.dataset.span === st.span[row.dataset.g]));
    });
  };

  const renderMsg = () => {
    if (!data) return;
    const errs = [data.network_error, data.viff_error].filter(Boolean);
    msg.className = "dm-msg" + (errs.length ? " err" : "");
    msg.textContent = errs.length ? errs.join(" · ") : `${data.icao} · ATIS ${data.atis?.letter || "–"} · A-CDM ${data.viff_error ? "?" : data.cdm ? "tak" : "nie"} · dane ${clock(data.now_ts * 1000, false)}Z`;
  };

  const renderClock = () => {
    const c = $(".dm-clock");
    if (c) c.textContent = clock(nowMs());
  };

  const render = () => {
    root.querySelectorAll(".dm-top [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === st.view));
    body.style.filter = st.bright ? `brightness(${1 + st.bright * 0.12})` : "";
    if (!data) return;
    renderMsg();
    const now = nowMs();
    if (st.view === "aman") {
      aman?.render(data);
      return;
    }
    renderLists(now);
    renderInfo(now);
    renderRwys();
    renderSpans();
    renderClock();
    renderLadders(now);
  };

  // user: kliknięcie przycisku widoku (krok do cofnięcia w adresie gospodarza); bez niego tylko pokazanie widoku
  const setView = (v, user = false) => {
    if (!VIEWS.includes(v)) return;
    if (v === st.view && body.firstElementChild) return;
    st.view = v;
    lsSet("dman.view", v);
    aman?.destroy();
    aman = null;
    if (v === "aman") aman = createAman(body, { icao, st, nowMs, getData: () => data });
    else buildA();
    render();
    if (user) onView?.(v, {});
  };

  // wybór lotu: bez przebudowy list (zostaje przewinięcie), tylko zaznaczenie, szczegóły i drabiny
  function select(k) {
    st.sel = st.sel === k ? null : k;
    if (st.view === "aman") return aman?.render(data);
    body.querySelectorAll(".dm-row[data-k]").forEach((r) => r.classList.toggle("sel", r.dataset.k === st.sel));
    renderInfo(nowMs());
    renderLadders(nowMs());
  }
  function openMap(k) {
    const s = byKey.get(k);
    if (s && !s.planned) navigate("map", s.callsign);
  }

  const refresh = async () => {
    const my = ++seq;
    try {
      const d = await api(`/api/aerodromes/${encodeURIComponent(icao)}/strips?dman=1`);
      if (dead || my !== seq) return;
      // zegar serwera: drabiny liczymy od jego czasu, gdy zegar przeglądarki różni się o więcej niż 30 s
      const off = d.now_ts ? d.now_ts * 1000 - Date.now() : 0;
      offset = Math.abs(off) > 30000 ? off : 0;
      data = d;
      note.hidden = true;
      byKey = new Map();
      ["departures", "arrivals", "local", "planned"].forEach((k) => d[k].forEach((s) => byKey.set(keyOf(s), s)));
      if (st.sel && !byKey.has(st.sel)) st.sel = null;
      render();
      onUpdate?.(d);
    } catch (e) {
      if (dead || my !== seq) return;
      msg.className = "dm-msg err";
      msg.textContent = e.message;
      // jeszcze bez danych: komunikat na środku zamiast pustych kolumn (z danymi zostają poprzednie, błąd w pasku)
      if (!data) {
        note.className = "dm-note err";
        note.hidden = false;
        note.firstElementChild.textContent = `Nie udało się pobrać danych: ${e.message}. Ponowna próba co 30 s (ODŚWIEŻ: teraz).`;
      }
    }
  };

  // odświeżanie co 30 s tylko, gdy widok jest widoczny; zegar co 1 s, drabiny co 2 s
  const visible = () => !document.hidden && root.isConnected && root.offsetParent !== null;
  const schedule = () => { clearTimeout(timer); timer = dead ? null : setTimeout(tick, REFRESH_MS); };
  const tick = async () => {
    timer = null;
    if (dead || !visible()) return;
    await refresh();
    if (!timer) schedule();
  };
  const onVis = () => { if (!document.hidden && !timer && visible()) { refresh(); schedule(); } };
  document.addEventListener("visibilitychange", onVis);
  clockTimer = setInterval(() => {
    if (dead || !visible()) return;
    renderClock();
    if (++ticks % (LADDER_MS / 1000) === 0) {
      if (st.view === "dman") renderLadders(nowMs());
      else aman?.tick();
    }
  }, 1000);
  let raf = 0;
  const ro = new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { if (!dead && data) (st.view === "dman" ? renderLadders(nowMs()) : aman?.render(data)); });
  });
  ro.observe(body);

  root.addEventListener("click", (e) => {
    const t = e.target;
    const vb = t.closest(".dm-top [data-view]");
    if (vb) return setView(vb.dataset.view, true);
    const act = t.closest("[data-act]")?.dataset.act;
    if (act === "reload") {
      if (!data) { note.className = "dm-note"; note.hidden = false; note.firstElementChild.textContent = "Ładowanie…"; }
      return refresh();
    }
    if (act === "brighter" || act === "darker") {
      st.bright = Math.max(-4, Math.min(4, st.bright + (act === "brighter" ? 1 : -1)));
      lsSet("dman.bright", String(st.bright));
      return render();
    }
    if (act === "map") return st.sel && openMap(st.sel);
    if (act === "strip") return navigate("strip", icao);
    const sp = t.closest(".dm-spans button[data-span]");
    if (sp) {
      const g = sp.parentElement.dataset.g;
      st.span[g] = +sp.dataset.span;
      lsSet("dman.span." + g, sp.dataset.span);
      renderSpans();
      return renderLadders(nowMs());
    }
    const so = t.closest(".dm-foot button[data-sort]");
    if (so) {
      const g = so.parentElement.dataset.g;
      const [by, dir] = st.sort[g].split(":");
      st.sort[g] = so.dataset.sort === by ? `${by}:${dir === "1" ? "0" : "1"}` : `${so.dataset.sort}:1`;
      lsSet("dman.sort." + g, st.sort[g]);
      firstList[g] = true;
      return renderLists(nowMs());
    }
    const k = t.closest("[data-k]")?.dataset.k;
    if (!k) return;
    // dwuklik wykrywamy sami: pierwsze kliknięcie przerysowuje drabiny, więc przeglądarka nie widzi dwukliku na etykiecie
    const t0 = Date.now();
    if (k === lastClick.k && t0 - lastClick.t < 450) {
      lastClick = { k: null, t: 0 };
      if (st.sel !== k) select(k);
      return openMap(k);
    }
    lastClick = { k, t: t0 };
    select(k);
  });

  setView(st.view);
  onView?.(st.view, { replace: true });
  refresh();
  schedule();
  return {
    // powrót do RUCH (menu, ←, odnośnik): odświeżenie i bieżący widok w adresie
    resume() { if (!dead) { refresh(); schedule(); onView?.(st.view, { replace: true }); } },
    // widok z adresu (← / Wstecz do #aerodrome/EPWA/dman): bez nowego wpisu w historii
    setView(v) { if (!dead) setView(v); },
    destroy() {
      dead = true;
      clearTimeout(timer);
      clearInterval(clockTimer);
      timer = clockTimer = null;
      ro.disconnect();
      aman?.destroy();
      document.removeEventListener("visibilitychange", onVis);
    },
  };
}
