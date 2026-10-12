import { api, esc, frameGo, h, remember } from "../api.js";

// Dokumenty PDF zgromadzone w folderze data/docs/ (podfolder = kategoria). Tylko podgląd.
export default {
  mount(root, ctx) {
    const wrap = h(`<div class="pane fill"><div class="mapwrap">
      <div class="mapside">
        <div class="ms-card"><h4>Dokumenty</h4><div class="list doc-list"></div></div>
        <p class="hint">Nowe PDF-y wrzuć do folderu <span class="mono">data/docs/</span>
        (podfolder = kategoria) i kliknij „Odśwież”.</p>
        <button class="btn refresh">Odśwież</button>
      </div>
      <iframe class="embed viewer" style="background:#333"></iframe>
    </div></div>`);
    root.append(wrap);
    const list = wrap.querySelector(".list");
    let viewer = wrap.querySelector(".viewer");
    // wybrany dokument w adresie: #docs/<plik w data/docs> (← i Wstecz wracają do poprzedniego dokumentu)
    const PREFIX = "/files/docs/";
    const argOf = (url) => (url.startsWith(PREFIX) ? url.slice(PREFIX.length) : url);
    let docs = [], want = null;   // want: dokument z adresu, zanim lista się wczyta
    const mark = () => list.querySelectorAll("a[data-url]").forEach((a) => a.classList.toggle("on", a.dataset.url === viewer.getAttribute("src")));
    const pick = (arg) => {        // "" = pierwszy dokument
      const d = docs.find((x) => argOf(x.url) === arg) || (arg ? null : docs[0]);
      if (d && viewer.getAttribute("src") !== d.url) viewer = frameGo(viewer, d.url);
      mark();
    };

    const load = async () => {
      docs = await api("/api/docs");
      if (!docs.length) {
        list.innerHTML = `<p class="hint">Brak dokumentów w data/docs/.</p>`;
        return;
      }
      const groups = {};
      docs.forEach((d) => (groups[d.category] ||= []).push(d));
      list.innerHTML = Object.entries(groups).map(([cat, ds]) => `<div class="doc-cat">${esc(cat)}</div>` +
        ds.map((d) => `<a href="#" data-url="${esc(d.url)}">${esc(d.title)}</a>`).join("")).join("");
      if (want !== null) { pick(want); want = null; } else if (!viewer.getAttribute("src")) viewer.src = docs[0].url;
      mark();
    };
    list.addEventListener("click", (e) => {
      const a = e.target.closest("a[data-url]");
      if (!a) return;
      e.preventDefault();
      viewer = frameGo(viewer, a.dataset.url);   // bez kroku w historii ramki: ← cofa wybór dokumentu
      list.querySelectorAll("a.on").forEach((x) => x.classList.remove("on"));
      a.classList.add("on");
      remember("docs", argOf(a.dataset.url));
    });
    // ponowny import tylko dla admina, reszta po prostu przeładowuje listę
    wrap.querySelector(".refresh").addEventListener("click", async () => {
      if (ctx.me?.admin) await api("/api/import", { method: "POST" }).catch(() => {});
      load();
    });
    load();
    return { activate: (arg) => {
      if (arg === undefined) return;   // przycisk menu: zostaje otwarty dokument
      if (docs.length) pick(arg); else want = arg;
    } };
  },
};
