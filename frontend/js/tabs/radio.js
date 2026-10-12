// RADIO: jedna strona jak w PANDORZE (runda 10, Marek): u góry pasek ATIS, po lewej duży podgląd LOW z sektoryzacją
// FIR EPWW i stosami sąsiadów (ESAA, EDWW, EDUU, EDMM, LKAA), w środku przyciski warstw i mały podgląd MID, po prawej
// duży podgląd HIGH ze stosami UMKK, EYVL, UMMV, UKLV, LZBB. Na podglądach: ID sektora (litera warstwy + litera sektora,
// np. LB) i częstotliwość stanowiska, które teraz go obsługuje wg kolejności przejmowania (ownership.json); bez nikogo
// online przygaszona częstotliwość stanowiska sektora. Dane: GET /api/radio/board co 30 s (geometria raz, potem tylko po
// zmianie wersji danych `ver`, np. po imporcie nowego pliku .ese).
import { api, esc, h, lsGet, lsSet, navigate, remember } from "../api.js";
import { labelPoint } from "../coverage.js";
import { displayName, loadDisplayNames, posName } from "../posname.js";
import { DEFAULT_CUT, DEFAULT_LAYERS, EPWW_BBOX, LAYERS, LAYER_IDS, argOf, bboxOf, bboxOrDefault, byShort, controllersOn, cutBand,
  cutBounds, cutOf, flNum, flText, levelLines, parseArg, pathOf, placeLabels, projection, ringsOf, sliceAt, utcHhmm } from "../radioboard.js";

const LS_LAYERS = "radio.layers", LS_CUT = "radio.cut";
const REFRESH_MS = 30000;
const LEVEL_FT = { MID: 35000, HIGH: 40000 };  // MID i HIGH mają jeden kształt sektorów
const MAPS = [
  { key: "LOW", layer: "low", cls: "rb-low", side: "left" },
  { key: "MID", layer: "mid", cls: "rb-midmap", side: null },
  { key: "HIGH", layer: "high", cls: "rb-high", side: "right" },
];
// miejsce stosu sąsiada w panelu: strona mapy z /api/radio/board (at) → obszar siatki panelu
const AREA = { n: "top", s: "bot", nw: "col", w: "col", sw: "col", ne: "col", e: "col", se: "col" };
const COL_ORDER = { nw: 0, w: 1, sw: 2, ne: 0, e: 1, se: 2 };
// etykiety warstw APP/TWR/FIS: krótkie ID bez rodzaju (kolor paska z lewej mówi, co to jest): WA S APP → WA S
const shortId = (id) => String(id).replace(/ (?:APP|DEP|TWR|FIS)$/, "");

function readLayers() {
  try {
    const v = JSON.parse(lsGet(LS_LAYERS));
    if (Array.isArray(v)) return new Set(v.filter((x) => LAYER_IDS.includes(x)));
  } catch { /* brak albo uszkodzony wpis */ }
  return new Set(DEFAULT_LAYERS);
}
function readCut() {
  const n = parseInt(lsGet(LS_CUT), 10);
  return n >= 95 && n <= 335 ? n : DEFAULT_CUT;
}

// przybliżone pole wielokąta (do wyboru większej części sektora dzielonego w poziomie, np. R-N i R-S)
const areaOf = (g) => ringsOf(g).reduce((a, r) => a + Math.abs(r.reduce((s, [x1, y1], i) => {
  const [x2, y2] = r[(i + 1) % r.length];
  return s + x1 * y2 - x2 * y1;
}, 0)) / 2, 0);

export default {
  mount(root) {
    const el = h(`<div class="rb">
      <div class="rb-atis" aria-label="ATIS"></div>
      <div class="rb-grid">
        ${MAPS.map((m) => m.key === "MID" ? `<div class="rb-mid">
            <div class="rb-btns">${LAYERS.map((l) => `<button type="button" data-ly="${l.id}" title="${esc(l.tip)}" aria-pressed="false">${esc(l.label)}</button>`).join("")}</div>
            <div class="rb-status"></div>
            <div class="rb-legend"><span><i class="rb-sw on"></i>obsadzony</span><span><i class="rb-sw off"></i>nikt (nominalna)</span><span><i class="rb-sw cov"></i>↑ przejmuje inne (TWR)</span></div>
            ${panelHtml(m)}
          </div>` : panelHtml(m)).join("")}
      </div>
      <div class="rb-tip" hidden></div>
    </div>`);
    root.append(el);
    const $ = (s) => el.querySelector(s);
    const tip = $(".rb-tip");

    let layers = readLayers(), cut = readCut();
    let d = null, geo = null, geoVer = "", err = "", border = null, noBorder = false, timer = null, loading = false, lastLoad = 0;
    const maps = Object.fromEntries(MAPS.map((m) => {
      const p = el.querySelector(`.rb-panel[data-map="${m.key}"]`);
      return [m.key, { ...m, el: p, svg: p.querySelector("svg"), lbls: p.querySelector(".rb-lbls"), free: p.querySelector(".rb-free") }];
    }));

    // --- nazwy i częstotliwości
    const nm = (cs) => (String(cs).startsWith("EP") ? displayName(cs).replace(/^EPWW /, "") : posName(cs));
    const on = (cs) => d?.online?.[cs];
    const freqOf = (cs) => on(cs)?.frequency || d?.pos?.[cs]?.frequency || "";
    const who = (cs) => {
      const o = on(cs);
      return o ? `${o.callsign} ${o.frequency} – ${o.name || "?"} (CID ${o.cid ?? "?"})${o.logon_time ? `, od ${utcHhmm(o.logon_time)}` : ""}` : "";
    };
    const posLine = (cs) => `${cs}${d?.pos?.[cs]?.name ? ` · ${d.pos[cs].name}` : ""} ${d?.pos?.[cs]?.frequency || ""}`.trim();
    const chainText = (ch) => ch.map(nm).join(" › ");
    // linijki etykiety: właściciele (najwyżej dwaj) albo częstotliwość nominalna; by = skrót właściciela innego niż nominalny
    const lines = (nominal, owners, short = false) => (owners.length
      ? owners.slice(0, 2).map((o) => ({ fq: freqOf(o), by: o === nominal ? "" : short ? byShort(nm(o)) : nm(o) }))
      : [{ fq: d?.pos?.[nominal]?.frequency || "—", by: "" }]);
    const nowText = (owners, nominal) => (owners.length ? owners.map((o) => `Teraz: ${who(o) || o}`).join("\n")
      : `Teraz nikt – częstotliwość nominalna ${nm(nominal)} ${d?.pos?.[nominal]?.frequency || ""}`.trim());

    // etykieta podglądu w trzech odmianach: pełna (ID nad częstotliwością), skrócona (ID i częstotliwość w jednej linii)
    // i samo ID; mniejsze, gdy pełna nie mieści się przy swoim punkcie (szczegóły zawsze w dymku)
    function lblHtml(lb, v = 0) {
      const attrs = `${lb.k ? ` data-k="${esc(lb.k)}"` : ""}${lb.icao ? ` data-icao="${esc(lb.icao)}" tabindex="0" role="button"` : ""} data-rt="${esc(lb.rt)}"`;
      const cls = `rb-lbl ${lb.cls} ${lb.state}${v ? " cmp" : ""}`;
      if (v === 2) return `<div class="${cls}"${attrs}><b class="rb-id">${esc(lb.cid)}</b></div>`;
      const fq = lb.ls[0]?.fq ? `<span class="rb-fq">${esc(lb.ls[0].fq)}</span>` : "";
      if (v === 1) return `<div class="${cls}"${attrs}><b class="rb-id">${esc(lb.cid)}</b>${fq}</div>`;
      const more = lb.more ? `<i class="rb-more">+${lb.more}</i>` : "";
      return `<div class="${cls}"${attrs}><b class="rb-id">${esc(lb.id)}</b>${lb.ls.filter((l) => l.fq || l.by).map((l) => `<span class="rb-ln">${l.by ? `<i class="rb-by">${esc(l.by)}</i>` : ""}`
        + `${l.fq ? `<span class="rb-fq">${esc(l.fq)}</span>` : ""}</span>`).join("")}${more}${lb.extra || ""}</div>`;
    }

    function boxHtml({ cls, id, nominal, owners, rt, k = "", extra = "", short = false }) {
      const ls = lines(nominal, owners, short);
      const more = owners.length > 2 ? `<i class="rb-more">+${owners.length - 2}</i>` : "";
      return `<div class="${cls} ${owners.length ? "on" : "off"}"${k ? ` data-k="${esc(k)}"` : ""} data-rt="${esc(rt)}">`
        + `<b class="rb-id">${esc(id)}</b>${ls.map((l) => `<span class="rb-ln">${l.by ? `<i class="rb-by">${esc(l.by)}</i>` : ""}`
        + `<span class="rb-fq">${esc(l.fq)}</span></span>`).join("")}${more}${extra}</div>`;
    }

    // --- pasek ATIS
    function renderAtis() {
      const box = $(".rb-atis");
      box.hidden = !layers.has("atis");
      if (box.hidden || !d) return;
      box.innerHTML = d.atis.map((a) => {
        const o = on(a.callsign), fq = freqOf(a.callsign) || "—";
        const rt = [posLine(a.callsign), o ? `Online: ${who(a.callsign)}${o.atis_code ? `\nInformacja ${o.atis_code}` : ""}` : "ATIS offline",
          ...(o?.text_atis || []).slice(0, 6), "Kliknij: AERODROME"].join("\n");
        return `<button type="button" class="rb-at ${o ? "on" : "off"}" data-icao="${esc(a.icao)}" data-rt="${esc(rt)}">`
          + `<span class="rb-ati">${esc(a.callsign.replace(/_ATIS$/, "").replace(/_/g, " "))}</span><span class="rb-fq">${esc(fq)}</span>`
          + `${o?.atis_code ? `<b class="rb-code">${esc(o.atis_code)}</b>` : ""}</button>`;
      }).join("") || `<span class="rb-none">Brak stanowisk ATIS w pliku .ese.</span>`;
    }

    // --- stosy sąsiadów
    function rowBox(r) {
      const rt = [posLine(r.callsign), `${flText(r.lo)}–${flText(r.hi)} · sektory: ${r.sectors.slice(0, 8).join(", ")}${r.sectors.length > 8 ? " …" : ""}`,
        nowText(r.owners, r.callsign), ...(r.chains[0]?.length > 1 ? [`Kolejność: ${chainText(r.chains[0])}`] : [])].join("\n");
      return boxHtml({ cls: "rb-box", id: nm(r.callsign), nominal: r.callsign, owners: r.owners, rt, short: true });
    }
    function stackHtml(st) {
      const lv = levelLines(st.rows);
      const tipH = `${st.fir} ${st.title}: stanowiska z sektorów przy granicy FIR EPWW (plik .ese sąsiada)\nliczby po lewej: górna i dolna granica (FL)`;
      return `<div class="rb-nb" data-fir="${esc(st.fir)}"><div class="rb-nbh" data-rt="${esc(tipH)}"><b>${esc(st.fir)}</b> ${esc(st.title)}</div>`
        + (lv.length ? lv.map((ln) => `<div class="rb-lv"><span class="rb-fl" data-rt="${esc(`${flText(ln.lo)}–${flText(ln.hi)}`)}"><i>${flNum(ln.hi)}</i><i>${flNum(ln.lo)}</i></span>`
          + `<div class="rb-bxs">${ln.rows.map(rowBox).join("")}</div></div>`).join("")
          : `<div class="rb-none">brak sektorów przy granicy</div>`) + "</div>";
    }
    function renderStacks() {
      if (!d) return;
      ["left", "right"].forEach((side) => {
        const m = MAPS.find((x) => x.side === side);
        const p = maps[m.key].el;
        const sts = d.stacks.filter((s) => s.map === side);
        p.querySelector(".rb-top").innerHTML = sts.filter((s) => AREA[s.at] === "top").map(stackHtml).join("");
        p.querySelector(".rb-bot").innerHTML = sts.filter((s) => AREA[s.at] === "bot").map(stackHtml).join("");
        p.querySelector(".rb-col").innerHTML = sts.filter((s) => AREA[s.at] === "col").sort((a, b) => COL_ORDER[a.at] - COL_ORDER[b.at]).map(stackHtml).join("");
      });
    }

    // --- podglądy
    const accTip = (s) => {
      const ly = d.layers?.[s.layer];
      return [`Sektor ${s.letter} · ${s.layer}${ly?.label ? ` ${ly.label}` : ""}`, `Stanowisko sektora: ${posLine(s.nominal)}`,
        nowText(s.owner ? [s.owner] : [], s.nominal), `Kolejność: ${chainText(s.chain)}`].join("\n");
    };
    const groupTip = (g, title, extra = [], now = null) => [title, `Stanowisko: ${posLine(g.nominal)}`,
      now || (g.owners.length || !g.topdown ? nowText(g.owners, g.nominal) : `Teraz top-down: ${who(g.topdown) || g.topdown}`), ...extra,
      `Kolejność: ${chainText(g.chains[0] || [])}`].join("\n");

    // etykiety podglądu: {lb (model etykiety), at: [lat, lon], prio, dot?, alts?}; APP, TWR i FIS tylko na LOW
    function labelsFor(m, accs) {
      const out = [];
      if (layers.has(m.layer)) {
        const byLetter = new Map();
        accs.forEach((s) => { const b = byLetter.get(s.letter); if (!b || areaOf(s.geometry) > areaOf(b.geometry)) byLetter.set(s.letter, s); });
        d.sectors.filter((s) => s.layer === m.key && byLetter.has(s.letter)).forEach((s) => {
          const sl = byLetter.get(s.letter);
          const at = labelPoint({ type: "Feature", geometry: sl.geometry, properties: { fir: "EPWW", name: sl.name } })?.at;
          const owners = s.owner ? [s.owner] : [];
          if (at) out.push({ at, prio: 0, max: 70, lb: { cls: "acc", state: owners.length ? "on" : "off", id: s.id, cid: s.id, k: s.letter,
            ls: lines(s.nominal, owners), rt: accTip(s) } });
        });
      }
      if (m.key !== "LOW") return out;  // APP, TWR i FIS to przestrzeń dolna: na podglądzie LOW
      const local = (g) => ({ ls: lines(g.nominal, g.owners), more: Math.max(0, g.owners.length - 2) });
      if (layers.has("app")) {
        // ta sama częstotliwość w kilku TMA (WA APP w TMA Warszawa, Łódź i Radom; PO N APP w TMA Poznań N i Zielona Góra):
        // z częstotliwością tylko w TMA "swojego" lotniska (albo w pierwszej), w pozostałych sama nazwa, reszta w dymku
        const tma = d.tma.filter((g) => g.at);
        const station = (g, i) => (g.owners.length ? g.owners[i] : g.nominal);
        const keeper = new Map();  // znak → id TMA, w której etykieta ma jego częstotliwość
        tma.forEach((g) => { const cs = station(g, 0); if (cs && cs.slice(0, 4) === g.icao && !keeper.has(cs)) keeper.set(cs, g.id); });
        tma.forEach((g) => {
          const { ls, more } = local(g);
          ls.forEach((l, i) => {
            const cs = station(g, i);
            if (!cs) return;
            if (!keeper.has(cs)) keeper.set(cs, g.id);
            if (keeper.get(cs) !== g.id) l.fq = "";
          });
          const id = nm(g.nominal);
          out.push({ at: g.at, prio: g.owners.length ? 1 : 1.5, dot: "app", lb: { cls: "app", state: g.owners.length ? "on" : "off", id, cid: shortId(id), ls, more,
            rt: groupTip(g, `${g.id} (${g.lo ? flText(g.lo) : "GND"}–${flText(g.hi)})`, g.apps.length > 1 ? [`APP: ${g.apps.map((c) => `${nm(c)} ${d.pos[c]?.frequency || ""}`).join(", ")}`] : []) } });
        });
      }
      const twra = layers.has("twra");
      if (twra || layers.has("twr")) {
        d.twr.forEach((g) => {
          if (!g.at) return;
          // TWR: częstotliwość wieży (jasna, gdy wieża online); wieżę przejmuje inne stanowisko (np. APP) → ↑APP, dymek mówi kto
          const tw = g.owners.find((o) => /_TWR$/.test(o)), cover = tw ? null : g.owners[0];
          const ls = [tw ? { fq: freqOf(tw), by: tw === g.nominal ? "" : nm(tw) }
            : { fq: d.pos[g.nominal]?.frequency || "—", by: cover ? `↑${byShort(nm(cover))}` : "" }];
          const a = g.atis ? on(g.atis) : null;
          const atl = twra && g.atis ? `<span class="rb-ln rb-atl${a ? " on" : ""}"><i class="rb-by">ATIS</i><span class="rb-fq">${esc(freqOf(g.atis))}</span>${a?.atis_code ? `<b class="rb-code">${esc(a.atis_code)}</b>` : ""}</span>` : "";
          const extra = g.atis ? [`ATIS: ${posLine(g.atis)}${a ? ` – online${a.atis_code ? `, informacja ${a.atis_code}` : ""}` : " – offline"}`] : [];
          const id = nm(g.nominal);
          out.push({ at: g.at, prio: tw ? 2 : 2.5, dot: "twr", lb: { cls: "twr", state: tw ? "on" : cover ? "cov" : "off", id, cid: shortId(id), ls, extra: atl,
            icao: g.icao, k: g.icao, rt: groupTip(g, `${g.id} · ${g.icao}`, [...extra, "Kliknij: AERODROME"],
              cover ? `Wieża offline – przejmuje ${g.owners.map((o) => who(o) || o).join(", ")}` : null) } });
        });
      }
      if (layers.has("fis")) {
        d.fis.forEach((g) => {
          if (!g.at) return;
          const { ls, more } = local(g);
          const id = nm(g.nominal);
          out.push({ at: g.at, prio: g.owners.length ? 3 : 3.5, dot: "fis", lb: { cls: "fis", state: g.owners.length ? "on" : "off", id, cid: shortId(id), ls, more,
            rt: groupTip(g, `${g.id} (do ${flText(g.hi)})`) } });
        });
      }
      return out;
    }

    function renderMap(m) {
      const p = m.el, W = p.clientWidth, H = p.clientHeight;
      if (!geo || !d || W < 40 || H < 40) return;
      const pr = p.getBoundingClientRect(), fr = m.free.getBoundingClientRect();
      const { P } = projection(border, { x: fr.left - pr.left, y: fr.top - pr.top, w: fr.width, h: fr.height }, 0.03,
        m.key === "MID" ? "bottom" : "middle");
      const ft = m.key === "LOW" ? cut * 100 : LEVEL_FT[m.key];
      const accs = sliceAt(geo.acc, ft);
      const staffed = new Set(d.sectors.filter((s) => s.layer === m.key && s.owner).map((s) => s.letter));
      const low = m.key === "LOW";
      const g = (cls, list) => `<g class="${cls}">${list.map((x) => `<path d="${pathOf(x.geometry, P)}"/>`).join("")}</g>`;
      m.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      m.svg.setAttribute("width", W);
      m.svg.setAttribute("height", H);
      m.svg.innerHTML = g("rb-firs", geo.firs)
        + `<path class="rb-land" d="${pathOf(geo.border, P)}"/>`
        + (low && layers.has("fis") ? g("rb-fisg", geo.fis) : "")
        + (low && layers.has("app") ? g("rb-tmag", geo.tma) : "")
        + (low && (layers.has("twr") || layers.has("twra")) ? g("rb-ctrg", geo.ctr) : "")
        + `<g class="rb-accg">${accs.map((s) => `<path data-k="${esc(s.letter)}" class="${staffed.has(s.letter) ? "st" : ""}" d="${pathOf(s.geometry, P)}"/>`).join("")}</g>`
        + `<path class="rb-border" d="${pathOf(geo.border, P)}"/>`;
      renderTitle(m, { top: fr.top - pr.top, left: fr.left - pr.left, right: pr.right - fr.right });
      // etykiety: wstawiamy wszystkie odmiany, mierzymy, układamy (ważniejsze pierwsze), zostawiamy wybraną odmianę
      const items = labelsFor(m, accs).sort((a, b) => a.prio - b.prio);
      const nv = (it) => (it.prio > 0 ? 3 : 1);
      m.lbls.innerHTML = items.map((it) => Array.from({ length: nv(it) }, (_, v) => lblHtml(it.lb, v)).join("")).join("");
      const els = [...m.lbls.children];
      let ei = 0;
      const vars = items.map((it) => els.slice(ei, (ei += nv(it))));
      const sz = (e) => ({ w: e.offsetWidth, h: e.offsetHeight });
      const pts = items.map((it) => P([it.at[1], it.at[0]]));
      // przeszkody: stosy sąsiadów i tytuł podglądu (współrzędne w panelu)
      const rel = (e) => { const r = e.getBoundingClientRect(); return { x0: r.left - pr.left - 2, y0: r.top - pr.top - 2, x1: r.right - pr.left + 2, y1: r.bottom - pr.top + 2 }; };
      const obstacles = [...p.querySelectorAll(".rb-nb, .rb-title")].filter((e) => e.offsetWidth && e.offsetHeight).map(rel);
      const moves = placeLabels(items.map((it, i) => {
        const [full, ...alts] = vars[i].map(sz);
        return { x: pts[i][0], y: pts[i][1], ...full, alts, max: it.max };
      }), { x: 2, y: 2, w: W - 4, h: H - 4 }, { obstacles });
      // przesunięta etykieta: cienka linia do jej punktu; lotniska (TWR), TMA (APP) i FIS z kropką w punkcie
      let marks = "";
      items.forEach((it, i) => {
        const { dx, dy, v } = moves[i];
        vars[i].forEach((e, j) => { if (j !== v) e.remove(); });
        const e = vars[i][v], w = e.offsetWidth, hh = e.offsetHeight, [x, y] = pts[i];
        e.style.left = `${Math.round(x + dx - w / 2)}px`;
        e.style.top = `${Math.round(y + dy - hh / 2)}px`;
        if (it.dot) marks += `<circle class="rb-dot ${it.dot}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2"/>`;
        if (Math.abs(dx) > w / 2 + 2 || Math.abs(dy) > hh / 2 + 2) {
          const ex = Math.max(x + dx - w / 2, Math.min(x, x + dx + w / 2)), ey = Math.max(y + dy - hh / 2, Math.min(y, y + dy + hh / 2));
          marks += `<path class="rb-lead" d="M${x.toFixed(1)},${y.toFixed(1)}L${ex.toFixed(1)},${ey.toFixed(1)}"/>`;
        }
      });
      if (marks) m.svg.insertAdjacentHTML("beforeend", `<g class="rb-marks">${marks}</g>`);
    }
    // tytuł w górnym rogu wolnego pola (bez stosów sąsiadów) od strony środkowej kolumny
    function renderTitle(m, free) {
      const t = m.el.querySelector(".rb-title");
      if (m.key === "LOW") Object.assign(t.style, { top: `${Math.round(free.top)}px`, right: `${Math.round(free.right)}px` });
      if (m.key === "HIGH") Object.assign(t.style, { top: `${Math.round(free.top)}px`, left: `${Math.round(free.left)}px` });
      const ly = d?.layers?.[m.key];
      let html = `<b>${m.key}</b> <span>${esc(ly?.label || "")}</span>`;
      if (m.key === "LOW" && geo) {
        const b = cutBounds(geo.acc.filter((s) => s.lo < (ly?.upper_ft ?? 33500) && s.hi > (ly?.lower_ft ?? 9500)), ly);
        const c = cutBand(b, cut);
        html += `<span class="rb-cut" title="Kształty sektorów LOW zmieniają się z wysokością: przekrój pokazuje przedział ${flText(c.lo)}–${flText(c.hi)}">`
          + `<button type="button" data-step="-1" ${c.i <= 0 ? "disabled" : ""} title="Niższy przedział">◀</button>`
          + `${flText(c.lo)}–${flNum(c.hi)}<button type="button" data-step="1" ${c.i >= c.n - 1 ? "disabled" : ""} title="Wyższy przedział">▶</button></span>`;
      }
      t.innerHTML = html;
    }
    function renderButtons() {
      el.querySelectorAll(".rb-btns button[data-ly]").forEach((b) => {
        b.classList.toggle("on", layers.has(b.dataset.ly));
        b.setAttribute("aria-pressed", String(layers.has(b.dataset.ly)));
      });
      el.classList.toggle("noatis", !layers.has("atis"));
    }
    function renderStatus() {
      const s = $(".rb-status");
      const n = d ? controllersOn(d.online) : 0;
      const net = d?.net;
      s.innerHTML = !d ? (err ? `<span class="error">${esc(err)}</span>` : "Wczytywanie…")
        : `${net?.ok ? `VATSIM ${esc(utcHhmm(net.updated))} · ${n} na tablicy` : `<span class="error">${esc(net?.error || "VATSIM: brak danych")}</span>`}`
          + (noBorder ? ` <span class="error">Brak granicy FIR EPWW (VATSpy): podglądy bez obrysu FIR, stosy sąsiadów mogą być puste.</span>` : "")
          + (err ? ` <span class="error">${esc(err)}</span>` : "");
      s.title = d?.source ? `Kolejność przejmowania: ${d.source}` : "";
    }
    let raf = 0;
    // jeden błąd (np. dziwne dane) nie zatrzymuje reszty strony
    const safe = (f, ...a) => { try { f(...a); } catch (e) { console.error("RADIO", e); } };
    function renderAll() {
      cancelAnimationFrame(raf);
      raf = 0;
      safe(renderButtons);
      if (d) {
        safe(renderAtis);
        safe(renderStacks);
        Object.values(maps).forEach((m) => safe(renderMap, m));
      }
      safe(renderStatus);
    }
    const later = () => { if (!raf) raf = requestAnimationFrame(renderAll); };

    // --- dane
    const visible = () => el.isConnected && !!el.closest(".view.active") && document.visibilityState !== "hidden";
    async function load() {
      if (loading) return;
      loading = true;
      try {
        // geometria raz, potem tylko gdy zmieni się wersja danych (np. po imporcie nowego pliku .ese)
        const r = await api(`/api/radio/board${geo && geoVer ? `?have=${encodeURIComponent(geoVer)}` : "?geo=1"}`);
        if (r.geo) {
          geo = r.geo;
          geoVer = r.ver || "";
          const b = bboxOrDefault(bboxOf(geo.border));
          noBorder = !b;
          border = b || EPWW_BBOX;
        } else if (geo && r.ver !== geoVer) {
          geoVer = "";  // nowa wersja danych bez geometrii w odpowiedzi: pobierzemy ją zaraz (?geo=1)
          setTimeout(load, 0);
        }
        d = r;
        err = "";
        lastLoad = Date.now();
      } catch (e) {
        err = `RADIO: ${e.message}`;
      } finally {
        loading = false;
      }
      renderAll();
    }
    const tick = () => { if (visible()) load(); };
    // powrót do karty przeglądarki: dane mogą mieć do 30 s – odświeżamy od razu
    const onVis = () => { if (visible() && Date.now() - lastLoad > REFRESH_MS) load(); };
    document.addEventListener("visibilitychange", onVis);
    loadDisplayNames().then(() => { if (d) later(); });
    load();
    timer = setInterval(tick, REFRESH_MS);
    const ro = new ResizeObserver(later);
    ro.observe($(".rb-grid"));

    // --- warstwy i przekrój: zapamiętane w przeglądarce, każda zmiana to krok do cofnięcia (#radio/atis,low/fl300)
    const save = () => { lsSet(LS_LAYERS, JSON.stringify([...layers])); lsSet(LS_CUT, String(cut)); };
    const apply = (arg) => {
      const a = parseArg(arg);
      let changed = false;
      if (a.layers && argOf(a.layers, cut) !== argOf(layers, cut)) { layers = a.layers; changed = true; }
      if (a.cut && a.cut >= 95 && a.cut <= 335 && a.cut !== cut) { cut = a.cut; changed = true; }
      if (changed) { save(); renderAll(); }
    };
    el.querySelector(".rb-btns").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-ly]");
      if (!b) return;
      const s = new Set(layers);
      if (s.has(b.dataset.ly)) s.delete(b.dataset.ly); else s.add(b.dataset.ly);
      // TWR/ATIS zawiera TWR: włączenie jednej z nich wyłącza drugą
      if (s.has(b.dataset.ly) && b.dataset.ly === "twra") s.delete("twr");
      if (s.has(b.dataset.ly) && b.dataset.ly === "twr") s.delete("twra");
      layers = s;
      save();
      renderAll();
      remember("radio", argOf(layers, cut));
    });
    maps.LOW.el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-step]");
      if (!b || !geo) return;
      const ly = d?.layers?.LOW;
      const bounds = cutBounds(geo.acc.filter((s) => s.lo < (ly?.upper_ft ?? 33500) && s.hi > (ly?.lower_ft ?? 9500)), ly);
      const c = cutBand(bounds, cut);
      const i = Math.max(0, Math.min(c.n - 1, c.i + Number(b.dataset.step)));
      cut = cutOf(bounds, i);
      save();
      renderAll();
      remember("radio", argOf(layers, cut));
    });
    // lotnisko z paska ATIS albo etykiety TWR → AERODROME (krok do cofnięcia)
    el.addEventListener("click", (e) => {
      const a = e.target.closest(".rb-at[data-icao], .rb-lbl.twr[data-icao]");
      if (a) navigate("aerodrome", a.dataset.icao);
    });
    // etykieta TWR z klawiatury (tabindex, role=button): Enter albo spacja
    el.addEventListener("keydown", (e) => {
      const a = e.target.closest?.(".rb-lbl.twr[data-icao]");
      if (a && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); navigate("aerodrome", a.dataset.icao); }
    });

    // --- dymki (jeden pływający, przycięty do okna) i podświetlenie sektora pod etykietą
    let tipFor = null;
    const moveTip = (e) => {
      const r = tip.getBoundingClientRect();
      tip.style.left = `${Math.max(4, Math.min(window.innerWidth - r.width - 4, e.clientX + 14))}px`;
      tip.style.top = `${Math.max(4, e.clientY + 16 + r.height > window.innerHeight ? e.clientY - r.height - 10 : e.clientY + 16)}px`;
    };
    const hl = (lbl, onOff) => {
      const k = lbl?.dataset.k;
      const svg = lbl?.closest(".rb-panel")?.querySelector("svg");
      if (k && svg && lbl.classList.contains("acc")) svg.querySelectorAll(`[data-k="${CSS.escape(k)}"]`).forEach((p) => p.classList.toggle("hl", onOff));
    };
    el.addEventListener("mouseover", (e) => {
      const t = e.target.closest("[data-rt]");
      if (t === tipFor) return;
      hl(tipFor, false);
      tipFor = t;
      if (!t) { tip.hidden = true; return; }
      tip.textContent = t.dataset.rt;
      tip.hidden = false;
      moveTip(e);
      hl(t, true);
    });
    el.addEventListener("mousemove", (e) => { if (tipFor) moveTip(e); });
    el.addEventListener("mouseleave", () => { hl(tipFor, false); tipFor = null; tip.hidden = true; });

    return {
      activate(arg) {
        if (arg) apply(arg);
        // menu albo adres bez stanu: dopisujemy bieżące warstwy do adresu (bez nowego kroku)
        remember("radio", argOf(layers, cut));
        if (Date.now() - lastLoad > REFRESH_MS) load();
        else later();
      },
      destroy() { clearInterval(timer); ro.disconnect(); document.removeEventListener("visibilitychange", onVis); },
    };
  },
};

function panelHtml(m) {
  const frame = m.side === "left"
    ? `<div class="rb-frame"><div class="rb-col"></div><div class="rb-top"></div><div class="rb-free"></div><div class="rb-bot"></div></div>`
    : m.side === "right"
      ? `<div class="rb-frame"><div class="rb-top"></div><div class="rb-free"></div><div class="rb-bot"></div><div class="rb-col"></div></div>`
      : `<div class="rb-frame"><div class="rb-free"></div></div>`;
  return `<section class="rb-panel ${m.cls}" data-map="${m.key}"><svg class="rb-svg" aria-hidden="true"></svg>${frame}
    <div class="rb-lbls"></div><div class="rb-title"></div></section>`;
}
