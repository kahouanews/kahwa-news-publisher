"""Map safety for every Kahwa map (cards, numbers/facts/map card frames, reel map + globe scenes).

HARD RULE (user 2026-10-28/29): Morocco, the UAE, Algeria and Israel are never drawn, outlined, shaded or labelled in any
form; Western Sahara is omitted as well (part of Morocco's disputed geometry). Their polygons are removed from both map
data files (assets/countries.geojson, reels/static/world.js) and `features()` filters them again at load time.

Removing a country leaves a sea-coloured hole. Where that hole would be visible (e.g. Tunisia next to an empty Algeria)
the map is cropped tighter or dropped: `view_clear()` / `ortho_view_clear()` test a view against KEEP_OUT, a coarse 1°
grid of the cells those territories touch (cell indices only, never rendered). Any overlap = the view is not used.
"""
import json
import math
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BANNED_MAP_ISO = frozenset({"MAR", "ARE", "DZA", "ISR", "ESH", "SAH"})
BANNED_MAP_NAMES = ("morocco", "western sahara", "algeria", "united arab emirates", "emirates", "israel")
BANNED_MAP_AR = ("المغرب", "الصحراء", "الجزائر", "الإمارات", "الامارات", "إسرائيل", "اسرائيل")

# 1° cells (floor(lon), floor(lat)) touched by the banned territories (computed from Natural Earth 110m before removal;
# coasts included). Rows: (lat, [(lon_from, lon_to), ...]) inclusive.
_ROWS = [
    (19, [(2, 6)]),
    (20, [(-18, -18), (1, 7)]),
    (21, [(-18, -13), (-1, 9)]),
    (22, [(-17, -14), (-2, 11), (52, 55)]),
    (23, [(-17, -12), (-4, 11), (51, 55)]),
    (24, [(-16, -12), (-5, 11), (51, 56)]),
    (25, [(-15, -9), (-7, 9), (54, 56)]),
    (26, [(-15, 9), (56, 56)]),
    (27, [(-14, 9)]),
    (28, [(-13, 9)]),
    (29, [(-11, 9), (34, 35)]),
    (30, [(-10, 9), (34, 35)]),
    (31, [(-10, 9), (34, 35)]),
    (32, [(-10, 9), (34, 35)]),
    (33, [(-9, 8), (35, 35)]),
    (34, [(-8, 8)]),
    (35, [(-7, 8)]),
    (36, [(0, 8)]),
    (37, [(6, 7)]),
]
KEEP_OUT = frozenset((x, y) for y, runs in _ROWS for a, b in runs for x in range(a, b + 1))


def is_banned_feature(f):
    p = f.get("properties") or {}
    if str(p.get("a", "")).upper() in BANNED_MAP_ISO:
        return True
    n = str(p.get("n", "")).lower()
    ar = str(p.get("ar", ""))
    return any(b in n for b in BANNED_MAP_NAMES) or any(b in ar for b in BANNED_MAP_AR)


_FEATS = None


def features():
    """Natural Earth countries without the banned territories (defence in depth: the file itself no longer has them)."""
    global _FEATS
    if _FEATS is None:
        geo = json.loads((ASSETS / "countries.geojson").read_text())
        _FEATS = [f for f in geo["features"] if not is_banned_feature(f)]
    return _FEATS


def _norm_lon(x):
    return ((x + 180.0) % 360.0) - 180.0


def view_clear(x0, y0, x1, y1):
    """True if the lon/lat rectangle touches no KEEP_OUT cell (lon may run past ±180)."""
    for cy in range(math.floor(y0), math.floor(y1) + 1):
        for cx in range(math.floor(x0), math.floor(x1) + 1):
            if (math.floor(_norm_lon(cx + 0.5)), cy) in KEEP_OUT:
                return False
    return True


def ortho_inverse(px, py, lon0, lat0, scale, cx, cy):
    """Screen pixel -> (lon, lat) for d3.geoOrthographic().rotate([-lon0, -lat0]).scale(scale).translate([cx, cy])."""
    x, y = (px - cx) / scale, (cy - py) / scale
    rho = math.hypot(x, y)
    if rho > 1:
        return None                                    # off the globe (space / background)
    if rho < 1e-12:
        return lon0, lat0
    c = math.asin(rho)
    p0 = math.radians(lat0)
    lat = math.asin(max(-1.0, min(1.0, math.cos(c) * math.sin(p0) + y * math.sin(c) * math.cos(p0) / rho)))
    lon = math.radians(lon0) + math.atan2(x * math.sin(c), rho * math.cos(p0) * math.cos(c) - y * math.sin(p0) * math.sin(c))
    return _norm_lon(math.degrees(lon)), math.degrees(lat)


def ortho_view_clear(lon0, lat0, scale, cx=540, cy=960, w=1080, h=1920, step=8):
    """True if no visible pixel of the orthographic view falls in a KEEP_OUT cell."""
    for py in range(0, h + 1, step):
        for px in range(0, w + 1, step):
            ll = ortho_inverse(px, py, lon0, lat0, scale, cx, cy)
            if ll and (math.floor(ll[0]), math.floor(ll[1])) in KEEP_OUT:
                return False
    return True


def reel_map_view(iso3, point, base_scale, cy=600, push=0.85):
    """Pick a safe reel map camera: centre on the country, try progressively tighter zooms (the story point must stay on
    screen) and return dict(ok, lon, lat, s0, s1, cy) — s0 = opening scale (slight push-in), s1 = final scale.
    ok=False -> the reel shows its 'where' scene without any map."""
    f = next((x for x in features() if x["properties"].get("a") == iso3), None)
    if not f or iso3 in BANNED_MAP_ISO:
        return {"ok": False}
    rings = [f["geometry"]["coordinates"][0]] if f["geometry"]["type"] == "Polygon" else [p[0] for p in f["geometry"]["coordinates"]]
    main = max(rings, key=len)
    lon = sum(p[0] for p in main) / len(main)
    lat = sum(p[1] for p in main) / len(main)
    for k in (1.0, 1.3, 1.7, 2.2, 3.0):
        s1 = base_scale * k
        s0 = s1 * push
        if point:
            # the story point must be inside the final frame
            from_c = _ortho_forward(point["lon"], point["lat"], lon, lat, s1, 540, cy)
            if not from_c or not (60 <= from_c[0] <= 1020 and 160 <= from_c[1] <= 1760):
                continue
        if ortho_view_clear(lon, lat, s0, 540, cy) and ortho_view_clear(lon, lat, s1, 540, cy):
            return {"ok": True, "lon": round(lon, 3), "lat": round(lat, 3), "s0": round(s0), "s1": round(s1), "cy": cy}
    return {"ok": False}


def _ortho_forward(lon, lat, lon0, lat0, scale, cx, cy):
    l, p, l0, p0 = map(math.radians, (lon, lat, lon0, lat0))
    if math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0) < 0:
        return None
    x = math.cos(p) * math.sin(l - l0)
    y = math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0)
    return cx + scale * x, cy - scale * y
