import { esc, h } from "../api.js";

// HOME ("?" w menu, strona startowa): tylko powitanie zalogowanego kontrolera (imię i nazwisko z CID z /auth/me).
// Bez logowania (tryb lokalny) samo "Witaj".
export function greeting(me) {
  const u = me?.user;
  if (!u || (!u.name && !u.cid)) return "Witaj";
  if (!u.name) return `Witaj, ${u.cid}`;
  return u.cid ? `Witaj, ${u.name} (${u.cid})` : `Witaj, ${u.name}`;
}

export default {
  mount(root, ctx) {
    root.append(h(`<div class="pane home-pane"><h1 class="home-hello">${esc(greeting(ctx.me))}</h1></div>`));
  },
};
