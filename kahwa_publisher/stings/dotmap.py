"""Dot-matrix world from Natural Earth land polygons (public domain).
All country polygons are filled into ONE land mask, so no internal border, no name and no division is ever drawn
(owner rule: Palestine never split; Western Sahara question never arises because nothing is outlined).
Outputs a transparent PNG (equirectangular, lat 76N..56S) and a JSON of land 'ping' points (no labels).

    python dotmap.py  -> assets/dots.png, assets/dots_lit.png, assets/pings.json
"""
import json
from pathlib import Path
import numpy as np
import cv2

HERE = Path(__file__).resolve().parent
GEO = HERE.parent.parent / "publisher" / "assets" / "countries.geojson"
W, LAT_N, LAT_S = 2400, 76.0, -56.0
STEP = 15                         # px between dots
H = int(round(W * (LAT_N - LAT_S) / 360.0))


def proj(lon, lat):
    return (lon + 180.0) / 360.0 * W, (LAT_N - lat) / (LAT_N - LAT_S) * H


def land_mask(geo_path=GEO, scale=2):
    g = json.loads(Path(geo_path).read_text())
    m = np.zeros((H * scale, W * scale), np.uint8)
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = np.array(poly[0], np.float64)
            x, y = proj(ring[:, 0], ring[:, 1])
            pts = np.stack([x * scale, y * scale], 1).round().astype(np.int32)
            cv2.fillPoly(m, [pts], 255)
            for hole in poly[1:]:            # lakes/holes stay land at this scale (no inner lines drawn either way)
                pass
    return cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)


def main():
    m = land_mask()
    S = 2                                   # draw at 2x for crisp anti-aliased dots
    dots = np.zeros((H * S, W * S, 4), np.uint8)
    lit = np.zeros_like(dots)
    rng = np.random.default_rng(3)
    for yy in range(STEP // 2, H, STEP):
        for xx in range(STEP // 2, W, STEP):
            cov = m[max(0, yy - 3):yy + 4, max(0, xx - 3):xx + 4].mean() / 255.0
            if cov < 0.35:
                continue
            r = 2.6 + 1.2 * cov
            jitter = rng.uniform(0.75, 1.0)
            c = (int(xx * S), int(yy * S))
            cv2.circle(dots, c, int(r * S), (235, 242, 255, int(150 * jitter)), -1, cv2.LINE_AA)
            cv2.circle(lit, c, int((r + 0.6) * S), (255, 255, 255, 255), -1, cv2.LINE_AA)
    out = HERE / "assets"
    out.mkdir(exist_ok=True)
    cv2.imwrite(str(out / "dots.png"), cv2.cvtColor(cv2.resize(dots, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_RGBA2BGRA))
    cv2.imwrite(str(out / "dots_lit.png"), cv2.cvtColor(cv2.resize(lit, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_RGBA2BGRA))
    # ping points: fixed land coordinates spread across continents (no names shown anywhere)
    pts = [(-74.0, 40.7), (-46.6, -23.5), (-0.1, 51.5), (31.2, 30.0), (36.8, -1.3), (55.3, 25.2), (77.2, 28.6),
           (116.4, 39.9), (139.7, 35.7), (151.2, -33.9), (3.4, 6.5), (37.6, 55.8), (-99.1, 19.4), (28.0, -26.2),
           (35.2, 31.8), (-3.7, 40.4)]
    pings = []
    for lon, lat in pts:
        x, y = proj(lon, lat)
        if 0 <= y < H and m[int(y), int(x)] > 0:
            pings.append(dict(x=round(x / W, 4), y=round(y / H, 4)))
    (out / "pings.json").write_text(json.dumps(dict(w=W, h=H, pings=pings)))
    print("dots", W, H, "pings", len(pings))


if __name__ == "__main__":
    main()
