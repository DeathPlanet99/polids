import { api, atisHtml, esc, fmt, h, hhmm, lvpState, lvpTip, positionTip, remember, stash, stashed, vatsimBookings, vatsimOnline, lsGet, lsSet, ONLINE_EVENT } from "../api.js";
import { colorize, wxLines, windVec, hwTxt, xwTxt, kt, validTxt, pointTip } from "./meteo.js";
import { FIR_KEYS, FIR_LABELS, aeroArg, firOf, mountAeroCard, mountFirList, obsInfo, splitArg } from "../aerolist.js";
import { mountAwos } from "../awos.js";
import { notamState } from "../notamstate.js";
import { mountDman } from "../dman.js";
import { mountOverview } from "../overview.js";

const TYPE_ORDER = ["CTR", "FSS", "APP", "DEP", "TWR", "GND", "DEL", "ATIS"];
const byType = (a, b) => TYPE_ORDER.indexOf(a.callsign.split("_").pop()) - TYPE_ORDER.indexOf(b.callsign.split("_").pop()) || a.callsign.localeCompare(b.callsign);

// Stan LVP z progami z AIP (dymek: przygotowanie, wprowadzenie, odwołanie, źródło); LVP w mocy pokazuje, czego
// brakuje do odwołania, odwołane LVP godzinę odwołania (przez 30 min), "nie dotyczy" = AIP nie przewiduje LVP
function lvpBox(lvp) {
  if (!lvp) return "";
  const s = lvpState(lvp), tip = esc(lvpTip(lvp));
  if (s.cls === "na") return `<div class="lvp lvp-off lvp-na" data-tip="${tip}">LVP <span>nie dotyczy</span></div>`;
  if (s.cls === "off") return `<div class="lvp lvp-off" data-tip="${tip}">LVP <span>nie obowiązują</span></div>`;
  const scope = lvp.scope === "DEP" ? " · ODLOTY" : "";
  return `<div class="lvp lvp-${s.cls}" data-tip="${tip}">${esc(s.label)}${scope}<span>${(lvp.reasons || []).map(esc).join("<br>")}</span></div>`;
}

// Częstotliwości z AIP (data/seed/frequencies.csv), gdy plik .ese nie zna stanowisk lotniska: służba, znak, MHz, uwaga
// (np. EPRA: TWR cywilna i wojskowa na tej samej częstotliwości), godziny
function aipFreqs(list) {
  if (!list?.length) return "";
  const srcs = new Set(list.map((x) => (/^AIP/.test(x.source || "") ? "AIP" : "lotniska.dlapilota.pl")));
  return list.map((x) => `<div class="radio-row aip-freq" data-tip="${esc([x.description, x.note, x.hours && `godziny: ${x.hours}`, x.source && `źródło: ${x.source}`].filter(Boolean).join("\n"))}">
      <span class="freq">${esc(x.mhz)}</span><span class="pos-txt"><span class="cs">${esc(x.kind)}</span><span class="nm">${esc(x.description || "")}</span>
      ${x.note ? `<span class="fq-note">${esc(x.note)}</span>` : ""}${x.hours ? `<span class="hrs">${esc(x.hours)}</span>` : ""}</span></div>`).join("")
    + `<p class="hint">wg ${[...srcs].sort().join(" i ")} · godziny UTC zimą, w nawiasie latem</p>`;
}

// Róża wiatrów: pasy jako prostokąty, strzałka wiatru skąd wieje.
// Kolor = rola jak na paskach postępu lotu (strips.css): ARR żółty, DEP niebieski (ten sam pas do obu: połowa na połowę).
// Wypełnienie pełne = pas w użyciu wg ATIS; kreskowane = pas preferowany (sugestia systemu).
const USE_COLOR = { arr: "#ffd84a", dep: "#6db8ff" };
const ROLE_LABEL = { arr: "ARR", dep: "DEP" };
function roleSets(status) {
  const use = status.runway_in_use || {};
  const pref = status.preferred || {};
  const atis = use.source === "ATIS" ? use : null;
  const sugg = atis ? pref : (pref.arr ? pref : use);
  // sugestię pokazujemy tylko tam, gdzie różni się od ATIS
  const s = { arr: sugg.arr, dep: sugg.dep || sugg.arr };
  if (atis) { if (s.arr === atis.arr) s.arr = null; if (s.dep === (atis.dep || atis.arr)) s.dep = null; }
  return { active: atis ? { arr: atis.arr, dep: atis.dep || atis.arr } : {}, sugg: s };
}
// pas główny: w użyciu, z podejściem albo twardy od 1200 m (jak upperwind.approach_runways w backendzie); trawiaste,
// awaryjne i krótkie pasy prawie równoległe do głównego (EPRZ 08L/26R, 09ES/27ES) na róży nakładałyby się na niego,
// więc ich nie rysujemy; krótki pas przecinający (np. EPML 17/35) zostaje
const SOFT_SURFACE = /gras|grs|\bgre\b|grv|gravel|turf|emergency|^g$/i;
const mainRunway = (r, inUse) => inUse || (r.equipment || []).length > 0
  || (!/ES$/i.test(r.designator) && !SOFT_SURFACE.test((r.surface || "").trim()) && (r.length_m || 0) >= 1200);
const axisDiff = (a, b) => { const d = Math.abs(a - b) % 180; return Math.min(d, 180 - d); };
// drugi koniec tego samego pasa: 29R -> 11L, 09 -> 27
const reciprocal = (d) => {
  const m = /^(\d{1,2})([LRC]?)$/.exec(d || "");
  return m ? String((+m[1] + 18) % 36 || 36).padStart(2, "0") + ({ L: "R", R: "L" }[m[2]] ?? m[2]) : null;
};

function windrose(status) {
  const p = status.parsed || {};
  const { active, sugg } = roleSets(status);
  const size = 280, c = size / 2, r = 112;
  const pt = (deg, d) => [c + Math.sin(deg * Math.PI / 180) * d, c - Math.cos(deg * Math.PI / 180) * d];
  const pairs = [];
  const used = [active.arr, active.dep, sugg.arr, sugg.dep];
  const main = status.runways.filter((x) => mainRunway(x, used.includes(x.designator)));
  const mainIds = new Set(main.flatMap((x) => [x.designator, reciprocal(x.designator)]));
  const shown = status.runways.filter((x) => mainIds.has(x.designator) || !main.some((m) => axisDiff(m.heading, x.heading) < 20));
  shown.forEach((x) => {
    const key = Math.round((x.heading % 180) / 5);
    let g = pairs.find((q) => q.key === key);
    if (!g) pairs.push(g = { key, ends: [] });
    if (!g.ends.some((e) => e.designator === x.designator)) g.ends.push(x);
  });
  const ticks = Array.from({ length: 36 }, (_, i) => {
    const [x1, y1] = pt(i * 10, r), [x2, y2] = pt(i * 10, r - (i % 9 === 0 ? 12 : 6));
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#9aa4af"/>`;
  }).join("");
  const labels = ["N", "E", "S", "W"].map((t, i) => {
    const [x, y] = pt(i * 90, r + 13);
    return `<text x="${x}" y="${y + 4}" fill="#9aa4af" font-size="12" text-anchor="middle">${t}</text>`;
  }).join("");
  const defs = `<defs>${["arr", "dep"].map((k) => `<pattern id="wr-h-${k}" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
    <rect width="6" height="6" fill="#151a1f"/><rect width="3" height="6" fill="${USE_COLOR[k]}"/></pattern>`).join("")}</defs>`;
  const rolesOf = (set, d) => ["arr", "dep"].filter((k) => set[k] === d);
  let rw = "", marks = "";
  pairs.forEach((g) => {
    const hdg = g.ends[0].heading;
    const act = [...new Set(g.ends.flatMap((e) => rolesOf(active, e.designator)))];
    const sug = [...new Set(g.ends.flatMap((e) => rolesOf(sugg, e.designator)))];
    const roles = act.length ? act : sug;
    const solid = act.length > 0;
    const fill = (k) => (solid ? USE_COLOR[k] : `url(#wr-h-${k})`);
    const tr = `transform="rotate(${hdg} ${c} ${c})"`;
    if (!roles.length) {
      rw += `<rect x="${c - 7}" y="${c - 72}" width="14" height="144" fill="#555" stroke="#888" ${tr}/>`;
    } else if (roles.length === 1) {
      rw += `<rect x="${c - 7}" y="${c - 72}" width="14" height="144" fill="${fill(roles[0])}" ${tr}/>`;
    } else {
      rw += `<rect x="${c - 7}" y="${c - 72}" width="7" height="144" fill="${fill("arr")}" ${tr}/><rect x="${c}" y="${c - 72}" width="7" height="144" fill="${fill("dep")}" ${tr}/>`;
    }
    if (roles.length) {
      rw += `<rect x="${c - 7}" y="${c - 72}" width="14" height="144" fill="none" stroke="#fff" stroke-width="1.5" ${solid ? "" : 'stroke-dasharray="5 3"'} ${tr}/>`;
    }
    g.ends.forEach((e) => {
      const ra = rolesOf(active, e.designator), rs = rolesOf(sugg, e.designator);
      const ro = ra.length ? ra : rs;
      // oznaczenie przy progu (z którego startuje / na który ląduje samolot lecący kursem pasa) i rola
      const [x, y] = pt(e.heading + 180, 88);
      const col = ro.length ? USE_COLOR[ro[0]] : "#c8ced4";
      marks += `<text x="${x}" y="${y + 3}" text-anchor="middle" font-family="Consolas, 'Liberation Mono', monospace" font-weight="700"
        font-size="${ro.length ? 16 : 12}" fill="${col}">${esc(e.designator)}</text>`;
      if (ro.length) {
        marks += `<text x="${x}" y="${y + 15}" text-anchor="middle" font-family="Consolas, 'Liberation Mono', monospace" font-weight="700" font-size="10"
          fill="${col}">${ro.map((k) => ROLE_LABEL[k]).join("/")}${ra.length ? "" : "?"}</text>`;
        // szewrony wzdłuż pasa w kierunku ruchu
        marks += `<g transform="rotate(${e.heading} ${c} ${c})" fill="none" stroke="#fff" stroke-width="2.5" opacity="${ra.length ? 1 : 0.7}">${[48, 12, -24]
          .map((o) => `<polyline points="${c - 5},${c + o + 6} ${c},${c + o} ${c + 5},${c + o + 6}" stroke="#000" stroke-width="5"/><polyline points="${c - 5},${c + o + 6} ${c},${c + o} ${c + 5},${c + o + 6}"/>`).join("")}</g>`;
      }
    });
  });
  let arrow = "";
  if (p.wind_dir !== null && p.wind_dir !== undefined) {
    arrow = `<g transform="rotate(${p.wind_dir} ${c} ${c})"><line x1="${c}" y1="${c - r + 4}" x2="${c}" y2="${c - 34}" stroke="#2f8fff" stroke-width="4"/>
      <polygon points="${c - 9},${c - 46} ${c + 9},${c - 46} ${c},${c - 30}" fill="#2f8fff"/></g>`;
  }
  if (p.wind_var_from !== null && p.wind_var_from !== undefined) {
    const [x1, y1] = pt(p.wind_var_from, r - 18), [x2, y2] = pt(p.wind_var_to, r - 18);
    const span = ((p.wind_var_to - p.wind_var_from) + 360) % 360;
    arrow += `<path d="M${x1},${y1} A${r - 18},${r - 18} 0 ${span > 180 ? 1 : 0} 1 ${x2},${y2}" stroke="#ffb020" stroke-width="3" fill="none"/>`;
  }
  return `<svg class="windrose" viewBox="0 0 ${size} ${size}">${defs}<circle cx="${c}" cy="${c}" r="${r}" fill="#1b1f24" stroke="#3c444e"/>${ticks}${labels}${rw}${arrow}${marks}</svg>
    <div class="wr-legend"><span><i class="sw-arr"></i>ARR</span><span><i class="sw-dep"></i>DEP</span><span><i class="sw-solid"></i>ATIS</span><span><i class="sw-hatch"></i>sugestia</span></div>`;
}

// Pas w użyciu z ATIS albo preferowany wg systemu (wiatr, wyposażenie, LVP)
function runwayBox(st) {
  const u = st.runway_in_use || {};
  const fromAtis = u.source === "ATIS";
  const atc = st.atc_online || [];
  const title = fromAtis ? `PAS W UŻYCIU · ATIS ${esc(st.atis?.letter || "")}` : "PAS PREFEROWANY";
  // kolory jak na paskach: ARR żółty, DEP niebieski (także gdy oba na tym samym pasie)
  const body = !u.arr ? "–"
    : `<span>ARR</span><b class="arr" style="color:${USE_COLOR.arr}">${esc(u.arr)}</b><span>DEP</span><b class="dep" style="color:${USE_COLOR.dep}">${esc(u.dep || u.arr)}</b>`;
  let note = fromAtis ? `${esc(st.atis.callsign)}` : esc(u.reason || "");
  if (!fromAtis && st.atis) note += `<br>ATIS ${esc(st.atis.letter || "")} online, nie udało się odczytać pasa`;
  else if (!fromAtis && atc.length) note += `<br>Kontrola online (${atc.map((c) => esc(c.callsign)).join(", ")}), brak ATIS`;
  if (fromAtis && st.preferred?.arr && st.preferred.arr !== u.arr) note += `<br><span class="hint">wg wiatru: ${esc(st.preferred.arr)}</span>`;
  return `<div class="rwy-use ${fromAtis ? "atis" : "pref"}"><div class="label">${title}</div><div class="pair">${body}</div><div class="hint">${note}</div></div>`;
}

const EQUIP_SHOW = ["ILS", "LOC", "RNP"];
// pas do lądowania (ATIS albo sugestia) na górze tabeli, potem do startu, potem pozostałe w kolejności z bazy
const roleOrder = (active, sugg) => (d) => (active.arr === d || sugg.arr === d ? 0 : active.dep === d || sugg.dep === d ? 1 : 2);
function runwayTable(st) {
  const { active, sugg } = roleSets(st);
  const tags = (d) => ["arr", "dep"].map((k) => (active[k] === d ? `<span class="tag ${k}">${ROLE_LABEL[k]}</span>`
    : sugg[k] === d ? `<span class="tag ${k} sugg" title="sugestia systemu">${ROLE_LABEL[k]}?</span>` : "")).join("");
  // pasy w użyciu na górze, potem z podejściem, na końcu pozostałe (np. trawiaste): w niskim oknie tabela przewija się
  const order = roleOrder(active, sugg);
  const rank = (r) => order(r.designator) * 2 + ((r.equipment || []).length ? 0 : 1);
  return `<table class="data rwy-table"><thead><tr><th>Pas</th><th></th><th>Kurs</th><th>Dł. m</th><th>Podejście</th><th>Czoł.</th><th>Bocz.</th></tr></thead><tbody>
    ${[...st.runways].sort((a, b) => rank(a) - rank(b)).map((r) => {
      const tg = tags(r.designator);
      const eq = r.equipment || [];
      const tip = `${r.designator}: ${fmt(r.length_m, 0)} × ${fmt(r.width_m, 0)} m, ${r.surface || "?"}; podejścia: ${eq.join(", ") || "brak danych"}`;
      const rl = ["arr", "dep"].filter((k) => active[k] === r.designator || sugg[k] === r.designator);
      // zaokrąglenie jak w karcie wiatru (meteo.js kt): po wartości bezwzględnej, TW dopiero od 1 kt
      const tw = r.headwind !== null && r.headwind < 0 && kt(r.headwind) > 0;
      return `<tr class="${tg ? "inuse" : ""} ${rl.join(" ")}" title="${esc(tip)}"><td class="rwy">${esc(r.designator)}</td><td class="tags">${tg}</td>
      <td class="num">${fmt(r.heading, 0)}°</td><td class="num">${fmt(r.length_m, 0)}</td>
      <td>${eq.filter((k) => EQUIP_SHOW.includes(k)).map((k) => `<span class="eq ${k === "ILS" ? "ils" : ""}">${k}</span>`).join("")}</td>
      <td class="num" style="color:${tw ? "var(--bad)" : "inherit"}">${r.headwind === null ? "–" : (tw ? "TW " : "") + kt(r.headwind) + " kt"}</td>
      <td class="num">${r.crosswind === null ? "–" : kt(r.crosswind) + " kt" + (!kt(r.crosswind) ? "" : r.crosswind > 0 ? " R" : " L")}</td></tr>`;
    }).join("")}
  </tbody></table><div class="hint">TW = wiatr w plecy · ARR? / DEP? = sugestia systemu · najedź na pas: szerokość, nawierzchnia, podejścia</div>`;
}

// Wiatr przy ziemi i na 3000 ft w punkcie podejścia każdego pasa (jak METEO › WIND, bez mapy): METAR i model 10 m
// (Open-Meteo) w punkcie lotniska, 3000 ft AMSL w punkcie FAF/IF z procedury albo na przedłużeniu osi (3°)
function approachWind(aw, st, err) {
  // lotnisko bez pasów w bazie (część lądowisk z listy EPWW, np. EPGM): nie ma czego liczyć, to nie błąd
  if (!aw && st && Array.isArray(st.runways) && !st.runways.length) return `<span class="hint">Brak danych o pasach tego lotniska.</span>`;
  if (!aw) return `<span class="${err ? "error" : "hint"}">${esc(err || "Ładowanie prognozy…")}</span>`;
  const { active, sugg } = roleSets(st || {});
  const rolesOf = (d) => ["arr", "dep"].filter((k) => active[k] === d || sugg[k] === d);
  const isSugg = (k, d) => active[k] !== d;
  const pair = (c) => (c.hw === null && c.xw === null ? "–" : `${hwTxt(c.hw)} ${xwTxt(c.xw)}`);
  // pasy w użyciu (ARR, potem DEP) na górze jak w tabeli pasów, żeby przy niskim oknie (karta przewija się) zostały widoczne
  const order = roleOrder(active, sugg);
  const rows = [...aw.runways].sort((a, b) => order(a.designator) - order(b.designator)).map((r) => {
    const rl = rolesOf(r.designator);
    return `<tr class="${rl.join(" ")}" title="${esc(pointTip(r))}"><td class="rwy"><b>${esc(r.designator)}</b><span class="apw-tags">${rl.map((k) =>
      `<span class="apw-tag ${k}">${ROLE_LABEL[k]}${isSugg(k, r.designator) ? "?" : ""}</span>`).join("")}</span></td>
      <td class="num">${pair(r.c_metar)}</td><td class="num">${pair(r.c_model)}</td>
      <td class="num w">${windVec(r.w3000)}</td><td class="num">${pair(r.c3000)}</td>
      <td class="pt ${r.method}">${esc(r.fix || "oś 3°")}</td></tr>`;
  }).join("");
  const errs = [aw.model_error, aw.metar_error].filter(Boolean);
  return `<div class="apw-sfc"><span><i>0 ft METAR</i><b>${windVec(aw.metar_wind)}</b></span><span><i>model 10 m</i><b>${windVec(aw.model_surface)}</b></span></div>
    <table class="data apw-tab"><thead><tr><th rowspan="2">Pas</th><th colspan="2">0 ft · H/T XW</th><th colspan="2">3000 ft</th><th rowspan="2">Punkt</th></tr>
      <tr><th>METAR</th><th>10 m</th><th>wiatr</th><th>H/T XW</th></tr></thead><tbody>${rows}</tbody></table>
    ${errs.length ? `<div class="error apw-err" title="${esc(errs.join("\n"))}">${errs.map(esc).join(" · ")}</div>` : ""}
    <div class="hint apw-note" title="Punkt podejścia: nazwa = FAF/IF z procedury STAR (.ese), oś 3° = punkt na przedłużeniu osi pasa, w którym ścieżka 3° osiąga 3000 ft AMSL.
H = wiatr czołowy, T = w plecy, L/R = boczny z lewej/prawej, kt. Wiatr 0 ft z modelu: 10 m nad lotniskiem.
${esc(aw.source)} (model ${esc(aw.model)}), prognoza na ${validTxt(aw.valid)}, pobrano ${esc(aw.fetched)}.">punkt: nazwa = FAF z procedury, oś 3° = na przedłużeniu osi, 3° · H/T L/R kt</div>`;
}

const NOTAM_SORT = {
  id: (a, b) => (a.id || "").localeCompare(b.id || ""),
  start: (a, b) => (a.start || "").localeCompare(b.start || ""),
  end: (a, b) => (a.perm - b.perm) || (a.end || "9").localeCompare(b.end || "9"),
};

// Ruch: w PRZEGLĄDZIE tylko jednowierszowe podsumowanie; widok RUCH to DMAN/AMAN (dman.js), paski EFES są w zakładce STRIP
function ruchLine(t, icao) {
  const local = t.departures.filter((p) => p.arrival === icao).length;
  const n = (v, one, few, many) => `<b>${v}</b> ${v === 1 ? one : v % 10 >= 2 && v % 10 <= 4 && (v % 100 < 10 || v % 100 >= 20) ? few : many}`;
  return `<div class="ruch-line"><span><s class="lg dep"></s>${n(t.departures.length - local, "odlot", "odloty", "odlotów")}</span>
    <span><s class="lg arr"></s>${n(t.arrivals.length, "przylot", "przyloty", "przylotów")}</span>
    ${local ? `<span><s class="lg loc"></s>${n(local, "lokalny", "lokalne", "lokalnych")}</span>` : ""}
    ${t.prefiles.length ? `<span><s class="lg plan"></s>${n(t.prefiles.length, "prefile", "prefile", "prefile")}</span>` : ""}
</div>`;
}

function windText(p) {
  if (!p || p.wind_speed === null || p.wind_speed === undefined) return "–";   // bez METAR parsed = {}
  const dir = p.wind_variable ? "VRB" : String(p.wind_dir).padStart(3, "0");
  return `${dir}°/${p.wind_speed}${p.wind_gust ? "G" + p.wind_gust : ""} kt`;
}

// Wschód i zachód słońca oraz zmierzch cywilny (BMCT/EECT) dla lotniska, czas UTC (backend: services/sun.py)
function sunBox(sun) {
  if (!sun) return "";
  const t = (v) => esc(v || "–");
  return `<div class="sunbox ${sun.day ? "day" : "night"}" title="Słońce w UTC: BMCT = początek zmierzchu cywilnego rano, EECT = koniec wieczorem">
    <div class="label">Słońce UTC · ${sun.day ? "dzień" : "noc"}</div>
    <div><span>Wschód</span><b>${t(sun.sunrise)}</b><span>Zachód</span><b>${t(sun.sunset)}</b></div>
    <div class="hint">BMCT ${t(sun.civil_dawn)} · EECT ${t(sun.civil_dusk)}</div></div>`;
}

function value(label, v, extra = "") {
  return `<div class="card"><div class="label">${label}</div><div class="val">${v}</div>${extra}</div>`;
}

export default {
  mount(root, ctx) {
    // Podmenu jak w PANDORZE: 0 = OVERVIEW całego FIR EPWW (overview.js), dalej listy lotnisk FIR-ów (aerolist.js):
    // lotnisko polskie z bazy otwiera PRZEGLĄD / AWOS / RUCH, pozostałe kartę z METAR, TAF i stanowiskami.
    const sub = h(`<nav class="submenu aero-firs"><button data-fir="0" title="OVERVIEW: cały FIR EPWW">0</button>
      ${FIR_KEYS.map((k) => `<button data-fir="${k}" title="${esc(FIR_LABELS[k])}">${k}</button>`).join("")}</nav>`);
    root.append(sub);
    const pane = h(`<div class="pane aero-pane">
      <div class="toolbar">
        <input class="field aero-icao" list="aero-list" size="10" placeholder="ICAO">
        <datalist id="aero-list"></datalist>
        <button class="btn primary go">Pokaż</button>
        <span class="aero-view"><button data-view="overview">PRZEGLĄD</button><button data-view="awos">AWOS</button><button data-view="ruch">RUCH</button></span>
        <span class="title" style="font-weight:600"></span><span style="flex:1"></span>
        <span class="hint upd"></span>
      </div>
      <div class="content"></div>
    </div>`);
    root.append(pane);
    const $ = (s) => pane.querySelector(s);
    // lotnisko pełnej strony; ustawienie serwera default_aerodrome tylko wypełnia nim pole ICAO, zanim użytkownik coś
    // wybierze (pierwsze otwarcie AERODROME to zawsze 0, potem ostatni ekran: initial() niżej)
    let icao = lsGet("aerodrome.icao") || ctx.config.default_aerodrome || "EPWA";
    let timer = null;
    // widok: PRZEGLĄD (siatka poniżej), AWOS (awos.js) albo RUCH (paski EFES, dman.js); w adresie: #aerodrome/EPWA,
    // #aerodrome/EPWA/awos, #aerodrome/EPWA/ruch (krok do cofnięcia). AWOS i RUCH mają własne zegary sprzątane
    // przy zmianie widoku.
    let view = "overview", awos = null, strips = null;
    // tryb ekranu: "ov" (0 = OVERVIEW), "list" (lista FIR-u), "page" (lotnisko z bazy: PRZEGLĄD/AWOS/RUCH), "card" (karta)
    let mode = null, state = null, ov = null, list = null, listKey = null, card = null, cardIcao = null;
    // filtr, wybrany wiersz i przewinięcie listy: każdy wpis historii z listą ma swoje (stash w api.js, ← i Wstecz wracają
    // do listy z jej filtrem, także gdy tę samą listę otwarto później z innym); listState: ostatni stan każdej listy
    // (powrót bez wpisu historii, np. odnośnik do listy z karty)
    const listState = {};

    // lotniska z bazy (pełna strona); lista podpowiedzi ICAO; bez niej pole działa dalej (wpisany kod)
    let adSet = null;
    api("/api/aerodromes").then((ads) => {
      adSet = new Set(ads.map((a) => a.icao));
      $("#aero-list").innerHTML = ads.map((a) => `<option value="${esc(a.icao)}">${esc(a.name)}</option>`).join("");
      // otwarte przed wczytaniem listy lotnisko EP spoza bazy (np. EPKX z navdata): karta zamiast pustej strony
      if (mode === "page" && !adSet.has(icao)) {
        showCard(icao);
        state = icao;
        remember("aerodrome", icao, { replace: true });
      } else saveLast();
    }).catch((e) => console.warn("Lista lotnisk:", e.message));
    const isPage = (code) => code.startsWith("EP") && (!adSet || adSet.has(code));

    const renderRuch = async (el) => {
      try {
        el.innerHTML = ruchLine(await api(`/api/vatsim/airport/${icao}`), icao);
      } catch (e) { el.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
    };

    let notamSort = lsGet("notam.sort") || "id";
    const renderNotams = async (el) => {
      if (!el.innerHTML) el.innerHTML = `<span class="hint">Ładowanie NOTAM…</span>`;
      let data;
      try { data = await api(`/api/notam/${icao}`); } catch (e) { el.innerHTML = `<span class="error">${esc(e.message)}</span>`; return; }
      const draw = () => {
        const list = [...data.notams].sort(NOTAM_SORT[notamSort] || NOTAM_SORT.id);
        el.innerHTML = `<div class="notam-bar">Sortuj: ${[["id", "numer"], ["start", "początek"], ["end", "koniec"]].map(([k, l]) =>
          `<button class="btn ${k === notamSort ? "primary" : ""}" data-sort="${k}">${l}</button>`).join("")}
          <span class="hint">${list.length} NOTAM</span></div>`
          + (list.length ? list.map((n) => {
            const s = notamState(n);
            return `<div class="notam ${s.cls}"><div class="nhead"><b class="mono">${esc(n.id || "")}</b><span class="valid">${esc(s.txt)}</span>
              ${n.schedule ? `<span class="hint">harmonogram: ${esc(n.schedule)}</span>` : ""}</div><pre>${esc(n.raw)}</pre></div>`;
          }).join("") : `<span class="hint">Brak NOTAM-ów dla ${esc(icao)}.</span>`);
      };
      el.onclick = (e) => {
        const b = e.target.closest("button[data-sort]");
        if (!b) return;
        notamSort = b.dataset.sort;
        lsSet("notam.sort", notamSort);
        draw();
      };
      draw();
    };

    const renderFreqs = async (el, info) => {
      const draw = (online = {}, bookings = {}, msg = "") => {
        // częstotliwości FIS (Information) są w RADIO (warstwa FIS); informacja lotniskowa (np. EPBC) zostaje
        const ps = info.atc_positions.filter((p) => !(/information/i.test(p.name) && /_(APP|CTR)$/.test(p.callsign))).sort(byType);
        const extra = Object.keys(bookings).filter((cs) => cs.startsWith(icao + "_") && !ps.some((p) => p.callsign === cs));
        // nazwa stacji najwyżej tak szeroka jak najdłuższa w panelu: imiona i godziny rezerwacji w jednej kolumnie tuż za nią
        const names = ps.length ? ps.map((p) => p.name) : info.frequencies.map((x) => x.description);
        el.style.setProperty("--nm-w", `${Math.max(0, ...names.map((n) => (n || "").length))}ch`);
        el.innerHTML = (ps.map((p) => {
          const on = online[p.callsign], books = bookings[p.callsign] || [];
          const cls = on ? "on" : books.length ? "booked" : "";
          const tip = positionTip(on, books);
          // pos-txt: znak, nazwa stacji i kto (imię i nazwisko albo rezerwacja); przy braku miejsca kto schodzi pod znak (style.css)
          return `<div class="radio-row ${cls}" ${tip ? `data-tip="${esc(tip)}"` : ""}><span class="freq ${cls}">${esc(p.frequency)}</span>
            <span class="pos-txt"><span class="cs">${esc(p.callsign)}</span><span class="nm" title="${esc(p.name)}">${esc(p.name)}</span>
            ${on ? `<b class="who">${esc(on.name || "")}</b>` : books.length ? `<span class="booked-txt">${hhmm(books[0].start)}–${hhmm(books[0].end)}</span>` : ""}</span></div>`;
        }).join("") || aipFreqs(info.frequencies)
          || `<span class="hint">Brak danych</span>`)
          + extra.map((cs) => `<div class="radio-row booked" data-tip="${esc(positionTip(null, bookings[cs]))}"><span class="freq booked">-</span>
            <span class="pos-txt"><span class="cs">${esc(cs)}</span><span class="nm"></span>
            <span class="booked-txt">${hhmm(bookings[cs][0].start)}–${hhmm(bookings[cs][0].end)}</span></span></div>`).join("")
          + `<p class="hint">${msg}</p>`;
      };
      draw();
      const [on, bk] = await Promise.allSettled([vatsimOnline(), vatsimBookings()]);
      const errs = [on, bk].filter((r) => r.status === "rejected").map((r) => esc(r.reason.message));
      draw(on.status === "fulfilled" ? on.value.positions : {}, bk.status === "fulfilled" ? bk.value : {},
        `Zielone = online, przerywana ramka = rezerwacja (najedź, żeby zobaczyć kto). ${errs.length ? `<span class="error">${errs.join(" · ")}</span>` : ""}`);
    };

    // Układ na jeden ekran: trzy kolumny, długie listy przewijają się wewnątrz swoich okienek
    // (checklista jest tylko w zakładce CHECKLIST, loty w widoku RUCH). Szkielet budujemy raz na lotnisko, dane odświeżamy co minutę.
    const build = () => {
      $(".content").innerHTML = `
        <div class="aero-errors"></div>
        <div class="metar-head"></div>
        <div class="aero-grid">
          <div class="aero-col">
            <div class="card aero-wind"></div>
            <div class="card grow"><h3 class="atis-h">ATIS</h3><div class="scroll aero-atis"></div></div>
          </div>
          <div class="aero-col">
            <div class="values compact"></div>
            <div class="card aero-rwy-card"><h3>Pasy</h3><div class="aero-rwys"></div></div>
            <div class="card grow aero-taf-card"><h3>TAF</h3><div class="scroll aero-taf wx"></div></div>
            <div class="card aero-ruch" title="Pokaż ruch na drabinach DMAN/AMAN (widok RUCH)"><h3>Ruch VATSIM<span class="ruch-go">DMAN/AMAN ›</span></h3><div class="aero-ruch-line"><span class="hint">Ładowanie…</span></div></div>
          </div>
          <div class="aero-col">
            <div class="card part"><h3>Częstotliwości · online · rezerwacje</h3><div class="scroll freqs"></div></div>
            <div class="card aero-apw" title="Wiatr przy ziemi i na 3000 ft AMSL w punkcie podejścia każdego pasa (Open-Meteo); METEO › WIND pokazuje to samo na mapie Windy">
              <h3>Wiatr 0 / 3000 ft · podejście<span class="apw-src"></span></h3><div class="apw"></div></div>
            <div class="card grow aero-notam"><h3>NOTAM</h3><div class="scroll notams"></div></div>
          </div>
        </div>`;
      $(".aero-ruch").addEventListener("click", () => setView("ruch"));
      drawApw();
      renderNotams($(".notams"));
    };

    // wiatr na podejściu: prognoza zmienia się co godzinę (backend trzyma ją 10 min), pobieramy co 5 min
    let aw = null, awErr = "", awAt = 0, awFor = null, lastSt = null;
    const drawApw = () => {
      const el = $(".apw");
      if (!el) return;
      el.innerHTML = approachWind(aw, lastSt, awErr);
      // godzina prognozy (dzień w dymku karty)
      $(".apw-src").textContent = aw?.valid ? `${aw.source} ${aw.valid.slice(11, 13)}Z` : aw ? aw.source : "";
    };
    const loadApw = async () => {
      const code = icao;
      awAt = Date.now();
      try {
        const d = await api(`/api/meteo/approach-wind/${code}`);
        if (code !== icao) return;
        aw = d;
        awErr = "";
      } catch (e) {
        if (code !== icao) return;
        awErr = e.message;
      }
      if (mode === "page" && view === "overview") drawApw();
    };
    // lądowisko bez pasów w bazie (np. EPGM): wiatru na podejściu nie ma dla czego liczyć, nie pytamy serwera
    const noRunways = () => !!lastSt && Array.isArray(lastSt.runways) && !lastSt.runways.length;
    const apwDue = () => Date.now() - awAt > 300000 && !noRunways();

    let ticks = 0;
    const load = async () => {
      const code = icao;
      let info, st;
      try {
        [info, st] = await Promise.all([api(`/api/aerodromes/${code}`), api(`/api/aerodromes/${code}/status`)]);
      } catch (e) {
        if (view !== "overview" || mode !== "page" || code !== icao) return;
        $(".aero-errors").innerHTML = `<p class="error">${esc(e.message)}</p>`;
        // tytuł i godzina aktualizacji po poprzednim lotnisku nie mogą zostać przy nowo wybranym
        if (!$(".title").textContent.startsWith(icao)) {
          $(".title").textContent = icao;
          $(".upd").textContent = "";
        }
        // RUCH nie zależy od statusu lotniska: własne zapytanie, przy braku serwera błąd zamiast "Ładowanie…"
        renderRuch($(".aero-ruch-line"));
        if (apwDue()) loadApw();   // błąd statusu nie blokuje karty wiatru
        return;
      }
      // w międzyczasie przełączono na AWOS, RUCH, inne lotnisko, listę albo OVERVIEW
      if (view !== "overview" || mode !== "page" || code !== icao) return;
      const p = st.parsed || {};
      $(".title").textContent = `${info.icao} · ${info.name}  (elev ${fmt(info.elevation_ft, 0)} ft)`;
      $(".upd").textContent = "Aktualizacja " + new Date().toISOString().slice(11, 16) + "Z";
      const clouds = (p.clouds || []).map((c) => `${c.cover}${c.base_ft !== null ? String(c.base_ft / 100).padStart(3, "0") : "///"}${c.type || ""}`).join(" ") || (p.cavok ? "CAVOK" : "NSC");
      const rvr = (p.rvr || []).map((x) => `R${x.runway} ${x.value}`).join("<br>") || "–";
      const obs = obsInfo(p.time);
      const gust = p.wind_gust ? ` <span class="gust">G${p.wind_gust}</span>` : "";
      $(".aero-errors").innerHTML = st.errors.length ? `<p class="error">${st.errors.map(esc).join("<br>")}</p>` : "";
      $(".metar-head").innerHTML = `
          <div class="wx">${colorize(st.metar)}</div>
          <div class="obs ${obs.stale ? "stale" : ""}">${esc(obs.text)}</div>
          ${sunBox(st.sun)}
          ${lvpBox(st.lvp)}`;
      $(".aero-wind").innerHTML = `${windrose(st)}
            <div class="big">${windText(p).replace(/G\d+/, "")}${gust}</div>
            <div class="hint">${p.wind_speed !== null && p.wind_speed !== undefined ? `${Math.round(p.wind_speed * 0.514)} m/s` : ""}${p.wind_var_from !== null && p.wind_var_from !== undefined ? ` · zmienny ${p.wind_var_from}°–${p.wind_var_to}°` : ""}</div>
            ${runwayBox(st)}`;
      $(".values").innerHTML = `
              ${value("QNH", p.qnh ?? "–", '<span class="hint">hPa</span>')}
              ${value("QFE", st.qfe ?? "–", '<span class="hint">hPa (przybl.)</span>')}
              ${value("Widzialność", p.visibility_m !== null && p.visibility_m !== undefined ? (p.visibility_m >= 9999 ? "≥10 km" : p.visibility_m + " m") : "–")}
              ${value("RVR", `<span class="sm">${rvr}</span>`)}
              ${value("Podstawa", p.ceiling_ft ? p.ceiling_ft + " ft" : "–")}
              ${value("Chmury", `<span class="sm">${esc(clouds)}</span>`)}
              ${value("Temp / Dew", `${p.temperature ?? "–"} / ${p.dewpoint ?? "–"}`, p.temperature !== null && p.dewpoint !== null && p.temperature !== undefined && p.temperature - p.dewpoint <= 2 ? '<span class="hint" style="color:var(--warn)">mały spread</span>' : "")}
              ${value("Zjawiska", `<span class="sm">${esc((p.weather || []).join(" ") || "–")}</span>`)}
              ${value("Kategoria", `<span class="cat-${esc(p.flight_category)}">${esc(p.flight_category || "–")}</span>`, p.trend ? `<span class="hint mono">${esc(p.trend)}</span>` : "")}`;
      $(".aero-rwys").innerHTML = runwayTable(st);
      lastSt = st;
      drawApw();
      if (apwDue()) loadApw();
      $(".aero-taf").innerHTML = st.taf ? wxLines("taf", st.taf) : colorize(null);
      $(".atis-h").textContent = `ATIS ${st.atis ? st.atis.letter || "" : ""}`;
      $(".aero-atis").innerHTML = st.atis ? `<div class="mono atis-text">${atisHtml(st.atis.lines)}</div>`
        : `<span class="hint">${st.network_error ? esc(st.network_error) : "ATIS nie jest teraz nadawany w sieci VATSIM."}</span>`;
      renderFreqs($(".freqs"), info);
      renderRuch($(".aero-ruch-line"));
      if (++ticks % 10 === 0) renderNotams($(".notams"));
    };

    // --- przełączanie ekranów ---------------------------------------------------------------------------------
    const markSub = (key) => sub.querySelectorAll("button[data-fir]").forEach((b) => b.classList.toggle("active", b.dataset.fir === key));
    const setMode = (m) => {
      mode = m;
      pane.classList.toggle("ov-mode", m === "ov");
      pane.classList.toggle("list-mode", m === "list");
      pane.classList.toggle("card-mode", m === "card");
    };
    // sprząta bieżący ekran (zegary PRZEGLĄDU, AWOS, RUCH, OVERVIEW, listy i karty); stan listy zostaje w listState
    const leave = () => {
      if (list) { listState[listKey] = list.save(); list.destroy(); list = null; }
      ov?.destroy();
      ov = null;
      card?.destroy();
      card = null;
      clearInterval(timer);
      timer = null;
      awos?.destroy();
      awos = null;
      strips?.destroy();
      strips = null;
    };

    // 0: cały FIR EPWW (overview.js) w miejscu siatki lotniska
    const showOverview = () => {
      markSub("0");
      if (mode === "ov" && ov) return;      // już otwarty: zostaje ze swoim filtrem i zaznaczeniami checklisty
      leave();
      setMode("ov");
      $(".title").textContent = "OVERVIEW · FIR EPWW";
      $(".upd").textContent = "";
      $(".content").innerHTML = "";
      // wieża wybrana w filtrze: zawsze PRZEGLĄD lotniska, także gdy ostatnio był otwarty AWOS albo RUCH
      ov = mountOverview($(".content"), { onGoto: (code) => goto(code) });
    };

    // lista lotnisk FIR-u (aerolist.js); fresh = kliknięta podzakładka: lista od nowa, bez filtra
    const showList = (key, { fresh = false } = {}) => {
      markSub(key);
      if (mode === "list" && listKey === key && list && !fresh) { list.focus(); return; }
      leave();
      if (fresh) listState[key] = {};
      const saved = fresh ? null : stashed("aerodrome");
      setMode("list");
      listKey = key;
      $(".title").textContent = $(".upd").textContent = "";
      $(".content").innerHTML = "";
      list = mountFirList($(".content"), key, saved?.key === key ? saved : listState[key] || {}, {
        onOpen: (code) => goto(code),
        onChange: (st) => { if (mode === "list" && listKey === key) stash("aerodrome", { key, ...st }); },
      });
      list.focus();
    };

    // lotnisko zagraniczne (i polskie spoza bazy): karta z METAR, TAF i stanowiskami
    const showCard = (code) => {
      markSub(firOf(code));
      if (mode === "card" && cardIcao === code && card) { card.resume(); return; }   // powrót: odświeżenie, gdy dane starsze niż minuta
      leave();
      setMode("card");
      cardIcao = code;
      $(".aero-icao").value = code;
      $(".title").textContent = "";
      $(".upd").textContent = "";
      $(".content").innerHTML = "";
      card = mountAeroCard($(".content"), code, {
        onInfo: (d) => {
          if (d.fir) markSub(d.fir);
          $(".upd").textContent = "Aktualizacja " + new Date().toISOString().slice(11, 16) + "Z · co minutę";
          saveLast(true);
        },
        onMissing: () => {
          $(".upd").textContent = "";
          // zapisany wcześniej nieistniejący kod nie otwiera się więcej na starcie
          if (lsGet("aerodrome.last") === code) lsSet("aerodrome.last", "0");
        },
      });
    };

    // RUCH ma dwa widoki (dman.js): DMAN/AMAN i AMAN, w adresie #aerodrome/EPWA/dman i #aerodrome/EPWA/aman;
    // samo "ruch" (przycisk RUCH, stary adres) = ostatnio używany widok
    const ruchOf = (v) => (["dman", "aman"].includes(v) ? v : v === "ruch" ? (lsGet("dman.view") === "aman" ? "aman" : "dman") : null);
    // lotnisko z bazy: PRZEGLĄD, AWOS albo RUCH (jak dotąd; widok z adresu); zwraca adres pokazanego widoku
    const showPage = (code, v = "overview") => {
      markSub("EPWW");
      leave();
      setMode("page");
      icao = code;
      const rv = ruchOf(v);
      view = rv ? "ruch" : v;
      $(".aero-icao").value = icao;
      ticks = 0;
      if (awFor !== icao) { aw = null; awErr = ""; awAt = 0; awFor = icao; lastSt = null; }
      pane.querySelectorAll(".aero-view button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
      if (view === "awos") {
        $(".title").textContent = $(".upd").textContent = "";
        awos = mountAwos($(".content"), icao);
        return;
      }
      if (view === "ruch") {
        $(".title").textContent = icao;
        $(".upd").textContent = "";
        // przycisk widoku DMAN/AMAN ↔ AMAN w dman.js = krok do cofnięcia (#aerodrome/EPWA/aman). Zgłoszenie z montowania
        // i resume() ({replace: true}) tylko uzupełnia stan: adres zapisuje goto() albo activate() z wyniku show(),
        // już po zmianie ekranu (remember tutaj podmieniłby wpis ekranu, z którego przyszliśmy, i ← by go pominęło)
        strips = mountDman($(".content"), icao, { view: rv, onView: (vv, o) => {
          state = `${code}/${vv}`;
          if (!o?.replace) remember("aerodrome", state);
          saveLast();
        }, onUpdate: (d) => {
          $(".title").textContent = `${d.icao} · ${d.name}  (elev ${fmt(d.elevation_ft, 0)} ft)`;
          $(".upd").textContent = `Aktualizacja ${d.now.slice(0, 2)}:${d.now.slice(2)}Z · co 30 s`;
        } });
        return `${code}/${rv}`;
      }
      build();
      load();   // wiatr na podejściu po statusie (load: tylko lotnisko z pasami, także przy błędzie statusu)
      timer = setInterval(load, 60000);
    };

    // Ostatni ekran (0 albo lotnisko z widokiem) na następne uruchomienie: zapisany dopiero, gdy wiadomo, że lotnisko
    // istnieje (w bazie albo karta się wczytała), więc literówka (ZZZZ) nie zostaje ekranem startowym.
    const saveLast = (cardOk = false) => {
      if (!state || state.startsWith("fir/")) return;
      const { code } = splitArg(state);
      if (state === "0" || (mode === "page" && adSet?.has(code)) || (mode === "card" && cardOk && cardIcao === code)) {
        lsSet("aerodrome.last", state);
      }
    };
    // ekran wg arg (postać z aeroArg); zwraca adres tego, co naprawdę pokazano (karta nie ma widoków AWOS i RUCH)
    const show = (a, opts = {}) => {
      if (a === "0") showOverview();
      else if (a.startsWith("fir/")) showList(a.slice(4), opts);
      else {
        const { code, view: v } = splitArg(a);
        if (isPage(code)) a = showPage(code, v) || a;
        else {
          showCard(code);
          a = code;
        }
      }
      state = a;
      saveLast();
      return a;
    };
    // zmiana ekranu z wnętrza zakładki (podzakładka, lotnisko z listy, widok, pole ICAO): krok do cofnięcia (←)
    const goto = (arg, opts) => {
      let a = aeroArg(arg);
      if (!a) {
        $(".title").textContent = `Niepoprawny kod ICAO: ${String(arg ?? "").trim().slice(0, 10)}`;
        return;
      }
      if (mode === "list" && list) stash("aerodrome", { key: listKey, ...list.save() });   // stan listy w jej wpisie, przed krokiem dalej
      a = show(a, opts);
      remember("aerodrome", a);
    };
    // pierwsze otwarcie z menu: ostatni ekran albo 0 (także z kluczy zapisanych przez wersje do 0.12: lotnisko i widok)
    const initial = () => {
      const last = aeroArg(lsGet("aerodrome.last"));
      if (last && !last.startsWith("fir/")) return last;
      if (lsGet("aerodrome.ov") === "1") return "0";
      const old = aeroArg(lsGet("aerodrome.icao"));
      const oldView = lsGet("aerodrome.view");
      return old && !old.startsWith("fir/") ? old + (["awos", "ruch"].includes(oldView) ? "/" + oldView : "") : "0";
    };

    sub.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-fir]");
      if (b) goto(b.dataset.fir === "0" ? "0" : "fir/" + b.dataset.fir, { fresh: true });
    });
    // serwer wrócił (okno run.bat uruchomione ponownie): PRZEGLĄD od razu zamiast komunikatów błędu, nie po minucie
    // (NOTAM i wiatr na podejściu odświeżają się rzadziej, więc pobieramy je ponownie tylko po błędzie)
    window.addEventListener(ONLINE_EVENT, () => {
      if (mode !== "page" || view !== "overview" || !$(".notams")) return;
      load();
      if (awErr && !noRunways()) loadApw();
      if ($(".notams .error")) renderNotams($(".notams"));
    });
    // pole ICAO w pasku: na stronie lotniska zostaje bieżący widok (z RUCH EPWA wpisane EPKK otwiera RUCH EPKK)
    const typed = () => {
      const t = $(".aero-icao").value.trim().toUpperCase();
      return t && /^[A-Z0-9]{4}$/.test(t) && mode === "page" && view !== "overview" ? `${t}/${view === "ruch" ? splitArg(state).view : view}` : t;
    };
    $(".go").addEventListener("click", () => typed() && goto(typed()));
    // PRZEGLĄD / AWOS / RUCH: krok w historii (← wraca do poprzedniego widoku tego lotniska)
    const setView = (v) => {
      if (v === view && mode === "page") return;
      goto(v === "overview" ? icao : `${icao}/${v}`);
    };
    $(".aero-view").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-view]");
      if (b) setView(b.dataset.view);
    });
    $(".aero-icao").addEventListener("keydown", (e) => e.key === "Enter" && typed() && goto(typed()));
    $(".aero-icao").addEventListener("change", () => typed() && aeroArg(typed()) !== state && goto(typed()));

    // arg: undefined = przycisk AERODROME w menu (ekran zostaje), napis = adres (←, Wstecz, odnośnik z innej zakładki).
    // AWOS tego lotniska zostaje (wybrany pas, nakładka, wykrywanie nowej litery ATIS); RUCH tego lotniska zostaje
    // (zaznaczony pasek, przewinięcie zatok) i tylko wznawia odświeżanie co 30 s; lista dostaje kursor w wyszukiwarce.
    return { activate: (arg) => {
      let a = (arg ? aeroArg(arg) : null) || (arg ? "0" : state || initial());
      const rv = a && splitArg(a).view;
      if (a === state && mode === "page" && strips) strips.resume();
      else if (mode === "page" && strips && ["dman", "aman"].includes(rv) && splitArg(a).code === icao) {
        strips.setView(rv);         // ←/Wstecz między DMAN/AMAN a AMAN tego samego lotniska: bez przebudowy
        strips.resume();
        state = a;
      }
      else if (a === state && mode === "page" && awos) { /* AWOS bez zmian */ }
      else if (a === state && mode === "list" && list) list.focus();
      else a = show(a);
      remember("aerodrome", a);
    } };
  },
};
