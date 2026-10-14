"""MAP: etykieta stanowiska z częstotliwością nie wpada pod nazwę lotniska, plakietkę ani inną etykietę
(nudge z frontend/js/coverage.js: najbliższe wolne miejsce w obrębie własnego obszaru, inaczej najmniej zasłonięte)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[2] / "frontend" / "js"

NODE_SCRIPT = r"""
const { nudge } = await import(process.argv[2]);
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
out.down = nudge(LBL, [name], (dx, dy) => dy > 0);
out.downOver = over(out.down, name);
// nigdzie wolnego miejsca w obszarze (ok tylko blisko): najmniej zasłonięte z dozwolonych, ok zawsze spełnione
const wall = [{ x: 0, y: 0, w: 400, h: 400, k: 1 }, { x: -30, y: 0, w: 20, h: 30, k: 3 }];
const near = (dx, dy) => Math.abs(dx) <= 20 && Math.abs(dy) <= 10;
out.wall = nudge(LBL, wall, near);
out.wallOk = near(out.wall.dx, out.wall.dy);
// na miejscu plakietka; w górę cała wcześniej postawiona etykieta (waga 3), w dół część nazwy lotniska (waga 1):
// mniejsze zło to nazwa, nie inna etykieta
const badge = { x: 0, y: 0, w: 68, h: 30 }, other = { x: 0, y: -36, w: 68, h: 30, k: 3 }, low = { x: 0, y: 46, w: 68, h: 30 };
out.prio = nudge(LBL, [badge, other, low], (dx, dy) => dx === 0 && Math.abs(dy) === 36);
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
