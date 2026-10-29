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
    assert '"stroke": "#1C2232"' not in js and 'stroke: "#3B4762"' in js    # borders visible


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
