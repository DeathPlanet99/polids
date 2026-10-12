// Okres ważności NOTAM-u względem teraz (pola B i C): jeden opis dla SYT OP i listy NOTAM w AERODROME › PRZEGLĄD.
// {cls: active | future | expired | "", txt}; cls to klasa wiersza .notam (kolor w style.css i sytop.css).
const dm = (iso) => { const d = new Date(iso); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")} ${d.toISOString().slice(11, 16)}Z`; };
function span(ms) {
  const min = Math.round(Math.abs(ms) / 60000);
  if (min < 1) return "<1 min";
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
  if (s) return { cls: "active", txt: `AKTYWNY od ${dm(n.start)}` };
  // bez B) i C) (nieznany format, uszkodzone pola): bez zielonego "aktywny", żółty jak domyślny NOTAM
  return { cls: "", txt: "ważność nieznana (brak pól B/C)" };
}
