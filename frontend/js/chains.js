// Kolejność przejmowania TMA, CTR i innych wycinków spoza sektorów ACC na MAP (RADIO od 0.12 liczy ją na serwerze:
// services/radioboard.py, GET /api/radio/board).
// Stanowiska lotniskowe i zbliżania z listy OWNER pliku .ese (APP, DEP, TWR…) w kolejności z pliku, a w miejscu ACC:
// dla TMA łańcuch top-down z /api/nav/ownership (tma_topdown, tabela om.plvacc.pl), gdy prefiks wycinka pasuje,
// a dla pozostałych wycinków ACC w kolejności z listy OWNER .ese.

// stanowisko ACC/FIS EPWW (EPWW_C_CTR, EPWW_I_FSS…) – w łańcuchu TMA zastępowane kolejnością top-down
export const isAccCallsign = (cs) => /^EPWW_[A-Z0-9_]*(CTR|FSS)$/.test(String(cs || "").toUpperCase());

// wpis tma_topdown pasujący do nazwy wycinka: {name: "TMA Warszawa", chain: [...], ese_prefixes: [...]} albo null
export function tmaTopdown(sliceName, own) {
  const hit = Object.entries(own?.tma_topdown || {})
    .find(([, v]) => (v.ese_prefixes || []).some((p) => String(sliceName || "").startsWith(p)));
  return hit ? { name: hit[0], ...hit[1] } : null;
}

// owners: znaki stanowisk z listy OWNER pliku .ese w kolejności (owner_callsigns z /api/nav/slices).
// Kolejność z pliku jest zachowana: stanowiska przed pierwszym ACC (local), potem ACC (dla TMA z tabeli om) i stanowiska,
// które plik stawia za ACC (np. CTA07: EPZG_TWR na końcu) – te dwie ostatnie grupy razem w `acc`.
// Wynik: {chain: [znaki], local: [APP/TWR… przed ACC], acc: [ACC i to, co po nich], src: "om" | "ese", tma: nazwa TMA z tabeli om albo null}
export function airspaceChain(sliceName, owners, own) {
  const list = (owners || []).filter(Boolean);
  const first = list.findIndex(isAccCallsign);
  const local = first < 0 ? list : list.slice(0, first);
  const rest = first < 0 ? [] : list.slice(first);
  const tail = rest.filter((cs) => !isAccCallsign(cs));
  const t = tmaTopdown(sliceName, own);
  const accPart = t?.chain?.length ? t.chain : rest.filter(isAccCallsign);
  const acc = [...accPart, ...tail].filter((cs, i, a) => !local.includes(cs) && a.indexOf(cs) === i);
  return { chain: [...local, ...acc], local, acc, src: t ? "om" : "ese", tma: t?.name || null };
}
