"""Kahwa News card system v4 — sibling of the Kahwa Sports card family.

Same visual DNA as Kahwa Sports (navy gradient + diagonal light streaks, Almarai typography,
rounded navy panels, brand block 'قهوة نيوز' + date + dots, accent underline), with brand RED
instead of sports yellow. Templates benchmarked on CNN Arabic, Sky News Arabia, Al Jazeera (AJA
albums), BBC Arabic and France 24 Facebook/Instagram output (Oct 2026):

  breaking  — #عاجل: red band, big statement, optional photo strip
  photo     — header + framed real photo + headline (CNN 'white card' logic, in navy)
  quote     — تصريح: speaker photo in a circle, big red quote marks, quote, name + role
  numbers   — أرقام: 2-4 big figures with labels in panels (AJA numbers album)
  facts     — ماذا نعرف؟: 3-4 numbered rows, red number tab (Kahwa Sports 'levels' layout)
  map       — country map in a frame + headline (fallback when no photo exists)

All text is right-to-left via Raqm. Every card carries a source line and a photo credit.
Source line (2026-10-29): «المصدر: <primary source>» (official body > wire agency > «وكالات»), never a competing outlet;
our own compilations (numbers / facts cards, maps) add «إعداد: قهوة نيوز». Photos credited to an outlet are skipped.
"""
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import attribution as A
from .card import cover, map_image, wiki_photo, commons_photo, openverse_photo

ASSETS = Path(__file__).resolve().parent.parent / "assets"
W, H = 1080, 1350
NAVY0, NAVY1 = (9, 11, 16), (22, 27, 38)          # palette B: desaturated ink (trust > saturated navy)
PANEL, PANEL_LINE = (24, 29, 40), (50, 58, 74)
RED, RED_DARK = (220, 24, 12), (160, 14, 8)
WHITE, MUTED = (255, 255, 255), (165, 172, 186)
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر",
          "نوفمبر", "ديسمبر"]
PUNCT = "،.:؛!؟\"«»()"


def F(weight, size):
    return ImageFont.truetype(str(ASSETS / f"Almarai-{weight}.ttf"), size, layout_engine=ImageFont.Layout.RAQM)


def LAT(size):
    return ImageFont.truetype(str(ASSETS / "Barlow-BlackItalic.ttf"), size, layout_engine=ImageFont.Layout.BASIC)


def T(d, xy, text, f, fill, anchor="ra"):
    d.text(xy, text, font=f, fill=fill, anchor=anchor, direction="rtl", language="ar")


def tl(d, text, f):
    return d.textlength(text, font=f, direction="rtl", language="ar")


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in (text or "").split():
        t = (cur + " " + w).strip()
        if tl(d, t, f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(d, text, width, start, minimum, max_lines, weight="ExtraBold"):
    size = start
    while True:
        f = F(weight, size)
        lines = wrap(d, text, f, width)
        if len(lines) <= max_lines or size <= minimum:
            return f, lines[:max_lines], size
        size -= 3


def draw_rich(d, lines, x_right, y, f, lh, hl, color=WHITE, hl_color=None):
    """Right-aligned lines, words found in `hl` painted red."""
    space = d.textlength(" ", font=f)
    for ln in lines:
        x = x_right
        for w in ln.split():
            T(d, (x, y), w, f, (hl_color or RED) if w.strip(PUNCT) in hl else color)
            x -= tl(d, w, f) + space
        y += lh
    return y


# ------------------------------------------------------------------ shared chrome
def background():
    """Navy vertical gradient + soft diagonal light streaks (Kahwa Sports background family)."""
    bg = Image.new("RGB", (W, H))
    px = bg.load()
    for y in range(H):
        t = y / H
        c = tuple(int(NAVY1[i] * (1 - t) * 0.9 + NAVY0[i] * (0.1 + t * 0.9)) for i in range(3))
        for x in range(W):
            px[x, y] = c
    streak = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(streak)
    for i, (x0, wdt, a) in enumerate(((-200, 160, 26), (80, 60, 18), (520, 220, 14), (900, 90, 20))):
        sd.polygon([(x0 + 600, 0), (x0 + 600 + wdt, 0), (x0 + wdt, H), (x0, H)], fill=a)
    streak = streak.filter(ImageFilter.GaussianBlur(40))
    bg = Image.composite(Image.new("RGB", (W, H), (60, 70, 92)), bg, streak)
    return bg


def header(d, kicker, title, hl=(), title_lines=2, kicker_color=RED):
    """Right: red kicker + big white title + red underline. Left: brand block + date + dots."""
    # brand block (left)
    fb = F("ExtraBold", 40)
    T(d, (64, 92), "قهوة نيوز", fb, WHITE, "la")
    cup_x = 64 + int(tl(d, "قهوة نيوز", fb)) + 18
    d.rounded_rectangle([cup_x, 74, cup_x + 34, 118], 7, outline=RED, width=5)
    d.arc([cup_x + 24, 82, cup_x + 46, 106], 270, 90, fill=RED, width=5)
    for i in range(3):
        d.line([(cup_x + 8 + i * 9, 66), (cup_x + 12 + i * 9, 54)], fill=WHITE, width=3)
    now = datetime.now(timezone.utc)
    T(d, (64, 146), f"{now.day} {MONTHS[now.month - 1]} {now.year}", F("Bold", 24), MUTED, "la")
    for i in range(5):
        d.regular_polygon((72 + i * 30, 190, 7), 4, rotation=45, fill=RED)
    # kicker + title (right)
    xr = W - 64
    T(d, (xr, 70), kicker or "دولي", F("ExtraBold", 36), kicker_color)
    f, lines, size = fit(d, title, 600, 78, 50, title_lines)
    lh = int(size * 1.28)
    y = draw_rich(d, lines, xr, 122, f, lh, set(hl))
    d.rounded_rectangle([xr - 120, y + 16, xr, y + 26], 5, fill=RED)
    return y + 60


def source_label(source, compiled=False):
    """Card source text. Outlet names can never reach a card (falls back to «وكالات»)."""
    src = (source or "").strip()
    if not src or A.outlet_hits("المصدر: " + src):
        src = A.GENERIC
    return (f"إعداد: {A.OURS} · " if compiled else "") + "المصدر: " + src


def footer(d, source, credit, compiled=False):
    d.line([(64, H - 96), (W - 64, H - 96)], fill=PANEL_LINE, width=2)
    lab = source_label(source, compiled)
    f = F("Bold", 24)
    while tl(d, lab, f) > W - 128 - 260 and f.size > 16:
        f = F("Bold", f.size - 2)
    T(d, (W - 64, H - 56), lab, f, MUTED)
    lw = LAT(30)
    d.text((64, H - 56), "KAHWA", font=lw, fill=WHITE, anchor="lm")
    bx = 64 + d.textlength("KAHWA", font=lw) + 8
    d.polygon([(bx + 6, H - 70), (bx + 80, H - 70), (bx + 74, H - 42), (bx, H - 42)], fill=RED)
    d.text((bx + 40, H - 55), "NEWS", font=LAT(18), fill=WHITE, anchor="mm")
    if credit:
        T(d, (W - 64, H - 112), ("صورة أرشيفية: " + credit)[:80] if not credit.startswith("خريطة") else credit,
          F("Regular", 18), (110, 126, 160))


def framed(canvas, img, box, radius=30):
    x0, y0, x1, y1 = box
    pic = cover(img, x1 - x0, y1 - y0)
    m = Image.new("L", pic.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, pic.width - 1, pic.height - 1], radius, fill=255)
    canvas.paste(pic, (x0, y0), m)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, outline=PANEL_LINE, width=3)


def find_photo(entity=None, photo_query=None):
    for get in (lambda: wiki_photo(entity), lambda: commons_photo(photo_query), lambda: openverse_photo(photo_query)):
        ph = get()
        if ph and not A.outlet_hits(ph[1] or ""):      # never credit (= advertise) a competing outlet under our card
            return ph
    return None


# ------------------------------------------------------------------ templates
def t_photo(c, d, p, photo, mp):
    y = header(d, p.get("kicker"), p.get("title") or p["headline"], p.get("hl", ()), 2)
    img, credit = photo or mp or (None, "")
    if img is None:     # no photo and no map (e.g. a no-focus territory or unknown country): big text-only card
        f, lines, size = fit(d, p["headline"], W - 128, 84, 56, 5, "ExtraBold")
        lh = int(size * 1.45)
        top = y + 10 + max(0, (H - 150 - (y + 10) - len(lines) * lh) // 2)
        d.line([(W - 64 - 120, top - 40), (W - 64, top - 40)], fill=RED, width=8)
        draw_rich(d, lines, W - 64, top, f, lh, set(p.get("hl", ())))
        return credit
    f, lines, size = fit(d, p["headline"], W - 128, 56, 40, 3, "Bold")
    lh = int(size * 1.45)
    text_h = len(lines) * lh
    box = (64, y + 10, W - 64, H - 150 - text_h - 40)
    if img is not None:
        framed(c, img, box)
    draw_rich(d, lines, W - 64, box[3] + 34, f, lh, set(p.get("hl", ())))
    return credit


def t_breaking(c, d, p, photo, mp):
    d.rectangle([0, 0, W, 16], fill=RED)
    y = header(d, "عاجل", p.get("title") or "خبر عاجل", (), 1, kicker_color=RED)
    d.rounded_rectangle([W - 64 - 190, y - 4, W - 64, y + 58], 12, fill=RED)
    T(d, (W - 64 - 95, y + 28), "#عاجل", F("ExtraBold", 34), WHITE, "mm")
    f, lines, size = fit(d, p["headline"], W - 128, 76, 52, 4)
    lh = int(size * 1.4)
    yy = draw_rich(d, lines, W - 64, y + 100, f, lh, set(p.get("hl", ())))
    img, credit = photo or mp or (None, "")
    if img is not None and H - 170 - yy > 280:
        framed(c, img, (64, yy + 36, W - 64, H - 150))
    return credit


def t_quote(c, d, p, photo, mp):
    y = header(d, p.get("kicker") or "تصريح", p.get("title") or p.get("speaker", ""), (), 2)
    q = p.get("quote") or p["headline"]
    panel = (64, y + 30, W - 64, H - 150)
    d.rounded_rectangle(panel, 30, fill=PANEL, outline=PANEL_LINE, width=2)
    # speaker circle
    cx, cy, r = W // 2, panel[1] + 150, 115
    if photo:
        im = cover(photo[0], 2 * r, 2 * r)
        m = Image.new("L", im.size, 0)
        ImageDraw.Draw(m).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
        c.paste(im, (cx - r, cy - r), m)
    d.ellipse([cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6], outline=RED, width=7)
    T(d, (W - 100, panel[1] + 40), "”", F("ExtraBold", 200), RED, "ra")
    T(d, (100, cy + r + 10), "“", F("ExtraBold", 200), (60, 80, 130), "la")
    f, lines, size = fit(d, f"{q}", W - 240, 58, 40, 5)
    lh = int(size * 1.5)
    yy = cy + r + 70
    for ln in lines:
        T(d, (W // 2, yy), ln, f, WHITE, "ma")
        yy += lh
    T(d, (W // 2, yy + 26), p.get("speaker", ""), F("ExtraBold", 36), RED, "ma")
    T(d, (W // 2, yy + 76), p.get("role", ""), F("Regular", 26), MUTED, "ma")
    return photo[1] if photo else ""


def t_numbers(c, d, p, photo, mp):
    y = header(d, p.get("kicker") or "بالأرقام", p.get("title") or p["headline"], p.get("hl", ()), 2)
    nums = (p.get("numbers") or [])[:4]
    n = len(nums)
    cols = 2 if n >= 2 else 1
    rows = (n + cols - 1) // cols
    gap = 26
    top, bottom = y + 20, H - 150 - (300 if photo or mp else 0)
    ph = (bottom - top - gap * (rows - 1)) // max(rows, 1)
    pw = (W - 128 - gap * (cols - 1)) // cols
    for i, it in enumerate(nums):
        r_, c_ = divmod(i, cols)
        x1 = W - 64 - c_ * (pw + gap)
        x0, y0 = x1 - pw, top + r_ * (ph + gap)
        d.rounded_rectangle([x0, y0, x1, y0 + ph], 26, fill=PANEL, outline=PANEL_LINE, width=2)
        d.rounded_rectangle([x1 - 14, y0 + 30, x1, y0 + ph - 30], 6, fill=RED)
        T(d, ((x0 + x1) // 2, y0 + ph * 0.42), str(it.get("value", "")), F("ExtraBold", min(110, int(ph * 0.42))), WHITE, "mm")
        fl, ll, s2 = fit(d, it.get("label", ""), pw - 60, 30, 22, 2, "Bold")
        yy = y0 + ph * 0.68
        for ln in ll:
            T(d, ((x0 + x1) // 2, yy), ln, fl, MUTED, "ma")
            yy += int(s2 * 1.35)
    img, credit = photo or mp or (None, "")
    if img is not None:
        framed(c, img, (64, bottom + 26, W - 64, H - 150))
    return credit


def t_facts(c, d, p, photo, mp):
    y = header(d, p.get("kicker") or "ماذا نعرف؟", p.get("title") or p["headline"], p.get("hl", ()), 2)
    facts = (p.get("facts") or [])[:4]
    img, credit = photo or mp or (None, "")
    bottom = H - 150 - (260 if img is not None and len(facts) <= 3 else 0)
    gap = 22
    rh = (bottom - y - 10 - gap * (len(facts) - 1)) // max(len(facts), 1)
    for i, fact in enumerate(facts):
        y0 = y + 10 + i * (rh + gap)
        d.rounded_rectangle([64, y0, W - 64, y0 + rh], 24, fill=PANEL, outline=PANEL_LINE, width=2)
        d.rounded_rectangle([W - 64 - 120, y0, W - 64, y0 + rh], 24, fill=RED)
        d.rectangle([W - 64 - 120, y0, W - 64 - 96, y0 + rh], fill=RED)
        T(d, (W - 64 - 60, y0 + rh // 2), str(i + 1), F("ExtraBold", 72), WHITE, "mm")
        fl, ll, s2 = fit(d, fact, W - 128 - 170, 38, 26, 3, "Bold")
        lh = int(s2 * 1.45)
        yy = y0 + (rh - len(ll) * lh) // 2 + 4
        for ln in ll:
            T(d, (W - 64 - 150, yy), ln, fl, WHITE)
            yy += lh
    if img is not None and len(facts) <= 3:
        framed(c, img, (64, bottom + 26, W - 64, H - 150))
    return credit


def overlay(p, photo, out):
    """Global news style (Sky News Arabia / Al Arabiya / CNN social): full-bleed real photo, dark bottom
    fade, first headline line in a solid red box, the rest in white beneath; small brand + source."""
    img, credit = photo
    c = cover(img, W, H).convert("RGB")
    fade = Image.new("L", (1, H), 0)
    for y in range(H):
        t = max(0.0, (y - H * 0.42) / (H * 0.58))
        fade.putpixel((0, y), int(250 * min(1, t ** 1.15)))
    fade = fade.resize((W, H))
    c = Image.composite(Image.new("RGB", (W, H), (6, 8, 12)), c, fade)
    d = ImageDraw.Draw(c)
    xr, maxw = W - 60, W - 120
    head = (p.get("title") or p["headline"]).strip()
    sub = (p.get("headline") if p.get("title") else "").strip()
    if sub == head:
        sub = ""
    # line 1: red box (the hook), up to 2 lines
    fb, bl, bs = fit(d, head, maxw - 60, 76, 68, 2)
    fs, sl, ss = fit(d, sub, maxw, 54, 44, 3, "Bold") if sub else (None, [], 0)
    blh, slh = int(bs * 1.42), int(ss * 1.42)
    y = H - 140 - len(sl) * slh - (24 if sl else 0) - len(bl) * blh
    if p.get("card_type") == "breaking":
        T(d, (xr, y - 66), "عاجل", F("ExtraBold", 34), WHITE)
        d.ellipse([xr - tl(d, "عاجل", F("ExtraBold", 34)) - 34, y - 52, xr - tl(d, "عاجل", F("ExtraBold", 34)) - 16, y - 34], fill=RED)
    urgent = p.get("card_type") == "breaking"
    box, ink = (RED, WHITE) if urgent else (WHITE, (12, 14, 20))   # red box only for breaking (evidence: red = low trust)
    for ln in bl:
        w = tl(d, ln, fb)
        d.rectangle([xr - w - 28, y - 4, xr + 14, y + blh - 10], fill=box)
        T(d, (xr - 6, y + 4), ln, fb, ink)
        y += blh
    y += 24
    draw_rich(d, sl, xr, y, fs, slh, set(p.get("hl", ())), hl_color=(255, 77, 64)) if sl else None
    # brand chip top-right, source + credit bottom
    lw = LAT(34)
    d.text((W - 60 - 112 - d.textlength("KAHWA", font=lw), 74), "KAHWA", font=lw, fill=WHITE, anchor="lm",
           stroke_width=1, stroke_fill=(0, 0, 0))
    d.polygon([(W - 60 - 84, 58), (W - 60, 58), (W - 66, 90), (W - 150, 90)], fill=RED)
    d.text((W - 60 - 75, 74), "NEWS", font=LAT(20), fill=WHITE, anchor="mm")
    d.line([(60, H - 100), (W - 60, H - 100)], fill=(60, 66, 80), width=2)
    lab = source_label(p.get("source"))
    fo = F("Bold", 30)
    while tl(d, lab, fo) > W - 120 - 300 and fo.size > 18:
        fo = F("Bold", fo.size - 2)
    T(d, (W - 60, H - 56), lab, fo, MUTED)
    now = datetime.now(timezone.utc)
    T(d, (60, H - 56), f"{now.day} {MONTHS[now.month - 1]} {now.year}", F("Bold", 30), MUTED, "la")
    if credit:
        d.text((60, 74), ("Photo: " + credit)[:60], font=ImageFont.truetype(str(ASSETS / "Almarai-Regular.ttf"), 17),
               fill=(235, 235, 235), anchor="lm", stroke_width=2, stroke_fill=(0, 0, 0))
    c.save(out, "JPEG", quality=93, optimize=True, progressive=True)
    return credit


TEMPLATES = {"photo": t_photo, "breaking": t_breaking, "quote": t_quote, "numbers": t_numbers,
             "facts": t_facts, "map": t_photo}


def render_card(p, out):
    """p: dict with card_type, headline, title, kicker, highlight, entity, photo_query, country_en, source,
    quote/speaker/role, numbers=[{value,label}], facts=[str]."""
    ctype = p.get("card_type") if p.get("card_type") in TEMPLATES else "photo"
    if ctype == "quote" and not (p.get("quote") and p.get("speaker")):
        ctype = "photo"
    if ctype == "numbers" and len(p.get("numbers") or []) < 2:
        ctype = "photo"
    if ctype == "facts" and len(p.get("facts") or []) < 3:
        ctype = "photo"
    p = dict(p, hl=[w.strip(PUNCT) for w in (p.get("highlight") or "").split()])
    if p.get("no_photo"):                       # war/violence: Meta never recommends such imagery -> own visual
        if ctype in ("photo", "breaking", "quote"):
            ctype = "facts" if len(p.get("facts") or []) >= 3 else ("numbers" if len(p.get("numbers") or []) >= 2 else "map")
    photo = None if ctype == "map" or p.get("no_photo") else find_photo(p.get("entity"), p.get("photo_query"))
    if photo and ctype in ("photo", "breaking"):
        return {"card_type": ctype, "style": "overlay", "credit": overlay(p, photo, out)}
    mp = None if photo else map_image(p.get("country_en"))
    c = background()
    d = ImageDraw.Draw(c)
    credit = TEMPLATES[ctype](c, d, p, photo, mp)
    footer(d, p.get("source", ""), credit, compiled=ctype in ("numbers", "facts", "map"))
    c.save(out, "JPEG", quality=93, optimize=True, progressive=True)
    return {"card_type": ctype, "credit": credit}
