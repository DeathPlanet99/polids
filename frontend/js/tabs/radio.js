// RADIO: jedna strona jak w PANDORZE (runda 10, Marek): u góry pasek ATIS, po lewej duży podgląd LOW z sektoryzacją
// FIR EPWW i stosami sąsiadów (ESAA, EDWW, EDUU, EDMM, LKAA), w środku przyciski warstw i mały podgląd MID, po prawej
// duży podgląd HIGH ze stosami UMKK, EYVL, UMMV, UKLV, LZBB. Na podglądach: ID sektora (litera warstwy + litera sektora,
// np. LB) i częstotliwość stanowiska, które teraz go obsługuje wg kolejności przejmowania (ownership.json); bez nikogo
// online przygaszona częstotliwość stanowiska sektora. Dane: GET /api/radio/board co 30 s (geometria raz na sesję).
import { api, esc, h, hhmm, lsGet, lsSet, navigate, remember } from "../api.js";
import { labelPoint } from "../coverage.js";
import { displayName, loadDisplayNames, posName } from "../posname.js";
import { DEFAULT_CUT, DEFAULT_LAYERS, LAYERS, LAYER_IDS, argOf, bboxOf, byShort, cutBand, cutBounds, cutOf, flNum, flText,
  levelLines, parseArg, pathOf, placeLabels, projection, ringsOf, sliceAt } from "../radioboard.js";

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
            <div class="rb-btns">${LAYERS.map((l) => `<button type="button" data-ly="${l.id}" title="${esc(l.tip)}">${esc(l.label)}</button>`).join("")}</div>
            <div class="rb-status"></div>
            <div class="rb-legend"><span><i class="rb-sw on"></i>obsadzony</span><span><i class="rb-sw off"></i>nikt (nominalna)</span></div>
            ${panelHtml(m)}
          </div>` : panelHtml(m)).join("")}
      </div>
      <div class="rb-tip" hidden></div>
    </div>`);
    root.append(el);
    const $ = (s) => el.querySelector(s);
    const tip = $(".rb-tip");

    let layers = readLayers(), cut = readCut();
    let d = null, geo = null, err = "", border = null, timer = null, loading = false, lastLoad = 0;
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
      return o ? `${o.callsign} ${o.frequency} – ${o.name || "?"} (CID ${o.cid ?? "?"})${o.logon_time ? `, od ${hhmm(o.logon_time)}` : ""}` : "";
    };
    const posLine = (cs) => `${cs}${d?.pos?.[cs]?.name ? ` · ${d.pos[cs].name}` : ""} ${d?.pos?.[cs]?.frequency || ""}`.trim();
    const chainText = (ch) => ch.map(nm).join(" › ");
    // linijki etykiety: właściciele (najwyżej dwaj) albo częstotliwość nominalna; by = skrót właściciela innego niż nominalny
    const lines = (nominal, owners, short = false) => (owners.length
      ? owners.slice(0, 2).map((o) => ({ fq: freqOf(o), by: o === nominal ? "" : short ? byShort(nm(o)) : nm(o) }))
      : [{ fq: d?.pos?.[nominal]?.frequency || "—", by: "" }]);
    const nowText = (owners, nominal) => (owners.length ? owners.map((o) => `Teraz: ${who(o) || o}`).join("\n")
      : `Teraz nikt – częstotliwość nominalna ${nm(nominal)} ${d?.pos?.[nominal]?.frequency || ""}`.trim());

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
    const groupTip = (g, title, extra = []) => [title, `Stanowisko: ${posLine(g.nominal)}`,
      g.owners.length || !g.topdown ? nowText(g.owners, g.nominal) : `Teraz top-down: ${who(g.topdown) || g.topdown}`, ...extra,
      `Kolejność: ${chainText(g.chains[0] || [])}`].join("\n");

    function labelsFor(m, accs) {
      const out = [];  // {html, at: [lat, lon], prio}
      if (layers.has(m.layer)) {
        const byLetter = new Map();
        accs.forEach((s) => { const b = byLetter.get(s.letter); if (!b || areaOf(s.geometry) > areaOf(b.geometry)) byLetter.set(s.letter, s); });
        d.sectors.filter((s) => s.layer === m.key && byLetter.has(s.letter)).forEach((s) => {
          const sl = byLetter.get(s.letter);
          const at = labelPoint({ type: "Feature", geometry: sl.geometry, properties: { fir: "EPWW", name: sl.name } })?.at;
          if (at) out.push({ at, prio: 0, html: boxHtml({ cls: "rb-lbl acc", id: s.id, nominal: s.nominal, owners: s.owner ? [s.owner] : [], rt: accTip(s), k: s.letter }) });
        });
      }
      if (m.key !== "LOW") return out;  // APP, TWR i FIS to przestrzeń dolna: na podglądzie LOW
      if (layers.has("app")) {
        d.tma.forEach((g) => g.at && out.push({ at: g.at, prio: 1, dot: "app", html: boxHtml({ cls: "rb-lbl app", id: nm(g.nominal), nominal: g.nominal, owners: g.owners,
          rt: groupTip(g, `${g.id} (${g.lo ? flText(g.lo) : "GND"}–${flText(g.hi)})`, g.apps.length > 1 ? [`APP: ${g.apps.map((c) => `${nm(c)} ${d.pos[c]?.frequency || ""}`).join(", ")}`] : []) }) }));
      }
      const twra = layers.has("twra");
      if (twra || layers.has("twr")) {
        d.twr.forEach((g) => {
          if (!g.at) return;
          const a = g.atis ? on(g.atis) : null;
          const atl = twra && g.atis ? `<span class="rb-ln rb-atl${a ? " on" : ""}"><i class="rb-by">ATIS</i><span class="rb-fq">${esc(freqOf(g.atis))}</span>${a?.atis_code ? `<b class="rb-code">${esc(a.atis_code)}</b>` : ""}</span>` : "";
          const extra = g.atis ? [`ATIS: ${posLine(g.atis)}${a ? ` – online${a.atis_code ? `, informacja ${a.atis_code}` : ""}` : " – offline"}`] : [];
          out.push({ at: g.at, prio: 2, dot: "twr", html: boxHtml({ cls: "rb-lbl twr", id: nm(g.nominal), nominal: g.nominal, owners: g.owners,
            rt: groupTip(g, `${g.id} · ${g.icao}`, [...extra, "Kliknij: AERODROME"]), k: g.icao, extra: atl }), icao: g.icao });
        });
      }
      if (layers.has("fis")) {
        d.fis.forEach((g) => g.at && out.push({ at: g.at, prio: 3, dot: "fis", html: boxHtml({ cls: "rb-lbl fis", id: nm(g.nominal), nominal: g.nominal, owners: g.owners,
          rt: groupTip(g, `${g.id} (do ${flText(g.hi)})`) }) }));
      }
      return out;
    }

    function renderMap(m) {
      const p = m.el, W = p.clientWidth, H = p.clientHeight;
      if (!geo || !d || W < 40 || H < 40) return;
      const pr = p.getBoundingClientRect(), fr = m.free.getBoundingClientRect();
      const { P, box } = projection(border, { x: fr.left - pr.left, y: fr.top - pr.top, w: fr.width, h: fr.height }, 0.03,
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
      // etykiety: wstawiamy, mierzymy, rozsuwamy (ważniejsze zostają na miejscu)
      const items = labelsFor(m, accs).sort((a, b) => a.prio - b.prio);
      m.lbls.innerHTML = items.map((it) => it.html).join("");
      const els = [...m.lbls.children];
      const sizes = els.map((e) => [e.offsetWidth, e.offsetHeight]);
      const pts = items.map((it) => P([it.at[1], it.at[0]]));
      // etykiety mogą wyjść trochę poza FIR (MID: w całym panelu, nad mapą jest miejsce)
      const bx0 = Math.max(2, box.x - 30), by0 = Math.max(2, box.y - 20);
      const bounds = m.key === "MID" ? { x: 2, y: 2, w: W - 4, h: H - 4 }
        : { x: bx0, y: by0, w: Math.min(W - 2, box.x + box.w + 30) - bx0, h: Math.min(H - 2, box.y + box.h + 20) - by0 };
      const moves = placeLabels(items.map((it, i) => ({ x: pts[i][0], y: pts[i][1], w: sizes[i][0], h: sizes[i][1] })), bounds);
      // przesunięta etykieta: cienka linia do jej punktu; lotniska (TWR) i TMA (APP) z kropką w punkcie
      let marks = "";
      els.forEach((e, i) => {
        const [w, hh] = sizes[i], [x, y] = pts[i], { dx, dy } = moves[i];
        e.style.left = `${Math.round(x + dx - w / 2)}px`;
        e.style.top = `${Math.round(y + dy - hh / 2)}px`;
        if (items[i].icao) e.dataset.icao = items[i].icao;
        if (items[i].dot) marks += `<circle class="rb-dot ${items[i].dot}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2"/>`;
        if (Math.abs(dx) > w / 2 + 2 || Math.abs(dy) > hh / 2 + 2) {
          const ex = Math.max(x + dx - w / 2, Math.min(x, x + dx + w / 2)), ey = Math.max(y + dy - hh / 2, Math.min(y, y + dy + hh / 2));
          marks += `<path class="rb-lead" d="M${x.toFixed(1)},${y.toFixed(1)}L${ex.toFixed(1)},${ey.toFixed(1)}"/>`;
        }
      });
      if (marks) m.svg.insertAdjacentHTML("beforeend", `<g class="rb-marks">${marks}</g>`);
      renderTitle(m, { top: fr.top - pr.top, left: fr.left - pr.left, right: pr.right - fr.right });
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
      el.querySelectorAll(".rb-btns button[data-ly]").forEach((b) => b.classList.toggle("on", layers.has(b.dataset.ly)));
      el.classList.toggle("noatis", !layers.has("atis"));
    }
    function renderStatus() {
      const s = $(".rb-status");
      const n = d ? Object.keys(d.online || {}).filter((cs) => !/_ATIS$/.test(cs)).length : 0;
      const net = d?.net;
      s.innerHTML = !d ? (err ? `<span class="error">${esc(err)}</span>` : "Wczytywanie…")
        : `${net?.ok ? `VATSIM ${esc(hhmm(net.updated))} · ${n} na tablicy` : `<span class="error">${esc(net?.error || "VATSIM: brak danych")}</span>`}`
          + (err ? ` <span class="error">${esc(err)}</span>` : "");
      s.title = d?.source ? `Kolejność przejmowania: ${d.source}` : "";
    }
    let raf = 0;
    function renderAll() {
      cancelAnimationFrame(raf);
      raf = 0;
      renderButtons();
      renderStatus();
      if (!d) return;
      renderAtis();
      renderStacks();
      Object.values(maps).forEach(renderMap);
    }
    const later = () => { if (!raf) raf = requestAnimationFrame(renderAll); };

    // --- dane
    const visible = () => el.isConnected && !!el.closest(".view.active") && document.visibilityState !== "hidden";
    async function load() {
      if (loading) return;
      loading = true;
      try {
        const r = await api(`/api/radio/board${geo ? "" : "?geo=1"}`);
        if (r.geo) {
          geo = r.geo;
          border = bboxOf(geo.border);
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
      destroy() { clearInterval(timer); ro.disconnect(); },
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
