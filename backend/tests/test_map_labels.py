"""MAP: etykieta stanowiska z częstotliwością nie wpada pod nazwę lotniska, plakietkę ani inną etykietę
(nudge z frontend/js/coverage.js: najbliższe wolne miejsce w obrębie własnego obszaru, inaczej najmniej zasłonięte,
a ostatecznie tuż obok obszaru, gdy tam wyraźnie mniej zasłonięta); przeszkody po animowanym zoomie tam, dokąd
dojeżdżają (settledRect)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[2] / "frontend" / "js"

NODE_SCRIPT = r"""
const { nudge, settledRect } = await import(process.argv[2]);
const LBL = { x: 0, y: 0, w: 68, h: 30 };  // "EPWW DTC / 125.340" w pikselach, środek w (0, 0)
const over = (r, o) => Math.min(r.dx + 34, o.x + o.w / 2) > Math.max(r.dx - 34, o.x - o.w / 2)
  && Math.min(r.dy + 15, o.y + o.h / 2) > Math.max(r.dy - 15, o.y - o.h / 2);
const out = {};
// nic nie zasłania: bez zmian
out.free = nudge(LBL, [{ x: 200, y: 0, w: 40, h: 14 }]);
// nazwa lotniska (EPZG) na prawej części dolnej linijki (częstotliwość): najbliższe wolne miejsce (w górę o 18 px)
const name = { x: 25, y: 8, w: 40, h: 14 };
out.name = nudge(LBL, [name]);
out.nameOver = over(out.name, name);
// w górę nie wolno (tam granica własnego obszaru): w dół, pod nazwę
out.down = nudge(LBL, [name], [{ ok: (dx, dy) => dy > 0 }]);
out.downOver = over(out.down, name);
// nigdzie wolnego miejsca w obszarze (ok tylko blisko): najmniej zasłonięte z dozwolonych, ok zawsze spełnione
const wall = [{ x: 0, y: 0, w: 400, h: 400, k: 1 }, { x: -30, y: 0, w: 20, h: 30, k: 3 }];
const near = (dx, dy) => Math.abs(dx) <= 20 && Math.abs(dy) <= 10;
out.wall = nudge(LBL, wall, [{ ok: near }]);
out.wallOk = near(out.wall.dx, out.wall.dy);
// na miejscu plakietka; w górę cała wcześniej postawiona etykieta (waga 3), w dół część nazwy lotniska (waga 1):
// mniejsze zło to nazwa, nie inna etykieta
const badge = { x: 0, y: 0, w: 68, h: 30 }, other = { x: 0, y: -36, w: 68, h: 30, k: 3 }, low = { x: 0, y: 46, w: 68, h: 30 };
out.prio = nudge(LBL, [badge, other, low], [{ ok: (dx, dy) => dx === 0 && Math.abs(dy) === 36 }]);
// sektor ACC przy zoomie 5 (EPWW C): w obszarze tylko miejsca pod inną etykietą (częstotliwość EPWW N zasłonięta),
// tuż obok obszaru wolno (ostatni poziom z karą 1/8 pola etykiety): etykieta wychodzi obok
const inArea = (dx, dy) => Math.abs(dx) <= 10 && Math.abs(dy) <= 9;
const lastTier = (ok) => [{ ok }, { ok }, { ok: () => true, rings: 3, add: (68 * 30) / 8 }];
const nb = { x: 0, y: 14, w: 68, h: 30, k: 3 };
out.out = nudge(LBL, [nb], lastTier(inArea));
out.outOver = over(out.out, nb);
// tylko styk z inną etykietą (bez zasłonięcia, w pasie marginesu): zostaje w obszarze
const touch = { x: 0, y: 31, w: 68, h: 30, k: 3 };
out.touch = nudge(LBL, [touch], lastTier((dx, dy) => !dx && !dy));
// kara przebita tylko wyraźnie: obok obszaru też zasłonięta prawie tak samo, więc zostaje na miejscu
const wide = { x: 0, y: 0, w: 400, h: 400, k: 1 };
out.same = nudge(LBL, [wide], lastTier((dx, dy) => !dx && !dy));
// plakietka po animowanym zoomie jeszcze dojeżdża (przejście CSS): prostokąt z ekranu w trakcie, z błędami float rzędu
// 1e-4 px, przesunięty o resztę drogi. Po dojechaniu styka się z etykietą dokładnie w pasie marginesu (lewa krawędź 34 + 2)
const box = (r) => ({ x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2, w: r.width, h: r.height });
const end = { left: 36, top: -7, right: 76.40625, bottom: 7, width: 40.40625, height: 14 };
const to = { x: 120, y: 80 }, now = { x: 107.6543211, y: 84.321 }, e = 3e-4;  // jeszcze 12,35 px w prawo, 4,32 px w górę
const mid = { left: end.left - 12.3456789 - e, top: end.top + 4.321 + e, width: end.width + e, height: end.height - e };
mid.right = mid.left + mid.width;
mid.bottom = mid.top + mid.height;
out.still = settledRect(end, to, to) === end;
out.settled = settledRect(mid, now, to);
out.settledLbl = nudge(LBL, [box(out.settled)]);
// bez siatki 1/64 px: ułamek piksela błędu robi ze styku zasłonięcie i etykieta ucieka (inaczej niż po dojechaniu)
const raw = { left: mid.left + 12.3456789, top: mid.top - 4.321, width: mid.width, height: mid.height };
raw.right = raw.left + raw.width;
raw.bottom = raw.top + raw.height;
out.rawLbl = nudge(LBL, [box(raw)]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("labels")
    (d / "coverage.mjs").write_text((JS / "coverage.js").read_text("utf-8"), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "coverage.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_label_stays_when_free(js):
    assert js["free"] == {"dx": 0, "dy": 0, "c": 0}


def test_label_leaves_airport_name(js):
    # EPWW DTC pod EPZG (zrzut 01-map-fl300): etykieta przesunięta o tyle, żeby nazwa jej nie zasłaniała, najbliżej
    assert js["name"] == {"dx": 0, "dy": -18, "c": 0} and not js["nameOver"]


def test_label_respects_own_area(js):
    # przesunięcie tylko tam, gdzie etykieta zostaje we własnym obszarze (tu: w dół, pod nazwę)
    assert js["down"] == {"dx": 0, "dy": 36, "c": 0} and not js["downOver"]


def test_label_least_covered_when_no_room(js):
    # brak wolnego miejsca: najmniejsze ważone zasłonięcie wśród miejsc dozwolonych
    assert js["wallOk"] and js["wall"]["c"] > 0 and js["wall"]["dx"] > 0


def test_label_not_on_other_label(js):
    assert (js["prio"]["dx"], js["prio"]["dy"]) == (0, 36)


def test_label_leaves_crowded_area(js):
    # w obszarze wszędzie pod inną etykietą: tuż obok obszaru, bez zasłonięcia (zoom 5, EPWW C nad EPWW N)
    assert js["out"]["c"] > 0 and not js["outOver"]
    assert (js["out"]["dx"], js["out"]["dy"]) != (0, 0)


def test_label_touching_stays_in_area(js):
    # styk z inną etykietą (margines 2 px) to mniej niż kara za wyjście z obszaru
    assert (js["touch"]["dx"], js["touch"]["dy"]) == (0, 0) and 0 < js["touch"]["c"] < 68 * 30 / 8
    assert (js["same"]["dx"], js["same"]["dy"]) == (0, 0)


def test_obstacle_where_it_settles_after_zoom(js):
    # plakietka w trakcie przejścia po animowanym zoomie: dokładnie tam, gdzie stanie; na miejscu prostokąt bez zmian
    assert js["still"]
    assert js["settled"] == {"left": 36, "top": -7, "right": 76.40625, "bottom": 7, "width": 40.40625, "height": 14}
    # styk w pasie marginesu zostaje stykiem: etykieta na miejscu, jak przy plakietce, która już dojechała
    assert js["settledLbl"] == {"dx": 0, "dy": 0, "c": 0}
    assert (js["rawLbl"]["dx"], js["rawLbl"]["dy"]) != (0, 0)
