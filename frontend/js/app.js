import { api, clearNavPath, esc, hashOf, navPath, parseHash, presetNavPath, selectNavPath, setRouter } from "./api.js";
import home from "./tabs/home.js";
import meteo from "./tabs/meteo.js";
import { adciv, admil, advfr } from "./tabs/aip.js";
import inop from "./tabs/inop.js";
import about from "./tabs/about.js";
import radio from "./tabs/radio.js";
import emergency from "./tabs/emergency.js";
import aircraft from "./tabs/aircraft.js";
import callsign from "./tabs/callsign.js";
import aerodrome from "./tabs/aerodrome.js";
import map from "./tabs/map.js";
import phraseology from "./tabs/phraseology.js";
import checklist from "./tabs/checklist.js";
import sytop from "./tabs/sytop.js";
import aup from "./tabs/aup.js";
import strip from "./tabs/strip.js";
import other from "./tabs/other.js";
import admin from "./tabs/admin.js";

// Leaflet po polsku, raz dla wszystkich map (RADIO, METEO › QNH, MAP itd.), zanim którakolwiek się zbuduje:
// dymki przycisków powiększenia i odnośnika "Leaflet" w atrybucji (sama treść atrybucji bez zmian)
if (window.L) {
  L.Control.Zoom.mergeOptions({ zoomInTitle: "Przybliż", zoomOutTitle: "Oddal" });
  const prefix = L.Control.Attribution.prototype.options.prefix;
  if (typeof prefix === "string") {
    L.Control.Attribution.mergeOptions({ prefix: prefix.replace(/title="[^"]*"/, 'title="Leaflet: biblioteka JavaScript do map interaktywnych"') });
  }
}

const TABS = { home, about, radio, meteo, aerodrome, adciv, admil, advfr, inop, callsign, aircraft, map, phraseology, checklist,
  sytop, aup, strip, other, emergency, admin };
// stare adresy zakładek przeniesionych do podmenu: #docs/<plik> → OTHER › DOCS (#other/docs/<plik>);
// bez arg (link #docs, navigate("docs")) OTHER zostaje w swoim stanie, z otwartym dokumentem
const MOVED = { docs: (arg) => ["other", arg === undefined ? undefined : arg ? "docs/" + arg : "docs"] };
const views = document.getElementById("views");
const back = document.getElementById("nav-back");
const mounted = {};
const ctx = { config: null, open: (tab, arg) => open(tab, arg, true) };

// --- Historia ekranów (przycisk ← i Wstecz przeglądarki). Każdy wpis historii tej sesji ma stan {n: głębokość};
// n = 0 to pierwszy ekran po uruchomieniu (HOME), więc ← nigdy nie wychodzi z aplikacji (np. do strony logowania).
let depth = 0;           // n bieżącego wpisu
let current = null;      // zakładka na ekranie
let shown = "";          // location.hash ekranu na ekranie
let activating = null;   // {tab, arg?} w trakcie mount()/activate(): remember() tej zakładki uzupełnia adres
const lastArg = {};      // ostatni arg każdej zakładki (powrót przyciskiem menu = ten sam stan, ten sam adres)
const sameHash = (a, b) => { const x = parseHash(a), y = parseHash(b); return x.tab === y.tab && x.arg === y.arg; };

function write(tab, arg, push) {
  const hash = hashOf(tab, arg);
  lastArg[tab] = arg;
  const same = sameHash(hash, location.hash);
  if (push && !same) history.pushState({ n: ++depth }, "", hash);
  // ten sam ekran: dane przypięte do wpisu (stash) zostają; inny ekran w tym wpisie: bez nich
  else history.replaceState({ ...(same && history.state?.s ? { s: history.state.s } : {}), n: depth }, "", hash);
  shown = location.hash;
  back.disabled = depth <= 0;
}

// arg: undefined = przycisk menu (zakładka zostaje w swoim stanie), napis = stan z adresu ("" = domyślny).
// Zakładka z activate(arg) sama czyta arg; bez activate() arg to ścieżka podzakładek (subtabs w api.js).
function open(tab, arg, push = false) {
  if (Object.hasOwn(MOVED, tab)) [tab, arg] = MOVED[tab](arg);   // adres zapisuje już nowa zakładka (bez martwego ekranu)
  if (!TABS[tab]) { tab = "home"; arg = undefined; }
  document.querySelectorAll("#tabs button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  views.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  current = tab;
  const me = { tab };
  activating = me;
  try {
    if (!mounted[tab]) {
      const el = document.createElement("section");
      el.className = "view active";   // widoczna już w mount(): podzakładka z adresu (np. mapa QNH) liczy wymiary
      el.dataset.tab = tab;
      views.append(el);
      mounted[tab] = { el, api: {} };
      presetNavPath(el, arg);   // podzakładka z adresu od pierwszego rysowania (bez zbędnego wczytania pierwszej)
      mounted[tab].api = TABS[tab].mount(el, ctx) || {};
      if (mounted[tab].api.activate) clearNavPath(el);
    }
    const m = mounted[tab];
    m.el.classList.add("active");
    if (m.api.activate) m.api.activate(arg);
    else if (arg !== undefined) selectNavPath(m.el, arg);
  } catch (e) {
    console.error(e);
  } finally {
    if (activating === me) activating = null;
  }
  if (current !== tab) return;   // activate() przeniósł od razu do innej zakładki: adres zapisało tamto open()
  const m = mounted[tab];
  const a = !m.api.activate ? navPath(m.el) || (arg ?? lastArg[tab]) : me.arg ?? arg ?? lastArg[tab];
  write(tab, a || "", push);
}

function remember(tab, arg, { replace = false } = {}) {
  arg = arg === null || arg === undefined ? "" : String(arg);
  if (activating) {
    if (activating.tab === tab) activating.arg = arg;
    return;
  }
  if (tab === current) write(tab, arg, !replace);
}
// dane ekranu w jego wpisie historii (history.state.s[zakładka]); zapis tylko dla ekranu na wierzchu, poza activate()
let restoring = false;   // open() z ←/Wstecz/Dalej: activate() może odczytać dane wpisu, do którego wraca
function stash(tab, data) {
  if (activating || tab !== current) return;
  const st = history.state || { n: depth };
  history.replaceState({ ...st, s: { ...(st.s || {}), [tab]: data } }, "");
}
const stashed = (tab) => (restoring && activating?.tab === tab ? history.state?.s?.[tab] : undefined);
setRouter({ open: (tab, arg) => open(tab, arg, true), remember, stash, stashed });

// Wstecz / Dalej przeglądarki, ← w menu, link <a href="#..."> i location.hash = "..." (nowy wpis bez stanu)
function fromLocation() {
  const n = history.state?.n;
  if (typeof n === "number") depth = n;
  else if (!sameHash(location.hash, shown)) history.replaceState({ n: ++depth }, "", location.href);
  else history.replaceState({ n: depth }, "", location.href);   // ten sam adres: przeglądarka podmieniła wpis
  if (sameHash(location.hash, shown)) { back.disabled = depth <= 0; return; }
  const { tab, arg } = parseHash(location.hash);
  restoring = true;
  try { open(tab, arg); } finally { restoring = false; }
}

let ready = false;   // menu działa po wczytaniu konfiguracji i otwarciu HOME
document.getElementById("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b && ready) open(b.dataset.tab, undefined, true);
});
back.addEventListener("click", () => { if (history.state?.n > 0) history.back(); });
// linki wewnątrz aplikacji (logo, odnośniki w treści) przez navigate: krok do cofnięcia i bez przewijania strony;
// sam "#" (martwy link) nie zmienia adresu
document.addEventListener("click", (e) => {
  const a = e.target.closest?.('a[href^="#"]');
  if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || a.target) return;
  const href = a.getAttribute("href");
  if (href === "#") return e.preventDefault();
  const { tab, arg } = parseHash(href);
  if (!TABS[tab] && !Object.hasOwn(MOVED, tab)) return;
  e.preventDefault();
  if (ready) open(tab, href.includes("/") ? arg : undefined, true);
});

function tick() {
  const d = new Date();
  document.getElementById("utc").textContent = d.toISOString().slice(11, 19) + "Z";
}
setInterval(tick, 1000);
tick();

(async () => {
  try {
    ctx.config = await api("/api/config");
    document.getElementById("airac").textContent = `AIRAC ${ctx.config.airac.ident}`;
    document.getElementById("airac").title = `od ${ctx.config.airac.effective}, następny ${ctx.config.airac.next}`;
  } catch (e) {
    ctx.config = { links: {}, default_aerodrome: "EPWA", airac: {} };
    console.error(e);
  }
  await showUser();
  // po uruchomieniu zawsze HOME ("?"), niezależnie od adresu zapamiętanego w przeglądarce; to pierwszy wpis historii (n = 0)
  depth = 0;
  open("home");
  ready = true;
  window.addEventListener("popstate", fromLocation);
  window.addEventListener("hashchange", fromLocation);
})();

// zalogowany użytkownik pod zegarem
async function showUser() {
  const me = await api("/auth/me").catch(() => null);
  ctx.me = me;
  document.querySelector('#tabs button[data-tab="admin"]').hidden = !me?.admin;
  if (!me?.user) return;
  const u = me.user;
  const box = document.getElementById("userbox");
  box.innerHTML = `<div class="who" title="CID ${esc(u.cid)}${u.subdivision ? " · " + esc(u.subdivision) : ""}">${esc(u.name || "CID " + u.cid)}</div>
    <div class="hint">${esc(u.cid)} · ${esc(u.rating_short || "")}${me.mode === "dev" ? ' · <span class="dev">DEV</span>' : ""}</div>
    <form method="post" action="/auth/logout"><button type="submit">Wyloguj</button></form>`;
  box.hidden = false;
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("Service worker:", e));
}
