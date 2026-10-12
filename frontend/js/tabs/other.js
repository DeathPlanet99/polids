import { remember, subtabs } from "../api.js";
import { mountDocs } from "../docs.js";

// OTHER: podmenu jak w METEO, na razie z jedną podzakładką DOCS z PDF-ami z data/docs/.
// Adres "#other/docs/<plik w data/docs>" (bez pliku: pierwszy dokument); stare "#docs/<plik>" przekierowuje app.js.
export default {
  mount(root, ctx) {
    let docs = null, file = "";   // file: dokument z adresu albo kliknięty na liście ("" = pierwszy)
    const argOf = () => (file ? `docs/${file}` : "docs");
    // kolejna podzakładka: następny wpis w subtabs(), a w activate() i argOf() wybór po jej id
    subtabs(root, [
      // ponowne kliknięcie DOCS wczytuje listę od nowa, z tym samym dokumentem
      { id: "docs", label: "DOCS", fill: true, render: (pane) => {
        docs = mountDocs(pane, ctx, { start: file, onPick: (f) => { file = f; remember("other", argOf()); } });
      } },
    ], { arg: argOf });
    return {
      // arg undefined (przycisk menu): zostaje otwarty dokument; "docs/LOA/plik.pdf" → DOCS z tym plikiem
      activate(arg) {
        if (arg !== undefined) {
          file = arg.startsWith("docs/") ? arg.slice("docs/".length) : "";
          docs.pick(file);
        }
        remember("other", argOf());   // menu: bieżący stan do adresu (bez nowego kroku)
      },
    };
  },
};
