// Pozycje w MAP (runda 12, Marek): drzewo FIR → ACC / FIS / lotniska → stanowiska z włączaniem pojedynczych stanowisk.
// Zestaw aktywny = zalogowani w sieci z nadpisaniami (sessionStorage): true = symulacja logowania (stanowisko offline
// rysujemy tak, jakby ktoś na nim był), false = ukryj zalogowanego. Z zestawu aktywnego korzysta sektoryzacja
// (obsada wg kontrolerów online), plakietki lotnisk i FIR-ów. Funkcje czyste (testy w Node), widok w tabs/map.js.

export const SIM_NAME = "symulacja";

// rodzaj plakietki lotniska ze znaku (jak w airspace.js): EPGD_L_APP → APP, EPWA_DEL → DEL
const BADGE = { DEL: "DEL", GND: "GND", RMP: "GND", TWR: "TWR", APP: "APP", DEP: "APP", ATIS: "ATIS" };

// Stan wyboru: {base, ovr}. base "online" = domyślnie zalogowani, "none" = domyślnie nikt (po "wyczyść");
// ovr: {znak: true | false} – odstępstwa od base kliknięte na liście.
// Zestaw aktywny w kształcie /api/vatsim/online: positions (znak stanowiska → kontroler), controllers (EP**), firs (VATSpy),
// live (znaki z sieci aktywnych kontrolerów), gone (znaki z sieci ukrytych) i sims (znaki symulowanych stanowisk).
// online: dane z sieci albo null (błąd VATSIM: same symulacje). posOf(znak): stanowisko z listy (/api/nav/positions/all:
// frequency, twin) albo nic – symulujemy tylko stanowiska z listy, więc zalogowany spoza listy (też po wylogowaniu)
// nie staje się symulacją. firOf(znak): FIR-y VATSpy stanowiska CTR/FSS (firsOf), plakietka CTR symulowanego ACC.
// Kontroler sąsiada pod drugim znakiem (alias_of, ten sam człowiek) idzie za stanowiskiem, pod którym jest zalogowany
// (ukryty i po "wyczyść" włączony razem z nim); symulacja stanowiska z drugim znakiem (twin, np. EKDK_CTR → EKDK_UN_CTR,
// właściciel sektorów Danii) jest tam wpisana tak samo jak zalogowany (alias_of), o ile nikt tam naprawdę nie siedzi.
export function activeOnline(online, state = {}, { posOf = () => null, firOf = () => [] } = {}) {
  const ovr = state.ovr || {}, none = state.base === "none", src = online?.positions || {};
  const extra = strays(online), stray = new Set(extra.map((c) => c.callsign));
  const sims = Object.keys(ovr).filter((cs) => ovr[cs] === true && !src[cs] && !stray.has(cs) && posOf(cs));
  if (!online && !sims.length && !none) return null;
  const positions = {};
  Object.entries(src).forEach(([cs, c]) => {
    if (ovr[cs] ?? (c.alias_of ? ovr[c.alias_of] : undefined) ?? !none) positions[cs] = c;
  });
  sims.forEach((cs) => { positions[cs] = { callsign: cs, frequency: posOf(cs)?.frequency || "", name: SIM_NAME, sim: true }; });
  sims.forEach((cs) => {
    const tw = posOf(cs)?.twin;
    if (tw && !src[tw] && !positions[tw] && ovr[tw] !== false) positions[tw] = { ...positions[cs], alias_of: cs };
  });
  const live = new Set([...Object.values(positions), ...extra.filter((c) => ovr[c.callsign] ?? !none)].map((c) => c.callsign));
  const gone = new Set([...Object.values(src), ...extra].map((c) => c.callsign).filter((cs) => !live.has(cs)));
  const keep = (c) => !gone.has(c.callsign);
  const firs = Object.fromEntries(Object.entries(online?.firs || {}).map(([id, l]) => [id, (l || []).filter(keep)]).filter(([, l]) => l.length));
  sims.forEach((cs) => firOf(cs).forEach((id) => (firs[id] ||= []).push(positions[cs])));
  return {
    ...(online || {}), positions, live, gone, sims, firs,
    controllers: (online?.controllers || []).filter(keep),
  };
}

// FIR-y (id z VATSpy, /api/vatsim/firs) stanowiska CTR/FSS jak online_firs w backendzie: najdłuższy pasujący prefiks
// FIR-u albo UIR-u (EPWW_N_CTR → EPWW-N). Indeks prefiksów w pamięci dla danych VATSpy.
const firIdx = new WeakMap();
export function firsOf(cs, vatspy) {
  if (!vatspy?.features || !/_(CTR|FSS)$/.test(cs || "")) return [];
  let idx = firIdx.get(vatspy);
  if (!idx) {
    const ids = vatspy.features.map((f) => f.properties.id);
    idx = [...vatspy.features.flatMap((f) => (f.properties.prefixes || []).map((pre) => [pre, [f.properties.id]])),
      ...(vatspy.uirs || []).map((u) => [u.id, ids.filter((id) => (u.firs || []).includes(id.split("-")[0]))])];
    idx.sort((a, b) => b[0].length - a[0].length);
    firIdx.set(vatspy, idx);
  }
  return idx.find(([pre]) => cs === pre || cs.startsWith(pre + "_"))?.[1] || [];
}

// Plakietki lotnisk (/api/vatsim/atc) z zestawem aktywnym: bez ukrytych kontrolerów, z symulowanymi stanowiskami
// lotniskowymi (położenie lotniska bez nikogo online z airports z /api/nav/positions/all). Lotnisko z symulacją: sim.
export function activeAtc(atc, act, airports = {}) {
  if (!act) return atc;
  const by = new Map();
  (atc?.airports || []).forEach((ap) => {
    const fac = {};
    Object.entries(ap.facilities || {}).forEach(([k, l]) => { const x = (l || []).filter((c) => !act.gone.has(c.callsign)); if (x.length) fac[k] = x; });
    by.set(ap.icao, { ...ap, facilities: fac });
  });
  act.sims.forEach((cs) => {
    const parts = cs.split("_"), k = BADGE[parts[parts.length - 1]], icao = parts[0];
    if (!k || parts.length < 2) return;
    let ap = by.get(icao);
    if (!ap) {
      const a = airports[icao];
      if (!a) return;
      by.set(icao, (ap = { icao, name: a.name, lat: a.lat, lon: a.lon, facilities: {} }));
    }
    ap.sim = true;
    (ap.facilities[k] ||= []).push(act.positions[cs]);
  });
  return { ...atc, airports: [...by.values()].filter((ap) => Object.keys(ap.facilities).length) };
}

// Zalogowani spoza listy stanowisk (np. EPWA_W_APP na innej częstotliwości): też w drzewie, żeby dało się ich ukryć.
export function strays(online) {
  const used = new Set(Object.values(online?.positions || {}).map((c) => c.callsign));
  return (online?.controllers || []).filter((c) => c.callsign && !used.has(c.callsign) && !/_(OBS|SUP)$/.test(c.callsign));
}

// Drzewo: [{id, label, name, groups: [{id, label, items: [{cs, name, title, freq, state}]}]}]; state: on (zalogowany
// i aktywny), sim (symulacja), hid (zalogowany, ukryty), off. Grupy: ACC, FIS, potem lotniska wg ICAO; w ACC i FIS
// stanowiska wg nazwy, na lotnisku APP, TWR, GND, DEL, ATIS. nameOf: krótka nazwa (displayName z posname.js).
export function buildTree(data, online, act, nameOf = (cs) => cs) {
  const firs = new Map((data?.firs || []).map((f) => [f.id, { ...f, groups: new Map() }]));
  const add = (p, extra = false) => {
    const fir = firs.get(p.fir) || firs.set(p.fir, { id: p.fir, label: p.fir, name: "", groups: new Map() }).get(p.fir);
    if (!fir.groups.has(p.group)) fir.groups.set(p.group, { id: p.group, label: p.group, name: data?.airports?.[p.group]?.name || "", items: [] });
    const cs = p.callsign, on = extra || !!online?.positions?.[cs];
    const live = extra ? !!act?.live.has(cs) : !!act?.positions[cs];
    fir.groups.get(p.group).items.push({ cs, name: nameOf(cs), title: p.name || "", freq: p.frequency || online?.positions?.[cs]?.frequency || "",
      order: p.order ?? 5, state: live ? (act.positions?.[cs]?.sim ? "sim" : "on") : on ? "hid" : "off", extra });
  };
  (data?.positions || []).forEach((p) => add(p));
  const known = new Set((data?.positions || []).map((p) => p.callsign));
  strays(online).filter((c) => !known.has(c.callsign)).forEach((c) => {
    const parts = c.callsign.split("_"), t = parts[parts.length - 1];
    add({ callsign: c.callsign, frequency: c.frequency, fir: "EPWW", group: ["CTR", "FSS"].includes(t) ? "ACC" : parts[0], order: { APP: 0, DEP: 0, TWR: 1, GND: 2, DEL: 3, ATIS: 4 }[t] ?? 5 }, true);
  });
  const rank = (g) => (g.id === "ACC" ? "0" : g.id === "FIS" ? "1" : "2" + g.id);
  return [...firs.values()].filter((f) => f.groups.size).map((f) => ({
    ...f,
    groups: [...f.groups.values()].sort((a, b) => rank(a).localeCompare(rank(b))).map((g) => ({
      ...g, items: g.items.sort((a, b) => (["ACC", "FIS"].includes(g.id) ? 0 : a.order - b.order) || a.name.localeCompare(b.name, "pl", { numeric: true })),
    })),
  }));
}

// Liczniki węzła: aktywne (zalogowani i symulowani), symulowane, zalogowane
export function countOf(items) {
  return items.reduce((n, it) => ({ act: n.act + (it.state === "on" || it.state === "sim"), sim: n.sim + (it.state === "sim"),
    onl: n.onl + (it.state === "on" || it.state === "hid") }), { act: 0, sim: 0, onl: 0 });
}

// Wyszukiwanie: wszystkie słowa w znaku, krótkiej nazwie, nazwie z .ese, częstotliwości, grupie, lotnisku albo FIR-ze
export function matchItem(it, g, f, q) {
  const words = String(q || "").toUpperCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = `${it.cs} ${it.name} ${it.title} ${it.freq} ${g.id} ${g.name} ${f.id} ${f.label}`.toUpperCase();
  return words.every((w) => hay.includes(w));
}

// Kliknięcie stanowiska: nowy stan wyboru. Stan zgodny z tym, co bez odstępstwa dla tego znaku daje reszta wyboru
// (base "online": zalogowany włączony, offline wyłączony; base "none": wyłączony; drugi znak – alias_of albo twin – jak
// stanowisko, za którym idzie), zapisujemy bez odstępstwa. opts: jak w activeOnline.
export function toggle(state, cs, checked, online, opts = {}) {
  const base = state?.base === "none" ? "none" : "online", ovr = { ...(state?.ovr || {}) };
  delete ovr[cs];
  const a = activeOnline(online, { base, ovr }, opts);
  const def = strays(online).some((c) => c.callsign === cs) ? !!a?.live.has(cs) : !!a?.positions[cs];
  if (checked !== def) ovr[cs] = checked;
  return { base, ovr };
}
