import { api, debounce, esc, h, letterMenu } from "../api.js";

const CATEGORY = { CARGO: "CARGO", MIL: "MILITARY" };

export default {
  mount(root) {
    let prefix = "";
    const letters = letterMenu(root, (l) => { prefix = l; load(); });
    const pane = h(`<div class="pane">
      <div class="toolbar">
        <input type="search" class="q" placeholder="Szukaj: kod ICAO, callsign, operator, kraj…" size="46" autofocus>
        <select class="cat"><option value="">Wszystkie</option><option value="CARGO">tylko CARGO</option><option value="MIL">tylko MILITARY</option></select>
        <span class="hint count"></span>
      </div>
      <table class="data callsigns"><thead><tr><th>ICAO</th><th>Callsign</th><th>Operator</th><th>Kraj</th><th>Info</th></tr></thead><tbody></tbody></table>
    </div>`);
    root.append(pane);
    const q = pane.querySelector(".q");
    const body = pane.querySelector("tbody");
    let seq = 0;
    const load = async () => {
      const my = ++seq;
      try {
        const rows = await api(`/api/callsigns?q=${encodeURIComponent(q.value.trim())}&prefix=${prefix}&category=${pane.querySelector(".cat").value}&limit=1000`);
        if (my !== seq) return;
        pane.querySelector(".count").textContent = `${rows.length} wyników`;
        body.innerHTML = rows.map((r) => `<tr><td class="icao">${esc(r.icao)}</td><td class="tel">${esc(r.telephony || "–")}</td>
          <td class="minor">${esc(r.name)}</td><td class="minor">${esc(r.country)}</td>
          <td>${r.category ? `<span class="tag ${r.category.toLowerCase()}">${CATEGORY[r.category] || esc(r.category)}</span>` : ""}</td></tr>`).join("");
      } catch (e) {
        body.innerHTML = `<tr><td colspan="5" class="error">${esc(e.message)}</td></tr>`;
      }
    };
    q.addEventListener("input", debounce(load));
    pane.querySelector(".cat").addEventListener("change", load);
    load();
    // #callsign/A: litera z adresu (← i Wstecz), "" = wszystkie; przycisk menu (arg undefined) zostawia bieżącą
    return { activate: (arg) => {
      if (arg !== undefined && /^[A-Z]?$/.test(arg) && arg !== prefix) { prefix = arg; letters.set(arg); load(); }
      q.focus();
    } };
  },
};
