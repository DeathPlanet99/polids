import { esc, h } from "../api.js";

// Zakładka-zaślepka: jedna krótka informacja na środku ekranu (AUP, OTHER).
export const soon = (label, text) => ({
  mount(root) {
    root.append(h(`<div class="pane soon-pane"><p class="soon-note"><b>${esc(label)}</b> – ${esc(text)}</p></div>`));
  },
});
