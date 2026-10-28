"""Kahwa News silent explainer reel ("قهوة في 30 ثانية").

Research 2026-10-28 (work/research-reels-2026-10-28.md):
- 9:16 1080x1920, 30 fps, 20-40 s (Zernio accepts 3-60 s).
- Key fact on screen from frame 0, no logo intro; burned-in Arabic text (most viewers watch muted).
- All text inside the safe zone x 65-950, y 270-1240 (clear of top 14%, bottom 35%, right button bar).
- NO AI voice: a realistic AI voice-over requires Meta's "AI info" label, which our publishers can't set.
- End card: source line + brand. No links, no requests to like/comment/share.
"""
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .card import cover
from .cards import F, LAT, T, fit, tl, draw_rich, RED, WHITE, MUTED, NAVY0, NAVY1, PUNCT

W, H, FPS = 1080, 1920, 30
SX0, SX1, SY0, SY1 = 65, 950, 270, 1240          # text safe zone
XR = SX1


def _bg(photo):
    if photo is not None:
        im = cover(photo, W, H).convert("RGB").filter(ImageFilter.GaussianBlur(2))
    else:
        im = Image.new("RGB", (W, H), NAVY0)
    shade = Image.new("L", (1, H))
    for y in range(H):
        shade.putpixel((0, y), int(150 + 80 * abs(y - 760) / H))
    return Image.composite(Image.new("RGB", (W, H), (6, 8, 12)), im, shade.resize((W, H)))


def _brand(d):
    lw = LAT(40)
    x = SX0
    d.text((x, SY0 - 70), "KAHWA", font=lw, fill=WHITE, anchor="lm")
    bx = x + d.textlength("KAHWA", font=lw) + 10
    d.polygon([(bx + 6, SY0 - 88), (bx + 100, SY0 - 88), (bx + 92, SY0 - 52), (bx, SY0 - 52)], fill=RED)
    d.text((bx + 50, SY0 - 70), "NEWS", font=LAT(22), fill=WHITE, anchor="mm")


def _box_lines(d, text, y, size=84, minimum=64, box=RED, ink=WHITE, max_lines=3):
    f, lines, s = fit(d, text, SX1 - SX0 - 60, size, minimum, max_lines)
    lh = int(s * 1.45)
    for ln in lines:
        w = tl(d, ln, f)
        d.rectangle([XR - w - 28, y - 6, XR + 14, y + lh - 14], fill=box)
        T(d, (XR - 6, y + 4), ln, f, ink)
        y += lh
    return y


def slide_hook(photo, kicker, title, sub, hl, urgent):
    im = _bg(photo)
    d = ImageDraw.Draw(im)
    _brand(d)
    y = 760
    if kicker:
        T(d, (XR, y - 80), ("عاجل | " if urgent else "") + kicker, F("ExtraBold", 44), RED if urgent else WHITE)
    y = _box_lines(d, title, y, box=RED if urgent else WHITE, ink=WHITE if urgent else (12, 14, 20))
    if sub:
        f, lines, s = fit(d, sub, SX1 - SX0, 58, 46, 4, "Bold")
        draw_rich(d, lines, XR, y + 30, f, int(s * 1.45), hl, hl_color=(255, 77, 64))
    return im


def slide_point(photo, n, total, text, hl):
    im = _bg(photo)
    d = ImageDraw.Draw(im)
    _brand(d)
    T(d, (XR, 560), f"{n}/{total}", F("ExtraBold", 54), RED)
    d.rounded_rectangle([XR - 110, 640, XR, 650], 5, fill=RED)
    f, lines, s = fit(d, text, SX1 - SX0, 76, 58, 5, "Bold")
    draw_rich(d, lines, XR, 700, f, int(s * 1.5), hl, hl_color=(255, 77, 64))
    return im


def slide_number(photo, value, label):
    im = _bg(photo)
    d = ImageDraw.Draw(im)
    _brand(d)
    f, _, _ = fit(d, value, SX1 - SX0, 190, 110, 1)
    T(d, (XR, 620), value, f, (255, 77, 64))
    fl, lines, s = fit(d, label, SX1 - SX0, 70, 54, 3, "Bold")
    draw_rich(d, lines, XR, 900, fl, int(s * 1.45), set())
    return im


def slide_end(source):
    im = Image.new("RGB", (W, H), NAVY0)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(NAVY1[i] * (1 - t) + NAVY0[i] * t) for i in range(3)))
    lw = LAT(120)
    tw = d.textlength("KAHWA", font=lw)
    x = (W - tw - 260) / 2
    d.text((x, 700), "KAHWA", font=lw, fill=WHITE, anchor="lm")
    bx = x + tw + 20
    d.polygon([(bx + 16, 650), (bx + 250, 650), (bx + 230, 750), (bx, 750)], fill=RED)
    d.text((bx + 125, 700), "NEWS", font=LAT(64), fill=WHITE, anchor="mm")
    T(d, (W // 2, 860), "قهوة نيوز | العالم كما هو", F("Bold", 50), WHITE, "ma")
    if source:
        T(d, (W // 2, 990), "المصادر: " + source, F("Bold", 40), MUTED, "ma")
    return im


def beats_from(d):
    """3-4 short beats from facts / numbers / the post body (already fact-checked)."""
    if len(d.get("facts") or []) >= 3:
        return [("point", f) for f in d["facts"][:4]]
    out = [("number", n) for n in (d.get("numbers") or [])[:2]]
    body = d.get("post", "").split("المصادر")[0]
    body = re.sub(r"#\S+", "", body)
    sents = [s.strip(" .،\n") for s in re.split(r"[.\n؟!]", body) if 25 <= len(s.strip()) <= 160]
    for s in sents[1:]:
        if len(out) >= 4:
            break
        out.append(("point", s))
    return out


def render_reel(d, photo, source, out):
    """d: the newsroom item (title/subline/kicker/highlight/facts/numbers/post/urgent). Returns seconds or None."""
    beats = beats_from(d)
    if len(beats) < 2:
        return None
    hl = {w.strip(PUNCT) for w in (d.get("highlight") or "").split()}
    img = photo[0] if photo else None
    slides = [(slide_hook(img, d.get("kicker"), d.get("title") or d.get("headline"), d.get("subline") or "",
                          hl, d.get("urgent")), 5.0)]
    pts = [b for b in beats if b[0] == "point"]
    for kind, b in beats:
        if kind == "number":
            slides.append((slide_number(img, str(b.get("value", "")), str(b.get("label", ""))), 4.0))
        else:
            n = pts.index((kind, b)) + 1
            slides.append((slide_point(img, n, len(pts), b, hl), 3.0 + min(3.0, len(b) / 30)))
    slides.append((slide_end(source), 3.0))
    with tempfile.TemporaryDirectory() as tmp:
        inputs, filters, offs, total = [], [], [], 0.0
        for i, (im, dur) in enumerate(slides):
            p = Path(tmp) / f"s{i}.png"
            im.save(p)
            inputs += ["-loop", "1", "-t", f"{dur + 0.5:.2f}", "-i", str(p)]
            frames = int((dur + 0.5) * FPS)
            # gentle Ken Burns push-in (1.00 -> 1.04) keeps the frame alive without moving text out of the safe zone
            filters.append(f"[{i}:v]scale={W*2}:{H*2},zoompan=z='1+0.04*on/{frames}':x='iw/2-(iw/zoom/2)':"
                           f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[v{i}]")
            offs.append(dur)
        last = "v0"
        t = offs[0]
        for i in range(1, len(slides)):
            filters.append(f"[{last}][v{i}]xfade=transition=fade:duration=0.5:offset={t:.2f}[x{i}]")
            last = f"x{i}"
            t += offs[i]
        total = t + 0.5
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
               "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
               "-filter_complex", ";".join(filters), "-map", f"[{last}]", "-map", f"{len(slides)}:a",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, timeout=600)
    return round(total, 1)
