"""Build the two Kahwa map data files from Natural Earth admin-0 countries 1:110m (public domain, naturalearthdata.com).

    pip install shapely   # build-time only, never needed by the newsroom / reel runners
    python tools/maps/build_world.py [path/to/ne_110m_admin_0_countries.geojson]

Owner map rules (2026-10-29):
- every country is drawn with its borders and labelled with its Arabic name, as on a normal world map;
- Morocco, Algeria and the UAE are drawn AND labelled (المغرب، الجزائر، الإمارات) - map label layer only; the text ban on
  these names in posts / captions / card text / reel lines is unchanged;
- Western Sahara is its own shape (Natural Earth keeps it separate from Morocco, border drawn) labelled «الصحراء الغربية»;
- Israel + Palestine are merged into ONE undivided shape with no internal border and NO name at all (properties carry
  only "a": "PSE" and "nl": 1 = never label, never a named focus).

Writes assets/countries.geojson (cards) and kahwa_publisher/reels/static/world.js (reels). Properties:
  a  = ISO3 (ISO_A3_EH, else ADM0_A3), n = English name, ar = Arabic label, l = [lon, lat] label point (NE LABEL_X/Y),
  r  = NE MIN_LABEL (smaller = more important; used to drop small labels first), nl = 1 -> unlabelled feature.
"""
import json
import sys
import urllib.request
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
AR_FIX = {"ARE": "الإمارات", "ESH": "الصحراء الغربية", "USA": "الولايات المتحدة", "CAF": "أفريقيا الوسطى",
          "COD": "الكونغو الديمقراطية", "COG": "الكونغو", "BIH": "البوسنة والهرسك", "GNQ": "غينيا الاستوائية",
          "TTO": "ترينيداد وتوباغو", "PNG": "بابوا غينيا الجديدة", "SLB": "جزر سليمان", "SSD": "جنوب السودان",
          "DOM": "الدومينيكان", "FLK": "جزر فوكلاند", "ATF": "أراض فرنسية جنوبية", "MKD": "مقدونيا الشمالية",
          "GBR": "بريطانيا", "RUS": "روسيا", "CZE": "التشيك", "KOR": "كوريا الجنوبية", "PRK": "كوريا الشمالية"}
MERGE = ("ISR", "PSE")            # one undivided, unlabelled shape


def rnd(o, nd=3):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, (list, tuple)):
        return [rnd(x, nd) for x in o]
    return o


def _a2(r):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]))


def rgeom(g, nd):
    """Round a Polygon / MultiPolygon and drop polygons whose exterior ring collapses (zero area) after rounding."""
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    keep = [[rnd(r, nd) for r in poly] for poly in polys]
    keep = [poly for poly in keep if _a2(poly[0]) < 0]
    if len(keep) == 1:
        return {"type": "Polygon", "coordinates": keep[0]}
    return {"type": "MultiPolygon", "coordinates": keep}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    raw = Path(src).read_bytes() if src else urllib.request.urlopen(URL, timeout=60).read()
    ne = json.loads(raw)
    out, merge_geoms = [], []
    for f in ne["features"]:
        p = f["properties"]
        iso = p["ISO_A3_EH"] if p.get("ISO_A3_EH") not in (None, "-99") else p["ADM0_A3"]
        if iso in MERGE:
            merge_geoms.append(shape(f["geometry"]).buffer(0))
            continue
        name = "Western Sahara" if iso == "ESH" else p["NAME_LONG"] if iso in ("BIH",) else p["NAME"]
        props = {"n": name, "a": iso, "ar": AR_FIX.get(iso, p.get("NAME_AR") or name),
                 "l": [round(p["LABEL_X"], 2), round(p["LABEL_Y"], 2)], "r": p.get("MIN_LABEL", 5)}
        out.append({"type": "Feature", "properties": props, "geometry": rgeom(f["geometry"], 3)})
    assert len(merge_geoms) == 2, "Israel / Palestine not found in the Natural Earth file"
    u = unary_union(merge_geoms).buffer(0.02, join_style=2).buffer(-0.02, join_style=2)   # close slivers: no inner border
    if u.geom_type == "MultiPolygon":                    # keep only the main body if a crumb remains
        u = max(u.geoms, key=lambda g: g.area)
    u = orient(shape(json.loads(json.dumps(mapping(u)))).simplify(0.01), sign=-1.0)   # clockwise = d3 spherical fill
    g = mapping(u)
    g = {"type": g["type"], "coordinates": rnd(json.loads(json.dumps(g["coordinates"])))}
    g["coordinates"] = [ring for ring in g["coordinates"][:1]]          # outer ring only: no holes, no inner lines
    out.append({"type": "Feature", "properties": {"a": "PSE", "nl": 1}, "geometry": g})
    fc = {"type": "FeatureCollection", "features": out}
    (ROOT / "assets/countries.geojson").write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    # reels: 2 decimals keep world.js small; same properties (labels drawn by kahwa.js)
    slim = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": f["properties"], "geometry": rgeom(f["geometry"], 2)} for f in out]}
    (ROOT / "kahwa_publisher/reels/static/world.js").write_text(
        "window.WORLD=" + json.dumps(slim, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(len(out), "features; merged shape", u.geom_type, round(u.area, 3))


if __name__ == "__main__":
    main()
