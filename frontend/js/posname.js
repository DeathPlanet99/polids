// Nazwy stanowisk jak w VACS: EPWW_DBF_CTR → EPWW DBF, EDMM_MEI_CTR → EDMM MEI, EPPO_N_APP → PO N APP, EPWA_TWR → WA TWR.
// CTR/FSS bez typu; polskie lotniska bez "EP" (poza EPWW); pozostałe: prefiks, środek i typ.
import { api } from "./api.js";

const NO_TYPE = ["CTR", "FSS"];
const TYPES = ["CTR", "FSS", "APP", "DEP", "TWR", "GND", "DEL", "ATIS", "FMP", "TMU", "RMP", "FIS", "DIR"];

export function posName(callsign) {
  const parts = String(callsign || "").toUpperCase().split("_").filter(Boolean);
  if (parts.length < 2) return parts.join(" ");
  const type = TYPES.includes(parts[parts.length - 1]) ? parts.pop() : null;
  let [prefix, ...mid] = parts;
  if (type && NO_TYPE.includes(type)) return [prefix, ...mid].join(" ");
  if (prefix.startsWith("EP") && prefix !== "EPWW" && prefix.length === 4) prefix = prefix.slice(2);
  return [prefix, ...mid, ...(type ? [type] : [])].join(" ");
}

// Jedna nazwa stanowiska w całej aplikacji (RADIO: etykiety podglądów; MAP: dymki).
// Etykieta VACS, gdy stanowisko ma kafelek na stronach GEO (np. EPWA_I_APP → "WA C FIS", LKAA_U_CTR → "LKAA N/S")
// albo etykietę sąsiada w profilu VACS ACC_EPWW; inaczej posName. Stanowiska ACC EPWW (EPWW_*_CTR) zawsze posName.
// Przed wczytaniem etykiet (loadDisplayNames) displayName zwraca posName.
const LABELS = new Map(), ALTS = new Map();
let namesP = null;

// wspólne stanowisko kilku kluczy: wspólny początek etykiety + reszty po "/" (LKAA N + LKAA S -> LKAA N/S)
function mergeLabels(labels) {
  const words = labels.map((l) => String(l).split(" "));
  let i = 0;
  while (words.every((w) => i < w.length - 1 && w[i] === words[0][i])) i++;
  return [...words[0].slice(0, i), [...new Set(words.map((w) => w.slice(i).join(" ")))].join("/")].join(" ");
}

// na kafelkach VACS długie nazwy lotnisk są łamane w środku słowa (BIALY/STOK) – w nazwie stanowiska sklejamy je z powrotem
const SPLIT_WORDS = { "BIALY STOK": "BIALYSTOK", "JASTA RNIA": "JASTARNIA", "POBIE DNIK": "POBIEDNIK", "PRZAS NYSZ": "PRZASNYSZ" };
const joinSplit = (label) => Object.entries(SPLIT_WORDS).reduce((l, [a, b]) => l.replace(a, b), String(label || ""));

export function setDisplayNames(vacs) {
  LABELS.clear(); ALTS.clear();
  const by = new Map();
  const add = (cs, label, alt) => {
    const k = String(cs || "").toUpperCase();
    if (!k || !label || /^EPWW_.*_(CTR|FSS)$/.test(k)) return;
    if (!by.has(k)) by.set(k, { labels: [], alts: [] });
    const e = by.get(k);
    if (!e.labels.includes(label)) e.labels.push(label);
    if (alt && !e.alts.includes(alt)) e.alts.push(alt);
  };
  // 1) kafelki stron GEO (bez FMP i bez kafelków sektorów ACC)
  (vacs?.geo_pages || []).forEach((pg) => (pg.keys || []).forEach((k) => {
    if (k && !k.fmp && !k.sector && k.callsign) add(k.callsign, joinSplit(k.label), k.alt);
  }));
  // 2) sąsiedzi z profilu VACS, których nie ma na kafelkach
  const tiles = new Set(by.keys());
  (vacs?.neighbours || []).forEach((n) => {
    const cs = String(n?.ese?.callsign || "").toUpperCase();
    if (!n.fmp && cs && !tiles.has(cs)) add(cs, n.label, n.alt);
  });
  by.forEach((e, k) => { LABELS.set(k, mergeLabels(e.labels)); if (e.alts.length) ALTS.set(k, e.alts.join("/")); });
}

// /api/nav/vacs raz na sesję (etykiety displayName); po błędzie kolejne wywołanie próbuje znowu
let jsonP = null;
export const loadVacsJson = () => (jsonP ||= api("/api/nav/vacs").catch((e) => { jsonP = null; throw e; }));
export const loadDisplayNames = () => (namesP ||= loadVacsJson().then((d) => { setDisplayNames(d); return true; })
  .catch(() => { namesP = null; return false; }));
export const displayName = (cs) => LABELS.get(String(cs || "").toUpperCase()) || posName(cs);
// dopisek wysokości z VACS (np. "+365", "335-365") albo ""
export const displayAlt = (cs) => ALTS.get(String(cs || "").toUpperCase()) || "";
