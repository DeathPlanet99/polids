"""Nazwy klas i id w interfejsie nie mogą wyglądać jak reklama.

Blokery reklam (uBlock, AdBlock, Opera, Brave) mają ogólne reguły z EasyList, np. ##.ad-grid i ##.ad-col, które chowają
element o takiej klasie na każdej stronie. Na polids.pl schowały całą siatkę AERODROME › PRZEGLĄD (0.11.0), więc lotnisko
to "aero", nie "ad".
"""
import re

from backend.app.config import ROOT_DIR

FRONTEND = ROOT_DIR / "frontend"
# słowa w nazwach klas, id i atrybutów data-*, po których blokery rozpoznają reklamy
AD_WORDS = {"ad", "ads", "adv", "advert", "adverts", "advertisement", "advertising", "adbox", "adslot", "adunit",
            "adsense", "banner", "banners", "sponsor", "sponsored", "promo", "promoted"}
# początek wartości z klasami: atrybut class/id w szablonie, className (także opcja Leaflet), L.DomUtil.create,
# setAttribute("class", ...); samo "id:" w obiektach JS (np. podzakładki RADIO) to nie id elementu
VALUE_START = re.compile(r"""(?:\b(?:class|id)\s*=\s*\\?|\bclassName\s*[=:]\s*\\?|DomUtil\.create\(\s*["'][^"']*["']\s*,\s*"""
                         r"""|setAttribute\(\s*["']class["']\s*,\s*)(["'`])""")
# selektory: querySelector, closest, matches i lokalne skróty $(".x") / $$(".x")
SELECTOR = re.compile(r"""(?:querySelector(?:All)?|closest|matches|\$\$?)\(\s*["'`]([^"'`]+)""")


def _value(text, i, quote):
    """Wartość od pozycji i do zamykającego cudzysłowu: stały tekst plus napisy z wyrażeń ${...}
    (np. class="ov-aero${open ? " open" : ""}" -> "ov-aero  open ")."""
    out, depth = [], 0
    while i < len(text):
        c = text[i]
        if depth == 0:
            if c == "\\" and text[i + 1:i + 2] == quote:
                break                                   # class=\"...\" w napisie JS
            if c == quote:
                break
            if text.startswith("${", i):
                depth, i = 1, i + 2
                out.append(" ")
                continue
            out.append(c)
        else:
            if c in "\"'`":
                end = text.find(c, i + 1)
                if end < 0:
                    break
                out.append(" " + text[i + 1:end] + " ")
                i = end
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                out.append(" ")
        i += 1
    return "".join(out)


def _names():
    """(nazwa, plik) klas i id z CSS oraz klas, id i data-* z szablonów JS/HTML (bez bibliotek z vendor/)."""
    found = []
    for path in sorted(FRONTEND.rglob("*")):
        if "vendor" in path.parts or path.suffix not in (".css", ".js", ".html"):
            continue
        text = path.read_text("utf-8")
        rel = str(path.relative_to(FRONTEND))
        if path.suffix == ".css":
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            for selector in re.findall(r"([^{}]+)\{", text):
                if not selector.strip().startswith("@"):
                    found += [(n, rel) for n in re.findall(r"[.#](-?[A-Za-z_][\w-]*)", selector)]
            continue
        for m in VALUE_START.finditer(text):
            found += [(n, rel) for n in re.findall(r"-?[A-Za-z_][\w-]*", _value(text, m.end(), m.group(1)))]
        for args in re.findall(r"classList\.(?:add|remove|toggle|contains|replace)\(([^)]*)\)", text):
            found += [(n, rel) for n in re.findall(r"""["'`]([\w-]+)["'`]""", args)]
        for selector in SELECTOR.findall(text):
            found += [(n, rel) for n in re.findall(r"[.#](-?[A-Za-z_][\w-]*)", selector)]
        found += [(n, rel) for n in re.findall(r"\b(data-[\w-]+)=", text)]
    return found


def test_names_found():
    names = {n for n, _ in _names()}
    # pilnujemy, że parser coś widzi (inaczej test przechodziłby zawsze): klasy z CSS, z ${...} w szablonie,
    # z opcji className Leaflet i z lokalnego skrótu $(".x")
    assert {"aero-grid", "aero-col", "aero-pane", "data-icao", "metar-head", "open", "ctl", "atcicon", "aeros"} <= names


def test_value_parser():
    t = 'x = `<div class="row${open ? " ad-open" : ""}" id="a">`; L.divIcon({ className: "ad-marker" });'
    names = re.findall(r"-?[A-Za-z_][\w-]*", " ".join(_value(t, m.end(), m.group(1)) for m in VALUE_START.finditer(t)))
    assert {"row", "ad-open", "a", "ad-marker"} <= set(names)


def test_no_ad_like_names():
    bad = sorted({f"{name} ({rel})" for name, rel in _names()
                  if AD_WORDS & set(re.split(r"[-_]+", name.lower().removeprefix("data-")))})
    assert not bad, "nazwy chowane przez blokery reklam: " + ", ".join(bad)
