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
LAST_DRAWN = []          # ISO3 codes drawn by the last map_image() call
LAST_LABELS = []         # (iso3, label) pairs written by the last map_image() call (tests: no label on the PSE shape)
LAST_FRAME = []          # [lon0, lon1, lat0, lat1] of the last map_image() frame
LAST_LABEL_AT = {}       # iso3 -> (lon, lat) of the label centre written by the last map_image() call
BORDER = (150, 170, 206)  # every international border / coastline (readable at 1080px: MAR / ESH border, owner 2026-10-29)


def _pip(x, y, ring):
    """Point in polygon (screen or lon/lat coordinates)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def map_image(country_en, zoom=1.0):
    """Normal country map (owner rules 2026-10-29, see geo.py): every neighbour drawn with its borders and its Arabic name,
    target in red. The merged Palestine/Israel shape is drawn with no internal border and no text; Western Sahara is its
    own shape; Morocco / Algeria / UAE are labelled on the map like any country (map label layer only). A map is never
    focused on Morocco, Algeria, the UAE, Israel or the Palestine/Israel shape -> None (caller uses a non-map card)."""
    from . import geo
    LAST_DRAWN.clear()
    LAST_LABELS.clear()
    LAST_LABEL_AT.clear()
    LAST_FRAME.clear()
    feats = geo.features()
    q = (country_en or "").strip().lower()
    if not q or any(b == q or b in q for b in geo.NO_FOCUS_NAMES):
        return None
    named = [f for f in feats if f["properties"].get("n")]
    target = next((f for f in named if f["properties"]["n"].lower() == q), None) or \
        next((f for f in named if q in f["properties"]["n"].lower()), None)
    if not target or geo.is_no_focus(target):
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
    span = base * (2.6 if base < 40 else 1.8 if base < 90 else 1.35) * zoom      # zoom > 1 = wider view (tests)
    x0, x1 = cx - span / 2, cx + span / 2
    yspan = span * PH / W
    y0, y1 = cy - yspan / 2, cy + yspan / 2
    LAST_FRAME[:] = [x0, x1, y0, y1]
    S = 2
    im = Image.new("RGB", (W * S, PH * S), (12, 22, 40))
    d = ImageDraw.Draw(im)
    for gx in range(0, W * S, 60 * S):                          # subtle grid
        d.line([(gx, 0), (gx, PH * S)], fill=(18, 30, 52), width=S)
    for gy in range(0, PH * S, 60 * S):
        d.line([(0, gy), (W * S, gy)], fill=(18, 30, 52), width=S)

    def P(r):
        return [((x - x0) / (x1 - x0) * W * S, (y1 - y) / (y1 - y0) * PH * S) for x, y in r]

    def visible(r):
        return any(x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2 for x, y in r)

    for f in feats:                                              # land + borders (outline = border line)
        drawn = False
        for r in rings(f["geometry"]):
            for off in (0, 360, -360):
                rr = [(x + off, y) for x, y in r]
                if visible(rr):
                    d.polygon(P(rr), fill=(32, 46, 72))
                    drawn = True
        if drawn:
            LAST_DRAWN.append(f["properties"].get("a"))
    for f in feats:                                              # borders on top so every shared edge shows
        for r in rings(f["geometry"]):                            # (brighter + thicker since 2026-10-29: the Morocco /
            for off in (0, 360, -360):                            # Western Sahara border must read clearly at 1080px)
                rr = [(x + off, y) for x, y in r]
                if visible(rr):
                    d.line(P(rr) + P(rr[:1]), fill=BORDER, width=3 * S, joint="curve")
    for r in tr:
        d.polygon(P(r), fill=RED)
        d.line(P(r) + P(r[:1]), fill=(255, 120, 100), width=2 * S, joint="curve")
    px, py = (cx - x0) / (x1 - x0) * W * S, (y1 - cy) / (y1 - y0) * PH * S
    for rad, a in ((90, 40), (55, 90)):
        d.ellipse([px - rad * S, py - rad * S, px + rad * S, py + rad * S], outline=(255, 255, 255), width=2 * S)
    # ---- label layer: Arabic country names (never on the merged Palestine/Israel shape), target first, then Western
    # Sahara + Morocco whenever Western Sahara is in view (always written: several anchors / font steps / 2 lines / on the
    # sea beside a sliver), then the rest by importance, no overlaps, only where the country is big enough for its name
    f_tgt = font("Almarai-ExtraBold.ttf", 36 * S)
    placed = [(px - 92 * S, py - 92 * S, px + 92 * S, py + 92 * S)]     # never write over the target rings

    def hits(box, others):
        return any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in others)

    def in_frame(box):
        return box[0] >= 10 and box[2] <= W * S - 10 and box[1] >= 10 and box[3] <= PH * S - 10

    def text_box(lines, fnt, X, Y):
        tw = max(d.textlength(ln, font=fnt, direction="rtl", language="ar") for ln in lines)
        lh = fnt.size * 1.15
        hh = fnt.size * 0.7 + (len(lines) - 1) * lh / 2
        return (X - tw / 2 - 8, Y - hh, X + tw / 2 + 8, Y + hh), tw, lh

    def draw_label(iso, lab, lines, X, Y, fnt, lh, is_t=False):
        n = len(lines)
        for i, ln in enumerate(lines):
            d.text((X, Y + (i - (n - 1) / 2) * lh), ln, font=fnt, anchor="mm", direction="rtl", language="ar",
                   fill=(255, 255, 255) if is_t else (190, 202, 224), stroke_width=3 * S if is_t else 2 * S,
                   stroke_fill=(12, 22, 40))
        LAST_LABELS.append((iso, lab))
        LAST_LABEL_AT[iso] = (x0 + X / (W * S) * (x1 - x0), y1 - Y / (PH * S) * (y1 - y0))

    by_iso = {f["properties"].get("a"): f for f in feats}

    def on_screen_rings(f):
        out = []
        for r in rings(f["geometry"]):
            for off in (0, 360, -360):
                rr = [(x + off, y) for x, y in r]
                if visible(rr):
                    out.append(rr)
        return out

    pair_on = geo.PAIR_TRIGGER in LAST_DRAWN
    keep_free = [P(r) for iso in (geo.PAIR_TRIGGER, "MAR") if by_iso.get(iso) is not None and by_iso[iso] is not target
                 for r in on_screen_rings(by_iso[iso])] if pair_on else []

    # target (below the rings; when Western Sahara / Morocco are in view, never on top of them: above / beside instead)
    if geo.label_of(target):
        lab = geo.label_of(target)
        below = (px, py + 120 * S) if py + 160 * S < PH * S else (px, py - 120 * S)
        spots = [below, (px, py - 120 * S), (px + 190 * S, py), (px - 190 * S, py)] if keep_free else [below]
        for X, Y in spots:
            box, tw, lh = text_box([lab], f_tgt, X, Y)
            if not in_frame(box):
                continue
            if keep_free and (X, Y) != spots[-1] and any(_pip(a, b, r) for a in (box[0], X, box[2])
                                                         for b in (box[1], Y, box[3]) for r in keep_free):
                continue
            placed.append(box)
            draw_label(target["properties"].get("a"), lab, [lab], X, Y, f_tgt, lh, True)
            break
    done = {target["properties"].get("a")}

    # Western Sahara + Morocco: both names whenever any part of Western Sahara is in view
    if pair_on:
        for iso in (geo.PAIR_TRIGGER, "MAR"):
            f = by_iso.get(iso)
            lab = geo.label_of(f) if f else None
            if not lab or iso in done:
                continue
            vis = on_screen_rings(f)
            if not vis:
                continue
            spts = [P(r) for r in vis]
            land = [P(r) for g in feats if g is not f for r in on_screen_rings(g)]
            pts = list(geo.PRIORITY_ANCHORS.get(iso, []))
            gx = [x for r in vis for x, _ in r]
            gy = [y for r in vis for _, y in r]
            bx0, bx1, by0, by1 = max(min(gx), x0), min(max(gx), x1), max(min(gy), y0), min(max(gy), y1)
            mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
            grid = [(bx0 + (bx1 - bx0) * i / 8, by0 + (by1 - by0) * j / 8) for i in range(1, 8) for j in range(1, 8)]
            pts += sorted(grid, key=lambda q: (q[0] - mx) ** 2 + (q[1] - my) ** 2)
            fine = [(bx0 + (bx1 - bx0) * (i + .5) / 24, by0 + (by1 - by0) * (j + .5) / 24) for i in range(24) for j in range(24)]
            inside = [q for q in pts + fine if any(_pip(q[0], q[1], r) for r in vis)]
            if inside:                                                # sea fallback: start from the outermost points
                bx = sorted(inside, key=lambda q: q[0])
                by_ = sorted(inside, key=lambda q: q[1])
                inside = bx[:4] + bx[-4:] + by_[:4] + by_[-4:] + inside
            variants = [([lab], sz) for sz in (26, 22, 19)]
            if iso in geo.PRIORITY_SPLIT:
                variants += [(list(geo.PRIORITY_SPLIT[iso]), sz) for sz in (24, 20, 17)]
            variants += [([lab], 16)]
            ok = False
            for lines, sz, beside in [(ln, sz, False) for ln, sz in variants] + [(ln, sz, True) for ln, sz in variants[-3:]]:
                fnt = font("Almarai-Bold.ttf", sz * S)
                tries = []
                for lx, ly in (inside if beside else pts):
                    X, Y = (lx - x0) / (x1 - x0) * W * S, (y1 - ly) / (y1 - y0) * PH * S
                    if not beside:
                        tries.append((X, Y))
                    else:                                             # last resort (a sliver of the country in view):
                        bw, _t, _l = text_box(lines, fnt, 0, 0)       # the name on the sea right next to it
                        hw, hh2 = (bw[2] - bw[0]) / 2 + 6 * S, (bw[3] - bw[1]) / 2 + 6 * S
                        tries += [(X - hw, Y), (X + hw, Y), (X, Y - hh2), (X, Y + hh2)]
                for X, Y in tries:
                    if not beside and not any(_pip(X, Y, r) for r in spts):
                        continue                                      # anchor must be inside the country
                    box, tw, lh = text_box(lines, fnt, X, Y)
                    if beside and not in_frame(box):                  # slide the sea label back inside the frame
                        X += max(0, 12 - box[0]) - max(0, box[2] - (W * S - 12))
                        Y += max(0, 12 - box[1]) - max(0, box[3] - (PH * S - 12))
                        box, tw, lh = text_box(lines, fnt, X, Y)
                    if not in_frame(box) or hits(box, placed):
                        continue
                    # the whole name sits on its own country (or on the sea), never across a border onto a neighbour
                    xs5 = [box[0] + 6 + (box[2] - box[0] - 12) * k / 4 for k in range(5)]
                    ys3 = [box[1] + 4, (box[1] + box[3]) / 2, box[3] - 4]
                    if any(not any(_pip(a, b, r) for r in spts) and any(_pip(a, b, r) for r in land)
                           for a in xs5 for b in ys3):
                        continue
                    placed.append(box)
                    draw_label(iso, lab, lines, X, Y, fnt, lh)
                    done.add(iso)
                    ok = True
                    break
                if ok:
                    break

    # everyone else
    f_lab = font("Almarai-Bold.ttf", 26 * S)
    cands = []
    for f in feats:
        lab = geo.label_of(f)
        p = f["properties"]
        if not lab or p.get("a") in done:
            continue
        lx, ly = p.get("l") or (None, None)
        if lx is None:
            continue
        for off in (0, 360, -360):
            if x0 <= lx + off <= x1 and y0 <= ly <= y1:
                fr = rings(f["geometry"])
                fx = [x + off for r in fr for x, _ in r]
                wpx = (max(fx) - min(fx)) / (x1 - x0) * W * S
                cands.append((p.get("r", 5), -wpx, lab, lx + off, ly, wpx, p.get("a")))
    for _r, _w, lab, lx, ly, wpx, iso in sorted(cands, key=lambda c: c[:2]):
        X, Y = (lx - x0) / (x1 - x0) * W * S, (y1 - ly) / (y1 - y0) * PH * S
        box, tw, lh = text_box([lab], f_lab, X, Y)
        if wpx < tw * 0.55:
            continue                                             # country too small on screen for its name
        if not in_frame(box) or hits(box, placed):
            continue
        placed.append(box)
        draw_label(iso, lab, [lab], X, Y, f_lab, lh)
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
