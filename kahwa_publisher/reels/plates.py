"""Plate clips for Kahwa reels v2: real footage / photos -> graded 1080x1920 H.264 clips.

Every shot of a reel is one plate clip. Nothing is generated: pixels come only from the
licensed source, re-framed with a slow camera move (push / pull / drift) and a fixed grade.

mode "full"   : cover-crop to 1080x1920 around the focus point.
mode "framed" : sharp source inside a 936x520 window (x72, y430) over a blurred, darkened copy
                of itself (the Al Jazeera "screen within a screen" grammar) - used for
                landscape footage so it is never upscaled past ~1.0x.
The plate renderer can also save exact frames ("grabs") used as GL-transition textures.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

W, H, FPS = 1080, 1920, 30
BOX = (72, 400, 936, 452)          # framed window x, y, w, h (2:1, clear of the caption block)
GRADES = {
    "std":  dict(contrast=1.06, sat=0.85, bright=0.97),
    "dark": dict(contrast=1.10, sat=0.55, bright=0.62),   # breaking-news backdrop under text
    "sat":  dict(contrast=1.04, sat=0.95, bright=0.98),   # satellite imagery: keep colours honest
}


def ease_inout(x):
    return 0.5 - 0.5 * np.cos(np.pi * min(max(x, 0.0), 1.0))


def grade_lut(contrast, bright):
    x = np.arange(256, dtype=np.float32) / 255
    y = ((x - 0.5) * contrast + 0.5) * bright
    return np.clip(y * 255, 0, 255).astype(np.uint8)


def apply_grade(img, g):
    lut = grade_lut(g["contrast"], g["bright"])
    out = cv2.LUT(img, lut)
    if abs(g["sat"] - 1) > 1e-3:
        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[..., None].astype(np.float32)
        out = np.clip(gray + g["sat"] * (out.astype(np.float32) - gray), 0, 255).astype(np.uint8)
    return out


# ------------------------------------------------------------------ sources
class Source:
    """Yields BGR frames for local time t (seconds) at a requested working size."""

    def __init__(self, media, root, dur):
        self.kind = media["kind"]
        self.path = str(Path(root) / media["file"])
        self.crop = media.get("crop")
        self.t_in = float(media.get("in", 0.0))
        self.dur = dur
        self.frames = None

    def size(self):
        if self.kind == "photo":
            w, h = Image.open(self.path).size
        else:
            pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                 "-of", "csv=p=0", self.path], capture_output=True, text=True).stdout.strip().split(",")
            w, h = int(pr[0]), int(pr[1])
        if self.crop:
            w, h = self.crop[2], self.crop[3]
        return w, h

    def load(self, scale_to):
        """scale_to: (w, h) of the working image (already cover-sized for the target box)."""
        sw, sh = scale_to
        if self.kind == "photo":
            im = Image.open(self.path).convert("RGB")
            if self.crop:
                x, y, w, h = self.crop
                im = im.crop((x, y, x + w, y + h))
            im = im.resize((sw, sh), Image.LANCZOS)
            self.frames = [cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)]
            self.fps = None
        elif self.kind == "gif":
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", self.path, "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                                 capture_output=True).stdout
            w0, h0 = [int(v) for v in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                                     self.path], capture_output=True, text=True).stdout.strip().split(",")[:2]]
            n = len(raw) // (w0 * h0 * 3)
            arr = np.frombuffer(raw, np.uint8)[: n * w0 * h0 * 3].reshape(n, h0, w0, 3)
            out = []
            for f in arr:
                if self.crop:
                    x, y, w, h = self.crop
                    f = f[y:y + h, x:x + w]
                out.append(cv2.resize(f, (sw, sh), interpolation=cv2.INTER_LANCZOS4))
            self.frames = out
            self.fps = 3.0          # 12 satellite frames (~7 h of real time) over 4 s
        else:
            vf = []
            if self.crop:
                x, y, w, h = self.crop
                vf.append(f"crop={w}:{h}:{x}:{y}")
            nw, nh = self.size()
            dw, dh = (sw, sh) if sw <= nw else (nw - nw % 2, int(round(nw * sh / sw)) // 2 * 2)   # never upscale in memory
            self.target = (sw, sh)
            vf += [f"fps={FPS}", f"scale={dw}:{dh}:flags=lanczos"]
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(self.t_in), "-i", self.path, "-t", str(self.dur + 0.2),
                                  "-an", "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
            n = len(raw) // (dw * dh * 3)
            self.frames = list(np.frombuffer(raw, np.uint8)[: n * dw * dh * 3].reshape(n, dh, dw, 3))
            self.fps = FPS

    def at(self, t):
        if self.fps is None:
            return self.frames[0]
        if self.kind == "gif":
            p = t * self.fps
            i = int(np.floor(p)) % len(self.frames)
            j = (i + 1) % len(self.frames)
            a = p - np.floor(p)
            return cv2.addWeighted(self.frames[i], 1 - a, self.frames[j], a, 0)
        i = min(int(round(t * FPS)), len(self.frames) - 1)
        f = self.frames[i]
        tw, th = getattr(self, "target", (f.shape[1], f.shape[0]))
        if f.shape[1] != tw:
            f = cv2.resize(f, (tw, th), interpolation=cv2.INTER_CUBIC)
        return f


def _cover(sw, sh, bw, bh, margin):
    s = max(bw / sw, bh / sh) * margin
    return max(bw, int(round(sw * s))), max(bh, int(round(sh * s)))


def _camera(img, bw, bh, focus, zoom, dx):
    """Crop a bw x bh window out of img (cover-sized * margin) at zoom >= 1 relative to cover, shifted by dx px."""
    ih, iw = img.shape[:2]
    cw, ch = bw / zoom, bh / zoom
    # cover size inside img (img was enlarged by margin); keep focus centred, clamp to edges
    cx = focus[0] * iw + dx
    cy = focus[1] * ih
    x0 = min(max(cx - cw / 2, 0), iw - cw)
    y0 = min(max(cy - ch / 2, 0), ih - ch)
    M = np.array([[bw / cw, 0, -x0 * bw / cw], [0, bh / ch, -y0 * bh / ch]], np.float32)
    return cv2.warpAffine(img, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def render_plate(media, root, out, dur, mode="full", move="in", grade="std", grabs=(), grab_dir=None, key="p"):
    """Render one plate clip; returns {grab_time: path}."""
    src = Source(media, root, dur)
    sw, sh = src.size()
    focus = media.get("focus", [0.5, 0.5])
    g = GRADES[grade]
    margin = 1.08
    if mode == "full":
        bw, bh = W, H
    else:
        bw, bh = BOX[2], BOX[3]
    ww, wh = _cover(sw, sh, bw, bh, margin)
    src.load((ww, wh))
    # zoomRel is relative to the plain cover; the working image is cover*margin, so zoomRel == margin is 1:1 pixels
    z_from, z_to = {"in": (1.02, 1.08), "out": (1.08, 1.02), "drift": (1.05, 1.05), "still": (1.03, 1.03)}[move]
    drift = {"in": -10, "out": 10, "drift": -36, "still": 0}[move]
    n = int(round(dur * FPS))
    grabs = sorted(set(round(t, 4) for t in grabs))
    grab_frames = {int(round(t * FPS)): t for t in grabs}
    saved = {}
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-g", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                           stdin=subprocess.PIPE)
    small = None
    for k in range(n):
        t = k / FPS
        e = ease_inout(t / max(dur, 1e-6))
        zrel = z_from + (z_to - z_from) * e
        dx = drift * (e - 0.5)
        frame = src.at(t)
        fg = _camera(frame, bw, bh, focus, zrel / margin, dx)
        if mode == "full":
            img = apply_grade(fg, g)
        else:
            if small is None or src.fps is not None:
                # blurred, darkened backdrop from the same frame (cover 1080x1920)
                bwid, bhgt = _cover(frame.shape[1], frame.shape[0], 270, 480, 1.0)
                sm = cv2.resize(frame, (bwid, bhgt), interpolation=cv2.INTER_AREA)
                x0 = (bwid - 270) // 2; y0 = (bhgt - 480) // 2
                sm = sm[y0:y0 + 480, x0:x0 + 270]
                sm = cv2.GaussianBlur(sm, (0, 0), 9)
                sm = apply_grade(sm, dict(contrast=1.0, sat=0.6, bright=0.36))
                small = cv2.resize(sm, (W, H), interpolation=cv2.INTER_CUBIC)
            img = small.copy()
            x, y, w2, h2 = BOX
            img[y:y + h2, x:x + w2] = apply_grade(fg, g)
        enc.stdin.write(img.tobytes())
        if k in grab_frames and grab_dir:
            p = Path(grab_dir) / f"{key}-{grab_frames[k]:.3f}.jpg"
            cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 93])
            saved[grab_frames[k]] = p
    enc.stdin.close()
    enc.wait()
    # any grab at/after the last frame -> last frame
    for t in grabs:
        if t not in saved and grab_dir:
            p = Path(grab_dir) / f"{key}-{t:.3f}.jpg"
            cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 93])
            saved[t] = p
    return saved
