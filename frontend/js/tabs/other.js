import { remember, subtabs } from "../api.js";
import { mountDocs } from "../docs.js";

// OTHER: podmenu jak w METEO, pierwsza (na razie jedyna) podzakładka DOCS z PDF-ami z data/docs/.
// Adres "#other/docs/<plik w data/docs>" (bez pliku: pierwszy dokument); stare "#docs/<plik>" przekierowuje app.js.
const SUBS = ["docs"];

export default {
  mount(root, ctx) {
    let docs = null, file = "";   // file: dokument z adresu albo kliknięty na liście ("" = pierwszy)
    const argOf = (id) => (id === "docs" && file ? `docs/${file}` : id);
    const sub = subtabs(root, [
      // ponowne kliknięcie DOCS wczytuje listę od nowa, z tym samym dokumentem
      { id: "docs", label: "DOCS", fill: true, render: (pane) => {
        docs = mountDocs(pane, ctx, { start: file, onPick: (f) => { file = f; remember("other", argOf("docs")); } });
      } },
    ], { arg: argOf });
    return {
      // arg undefined (przycisk menu): zostaje podzakładka i otwarty dokument; "docs/LOA/plik.pdf" → DOCS z tym plikiem
      activate(arg) {
        if (arg !== undefined) {
          const [id, ...rest] = arg.split("/");
          if (SUBS.includes(id) && sub.current() !== id) sub.show(id);
          if (sub.current() === "docs") { file = id === "docs" ? rest.join("/") : ""; docs.pick(file); }
        }
        remember("other", argOf(sub.current()));   // menu: bieżący stan do adresu (bez nowego kroku)
      },
    };
  },
};
