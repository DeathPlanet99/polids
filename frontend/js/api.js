// Wspólne funkcje: zapytania do API i drobne narzędzia DOM.

// Serwer nie odpowiada (brak internetu, restart serwera): zamiast angielskiego "Failed to fetch" komunikat po polsku i jeden pasek
// u dołu ekranu; znika po pierwszym udanym zapytaniu (sprawdzamy co 5 s, także gdy żadna zakładka nic nie odświeża).
// Po powrocie serwera zdarzenie ONLINE_EVENT na window: zakładki z komunikatem błędu od razu pobierają dane ponownie.
export const OFFLINE_MSG = "brak połączenia z serwerem PolIDS";
export const ONLINE_EVENT = "polids:online";
let offlineBar = null, offlineTimer = null;
function setOffline(on) {
  if (!on) {
    if (!offlineBar) return;
    clearInterval(offlineTimer);
    offlineTimer = null;
    offlineBar.remove();
    offlineBar = null;
    // po bieżącym zapytaniu, żeby zakładka nie pobierała tego samego dwa razy w jednej chwili
    setTimeout(() => window.dispatchEvent(new Event(ONLINE_EVENT)), 0);
    return;
  }
  if (offlineBar) return;
  offlineBar = h(`<div class="srv-off" role="alert"><b>Brak połączenia z serwerem PolIDS</b>
    <span>sprawdź internet; strona połączy się sama, gdy serwer odpowie.</span></div>`);
  document.body.append(offlineBar);
  offlineTimer = setInterval(() => fetch("/api/config", { cache: "no-store" }).then(() => setOffline(false), () => {}), 5000);
}

export async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    if (e?.name === "AbortError") throw e;   // zapytanie przerwane celowo (AbortController)
    setOffline(true);
    throw new Error(OFFLINE_MSG);
  }
  setOffline(false);
  // sesja wygasła
  if (res.status === 401) {
    location.href = "/auth/login";
    throw new Error("Wymagane logowanie");
  }
  let body = null;
  try { body = await res.json(); } catch { /* pusta odpowiedź albo nie JSON (np. strona błędu) */ }
  if (!res.ok) {
    const msg = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))
      : `Błąd serwera PolIDS (HTTP ${res.status})`;
    throw new Error(msg);
  }
  return body;
}

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function fmt(v, digits = 1, unit = "") {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (Number.isNaN(n)) return esc(v);
  return (Number.isInteger(n) ? n : n.toFixed(digits)) + unit;
}

export function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// --- Nawigacja: przycisk ← w menu i Wstecz przeglądarki (Alt+←) wracają do poprzedniego ekranu tej sesji.
// Adres ekranu: "#zakładka/arg", arg = wszystko po pierwszym "/" (np. "#aerodrome/fir/ED" → zakładka "aerodrome",
// arg "fir/ED"). Historię prowadzi app.js (setRouter); bez niego (testy w Node) navigate i remember nic nie robią.
let router = null;
export function setRouter(r) { router = r; }
// otwiera inną zakładkę (albo inny stan bieżącej) z wnętrza zakładki; zapisuje krok do cofnięcia
export function navigate(tab, arg) { router?.open(tab, arg); }
// zakładka sama zmieniła stan (podzakładka, wybrane lotnisko): nowy wpis historii bez ponownego activate();
// { replace: true } podmienia bieżący wpis zamiast dodawać krok. Wywołane z activate() tej zakładki tylko
// uzupełnia adres otwieranego właśnie ekranu (bez dodatkowego kroku); dla zakładki w tle nie robi nic.
export function remember(tab, arg, opts = {}) { router?.remember(tab, arg, opts); }
// Dane ekranu przypięte do jego wpisu historii (np. filtr, zaznaczenie i przewinięcie listy lotnisk FIR-u): stash zapisuje
// je w bieżącym wpisie, gdy ekran tej zakładki jest na wierzchu; stashed(tab) w activate() zwraca je tylko przy powrocie
// do istniejącego wpisu (←, Wstecz, Dalej), więc dwa wpisy tej samej listy pamiętają każdy swój filtr.
export function stash(tab, data) { router?.stash?.(tab, data); }
export function stashed(tab) { return router?.stashed?.(tab); }
// klucz zakładki, do której należy element (section.view[data-tab] tworzy app.js)
export const tabOf = (el) => el?.closest?.(".view")?.dataset.tab || null;

const decodeArg = (s) => { try { return decodeURIComponent(s); } catch { return s; } };
// "#aerodrome/fir/ED" → {tab: "aerodrome", arg: "fir/ED"}; brak "/" → arg ""
export function parseHash(hash) {
  const s = String(hash || "").replace(/^#/, "");
  const i = s.indexOf("/");
  return i < 0 ? { tab: decodeArg(s), arg: "" } : { tab: decodeArg(s.slice(0, i)), arg: decodeArg(s.slice(i + 1)) };
}
export const hashOf = (tab, arg) => "#" + tab + (arg ? "/" + encodeURI(String(arg)).replace(/#/g, "%23") : "");

// Podmenu zakładki (subtabs) zarejestrowane przy swoim <nav>: ścieżka wybranych id od zewnętrznego do
// zagnieżdżonego ("sat/eur") to arg w adresie zakładki bez własnego activate().
const NAVS = new WeakMap();
const WANT = new WeakMap();   // ścieżka z adresu dla podmenu, które dopiero powstaną (dane wczytywane asynchronicznie)
const navList = (view) => [...view.querySelectorAll("nav.submenu")].filter((n) => NAVS.has(n));
export const navPath = (view) => navList(view).map((n) => NAVS.get(n).current()).join("/");
// wybiera podzakładki wg ścieżki (bez zapisu w historii); "" = pierwsza podzakładka na każdym poziomie
export function selectNavPath(view, arg) {
  const segs = arg ? String(arg).split("/") : [];
  WANT.set(view, segs);
  for (let i = 0; i < navList(view).length; i++) {   // lista od nowa: wybór może zbudować zagnieżdżone podmenu
    const ctl = NAVS.get(navList(view)[i]);
    const id = ctl.has(segs[i]) ? segs[i] : ctl.first;
    if (ctl.current() !== id) ctl.show(id);
  }
}

// Zakładka otwierana pierwszy raz od razu z adresem (np. #meteo/turb po Wstecz albo z odnośnika): podmenu budowane
// w mount() pokazują od razu podzakładkę z adresu, bez wczytywania pierwszej (METAR PL przed TURB). app.js woła przed
// mount(); dla zakładki z własnym activate(arg) zapis jest potem usuwany (clearNavPath), bo arg czyta ona sama.
export function presetNavPath(view, arg) { if (arg) WANT.set(view, String(arg).split("/")); }
export function clearNavPath(view) { WANT.delete(view); }

// Podmenu PANDORY: kolumna zielonych przycisków obok menu głównego.
// tabs: [{id, label, render(pane), fill?}] albo {sep: true} jako odstęp.
// Kliknięcie podzakładki to krok do cofnięcia (remember z kluczem zakładki i ścieżką id podmenu);
// opts.remember = false wyłącza, opts.arg(id) podaje własny arg adresu zamiast ścieżki id.
export function subtabs(root, tabs, opts = {}) {
  const nav = h(`<nav class="submenu">${tabs.map((t) => t.sep ? `<div class="sep"></div>` : `<button data-id="${esc(t.id)}">${esc(t.label)}</button>`).join("")}</nav>`);
  const pane = h(`<div class="pane"></div>`);
  root.append(nav, pane);
  let current = null, currentId = null;
  const first = tabs.find((x) => !x.sep).id;
  const show = (id) => {
    current?.destroy?.();
    const t = tabs.find((x) => !x.sep && x.id === id) || tabs.find((x) => !x.sep);
    currentId = t.id;
    nav.querySelectorAll("button[data-id]").forEach((b) => b.classList.toggle("active", b.dataset.id === t.id));
    pane.className = "pane" + (t.fill ? " fill" : "");
    pane.innerHTML = "";
    current = t.render(pane) || null;
  };
  NAVS.set(nav, { first, current: () => currentId, show, has: (id) => !!id && tabs.some((x) => !x.sep && x.id === id) });
  nav.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b) return;
    const view = nav.closest(".view");
    if (view) WANT.delete(view);
    show(b.dataset.id);
    const tab = tabOf(nav);
    if (tab && opts.remember !== false) remember(tab, opts.arg ? opts.arg(currentId) : navPath(view));
  });
  // podmenu budowane po wczytaniu danych albo zagnieżdżone: podzakładka z adresu, jeśli ją wskazuje
  const view = nav.closest(".view");
  const want = view && WANT.get(view)?.[navList(view).indexOf(nav)];
  show(want && NAVS.get(nav).has(want) ? want : first);
  return { nav, pane, show, current: () => currentId };
}

// Podmenu z literami A–Z (jak w AIRCRAFT / CALLSIGN w PANDORZE). onPick("") = wszystkie.
// Kliknięcie litery to krok do cofnięcia: remember(zakładka, "A".."Z" albo "" dla wszystkich); activate(arg)
// zakładki ustawia literę z adresu przez set(). opts.remember = false wyłącza.
export function letterMenu(root, onPick, opts = {}) {
  const letters = ["*", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];
  const nav = h(`<nav class="submenu letters">${letters.map((l) => `<button data-l="${l}">${l === "*" ? "∗" : l}</button>`).join("")}</nav>`);
  root.append(nav);
  const set = (l) => nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.l === l));
  nav.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-l]");
    if (!b) return;
    set(b.dataset.l);
    const l = b.dataset.l === "*" ? "" : b.dataset.l;
    onPick(l);
    const tab = tabOf(nav);
    if (tab && opts.remember !== false) remember(tab, l);
  });
  set("*");
  return { set: (l) => set(l || "*") };
}

// Nowy adres w ramce (iframe) bez kroku w historii przeglądarki: zmiana src w załadowanej ramce dodaje wpis, który
// zabrałby najbliższe ← i Wstecz (cofnięcie samej ramki zamiast ekranu). Nowa ramka ładuje adres bez wpisu.
// Zwraca nową ramkę (stara jest podmieniana w DOM, razem z atrybutami).
export function frameGo(frame, url) {
  if (!frame.getAttribute("src")) { frame.src = url; return frame; }
  const f = frame.cloneNode(false);
  f.src = url;
  frame.replaceWith(f);
  return f;
}

export function iframeWithFallback(pane, url, note = "") {
  pane.append(h(`<div class="toolbar" style="padding:6px 10px;margin:0;background:var(--panel)">
      <span class="hint mono">${esc(url)}</span><span style="flex:1"></span>
      ${note ? `<span class="hint">${esc(note)}</span>` : ""}
      <a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Otwórz w nowej karcie ↗</a></div>`));
  pane.append(h(`<iframe class="embed" src="${esc(url)}" referrerpolicy="no-referrer"></iframe>`));
}

// Podkłady mapy. CARTO wymaga klucza w parametrze ?key= (POLIDS_CARTO_API_KEY); bez klucza kafelki dostają
// znak wodny "API KEY REQUIRED". Dlatego bez klucza (albo gdy CARTO odrzuca klucz, np. 403 przy ograniczeniu
// domen) używamy szarych podkładów Esri Canvas, które działają bez klucza. "white" = sama biała plansza.
const CARTO_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, © <a href="https://carto.com/attributions">CARTO</a>';
const ESRI_ATTR = "Powered by Esri | Esri, HERE, Garmin, © OpenStreetMap contributors, and the GIS user community";
const cartoUrl = (style, key) => `https://{s}.basemaps.cartocdn.com/rastertiles/${style}/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(key)}`;
// Esri ma kafelki Europy do zoomu 16, dalej powiększamy ostatni poziom
const esri = (L, svc) => L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${svc}/MapServer/tile/{z}/{y}/{x}`,
  { attribution: ESRI_ATTR, maxNativeZoom: 16, maxZoom: 19 });
function cartoOrEsri(L, style, svc, key) {
  key = (key || "").trim();
  if (!key) return esri(L, svc);
  const group = L.layerGroup();
  const carto = L.tileLayer(cartoUrl(style, key), { attribution: CARTO_ATTR, subdomains: "abcd", maxZoom: 20 });
  let errors = 0;
  carto.on("tileerror", () => {
    if (++errors !== 4) return;
    console.warn("CARTO nie wydaje kafelków (np. 403: klucz ograniczony do innych domen), przełączam podkład na Esri");
    group.removeLayer(carto);
    group.addLayer(esri(L, svc));
  });
  return group.addLayer(carto);
}
export const BASEMAPS = {
  dark: (L, key) => cartoOrEsri(L, "dark_nolabels", "World_Dark_Gray_Base", key),
  light: (L, key) => cartoOrEsri(L, "light_nolabels", "World_Light_Gray_Base", key),
  white: (L) => L.layerGroup(),
  osm: (L) => L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }),
};
export const LIGHT_BASEMAPS = ["light", "white", "osm"];

// --- VATSIM: wspólny cache dla wszystkich zakładek (data feed odświeża się co ~15 s, rezerwacje rzadziej)
const shared = {};
function cached(key, ttl, fn) {
  const hit = shared[key];
  if (hit && Date.now() - hit.t < ttl) return hit.p;
  const p = fn().catch((e) => { delete shared[key]; throw e; });
  shared[key] = { t: Date.now(), p };
  return p;
}
export const vatsimOnline = () => cached("online", 30000, () => api("/api/vatsim/online"));
export const vatsimAtc = () => cached("atc", 30000, () => api("/api/vatsim/atc"));
export const vatsimBookings = () => cached("bookings", 300000, () => api("/api/vatsim/bookings?prefix=EP"));
export const atcPositions = () => cached("positions", 3600000, () => api("/api/nav/positions"));
export const aerodromeNames = () => cached("adnames", 3600000, () => api("/api/aerodromes")
  .then((list) => Object.fromEntries(list.map((a) => [a.icao, a.name]))));

export const hhmm = (iso) => (iso ? new Date(iso).toISOString().slice(11, 16) + "Z" : "–");

// Opis do dymka po najechaniu na stanowisko: kto jest zalogowany / kto zarezerwował.
export function positionTip(on, books = []) {
  const lines = [];
  if (on) lines.push(`ONLINE: ${on.name || "?"} (CID ${on.cid ?? "?"})`, `${on.callsign} ${on.frequency}, od ${hhmm(on.logon_time)}`);
  books.forEach((b) => lines.push(`${b.active ? "BOOKING TERAZ" : "BOOKING"}: CID ${b.cid}${b.name ? " " + b.name : ""}`,
    `${b.callsign} ${hhmm(b.start)}–${hhmm(b.end)}${b.type && b.type !== "booking" ? " (" + b.type + ")" : ""}`));
  return lines.join("\n");
}

// LVP (services/lvp.py, progi z AIP w data/seed/lvp.json): stan do AERODROME, OVERVIEW i AWOS. cls: lvp / prep /
// cancel (odwołane, przez 30 min) / off (nie obowiązują) / na (AIP nie przewiduje LVP).
export function lvpState(lvp) {
  if (!lvp || lvp.applies === false) return { cls: "na", label: "LVP nie dotyczy" };
  if (lvp.state === "LVP") return { cls: "lvp", label: "LVP W MOCY" };
  if (lvp.state === "PREP") return { cls: "prep", label: "PRZYGOTOWANIE LVP" };
  if (lvp.state === "CANCELLED") return { cls: "cancel", label: `LVP ODWOŁANE ${lvp.since || ""}`.trim() };
  return { cls: "off", label: "LVP nie obowiązują" };
}

// dymek LVP: progi trzech faz (przygotowanie, wprowadzenie, odwołanie), zakres i źródło w AIP; długie uwagi łamane
// do ok. 60 znaków (dymek ma white-space: pre)
export function lvpTip(lvp) {
  if (!lvp) return "";
  const wrap = (t) => (t || "").split(" ").reduce((ls, w) => {
    if (ls.length && (ls[ls.length - 1] + " " + w).length <= 60) ls[ls.length - 1] += " " + w; else ls.push(w);
    return ls;
  }, []).join("\n");
  const src = lvp.source ? `Źródło: ${lvp.source}` : "";
  if (lvp.applies === false) return [lvp.reason || "AIP nie przewiduje LVP", src].filter(Boolean).map(wrap).join("\n");
  return [...(lvp.rules || []), lvp.scope === "DEP" ? "LVP tylko dla odlotów" : "", src, lvp.note || ""]
    .filter(Boolean).map(wrap).join("\n");
}

// ATIS z VATSIM (text_atis): każdy element tablicy w osobnej linijce, jak w VATSIM Radar, a nie jeden ciągły tekst.
// Element bywa też napisem z własnymi znakami nowej linii; puste linijki pomijamy. null (ATIS bez tekstu) → [].
export function splitAtis(lines) {
  if (lines === null || lines === undefined) return [];
  return (Array.isArray(lines) ? lines : [lines]).flatMap((l) => String(l ?? "").split(/\r?\n/)).map((l) => l.trim()).filter(Boolean);
}
// ATIS do HTML: każda linijka osobno (esc), dłuższa zawija się z wcięciem, żeby było widać początek następnej (.atl w map.css)
export const atisHtml = (lines) => splitAtis(lines).map((l) => `<div class="atl">${esc(l)}</div>`).join("");

// TAF: każda grupa zmian (BECMG, TEMPO, PROB, FM) w osobnej linii.
export function splitTaf(raw) {
  if (!raw) return [];
  const tokens = raw.replace(/\s+/g, " ").trim().split(" ");
  const lines = [[]];
  tokens.forEach((t, i) => {
    const prev = tokens[i - 1];
    const starts = /^(BECMG|TEMPO|FM\d{6}|PROB\d{2})$/.test(t) && !(t === "TEMPO" && /^PROB\d{2}$/.test(prev));
    if (starts && lines[lines.length - 1].length) lines.push([]);
    lines[lines.length - 1].push(t);
  });
  return lines.map((l) => l.join(" "));
}

// localStorage bywa niedostępny (tryb prywatny, zablokowane dane witryny): odczyt i zapis zawsze w try
export function lsGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
export function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* tryb prywatny */ }
}
