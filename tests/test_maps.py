"""Map safety (user hard rule): Morocco (+ Western Sahara), UAE, Algeria, Israel are never drawn, outlined, shaded or
labelled on any map - card map/numbers/facts frames and reel map/globe scenes."""
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

BANNED = {"MAR", "ARE", "DZA", "ISR", "ESH", "SAH"}
NAMES = re.compile(r"morocc|western sahara|algeri|emirates|israel|المغرب|الصحراء الغربية|الجزائر|الإمارات|إسرائيل", re.I)


def _world_js():
    s = (ROOT / "kahwa_publisher/reels/static/world.js").read_text()
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


@pytest.mark.parametrize("data", ["geojson", "world.js"])
def test_banned_geometries_absent_from_map_data(data):
    feats = json.loads((ROOT / "assets/countries.geojson").read_text())["features"] if data == "geojson" else _world_js()["features"]
    assert len(feats) > 150
    isos = {str(f["properties"].get("a", "")).upper() for f in feats}
    assert not isos & BANNED
    assert not any(NAMES.search(json.dumps(f["properties"], ensure_ascii=False)) for f in feats)


def test_geo_features_filtered():
    assert not {f["properties"]["a"] for f in geo.features()} & BANNED
    for f in ({"properties": {"a": "MAR"}}, {"properties": {"a": "x", "n": "Western Sahara"}}, {"properties": {"ar": "الإمارات"}}):
        assert geo.is_banned_feature(f)


def _no_banned_vertex(view):
    """No KEEP_OUT cell inside a lon/lat view."""
    x0, y0, x1, y1 = view
    return geo.view_clear(x0, y0, x1, y1)


@pytest.mark.parametrize("country", ["France", "Brazil", "Japan", "India", "Greece", "Germany", "Sudan", "Yemen", "Iraq",
                                     "Tunisia", "Egypt", "Spain", "Saudi Arabia", "Libya", "Jordan", "Oman", "Italy", "Mali"])
def test_rendered_card_maps_never_contain_banned(country):
    res = card.map_image(country)
    assert not set(card.LAST_DRAWN) & BANNED                 # nothing banned was drawn
    if res is not None:
        assert res[0].size == (card.W, card.PH)


@pytest.mark.parametrize("country", ["Tunisia", "Libya", "Mauritania", "Mali", "Niger", "Spain", "Saudi Arabia", "Oman",
                                     "Qatar", "Egypt", "Jordan", "Lebanon", "Palestine", "Syria"])
def test_conspicuous_gap_falls_back_to_no_map(country):
    assert card.map_image(country) is None                  # the banned neighbour's gap would show -> non-map card


@pytest.mark.parametrize("country", ["Morocco", "Algeria", "United Arab Emirates", "Israel", "Western Sahara", "UAE"])
def test_banned_country_has_no_map(country):
    assert card.map_image(country) is None


def test_map_card_without_safe_map_renders_text_only(tmp_path):
    out = tmp_path / "c.jpg"
    r = cards.render_card({"card_type": "map", "headline": "عنوان تجريبي عن تونس", "title": "تونس", "kicker": "تونس",
                           "country_en": "Tunisia", "source": "وكالات"}, out)
    assert out.exists() and not r["credit"].startswith("خريطة")


@pytest.mark.parametrize("iso", ["TUN", "EGY", "ESP", "SAU", "LBY", "JOR", "LBN", "PSE", "PSX", "MRT", "NER", "OMN"])
def test_reel_map_view_rejects_conspicuous(iso):
    if iso not in story.countries():
        pytest.skip("not in data")
    lon, lat = story.centroid(iso)
    assert story.map_view(iso, lon, lat) == {"ok": False}


@pytest.mark.parametrize("iso", ["BRA", "JPN", "IND", "USA", "CHN", "GBR", "DEU", "PAK", "NGA", "IDN"])
def test_reel_map_view_safe(iso):
    lon, lat = story.centroid(iso)
    mv = story.map_view(iso, lon, lat)
    assert mv["ok"]
    for sc in (mv["s0"], mv["s1"]):                          # no visible pixel of the camera falls on a banned cell
        assert geo.ortho_view_clear(mv["lon"], mv["lat"], sc, 540, mv["cy"], step=6)


def test_banned_iso_not_in_reel_countries():
    assert not set(story.countries()) & BANNED


def test_keep_out_covers_banned_capitals():
    for lon, lat in ((-6.84, 34.02), (3.06, 36.75), (54.37, 24.45), (34.78, 32.08), (-13.2, 27.15)):   # Rabat, Algiers, Abu Dhabi, Tel Aviv, Laayoune
        assert (int(lon // 1), int(lat // 1)) in geo.KEEP_OUT


def test_gate_rejects_unsafe_reel_camera():
    s = {"id": "x", "headline": "عنوان", "location": {"country_iso3": "TUN", "map": {"ok": True, "lon": 9.5, "lat": 34, "s0": 1500, "s1": 1800, "cy": 600}},
         "media": {}}
    with pytest.raises(ValueError):
        gate(s)


def test_kahwa_js_filters_banned():
    js = (ROOT / "kahwa_publisher/reels/templates/kahwa.js").read_text()
    assert 'BANNED_MAP = ["MAR", "ARE", "DZA", "ISR", "ESH", "SAH"]' in js
    assert "MAPOK" in js and "lon: 30, lat: 18, scale: 430" not in js     # no wide fly-in globe any more
