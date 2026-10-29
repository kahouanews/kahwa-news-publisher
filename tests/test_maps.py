"""Map rules (owner 2026-10-29): normal world map with every border and every Arabic country name, including Morocco /
Algeria / UAE (map label layer only); Western Sahara a separate labelled shape; Israel + Palestine ONE undivided shape with
no label; no map focused on MAR / DZA / ARE / ISR / the Palestine-Israel shape; MENA countries get maps again."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kahwa_publisher import card, cards, geo  # noqa: E402
from kahwa_publisher.reels import story  # noqa: E402
from kahwa_publisher.reels.render import gate  # noqa: E402

ISRAEL = re.compile(r"israel|إسرائيل|اسرائيل|palestin|فلسطين", re.I)


def _world_js():
    s = (ROOT / "kahwa_publisher/reels/static/world.js").read_text()
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


def _both():
    return [("geojson", json.loads((ROOT / "assets/countries.geojson").read_text())["features"]),
            ("world.js", _world_js()["features"])]


@pytest.mark.parametrize("name,feats", _both())
def test_full_world_with_labels(name, feats):
    assert len(feats) > 170
    by = {f["properties"]["a"]: f for f in feats}
    for iso in ("TUN", "EGY", "ESP", "ITA", "TUR", "SAU", "LBY", "JOR", "LBN", "SYR", "MRT", "FRA"):
        assert geo.label_of(by[iso]), iso
    # Morocco / Algeria / UAE drawn AND labelled with their Arabic names (map label layer only)
    assert geo.label_of(by["MAR"]) == "المغرب"
    assert geo.label_of(by["DZA"]) == "الجزائر"
    assert geo.label_of(by["ARE"]) == "الإمارات"


@pytest.mark.parametrize("name,feats", _both())
def test_palestine_israel_one_unlabelled_shape(name, feats):
    pi = [f for f in feats if f["properties"]["a"] in ("ISR", "PSE", "PSX")]
    assert len(pi) == 1                                          # one feature, no Israel feature, no separate Palestine
    f = pi[0]
    assert f["properties"] == {"a": "PSE", "nl": 1}              # no name, no Arabic label, no label point
    assert geo.label_of(f) is None
    assert f["geometry"]["type"] == "Polygon" and len(f["geometry"]["coordinates"]) == 1   # outer ring only, no inner line
    lons = [x for x, _ in f["geometry"]["coordinates"][0]]
    lats = [y for _, y in f["geometry"]["coordinates"][0]]
    assert min(lons) < 34.6 and max(lons) > 35.4 and min(lats) < 30 and max(lats) > 32.9   # covers both areas
    # no label anywhere says Israel / Palestine
    assert not any(ISRAEL.search(str(geo.label_of(x) or "")) for x in feats)
    assert not any(ISRAEL.search(json.dumps(x["properties"], ensure_ascii=False)) for x in feats)


@pytest.mark.parametrize("name,feats", _both())
def test_western_sahara_separate_from_morocco(name, feats):
    by = {f["properties"]["a"]: f for f in feats}
    assert "ESH" in by and "MAR" in by and by["ESH"] is not by["MAR"]
    assert geo.label_of(by["ESH"]) == "الصحراء الغربية"
    mar = [(round(x, 1), round(y, 1)) for x, y in by["MAR"]["geometry"]["coordinates"][0]]
    esh = [(round(x, 1), round(y, 1)) for x, y in by["ESH"]["geometry"]["coordinates"][0]]
    assert max(y for _, y in mar) > 35 and min(y for _, y in esh) < 22        # Morocco north, Western Sahara south
    assert set(mar) & set(esh)                                                # shared border edge (drawn)


@pytest.mark.parametrize("country", ["Tunisia", "Egypt", "Spain", "Italy", "Turkey", "Saudi Arabia", "Libya", "Jordan",
                                     "Lebanon", "Mauritania", "Oman", "Qatar", "Syria", "France", "Brazil", "Japan"])
def test_card_maps_render_for_mena_and_world(country):
    res = card.map_image(country)
    assert res is not None and res[0].size == (card.W, card.PH)
    labels = dict(card.LAST_LABELS)
    assert labels and all(geo.label_of({"properties": {"a": iso, "ar": lab}}) for iso, lab in card.LAST_LABELS)
    assert "PSE" not in labels                                   # the Palestine/Israel shape never carries text
    assert not any(ISRAEL.search(lab) for lab in labels.values())


def test_card_maps_label_morocco_and_algeria_and_draw_western_sahara():
    card.map_image("Mauritania")
    drawn = set(card.LAST_DRAWN)
    assert {"MAR", "DZA", "ESH"} <= drawn
    labs = dict(card.LAST_LABELS)
    assert labs.get("DZA") == "الجزائر" or labs.get("MAR") == "المغرب"
    card.map_image("Lebanon")
    assert "PSE" in card.LAST_DRAWN and "PSE" not in dict(card.LAST_LABELS)


@pytest.mark.parametrize("country", ["Morocco", "Algeria", "United Arab Emirates", "UAE", "Israel", "Palestine"])
def test_no_map_focused_on_no_focus_territory(country):
    assert card.map_image(country) is None


def test_map_card_for_tunisia_uses_the_map(tmp_path):
    out = tmp_path / "c.jpg"
    r = cards.render_card({"card_type": "map", "headline": "عنوان تجريبي عن تونس", "title": "تونس", "kicker": "تونس",
                           "country_en": "Tunisia", "source": "وكالات"}, out)
    assert out.exists() and r["credit"].startswith("خريطة")


@pytest.mark.parametrize("iso", ["TUN", "EGY", "ESP", "ITA", "TUR", "SAU", "LBY", "JOR", "LBN", "MRT", "OMN",
                                 "BRA", "JPN", "IND", "USA", "CHN", "GBR", "DEU"])
def test_reel_map_view_ok(iso):
    lon, lat = story.centroid(iso)
    mv = story.map_view(iso, lon, lat)
    assert mv["ok"] and mv["s1"] > mv["s0"] > 0


@pytest.mark.parametrize("iso", ["MAR", "DZA", "ARE", "PSE"])
def test_reel_map_view_never_focuses_no_focus(iso):
    lon, lat = story.centroid(iso)
    assert story.map_view(iso, lon, lat) == {"ok": False}


def test_gate_rejects_map_focused_on_no_focus():
    s = {"id": "x", "headline": "عنوان", "location": {"country_iso3": "PSE", "map": {"ok": True, "lon": 35, "lat": 31.5, "s0": 1500,
                                                                                    "s1": 1800, "cy": 600}}, "media": {}}
    with pytest.raises(ValueError):
        gate(s)


def test_gate_accepts_tunisia_map():
    lon, lat = story.centroid("TUN")
    s = {"id": "x", "headline": "عنوان", "location": {"country_iso3": "TUN", "map": story.map_view("TUN", lon, lat)}, "media": {}}
    assert gate(s)


def test_kahwa_js_map_rules():
    js = (ROOT / "kahwa_publisher/reels/templates/kahwa.js").read_text()
    assert 'NO_FOCUS = ["MAR", "DZA", "ARE", "ISR", "PSE", "PSX"]' in js
    assert "!f.properties.nl" in js and "f.properties.ar" in js           # labels drawn, never on the nl shape
    assert "BANNED_MAP" not in js and ".filter((f) => !BANNED" not in js   # nothing removed from the map any more
    assert '"stroke": "#1C2232"' not in js and 'stroke: "#8A9AC0", "stroke-width": 2.4' in js    # borders clearly visible


@pytest.mark.parametrize("name,feats", _both())
def test_exterior_rings_clockwise_for_d3(name, feats):
    """d3-geo fills the complement of a counter-clockwise ring (the whole globe): every exterior ring must be clockwise."""
    def area2(r):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]))
    for f in feats:
        g = f["geometry"]
        for r in ([g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]):
            assert area2(r) < 0, f["properties"].get("a")


@pytest.mark.parametrize("fx", ["reel-map-tunisia", "reel-map-lebanon"])
def test_map_fixtures_pass_gate(fx):
    s = json.loads((ROOT / "tests/fixtures" / f"{fx}.json").read_text())
    assert s["location"]["map"]["ok"] and gate(s)


# ---------------------------------------------------------------- Western Sahara / Morocco (owner 2026-10-29:
# "I don't see Western Sahara separated from Morocco")
def _ring(feats, iso):
    return next(f for f in feats if f["properties"]["a"] == iso)["geometry"]["coordinates"][0]


@pytest.mark.parametrize("name,feats", _both())
def test_western_sahara_full_territory_border_at_27_40(name, feats):
    """Western Sahara = the whole territory (Atlantic coast 21N-27.7N), Morocco stops at the 27°40'N border."""
    esh, mar = _ring(feats, "ESH"), _ring(feats, "MAR")
    assert max(y for _, y in esh) == pytest.approx(27.656, abs=0.01)
    assert min(y for _, y in mar) == pytest.approx(27.656, abs=0.01)          # no Morocco south of the border
    assert min(x for x, _ in esh) < -17 and max(x for x, _ in esh) > -8.8     # coast to the Algeria tripoint
    assert card._pip(-14.0, 26.0, esh) and card._pip(-15.9, 23.7, esh) and card._pip(-13.0, 25.0, esh)   # coastal part
    assert not card._pip(-14.0, 26.0, mar) and card._pip(-7.0, 31.5, mar)
    border = [p for p in esh if abs(p[1] - 27.656) < 0.01 and -13.2 < p[0] < -8.6]
    assert min(x for x, _ in border) < -13.0 and max(x for x, _ in border) > -8.7   # shared straight border, coast -> tripoint
    by = {f["properties"]["a"]: f for f in feats}
    assert len([f for f in feats if f["properties"]["a"] == "ESH"]) == 1 and by["ESH"]["geometry"]["type"] == "Polygon"
    assert by["ESH"] is not by["MAR"]
    assert card._pip(*by["ESH"]["properties"]["l"], esh)                       # label point inside the full shape


def test_priority_anchors_inside_and_same_in_reels():
    feats = geo.features()
    for iso, pts in geo.PRIORITY_ANCHORS.items():
        for x, y in pts:
            assert card._pip(x, y, _ring(feats, iso)), (iso, x, y)
    js = (ROOT / "kahwa_publisher/reels/templates/kahwa.js").read_text()
    for iso, pts in geo.PRIORITY_ANCHORS.items():
        assert f"{iso}: [" + ", ".join(f"[{x}, {y}]" for x, y in pts) + "]" in js, iso
    assert 'PRIORITY_SPLIT = { ESH: ["الصحراء", "الغربية"] }' in js and geo.PRIORITY_SPLIT["ESH"] == ("الصحراء", "الغربية")
    assert "placePriority(boxes, fs);" in js and js.index("placePriority(boxes, fs);") < js.index("for (const { f, t } of LABS)")


def _visible_share(iso, frame, n=60):
    ring = _ring(geo.features(), iso)
    x0, x1, y0, y1 = frame
    return sum(card._pip(x0 + (x1 - x0) * (i + .5) / n, y0 + (y1 - y0) * (j + .5) / n, ring)
               for i in range(n) for j in range(n)) / n / n


@pytest.mark.parametrize("country,zoom", [("Mauritania", 1), ("Mali", 1), ("Senegal", 1), ("Gambia", 1), ("Guinea-Bissau", 1),
                                          ("Western Sahara", 1), ("Spain", 1), ("Spain", 1.8), ("Portugal", 2.2),
                                          ("Niger", 1), ("Guinea", 1), ("Tunisia", 1)])
def test_card_western_sahara_and_morocco_both_named_when_in_view(country, zoom):
    assert card.map_image(country, zoom=zoom) is not None
    labs, frame = dict(card.LAST_LABELS), list(card.LAST_FRAME)
    if _visible_share("ESH", frame) > 0:                                        # any part of Western Sahara in view
        assert labs.get("ESH") == "الصحراء الغربية", country
        lon, lat = card.LAST_LABEL_AT["ESH"]
        esh = _ring(geo.features(), "ESH")
        if country != "Western Sahara":                                         # (the target's name goes under its rings)
            # on Western Sahara itself, or (only a sliver in view) on the sea right beside it, never on another country
            near = min(((x - lon) ** 2 + (y - lat) ** 2) ** .5 for x, y in esh) < 0.12 * (frame[1] - frame[0])
            on_other = any(card._pip(lon, lat, f["geometry"]["coordinates"][0]) for f in geo.features()
                           if f["properties"]["a"] != "ESH" and f["geometry"]["type"] == "Polygon")
            assert card._pip(lon, lat, esh) or (near and not on_other), (country, lon, lat)
        if _visible_share("MAR", frame) > 0.004:
            assert labs.get("MAR") == "المغرب", country
            assert card.LAST_LABEL_AT["MAR"][1] > 27.656                           # Morocco's name north of the border


@pytest.mark.parametrize("country,zoom", [("Mauritania", 1), ("Mali", 1), ("Spain", 1.8), ("Portugal", 2.2)])
def test_card_views_that_must_show_both_names(country, zoom):
    card.map_image(country, zoom=zoom)
    labs = dict(card.LAST_LABELS)
    assert labs.get("ESH") == "الصحراء الغربية" and labs.get("MAR") == "المغرب", (country, labs)
    assert {"ESH", "MAR"} <= set(card.LAST_DRAWN)


def test_card_border_colour_is_bright():
    assert sum(card.BORDER) / 3 > 150                                           # clearly lighter than the land fill


def test_mauritania_reel_fixture():
    s = json.loads((ROOT / "tests/fixtures/reel-map-mauritania.json").read_text())
    assert s["location"]["country_iso3"] == "MRT" and s["location"]["map"]["ok"] and gate(s)


@pytest.mark.skipif(not __import__("os").getenv("KAHWA_CHROME"), reason="needs a Chrome binary in KAHWA_CHROME")
@pytest.mark.parametrize("iso,lon,lat", [("MRT", -15.98, 18.09), ("MLI", -8.0, 12.65), ("ESP", -3.7, 40.42),
                                         ("PRT", -9.14, 38.72), ("SEN", -17.45, 14.7)])
def test_reel_western_sahara_and_morocco_labels_in_browser(tmp_path, iso, lon, lat):
    """Reel map scene (style A) rendered in Chrome: Western Sahara named at the final camera; Morocco too when on screen."""
    import subprocess
    from kahwa_publisher.reels import render
    s = json.loads((ROOT / "tests/fixtures/reel-map-mauritania.json").read_text())
    s["location"].update({"country_iso3": iso, "point": {"lon": lon, "lat": lat, "label_ar": "نقطة"},
                          "map": story.map_view(iso, lon, lat)})
    out, plan, _ = render.prep(s, "A", tmp_path, ROOT)
    m = next(x for x in plan["scenes"] if x["id"] == "map")
    t = round(m["t"] + m["d"] - 0.6, 2)
    r = subprocess.run(["node", str(ROOT / "tests/reel_map_labels.js"), str(out), "", str(t)], capture_output=True, text=True,
                       timeout=90)
    labs = json.loads(r.stdout.strip().splitlines()[-1])[str(t)]
    assert "ESH" in labs, (iso, labs)
    if iso in ("MRT", "MLI", "ESP", "PRT"):                     # Morocco on screen in these views
        assert "MAR" in labs, (iso, labs)
