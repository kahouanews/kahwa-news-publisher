"""Map rules for every Kahwa map (cards, numbers/facts/map card frames, reel map + globe scenes).

Owner decisions 2026-10-29 (replace the earlier "banned countries are never drawn" rule):
- A normal world map: every country drawn with its borders and labelled with its Arabic name. Data = Natural Earth
  admin-0 1:110m (public domain), built by tools/maps/build_world.py into assets/countries.geojson + reels/static/world.js.
- Morocco, Algeria and the UAE are drawn and labelled normally (المغرب، الجزائر، الإمارات). This is the MAP LABEL LAYER only:
  the text ban on these names in posts, captions, card text and reel lines is unchanged (newsroom.banned_country,
  reels.render.banned_hits). Map labels come from the data files, never from story text, so the text gates stay strict.
- Western Sahara is its own shape over its full territory (border with Morocco = 27°40'N, see tools/maps/build_world.py),
  the border drawn, labelled «الصحراء الغربية».
- Israel + Palestine = ONE undivided shape (feature "PSE", property nl=1): no internal border, no label, no text.
- A map is never centred / focused on Morocco, Algeria, the UAE, Israel or the Palestine/Israel shape (NO_FOCUS).
"""
import json
import math
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
# never the focus (target) of a map: the four text-banned countries and the merged Palestine/Israel shape
NO_FOCUS_ISO = frozenset({"MAR", "DZA", "ARE", "ISR", "PSE", "PSX"})
NO_FOCUS_NAMES = ("morocco", "algeria", "united arab emirates", "emirates", "uae", "israel", "palestine")
UNLABELLED = frozenset({"PSE"})                      # the merged Palestine/Israel shape never carries text
# Owner 2026-10-29 ("I don't see Western Sahara separated from Morocco"): whenever any part of Western Sahara is in view,
# «الصحراء الغربية» AND «المغرب» are both written (highest label priority after the target), on either side of the border.
# Anchors (lon, lat) tried in order, inside each polygon; then a grid of points inside the visible part; then smaller font
# steps and a 2-line split; last resort (only a sliver in view) the name on the sea right beside it.
# Same lists in reels/templates/kahwa.js (PRIORITY_ANCHORS), checked by tests/test_maps.py.
PAIR_TRIGGER = "ESH"
PRIORITY_ANCHORS = {
    "ESH": [(-13.0, 25.0), (-12.5, 26.0), (-11.0, 26.5), (-14.0, 24.5), (-13.6, 23.6), (-10.2, 26.8), (-14.8, 22.8),
            (-15.8, 22.0)],
    "MAR": [(-7.19, 31.65), (-6.2, 33.0), (-8.0, 30.6), (-9.2, 29.6), (-5.4, 32.2), (-10.0, 28.3), (-6.5, 31.2)],
}
PRIORITY_SPLIT = {"ESH": ("الصحراء", "الغربية")}       # 2-line fallback


def label_of(f):
    """Arabic label to draw for a feature, or None (the merged Palestine/Israel shape has none)."""
    p = f.get("properties") or {}
    if p.get("nl") or str(p.get("a", "")).upper() in UNLABELLED:
        return None
    return p.get("ar") or None


def is_no_focus(f):
    p = f.get("properties") or {}
    return str(p.get("a", "")).upper() in NO_FOCUS_ISO or bool(p.get("nl"))


_FEATS = None


def features():
    """All Natural Earth countries (with the merged, unlabelled Palestine/Israel shape)."""
    global _FEATS
    if _FEATS is None:
        _FEATS = json.loads((ASSETS / "countries.geojson").read_text())["features"]
    return _FEATS


def _norm_lon(x):
    return ((x + 180.0) % 360.0) - 180.0


def ortho_inverse(px, py, lon0, lat0, scale, cx, cy):
    """Screen pixel -> (lon, lat) for d3.geoOrthographic().rotate([-lon0, -lat0]).scale(scale).translate([cx, cy])."""
    x, y = (px - cx) / scale, (cy - py) / scale
    rho = math.hypot(x, y)
    if rho > 1:
        return None
    if rho < 1e-12:
        return lon0, lat0
    c = math.asin(rho)
    p0 = math.radians(lat0)
    lat = math.asin(max(-1.0, min(1.0, math.cos(c) * math.sin(p0) + y * math.sin(c) * math.cos(p0) / rho)))
    lon = math.radians(lon0) + math.atan2(x * math.sin(c), rho * math.cos(p0) * math.cos(c) - y * math.sin(p0) * math.sin(c))
    return _norm_lon(math.degrees(lon)), math.degrees(lat)


def reel_map_view(iso3, point, base_scale, cy=600, push=0.85):
    """Reel map camera: centre on the country, zoom in progressively until the story point is inside the final frame.
    Returns dict(ok, lon, lat, s0, s1, cy) - s0 = opening scale (slight push-in), s1 = final scale.
    ok=False (no map) for a no-focus territory, a country missing from the data, or a point that cannot be framed."""
    f = next((x for x in features() if x["properties"].get("a") == iso3), None)
    if not f or iso3 in NO_FOCUS_ISO or is_no_focus(f):
        return {"ok": False}
    rings = [f["geometry"]["coordinates"][0]] if f["geometry"]["type"] == "Polygon" else [p[0] for p in f["geometry"]["coordinates"]]
    main = max(rings, key=len)
    lon = sum(p[0] for p in main) / len(main)
    lat = sum(p[1] for p in main) / len(main)
    for k in (1.0, 1.3, 1.7, 2.2, 3.0):
        s1 = base_scale * k
        s0 = s1 * push
        if point:
            p = _ortho_forward(point["lon"], point["lat"], lon, lat, s1, 540, cy)
            if not p or not (60 <= p[0] <= 1020 and 160 <= p[1] <= 1760):
                continue
        return {"ok": True, "lon": round(lon, 3), "lat": round(lat, 3), "s0": round(s0), "s1": round(s1), "cy": cy}
    return {"ok": False}


def _ortho_forward(lon, lat, lon0, lat0, scale, cx, cy):
    l, p, l0, p0 = map(math.radians, (lon, lat, lon0, lat0))
    if math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0) < 0:
        return None
    x = math.cos(p) * math.sin(l - l0)
    y = math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0)
    return cx + scale * x, cy - scale * y
