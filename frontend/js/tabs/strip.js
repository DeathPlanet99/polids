import { api, esc, fmt, lsGet, lsSet, remember, subtabs } from "../api.js";
import { mountStrips } from "../strips.js";

// STRIP: paski postępu lotu (EFES) przeniesione z AERODROME › RUCH, z podzakładką dla każdego z 15 głównych lotnisk.
// Adres "#strip/EPWA"; bez lotniska w adresie ostatnio oglądane (lsGet) albo EPWA. Kliknięcie lotniska = krok do cofnięcia.
export const STRIP_AIRPORTS = ["EPBY", "EPGD", "EPKK", "EPKT", "EPLB", "EPLL", "EPMO", "EPPO", "EPRA", "EPRZ", "EPSC", "EPSY", "EPWA", "EPWR", "EPZG"];
const DEFAULT = "EPWA";
const valid = (i) => STRIP_AIRPORTS.includes(i);

export default {
  mount(root) {
    let booting = true, strips = null;
    let names = {};
    api("/api/aerodromes").then((ads) => { names = Object.fromEntries(ads.map((a) => [a.icao, a.name])); }).catch(() => {});
    const render = (icao) => (pane) => {
      if (booting) return null;   // pierwsze lotnisko wybiera activate() (adres albo ostatnio oglądane), bez zbędnego zapytania
      lsSet("strip.icao", icao);
      pane.classList.add("strip-pane");
      pane.innerHTML = `<div class="toolbar strip-head"><span class="title">${esc(icao)}${names[icao] ? " · " + esc(names[icao]) : ""}</span>
        <span style="flex:1"></span><span class="hint upd"></span></div><div class="strip-body"></div>`;
      const head = pane.querySelector(".title"), upd = pane.querySelector(".upd");
      strips = mountStrips(pane.querySelector(".strip-body"), icao, { onUpdate: (d) => {
        head.textContent = `${d.icao} · ${d.name}  (elev ${fmt(d.elevation_ft, 0)} ft)`;
        upd.textContent = `Aktualizacja ${d.now.slice(0, 2)}:${d.now.slice(2)}Z · co 30 s`;
      } });
      const mine = strips;
      return { destroy() { mine.destroy(); if (strips === mine) strips = null; } };
    };
    const sub = subtabs(root, STRIP_AIRPORTS.map((i) => ({ id: i, label: i, render: render(i), fill: true })), { arg: (id) => id });
    booting = false;
    let shown = null;
    const show = (icao) => {
      if (shown === icao && strips) return strips.resume();
      shown = icao;
      sub.show(icao);
    };
    sub.nav.addEventListener("click", (e) => { const b = e.target.closest("button[data-id]"); if (b) shown = b.dataset.id; });
    return {
      // arg undefined (przycisk menu): zostaje bieżące lotnisko; "" = ostatnio oglądane albo EPWA; inaczej ICAO z adresu
      activate(arg) {
        const want = String(arg ?? "").trim().toUpperCase();
        const last = valid(lsGet("strip.icao")) ? lsGet("strip.icao") : DEFAULT;
        const icao = valid(want) ? want : arg === undefined && shown ? shown : last;
        show(icao);
        remember("strip", icao);
      },
    };
  },
};
