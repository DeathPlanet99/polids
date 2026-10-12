// AERODROME › OVERVIEW: jeden ekran dla całego FIR EPWW, coś jak self-checkin przed otwarciem stanowiska.
// Panele (dane z /api/overview, każdy z własnym komunikatem błędu):
//   1. poziom przejściowy (TL): jeden dla całego kraju, FL080, a FL090, gdy gdziekolwiek QNH ≤ 995 hPa,
//   2. METAR + TAF wszystkich kontrolowanych lotnisk ze stanem LVP, kolory jak w METEO,
//   3. NOTAM-y obowiązujące teraz,
//   4. restrykcje ECFMP dla FIR EPWW i regulacje vIFF na sektorach EP,
//   5. Airport Monitor vIFF (przepustowość, ruch, loty z CTOT) + liczby lotów z sieci VATSIM.
// Filtr stanowiska u góry: ACC / APP / TWR. APP i ACC zawężają panele do swoich lotnisk i pokazują checklistę
// otwarcia stanowiska, TWR przeskakuje od razu do PRZEGLĄDU danego lotniska (onGoto).
import { api, esc, h, lsGet, lsSet, ONLINE_EVENT } from "./api.js";
import { colorize, wxLines } from "./tabs/meteo.js";
import { renderChecklist } from "./checklist.js";

const POS_KEY = "overview.position";
const CHK_KEY = "checklist.overview.open-position";
const REFRESH_MS = 60000;

const hm = (iso) => (iso ? new Date(iso).toISOString().slice(11, 16) + "Z" : "–");
const dhm = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso);
  return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")} ${hm(iso)}`;
};
const n0 = (v) => (v === null || v === undefined ? "–" : esc(v));
// odmiana: 1 lotnisko, 2–4 lotniska (też 22–24 …), 5–21 lotnisk
const lotnisk = (n) => (n === 1 ? "lotnisko" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "lotniska" : "lotnisk");

// --- 1. poziom przejściowy: jeden dla całego kraju, bez osobnego TL przy lotniskach ------------------------------
function tlPanel(d) {
  const t = d.tl || {};
  const low = t.low || [];
  const miss = t.missing || [];
  const thr = `QNH ≤ ${n0(t.threshold_hpa)} hPa`;
  const head = t.fl ? `TL <b>${esc(t.text)}</b> w całym kraju` : "TL <b>–</b> (brak QNH)";
  return `<div class="ov-tl-big">${head}</div>
    <div class="hint ov-tl-rule">${esc(t.rule || "")}</div>
    ${t.qnh_icao ? `<div class="ov-tl-min">najniższe QNH: <b class="ic">${esc(t.qnh_icao)}</b> ${n0(t.qnh_min)} hPa</div>` : ""}
    ${low.length ? `<div class="ov-note warn">${thr}: ${low.map(esc).join(", ")}</div>` : ""}
    ${miss.length ? `<div class="${t.uncertain ? "ov-note warn" : "hint"}">bez QNH z METAR-u (nieuwzględnione): ${miss.map(esc).join(", ")}${
      t.uncertain ? ` – jeśli tam ${thr}, TL to ${esc(t.raised)}` : ""}</div>` : ""}
    <div class="hint ov-src">${esc(t.normal || "")}, a gdy na którymkolwiek z ${n0((t.stations || 0) + miss.length)} kontrolowanych lotnisk
      ${thr}, w całym kraju ${esc(t.raised || "")}. TA ${n0(t.ta_ft)} ft.
      <a href="${esc(t.source_url || "")}" target="_blank" rel="noopener">${esc(t.source || "")}</a></div>`;
}

// --- 2. METAR / TAF / LVP --------------------------------------------------------------------------------------
function lvpChip(lvp) {
  if (!lvp) return "";
  const th = lvp.thresholds;
  // dymek w dwóch linijkach: w jednej nie mieścił się w panelu i był ucięty z prawej
  const tip = th ? `LVP: RVR/widzialność < ${th.lvp.rvr_m} m lub podstawa ≤ ${th.lvp.ceiling_ft} ft\nprzygotowanie: < ${th.prep.rvr_m} m lub ≤ ${th.prep.ceiling_ft} ft` : "";
  if (!lvp.state) return `<span class="ov-chip" data-tip="${esc(tip)}">LVP nie</span>`;
  const cls = lvp.state === "LVP" ? "bad" : "warn";
  const label = lvp.state === "LVP" ? "LVP W MOCY" : "PRZYGOT. LVP";
  return `<span class="ov-chip ${cls}" data-tip="${esc([tip, ...(lvp.reasons || [])].join("\n"))}">${label}</span>`;
}

function wxRow(a, open) {
  const p = a.parsed || {};
  const t = a.traffic || {};
  const obs = p.time ? `${esc(p.time.slice(2, 4))}:${esc(p.time.slice(4, 6))}Z` : "";
  return `<div class="ov-aero${open ? " open" : ""}" data-icao="${esc(a.icao)}">
    <div class="ov-aero-head">
      <b class="ic">${esc(a.icao)}</b>
      <span class="cat cat-${esc(p.flight_category || "")}">${esc(p.flight_category || "–")}</span>
      ${lvpChip(a.lvp)}
      ${a.atis ? `<span class="ov-chip on" data-tip="${esc(a.atis.callsign)}">ATIS ${esc(a.atis.letter || "")}</span>` : ""}
      ${(a.atc || []).length ? `<span class="ov-chip on" data-tip="${esc(a.atc.map((c) => `${c.callsign} ${c.frequency} ${c.name || ""}`).join("\n"))}">ATC ${a.atc.length}</span>` : ""}
      <span class="ov-traffic" data-tip="Loty w sieci VATSIM z planem z/do tego lotniska">↑${t.departures ?? 0} ↓${t.arrivals ?? 0}${t.prefiles ? ` ✎${t.prefiles}` : ""}</span>
      <span class="ov-obs">${obs}</span>
      <span class="ov-more">${open ? "▾" : "▸"} TAF</span>
    </div>
    <div class="wx ov-metar">${colorize(a.metar)}</div>
    ${open ? `<div class="wx ov-taf">${a.taf ? wxLines("taf", a.taf) : colorize(null)}</div>` : ""}
    ${a.lvp && a.lvp.state ? `<div class="ov-lvp-why">${(a.lvp.reasons || []).map(esc).join(" · ")}</div>` : ""}
  </div>`;
}

// --- 3. NOTAM -------------------------------------------------------------------------------------------------
function notamValid(n) {
  if (n.perm) return "PERM";
  if (!n.end) return "do ?";
  return `do ${dhm(n.end)}${n.est ? " EST" : ""}`;
}
function notamPanel(d) {
  // po błędzie serwera NOTAM nie piszemy "brak": nie wiemy, czy ich nie ma (komunikat błędu jest nad listą)
  if (!d.notams.length) return d.notam_error ? "" : `<div class="hint">Brak NOTAM-ów obowiązujących teraz dla pokazanych lotnisk.</div>`;
  return d.notams.map((n) => `<div class="ov-nt" title="${esc(n.raw || "")}">
    <div class="ov-nt-head"><b class="ic">${esc(n.icao)}</b><span class="mono id">${esc(n.id || "")}</span>
      <span class="valid">${esc(notamValid(n))}</span>
      ${n.schedule ? `<span class="hint">D) ${esc(n.schedule)}</span>` : ""}</div>
    <div class="ov-nt-txt">${esc(n.text)}</div></div>`).join("");
}

// --- 4. ECFMP / vIFF ------------------------------------------------------------------------------------------
const STATE_PL = { active: "obowiązuje", notified: "zgłoszona", finished: "zakończona" };
function flowPanel(d) {
  const f = d.flow || {};
  const ms = (f.measures || []).map((m) => `<div class="ov-fm ${esc(m.state)}">
    <div class="ov-fm-head"><b class="mono">${esc(m.ident)}</b>
      <span class="ov-chip ${m.state === "active" ? "on" : "warn"}">${esc(STATE_PL[m.state] || m.state)}</span>
      <span class="ov-fm-val">${esc(m.type_label)}${m.value_text ? ` · ${esc(m.value_text)}` : ""}</span>
      <span class="ov-fm-time mono">${hm(m.start)}–${hm(m.end)}</span></div>
    <div class="ov-fm-why">${esc(m.reason || "")}${m.firs.length ? ` · FIR: ${esc(m.firs.join(", "))}` : ""}</div>
    <div class="ov-fm-flt">
      ${m.adep.length ? `<span>odlot z <b>${esc(m.adep.join(", "))}</b></span>` : ""}
      ${m.ades.length ? `<span>przylot do <b>${esc(m.ades.join(", "))}</b></span>` : ""}
      ${m.waypoints.length ? `<span>przez <b>${esc(m.waypoints.join(", "))}</b></span>` : ""}
      ${m.levels.length ? `<span>${esc(m.levels.join(", "))}</span>` : ""}</div></div>`).join("");
  const rs = (f.restrictions || []).map((r) => `<tr class="${r.active ? "on" : ""}" title="${esc(r.label || "")}${r.reason ? ` · powód: ${esc(r.reason)}` : ""}">
    <td class="mono">${esc(r.tv)}</td><td>${esc((r.type || "").replace(/^ENR-/, ""))}</td>
    <td class="num">${n0(r.value)}</td><td class="num mono">${esc(r.start || "")}–${esc(r.end || "")}</td></tr>`).join("");
  // "brak restrykcji" tylko wtedy, gdy źródło odpowiedziało; po awarii zostaje sam komunikat błędu
  return `<div class="ov-sub">ECFMP · FIR EPWW${f.source ? ` <span class="hint">(${esc(f.source)})</span>` : ""}</div>
    ${f.error ? `<div class="error ov-err">${esc(f.error)}</div>` : ""}
    ${ms || (f.source ? `<div class="hint">Brak restrykcji ECFMP dla FIR EPWW i polskich lotnisk.</div>` : "")}
    <div class="ov-sub">vIFF · regulacje sektorów EP</div>
    ${f.viff_error ? `<div class="error ov-err">${esc(f.viff_error)}</div>` : ""}
    ${rs ? `<table class="data ov-tab"><thead><tr><th>Sektor</th><th>Rodzaj</th><th>Wart.</th><th>Godziny</th></tr></thead>
      <tbody>${rs}</tbody></table><div class="hint">ENTRIES = limit wejść na godzinę, OCCUPANCY = limit zajętości (najedź: opis sektora).
      Podświetlone = obowiązuje teraz.</div>`
      : f.viff_error ? "" : `<div class="hint">Brak ograniczeń na sektorach EP w vIFF.</div>`}`;
}

// --- 5. Airport Monitor vIFF ----------------------------------------------------------------------------------
const loadCls = (n, cap) => {
  if (n === null || n === undefined || !cap) return "";
  const r = n / cap;
  return r > 1 ? "over" : r >= 0.9 ? "near" : "ok";
};
function monitorPanel(d) {
  const rows = d.aerodromes.map((a) => {
    const m = a.monitor || {};
    const now = m.now || {}, nxt = m.next || {};
    const regs = m.regulated || [];
    // lista lotów w zwykłym dymku przeglądarki (title): dymek data-tip ucinała dolna krawędź przewijanego panelu
    const tip = regs.length
      ? regs.map((f) => `${f.callsign} ${f.role} ${f.departure}→${f.arrival} CTOT ${f.ctot || "–"}${f.delay ? ` (+${f.delay} min)` : ""} ${f.regulation || ""}`).join("\n")
      : "Brak lotów z CTOT";
    const t = a.traffic || {};
    return `<tr><td class="ic">${esc(a.icao)}</td>
      <td class="num ${loadCls(now.entries, now.cap)}">${n0(now.entries)}</td>
      <td class="num ${loadCls(nxt.entries, nxt.cap)}">${n0(nxt.entries)}</td>
      <td class="num">${n0(m.rate ?? now.cap)}</td>
      <td>${m.cdm ? `<span class="ov-chip on" title="Lotnisko z A-CDM (TOBT/TSAT/TTOT)">CDM</span>` : ""}</td>
      <td class="num tip" title="${esc(tip)}">${regs.length || "–"}</td>
      <td class="num ${m.avg_delay >= 10 ? "near" : ""}">${m.avg_delay ? "+" + m.avg_delay : "–"}</td>
      <td class="num">${t.departures ?? 0}/${t.arrivals ?? 0}</td></tr>`;
  }).join("");
  // vIFF liczy przepustowość lotniska dla przylotów (Base Rate = Default Arrival Rate w dokumentacji vIFF),
  // a godzinowe entriesCount z /etfms/airports to przyloty w tej godzinie
  return `<table class="data ov-tab ov-mon-tab"><thead>
      <tr><th rowspan="2">Lotn.</th><th colspan="2">vIFF przyloty</th><th rowspan="2" title="Przepustowość lotniska w vIFF (przyloty/h)">Przep.</th>
        <th rowspan="2">A-CDM</th><th colspan="2">Loty z CTOT</th><th rowspan="2" title="Loty w sieci VATSIM: odloty/przyloty">VATSIM</th></tr>
      <tr><th title="Bieżąca godzina UTC">teraz</th><th>+1 h</th><th title="Liczba lotów z CTOT (najedź: lista)">szt.</th><th title="Średnie opóźnienie ATFM (CTOT − ETOT)">opóźn.</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div class="hint">vIFF: przyloty zaplanowane w godzinie vs przepustowość lotniska (zielony &lt; 90&nbsp;%, pomarańczowy 90–100&nbsp;%, czerwony powyżej).</div>`;
}

// --- widok -----------------------------------------------------------------------------------------------------
export function mountOverview(el, { onGoto } = {}) {
  el.innerHTML = `<div class="ov">
    <div class="toolbar ov-bar">
      <label class="sc">Stanowisko
        <select class="field ov-pos"><option value="">Wszystkie lotniska FIR EPWW</option></select></label>
      <span class="hint ov-pos-note"></span>
      <span style="flex:1"></span>
      <span class="error ov-net"></span>
      <span class="hint ov-upd"></span>
    </div>
    <div class="ov-grid">
      <div class="ov-col">
        <div class="card ov-tl"><h3>Poziom przejściowy</h3><div class="scroll ov-tl-body"><span class="hint">Ładowanie…</span></div></div>
        <div class="card grow ov-chk"><h3>Checklista otwarcia stanowiska</h3><div class="scroll ov-chk-body"></div></div>
      </div>
      <div class="ov-col">
        <div class="card grow ov-wx"><h3>METAR · TAF · LVP<span class="ov-h-note"></span></h3><div class="scroll ov-wx-body"><span class="hint">Ładowanie…</span></div></div>
        <div class="card part ov-mon"><h3>Airport Monitor vIFF</h3><div class="scroll ov-mon-body"></div></div>
      </div>
      <div class="ov-col">
        <div class="card grow ov-notam"><h3>NOTAM obowiązujące teraz<span class="ov-h-note ov-nt-n"></span></h3><div class="scroll ov-notam-body"><span class="hint">Ładowanie…</span></div></div>
        <div class="card grow ov-flow"><h3>Restrykcje ECFMP · vIFF</h3><div class="scroll ov-flow-body"></div></div>
      </div>
    </div></div>`;
  const $ = (s) => el.querySelector(s);
  let position = lsGet(POS_KEY) || "";
  let open = new Set();          // lotniska kliknięte: TAF w stanie odwrotnym do domyślnego (rozwinięty / zwinięty)
  let timer = null, chk = null, destroyed = false, last = null, posLoaded = false;

  // lista stanowisk do filtra (ACC / APP / TWR); bez niej zostaje sam widok całego FIR-u
  const loadPositions = async () => {
    let data;
    try { data = await api("/api/overview/positions"); } catch (e) { $(".ov-pos-note").textContent = e.message; return; }
    if (destroyed || posLoaded) return;
    posLoaded = true;
    const sel = $(".ov-pos");
    data.groups.forEach((g) => {
      if (!g.positions.length) return;
      const og = document.createElement("optgroup");
      og.label = g.label;
      og.innerHTML = g.positions.map((p) => `<option value="${esc(p.callsign)}" ${p.goto ? `data-goto="${esc(p.goto)}"` : ""}>${esc(p.callsign)}${p.goto
        ? ` → PRZEGLĄD ${esc(p.goto)}` : ` (${p.airports.length} ${lotnisk(p.airports.length)})`}</option>`).join("");
      sel.append(og);
    });
    if (position && !sel.querySelector(`option[value="${CSS.escape(position)}"]`)) position = "";
    sel.value = position;
  };

  const drawChecklist = (data) => {
    const body = $(".ov-chk-body");
    if (!position || data?.position?.goto) {
      body.innerHTML = `<div class="hint">Wybierz stanowisko (ACC albo APP) u góry: pokaże się checklista otwarcia
        i tylko lotniska tego stanowiska. Wybór wieży (TWR) przenosi od razu do PRZEGLĄDU lotniska.</div>`;
      return;
    }
    if (chk && chk.position === position) return;   // ta sama checklista: nie przerysowujemy (zaznaczenia zostają)
    body.innerHTML = `<span class="hint">Ładowanie checklisty…</span>`;
    const want = position;
    api("/api/checklists").then((d) => {
      if (destroyed || want !== position) return;   // w międzyczasie wybrano inne stanowisko
      const item = d.checklists.find((c) => c.id === (d.aerodrome || "open-position"));
      if (!item) { body.innerHTML = `<div class="hint">Brak checklisty w data/seed/checklists.json.</div>`; return; }
      body.innerHTML = `<div class="ov-chk-pos">${esc(position)}</div><div class="checklist"></div>`;
      renderChecklist(body.querySelector(".checklist"), item, CHK_KEY);
      chk = { position };
    }).catch((e) => { if (!destroyed && want === position) body.innerHTML = `<div class="error">${esc(e.message)}</div>`; });
  };

  const draw = (d) => {
    const pos = d.position;
    $(".ov-pos-note").innerHTML = pos
      ? `${esc(pos.name || "")}${pos.frequency ? ` · ${esc(pos.frequency)}` : ""} · lotniska: <b>${d.airports.map(esc).join(", ")}</b>`
      : `wszystkie kontrolowane lotniska FIR EPWW (${d.airports.length})`;
    $(".ov-net").textContent = [d.network_error, d.wx_error].filter(Boolean).join(" · ");
    $(".ov-upd").textContent = "Aktualizacja " + new Date().toISOString().slice(11, 16) + "Z";
    $(".ov-tl-body").innerHTML = tlPanel(d);
    // z filtrem (mało lotnisk) TAF od razu widoczny, bez filtra dopiero po kliknięciu wiersza;
    // kliknięcie odwraca stan domyślny, więc przy rozwiniętych TAF-ach zwija TAF danego lotniska
    const all = Boolean(position) && d.aerodromes.length <= 6;
    $(".ov-h-note").textContent = all ? "TAF rozwinięty · kliknij = zwiń" : "kliknij lotnisko = TAF";
    $(".ov-wx-body").innerHTML = (d.wx_error ? `<div class="error ov-err">${esc(d.wx_error)}</div>` : "")
      + d.aerodromes.map((a) => wxRow(a, all !== open.has(a.icao))).join("");
    $(".ov-nt-n").textContent = d.notam_error && !d.notams.length ? "–" : `${d.notams.length}`;   // "0" po awarii by kłamało
    $(".ov-notam-body").innerHTML = (d.notam_error ? `<div class="error ov-err">${esc(d.notam_error)}</div>` : "")
      + notamPanel(d);
    $(".ov-flow-body").innerHTML = flowPanel(d);
    $(".ov-mon-body").innerHTML = (d.monitor_error ? `<div class="error ov-err">${esc(d.monitor_error)}</div>` : "")
      + monitorPanel(d);
    drawChecklist(d);
  };

  const load = async () => {
    const want = position;
    let d;
    try {
      d = await api("/api/overview" + (want ? "?position=" + encodeURIComponent(want) : ""));
    } catch (e) {
      if (destroyed || want !== position) return;
      // komunikat w każdym panelu: pusty panel (albo dane sprzed awarii) wyglądałby jak "brak restrykcji"
      const msg = `<div class="error">${esc(e.message)}</div>`;
      [".ov-wx-body", ".ov-tl-body", ".ov-notam-body", ".ov-flow-body", ".ov-mon-body"].forEach((s) => { $(s).innerHTML = msg; });
      return;
    }
    if (destroyed || want !== position) return;
    last = d;
    draw(d);
  };

  $(".ov-pos").addEventListener("change", (e) => {
    const goto = e.target.selectedOptions[0]?.dataset.goto;
    if (goto) {                     // TWR: od razu PRZEGLĄD lotniska, filtr zostaje jak był
      e.target.value = position;
      onGoto?.(goto);
      return;
    }
    position = e.target.value;
    lsSet(POS_KEY, position);
    open = new Set();
    chk = null;
    load();
  });
  // kliknięcie wiersza lotniska rozwija / zwija TAF
  $(".ov-wx-body").addEventListener("click", (e) => {
    const row = e.target.closest(".ov-aero");
    if (!row) return;
    const icao = row.dataset.icao;
    if (open.has(icao)) open.delete(icao); else open.add(icao);
    if (last) draw(last);           // sam TAF: przerysowujemy z danych, które już mamy
  });
  // serwer wrócił: lista stanowisk, jeśli się wtedy nie wczytała, i dane, jeśli któryś panel pokazuje błąd
  // (.ov-net ma klasę error także pusty, więc liczą się tylko niepuste komunikaty)
  const online = () => {
    if (!posLoaded) loadPositions().then(load);
    else if (el.querySelector(".ov .error:not(:empty)")) load();
  };
  window.addEventListener(ONLINE_EVENT, online);

  loadPositions().then(load);
  timer = setInterval(load, REFRESH_MS);
  return {
    destroy() {
      destroyed = true;
      clearInterval(timer);
      window.removeEventListener(ONLINE_EVENT, online);
    },
  };
}
