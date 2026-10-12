// AERODROME: listy lotnisk FIR-ów (podzakładki EPWW … UKLV) i karta lotniska zagranicznego.
// Lista jak w PANDORZE: u góry wyszukiwarka z klawiatury z przyciskami WYŚWIETL i WYCZYŚĆ, niżej wiersze
// "ICAO NAZWA" w kilku kolumnach, przewijane w swoim okienku. Backend: GET /api/aerodromes/fir/{klucz}
// (services/aerolist.py) i GET /api/aerodromes/{icao}/card (METAR, TAF, stanowiska ze stanem VATSIM).
import { api, atisHtml, esc, hhmm, vatsimAtc } from "./api.js";
import { colorize, wxLines } from "./tabs/meteo.js";

export const FIR_KEYS = ["EPWW", "ESAA", "ED", "ET", "LKAA", "LZBB", "UMKK", "EYVL", "UMMV", "UKLV"];
export const FIR_LABELS = {
  EPWW: "Polska", ESAA: "Szwecja", ED: "Niemcy, lotniska cywilne", ET: "Niemcy, lotniska wojskowe", LKAA: "Czechy",
  LZBB: "Słowacja", UMKK: "Kaliningrad", EYVL: "Litwa", UMMV: "Białoruś", UKLV: "Ukraina, FIR Lwów",
};
// rodzaje stanowisk jak na plakietkach lotnisk na MAP (airspace.js FACILITIES): kolory .ab-* w style.css
const FAC = [["DEL", "D"], ["GND", "G"], ["TWR", "T"], ["ATIS", "A"], ["APP", "APP"]];

// wyszukiwanie bez wielkości liter i polskich znaków: "łódź" = "LODZ" (Ł nie rozkłada się w NFD)
export const fold = (s) => String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .replace(/[łŁ]/g, "L").toUpperCase().replace(/\s+/g, " ").trim();
export const isIcao = (s) => /^[A-Z0-9]{4}$/.test(s);

// lotnisko → podzakładka (jak services/aerolist.py fir_key); UK* tylko w granicy FIR UKLV, czyli z listy UKLV
export function firOf(icao) {
  const i = String(icao || "").toUpperCase();
  const pre = [["UMK", "UMKK"], ["EP", "EPWW"], ["ES", "ESAA"], ["ED", "ED"], ["ET", "ET"], ["LK", "LKAA"], ["LZ", "LZBB"],
    ["UM", "UMMV"], ["EY", "EYVL"]].find(([p]) => i.startsWith(p));
  if (pre) return pre[1];
  if (i.startsWith("UK")) return lists.UKLV?.data ? (lists.UKLV.data.some((a) => a.icao === i) ? "UKLV" : null) : i.startsWith("UKL") ? "UKLV" : null;
  return null;
}

// Adres zakładki (#aerodrome/arg): "0" = OVERVIEW (dawne "OVERVIEW" też), "fir/ED" = lista lotnisk FIR-u,
// "EPWA" / "EDDB" = lotnisko. Zwraca postać kanoniczną albo null (nie rozpoznano).
export function aeroArg(arg) {
  const a = String(arg ?? "").trim();
  if (/^(0|overview)$/i.test(a)) return "0";
  const m = /^fir\/(\w{2,4})$/i.exec(a);
  if (m) return FIR_KEYS.includes(m[1].toUpperCase()) ? "fir/" + m[1].toUpperCase() : null;
  const c = a.toUpperCase();
  return isIcao(c) ? c : null;
}

// listy lotnisk: raz na sesję (navdata zmienia się co AIRAC), błąd nie zostaje w pamięci
const lists = {};
export function firList(key) {
  const c = lists[key] ||= {};
  c.p ||= api(`/api/aerodromes/fir/${encodeURIComponent(key)}`).then((d) => (c.data = d))
    .catch((e) => { c.p = null; throw e; });
  return c.p;
}

// kolumny listy po co najmniej 300 px; wiersze płyną kolumnami (z góry na dół, potem następna kolumna)
const COL_MIN = 300;

// Strona listy jednego FIR-u. state: {q, sel, scroll} (zapamiętane w aerodrome.js, ← wraca do tej samej listy z tym
// samym filtrem); onOpen(icao) otwiera lotnisko (krok w historii). Zwraca {focus(), save(), destroy()}.
export function mountFirList(el, key, state, { onOpen }) {
  el.innerHTML = `<div class="alist">
    <div class="alist-bar">
      <input class="alist-q" type="text" autocomplete="off" spellcheck="false" maxlength="40"
        placeholder="ICAO albo nazwa lotniska" aria-label="Szukaj lotniska" value="${esc(state.q || "")}">
      <button class="alist-btn alist-go" type="button">WYŚWIETL</button>
      <button class="alist-btn alist-clear" type="button">WYCZYŚĆ</button>
      <span class="alist-title"><b>${esc(key)}</b> ${esc(FIR_LABELS[key] || "")}</span>
      <span class="alist-n hint"></span>
      <span style="flex:1"></span>
      <span class="alist-legend hint" title="Plakietki przy lotnisku: kontrola online (DEL, GND, TWR, ATIS, APP), jak na MAP.
Enter = WYŚWIETL (dokładny ICAO, inaczej zaznaczone albo pierwsze trafienie), ↑ ↓ = wybór, Esc = WYCZYŚĆ.">${FAC.map(([k, l]) =>
        `<span class="ab ab-${k.toLowerCase()}">${l}</span>`).join("")} online · ↑↓ Enter Esc</span>
    </div>
    <div class="alist-box"><div class="alist-msg hint">Ładowanie listy lotnisk…</div><div class="alist-cols"></div></div>
  </div>`;
  const $ = (s) => el.querySelector(s);
  const input = $(".alist-q"), box = $(".alist-box"), cols = $(".alist-cols"), msg = $(".alist-msg");
  let rows = [], data = null, sel = null, destroyed = false, atcTimer = null;

  const visible = () => rows.filter((r) => !r.hidden);
  const select = (row, scroll = true) => {
    sel?.classList.remove("sel");
    sel = row || null;
    if (!sel) return;
    sel.classList.add("sel");
    if (scroll) sel.scrollIntoView({ block: "nearest" });
  };
  // najlepsze trafienie: dokładny ICAO, potem początek ICAO, potem pierwsza pasująca nazwa (kolejność listy po ICAO)
  const best = (q, vis) => vis.find((r) => r.dataset.icao === q) || vis.find((r) => r.dataset.icao.startsWith(q)) || vis[0];
  const filter = ({ keepSel = false } = {}) => {
    if (!data) return;
    const q = fold(input.value);
    let n = 0;   // pasy liczone po widocznych wierszach (po filtrze nadal na przemian)
    rows.forEach((r) => {
      r.hidden = !!q && !r.dataset.icao.startsWith(q) && !r.dataset.key.includes(q);
      if (!r.hidden) r.classList.toggle("odd", n++ % 2 === 1);
    });
    const vis = visible();
    $(".alist-n").textContent = q ? `${vis.length} z ${data.length}` : `${data.length} ${data.length === 1 ? "lotnisko" : "lotnisk"}`;
    msg.hidden = vis.length > 0;
    if (!vis.length) {
      msg.innerHTML = `Brak lotniska pasującego do „${esc(input.value.trim())}” w ${esc(key)}.`
        + (isIcao(q) ? ` Enter albo WYŚWIETL otworzy <b>${esc(q)}</b>.` : "");
    }
    // kolumny: lista dzieli się równo na tyle kolumn, ile mieści okienko (CSS columns), krótka lista w jednej
    cols.style.columnCount = String(Math.max(1, Math.min(Math.floor((box.clientWidth || 1200) / COL_MIN), Math.ceil(vis.length / 8))));
    if (!(keepSel && sel && !sel.hidden)) select(q ? best(q, vis) : null);
  };

  const open = () => {
    const q = fold(input.value);
    const target = sel && !sel.hidden ? sel.dataset.icao : q ? best(q, visible())?.dataset.icao || (isIcao(q) ? q : null) : null;
    if (target) onOpen(target);
    else input.focus();
  };
  const clear = () => {
    input.value = "";
    filter();
    box.scrollTop = 0;
    input.focus();
  };

  input.addEventListener("input", () => filter());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); open(); return; }
    if (e.key === "Escape") { e.preventDefault(); clear(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const vis = visible();
      if (!vis.length) return;
      const i = vis.indexOf(sel);
      select(vis[e.key === "ArrowDown" ? (i < 0 ? 0 : Math.min(vis.length - 1, i + 1)) : Math.max(0, i - 1)]);
    }
  });
  $(".alist-go").addEventListener("click", open);
  $(".alist-clear").addEventListener("click", clear);
  cols.addEventListener("click", (e) => {
    const r = e.target.closest(".alist-row");
    if (r) { select(r, false); onOpen(r.dataset.icao); }
  });
  // pisanie poza polem (np. po kliknięciu w listę) trafia do wyszukiwarki
  el.addEventListener("keydown", (e) => {
    if (e.target === input || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) return;
    input.focus();
  });
  const onResize = () => filter({ keepSel: true });
  window.addEventListener("resize", onResize);

  // kontrola online: plakietki D/G/T/A/APP przy lotniskach (ten sam cache co MAP, co 30 s)
  const drawAtc = async () => {
    let d;
    try { d = await vatsimAtc(); } catch { return; }
    if (destroyed) return;
    const on = new Map((d.airports || []).map((a) => [a.icao, a.facilities || {}]));
    rows.forEach((r) => {
      const f = on.get(r.dataset.icao);
      const html = f ? FAC.filter(([k]) => f[k]?.length).map(([k, l]) => `<span class="ab ab-${k.toLowerCase()}">${l}</span>`).join("") : "";
      const slot = r.querySelector(".alist-atc");
      if (slot.innerHTML !== html) slot.innerHTML = html;
      r.classList.toggle("on", !!html);
    });
  };

  firList(key).then((d) => {
    if (destroyed) return;
    data = d;
    cols.innerHTML = d.map((a) => `<div class="alist-row" data-icao="${esc(a.icao)}" data-key="${esc(fold(a.name))}" title="${esc(a.icao)} ${esc(a.name)}${a.kind ? " · " + esc(a.kind) : ""}">
      <b>${esc(a.icao)}</b><span class="alist-nm">${esc(a.name || "–")}</span>${a.kind ? `<i class="alist-kind">${esc(a.kind)}</i>` : ""}<span class="alist-atc"></span></div>`).join("");
    rows = [...cols.children];
    filter();
    const back = state.sel && rows.find((r) => r.dataset.icao === state.sel && !r.hidden);
    if (back) select(back, false);
    box.scrollTop = state.scroll || 0;
    if (back && !state.scroll) back.scrollIntoView({ block: "nearest" });
    drawAtc();
    atcTimer = setInterval(drawAtc, 30000);
  }).catch((e) => {
    if (destroyed) return;
    msg.hidden = false;
    msg.innerHTML = `<span class="error">${esc(e.message)}</span>`;
  });

  return {
    focus: () => { input.focus(); input.select(); },
    save: () => ({ q: input.value, sel: sel?.dataset.icao || null, scroll: box.scrollTop }),
    destroy: () => { destroyed = true; clearInterval(atcTimer); window.removeEventListener("resize", onResize); },
  };
}

// --- karta lotniska zagranicznego (i polskiego spoza bazy lotnisk) ----------------------------------------------

const TYPE_LABEL = { APP: "APP", TWR: "TWR", GND: "GND", DEL: "DEL", ATIS: "ATIS" };

function obsText(p) {
  if (!p?.time) return "";
  const m = /^(\d{2})(\d{2})(\d{2})Z$/.exec(p.time);
  return m ? `obs. ${m[2]}:${m[3]}Z` : "";
}

function positionsTable(ps) {
  if (!ps.length) return `<p class="hint">Brak stanowisk APP, TWR, GND, DEL i ATIS tego lotniska w danych PolIDS i nikt nie jest teraz zalogowany.</p>`;
  return `<table class="data acard-pos"><thead><tr><th>Typ</th><th>Stanowisko</th><th>Częst.</th><th>Stan</th></tr></thead><tbody>
    ${ps.map((p) => {
      const on = p.online;
      const logged = on && on.callsign && on.callsign !== p.callsign ? ` jako ${esc(on.callsign)}` : "";
      const freq = (on && on.frequency) || p.frequency;
      const state = !on ? "offline"
        : `<b>${esc(on.name || "?")}</b> · od ${esc(hhmm(on.logon_time))}${logged}${p.type === "ATIS" && on.atis_code ? ` · <b class="acard-letter">${esc(on.atis_code)}</b>` : ""}`;
      return `<tr class="${on ? "on" : "off"}"><td><span class="ab ab-${esc(p.type.toLowerCase())}">${esc(TYPE_LABEL[p.type] || p.type)}</span></td>
        <td class="acard-cs">${esc(p.callsign)}${p.name ? `<span class="acard-pn">${esc(p.name)}</span>` : ""}</td>
        <td><span class="freq ${on ? "on" : ""}">${esc(freq || "–")}</span></td><td class="acard-st">${state}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// onInfo(d): nagłówek w pasku AERODROME (nazwa, FIR) po każdym odświeżeniu; zwraca {destroy()}
export function mountAeroCard(el, icao, { onInfo } = {}) {
  el.innerHTML = `<div class="acard">
    <div class="acard-head"><b>${esc(icao)}</b><span class="acard-name"></span><span class="acard-fir hint"></span></div>
    <div class="aero-errors"></div>
    <div class="metar-head"><div class="wx"><span class="hint">Ładowanie METAR…</span></div><div class="obs"></div></div>
    <div class="acard-grid">
      <div class="acard-col">
        <div class="card acard-taf-card"><h3>TAF</h3><div class="scroll acard-taf wx"></div></div>
        <div class="card acard-atis-card"><h3 class="acard-atis-h">ATIS</h3><div class="scroll acard-atis"></div></div>
      </div>
      <div class="card acard-pos-card"><h3>Stanowiska APP · TWR · GND · DEL · ATIS<span class="acard-upd"></span></h3><div class="scroll acard-pos-body"></div></div>
    </div>
  </div>`;
  const $ = (s) => el.querySelector(s);
  let timer = null, destroyed = false;
  const load = async () => {
    let d;
    try { d = await api(`/api/aerodromes/${encodeURIComponent(icao)}/card`); } catch (e) {
      if (destroyed) return;
      $(".aero-errors").innerHTML = `<p class="error">${esc(e.message)}</p>`;
      if (!$(".acard-pos-body").innerHTML) {
        $(".metar-head .wx").innerHTML = colorize(null);
        $(".acard-taf").innerHTML = colorize(null);
      }
      return;
    }
    if (destroyed) return;
    onInfo?.(d);
    $(".acard-name").textContent = d.name || "";
    $(".acard-fir").textContent = [d.fir ? `FIR ${d.fir}${d.fir_label ? " · " + d.fir_label : ""}` : "",
      d.kind === "MIL" ? "lotnisko wojskowe" : "", d.elevation_ft !== null && d.elevation_ft !== undefined ? `elev ${Math.round(d.elevation_ft)} ft` : ""]
      .filter(Boolean).join(" · ");
    const errs = [...(d.errors || []), ...(d.network_error ? [d.network_error] : [])];
    $(".aero-errors").innerHTML = errs.length ? `<p class="error">${errs.map(esc).join("<br>")}</p>` : "";
    $(".metar-head .wx").innerHTML = colorize(d.metar);
    $(".metar-head .obs").textContent = obsText(d.parsed);
    $(".acard-taf").innerHTML = d.taf ? wxLines("taf", d.taf) : colorize(null);
    $(".acard-pos-body").innerHTML = positionsTable(d.positions);
    // ATIS w sieci: litera i tekst linijka po linijce (jak w PRZEGLĄDZIE), kilka ATIS-ów (ARR/DEP) jeden pod drugim
    const atis = d.positions.filter((p) => p.type === "ATIS" && p.online);
    $(".acard-atis-h").textContent = `ATIS ${atis.map((p) => p.online.atis_code || "").filter(Boolean).join(" / ")}`;
    $(".acard-atis").innerHTML = atis.length ? atis.map((p) => `<div class="acard-atis-one">
        <div class="hint">${esc(p.online.callsign || p.callsign)} ${esc(p.online.frequency || p.frequency)} · ${esc(p.online.name || "?")} · od ${esc(hhmm(p.online.logon_time))}</div>
        <div class="mono atis-text">${atisHtml(p.online.text_atis) || '<span class="hint">bez tekstu</span>'}</div></div>`).join("")
      : `<span class="hint">${d.network_error ? esc(d.network_error) : "ATIS nie jest teraz nadawany w sieci VATSIM."}</span>`;
    $(".acard-upd").textContent = `VATSIM ${d.updated ? hhmm(d.updated) : "–"}`;
  };
  load();
  timer = setInterval(load, 60000);
  return { destroy: () => { destroyed = true; clearInterval(timer); } };
}

