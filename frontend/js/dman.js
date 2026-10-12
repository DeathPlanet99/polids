// AERODROME › RUCH: DMAN / AMAN. Na razie widok pasków (strips.js) bez zmian; ten sam interfejs co mountStrips:
// mountDman(el, icao, { onUpdate }) → { resume(), destroy() }.
import { mountStrips } from "./strips.js";

export function mountDman(el, icao, opts = {}) {
  return mountStrips(el, icao, opts);
}
