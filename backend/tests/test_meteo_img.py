"""METEO (runda 10): SAT PL i SAT EUR (Windy z własnym środkiem i zoomem), TURB (8 klatek IMGW co 3 h, odtwarzacz)
i SIGWX (mapa IMGW odświeżana co 15 min). Obrazy IMGW wczytuje przeglądarka użytkownika, nie serwer PolIDS."""
import json
import re
import shutil
import subprocess

import pytest

from backend.app.config import ROOT_DIR

FRONTEND = ROOT_DIR / "frontend"
METEO = (FRONTEND / "js" / "tabs" / "meteo.js").read_text("utf-8")
CSS = (FRONTEND / "css" / "meteoimg.css").read_text("utf-8")
TURB_BASE = "https://aviation-api.imgw.pl/image/TURBULENCJE/gify_LOTN07_TURB_DWD_statkirejsowe/Turb11000."
# adres RADAR sprzed rundy 10 (bez zmian)
RADAR_URL = ("https://embed.windy.com/embed2.html?lat=52.0&lon=19.3&detailLat=52.17&detailLon=20.97&zoom=6&level=surface"
             "&overlay=radar&product=ecmwf&menu=&message=true&marker=&calendar=now&pressure=true&type=map&location=coordinates"
             "&detail=&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1")

NODE_SCRIPT = r"""
const m = await import(process.argv[2]);
const T = Date.UTC(2026, 9, 12, 12, 22, 5);
const P = m.IMG_REFRESH;
const S = (s) => s.split("");   // "olle" -> ["o","l","l","e"]; o = ok, l = wczytywanie, e = błąd
const st = (s) => S(s).map((c) => ({ o: "ok", l: "load", e: "err" }[c]));
console.log(JSON.stringify({
  frames: m.turbFrames(),
  steps: m.TURB_STEPS, ms: m.TURB_MS, refresh: P, sigwx: m.SIGWX_URL,
  windy: Object.fromEntries(Object.entries(m.WINDY_VIEWS).map(([k, v]) => [k, m.windyEmbedUrl(v)])),
  stamp: [m.stamp(T, P), m.stamp(T + 7 * 60000, P), m.stamp(T + 8 * 60000, P), m.stamp(T, 1000)],
  bust: [m.bust(m.SIGWX_URL, "20261012121500"), m.bust("https://x/y?a=1", "1")],
  next: [m.nextFrame(st("oooooooo"), 7), m.nextFrame(st("oeeooooo"), 0), m.nextFrame(st("oooooooe"), 6),
         m.nextFrame(st("eeeeeeee"), 3), m.nextFrame(st("oooooooo"), 0, -1), m.nextFrame(st("eooooooe"), 1, -1)],
  play: [m.playStep(st("oooooooo"), 0), m.playStep(st("oooooooo"), 7), m.playStep(st("olllllll"), 0),
         m.playStep(st("oeolllll"), 0), m.playStep(st("eeeeeeee"), 0), m.playStep(st("eeeeoeee"), 4),
         m.playStep(st("leeeeeee"), 0), m.playStep(st("oeeeeeee"), 0), m.playStep(st("eeoeeeeo"), 7)],
}));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("brak Node.js")
    d = tmp_path_factory.mktemp("meteo")
    (d / "api.mjs").write_text((FRONTEND / "js" / "api.js").read_text("utf-8"), "utf-8")
    (d / "meteo.mjs").write_text(METEO.replace('"../api.js"', '"./api.mjs"'), "utf-8")
    script = d / "run.mjs"
    script.write_text(NODE_SCRIPT, "utf-8")
    r = subprocess.run([node, str(script), (d / "meteo.mjs").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_submenu_order():
    """Dotychczasowe podzakładki bez zmian, potem SAT PL, SAT EUR, TURB, SIGWX."""
    mount = METEO[METEO.index("export default {"):]
    items = re.findall(r'\{ id: "([^"]+)", label: "([^"]+)"', mount)
    assert items == [("metar-pl", "METAR PL"), ("metar-mil", "METAR MIL"), ("metar-intl", "METAR INTL"), ("taf-pl", "TAF PL"),
                     ("taf-intl", "TAF INTL"), ("qnh", "QNH"), ("wind", "WIND"), ("radar", "RADAR"), ("sat-pl", "SAT PL"),
                     ("sat-eur", "SAT EUR"), ("turb", "TURB"), ("sigwx", "SIGWX")]
    # id podzakładek są częścią adresu "#meteo/<id>": bez "/"
    assert all("/" not in i for i, _ in items)


def test_windy_views(js):
    w = js["windy"]
    assert w["radar"] == RADAR_URL
    assert "overlay=satellite" in w["sat-pl"] and "lat=52.0&lon=19.0&" in w["sat-pl"] and "&zoom=6&" in w["sat-pl"]
    assert "overlay=satellite" in w["sat-eur"] and "lat=50.0&lon=12.0&" in w["sat-eur"] and "&zoom=4&" in w["sat-eur"]
    for url in w.values():
        assert url.startswith("https://embed.windy.com/embed2.html?") and "metricWind=kt" in url


def test_turb_frames(js):
    assert js["steps"] == ["000", "003", "006", "009", "012", "015", "018", "021"]
    assert [f["label"] for f in js["frames"]] == ["+0 h", "+3 h", "+6 h", "+9 h", "+12 h", "+15 h", "+18 h", "+21 h"]
    assert [f["url"] for f in js["frames"]] == [f"{TURB_BASE}{s}.gif" for s in js["steps"]]
    assert js["ms"] == 1500


def test_sigwx_and_refresh(js):
    assert js["sigwx"] == "https://aviation-api.imgw.pl/image/significant/pl"
    assert js["refresh"] == 15 * 60000
    # znacznik czasu: pełny kwadrans UTC, ten sam w obrębie 15 min; "Wczytaj ponownie" z dokładnością do sekundy
    assert js["stamp"] == ["20261012121500", "20261012121500", "20261012123000", "20261012122205"]
    assert js["bust"] == ["https://aviation-api.imgw.pl/image/significant/pl?t=20261012121500", "https://x/y?a=1&t=1"]


def test_frame_order_logic(js):
    # następna klatka: po kolei z zawinięciem, klatki z błędem pominięte, null gdy wszystkie pozostałe z błędem
    assert js["next"] == [0, 3, 0, None, 7, 6]
    # krok animacji: następna wczytana klatka; czeka (ta sama), gdy następna się wczytuje; -1 = stop (nic się nie wczytało)
    assert js["play"] == [1, 0, 0, 2, -1, 4, 0, 0, 2]


def test_images_loaded_by_browser():
    """Obrazy IMGW bezpośrednio z aviation-api.imgw.pl (bez pośrednictwa serwera PolIDS), bez nagłówka Referer."""
    assert METEO.count("referrerPolicy = \"no-referrer\"") == 1 and "const pre = mk(i);" in METEO
    assert "aviation-api" not in "".join(p.read_text("utf-8").lower() for p in (ROOT_DIR / "backend" / "app").rglob("*.py"))
    assert not re.search(r"api\([^)]*imgw", METEO, re.I)
    # błąd klatki: odnośnik do obrazu w nowej karcie; obraz dopasowany do okna
    assert "otwórz w nowej karcie ↗</a>" in METEO and 'rel="noopener noreferrer"' in METEO
    assert "object-fit: contain" in CSS
    # timery odtwarzacza i odświeżania zatrzymywane przy zamknięciu podzakładki
    assert "return { destroy: () => { dead = true; clearInterval(play); clearInterval(refreshTimer); } };" in METEO
    assert "setInterval(tick, TURB_MS)" in METEO


def test_no_glow():
    assert "text-shadow" not in CSS and "glow" not in CSS.lower().replace("bez poświaty", "")
