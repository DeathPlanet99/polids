"""MAP i RADIO (runda 12): suma wycinków jednego stanowiska (frontend/js/dissolve.js z polygon-clipping z vendor/).

Granica tylko tam, gdzie zmienia się obsługujący: bez linii między sektorami tego samego stanowiska, bez szczelin
i klinów po upraszczaniu granic przy imporcie; prawdziwe dziury (TMA w sektorze ACC) zostają.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "js"
VENDOR = ROOT / "frontend" / "vendor" / "polygon-clipping"

LAT = 52.0  # szerokość jak w Polsce (odległości w dissolve.js: długość × cos(szerokości))


def poly(*pts):
    """Polygon GeoJSON z punktów (x, y), y liczone od szerokości LAT."""
    ring = [[x, y + LAT] for x, y in pts]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def box(x0, y0, x1, y1):
    return poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))


GEOMS = {
    # dwa sąsiednie sektory: jeden prostokąt bez granicy w środku
    "adjacent": [box(0, 0, 1, 1), box(1, 0, 2, 1)],
    # klin od krawędzi: wspólna granica zapisana w drugim sektorze 0,0008° obok (od góry do połowy wysokości)
    "slit": [box(0, 0, 1, 1), poly((1, 0), (2, 0), (2, 1), (1.0008, 1), (1.0008, 0.5), (1, 0.49))],
    # szczelina zamknięta z obu stron (dziura szeroka na kilkadziesiąt metrów) i prawdziwa dziura 0,2° × 0,2°
    "holes": [box(0, -0.5, 2, 0), box(0, 1, 2, 1.5), box(0, 0, 1, 1), box(1.0005, 0, 2, 1),
              box(2, -0.5, 3, 0.4), box(2, 0.6, 3, 1.5), box(2, 0.4, 2.4, 0.6), box(2.6, 0.4, 3, 0.6)],
    # prawdziwe wcięcie (szerokie na ok. 700 m) zostaje, okruch obok znika
    "notch": [poly((0, 0), (2, 0), (2, 1), (1.01, 1), (1.01, 0.5), (1, 0.5), (1, 1), (0, 1)), box(5, 5, 5.001, 5.001)],
    # MultiPolygon i pierścienie zbyt krótkie (pomijane)
    "multi": [{"type": "MultiPolygon", "coordinates": [box(0, 0, 1, 1)["coordinates"], box(3, 0, 4, 1)["coordinates"]]},
              {"type": "Polygon", "coordinates": [[[0, LAT], [1, LAT + 1]]]}, None],
}

NODE_SCRIPT = r"""
import { createRequire } from "node:module";
const pc = createRequire(import.meta.url)(process.argv[3]);
const d = await import(process.argv[2]);
const geoms = JSON.parse(process.argv[4]);
const warns = [];
console.warn = (...a) => warns.push(a.join(" "));
const out = { lib: typeof pc.union };
for (const [k, gs] of Object.entries(geoms)) out[k] = d.unionOf(gs, pc);
// dissolve: w pamięci wg klucza, błąd sumy i brak biblioteki dają null (rysujemy same wypełnienia)
const a = d.dissolve("EPWW_N_CTR|1,2", geoms.adjacent, pc);
out.cached = d.dissolve("EPWW_N_CTR|1,2", geoms.slit, pc) === a;
out.area = a.area; out.type = a.geometry.type;
out.boom = d.dissolve("X|1", geoms.adjacent, { union: () => { throw new Error("boom"); } });
out.nolib = d.dissolve("X|2", geoms.adjacent, {});
out.empty = d.dissolve("X|3", [], pc);
out.warns = warns;
// punkt etykiety: poza dziurą i w ramce within
const ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], hole = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8], [0.2, 0.2]];
const sq = { type: "Polygon", coordinates: [ring.map(([x, y]) => [x, y + 52]), hole.map(([x, y]) => [x, y + 52])] };
out.pt = d.pointIn(sq);
out.ptWithin = d.pointIn(sq, [0.85, 52, 1, 53]);
out.ptOut = d.pointIn(sq, [5, 5, 6, 6]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("dissolve")
    (d / "dissolve.mjs").write_text((JS / "dissolve.js").read_text("utf-8"), "utf-8")
    # UMD z vendor/ jako CommonJS (kopia .cjs: działa niezależnie od package.json wyżej w drzewie)
    (d / "pc.cjs").write_text((VENDOR / "polygon-clipping.umd.min.js").read_text("utf-8"), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "dissolve.mjs").as_uri(), str(d / "pc.cjs"), json.dumps(GEOMS)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def corners(poly):
    """Wierzchołki pierścienia zewnętrznego bez zamknięcia, po zaokrągleniu i bez punktów na prostej."""
    ring = [tuple(round(c, 6) for c in p) for p in poly[0][:-1]]
    keep = []
    for i, (x, y) in enumerate(ring):
        (ax, ay), (bx, by) = ring[i - 1], ring[(i + 1) % len(ring)]
        if abs((x - ax) * (by - ay) - (y - ay) * (bx - ax)) > 1e-12:
            keep.append((x, y))
    return sorted(keep)


def test_vendor_library():
    # polygon-clipping 0.15.7 z licencją MIT (splaytree i robust-predicates są w pliku UMD)
    src = (VENDOR / "polygon-clipping.umd.min.js").read_text("utf-8")
    assert "polygonClipping" in src and "MIT" in (VENDOR / "LICENSE.md").read_text("utf-8")
    html = (ROOT / "frontend" / "index.html").read_text("utf-8")
    assert html.index("vendor/polygon-clipping/polygon-clipping.umd.min.js") < html.index("js/app.js")


def test_adjacent_sectors_one_outline(js):
    assert js["lib"] == "function"
    # jeden wielokąt bez dziur, narożniki tylko zewnętrzne (granica między sektorami znika)
    assert len(js["adjacent"]) == 1 and len(js["adjacent"][0]) == 1
    assert corners(js["adjacent"][0]) == [(0, 52), (0, 53), (2, 52), (2, 53)]


def test_slit_from_edge_removed(js):
    # klin wzdłuż dawnej granicy (otwarty od krawędzi, ok. 50 m) wycięty: żaden wierzchołek nie zostaje w środku
    assert len(js["slit"]) == 1 and len(js["slit"][0]) == 1
    inner = [p for p in js["slit"][0][0] if 0.01 < p[0] < 1.99 and 52.01 < p[1] < 52.99]
    assert inner == []


def test_sliver_hole_removed_real_hole_kept(js):
    # szczelina zamknięta z obu stron znika, dziura 0,2° × 0,2° (jak TMA w sektorze) zostaje
    assert len(js["holes"]) == 1
    outer, *holes = js["holes"][0]
    assert len(holes) == 1
    xs, ys = [p[0] for p in holes[0]], [p[1] for p in holes[0]]
    assert (min(xs), max(xs)) == pytest.approx((2.4, 2.6)) and (min(ys), max(ys)) == pytest.approx((52.4, 52.6))


def test_real_notch_kept_crumbs_dropped(js):
    # wcięcie szerokie na ok. 700 m zostaje (to nie szczelina), okruch 0,001° × 0,001° znika
    assert len(js["notch"]) == 1
    assert any(p == pytest.approx([1.01, 52.5]) for p in js["notch"][0][0])


def test_multipolygon_parts(js):
    # MultiPolygon na wejściu, pierścienie < 4 punktów i brak geometrii pomijane; rozłączne części zostają osobno
    assert len(js["multi"]) == 2


def test_dissolve_cache_and_fallback(js):
    # ten sam klucz: wynik z pamięci; błąd sumy i brak biblioteki: null (rysujemy same wypełnienia wycinków)
    assert js["cached"] is True and js["type"] == "MultiPolygon"
    assert js["area"] == pytest.approx(2 * 0.6157, rel=1e-3)
    assert js["boom"] is None and js["nolib"] is None and js["empty"] is None
    assert any("boom" in w for w in js["warns"]) and any("polygon-clipping" in w for w in js["warns"])


def test_label_point_avoids_hole(js):
    # etykieta poza dziurą (np. TMA obsługiwana przez APP) i w zadanej ramce; ramka poza wielokątem: brak punktu
    lat, lon = js["pt"]
    assert 52 < lat < 53 and 0 < lon < 1 and not (52.2 < lat < 52.8 and 0.2 < lon < 0.8)
    lat, lon = js["ptWithin"]
    assert lon >= 0.85
    assert js["ptOut"] is None
