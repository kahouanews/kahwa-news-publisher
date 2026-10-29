"""Kahwa News card v2 — photo-led, benchmarked on global news social cards.

Layout (1080x1350, 4:5 = max feed real estate on Facebook/Instagram):
  * top ~60%: real, legally reusable photo (Wikimedia Commons, free licences only, credited)
    with a cinematic grade and a navy fade into the text panel; fallback = vector map of the
    country with the country highlighted in brand red; last fallback = brand pattern.
  * small vector brand badge top-left, red label (عاجل / category) on the seam.
  * headline: 2-4 lines, right-aligned, bottom-placed (not huge, not centred — research shows
    oversized centred overlays lower engagement), key phrase highlighted in red.
  * footer: KAHWA NEWS wordmark, date, photo credit + licence.
"""
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
UA = {"User-Agent": "KahwaNewsBot/1.0 (https://github.com/kahouanews/kahwa-news-publisher; momomani1995@gmail.com)"}
W, H = 1080, 1350
PH = 800                      # photo area height
NAVY, NAVY2, RED, WHITE, GREY = (10, 16, 28), (16, 24, 40), (220, 24, 12), (248, 248, 248), (150, 160, 178)
FREE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? ?\d|cc-by(-sa)?-\d|attribution)", re.I)
RAQM = ImageFont.Layout.RAQM


def font(name, size, raqm=True):
    return ImageFont.truetype(str(ASSETS / name), size, layout_engine=RAQM if raqm else ImageFont.Layout.BASIC)


# ------------------------------------------------------------------ photo sourcing (legal only)
def _get(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def commons_photo(query, min_w=1000):
    """Best freely-licensed photo on Wikimedia Commons for `query` -> (PIL image, credit) or None."""
    if not query:
        return None
    q = urllib.parse.quote(f"{query} filetype:bitmap")
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&formatversion=2&generator=search"
           f"&gsrnamespace=6&gsrlimit=12&gsrsearch={q}&prop=imageinfo&iiprop=url|size|mime|extmetadata"
           "&iiurlwidth=1600&iiextmetadatafilter=LicenseShortName|Artist|Restrictions")
    try:
        pages = json.loads(_get(url)).get("query", {}).get("pages", [])
    except Exception:
        return None
    cands = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata", {})
        lic = (m.get("LicenseShortName", {}).get("value") or "").strip()
        if not FREE.match(lic) or "nc" in lic.lower() or "nd" in lic.lower():
            continue
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or ii.get("width", 0) < min_w:
            continue
        if m.get("Restrictions", {}).get("value"):          # personality/trademark restrictions -> skip
            continue
        title = p.get("title", "").lower()
        if not any(w.lower() in title for w in query.split() if len(w) > 3):
            continue
        if any(b in title for b in ("logo", "flag", "map", "coat of arms", "seal", "diagram", "chart", "signature")):
            continue
        artist = re.sub(r"<[^>]+>", "", m.get("Artist", {}).get("value") or "").strip()[:40] or "Wikimedia Commons"
        score = p.get("index", 50) * -1 + (8 if ii["width"] >= 1600 else 0) + (4 if ii["width"] > ii["height"] else 0)
        cands.append((score, ii.get("thumburl") or ii["url"], f"{artist} / {lic}"))
    for _, u, credit in sorted(cands, reverse=True)[:3]:
        try:
            im = Image.open(io.BytesIO(_get(u, 40))).convert("RGB")
            if im.width >= 800:
                return im, credit
        except Exception:
            continue
    return None


def wiki_photo(entity):
    """Lead image of the entity's English Wikipedia article (precise for people/places), licence-checked."""
    if not entity:
        return None
    try:
        t = urllib.parse.quote(entity.replace(" ", "_"))
        s = json.loads(_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"))
        src = (s.get("originalimage") or {}).get("source")
        if not src or s.get("type") == "disambiguation":
            return None
        fname = urllib.parse.unquote(src.split("?")[0].split("/")[-1])
        meta = json.loads(_get("https://commons.wikimedia.org/w/api.php?action=query&format=json&formatversion=2"
                               f"&titles=File:{urllib.parse.quote(fname)}&prop=imageinfo&iiprop=url|size|mime|extmetadata"
                               "&iiurlwidth=1600&iiextmetadatafilter=LicenseShortName|Artist"))
        ii = meta["query"]["pages"][0]["imageinfo"][0]
        lic = ii["extmetadata"].get("LicenseShortName", {}).get("value", "")
        if not FREE.match(lic) or "nc" in lic.lower() or "nd" in lic.lower() or ii.get("width", 0) < 600:
            return None
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            return None
        artist = re.sub(r"<[^>]+>", "", ii["extmetadata"].get("Artist", {}).get("value") or "").strip()[:40]
        im = Image.open(io.BytesIO(_get(ii.get("thumburl") or ii["url"], 40))).convert("RGB")
        return im, f"{artist or 'Wikimedia Commons'} / {lic}"
    except Exception:
        return None


# ------------------------------------------------------------------ vector map fallback
LAST_DRAWN = []          # ISO3 codes drawn by the last map_image() call (tests assert no banned territory is ever in it)


def map_image(country_en):
    """Country map (target in red, neighbours in slate). Banned territories (Morocco incl. Western Sahara, UAE, Algeria,
    Israel) are not in the data at all (geo.features); if their empty space would show inside the frame the crop is
    tightened once, otherwise None is returned and the caller uses a non-map card."""
    from . import geo
    LAST_DRAWN.clear()
    feats = geo.features()
    q = (country_en or "").strip().lower()
    if not q or any(b in q for b in geo.BANNED_MAP_NAMES):
        return None
    target = next((f for f in feats if (f["properties"]["n"] or "").lower() == q), None) or \
        next((f for f in feats if q in (f["properties"]["n"] or "").lower()), None)
    if not target or geo.is_banned_feature(target):
        return None

    def rings(g):
        if g["type"] == "Polygon":
            return [g["coordinates"][0]]
        return [poly[0] for poly in g["coordinates"]]

    tr = rings(target["geometry"])
    if max(x for r in tr for x, _ in r) - min(x for r in tr for x, _ in r) > 180:   # crosses antimeridian (Russia, Fiji...)
        tr = [[(x + 360 if x < 0 else x, y) for x, y in r] for r in tr]
    main = max(tr, key=len)                                       # frame on the mainland, not far islands
    xs = [x for x, _ in main]
    ys = [y for _, y in main]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    base = max(max(xs) - min(xs), (max(ys) - min(ys)) * W / PH, 12)
    view = None
    for mult in ((2.6 if base < 40 else 1.8 if base < 90 else 1.35), 1.12, 1.0):   # normal crop, then tighter crops
        span = base * mult
        x0, x1 = cx - span / 2, cx + span / 2
        yspan = span * PH / W
        y0, y1 = cy - yspan / 2, cy + yspan / 2
        if geo.view_clear(x0, y0, x1, y1):
            view = (x0, y0, x1, y1)
            break
    if view is None:
        return None                                               # a banned territory's gap would show -> no map
    x0, y0, x1, y1 = view
    S = 2
    im = Image.new("RGB", (W * S, PH * S), (12, 22, 40))
    d = ImageDraw.Draw(im)
    for gx in range(0, W * S, 60 * S):                          # subtle grid
        d.line([(gx, 0), (gx, PH * S)], fill=(18, 30, 52), width=S)
    for gy in range(0, PH * S, 60 * S):
        d.line([(0, gy), (W * S, gy)], fill=(18, 30, 52), width=S)

    def P(r):
        return [((x - x0) / (x1 - x0) * W * S, (y1 - y) / (y1 - y0) * PH * S) for x, y in r]

    for f in feats:
        if geo.is_banned_feature(f):                              # never (already absent from the data)
            continue
        LAST_DRAWN.append(f["properties"].get("a"))
        for r in rings(f["geometry"]):
            for off in (0, 360, -360):
                d.polygon(P([(x + off, y) for x, y in r]), fill=(32, 46, 72), outline=(60, 78, 110))
    for r in tr:
        d.polygon(P(r), fill=RED, outline=(255, 120, 100))
    px, py = (cx - x0) / (x1 - x0) * W * S, (y1 - cy) / (y1 - y0) * PH * S
    for rad, a in ((90, 40), (55, 90)):
        d.ellipse([px - rad * S, py - rad * S, px + rad * S, py + rad * S], outline=(255, 255, 255), width=2 * S)
    return im.resize((W, PH), Image.LANCZOS), "خريطة: قهوة نيوز (بيانات Natural Earth)"


# ------------------------------------------------------------------ composition
def cover(im, w, h):
    r = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * r + 1), int(im.height * r + 1)), Image.LANCZOS)
    x = (im.width - w) // 2
    y = max(0, int((im.height - h) * 0.28))                    # keep faces (upper third) in frame
    return im.crop((x, y, x + w, y + h))


def grade(im):
    im = ImageEnhance.Color(im).enhance(0.88)
    im = ImageEnhance.Contrast(im).enhance(1.08)
    tint = Image.new("RGB", im.size, (14, 24, 44))
    return Image.blend(im, tint, 0.12)


def wrap(d, text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f, direction="rtl", language="ar") <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_rtl_line(d, x_right, y, line, f, hl_words):
    """Draw one RTL line word by word so highlighted words can be red."""
    words = line.split()
    space = d.textlength(" ", font=f)
    x = x_right
    for w in words:
        wl = d.textlength(w, font=f, direction="rtl", language="ar")
        col = RED if w.strip("،.:؛!؟\"«»") in hl_words else WHITE
        d.text((x, y), w, font=f, fill=col, anchor="ra", direction="rtl", language="ar")
        x -= wl + space


def openverse_photo(query, min_w=1000):
    """Openverse (WordPress/Creative Commons index, no key): commercial+modification-safe licences only."""
    if not query:
        return None
    url = ("https://api.openverse.org/v1/images/?page_size=12&license_type=commercial,modification"
           f"&mature=false&q={urllib.parse.quote(query)}")
    try:
        res = json.loads(_get(url)).get("results", [])
    except Exception:
        return None
    words = [w.lower() for w in query.split() if len(w) > 3]
    for r in res:
        if (r.get("width") or 0) < min_w or (r.get("license") or "").lower() in ("nc", "nd", "by-nc", "by-nd"):
            continue
        title = (r.get("title") or "").lower() + " " + " ".join(t.get("name", "") for t in r.get("tags") or []).lower()
        if words and sum(w in title for w in words) < max(1, len(words) // 2):
            continue
        try:
            im = Image.open(io.BytesIO(_get(r["url"], 40))).convert("RGB")
        except Exception:
            continue
        if im.width >= 800:
            lic = f"CC {r.get('license', '').upper()} {r.get('license_version', '')}".replace("CC CC0", "CC0").replace("CC PDM", "Public Domain").strip()
            return im, f"{(r.get('creator') or 'Openverse')[:40]} / {lic}"
    return None


def badge(d, x, y, scale=1.0, dark_text=False, anchor_right=False):
    f1 = font("Barlow-BlackItalic.ttf", int(40 * scale), raqm=False)
    f2 = font("Barlow-BlackItalic.ttf", int(22 * scale), raqm=False)
    lw = d.textlength("KAHWA", font=f1)
    total = lw + 10 * scale + 104 * scale
    if anchor_right:
        x = x - total
    d.text((x, y), "KAHWA", font=f1, fill=NAVY if dark_text else WHITE, anchor="lm")
    bx = x + lw + 10 * scale
    d.polygon([(bx + 8 * scale, y - 16 * scale), (bx + 104 * scale, y - 16 * scale), (bx + 96 * scale, y + 16 * scale),
               (bx, y + 16 * scale)], fill=RED)
    d.text((bx + 52 * scale, y + 1), "NEWS", font=f2, fill=WHITE, anchor="mm")


def draw_lines(d, lines, x_right, y, f, lh, hl, color):
    space = d.textlength(" ", font=f)
    for ln in lines:
        x = x_right
        for w in ln.split():
            wl = d.textlength(w, font=f, direction="rtl", language="ar")
            col = RED if w.strip("،.:؛!؟\"«»") in hl else color
            d.text((x, y), w, font=f, fill=col, anchor="ra", direction="rtl", language="ar")
            x -= wl + space
        y += lh
    return y


def fit(d, text, width, start, minimum, max_lines):
    size = start
    while True:
        f = font("Tajawal-Black.ttf", size)
        lines = wrap(d, text, f, width)
        if len(lines) <= max_lines or size <= minimum:
            return f, lines[:max_lines + 1], size
        size -= 4


def rounded(im, r):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], r, fill=255)
    return m


def render(headline, category, urgent, out, photo_query=None, country_en=None, highlight="", entity=None):
    """v3 — modelled on what CNN Arabic / Sky News Arabia actually run on Facebook (Oct 2026):
    A) real photo -> full-bleed photo, dark bottom gradient, small logo, white 2-3 line headline.
    B) no photo   -> clean white editorial card: logo top, black headline, image/map in a rounded frame.
    No date, no big category banners; only a small red 'عاجل' pill for breaking news."""
    photo = wiki_photo(entity) or commons_photo(photo_query) or openverse_photo(photo_query)
    hl = set(w.strip("،.:؛!؟\"«»") for w in (highlight or "").split())
    if photo:
        img, credit = photo
        canvas = cover(ImageEnhance.Contrast(img).enhance(1.05), W, H)
        g = Image.new("L", (1, H), 0)
        for y in range(H):
            t = (y - H * 0.34) / (H * 0.40)
            g.putpixel((0, y), int(242 * max(0, min(1, t)) ** 0.9) if t > 0 else (int(70 * (1 - y / 160)) if y < 160 else 0))
        canvas = Image.composite(Image.new("RGB", (W, H), (6, 8, 14)), canvas, g.resize((W, H)))
        d = ImageDraw.Draw(canvas)
        f, lines, size = fit(d, headline, W - 140, 70, 52, 3)
        lh = int(size * 1.38)
        top = H - 110 - len(lines) * lh
        badge(d, W - 70, top - 70, 0.95, anchor_right=True)
        if urgent:
            ft = font("Tajawal-Black.ttf", 30)
            tw = d.textlength("عاجل", font=ft, direction="rtl", language="ar")
            bx = W - 70 - d.textlength("KAHWA", font=font("Barlow-BlackItalic.ttf", 38, raqm=False)) - 140 - tw - 40
            d.rounded_rectangle([bx, top - 92, bx + tw + 36, top - 48], 8, fill=RED)
            d.text((bx + tw / 2 + 18, top - 71), "عاجل", font=ft, fill=WHITE, anchor="mm", direction="rtl", language="ar")
        draw_lines(d, lines, W - 70, top, f, lh, hl, WHITE)
        fc = font("Tajawal-Medium.ttf", 19)
        d.text((70, H - 48), ("صورة أرشيفية: " + credit)[:76], font=fc, fill=(150, 156, 170), anchor="lm", direction="rtl", language="ar")
    else:
        mp = map_image(country_en)
        credit = mp[1] if mp else ""
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(canvas)
        badge(d, W - 70, 96, 1.0, dark_text=True, anchor_right=True)
        if urgent:
            ft = font("Tajawal-Black.ttf", 30)
            d.rounded_rectangle([70, 72, 170, 120], 8, fill=RED)
            d.text((120, 96), "عاجل", font=ft, fill=WHITE, anchor="mm", direction="rtl", language="ar")
        f, lines, size = fit(d, headline, W - 140, 66, 50, 4)
        lh = int(size * 1.4)
        y = draw_lines(d, lines, W - 70, 180, f, lh, hl, (14, 16, 22))
        box_top = y + 40
        bw, bh = W - 140, H - box_top - 110
        if mp and bh > 300:
            pic = cover(mp[0], bw, bh)
            canvas.paste(pic, (70, box_top), rounded(pic, 28))
        d.text((70, H - 60), credit, font=font("Tajawal-Medium.ttf", 20), fill=(120, 126, 140), anchor="lm",
               direction="rtl", language="ar")
        d.text((W - 70, H - 60), "قهوة نيوز", font=font("Tajawal-Bold.ttf", 24), fill=(14, 16, 22), anchor="rm",
               direction="rtl", language="ar")
    canvas.save(out, "JPEG", quality=93, optimize=True, progressive=True)
    return credit
