import { api, debounce, esc, fmt, h, letterMenu, remember } from "../api.js";

const RECAT = {
  A: "Super Heavy", B: "Upper Heavy", C: "Lower Heavy", D: "Upper Medium", E: "Lower Medium", F: "Light",
};
// Przyciski producentów u góry: [klucz ?maker= w backendzie (tam dopasowanie różnych zapisów nazwy), etykieta,
// główna nazwa producenta, której grupa idzie na początek listy]
const MAKERS = [["airbus", "AIRBUS", "airbus"], ["boeing", "BOEING", "boeing"], ["embraer", "EMBRAER", "embraer"],
  ["mcdonnell", "MCDONNELL", "mcdonnell douglas"], ["atr", "ATR", "atr"], ["cessna", "CESSNA", "cessna"]];
const types = (n) => `${n} ${n === 1 ? "typ" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "typy" : "typów"}`;
const norm = (s) => (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

// Widok producenta: grupy wg nazwy producenta w bazie (np. Airbus, Airbus Helicopters), główna nazwa pierwsza,
// dalej od największej grupy; w grupie kolejność z serwera (wg kodu ICAO)
function makerGroups(list, primary) {
  const by = new Map();
  list.forEach((a) => {
    const k = a.manufacturer || "–";
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(a);
  });
  return [...by.entries()].sort(([a, x], [b, y]) =>
    (norm(b) === primary) - (norm(a) === primary) || y.length - x.length || a.localeCompare(b));
}
const searchName = (a) => `${a.manufacturer || ""} ${a.model || a.icao}`.trim();
// EUROCONTROL Aircraft Performance Database: strona typu (details.aspx?ICAO=…) obok strony głównej z config
let PERF = "https://learningzone.eurocontrol.int/ilp/customs/ATCPFDB/default.aspx";
const perfUrl = (icao) => PERF.replace(/default\.aspx.*$/i, `details.aspx?ICAO=${encodeURIComponent(icao)}`);

// Rekord w dwóch liniach: kod ICAO duży po lewej, reszta jednolitą czcionką
function record(a, i) {
  return `<div class="ac-rec" data-i="${i}">
    <div class="ac-icao">${esc(a.icao)}</div>
    <div class="ac-l1">${esc(a.manufacturer || "")} ${esc(a.model)}
      <a class="perf" href="${esc(perfUrl(a.icao))}" target="_blank" rel="noopener" title="EUROCONTROL Aircraft Performance Database">osiągi ↗</a></div>
    <div class="ac-l2">WTC <b>${esc(a.wtc || "–")}</b> · RECAT-EU <b class="recat-${esc(a.recat)}">${esc(a.recat || "–")}</b>
      · rozp. ${fmt(a.wingspan, 1, " m")} · dł. ${fmt(a.length, 1, " m")} · wys. ${fmt(a.height, 1, " m")} · MTOW ${a.mtow ? Math.round(a.mtow / 100) / 10 + " t" : "–"}</div>
  </div>`;
}

export default {
  mount(root, ctx) {
    PERF = ctx.config.links?.performance_db || PERF;
    let prefix = "";
    let maker = ""; // aktywny przycisk producenta; litera albo ponowny klik wraca do zwykłego widoku
    const letters = letterMenu(root, (l) => { prefix = l; setMaker(""); load(); });
    const pane = h(`<div class="pane ac-pane">
      <nav class="ac-makers">${MAKERS.map(([k, l]) => `<button class="gbtn" data-m="${k}" title="Wszystkie typy producenta ${l}">${l}</button>`).join("")}</nav>
      <div class="split">
      <div class="list">
        <div class="toolbar">
          <input type="search" class="q" placeholder="Typ ICAO, model, producent…" size="32">
          <select class="wtc"><option value="">WTC: wszystkie</option><option>L</option><option>M</option><option>H</option><option>J</option></select>
          <select class="recat"><option value="">RECAT-EU: wszystkie</option>${Object.entries(RECAT).map(([k, v]) => `<option value="${k}">${k} – ${v}</option>`).join("")}</select>
          <span class="hint count"></span>
        </div>
        <div class="ac-list"></div>
      </div>
      <div class="detail"><div class="card"><p class="hint">Wybierz typ z listy, żeby zobaczyć szczegóły i zdjęcie.</p></div></div>
    </div></div>`);
    root.append(pane);
    const $ = (s) => pane.querySelector(s);
    let rows = [];
    let seq = 0;

    // przyciski producentów: aktywny podświetlony, w podmenu liter wtedy żadna litera nie jest zaznaczona
    const setMaker = (m) => {
      maker = m;
      pane.querySelectorAll(".ac-makers button").forEach((b) => b.classList.toggle("active", b.dataset.m === m));
      if (m) root.querySelectorAll(".submenu.letters button.active").forEach((b) => b.classList.remove("active"));
    };
    const load = async () => {
      const qs = new URLSearchParams({ q: $(".q").value.trim(), prefix: maker ? "" : prefix, maker, wtc: $(".wtc").value,
        recat: $(".recat").value, limit: 500 });
      const my = ++seq;
      let data;
      try {
        data = await api(`/api/aircraft?${qs}`);
      } catch (e) {
        if (my !== seq) return;
        $(".count").textContent = "";
        $(".ac-list").innerHTML = `<p class="error">Nie udało się pobrać typów: ${esc(e.message)}</p>`;
        return;
      }
      if (my !== seq) return; // starsza odpowiedź przyszła po nowszej
      const [, label, primary] = MAKERS.find(([k]) => k === maker) || [];
      const groups = label ? makerGroups(data, primary) : [];
      rows = label ? groups.flatMap(([, list]) => list) : data;
      $(".count").textContent = `${label ? label + ": " : ""}${types(rows.length)}${groups.length > 1 ? ` w ${groups.length} grupach` : ""}`
        + (rows.length === 500 ? " (pokazano pierwsze 500)" : "");
      let i = 0;
      $(".ac-list").innerHTML = !rows.length ? `<p class="hint ac-empty">Brak typów${label ? ` producenta ${label}` : ""} dla tych filtrów.</p>`
        : !label ? rows.map(record).join("")
          : groups.map(([name, list]) => `<div class="ac-group"><div class="ac-gh">${esc(name)}<span>${types(list.length)}</span></div>
            ${list.map((a) => record(a, i++)).join("")}</div>`).join("");
    };

    const show = (a) => {
      $(".detail").innerHTML = `<div class="card">
        <h3 class="ac-title"><span class="ac-icao" style="font-size:26px">${esc(a.icao)}</span> ${esc(a.manufacturer)} ${esc(a.model)}</h3>
        <div class="perf-links"><a class="btn" href="${esc(perfUrl(a.icao))}" target="_blank" rel="noopener">EUROCONTROL Aircraft Performance: ${esc(a.icao)} ↗</a>
          <a class="hint" href="${esc(PERF)}" target="_blank" rel="noopener">wyszukiwarka bazy ↗</a></div>
        <div class="photo-box"><span class="hint">Ładowanie zdjęcia…</span></div>
        <dl class="props">
          <dt>WTC (ICAO)</dt><dd>${esc(a.wtc || "–")}</dd>
          <dt>RECAT-EU</dt><dd class="recat-${esc(a.recat)}">${esc(a.recat || "–")} ${a.recat ? "– " + RECAT[a.recat] : ""}</dd>
          <dt>Rozpiętość</dt><dd>${fmt(a.wingspan, 2, " m")}</dd>
          <dt>Długość</dt><dd>${fmt(a.length, 2, " m")}</dd>
          <dt>Wysokość</dt><dd>${fmt(a.height, 2, " m")}</dd>
          <dt>MTOW / MLW</dt><dd>${fmt(a.mtow, 0, " kg")} / ${fmt(a.mlw, 0, " kg")}</dd>
          <dt>Silniki</dt><dd>${esc(a.engine_count ?? "–")} × ${esc(a.engine_type || "–")}</dd>
          <dt>Pułap</dt><dd>${fmt(a.ceiling_ft, 0, " ft")}</dd>
          <dt>Vmo / Mmo</dt><dd>${fmt(a.vmo_kt, 0, " kt")} / ${fmt(a.mmo, 2)}</dd>
          <dt>IATA</dt><dd>${esc(a.iata || "–")}</dd>
        </dl></div>`;
      const box = $(".photo-box");
      const links = `<a href="https://www.jetphotos.com/photo/keyword/${encodeURIComponent(searchName(a))}" target="_blank" rel="noopener">JetPhotos ↗</a> ·
        <a href="https://www.google.com/search?tbm=isch&q=${encodeURIComponent(searchName(a))}" target="_blank" rel="noopener">Google Grafika ↗</a>`;
      api(`/api/aircraft/${a.id}/photo`).then((p) => {
        if (current !== a) return;
        box.innerHTML = `<img src="${esc(p.url)}" alt="${esc(searchName(a))}"><div class="hint">${p.page ? `<a href="${esc(p.page)}" target="_blank" rel="noopener">${esc(p.credit)}</a>` : esc(p.credit)} · ${links}</div>`;
      }).catch((e) => {
        if (current !== a) return;
        box.innerHTML = `<p class="hint">${esc(e.message)}. Własne zdjęcie wrzuć do <span class="mono">data/photos/${esc(a.icao)}.jpg</span>. ${links}</p>`;
      });
    };
    let current = null;

    $(".ac-list").addEventListener("click", (e) => {
      const rec = e.target.closest(".ac-rec[data-i]");
      if (!rec) return;
      pane.querySelectorAll(".ac-rec.selected").forEach((x) => x.classList.remove("selected"));
      rec.classList.add("selected");
      current = rows[rec.dataset.i];
      show(current);
    });
    pane.querySelector(".ac-makers").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      // ponowny klik: zwykły widok (wszystkie); nowy producent: wszystkie jego typy, więc bez tekstu wyszukiwania
      if (b.dataset.m === maker) { prefix = ""; setMaker(""); letters.set(""); } else { $(".q").value = ""; setMaker(b.dataset.m); }
      $(".list").scrollTop = 0;
      load();
      remember("aircraft", maker ? "maker/" + maker : "");   // krok do cofnięcia (←), jak litery
    });
    $(".q").addEventListener("input", debounce(load));
    $(".wtc").addEventListener("change", load);
    $(".recat").addEventListener("change", load);
    load();
    // arg z adresu (← i Wstecz): litera "A"–"Z", "" = wszystkie, "maker/airbus" = producent, inny tekst = szukany typ
    // (np. #aircraft/B738); przycisk menu (arg undefined) zostawia bieżący widok
    return { activate: (arg) => {
      const mk = arg?.startsWith("maker/") ? arg.slice(6) : null;
      if (arg === undefined) { /* bez zmian */ } else if (/^[A-Z]?$/.test(arg)) {
        if (arg !== prefix || maker) { prefix = arg; setMaker(""); letters.set(arg); load(); }
      } else if (mk && MAKERS.some(([k]) => k === mk)) {
        if (mk !== maker) { $(".q").value = ""; setMaker(mk); load(); }
      } else { $(".q").value = arg; prefix = ""; setMaker(""); letters.set(""); load(); }
      $(".q").focus();
    } };
  },
};
