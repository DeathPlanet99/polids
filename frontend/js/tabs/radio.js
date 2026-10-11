import { aerodromeNames, esc, h, hhmm, lsGet, lsSet, positionTip, subtabs } from "../api.js";
import { geoView } from "../geo.js";
import { displayName, loadDisplayNames } from "../posname.js";
import { sectorSplit } from "../sectorsplit.js";
import { sliceChain, sliceOwner } from "../coverage.js";
import { loadVacs } from "../airspace.js";
import * as R from "../radiostate.js";

const TYPE_ORDER = ["CTR", "FSS", "APP", "DEP", "TWR", "GND", "DEL", "ATIS"];
// Lotniska komunikacyjne z AIP IFR, wojskowe; pozostałe EP** traktujemy jak VFR
const IFR = ["EPBY", "EPGD", "EPKK", "EPKT", "EPLB", "EPLL", "EPMO", "EPPO", "EPRA", "EPRZ", "EPSC", "EPSY", "EPWA", "EPWR", "EPZG"];
const MIL = ["EPCE", "EPDA", "EPDE", "EPIR", "EPKS", "EPLK", "EPLY", "EPMB", "EPMI", "EPMM", "EPOK", "EPPR", "EPPW", "EPSN", "EPTM"];
// Sąsiedzi: zakładkę stanowiska (nb_tab) wyznacza serwer (/api/radio/positions): katalog FIR-u z vacs-data, bez niego
// prefiks; EDUU (Rhein Radar / Karlsruhe UAC) i EDYY (Maastricht UAC) we własnej zakładce, choć w vacs-data są w EDWW i EDMM.
// loa = klucz w /api/radio/loa (null = brak LOA z EPWW)
const NEIGHBOURS = [
  { id: "edww", key: "EDWW", label: "EDWW", title: "Niemcy (Bremen, Berlin, Hamburg)", loa: "EDWW" },
  { id: "edmm", key: "EDMM", label: "EDMM", title: "Niemcy (München, Dresden, Leipzig, Nürnberg)", loa: "EDMM" },
  { id: "eduu", key: "EDUU", label: "EDUU/EDYY", title: "Niemcy, przestrzeń górna (Rhein Radar – Karlsruhe UAC, Maastricht UAC)",
    loa: "EDUU", loaHint: "EDYY: brak LOA" },
  { id: "cz", key: "LKAA", label: "LKAA", title: "Czechy (Praha)", loa: "LKAA" },
  { id: "sk", key: "LZBB", label: "LZBB", title: "Słowacja (Bratislava)", loa: "LZBB" },
  { id: "ua", key: "UKLV", label: "UKLV", title: "Ukraina (Lviv)", loa: "UKLV" },
  { id: "by", key: "UMMV", label: "UMMV", title: "Białoruś (Minsk)", loa: null },
  { id: "kal", key: "UMKK", label: "UMKK", title: "Rosja (Kaliningrad)", loa: null },
  { id: "lt", key: "EYVL", label: "EYVL", title: "Litwa (Vilnius)", loa: "EYVL" },
  { id: "se", key: "ESAA", label: "ESAA", title: "Szwecja", loa: "ESAA" },
  { id: "dk", key: "EKDK", label: "EKDK", title: "Dania", loa: null },
];
const tabOf = (p) => NEIGHBOURS.find((n) => n.key === p.nb_tab);
const isOther = (p) => !p.prefix?.startsWith("EP") && !tabOf(p);

const typeOf = (cs) => cs.split("_").pop();
// polskie częstotliwości FIS (Warszawa Information) przenosimy do EPWW ACC; informacje lotniskowe (EPBC, EPML)
// zostają przy lotniskach, a FIS sąsiadów (Sweden, Kaunas, Kaliningrad Information) w zakładkach ich FIR-ów
const isFis = (p) => p.callsign.startsWith("EP") && /information/i.test(p.name || "") && /_(APP|CTR)$/.test(p.callsign);
const byType = (a, b) => TYPE_ORDER.indexOf(typeOf(a.callsign)) - TYPE_ORDER.indexOf(typeOf(b.callsign)) || a.callsign.localeCompare(b.callsign);
const isAcc = (p) => ["CTR", "FSS"].includes(typeOf(p.callsign));

// Grupy wg prefiksu; z ctrFirst najpierw grupy ze stanowiskami ACC/CTR, potem lotniska (APP, TWR…); first = grupa
// na samą górę (FIR zakładki: EDMM przed EDGG)
function group(list, keyFn, { ctrFirst = false, first = null } = {}) {
  const g = {};
  list.forEach((p) => (g[keyFn(p)] ||= []).push(p));
  const rank = (k, ps) => (k === first ? -1 : ctrFirst && ps.some(isAcc) ? 0 : 1);
  return Object.entries(g).sort(([a, pa], [b, pb]) => rank(a, pa) - rank(b, pb) || a.localeCompare(b)).map(([k, ps]) => [k, ps.sort(byType)]);
}

const st = R.state;
// 1 stanowisko, 2–4 stanowiska (poza 12–14), 5+ stanowisk
const nStan = (n) => `${n} ${n === 1 ? "stanowisko" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "stanowiska" : "stanowisk"}`;
// stan sieci z ostatniego udanego pobrania; po błędzie "VATSIM: brak danych" i komunikat (bez starej godziny)
const netMsg = () => esc(R.netText((n) => `${nStan(n)} online`)) + (st.err ? ` <span class="error">${esc(st.err)}</span>` : "");

// VACS (vacs-data, profil ACC_EPWW): etykiety stanowisk sąsiadów z dopiskiem wysokości. Bez danych zostaje displayName.
const VACS_URL = "https://github.com/vacs-project/vacs-data";
let vacs = null;
let vacsIdx = null;
const loadVacsIndex = () => (vacsIdx ||= R.loadVacsData().then(indexVacs).catch(() => { vacsIdx = null; return null; }));

// {pos: {callsign .ese: {label, alt, keys}}}; klucze FMP/TMU pomijamy (to nie są stanowiska z .ese)
function indexVacs(d) {
  const by = {};
  (d.neighbours || []).filter((n) => !n.fmp && n.ese?.callsign).forEach((n) => (by[n.ese.callsign] ||= []).push(n));
  const pos = {};
  Object.entries(by).forEach(([cs, keys]) => {
    // wspólne stanowisko kilku kluczy: wspólny początek etykiety + reszty po "/" (LKAA N + LKAA S -> LKAA N/S)
    const words = keys.map((k) => k.label.split(" "));
    let i = 0;
    while (words.every((w) => i < w.length - 1 && w[i] === words[0][i])) i++;
    const tails = [...new Set(words.map((w) => w.slice(i).join(" ")))];
    pos[cs] = {
      label: [...words[0].slice(0, i), tails.join("/")].join(" "),
      alt: [...new Set(keys.map((k) => k.alt).filter(Boolean))].join("/"),
      keys: keys.map((k) => ({ label: k.label, alt: k.alt })),
    };
  });
  return { pos, commit: (d.commit || "").slice(0, 7), date: d.commit_date || "" };
}

// dopisek wysokości w formie VACS (+365, -195, 335-365) -> opis w dymku
const altTip = (a) => a.split("/").map((x) => x.replace(/^\+(\d+)$/, "powyżej FL$1").replace(/^-(\d+)$/, "do FL$1")
  .replace(/^(\d+)-(\d+)$/, "FL$1–FL$2")).join(" / ");

// Kto przejmuje wycinki stanowiska offline: dla wycinków, w których jest pierwsze w kolejności przejmowania (coverage.js:
// sektory ACC wg /api/nav/ownership, TMA/CTR: APP/TWR z pliku .ese, potem ACC), pierwsze zalogowane dalej w tej kolejności.
let sectors = null, own = null;
function cover(cs) {
  if (st.online[cs] || !sectors) return "";
  const onl = new Set(R.onlineList());
  const hits = new Map();
  sectors.features.forEach((f) => {
    if (sliceChain(f.properties, own).chain[0] !== cs) return;
    const o = sliceOwner(f.properties, onl, own);
    if (o && o !== cs) (hits.get(o) || hits.set(o, new Set()).get(o)).add(f.properties.group);
  });
  if (!hits.size) return "";
  return `<span class="inhs">${[...hits].map(([o, groups]) => {
    const on = st.online[o];
    const tip = `${displayName(cs)} offline – wg kolejności przejmowania wycinki (${[...groups].join(", ")}) przejmuje ${displayName(o)} (${o})`
      + `${on?.name ? `, ${on.name}` : ""}${on?.frequency ? `, ${on.frequency}` : ""}`;
    return `<span class="inh" title="${esc(tip)}"><b>→ ${esc(displayName(o))}</b> (online)</span>`;
  }).join("")}</span>`;
}

// kolumna ID: etykieta VACS + dopisek wysokości (stanowiska z profilu ACC_EPWW), poza tym displayName (etykieta
// kafelka GEO, np. EPWA_I_APP → WA C FIS, albo nazwa w stylu VACS)
function pidHtml(p) {
  const v = vacs?.pos[p.callsign];
  if (v) {
    const tip = [`VACS ACC_EPWW · ${p.callsign}${p.position_id ? ` · ID w pliku .ese: ${p.position_id}` : ""}`,
      ...v.keys.map((k) => `${k.label}${k.alt ? " " + k.alt : ""}`)].join("\n");
    return `<span class="pid vacs"><span title="${esc(tip)}">${esc(v.label)}</span>${v.alt ? `<b class="alt" title="${esc(altTip(v.alt))}">${esc(v.alt)}</b>` : ""}</span>`;
  }
  const src = p.position_id ? `ID w pliku .ese: ${p.position_id}` : p.nb_source ? `stanowisko z pliku .ese sąsiada (${p.nb_source})` : "stanowisko spoza pliku .ese (vacs-data)";
  return `<span class="pid" title="${esc(src)}">${esc(displayName(p.callsign))}</span>`;
}
const pidLen = (p) => (vacs?.pos[p.callsign] ? vacs.pos[p.callsign].label.length + 5 : displayName(p.callsign).length);

// linijka o klikaniu pozycji i o źródle etykiet
function listNote(list) {
  const parts = [`Kliknij pozycję, żeby włączyć ją w symulacji zasięgu (<a data-goto="geo">mapa w GEO</a>).`];
  if (vacs && list.some((p) => vacs.pos[p.callsign] || !p.in_ese)) {
    parts.push(`Etykiety i stanowiska spoza pliku .ese: <a href="${VACS_URL}" target="_blank" rel="noopener"
      title="vacs-project/vacs-data, commit ${esc(vacs.commit)} z ${esc(vacs.date)}">vacs-data</a> (CC BY-NC-SA 4.0).`);
  }
  return `<p class="hint pickhint">${parts.join(" ")}</p>`;
}

// kolumna nazwy: znak radiowy (sąsiedzi: baza wiedzy VATSIM Germany, LOA, plik .ese) i drobniej nazwa sektora; pełny opis
// i źródło w dymku. Polskie stanowiska: nazwa z pliku .ese jak dotąd.
const SRC = { kb: "baza wiedzy VATSIM Germany (Centersektoren)", ese: "plik .ese", loa: "LOA" };
function nameTip(p, radio) {
  const lines = [[radio, p.sector].filter(Boolean).join(" · ")];
  const loaT = p.loa?.titles?.join(", ") || "LOA";
  const src = (k) => (k === "loa" ? loaT : SRC[k]);
  if (p.radio_src === "prefix") lines.push(`znak radiowy przyjęty jak u pozostałych stanowisk ${p.prefix} ${p.facility}`);
  if (p.radio_src && p.radio_src === p.sector_src) lines.push(`znak radiowy i sektor: ${src(p.radio_src)}`);
  else {
    if (p.radio_src && p.radio_src !== "prefix") lines.push(`znak radiowy: ${src(p.radio_src)}`);
    if (p.sector_src) lines.push(`sektor: ${src(p.sector_src)}`);
  }
  if (p.name && p.name !== radio) lines.push(`w pliku .ese: ${p.name}`);
  if (p.kb?.limits) lines.push(`zakres: ${p.kb.limits}`);
  if (p.kb?.covers) lines.push(`obejmuje: ${p.kb.covers}`);
  if (p.kb?.uwaga) lines.push(`uwaga: ${p.kb.uwaga}`);
  (p.loa?.notes || []).forEach((n) => lines.push(`LOA: ${n}`));
  return lines.join("\n");
}
function nameHtml(p) {
  const radio = p.radio || p.name;
  if (!radio && !p.sector) return `<span class="nm fir" title="Stanowisko tylko w vacs-data (bez nazwy w pliku .ese)">${esc(p.fir_name || "")}</span>`;
  return `<span class="nm${p.radio_src === "prefix" ? " der" : ""}" title="${esc(nameTip(p, radio))}">${esc(radio || p.fir_name || "")}${
    p.sector ? `<small class="sec">${esc(p.sector)}</small>` : ""}</span>`;
}

// pid = false: wiersz bez kolumny ID (lotniska VFR)
function row(p, colors, active, { pid = true } = {}) {
  const on = p._on || st.online[p.callsign];
  const books = st.bookings[p.callsign] || [];
  const cls = on ? "on" : books.length ? "booked" : "";
  const tip = positionTip(on, books);
  const act = active.has(p.callsign);
  const who = on ? `<b class="who">${esc(on.name || on.callsign)}</b>` : books.length ? `<span class="booked-txt">booking ${hhmm(books[0].start)}–${hhmm(books[0].end)}</span>` : "";
  return `<div class="radio-row pick ${cls}${act ? " act" : ""}" data-cs="${esc(p.callsign)}" ${act ? `style="--pc:${colors[p.callsign]}"` : ""}
    ${tip ? `data-tip="${esc(tip)}"` : ""}><span class="act-mark"></span><span class="freq ${cls}">${esc(p.frequency)}</span>
    ${pid ? pidHtml(p) : ""}<span class="cs">${esc(p.callsign)}</span>${nameHtml(p)}${who}${cover(p.callsign)}</div>`;
}

let names = {};
function groupsHtml(groups, empty = "Brak stanowisk.", opts = {}) {
  const active = R.activeSet(), colors = R.colorMap(active);
  // sąsiedzi: znaczek koloru FIR-u (jak kafelki GEO) przed nazwą grupy
  const sw = (ps) => {
    const k = R.firKey(ps[0]?.callsign, ps[0]?.fir);
    return k ? `<i class="fir-sw" style="background:${R.FIR_COLORS[k].c}" title="${esc(R.FIR_COLORS[k].name)}"></i>` : "";
  };
  return groups.length ? groups.map(([name, ps]) => `<div class="radio-group" data-group="${esc(name)}"><h3>${sw(ps)}${esc(name)}${names[name] ? ` <small>${esc(names[name])}</small>` : ""}</h3>${ps.map((p) => row(p, colors, active, opts)).join("")}</div>`).join("")
    : `<p class="hint">${esc(empty)}</p>`;
}

// Widok listy, odświeżany razem ze stanem sieci i symulacją. wide = szersza kolumna ID na etykiety VACS z dopiskiem wysokości.
// nb = zakładka sąsiada: panel LOA budowany raz i wstawiany w miejsce .loa-slot przy każdym odświeżeniu listy
// (zostaje rozwinięcie, filtr i przewinięcie tabel).
function listView(build, { wide = false, goto, nb } = {}) {
  return (pane) => {
    pane.innerHTML = `<div class="netstatus hint"></div><div class="body radio-list single"><p class="hint">Ładowanie…</p></div>`;
    let ps = null, err = "", alive = true, loaEl = null;
    const draw = () => {
      // po przejściu do innej podzakładki panel należy już do niej (spóźnione dane nie mogą go nadpisać)
      if (!alive) return;
      pane.querySelector(".netstatus").innerHTML = netMsg();
      const body = pane.querySelector(".body");
      if (!ps) { body.innerHTML = err ? `<p class="error">${esc(err)}</p>` : `<p class="hint">Ładowanie…</p>`; return; }
      body.classList.toggle("vacs", wide && !!vacs);
      const top = pane.scrollTop;
      const loaTop = loaEl?.querySelector(".loa-b")?.scrollTop || 0;
      // fokus w panelu LOA (filtr, pasek): przeniesienie panelu go zdejmuje, więc wraca na to samo pole i miejsce kursora
      const foc = loaEl?.contains(document.activeElement) ? document.activeElement : null;
      const sel = foc?.matches("input") ? [foc.selectionStart, foc.selectionEnd] : null;
      body.innerHTML = build(ps);
      const slot = body.querySelector(".loa-slot");
      if (slot && loaEl) {
        slot.replaceWith(loaEl);
        const lb = loaEl.querySelector(".loa-b");
        if (lb) lb.scrollTop = loaTop;
        if (foc) {
          foc.focus({ preventScroll: true });
          if (sel) foc.setSelectionRange(...sel);
        }
      }
      const byCs = Object.fromEntries(ps.map((p) => [p.callsign, p]));
      // szerokość kolumny ID tylko z wierszy, które ją mają (lotniska VFR są bez ID)
      const lens = [...body.querySelectorAll(".radio-row[data-cs] .pid")].map((el) => el.closest(".radio-row").dataset.cs)
        .map((cs) => pidLen(byCs[cs] || { callsign: cs }));
      body.style.setProperty("--pid-ch", Math.max(6, ...lens));
      pane.scrollTop = top;
    };
    Promise.all([R.loadPositions(), aerodromeNames().catch(() => ({})), loadVacsIndex(),
      R.loadSectors().catch(() => null), loadVacs().catch(() => null), loadDisplayNames(),
      nb?.loa ? R.loadLoa().catch((e) => ({ error: e.message })) : null])
      .then(([p, n, v, s, o, , l]) => {
        ps = p; names = n; vacs = v; sectors = s; own = o;
        // uszkodzony wpis w loa.json: komunikat w pasku LOA, lista stanowisk zostaje
        if (nb) {
          try { loaEl = loaPanel(nb, l, ps); } catch (e) { loaEl = loaPanel(nb, { error: `LOA: błędny wpis ${nb.loa} w data/seed/loa.json (${e.message})` }, ps); }
        }
        draw();
      })
      .catch((e) => { err = e.message; draw(); });
    // panel jest wspólny dla podzakładek: obsługę kliknięć zdejmujemy w destroy, inaczej kolejna lista
    // dostałaby drugą (kliknięcie wiersza przełączałoby pozycję dwa razy, czyli wcale)
    const onClick = (e) => {
      // szybki skok do lotniska albo grupy stanowisk z paska przycisków (LOTNISKA, sąsiedzi, INNE)
      const b = e.target.closest("[data-jump]");
      if (b) {
        const g = pane.querySelector(`.radio-group[data-group="${CSS.escape(b.dataset.jump)}"]`);
        // przewijamy tak, żeby nagłówek lotniska wypadł tuż pod przyklejonym paskiem przycisków
        if (g) pane.scrollBy({ top: g.getBoundingClientRect().top - pane.getBoundingClientRect().top - b.closest(".aero-jump").offsetHeight - 6, behavior: "smooth" });
        return;
      }
      const go = e.target.closest("[data-goto]");
      if (go) { goto?.(go.dataset.goto); return; }
      const r = e.target.closest(".radio-row[data-cs]");
      if (r && !e.target.closest("a")) R.toggle(r.dataset.cs);
    };
    pane.addEventListener("click", onClick);
    const unsub = R.on(draw);
    const unwatch = R.watch();
    return { destroy: () => { alive = false; pane.removeEventListener("click", onClick); unsub(); unwatch(); } };
  };
}

// Pasek przycisków na górze listy: kliknięcie przewija do grupy (lotniska, FIR-u); zielony = ktoś jest online,
// pomarańczowy = rezerwacja. kinds: [{short, g: [[nazwa grupy, stanowiska]], tip(nazwa, stanowiska)}]
const grpState = (ps) => (ps.some((p) => st.online[p.callsign]) ? "on" : ps.some((p) => st.bookings[p.callsign]?.length) ? "booked" : "");
// bez żadnej grupy (pusta lista) bez paska
const jumpBar = (kinds) => (kinds.some((k) => k.g.length) ? `<div class="aero-jump">${kinds.filter((k) => k.g.length).map((k) => `<span class="aj-kind">${k.short}</span>${k.g.map(([key, ps]) =>
  `<button data-jump="${esc(key)}" class="${grpState(ps)}" title="${esc(k.tip(key, ps))}">${esc(key)}</button>`).join("")}`).join("")}</div>` : "");
// sąsiedzi i INNE: najpierw grupy ze stanowiskami ACC/CTR (jak kolejność listy), potem lotniska
const nbJump = (groups) => {
  const fir = (k, ps) => ps.find((p) => p.fir_name)?.fir_name || R.FIR_COLORS[R.firKey(ps[0]?.callsign, ps[0]?.fir)]?.name || "";
  return jumpBar([{ short: "ACC", g: groups.filter(([, ps]) => ps.some(isAcc)), tip: (k, ps) => [k, fir(k, ps)].filter(Boolean).join(" · ") },
    { short: "AD", g: groups.filter(([, ps]) => !ps.some(isAcc)), tip: (k) => names[k] || k }]);
};

function aerodromes(ps, note) {
  const ad = ps.filter((p) => p.prefix?.startsWith("EP") && p.prefix !== "EPWW" && !isFis(p));
  // lotniska VFR bez kolumny ID (same częstotliwość, znak, nazwa i kto jest online)
  const kinds = [["Lotniska komunikacyjne (AIP IFR)", "IFR", (x) => IFR.includes(x)], ["Lotniska VFR", "VFR", (x) => !IFR.includes(x) && !MIL.includes(x), { pid: false }],
    ["Lotniska wojskowe", "MIL", (x) => MIL.includes(x)]].map(([title, short, filter, opts = {}]) =>
    ({ title, short, opts, g: group(ad.filter((p) => filter(p.prefix)), (p) => p.prefix), tip: (icao) => names[icao] || "" }));
  return jumpBar(kinds) + note + kinds.map((k) => (k.g.length ? `<h2 class="radio-section">${k.title}</h2>${groupsHtml(k.g, undefined, k.opts)}` : "")).join("");
}

// --- LOA EPWW z sąsiadem (data/seed/loa.json): jedna linijka (tytuł, wersja, liczba przekazań, PDF), po kliknięciu
// gęste tabele przekazań OUT/IN, zasady (silent transfer, VFR, inne) i sektory wg LOA. Rozwinięcie zapamiętane
// w przeglądarce osobno dla każdego FIR-u; domyślnie zwinięte.
const LS_LOA = "radio.loa.";
// tylko ścieżki do plików PolIDS (/files/…): spacje, przecinki i nawiasy w nazwach PDF-ów przez encodeURI
const pdfHref = (pdf) => (/^\/files\//.test(pdf || "") ? encodeURI(pdf) : "");

function loaTransfers(rows, from, to) {
  if (!rows.length) return "";
  // kolumny sektorów tylko, gdy LOA je podaje (LKAA, LZBB: bez sektorów)
  const sec = rows.some((t) => t.from || t.to);
  const head = `<tr><th>Ruch</th><th>COP</th><th>Poziom</th>${sec ? "<th>Z sektora</th><th>Do sektora</th>" : ""}<th>Warunki</th></tr>`;
  // filtr po treści wiersza bez pola dir ("out"/"in" pasowałoby do każdego przekazania w tę stronę)
  const body = rows.map((t) => `<tr data-f="${esc(Object.entries(t).filter(([k]) => k !== "dir").map(([, v]) => v).join(" ").toLowerCase())}"><td class="trf">${esc(t.traffic)}</td>
    <td class="cop">${esc(t.cop)}</td><td class="lvl${t.level?.length > 18 ? " wrap" : ""}">${esc(t.level)}</td>${sec ? `<td>${esc(t.from)}</td><td>${esc(t.to)}</td>` : ""}
    <td class="cond">${esc(t.conditions)}</td></tr>`).join("");
  return `<div class="loa-sec"><h4>${esc(from)} → ${esc(to)} <small>${rows.length}</small></h4>
    <table class="loa-tab${sec ? "" : " nosec"}"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}
const arr = (x) => (Array.isArray(x) ? x : []);
const loaList = (title, items) => (arr(items).length ? `<div class="loa-sec"><h4>${esc(title)} <small>${items.length}</small></h4>
  <ul>${items.map((x) => `<li data-f="${esc(String(x).toLowerCase())}">${esc(x)}</li>`).join("")}</ul></div>` : "");

function loaPanel(nb, d, ps) {
  const none = (html) => h(`<div class="loa none"><b class="loa-tag">LOA</b>${html}</div>`);
  if (!nb.loa) return none(`<span class="hint">brak LOA EPWW z tym FIR-em w PolIDS (wszystkie LOA: DOCS › LOA)</span>`);
  if (d?.error) return none(`<span class="error">${esc(d.error)}</span>`);
  const f = d?.firs?.[nb.loa];
  if (!f) return none(`<span class="hint">brak wpisu ${esc(nb.loa)} w data/seed/loa.json</span>`);
  const tr = arr(f.transfers);
  const out = tr.filter((t) => t.dir === "out"), inn = tr.filter((t) => t.dir === "in");
  const href = pdfHref(f.pdf);
  // stanowiska z LOA: znak radiowy z listy RADIO, gdy LOA go nie podaje (Niemcy: baza wiedzy VATSIM Germany)
  const byCs = Object.fromEntries((ps || []).map((p) => [p.callsign, p]));
  const pos = arr(f.positions).map((p) => `<tr data-f="${esc(Object.values(p).join(" ").toLowerCase())}">
    <td class="${p.side === "EP" ? "ep" : "nb"}">${p.side === "EP" ? "EPWW" : esc(nb.loa)}</td><td class="pcs">${esc(p.callsign)}</td>
    <td>${esc(p.radio || byCs[p.callsign]?.radio || "")}</td><td class="cop">${esc(p.sector)}</td><td class="lvl">${esc(p.frequency)}</td>
    <td class="cond">${esc(p.note)}</td></tr>`).join("");
  const el = h(`<section class="loa">
    <div class="loa-h" role="button" tabindex="0" aria-expanded="false" title="Rozwiń / zwiń wyciąg z LOA">
      <span class="loa-tw">▸</span><b class="loa-tag">LOA</b><span class="loa-t">${esc(f.title)}</span>
      <span class="loa-v">${esc(f.version)}</span>
      <span class="loa-n">przekazania: ${out.length} z EPWW · ${inn.length} do EPWW</span>
      ${nb.loaHint ? `<span class="loa-v">${esc(nb.loaHint)}</span>` : ""}
      ${href ? `<a class="loa-pdf" href="${esc(href)}" target="_blank" rel="noopener" title="${esc(f.pdf)}">PDF ↗</a>` : ""}
    </div>
    <div class="loa-b">
      <div class="loa-tools"><input type="search" class="loa-q" placeholder="filtr: COP, lotnisko, FL, sektor…" aria-label="Filtr LOA">
        <span class="loa-cnt hint"></span><span class="hint">Wyciąg z PDF (data/seed/loa.json), w razie wątpliwości obowiązuje PDF.</span></div>
      ${loaTransfers(out, "EPWW", nb.loa)}${loaTransfers(inn, nb.loa, "EPWW")}
      <div class="loa-rules">${loaList("Przekazanie kontroli i łączności", f.silent)}${loaList("VFR", f.vfr)}${loaList("Inne zasady", f.other)}</div>
      ${pos ? `<div class="loa-sec"><h4>Stanowiska i sektory wg LOA <small>${arr(f.positions).length}</small></h4>
        <table class="loa-tab pos"><thead><tr><th>Strona</th><th>Stanowisko</th><th>Znak radiowy</th><th>Sektor</th><th>Częst.</th><th>Uwagi</th></tr></thead>
        <tbody>${pos}</tbody></table></div>` : ""}
    </div></section>`);
  const hd = el.querySelector(".loa-h");
  const toggle = (open = !el.classList.contains("open"), save = true) => {
    el.classList.toggle("open", open);
    hd.setAttribute("aria-expanded", String(open));
    if (save) lsSet(LS_LOA + nb.loa, open ? "1" : "0");
  };
  toggle(lsGet(LS_LOA + nb.loa) === "1", false);
  hd.addEventListener("click", (e) => { if (!e.target.closest("a")) toggle(); });
  // Enter / spacja tylko na samym pasku: na linku PDF Enter ma otworzyć PDF, nie zwijać panelu
  hd.addEventListener("keydown", (e) => { if (e.target === hd && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(); } });
  // filtr: wiersze tabel i zasady zawierające wpisany tekst; sekcje bez trafień znikają
  const q = el.querySelector(".loa-q"), cnt = el.querySelector(".loa-cnt");
  q.addEventListener("input", () => {
    const t = q.value.trim().toLowerCase();
    const items = [...el.querySelectorAll("[data-f]")];
    items.forEach((x) => { x.hidden = !!t && !x.dataset.f.includes(t); });
    el.querySelectorAll(".loa-sec").forEach((sec) => { sec.hidden = !!t && !sec.querySelector("[data-f]:not([hidden])"); });
    cnt.textContent = t ? `${items.filter((x) => !x.hidden).length} z ${items.length}` : "";
  });
  return el;
}

export default {
  mount(root, ctx) {
    let geo = null, split = null;
    const goto = (id) => tabs.show(id);
    const tabs = subtabs(root, [
      { id: "geo", label: "GEO", fill: true, render: (p) => {
        geo ||= geoView(ctx);
        p.append(geo.el);
        geo.show();
        return { destroy: () => geo.hide() };
      } },
      { id: "sektoryzacja", label: "SEKTORYZACJA", fill: true, render: (p) => {
        split ||= sectorSplit(ctx);
        p.append(split.el);
        split.show();
        return { destroy: () => split.hide() };
      } },
      { id: "acc", label: "EPWW ACC", render: listView((ps) => {
        const acc = ps.filter((p) => p.prefix === "EPWW" && !isFis(p)).sort(byType);
        const fis = ps.filter(isFis).sort((a, b) => a.callsign.localeCompare(b.callsign));
        return listNote([]) + groupsHtml([["EPWW ACC · Warszawa Radar", acc]]) + groupsHtml([["FIS · Warszawa Information", fis]]);
      }, { goto }) },
      { id: "ad", label: "LOTNISKA", render: listView((ps) => aerodromes(ps, listNote([])), { goto }) },
      { sep: true },
      ...NEIGHBOURS.map((n) => ({ id: n.id, label: n.label, render: listView((ps) => {
        const list = ps.filter((p) => tabOf(p) === n);
        const g = group(list, (p) => p.prefix, { ctrFirst: true, first: n.key });
        return nbJump(g) + `<h2 class="radio-section">${esc(n.title)}</h2><div class="loa-slot"></div>` + listNote(list) + groupsHtml(g);
      }, { wide: true, goto, nb: n }) })),
      { id: "other", label: "INNE", render: listView((ps) => {
        const list = ps.filter((p) => isOther(p));
        const g = group(list, (p) => p.prefix, { ctrFirst: true });
        return nbJump(g) + `<h2 class="radio-section">Pozostałe (UIR, Eurocontrol)</h2>` + listNote(list) + groupsHtml(g);
      }, { wide: true, goto }) },
      { sep: true },
      { id: "online", label: "ONLINE", render: listView((ps) => {
        const al = R.aliased();  // ten sam kontroler pod dwoma znakami: zostaje wiersz ze znakiem z pliku sąsiada
        const list = ps.filter((p) => !al.has(p.callsign) && (st.online[p.callsign] || st.bookings[p.callsign]));
        const known = new Set(list.map((p) => st.online[p.callsign]?.callsign));
        // zalogowani spoza pliku .ese i vacs-data (np. nowe stanowiska) też są na liście
        const extra = st.controllers.filter((c) => !known.has(c.callsign))
          .map((c) => ({ callsign: c.callsign, name: "spoza .ese i vacs-data", frequency: c.frequency, prefix: c.callsign.split("_")[0], _on: c }));
        return listNote(list) + groupsHtml(group([...list, ...extra], (p) => p.prefix, { ctrFirst: true }),
          st.ok ? "Nikt z EPWW ani sąsiadów nie jest teraz online i nie ma rezerwacji na dziś." : "Brak danych z sieci VATSIM.");
      }, { wide: true, goto }) },
    ]);
    return {};
  },
};
