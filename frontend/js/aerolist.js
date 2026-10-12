// AERODROME: listy lotnisk FIR-ów (podzakładki EPWW … UKLV) i karta lotniska zagranicznego.
// Lista jak w PANDORZE: u góry wyszukiwarka z klawiatury z przyciskami WYŚWIETL i WYCZYŚĆ, niżej wiersze
// "ICAO NAZWA" w kilku kolumnach, przewijane w swoim okienku. Backend: GET /api/aerodromes/fir/{klucz}
// (services/aerolist.py) i GET /api/aerodromes/{icao}/card (METAR, TAF, stanowiska ze stanem VATSIM).
import { api, atisHtml, debounce, esc, hhmm, vatsimAtc } from "./api.js";
import { colorize, wxLines } from "./tabs/meteo.js";

export const FIR_KEYS = ["EPWW", "ESAA", "ED", "ET", "LKAA", "LZBB", "UMKK", "EYVL", "UMMV", "UKLV"];
export const FIR_LABELS = {
  EPWW: "Polska", ESAA: "Szwecja", ED: "Niemcy, lotniska cywilne", ET: "Niemcy, lotniska wojskowe", LKAA: "Czechy",
  LZBB: "Słowacja", UMKK: "Kaliningrad", EYVL: "Litwa", UMMV: "Białoruś", UKLV: "Ukraina, FIR Lwów",
};
// rodzaje stanowisk jak na plakietkach lotnisk na MAP (airspace.js FACILITIES): kolory .ab-* w style.css
const FAC = [["DEL", "D"], ["GND", "G"], ["TWR", "T"], ["ATIS", "A"], ["APP", "APP"]];
const VIEWS = ["awos", "ruch", "dman", "aman"];   // widoki lotniska z bazy w adresie (#aerodrome/EPWA/awos, RUCH: …/dman, …/aman); PRZEGLĄD = sam ICAO

// wyszukiwanie bez wielkości liter i polskich znaków: "łódź" = "LODZ" (Ł nie rozkłada się w NFD)
export const fold = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/[łŁ]/g, "L").toUpperCase().replace(/\s+/g, " ").trim();
export const isIcao = (s) => /^[A-Z0-9]{4}$/.test(s);
// do porównań: znaki rozdzielające (-/(),.') jak spacja, więc "bielsko biala" = "BIELSKO-BIALA", "M.R." = "M R"
const norm = (s) => fold(s).replace(/[-/(),.'’]/g, " ").replace(/\s+/g, " ").trim();
// wpisany tekst w dwóch postaciach: niemieckie Ä Ö Ü bez kropek (A O U) i jako AE OE UE, jak w navdata
// ("nürnberg" znajduje NUERNBERG, "gällivare" GALLIVARE)
export const queryForms = (s) => [...new Set([norm(s), norm(String(s ?? "").replace(/[äÄ]/g, "AE").replace(/[öÖ]/g, "OE")
  .replace(/[üÜ]/g, "UE"))])].filter(Boolean);
// klucze lotniska: nazwa, nazwa z AE OE UE → A O U ("nurnberg" bez kropek też trafia) i inne nazwy z backendu (alias)
export const searchKeys = (name, alias = []) => {
  const keys = [];
  for (const k of [name, ...(alias || [])].map(norm).filter(Boolean)) {
    keys.push(k);
    const de = k.replace(/AE/g, "A").replace(/OE/g, "O").replace(/UE/g, "U");
    if (de !== k) keys.push(de);
  }
  return keys;
};

// Jak dobrze lotnisko pasuje do wpisanego tekstu: 0 dokładny ICAO, 1 początek ICAO, 2 nazwa od tego tekstu,
// 2.5 słowo w nazwie od tego tekstu, 3 dowolne miejsce nazwy; Infinity = nie pasuje. it: {icao, keys}
export function matchTier(it, forms) {
  let t = Infinity;
  for (const q of forms) {
    if (it.icao === q) return 0;
    if (it.icao.startsWith(q)) t = Math.min(t, 1);
    for (const k of it.keys) {
      if (k.startsWith(q)) t = Math.min(t, 2);
      else if (k.includes(" " + q)) t = Math.min(t, 2.5);
      else if (k.includes(q)) t = Math.min(t, 3);
    }
  }
  return t;
}
// Kolejność trafień do Enter: grupa (ICAO dokładny, ICAO od początku, nazwa, dowolne miejsce), w grupie nazw
// ważniejsze lotnisko (rank: główne lotnisko miasta, potem ze stanowiskami ATC), dalej od początku nazwy przed
// słowem w środku, na końcu kolejność listy (ICAO). "warszawa" → EPWA, "krakow" → EPKK, "BER" → BERLIN BRANDENBURG.
export function bestMatch(items, forms) {
  const less = (a, b) => { for (let j = 0; j < a.length; j++) if (a[j] !== b[j]) return a[j] < b[j]; return false; };
  let best = null, bk = null;
  items.forEach((it, n) => {
    const t = matchTier(it, forms);
    if (t === Infinity) return;
    const g = Math.floor(t);
    const k = [g, g >= 2 ? it.rank ?? 2 : 0, t, it.i ?? n];
    if (!bk || less(k, bk)) { best = it; bk = k; }
  });
  return best;
}

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
// "EPWA" / "EDDB" = lotnisko (PRZEGLĄD albo karta), "EPWA/awos", "EPWA/ruch" = widok lotniska z bazy.
// Zwraca postać kanoniczną albo null (nie rozpoznano).
export function aeroArg(arg) {
  const a = String(arg ?? "").trim();
  if (/^(0|overview)$/i.test(a)) return "0";
  const m = /^fir\/(\w{2,4})$/i.exec(a);
  if (m) return FIR_KEYS.includes(m[1].toUpperCase()) ? "fir/" + m[1].toUpperCase() : null;
  const v = /^([A-Za-z0-9]{4})(?:\/(\w+))?$/.exec(a);
  if (!v) return null;
  const view = (v[2] || "").toLowerCase();
  if (view && !VIEWS.includes(view) && !["overview", "przeglad"].includes(view)) return null;
  return v[1].toUpperCase() + (VIEWS.includes(view) ? "/" + view : "");
}
// "EPWA/awos" → {code: "EPWA", view: "awos"}; "EPWA" → view "overview"
export const splitArg = (a) => {
  const [code, view] = String(a || "").split("/");
  return { code, view: VIEWS.includes(view) ? view : "overview" };
};

// odmiana: 1 lotnisko, 2–4 lotniska (też 22–24 …), 5–21 lotnisk (jak overview.js)
export const lotnisk = (n) => (n === 1 ? "lotnisko" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "lotniska" : "lotnisk");

// listy lotnisk: raz na sesję (navdata zmienia się co AIRAC), błąd nie zostaje w pamięci
const lists = {};
export function firList(key) {
  const c = lists[key] ||= {};
  c.p ||= api(`/api/aerodromes/fir/${encodeURIComponent(key)}`).then((d) => (c.data = d))
    .catch((e) => { c.p = null; throw e; });
  return c.p;
}
// czy kod jest na którejś z wczytanych list (bez czekania na resztę)
export const onLoadedList = (icao) => Object.values(lists).some((c) => c.data?.some((a) => a.icao === icao));

// kolumny listy po co najmniej 300 px
const COL_MIN = 300;

// Układ wierszy: N kolumn czytanych z góry na dół. Lista, która nie mieści się w okienku, dzieli się na bloki
// wysokości okienka (N kolumn × tyle wierszy, ile się mieści) ułożone jeden pod drugim, więc kolejność czytania
// (kolumna po kolumnie, potem blok niżej) jest zgodna z przewijaniem. Krótka lista: kolumny wyrównane, po co
// najmniej 8 wierszy. Zwraca dla każdego widocznego wiersza {row, col, block, r} (row, col od 0).
export function blockLayout(n, maxCols, fitRows) {
  // mieści się: najmniej kolumn, przy których żadna nie wychodzi poza okienko, ale nie krótszych niż 8 wierszy
  const cols = Math.max(1, n <= maxCols * fitRows ? Math.min(maxCols, Math.max(Math.ceil(n / 8), Math.ceil(n / fitRows))) : maxCols);
  const per = cols * Math.max(1, fitRows), out = [];
  let base = 0;
  for (let b = 0; b * per < n; b++) {
    const m = Math.min(per, n - b * per), rows = Math.ceil(m / cols);
    for (let j = 0; j < m; j++) out.push({ row: base + (j % rows), col: Math.floor(j / rows), block: b, r: j % rows });
    base += rows;
  }
  return { cols, cells: out };
}

// Na ekranie: karta przeglądarki widoczna i element w pokazanej zakładce (ukryta zakładka ma display: none). Listy i karta
// odświeżają się tylko wtedy; po powrocie (focus/resume, widoczna karta przeglądarki) od razu, jeśli dane są starsze niż okres.
const onScreen = (el) => !document.hidden && el.isConnected && el.offsetParent !== null;

// Strona listy jednego FIR-u. state: {q, sel, scroll} (zapamiętane w aerodrome.js, ← wraca do tej samej listy z tym
// samym filtrem); onOpen(icao) otwiera lotnisko (krok w historii); onChange(state) po zmianie filtra, wyboru albo
// przewinięcia przez użytkownika (z opóźnieniem). Zwraca {focus(), save(), destroy()}.
export function mountFirList(el, key, state, { onOpen, onChange }) {
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
Enter = WYŚWIETL (dokładny ICAO, inaczej zaznaczone trafienie: główne lotnisko miasta przed mniejszymi), ↑ ↓ = wybór, Esc = WYCZYŚĆ.">${FAC.map(([k, l]) =>
        `<span class="ab ab-${k.toLowerCase()}">${l}</span>`).join("")} online · ↑↓ Enter Esc</span>
    </div>
    <div class="alist-box"><div class="alist-msg hint">Ładowanie listy lotnisk…</div><div class="alist-cols"></div></div>
  </div>`;
  const $ = (s) => el.querySelector(s);
  const input = $(".alist-q"), box = $(".alist-box"), cols = $(".alist-cols"), msg = $(".alist-msg");
  // items: {i, icao, name, rank, keys, el, tier}; vis: widoczne po filtrze, w kolejności czytania
  let items = [], byIcao = new Map(), vis = [], data = null, sel = null, destroyed = false, atcTimer = null, dirty = true, atcAt = 0;

  const select = (it, scroll = true) => {
    sel?.el.classList.remove("sel");
    sel = it || null;
    if (!sel) return;
    sel.el.classList.add("sel");
    if (scroll) sel.el.scrollIntoView({ block: "nearest" });
  };

  // rozmieszczenie widocznych wierszy (blockLayout); w ukrytej zakładce (szerokość 0) czeka do pokazania
  const layout = () => {
    if (!data) return;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H) { dirty = true; return; }
    dirty = false;
    cols.style.gridAutoRows = "auto";
    const first = vis[0]?.el;
    const rowH = (first && first.getBoundingClientRect().height) || 26;
    const fit = Math.max(1, Math.floor(H / rowH));
    const { cols: n, cells } = blockLayout(vis.length, Math.max(1, Math.floor(W / COL_MIN)), fit);
    cols.style.gridTemplateColumns = `repeat(${n}, minmax(0, 1fr))`;
    // kilka bloków: wiersze rozciągnięte tak, żeby blok miał dokładnie wysokość okienka (przewinięcie o ekran = blok)
    if (cells.some((c) => c.block > 0)) cols.style.gridAutoRows = `${H / fit}px`;
    vis.forEach((it, j) => {
      const c = cells[j];
      it.el.style.gridArea = `${c.row + 1} / ${c.col + 1}`;
      it.el.classList.toggle("odd", c.r % 2 === 1);        // pasy w poprzek kolumn, jak wiersze w PANDORZE
      it.el.classList.toggle("cn", c.col > 0);            // linia między kolumnami
      it.el.classList.toggle("blk", c.block > 0 && c.r === 0);   // linia nad kolejnym blokiem
    });
  };

  const filter = ({ keepSel = false } = {}) => {
    if (!data) return;
    const forms = queryForms(input.value);
    vis = [];
    for (const it of items) {
      it.tier = forms.length ? matchTier(it, forms) : 0;
      it.el.hidden = it.tier === Infinity;
      if (!it.el.hidden) vis.push(it);
    }
    $(".alist-n").textContent = forms.length ? `${vis.length} z ${data.length}` : `${data.length} ${lotnisk(data.length)}`;
    msg.hidden = vis.length > 0;
    if (!vis.length) {
      const q = fold(input.value);
      msg.innerHTML = `Brak lotniska pasującego do „${esc(input.value.trim())}” w ${esc(key)}.`
        + (isIcao(q) ? ` Enter albo WYŚWIETL otworzy <b>${esc(q)}</b>.` : "");
    }
    layout();
    if (!(keepSel && sel && !sel.el.hidden)) select(forms.length ? bestMatch(vis, forms) : null);
  };

  const open = () => {
    const q = fold(input.value);
    const target = sel && !sel.el.hidden ? sel.icao : q ? bestMatch(vis, queryForms(input.value))?.icao || (isIcao(q) ? q : null) : null;
    if (target) onOpen(target);
    else input.focus();
  };
  const clear = () => {
    input.value = "";
    filter();
    box.scrollTop = 0;
    input.focus();
    changed();
  };

  const save = () => ({ q: input.value, sel: sel?.icao || null, scroll: box.scrollTop });
  const changed = debounce(() => { if (!destroyed) onChange?.(save()); }, 400);
  input.addEventListener("input", () => { filter(); changed(); });
  box.addEventListener("scroll", changed, { passive: true });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); open(); return; }
    if (e.key === "Escape") { e.preventDefault(); clear(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!vis.length) return;
      const i = vis.indexOf(sel);
      select(vis[e.key === "ArrowDown" ? (i < 0 ? 0 : Math.min(vis.length - 1, i + 1)) : Math.max(0, i - 1)]);
      changed();
    }
  });
  $(".alist-go").addEventListener("click", open);
  $(".alist-clear").addEventListener("click", clear);
  cols.addEventListener("click", (e) => {
    const it = byIcao.get(e.target.closest(".alist-row")?.dataset.icao);
    if (it) { select(it, false); onOpen(it.icao); }
  });
  // pisanie poza polem (np. po kliknięciu w listę albo w tytuł) trafia do wyszukiwarki; tylko gdy lista jest na
  // ekranie, i nie z innych pól ani przycisków (spacja na przycisku go klika)
  const onKey = (e) => {
    if (destroyed || !el.isConnected || !input.offsetParent || e.target === input) return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) return;
    if (e.target instanceof Element && e.target.closest("input, textarea, select, button, [contenteditable]")) return;
    input.focus();
  };
  document.addEventListener("keydown", onKey);
  // zmiana rozmiaru okienka (okno przeglądarki, pokazanie ukrytej zakładki): kolumny i bloki od nowa
  const ro = new ResizeObserver(() => layout());
  ro.observe(box);

  // kontrola online: plakietki D/G/T/A/APP przy lotniskach (ten sam cache co MAP, co 30 s, tylko gdy lista jest na ekranie)
  const ATC_MS = 30000;
  const drawAtc = async () => {
    if (destroyed || !data || !onScreen(box)) return;
    atcAt = Date.now();
    let d;
    try { d = await vatsimAtc(); } catch { return; }
    if (destroyed) return;
    const on = new Map((d.airports || []).map((a) => [a.icao, a.facilities || {}]));
    items.forEach((it) => {
      const f = on.get(it.icao);
      const html = f ? FAC.filter(([k]) => f[k]?.length).map(([k, l]) => `<span class="ab ab-${k.toLowerCase()}">${l}</span>`).join("") : "";
      const slot = it.el.querySelector(".alist-atc");
      if (slot.innerHTML !== html) slot.innerHTML = html;
      it.el.classList.toggle("on", !!html);
    });
  };

  firList(key).then((d) => {
    if (destroyed) return;
    data = d;
    cols.innerHTML = d.map((a) => `<div class="alist-row" data-icao="${esc(a.icao)}" title="${esc(a.icao)} ${esc(a.name)}${a.kind ? " · " + esc(a.kind) : ""}">
      <b>${esc(a.icao)}</b><span class="alist-nm">${esc(a.name || "–")}</span>${a.kind ? `<i class="alist-kind">${esc(a.kind)}</i>` : ""}<span class="alist-atc"></span></div>`).join("");
    items = d.map((a, i) => ({ i, icao: a.icao, name: a.name, rank: a.rank ?? 2, keys: searchKeys(a.name, a.alias), el: cols.children[i] }));
    byIcao = new Map(items.map((it) => [it.icao, it]));
    filter();
    const back = state.sel && byIcao.get(state.sel);
    if (back && !back.el.hidden) select(back, false);
    box.scrollTop = state.scroll || 0;
    if (back && !back.el.hidden && !state.scroll) back.el.scrollIntoView({ block: "nearest" });
    drawAtc();
    atcTimer = setInterval(drawAtc, ATC_MS);
  }).catch((e) => {
    if (destroyed) return;
    msg.hidden = false;
    msg.innerHTML = `<span class="error">${esc(e.message)}</span>`;
  });

  const atcDue = () => { if (Date.now() - atcAt > ATC_MS) drawAtc(); };
  document.addEventListener("visibilitychange", atcDue);

  return {
    focus: () => { if (dirty) layout(); input.focus(); input.select(); atcDue(); },
    save,
    destroy: () => {
      destroyed = true;
      clearInterval(atcTimer);
      ro.disconnect();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", atcDue);
    },
  };
}

// --- karta lotniska zagranicznego (i polskiego spoza bazy lotnisk) ----------------------------------------------

const TYPE_LABEL = { APP: "APP", TWR: "TWR", GND: "GND", DEL: "DEL", ATIS: "ATIS" };
// odpowiedź 404 karty (backend: routers/aerodromes.py card): takiego lotniska nie ma w danych
const UNKNOWN = /^Nie znam lotniska/;

// Czas obserwacji METAR (np. 051630Z) i ile minut temu (też PRZEGLĄD w aerodrome.js)
export function obsAge(time) {
  const m = /^(\d{2})(\d{2})(\d{2})Z$/.exec(time || "");
  if (!m) return null;
  const now = new Date();
  let t = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), +m[1], +m[2], +m[3]);
  if (t - now.getTime() > 86400000) t = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, +m[1], +m[2], +m[3]);
  return Math.round((now.getTime() - t) / 60000);
}
// "obs. 16:30Z · 12 min temu"; stale = starszy niż 70 min (na czerwono, jak w PRZEGLĄDZIE)
export function obsInfo(time) {
  const m = /^\d{2}(\d{2})(\d{2})Z$/.exec(time || "");
  if (!m) return { text: "", stale: false };
  const age = obsAge(time);
  return { text: `obs. ${m[1]}:${m[2]}Z${age !== null ? ` · ${age} min temu` : ""}`, stale: age !== null && age > 70 };
}

function positionsTable(ps, unknown) {
  if (!ps.length) return `<p class="hint">Brak stanowisk APP, TWR, GND, DEL i ATIS tego lotniska w danych PolIDS${unknown ? "" : " i nikt nie jest teraz zalogowany"}.</p>`;
  return `<table class="data acard-pos"><thead><tr><th>Typ</th><th>Stanowisko</th><th>Częst.</th><th>Stan</th></tr></thead><tbody>
    ${ps.map((p) => {
      const on = p.online;
      const logged = on && on.callsign && on.callsign !== p.callsign ? ` jako&nbsp;${esc(on.callsign)}` : "";
      const freq = (on && on.frequency) || p.frequency;
      // bez danych z sieci VATSIM stan jest nieznany: nie "offline"; "od 16:34Z" i "jako X" z twardą spacją (nie rozrywa ich zawijanie)
      const state = unknown ? `<span class="acard-unk">brak danych</span>` : !on ? "offline"
        : `<b>${esc(on.name || "?")}</b> · od&nbsp;${esc(hhmm(on.logon_time))}${logged}${p.type === "ATIS" && on.atis_code ? ` · <b class="acard-letter">${esc(on.atis_code)}</b>` : ""}`;
      return `<tr class="${on ? "on" : "off"}"><td><span class="ab ab-${esc(p.type.toLowerCase())}">${esc(TYPE_LABEL[p.type] || p.type)}</span></td>
        <td class="acard-cs">${esc(p.callsign)}${p.name ? `<span class="acard-pn">${esc(p.name)}</span>` : ""}</td>
        <td><span class="freq ${on ? "on" : ""}">${esc(freq || "–")}</span></td><td class="acard-st">${state}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// onInfo(d): dane wczytane (nagłówek w pasku AERODROME, zapamiętanie lotniska) po każdym udanym odświeżeniu;
// onMissing(): takiego lotniska nie ma w danych. Odświeżanie co minutę tylko na ekranie. Zwraca {resume(), destroy()}.
export function mountAeroCard(el, icao, { onInfo, onMissing } = {}) {
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
  const CARD_MS = 60000;
  let timer = null, destroyed = false, loaded = false, at = 0;
  const due = () => { if (!destroyed && Date.now() - at > CARD_MS) load(); };
  const stop = () => { destroyed = true; clearInterval(timer); document.removeEventListener("visibilitychange", due); };
  const load = async () => {
    // pierwsze wczytanie zawsze; potem tylko na ekranie (każde zapytanie to METAR, TAF i VATSIM na serwerze)
    if (destroyed || (at && !onScreen(el))) return;
    at = Date.now();
    let d;
    try { d = await api(`/api/aerodromes/${encodeURIComponent(icao)}/card`); } catch (e) {
      if (destroyed) return;
      // nieznany kod (literówka): jeden komunikat zamiast pustych paneli, bez odpytywania co minutę
      if (UNKNOWN.test(e.message)) {
        stop();
        el.innerHTML = `<div class="acard"><div class="acard-head"><b>${esc(icao)}</b></div>
          <div class="acard-missing"><p>Nie znam lotniska <b>${esc(icao)}</b>: nie ma go w navdata ani w danych stanowisk PolIDS.</p>
          <p class="hint">Sprawdź kod ICAO. ← wraca do poprzedniego ekranu.</p></div></div>`;
        onMissing?.();
        return;
      }
      $(".aero-errors").innerHTML = `<p class="error">${esc(e.message)}</p>`;
      if (!loaded) {   // nic jeszcze nie wczytano: "brak danych", nie NIL (NIL = lotnisko nie nadaje METAR)
        $(".metar-head .wx").innerHTML = `<span class="hint">METAR: brak danych</span>`;
        $(".acard-taf").innerHTML = $(".acard-atis").innerHTML = `<span class="hint">brak danych</span>`;
      }
      return;
    }
    if (destroyed) return;
    loaded = true;
    onInfo?.(d);
    const net = !!d.network_error;
    $(".acard-name").textContent = d.name || "";
    $(".acard-fir").textContent = [d.fir ? `${d.fir}${d.fir_label ? " · " + d.fir_label : ""}` : "",
      d.kind === "MIL" ? "lotnisko wojskowe" : "", d.elevation_ft !== null && d.elevation_ft !== undefined ? `elev ${Math.round(d.elevation_ft)} ft` : ""]
      .filter(Boolean).join(" · ");
    const errs = [...(d.errors || []), ...(net ? [d.network_error] : [])];
    $(".aero-errors").innerHTML = errs.length ? `<p class="error">${errs.map(esc).join("<br>")}</p>` : "";
    $(".metar-head .wx").innerHTML = colorize(d.metar);
    const obs = obsInfo(d.parsed?.time);
    $(".metar-head .obs").textContent = obs.text;
    $(".metar-head .obs").classList.toggle("stale", obs.stale);
    $(".acard-taf").innerHTML = d.taf ? wxLines("taf", d.taf) : colorize(null);
    $(".acard-pos-body").innerHTML = positionsTable(d.positions, net);
    // ATIS w sieci: litera i tekst linijka po linijce (jak w PRZEGLĄDZIE), kilka ATIS-ów (ARR/DEP) jeden pod drugim
    const atis = d.positions.filter((p) => p.type === "ATIS" && p.online);
    $(".acard-atis-h").textContent = `ATIS ${atis.map((p) => p.online.atis_code || "").filter(Boolean).join(" / ")}`;
    $(".acard-atis").innerHTML = atis.length ? atis.map((p) => `<div class="acard-atis-one">
        <div class="hint">${esc(p.online.callsign || p.callsign)} ${esc(p.online.frequency || p.frequency)} · ${esc(p.online.name || "?")} · od&nbsp;${esc(hhmm(p.online.logon_time))}</div>
        <div class="mono atis-text">${atisHtml(p.online.text_atis) || '<span class="hint">bez tekstu</span>'}</div></div>`).join("")
      : `<span class="hint">${net ? "Brak danych z sieci VATSIM (błąd powyżej)." : "ATIS nie jest teraz nadawany w sieci VATSIM."}</span>`;
    $(".acard-upd").textContent = `VATSIM ${d.updated ? hhmm(d.updated) : "–"}`;
  };
  load();
  timer = setInterval(load, CARD_MS);
  document.addEventListener("visibilitychange", due);
  return { resume: due, destroy: stop };
}
