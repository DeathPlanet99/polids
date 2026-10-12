import { api, esc, h, splitTaf, subtabs, lsGet, lsSet, ONLINE_EVENT } from "../api.js";

// listy domyślne alfabetycznie (wyniki i tak są sortowane po ICAO)
const PL_CIV = "EPBY EPGD EPKK EPKT EPLB EPLL EPMO EPPO EPRA EPRZ EPSC EPSY EPWA EPWR EPZG";
const PL_MIL = "EPCE EPDA EPDE EPIR EPKS EPLK EPLY EPMB EPMI EPMM EPOK EPPR EPPW EPSN EPTM";
const INTL = "EDDB EDDF EDDM EKCH ESSA EVRA EYVI LKPR LOWW LZIB UKLL";

// Kolorowanie METAR/TAF tak jak w PANDORZE
export function colorize(raw) {
  if (!raw) return `<span class="nil">NIL</span>`;
  let rmk = false, station = false;
  return esc(raw).split(/\s+/).map((t) => {
    if (!station && /^[A-Z]{4}$/.test(t) && !["METAR", "TAF", "AUTO"].includes(t)) { station = true; return `<span class="st">${t}</span>`; }
    if (["METAR", "SPECI", "TAF", "AMD", "COR"].includes(t)) return `<span class="tm">${t}</span>`;
    if (t === "RMK") rmk = true;
    if (rmk) return `<span class="rmk">${t}</span>`;
    if (["NOSIG", "TEMPO", "BECMG", "PROB30", "PROB40"].includes(t) || /^(FM|TL|AT)\d{4}/.test(t)) return `<span class="trend">${t}</span>`;
    if (/^\d{6}Z$/.test(t) || /^\d{4}\/\d{4}$/.test(t)) return `<span class="tm">${t}</span>`;
    if (/^(\d{3}|VRB)\d{2,3}(G\d{2,3})?(KT|MPS)$/.test(t) || /^\d{3}V\d{3}$/.test(t)) return `<span class="wind">${t}</span>`;
    if (t === "CAVOK") return `<span class="cavok">${t}</span>`;
    if (/^(FEW|SCT|BKN|OVC|VV)/.test(t) || ["NSC", "NCD", "SKC"].includes(t)) return `<span class="cld">${t}</span>`;
    if (/^[QA]\d{4}$/.test(t)) return `<span class="q">${t}</span>`;
    if (t === "NIL") return `<span class="nil">${t}</span>`;
    if (/^(\+|-|VC)?[A-Z]{2,8}$/.test(t) && !["METAR", "TAF", "AUTO", "COR", "AMD", "SPECI"].includes(t)) return `<span class="wxph">${t}</span>`;
    return t;
  }).join(" ");
}

// TAF: każdy okres zmian w nowej linii, wcięty pod pierwszą linią
export function wxLines(kind, raw) {
  const lines = kind === "taf" ? splitTaf(raw) : [raw];
  return lines.map((l, i) => (i ? `<span class="cont">${colorize(l)}</span>` : colorize(l))).join("<br>");
}

// lista stacji w polu: wielkie litery, bez powtórzeń, alfabetycznie (także lista zapamiętana w starszej wersji)
const normIds = (s) => [...new Set(String(s || "").toUpperCase().split(/[\s,;]+/).filter(Boolean))].sort().join(" ");

function wxList(kind, defaults, key) {
  return (pane) => {
    pane.append(h(`<div>
      <div class="toolbar">
        <input type="text" class="ids" size="80" value="${esc(normIds(lsGet(key) || defaults))}">
        <button class="btn primary go">Pokaż</button>
        <label class="sc"><input type="checkbox" class="auto" checked> odświeżaj co 2 min</label>
        <span class="hint upd"></span>
      </div>
      <div class="wx"></div>
    </div>`));
    const $ = (s) => pane.querySelector(s);
    const load = async () => {
      const ids = normIds($(".ids").value);
      $(".ids").value = ids;
      lsSet(key, ids);
      try {
        // METAR i TAF alfabetycznie po ICAO (odpowiedź też sortujemy, gdyby backend zmienił kolejność)
        const rows = (await api(`/api/meteo/${kind}?ids=${encodeURIComponent(ids.split(" ").join(","))}`)).sort((a, b) => a.icao.localeCompare(b.icao));
        $(".wx").innerHTML = rows.map((r) => `<div class="row">${r.raw ? wxLines(kind, r.raw) : `<span class="st">${esc(r.icao)}</span> <span class="nil">NIL</span>`}</div>`).join("");
        $(".upd").textContent = "Aktualizacja " + new Date().toISOString().slice(11, 16) + "Z";
      } catch (e) { $(".wx").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    };
    $(".go").addEventListener("click", load);
    $(".ids").addEventListener("keydown", (e) => e.key === "Enter" && load());
    const timer = setInterval(() => $(".auto")?.checked && load(), 120000);
    // serwer wrócił (okno run.bat uruchomione ponownie): komunikat błędu od razu zastępują dane
    const online = () => $(".wx .error") && load();
    window.addEventListener(ONLINE_EVENT, online);
    load();
    return { destroy: () => { clearInterval(timer); window.removeEventListener(ONLINE_EVENT, online); } };
  };
}

// Mapa QNH regionalnego jak mapa rejonów QNH z AIP Polska (PAŻP) z ramkami QNH jak w vAWOS ("MAPA QNH REGIONALNEGO"):
// jasny podkład bez kafelków w odwzorowaniu stożkowym (południki zbiegają się ku północy jak na mapie AIP), siatka co 1°
// z kreskami co 10′ i opisami stopni, granica FIR EPWW, rejony 1–14 (zielone granice i numery), pasy awaryjne 15–17
// (pomarańczowe linie 53°N i 51°N), TMA/MTMA (liliowe) z czarnymi opisami "BELOW TMA QNH FROM EPxx" i szarymi
// oznaczeniami przestrzeni (TMA EPGD, MCTR EPOK…) jak na mapie AIP, lotniska (romby, kody ICAO) oraz granatowe ramki:
// TMA/MTMA (nazwa i QNH lotniska) i "SEKTOR n" (QNH rejonu). Wszystkie napisy w miejscach [lon, lat] z pliku rejonów.
// Obok tabela rejonów (hPa, mmHg, inHg) i tabela TMA.
const QR_LAYERS = [["tma", "TMA/MTMA"], ["ad", "lotniska"], ["cap", "opisy BELOW"], ["box", "ramki QNH"],
  ["des", "oznaczenia TMA/CTR"]];
// zoom, dla którego dobrano miejsca ramek z JSON-a (mapa 960x900 przy oknie 1600x900, cały FIR): przy mniejszym napisy
// maleją razem z mapą (--qs w CSS); poniżej QR_FAR (już jedno "−" od widoku 1600x900 albo jeden ząbek kółka od
// domyślnego widoku 1366x768 z zoomem 7,2) bez kodów lotnisk, opisów BELOW i szarych oznaczeń, a ramki mniejsze
const QR_ZOOM = 7.5, QR_FAR = 7.15;

// odwzorowanie stożkowe wiernokątne Lamberta (kula) jak na mapie AIP: południk środkowy 19°E, stała stożka 0,798
// (styczne na ok. 53°N, wartość dopasowana do siatki mapy PAŻP); skala jak w EPSG:3857 (256 px na obwód przy zoomie 0).
// Tworzone dopiero przy otwarciu mapy: moduł importują też awos.js i overview.js, a w testach Node nie ma Leafleta (L).
let qrCrs = null;
const lccCrs = () => qrCrs || (qrCrs = (() => {
  const R = 6378137, d = Math.PI / 180, n = 0.798, l0 = 19 * d;
  const tn = (lat) => Math.tan(Math.PI / 4 + lat / 2) ** n;
  const F = Math.cos(Math.asin(n)) * tn(Math.asin(n)) / n, r0 = R * F / tn(52 * d);
  const projection = {
    project: (ll) => {
      const r = R * F / tn(ll.lat * d), t = n * (ll.lng * d - l0);
      return L.point(r * Math.sin(t), r0 - r * Math.cos(t));
    },
    unproject: (p) => {
      const y = r0 - p.y, r = Math.hypot(p.x, y);
      return L.latLng((2 * Math.atan((R * F / r) ** (1 / n)) - Math.PI / 2) / d, (l0 + Math.atan2(p.x, y) / n) / d);
    },
    bounds: L.bounds([-2e7, -2e7], [2e7, 2e7]),
  };
  const s = 0.5 / (Math.PI * R);
  return L.extend({}, L.CRS.Earth, { code: "PolIDS:LCC", projection, transformation: new L.Transformation(s, 0.5, -s, 0.5) });
})());

function qnhMap(pane) {
  const wrap = h(`<div class="qrwrap"><div class="qrmapbox"><div class="map qrmap"></div><div class="qr-grat"></div></div>
    <div class="qrside">
      <div class="qr-head"><b>QNH regionalne</b><span class="qr-stamp"></span></div>
      <div class="qr-tools">${QR_LAYERS.map(([k, l]) => `<label><input type="checkbox" data-k="${k}" checked> ${l}</label>`).join("")}</div>
      <div class="qr-err"></div>
      <div class="qr-card"><h4>Rejony QNH <span>najniższe QNH z lotnisk rejonu</span></h4><table class="data qr-reg"></table></div>
      <div class="qr-card"><h4>TMA / MTMA <span>pod TMA QNH lotniska</span></h4><table class="data qr-tma"></table></div>
      <p class="qr-note"></p>
    </div></div>`);
  pane.append(wrap);
  const $ = (s) => wrap.querySelector(s);
  // minZoom: dalej FIR to kilka pikseli, a napisy (nie mniejsze niż 86%, ramki 70%) zlewają się w jeden stos
  const map = L.map($(".qrmap"), { crs: lccCrs(), minZoom: 5.5, zoomSnap: 0.1, zoomDelta: 0.5, wheelPxPerZoomLevel: 120,
    attributionControl: false });
  // kolejność warstw jak na mapie AIP: siatka, TMA, pasy 15–17, granice rejonów, FIR, numery, szare oznaczenia,
  // lotniska, opisy BELOW, na wierzchu ramki SEKTOR n i TMA
  [["qgrid", 330], ["qtma", 340], ["qband", 345], ["qreg", 350], ["qfir", 360], ["qnum", 560], ["qdes", 580], ["qad", 600],
    ["qcap", 620], ["qlbl", 640]].forEach(([n, z]) => { map.createPane(n).style.zIndex = z; });
  const grid = L.layerGroup().addTo(map);   // siatka nie zależy od danych, draw() jej nie czyści
  const layers = Object.fromEntries(["base", "tma", "ad", "cap", "des", "box", "hl"].map((k) => [k, L.layerGroup().addTo(map)]));
  const mmhg = (q) => Math.round(q * 0.750062);
  const inhg = (q) => (q * 0.0295300).toFixed(2);
  const q4 = (q) => (q ? String(q) : "----");
  // napis wyśrodkowany w punkcie [lon, lat] z pliku; przy zoomie mniejszym od domyślnego maleje razem z mapą
  // (--qs w CSS), żeby układ dobrany dla zoomu domyślnego nie nachodził na siebie
  const div = (cls, html, attr = "") => L.divIcon({ className: "qr-icon", iconSize: null,
    html: `<div class="qr-pos ${cls}"${attr}>${html}</div>` });
  const mark = ([lon, lat], icon, pane, layer) => L.marker([lat, lon], { icon, pane, interactive: false, keyboard: false }).addTo(layer);

  // siatka co 1° (południki proste, równoleżniki łukami) z kreskami co 10′ jak na mapie AIP
  const GL = { pane: "qgrid", className: "qr-gl", interactive: false };
  const steps = (a, b, st) => Array.from({ length: Math.round((b - a) / st) + 1 }, (_, i) => a + i * st);
  for (let lon = 12; lon <= 26; lon++) L.polyline([[47, lon], [57, lon]], GL).addTo(grid);
  for (let lat = 47; lat <= 57; lat++) L.polyline(steps(11, 27, 0.25).map((lon) => [lat, lon]), GL).addTo(grid);
  const ticks = [];
  for (let lon = 12; lon <= 26; lon++) for (let m = 47 * 6 + 1; m < 57 * 6; m++) if (m % 6) ticks.push([[m / 6, lon - 0.035], [m / 6, lon + 0.035]]);
  for (let lat = 47; lat <= 57; lat++) for (let m = 11 * 6 + 1; m < 27 * 6; m++) if (m % 6) ticks.push([[lat - 0.022, m / 6], [lat + 0.022, m / 6]]);
  L.polyline(ticks, { ...GL, className: "qr-gl tick" }).addTo(grid);
  // opisy stopni: długość przy górnej krawędzi, szerokość przy lewej i prawej (tam, gdzie linia siatki przecina krawędź)
  const grat = () => {
    if (!map._loaded) return;
    const sz = map.getSize(), pt = (lat, lon) => map.latLngToContainerPoint([lat, lon]);
    let out = "";
    // przy bardzo małym zoomie (południki gęściej niż szerokość opisu) bez opisów długości, żeby nie zlewały się w jeden napis
    for (let lon = 12; lon <= 26 && pt(57, 20).x - pt(57, 19).x >= 28; lon++) {
      // po oddaleniu, gdy siatka nie sięga górnej krawędzi: opis nad końcem południka (57°N), a nie przy krawędzi
      const a = pt(50, lon), b = pt(57, lon), y = Math.max(8, b.y - 8), x = a.x + (b.x - a.x) * (y - a.y) / (b.y - a.y);
      if (x > 30 && x < sz.x - 30) out += `<span style="left:${Math.round(x)}px;top:${Math.round(y - 6)}px">${lon}°</span>`;
    }
    for (let lat = 47; lat <= 57; lat++) {
      const pts = steps(10, 28, 0.1).map((lon) => pt(lat, lon));
      [[3, "left"], [sz.x - 3, "right"]].forEach(([ex, side]) => {
        const i = pts.findIndex((p, k) => k && (pts[k - 1].x - ex) * (p.x - ex) <= 0);
        if (i < 0) return;
        const a = pts[i - 1], b = pts[i], y = a.y + (b.y - a.y) * (ex - a.x) / (b.x - a.x);
        if (y > 22 && y < sz.y - 10) out += `<span class="lat" style="top:${Math.round(y)}px;${side}:2px">${lat}°</span>`;
      });
    }
    $(".qr-grat").innerHTML = out;
  };
  map.on("move zoom resize", grat);
  map.on("zoomend", () => {
    const z = map.getZoom();
    wrap.style.setProperty("--qs", Math.min(1, 2 ** (z - QR_ZOOM)).toFixed(3));
    wrap.classList.toggle("qr-far", z < QR_FAR);
  });

  const show = () => wrap.querySelectorAll(".qr-tools input").forEach((i) => {
    const l = layers[i.dataset.k];
    if (i.checked && !map.hasLayer(l)) l.addTo(map);
    if (!i.checked && map.hasLayer(l)) map.removeLayer(l);
  });
  wrap.querySelector(".qr-tools").addEventListener("change", show);

  let data = null;
  const draw = () => {
    Object.values(layers).forEach((l) => l.clearLayers());
    // TMA jak na mapie AIP: liliowe wypełnienie obszarów "BELOW ... QNH FROM", na nim cienkie linie warstw TMA/CTR
    // z pliku sektorowego, na wierzchu pogrubiony ciemny obrys obszarów (także granica między sąsiednimi obszarami);
    // każdy wielokąt osobno, bo w jednym MultiPolygonie nakładające się warstwy wycinałyby dziury
    const polys = (k) => data.tmas.filter((t) => t[k]).flatMap((t) => t[k].coordinates.map((c) => ({ type: "Polygon", coordinates: c })));
    [["area", "qr-tfill", { stroke: false, fillOpacity: 1 }], ["layers", "qr-tin", { fill: false }], ["area", "qr-tedge", { fill: false }]]
      .forEach(([k, className, o]) => L.geoJSON(polys(k), { pane: "qtma", interactive: false, style: { className, ...o } }).addTo(layers.tma));
    data.tmas.filter((t) => t.outline).forEach((t) => L.geoJSON(t.outline, { pane: "qtma", interactive: false, style: { className: "qr-tline" } }).addTo(layers.tma));
    if (data.coast) L.geoJSON(data.coast, { pane: "qgrid", interactive: false, style: { className: "qr-coast" } }).addTo(layers.base);
    // granice rejonów (zielone), z obrysów pasów 15–17 tylko pomarańczowe linie 53°N i 51°N (reszta obrysu to granica FIR,
    // a szersza linia pasa wystawałaby spod niej pomarańczową obwódką) i FIR
    data.regions.filter((r) => !r.band).forEach((r) => L.geoJSON(r.geometry, { pane: "qreg", interactive: false,
      style: { className: "qr-rline", fill: false } }).addTo(layers.base));
    const par = [];
    data.regions.filter((r) => r.band).flatMap((r) => (r.geometry.type === "Polygon" ? [r.geometry.coordinates] : r.geometry.coordinates))
      .forEach((poly) => {
        let run = [];
        const flush = () => { if (run.length > 1) par.push(run); run = []; };
        poly[0].forEach(([lon, lat]) => ([51, 53].some((p) => Math.abs(lat - p) < 1e-6) ? run.push([lat, lon]) : flush()));
        flush();
      });
    if (par.length) L.polyline(par, { pane: "qband", interactive: false, className: "qr-bline" }).addTo(layers.base);
    if (data.fir) L.geoJSON(data.fir, { pane: "qfir", interactive: false, style: { className: "qr-fir", fill: false } }).addTo(layers.base);

    // numery rejonów (15–17 pomarańczowe jak na mapie AIP) i ramki SEKTOR n z QNH rejonu (obok numeru, jak w vAWOS)
    data.regions.forEach((r) => {
      mark(r.num, div("qr-num" + (r.band ? " band" : ""), esc(r.id)), "qnum", layers.base);
      if (r.label) mark(r.label, div("qr-lbl" + (r.qnh ? "" : " nil"), `<small>SEKTOR ${esc(r.id)}</small><b>${q4(r.qnh)}</b>`,
        ` data-r="${esc(r.id)}"`), "qlbl", layers.box);
    });
    // lotniska: kółko z kropką = lotnisko z METAR-em/TMA, romb = pozostałe
    const qnhOf = {};
    data.regions.forEach((r) => r.stations.forEach((s) => { qnhOf[s.icao] = s.qnh; }));
    data.tmas.forEach((t) => { qnhOf[t.icao] = t.qnh; });
    // lądowiska w miejscu lotniska z METAR-em (np. EPSW = EPLB, EPIN obok EPIR) pomijamy, żeby kody się nie nakładały
    const main = data.aerodromes.filter((a) => a.metar);
    const dup = (a) => !a.metar && main.some((m) => Math.abs(m.lat - a.lat) < 0.04 && Math.abs(m.lon - a.lon) < 0.06);
    data.aerodromes.filter((a) => !dup(a)).forEach((a) => {
      L.marker([a.lat, a.lon], { pane: "qad", keyboard: false, icon: L.divIcon({ className: "qr-icon", iconSize: null,
        html: `<div class="qr-aero${a.metar ? " ctl" : ""}"><i></i><span>${esc(a.icao)}</span></div>` }) })
        .bindTooltip(`${esc(a.icao)}${qnhOf[a.icao] ? " Q" + qnhOf[a.icao] : ""}`, { direction: "top", offset: [0, -6], className: "qr-tip" })
        .addTo(layers.ad);
    });
    // TMA/MTMA: czarny opis "BELOW TMA / QNH FROM EPxx" w miejscu z mapy AIP ("cap"); "join" = lotniska dopisane do
    // wspólnego opisu (EPKT i EPKK: kody obu lotnisk pod "QNH FROM", jak na mapie AIP)
    data.tmas.filter((t) => t.cap).forEach((t) => {
      const codes = [...(t.join || []), t.icao].map(esc);
      const from = codes.length > 1 ? `<span>QNH FROM</span><span class="m">${codes.map((c) => `<span>${c}</span>`).join("")}</span>`
        : `<span>QNH FROM ${codes[0]}</span>`;
      mark(t.cap, div("qr-cap", `<span>BELOW ${esc(t.below)}</span>${from}`), "qcap", layers.cap);
    });
    // szare oznaczenia przestrzeni z mapy AIP: rodzaj nad kodem (TMA / EPGD, MCTR / EPOK, TMA / EPPO NORTH)
    (data.chart_labels || []).forEach((c) => mark(c.lonlat, div("qr-des", c.lines.map((l) => `<span>${esc(l)}</span>`).join("")),
      "qdes", layers.des));
    // ramki TMA/MTMA jak w vAWOS ("box"): skrócona nazwa ("short") i QNH lotniska, którego QNH obowiązuje pod TMA
    data.tmas.filter((t) => t.box).forEach((t) => mark(t.box, div("qr-box" + (t.qnh ? "" : " nil"),
      `<small>${esc(t.short || t.name)}</small><b>${q4(t.qnh)}</b>`, ` data-t="${esc(t.icao)}"`), "qlbl", layers.box));
    show();
  };

  // podświetlenie rejonu / TMA po najechaniu na wiersz tabeli
  let hot = null;
  const highlight = (tr) => {
    if (tr === hot) return;
    hot = tr;
    layers.hl.clearLayers();
    wrap.querySelectorAll(".qrmap .on").forEach((e) => e.classList.remove("on"));
    if (!tr || !data) return;
    const r = tr.dataset.r && data.regions.find((x) => String(x.id) === tr.dataset.r);
    const t = tr.dataset.t && data.tmas.find((x) => x.icao === tr.dataset.t);
    const g = r ? r.geometry : t && (t.area || t.outline);
    if (g) L.geoJSON(g, { pane: "qfir", interactive: false, style: { className: "qr-hl" } }).addTo(layers.hl);
    wrap.querySelector(r ? `.qr-lbl[data-r="${CSS.escape(tr.dataset.r)}"]` : `.qr-box[data-t="${CSS.escape(tr.dataset.t)}"]`)?.classList.add("on");
  };
  const side = $(".qrside");
  side.addEventListener("mouseover", (e) => highlight(e.target.closest("tr[data-r], tr[data-t]")));
  side.addEventListener("mouseleave", () => highlight(null));

  // pasy awaryjne 15–17: zamiast wszystkich lotnisk pasa tylko lotnisko z najniższym QNH (i liczba lotnisk pasa)
  const BAND = { 15: "na północ od 53°N", 16: "między 51°N a 53°N", 17: "na południe od 51°N" };
  const table = () => {
    const st = (s, low) => `<span class="${s.qnh === null ? "nil" : s.qnh === low ? "low" : ""}">${esc(s.icao)}&nbsp;${s.qnh ?? "–"}</span>`;
    const sta = (r) => (!r.band ? r.stations.map((s) => st(s, r.qnh)).join(" ")
      : `${r.stations.filter((s) => s.qnh !== null && s.qnh === r.qnh).slice(0, 2).map((s) => st(s, r.qnh)).join(" ") || "–"}
        <span class="nil">z ${r.stations.length} lotn.</span>`);
    hot = null;
    $(".qr-reg").innerHTML = `<thead><tr><th>Sek.</th><th>QNH</th><th>mmHg</th><th>inHg</th><th>Lotniska</th></tr></thead><tbody>${data.regions.map((r) =>
      `<tr data-r="${esc(r.id)}"${r.band ? ` class="band" title="Rejon ${esc(r.id)}: pas awaryjny ${BAND[r.id] || ""}, QNH podawane tylko przy awarii modelu IMGW; tu najniższe QNH z ${r.stations.length} lotnisk pasa"` : ""}>
      <td class="num">${esc(r.id)}</td><td class="num q">${q4(r.qnh)}</td><td class="num">${r.qnh ? mmhg(r.qnh) : ""}</td>
      <td class="num">${r.qnh ? inhg(r.qnh) : ""}</td><td class="sta">${sta(r)}</td></tr>`).join("")}</tbody>`;
    $(".qr-tma").innerHTML = `<thead><tr><th>TMA / MTMA</th><th>Lotn.</th><th>QNH</th><th>mmHg</th></tr></thead><tbody>${data.tmas.map((t) =>
      `<tr data-t="${esc(t.icao)}"><td>${esc(t.name)}</td><td class="mono">${esc(t.icao)}</td><td class="num q">${q4(t.qnh)}</td>
      <td class="num">${t.qnh ? mmhg(t.qnh) : ""}</td></tr>`).join("")}</tbody>`;
  };

  // widok od razu (także gdy API nie odpowiada), po pierwszym wczytaniu dopasowany do granicy FIR
  map.fitBounds(L.latLngBounds([49, 14.1], [55.85, 24.15]), { padding: [4, 4] });
  let fitted = false, dead = false;
  const load = async () => {
    let d;
    try { d = await api("/api/meteo/qnh-regions"); } catch (e) { if (!dead) $(".qr-err").textContent = e.message; return; }
    if (dead) return;   // zakładka zamknięta w trakcie pobierania
    data = d;
    draw();
    if (!fitted && data.fir) map.fitBounds(L.geoJSON(data.fir).getBounds(), { padding: [4, 4], animate: false });
    fitted = true;
    grat();
    $(".qr-stamp").textContent = "Aktualizacja " + new Date().toISOString().slice(11, 16) + "Z";
    $(".qr-err").textContent = data.error || "";
    table();
    $(".qr-note").textContent = data.note || "";
  };
  load();
  const timer = setInterval(load, 300000);
  const online = () => $(".qr-err").textContent && load();   // serwer wrócił, a na mapie jest komunikat błędu
  window.addEventListener(ONLINE_EVENT, online);
  const ro = new ResizeObserver(() => { map.invalidateSize(); grat(); });
  ro.observe($(".qrmap"));
  return { destroy: () => { dead = true; clearInterval(timer); window.removeEventListener(ONLINE_EVENT, online); ro.disconnect(); map.remove(); } };
}

// --- RADAR, SAT PL, SAT EUR: Windy z własnym środkiem i zoomem dla każdej podzakładki
export const WINDY_VIEWS = {
  radar: { overlay: "radar", lat: 52.0, lon: 19.3, zoom: 6 },
  "sat-pl": { overlay: "satellite", lat: 52.0, lon: 19.0, zoom: 6 },     // Polska (FIR EPWW z sąsiadami)
  "sat-eur": { overlay: "satellite", lat: 50.0, lon: 12.0, zoom: 4 },    // Europa
};
export const windyEmbedUrl = ({ overlay, lat, lon, zoom }) => `https://embed.windy.com/embed2.html?lat=${lat.toFixed(1)}`
  + `&lon=${lon.toFixed(1)}&detailLat=52.17&detailLon=20.97&zoom=${zoom}&level=surface&overlay=${overlay}&product=ecmwf&menu=`
  + "&message=true&marker=&calendar=now&pressure=true&type=map&location=coordinates&detail=&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1";
const windy = (view) => (pane) => {
  pane.append(h(`<iframe class="embed" src="${esc(windyEmbedUrl(WINDY_VIEWS[view]))}" allowfullscreen></iframe>`));
};

// --- TURB i SIGWX: obrazy IMGW wczytywane wprost przez przeglądarkę (bez pośrednictwa serwera PolIDS: serwer IMGW może
// wymagać sesji użytkownika w jego przeglądarce), dopasowane do okna (object-fit: contain) albo 1:1 z przewijaniem
const IMGW = "https://aviation-api.imgw.pl/image";
// turbulencja (IMGW, model DWD, statki rejsowe): 8 klatek co 3 h, od +0 h do +21 h, w animacji co 1,5 s
export const TURB_STEPS = ["000", "003", "006", "009", "012", "015", "018", "021"];
export const TURB_MS = 1500;
export const turbFrames = () => TURB_STEPS.map((s) => ({
  label: `+${Number(s)} h`, url: `${IMGW}/TURBULENCJE/gify_LOTN07_TURB_DWD_statkirejsowe/Turb11000.${s}.gif` }));
export const SIGWX_URL = `${IMGW}/significant/pl`;
export const IMG_REFRESH = 15 * 60000;   // nowe obrazy co 15 min
// znacznik czasu do adresu (?t=20261012121500): ten sam w obrębie okresu, więc powrót na podzakładkę bierze obraz z pamięci
// podręcznej przeglądarki, a po upływie okresu przeglądarka pobiera obraz od nowa
export const stamp = (now, period) => new Date(Math.floor(now / period) * period).toISOString().replace(/\D/g, "").slice(0, 14);
export const bust = (url, t) => `${url}${url.includes("?") ? "&" : "?"}t=${t}`;
// następna klatka w kierunku dir z pominięciem klatek z błędem; null, gdy wszystkie pozostałe mają błąd
export function nextFrame(states, i, dir = 1) {
  const n = states.length;
  for (let k = 1; k < n; k++) {
    const j = (((i + dir * k) % n) + n) % n;
    if (states[j] !== "err") return j;
  }
  return null;
}
// krok animacji (stany klatek: "load" / "ok" / "err"): indeks klatki do pokazania (ten sam = czekamy, aż następna się
// wczyta, klatki z błędem pomijamy), -1 = zatrzymać animację (żadna klatka nie wczytała się)
export function playStep(states, cur) {
  const j = nextFrame(states, cur, 1);
  if (j === null) return states[cur] === "err" ? -1 : cur;
  return states[j] === "load" ? cur : j;
}

const utcHm = (d) => d.toISOString().slice(11, 16) + "Z";

// Widok obrazu (klatek) z serwera IMGW. o: {key, title, note, frames: [{label, url}]}; przy kilku klatkach odtwarzacz:
// start/stop, poprzednia/następna, wybór klatki; wszystkie klatki wczytywane od razu (płynna animacja).
function imgView(o) {
  return (pane) => {
    const fr = o.frames, n = fr.length, multi = n > 1;
    const st = fr.map(() => "load"), got = fr.map(() => null);
    let cur = 0, playing = false, held = false, play = null, dead = false, lastMsg = "", period = stamp(Date.now(), IMG_REFRESH);
    let zoom = lsGet(`meteo.${o.key}.zoom`) === "full" ? "full" : "fit";
    const secs = String(TURB_MS / 1000).replace(".", ",");
    const wrap = h(`<div class="mimg mimg-${esc(o.key)}">
      <div class="mimg-bar">
        <b class="mimg-title">${esc(o.title)}</b>
        ${multi ? `<span class="mimg-seg mimg-ctl"><button data-act="prev" title="Poprzednia klatka (←)">◀</button><button data-act="play"
          class="mimg-play"></button><button data-act="next" title="Następna klatka (→)">▶</button></span>
          <span class="mimg-seg mimg-steps">${fr.map((f, i) => `<button data-i="${i}">${esc(f.label)}</button>`).join("")}</span>` : ""}
        <span class="mimg-hint"></span>
        <span class="mimg-seg mimg-zoom"><button data-z="fit" title="Cały obraz w oknie">DOPASUJ</button><button data-z="full"
          title="Rzeczywista wielkość obrazu, z przewijaniem">1:1</button></span>
        <a class="btn mimg-open" target="_blank" rel="noopener noreferrer">Otwórz w nowej karcie ↗</a>
      </div>
      <div class="mimg-stage" tabindex="0"><div class="mimg-msg" hidden></div></div>
    </div>`);
    pane.append(wrap);
    const $ = (s) => wrap.querySelector(s);
    const stage = $(".mimg-stage"), msg = $(".mimg-msg");

    // klatki: <img> dla każdej, wszystkie od razu w DOM (przeglądarka pobiera je równolegle), widoczna tylko bieżąca
    const mk = (i) => {
      const im = document.createElement("img");
      im.referrerPolicy = "no-referrer";
      im.decoding = "async";
      im.draggable = false;
      im.alt = multi ? `${o.title} ${fr[i].label}` : o.title;
      im.dataset.i = i;
      return im;
    };
    const watch = (im, i) => {
      im.addEventListener("load", () => set(i, im.naturalWidth ? "ok" : "err"));
      im.addEventListener("error", () => set(i, "err"));
      return im;
    };
    const imgs = fr.map((_, i) => stage.insertBefore(watch(mk(i), i), msg));
    const load = (i, t) => { imgs[i].src = bust(fr[i].url, t); };
    function set(i, s) {
      if (dead) return;
      st[i] = s;
      if (s === "ok") got[i] = new Date();
      if (s === "ok" && held) start();   // animacja stanęła, bo nic się nie wczytało; jest klatka, więc rusza dalej
      draw();
    }

    function draw() {
      imgs.forEach((im, i) => { im.classList.toggle("on", i === cur); im.classList.toggle("ok", st[i] === "ok"); });
      stage.className = `mimg-stage ${zoom}`;
      wrap.querySelectorAll(".mimg-zoom button").forEach((b) => b.classList.toggle("active", b.dataset.z === zoom));
      wrap.querySelectorAll(".mimg-steps button").forEach((b, i) => {
        b.classList.toggle("active", i === cur);
        b.classList.toggle("load", st[i] === "load");
        b.classList.toggle("err", st[i] === "err");
        b.title = fr[i].label + (st[i] === "err" ? ": nie udało się wczytać obrazu" : st[i] === "load" ? ": wczytywanie…" : "");
      });
      const pb = $(".mimg-play");
      if (pb) {
        pb.textContent = playing ? "STOP" : "START";
        pb.title = playing ? "Zatrzymaj animację (spacja)" : `Odtwarzaj klatki po kolei, co ${secs} s (spacja)`;
        pb.classList.toggle("active", playing);
      }
      const f = fr[cur], name = multi ? `${o.title} ${f.label}` : o.title;
      $(".mimg-open").href = f.url;
      const hint = $(".mimg-hint");
      hint.textContent = [got[cur] ? `pobrano ${utcHm(got[cur])}` : "", o.note].filter(Boolean).join(" · ");
      hint.title = `${name}: ${o.note}\n${f.url}\nObraz z serwera IMGW, odświeżany co ${IMG_REFRESH / 60000} min.`;
      msg.hidden = st[cur] === "ok";
      msg.className = `mimg-msg ${st[cur]}`;
      const html = st[cur] === "err"
        ? `<b>${esc(name)}: nie udało się wczytać obrazu</b>
          <span>Serwer IMGW (aviation-api.imgw.pl) nie wydał obrazu. Może wymagać zalogowania w serwisie lotniczym IMGW
          w tej przeglądarce albo jest chwilowo niedostępny.</span>
          <span class="mimg-acts"><a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">otwórz w nowej karcie ↗</a>
          <button class="btn mimg-retry">Wczytaj ponownie</button></span>`
        : `<span>Wczytywanie: ${esc(name)}…</span>`;
      if (html !== lastMsg) msg.innerHTML = lastMsg = html;   // bez przebudowy przy każdej klatce (fokus na przycisku zostaje)
    }

    const go = (i) => { cur = ((i % n) + n) % n; draw(); };
    const tick = () => {
      if (document.hidden) return;
      const j = playStep(st, cur);
      if (j < 0) { stop(); held = true; return; }   // żadna klatka się nie wczytała: stop, ruszy po wczytaniu którejś
      if (j !== cur) go(j);
    };
    function start() { clearInterval(play); held = false; playing = true; play = setInterval(tick, TURB_MS); draw(); }
    function stop() { clearInterval(play); play = null; held = false; playing = false; draw(); }
    const act = (a) => {
      if (a === "play") return playing ? stop() : start();
      stop();   // ręczny wybór klatki zatrzymuje animację
      go(cur + (a === "next" ? 1 : -1));
    };

    $(".mimg-bar").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.act) act(b.dataset.act);
      else if (b.dataset.i !== undefined) { stop(); go(Number(b.dataset.i)); }
      else if (b.dataset.z) { zoom = b.dataset.z; lsSet(`meteo.${o.key}.zoom`, zoom); draw(); }
    });
    // "Wczytaj ponownie": nowy znacznik czasu, przeglądarka pyta serwer IMGW jeszcze raz (np. po zalogowaniu w innej karcie)
    msg.addEventListener("click", (e) => {
      if (!e.target.closest(".mimg-retry")) return;
      const t = stamp(Date.now(), 1000);
      fr.forEach((_, i) => { if (st[i] === "err") { st[i] = "load"; load(i, t); } });
      draw();
    });
    stage.addEventListener("keydown", (e) => {
      if (!multi || e.target !== stage) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") act(e.key === "ArrowLeft" ? "prev" : "next");
      else if (e.key === " ") act("play");
      else return;
      e.preventDefault();
    });

    // co 15 min (na pełny kwadrans) nowy znacznik czasu w adresie: nowy obraz wczytywany w tle i wstawiany w miejsce
    // starego dopiero po wczytaniu, więc nieudane odświeżenie nie zabiera wczytanego wcześniej obrazu; klatki z błędem
    // wczytujemy od nowa
    const refresh = () => {
      const t = stamp(Date.now(), IMG_REFRESH);
      if (t === period) return;
      period = t;
      fr.forEach((f, i) => {
        if (st[i] !== "ok") { st[i] = "load"; load(i, t); return; }
        const pre = mk(i);
        pre.onload = () => {
          if (dead || !pre.naturalWidth) return;
          pre.onload = null;
          imgs[i].replaceWith(watch(pre, i));
          imgs[i] = pre;
          set(i, "ok");
        };
        pre.src = bust(f.url, t);
      });
      draw();
    };
    const refreshTimer = setInterval(refresh, 60000);

    fr.forEach((_, i) => load(i, period));
    if (multi) start(); else draw();
    return { destroy: () => { dead = true; clearInterval(play); clearInterval(refreshTimer); } };
  };
}

// --- WIND: Windy przy ziemi (0 ft) albo na 900 hPa (≈3000 ft AMSL) z punktem prognozy w punkcie podejścia wybranego
// pasa; obok wiatr z Open-Meteo w punktach podejścia wszystkich pasów lotniska (backend: services/upperwind.py)
const MAIN_AD = PL_CIV.split(" ");

function windyUrl(o) {
  const q = new URLSearchParams({ lat: o.lat.toFixed(3), lon: o.lon.toFixed(3), detailLat: o.dlat.toFixed(4), detailLon: o.dlon.toFixed(4),
    zoom: o.zoom, level: o.level, overlay: "wind", product: "ecmwf", menu: "", message: "true", marker: "true", calendar: "now",
    pressure: "true", type: "map", location: "coordinates", detail: "true", metricWind: "kt", metricTemp: "°C", radarRange: "-1" });
  return `https://embed.windy.com/embed2.html?${q}`;
}

// wiatr "310/14G26", składowe "H13" / "T13" (w plecy) i "5R" / "5L"; używane też w AERODROME
const p2 = (v, n) => String(v).padStart(n, "0");
export const windVec = (w) => (!w || w.speed === null || w.speed === undefined ? "–"
  : `${w.variable ? "VRB" : p2(w.dir, 3)}/${p2(w.speed, 2)}${w.gust ? "G" + w.gust : ""}`);
// składowe zawsze zaokrąglane po wartości bezwzględnej (-13.5 -> T14, 13.5 -> H14), wszędzie tak samo
export const kt = (v) => Math.round(Math.abs(v));
export const hwTxt = (v) => (v === null || v === undefined ? "–" : v < 0 && kt(v) ? `<span class="tw">T${kt(v)}</span>` : `H${kt(v)}`);
export const xwTxt = (v) => (v === null || v === undefined ? "–" : kt(v) === 0 ? "0" : `${kt(v)}${v > 0 ? "R" : "L"}`);
// "2026-10-06T18:00Z" -> "18:00Z 06.10"
export const validTxt = (t) => (t ? `${t.slice(11, 16)}Z ${t.slice(8, 10)}.${t.slice(5, 7)}` : "–");
// odległość od progu zawsze z jedną cyfrą po przecinku ("3.0 NM", nie "3 NM" obok "4.9 NM")
const nm = (v) => (v === null || v === undefined ? "–" : Number(v).toFixed(1));
export const pointTip = (r) => `RWY ${r.designator}: ${r.fix ? r.fix + ", " : ""}${r.method_label}, ${nm(r.dist_nm)} NM od progu`
  + `, ścieżka 3° ≈ ${r.path_ft} ft${r.approaches?.length ? `\nPodejścia: ${r.approaches.join(", ")}` : ""}`
  + `${r.rejected?.length ? `\nOdrzucone punkty procedur: ${r.rejected.join("; ")}` : ""}`;

function windTab(pane) {
  const wrap = h(`<div class="wnd">
    <div class="wnd-bar">
      <span class="wnd-seg wnd-mode"><button data-m="0">0 ft</button><button data-m="3000">3000 ft (podejście)</button></span>
      <label class="wnd-aero-l">Lotnisko <select class="wnd-aero"></select></label>
      <span class="wnd-rwyl">Pas <span class="wnd-seg wnd-rwys"></span></span>
      <span class="wnd-hint"></span>
    </div>
    <div class="wnd-body"><iframe class="embed wnd-frame" allowfullscreen></iframe><aside class="wnd-side"></aside></div>
  </div>`);
  pane.append(wrap);
  const $ = (s) => wrap.querySelector(s);
  let mode = lsGet("meteo.wind.mode") === "3000" ? "3000" : "0";
  let icao = (lsGet("meteo.wind.ad") || "EPWA").toUpperCase();
  let ads = [], ad = null, rwy = null, inUse = null, aw = null, awErr = "", ptsErr = "", src = "", seq = 0, dead = false;
  const rwyKey = () => `meteo.wind.rwy.${icao}`;
  const sel = () => ad?.runways.find((r) => r.designator === rwy) || null;
  const roles = (d) => ["arr", "dep"].filter((k) => inUse?.[k] === d);
  const tags = (d) => roles(d).map((k) => `<span class="wnd-tag ${k}">${k.toUpperCase()}${inUse?.source === "ATIS" ? "" : "?"}</span>`).join("");

  const frame = () => {
    const r = sel(), up = mode === "3000";
    const o = up && r ? { lat: r.lat, lon: r.lon, dlat: r.lat, dlon: r.lon, zoom: 8, level: "900h" }
      : { lat: 52.0, lon: 19.3, dlat: ad ? ad.lat : 52.166, dlon: ad ? ad.lon : 20.967, zoom: 6, level: up ? "900h" : "surface" };
    const url = windyUrl(o);
    // nowa ramka zamiast zmiany src: bez kroku w historii przeglądarki (← i Wstecz cofają ekrany, nie ramkę Windy)
    if (url !== src) { const f = $(".wnd-frame"), g = f.cloneNode(false); g.src = src = url; f.replaceWith(g); }
    const hint = $(".wnd-hint");
    hint.textContent = up
      ? `Windy 900 hPa ≈ 3000 ft AMSL · punkt prognozy ${r ? `RWY ${r.designator} ${r.fix || "oś 3°"}` : "– brak danych pasa"}`
      : `Windy 10 m (przy ziemi) · punkt prognozy ${ad ? ad.icao : icao}`;
    hint.title = `${hint.textContent}${up && r ? ` (${r.method_label}, ${nm(r.dist_nm)} NM od progu)` : ""} · model Windy: ECMWF`;
  };

  const bar = () => {
    wrap.querySelectorAll(".wnd-mode button").forEach((b) => b.classList.toggle("active", b.dataset.m === mode));
    $(".wnd-rwyl").hidden = mode !== "3000" || !ad;
    $(".wnd-rwys").innerHTML = (ad?.runways || []).map((r) => `<button data-rwy="${esc(r.designator)}" class="${r.designator === rwy ? "active" : ""} ${roles(r.designator).join(" ")}"
      title="${esc(pointTip(r))}">${esc(r.designator)}</button>`).join("");
  };

  const side = () => {
    const el = $(".wnd-side");
    const head = `<h3>${esc(ad?.icao || icao)} · ${mode === "3000" ? "wiatr na podejściu 3000 ft" : "wiatr przy ziemi"}</h3>`;
    if (!aw) {
      el.innerHTML = `<div class="card wnd-card">${head}<p class="${awErr || ptsErr ? "error" : "hint"}">${esc(awErr || ptsErr || "Ładowanie…")}</p></div>`;
      return;
    }
    const thead = mode === "3000" ? `<tr><th>Pas</th><th>Punkt podejścia</th><th>Wiatr</th><th>H/T</th><th>XW</th></tr>`
      : `<tr><th rowspan="2">Pas</th><th colspan="2">METAR</th><th colspan="2">Model 10 m</th></tr><tr><th>H/T</th><th>XW</th><th>H/T</th><th>XW</th></tr>`;
    const rows = aw.runways.map((r) => {
      const cls = [r.designator === rwy ? "sel" : "", ...roles(r.designator)].join(" ");
      const cells = mode === "3000"
        ? `<td class="pt"><b>${esc(r.fix || "oś 3°")} <span>${nm(r.dist_nm)} NM</span></b><small>${esc(r.method_label)}</small></td>
          <td class="num w">${windVec(r.w3000)}</td><td class="num">${hwTxt(r.c3000.hw)}</td><td class="num">${xwTxt(r.c3000.xw)}</td>`
        : `<td class="num">${hwTxt(r.c_metar.hw)}</td><td class="num">${xwTxt(r.c_metar.xw)}</td>
          <td class="num">${hwTxt(r.c_model.hw)}</td><td class="num">${xwTxt(r.c_model.xw)}</td>`;
      return `<tr data-rwy="${esc(r.designator)}" class="${cls}" title="${esc(pointTip(r))}"><td class="rwy"><b>${esc(r.designator)}</b><span class="wnd-tags">${tags(r.designator)}</span></td>${cells}</tr>`;
    }).join("");
    el.innerHTML = `<div class="card wnd-card">${head}
      <div class="wnd-sfc"><span><i>0 ft METAR</i><b>${windVec(aw.metar_wind)}</b></span><span><i>0 ft model 10 m</i><b>${windVec(aw.model_surface)}</b></span></div>
      <table class="data wnd-tab"><thead>${thead}</thead><tbody>${rows}</tbody></table>
      ${[aw.model_error, aw.metar_error].filter(Boolean).map((e) => `<p class="error">${esc(e)}</p>`).join("")}
      <p class="wnd-note">Źródło: <a href="${esc(aw.source_url)}" target="_blank" rel="noopener">${esc(aw.source)}</a> (model ${esc(aw.model)}),
        ${aw.valid ? `prognoza na ${validTxt(aw.valid)}, pobrano ${esc(aw.fetched)}` : "prognoza niedostępna"}. Wiatr 3000 ft AMSL interpolowany z poziomów 1000–850 hPa.</p>
      ${mode === "3000" ? `<p class="wnd-note">Punkt podejścia: FAF/IF, na którym kończą się STAR z przejściem do podejścia (plik .ese), jeśli leży 2.5–15 NM od progu
        i do 15° od osi; inaczej punkt na przedłużeniu osi, w którym ścieżka 3° osiąga 3000 ft. Kliknij pas, żeby przenieść tam punkt prognozy Windy.</p>`
        : `<p class="wnd-note">Wiatr 0 ft: METAR oraz model 10 m nad punktem lotniska; składowe względem kursu każdego pasa.</p>`}
      <p class="wnd-note">H = czołowy, T = w plecy, L/R = boczny z lewej/prawej, kt. ${inUse ? `ARR/DEP: pas w użyciu (${inUse.source === "ATIS" ? "ATIS" : "? = sugestia systemu"}).` : ""}
        Windy pokazuje model ECMWF, Open-Meteo zwykle ICON, więc wartości mogą się różnić.</p>
    </div>`;
  };

  const loadWind = async () => {
    const my = seq;
    try {
      const d = await api(`/api/meteo/approach-wind/${icao}`);
      if (dead || my !== seq) return;
      aw = d;
      awErr = "";
    } catch (e) {
      if (dead || my !== seq) return;
      awErr = e.message;
    }
    side();
  };

  const pick = async (code) => {
    icao = code;
    lsSet("meteo.wind.ad", icao);
    ad = ads.find((a) => a.icao === icao) || null;
    rwy = lsGet(rwyKey());
    if (!ad?.runways.some((r) => r.designator === rwy)) rwy = null;
    inUse = aw = null;
    awErr = "";
    const my = ++seq;
    if (mode === "0" || rwy) frame();   // punkt prognozy już znany; w 3000 ft bez wybranego pasa czekamy na pas w użyciu
    bar();
    side();
    loadWind();
    // pas do lądowania w użyciu (ATIS albo sugestia systemu) jako domyślny, jeśli odpowiedź przyjdzie szybko
    const st = api(`/api/aerodromes/${icao}/status`).then((x) => x.runway_in_use || null).catch(() => null);
    const first = await Promise.race([st, new Promise((res) => setTimeout(() => res(null), 1500))]);
    if (dead || my !== seq) return;
    inUse = first;
    if (!rwy) rwy = (ad?.runways.some((r) => r.designator === inUse?.arr) ? inUse.arr : ad?.runways[0]?.designator) || null;
    bar();
    frame();
    side();
    st.then((u) => { if (!dead && my === seq && u && !inUse) { inUse = u; bar(); side(); } });
  };

  wrap.querySelector(".wnd-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-m]");
    if (!b || b.dataset.m === mode) return;
    mode = b.dataset.m;
    lsSet("meteo.wind.mode", mode);
    bar();
    frame();
    side();
  });
  const choose = (e) => {
    const t = e.target.closest("[data-rwy]");
    if (!t || !ad) return;
    rwy = t.dataset.rwy;
    lsSet(rwyKey(), rwy);
    bar();
    frame();
    side();
  };
  $(".wnd-rwys").addEventListener("click", choose);
  $(".wnd-side").addEventListener("click", choose);
  $(".wnd-aero").addEventListener("change", (e) => pick(e.target.value));

  bar();
  side();
  const init = async () => {
    try {
      ads = (await api("/api/meteo/approach-points")).aerodromes;
      ptsErr = "";
    } catch (e) { ptsErr = `Punkty podejścia: ${e.message}`; }
    if (dead) return;
    if (ads.length && !ads.some((a) => a.icao === icao)) icao = ads.some((a) => a.icao === "EPWA") ? "EPWA" : ads[0].icao;
    const opt = (a) => `<option value="${a.icao}"${a.icao === icao ? " selected" : ""}>${a.icao} ${esc(a.name)}</option>`;
    const main = ads.filter((a) => MAIN_AD.includes(a.icao)), rest = ads.filter((a) => !MAIN_AD.includes(a.icao));
    $(".wnd-aero").innerHTML = ads.length ? `<optgroup label="Lotniska główne">${main.map(opt).join("")}</optgroup><optgroup label="Pozostałe">${rest.map(opt).join("")}</optgroup>`
      : `<option>${esc(icao)}</option>`;
    pick(icao);
  };
  init();
  const timer = setInterval(() => { if (!document.hidden) loadWind(); }, 600000);
  // serwer wrócił po przerwie: brakujące punkty podejścia albo wiatr pobieramy od razu, nie po 10 min
  const online = () => (!ads.length ? init() : awErr && loadWind());
  window.addEventListener(ONLINE_EVENT, online);
  return { destroy: () => { dead = true; clearInterval(timer); window.removeEventListener(ONLINE_EVENT, online); } };
}

export default {
  mount(root) {
    subtabs(root, [
      { id: "metar-pl", label: "METAR PL", render: wxList("metar", PL_CIV, "meteo.metar.pl") },
      { id: "metar-mil", label: "METAR MIL", render: wxList("metar", PL_MIL, "meteo.metar.mil") },
      { id: "metar-intl", label: "METAR INTL", render: wxList("metar", INTL, "meteo.metar.intl") },
      { id: "taf-pl", label: "TAF PL", render: wxList("taf", PL_CIV, "meteo.taf.pl") },
      { id: "taf-intl", label: "TAF INTL", render: wxList("taf", INTL, "meteo.taf.intl") },
      { sep: true },
      { id: "qnh", label: "QNH", fill: true, render: qnhMap },
      { id: "wind", label: "WIND", fill: true, render: windTab },
      { id: "radar", label: "RADAR", fill: true, render: windy("radar") },
      { id: "sat-pl", label: "SAT PL", fill: true, render: windy("sat-pl") },
      { id: "sat-eur", label: "SAT EUR", fill: true, render: windy("sat-eur") },
      { id: "turb", label: "TURB", fill: true, render: imgView({ key: "turb", title: "TURB", frames: turbFrames(),
        note: "prognoza IMGW (model DWD), statki rejsowe" }) },
      { id: "sigwx", label: "SIGWX", fill: true, render: imgView({ key: "sigwx", title: "SIGWX", frames: [{ label: "", url: SIGWX_URL }],
        note: "mapa zjawisk istotnych SFC–FL100, IMGW" }) },
    ]);
  },
};
