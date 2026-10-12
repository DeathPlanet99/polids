import { api, debounce, esc, h, lsGet, lsSet, remember, ONLINE_EVENT } from "../api.js";

// SYT OP: wszystkie NOTAM-y FIR EPWW obowiązujące teraz – sam FIR i główne lotniska, z serwera PL vACC
// (backend: GET /api/notam/fir/EPWW, services/notam.py: fir_notams). Odświeżanie co 10 min (cache serwera też 10 min).
// Adres: "#sytop" (wszystkie, domyślna kolejność), "#sytop/EPKK" (jedna lokalizacja), "#sytop/ALL/end" (kolejność).
// Wybór lokalizacji i kolejności to kroki do cofnięcia (← i Wstecz); filtr tekstowy działa na bieżąco, bez historii.

const FIR = "EPWW";
const REFRESH_MS = 10 * 60 * 1000;
const RETRY_MS = 2 * 60 * 1000;       // po błędzie (także części lokalizacji) próbujemy ponownie po 2 min
const SORTS = [["loc", "lokalizacja"], ["id", "numer"], ["start", "początek"], ["end", "koniec"]];
const SORT_TITLES = {
  loc: "Najpierw FIR EPWW, potem lotniska alfabetycznie; w każdej grupie po numerze",
  id: "Po numerze NOTAM (seria, rok, numer)",
  start: "Po początku ważności (pole B), od najstarszych",
  end: "Po końcu ważności (pole C), najpierw kończące się najwcześniej; PERM na końcu",
};

// --- okres ważności względem teraz: ten sam opis co lista NOTAM w AERODROME
const dm = (iso) => { const d = new Date(iso); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")} ${d.toISOString().slice(11, 16)}Z`; };
function span(ms) {
  const min = Math.round(Math.abs(ms) / 60000);
  if (min < 60) return `${min} min`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} h ${min % 60} min`;
  return `${Math.round(min / 1440)} dni`;
}
export function notamState(n, now = Date.now()) {
  const s = n.start ? Date.parse(n.start) : null, e = n.end ? Date.parse(n.end) : null;
  const endTxt = n.perm ? "PERM" : e ? `${dm(n.end)}${n.est ? " EST" : ""}` : "?";
  if (e && e < now && !n.perm) return { cls: "expired", txt: `WYGASŁ ${dm(n.end)}` };
  if (s && s > now) return { cls: "future", txt: `AKTYWNY OD ${dm(n.start)} (za ${span(s - now)}) do ${endTxt}` };
  if (n.perm) return { cls: "active", txt: `AKTYWNY od ${s ? dm(n.start) : "?"} · PERM` };
  if (e) return { cls: "active", txt: `AKTYWNY DO ${endTxt} (jeszcze ${span(e - now)})` };
  return { cls: "active", txt: s ? `AKTYWNY od ${dm(n.start)}` : "" };
}

// --- kolejność i filtr
// numer A1234/26 → "A26" + "1234": seria, rok, numer (A0999/25 przed A0001/26); bez numeru na końcu
const idKey = (id) => { const m = /^([A-Z])(\d{4})\/(\d{2})$/.exec(id || ""); return m ? m[1] + m[3] + m[2] : "~"; };
const byId = (a, b) => idKey(a.id).localeCompare(idKey(b.id));
export const SORT_FN = {
  loc: (a, b) => ((a.location !== FIR) - (b.location !== FIR)) || a.location.localeCompare(b.location) || byId(a, b),
  id: byId,
  start: (a, b) => (a.start || "").localeCompare(b.start || "") || byId(a, b),
  end: (a, b) => (a.perm - b.perm) || (a.end || "9").localeCompare(b.end || "9") || byId(a, b),
};
const hay = (n) => [n.id, ...(n.locations || []), n.qcode, n.subject_text, n.condition_text, n.schedule, n.text, n.raw]
  .filter(Boolean).join(" ").toUpperCase();
// filtr: każde słowo musi wystąpić (numer, lokalizacja, kod Q, treść); lokalizacja z listy wyboru osobno
export function filterNotams(list, { q = "", loc = "" } = {}) {
  const words = q.toUpperCase().split(/\s+/).filter(Boolean);
  return list.filter((n) => {
    if (loc && !(n.locations || [n.location]).includes(loc)) return false;
    const text = words.length ? hay(n) : "";
    return words.every((w) => text.includes(w));
  });
}

// adres zakładki: [lokalizacja | ALL]/[kolejność]; wartości domyślne pomijane
export function parseArg(arg) {
  const [l = "", s = ""] = String(arg || "").split("/");
  const loc = /^[A-Za-z]{4}$/.test(l) && l.toUpperCase() !== "ALL" ? l.toUpperCase() : "";
  return { loc, sort: SORT_FN[s] ? s : "loc" };
}
export const argOf = (loc, sort) => (sort === "loc" ? loc : `${loc || "ALL"}/${sort}`);

const hm = (iso) => (iso ? new Date(iso).toISOString().slice(11, 16) + "Z" : "–");
// żadna lokalizacja nie odpowiedziała (odpowiedź 200 bez danych)
const allDown = (d) => !!d && d.locations.length > 0 && d.failed.length === d.locations.length;
// "EPKK, EPGD (timeout); EPWR (HTTP 500)" – lokalizacje pogrupowane wg komunikatu błędu
function failedHtml(failed) {
  const by = {};
  failed.forEach((f) => { (by[f.error] ||= []).push(f.icao); });
  return Object.entries(by).map(([e, ls]) => `<b>${ls.map(esc).join(", ")}</b> (${esc(e)})`).join("; ");
}
const limits = (n) => (n.lower_limit || n.upper_limit ? `${n.lower_limit || "?"} – ${n.upper_limit || "?"}` : "");
const subjectText = (n) => [n.subject_text, n.condition_text].filter(Boolean).join(" ");

export default {
  mount(root) {
    const pane = h(`<div class="pane fill sytop-pane">
      <div class="sytop-head">
        <h2 class="title">SYT OP – aktywne NOTAM FIR EPWW</h2>
        <span class="hint sytop-src" title="Serwer NOTAM PL vACC (cv.plvacc.pl): FIR EPWW i główne lotniska">źródło: PL vACC · odświeżanie co 10 min</span>
        <span class="sytop-gap"></span>
        <span class="hint sytop-upd"></span>
        <button class="btn sytop-refresh" title="Pobierz listę ponownie">ODŚWIEŻ</button>
      </div>
      <div class="toolbar sytop-bar">
        <input class="sytop-search" type="search" placeholder="Szukaj: numer, lokalizacja, treść…" aria-label="Szukaj w NOTAM-ach" spellcheck="false">
        <button class="btn sytop-clear" title="Wyczyść filtr i pokaż wszystkie lokalizacje">WYCZYŚĆ</button>
        <label class="sytop-loc-pick">Lokalizacja <select class="sytop-locsel" aria-label="Lokalizacja"></select></label>
        <span class="sytop-sort">Sortuj: ${SORTS.map(([k, l]) => `<button class="btn" data-sort="${k}" title="${esc(SORT_TITLES[k])}">${esc(l)}</button>`).join("")}</span>
        <span class="sytop-count"></span>
      </div>
      <div class="sytop-msg" role="status"></div>
      <div class="sytop-list" tabindex="0"><div class="hint sytop-empty">Ładowanie NOTAM…</div></div>
    </div>`);
    root.append(pane);
    const $ = (s) => pane.querySelector(s);
    const list = $(".sytop-list"), search = $(".sytop-search"), sel = $(".sytop-locsel");

    let data = null, error = "", loading = false, seq = 0, nextAt = 0;
    let loc = "", sort = SORT_FN[lsGet("sytop.sort")] ? lsGet("sytop.sort") : "loc";
    const opened = new Set();   // klucze NOTAM-ów z rozwiniętym pełnym tekstem

    const visible = () => !!root.closest(".view.active");
    const key = (n) => n.id || n.raw;
    const nameOf = (code) => (data?.names || {})[code] || "";

    const drawOptions = () => {
      const all = data?.notams || [];
      // liczby NOTAM-ów przy lokalizacjach tylko z danymi (przed pobraniem albo po awarii "(0)" byłoby nieprawdą)
      const count = (c) => (data && !allDown(data) ? ` (${c ? all.filter((n) => (n.locations || [n.location]).includes(c)).length : all.length})` : "");
      const codes = [...new Set(all.flatMap((n) => n.locations || [n.location]))].filter((c) => c !== FIR).sort();
      if (loc && loc !== FIR && !codes.includes(loc)) codes.push(loc);
      sel.innerHTML = [["", `Wszystkie${count("")}`], [FIR, `${FIR} – FIR${count(FIR)}`],
        ...codes.map((c) => [c, `${c}${nameOf(c) ? " – " + nameOf(c) : ""}${count(c)}`])]
        .map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
      sel.value = loc;
    };

    const item = (n, odd, main) => {
      const s = notamState(n);
      const locs = (n.locations || [n.location]).map((c) => (main.has(c) && c !== FIR
        ? `<a class="sytop-loc" href="#aerodrome/${esc(c)}" title="${esc(nameOf(c) || c)} – otwórz AERODROME">${esc(c)}</a>`
        : `<span class="sytop-loc" title="${esc(nameOf(c) || c)}">${esc(c)}</span>`)).join(" ");
      const subj = subjectText(n), lim = limits(n), k = key(n), open = opened.has(k);
      return `<div class="notam ${s.cls} sytop-item${n.location === FIR ? " fir" : ""}${odd ? " odd" : ""}">
        <div class="sytop-side"><b class="sytop-id">${esc(n.id || "–")}</b><span class="sytop-locs">${locs}</span></div>
        <div class="sytop-main">
          <div class="nhead"><span class="valid">${esc(s.txt)}</span>
            ${n.schedule ? `<span class="hint">harmonogram: ${esc(n.schedule)}</span>` : ""}
            ${lim ? `<span class="hint" title="Granice pionowe (pola F i G)">${esc(lim)}</span>` : ""}
            ${n.qcode ? `<span class="sytop-subj" title="Kod Q ${esc(n.qcode)}${subj ? ": " + esc(subj) : ""}">${esc(n.qcode)}${subj ? " · " + esc(subj) : ""}</span>` : ""}
            ${n.text ? `<button class="sytop-rawbtn" data-key="${esc(k)}" aria-expanded="${open}" title="Cały NOTAM z polami Q) A) B) C) D) E) F) G)">${open ? "▾" : "▸"} pełny NOTAM</button>` : ""}
          </div>
          <pre class="sytop-text">${esc(n.text || n.raw)}</pre>
          ${n.text && open ? `<pre class="sytop-raw">${esc(n.raw)}</pre>` : ""}
        </div></div>`;
    };

    const drawList = () => {
      pane.querySelectorAll(".sytop-sort [data-sort]").forEach((b) => b.classList.toggle("primary", b.dataset.sort === sort));
      if (!data) {
        list.innerHTML = error ? `<div class="error sytop-empty">Nie udało się pobrać NOTAM-ów: ${esc(error)}</div>`
          : `<div class="hint sytop-empty">Ładowanie NOTAM…</div>`;
        $(".sytop-count").textContent = "";
        return;
      }
      const all = data.notams;
      const shown = filterNotams(all, { q: search.value, loc }).sort(SORT_FN[sort]);
      const down = allDown(data);
      $(".sytop-count").textContent = down ? "– NOTAM" : shown.length === all.length ? `${all.length} NOTAM` : `${shown.length} z ${all.length} NOTAM`;
      const scroll = list.scrollTop;
      if (!all.length) {
        list.innerHTML = down
          ? `<div class="error sytop-empty">Serwer NOTAM PL vACC nie odpowiada – brak danych z żadnej lokalizacji.
              <div class="sytop-why">${failedHtml(data.failed)}</div><div class="hint">Kolejna próba za 2 min albo przyciskiem ODŚWIEŻ.</div></div>`
          : `<div class="hint sytop-empty">Brak NOTAM-ów obowiązujących teraz w FIR EPWW${data.failed.length ? " (bez lokalizacji, które nie odpowiedziały)" : ""}.</div>`;
        return;
      }
      if (!shown.length) {
        list.innerHTML = `<div class="hint sytop-empty">Żaden NOTAM nie pasuje do filtra.</div>`;
        return;
      }
      let html = "", group = null, odd = false;
      const main = new Set(data.locations);   // główne lotniska: kod lokalizacji to odnośnik do AERODROME
      for (const n of shown) {
        if (sort === "loc" && n.location !== group) {
          if (group !== null) html += `</section>`;
          group = n.location;
          odd = false;
          const cnt = shown.filter((x) => x.location === group).length;
          html += `<section class="sytop-grp"><div class="sytop-group"><b>${esc(group)}</b>${nameOf(group) ? ` <span>${esc(nameOf(group))}</span>` : ""} <i>· ${cnt}</i></div>`;
        }
        html += item(n, odd, main);
        odd = !odd;
      }
      if (group !== null) html += `</section>`;
      list.innerHTML = html;
      list.scrollTop = scroll;
    };

    const drawStatus = () => {
      const upd = $(".sytop-upd"), msg = $(".sytop-msg");
      const down = allDown(data);
      upd.classList.toggle("stale", !!(data && (error || down)));
      upd.textContent = !data ? "" : down ? `próba ${hm(data.now)} · brak danych`
        : `aktualizacja ${hm(data.now)}${error ? " · odświeżenie nieudane" : ""}`;
      upd.title = data && error ? `Ostatnia próba odświeżenia: ${error}` : "Czas UTC ostatniego pobrania listy";
      const parts = [];
      if (data && error) parts.push(`Lista z ${hm(data.now)} – nie udało się jej odświeżyć: ${esc(error)}`);
      if (data?.failed.length && !down) parts.push(`Brak danych z: ${failedHtml(data.failed)}. Lista bez tych lokalizacji.`);
      msg.innerHTML = parts.map((p) => `<div>${p}</div>`).join("");
    };

    const draw = () => { drawOptions(); drawList(); drawStatus(); };

    const load = async () => {
      if (loading) return;
      loading = true;
      const my = ++seq;
      $(".sytop-refresh").disabled = true;
      try {
        const d = await api("/api/notam/fir/EPWW");
        if (my !== seq) return;
        // nic nie odpowiedziało, a mamy wcześniejszą listę: zostaje ona, z ostrzeżeniem (lepsze starsze dane niż nic)
        if (allDown(d) && data && !allDown(data)) error = d.error || "serwer NOTAM nie odpowiada";
        else { data = d; error = ""; }
        nextAt = Date.now() + (d.failed.length ? RETRY_MS : REFRESH_MS);
      } catch (e) {
        if (my !== seq) return;
        error = e.message;
        nextAt = Date.now() + RETRY_MS;
      } finally {
        loading = false;
        $(".sytop-refresh").disabled = false;
      }
      draw();
    };

    // zmiana stanu przez użytkownika: krok do cofnięcia
    const changed = () => { drawList(); remember("sytop", argOf(loc, sort)); };
    sel.addEventListener("change", () => { loc = sel.value; changed(); });
    pane.querySelector(".sytop-sort").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-sort]");
      if (!b || b.dataset.sort === sort) return;
      sort = b.dataset.sort;
      lsSet("sytop.sort", sort);
      changed();
    });
    search.addEventListener("input", debounce(drawList, 120));
    search.addEventListener("keydown", (e) => { if (e.key === "Escape" && search.value) { search.value = ""; drawList(); } });
    $(".sytop-clear").addEventListener("click", () => {
      search.value = "";
      if (loc) { loc = ""; sel.value = ""; changed(); } else drawList();
      search.focus();
    });
    $(".sytop-refresh").addEventListener("click", () => load());
    // "pełny NOTAM": rozwinięcie zostaje po odświeżeniu listy (zbiór kluczy), bez kroku w historii
    list.addEventListener("click", (e) => {
      const b = e.target.closest("button.sytop-rawbtn[data-key]");
      if (!b) return;
      const k = b.dataset.key;
      opened.has(k) ? opened.delete(k) : opened.add(k);
      drawList();
    });

    drawOptions();
    const timer = setInterval(() => { if (visible() && Date.now() >= nextAt) load(); }, 30000);
    const online = () => { if (error) load(); };
    window.addEventListener(ONLINE_EVENT, online);

    return {
      // arg undefined = przycisk menu: zostaje bieżący stan; napis = stan z adresu ("" = domyślny)
      activate(arg) {
        if (arg !== undefined) {
          ({ loc, sort } = parseArg(arg));
          drawOptions();
          drawList();
        }
        remember("sytop", argOf(loc, sort));
        if (Date.now() >= nextAt) load();
      },
      destroy() { clearInterval(timer); window.removeEventListener(ONLINE_EVENT, online); },
    };
  },
};
