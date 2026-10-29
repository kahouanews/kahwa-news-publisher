"""Kahwa News opener — procedural globe plate (numpy + OpenCV, CPU, deterministic).

Orthographic Earth from NASA Blue Marble NG (day, Oct 2004) + NASA Black Marble 2016 (night lights),
both public-domain NASA imagery. No borders, no labels, no AI imagery.
Renders a 1080x1920 background plate: space field, stars, globe with moving terminator (night -> dawn),
atmosphere rim, ocean glint, sunrise glare on the limb, two 3D orbit rings with real occlusion, bloom.

    python globe.py --out plate.mp4 --dur 10.17 [--fps 30] [--frames 0,60,120 --png-dir dbg/]
"""
import argparse, math, subprocess, sys, os
from pathlib import Path
import numpy as np
import cv2

HERE = Path(__file__).resolve().parent
W, H = 1080, 1920
NAVY = np.array([0x0A, 0x10, 0x1C], np.float32) / 255.0      # brand navy (RGB)
RED = np.array([0xDC, 0x18, 0x0C], np.float32) / 255.0


def ease(x):                       # cubic in-out, clamped
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def eout(x):                       # expo-ish out
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 4


def lerp(a, b, k):
    return a + (b - a) * k


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def norm(v):
    v = np.asarray(v, np.float64)
    return v / np.linalg.norm(v)


class Globe:
    def __init__(self, tex_w=8192):
        day = cv2.imread(str(HERE / "assets" / "day_8k.jpg"), cv2.IMREAD_COLOR)
        night = cv2.imread(str(HERE / "assets" / "night_8k.jpg"), cv2.IMREAD_COLOR)
        if tex_w != day.shape[1]:
            day = cv2.resize(day, (tex_w, tex_w // 2), interpolation=cv2.INTER_AREA)
            night = cv2.resize(night, (tex_w, tex_w // 2), interpolation=cv2.INTER_AREA)
        # pad one column so longitude wrap samples cleanly
        self.day = np.concatenate([day, day[:, :2]], 1)
        self.night = np.concatenate([night, night[:, :2]], 1)
        self.tw, self.th = day.shape[1], day.shape[0]
        rng = np.random.default_rng(7)
        n = 900
        self.stars = np.stack([rng.uniform(0, W, n), rng.uniform(0, H, n), rng.uniform(0.15, 1.0, n) ** 2.2,
                               rng.uniform(0.4, 1.0, n)], 1)        # x, y, brightness, depth (parallax)

    # ---------------------------------------------------------------- one frame
    def frame(self, P):
        """P: dict with cx, cy, R, lon0, lat0 (deg), sun (3-vector camera space), rings [list], glare, exposure, drift"""
        img = self.background(P)
        cx, cy, R = P["cx"], P["cy"], P["R"]
        pad = int(R * 0.45) + 4
        x0, x1 = max(0, int(cx - R - pad)), min(W, int(cx + R + pad) + 1)
        y0, y1 = max(0, int(cy - R - pad)), min(H, int(cy + R + pad) + 1)
        if x1 > x0 and y1 > y0:
            self.draw_globe(img, P, x0, x1, y0, y1)
        for ring in P.get("rings", []):
            self.draw_ring(img, P, ring)
        img = self.bloom(img, P)
        return img

    def background(self, P):
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        top = np.array([0.020, 0.030, 0.058], np.float32)
        bot = NAVY * 0.9
        img = np.broadcast_to(lerp(top, bot, yy), (H, W, 3)).copy()
        # faint cool haze behind the globe
        cx, cy, R = P["cx"], P["cy"], P["R"]
        X, Y = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) / (R * 1.9)
        img += (np.exp(-d * d * 2.2) * 0.07)[..., None] * np.array([0.25, 0.45, 0.9], np.float32)
        # stars (parallax drift, twinkle-free = clean)
        s = self.stars
        drift = P.get("drift", 0.0)
        xs = (s[:, 0] + drift * 60 * s[:, 3]) % W
        ys = (s[:, 1] - drift * 140 * s[:, 3]) % H
        xi, yi = xs.astype(int), ys.astype(int)
        b = (s[:, 2] * 0.85 * P.get("stars", 1.0)).astype(np.float32)
        for dx, dy, f in ((0, 0, 1.0), (1, 0, 0.35), (0, 1, 0.35), (-1, 0, 0.35), (0, -1, 0.35)):
            xx, yy2 = np.clip(xi + dx, 0, W - 1), np.clip(yi + dy, 0, H - 1)
            img[yy2, xx] += (b * f * (0.35 if dx or dy else 1.0))[:, None] * np.array([0.85, 0.9, 1.0], np.float32)
        return img

    def rot(self, P):
        lon, lat = math.radians(P["lon0"]), math.radians(P["lat0"])
        # camera looks at (lon, lat). Build world<-camera rotation.
        Ry = np.array([[math.cos(lon), 0, math.sin(lon)], [0, 1, 0], [-math.sin(lon), 0, math.cos(lon)]])
        Rx = np.array([[1, 0, 0], [0, math.cos(lat), math.sin(lat)], [0, -math.sin(lat), math.cos(lat)]])
        return Ry @ Rx      # v_world = M @ v_cam

    def draw_globe(self, img, P, x0, x1, y0, y1):
        cx, cy, R = P["cx"], P["cy"], P["R"]
        X, Y = np.meshgrid(np.arange(x0, x1, dtype=np.float32) + 0.5, np.arange(y0, y1, dtype=np.float32) + 0.5)
        u = (X - cx) / R
        v = -(Y - cy) / R
        r2 = u * u + v * v
        r = np.sqrt(r2)
        inside = r2 < 1.0
        z = np.sqrt(np.clip(1 - r2, 0, 1))
        n_cam = np.stack([u, v, z], -1)                         # sphere normal in camera space
        M = self.rot(P).astype(np.float32)
        n_w = n_cam @ M.T
        lat = np.arcsin(np.clip(n_w[..., 1], -1, 1))
        lon = np.arctan2(n_w[..., 0], n_w[..., 2])
        mx = ((lon / (2 * np.pi) + 0.5) * self.tw).astype(np.float32) % self.tw
        my = ((0.5 - lat / np.pi) * self.th).astype(np.float32)
        day = cv2.remap(self.day, mx, my, cv2.INTER_LINEAR).astype(np.float32)[..., ::-1] / 255.0
        night = cv2.remap(self.night, mx, my, cv2.INTER_LINEAR).astype(np.float32)[..., ::-1] / 255.0
        sun = norm(P["sun"]).astype(np.float32)
        ndl = n_cam @ sun
        lit = smooth(-0.08, 0.22, ndl)
        # day shading: lambert + soft wrap, slight desaturation toward limb (aerial perspective)
        lamb = np.clip(ndl * 0.9 + 0.1, 0, 1) ** 0.85
        day_c = day ** 1.05 * 1.12 * lamb[..., None]
        # ocean glint (ocean = blue-dominant dark pixels)
        ocean = smooth(0.02, 0.10, day[..., 2] - day[..., 0]) * (day.sum(-1) < 0.9)
        h = norm(sun + np.array([0, 0, 1], np.float32)).astype(np.float32)
        spec = np.clip(n_cam @ h, 0, 1) ** 140 * ocean * lit
        day_c += spec[..., None] * np.array([1.0, 0.92, 0.78], np.float32) * 0.45
        # night: city lights, warm, only on the dark side
        lum = night.mean(-1, keepdims=True)
        nl = smooth(0.16, 0.55, lum) * (lum ** 0.9) * 2.4 * np.array([1.0, 0.80, 0.50], np.float32)
        base_night = night.mean(-1, keepdims=True) * np.array([0.05, 0.075, 0.13], np.float32) + np.array([0.004, 0.008, 0.016], np.float32)
        dark = (1 - lit)[..., None]
        col = day_c * lit[..., None] + (nl + base_night) * dark
        # terminator warmth (dawn band)
        band = np.exp(-((ndl - 0.02) / 0.07) ** 2)[..., None]
        col += band * np.array([1.0, 0.45, 0.18], np.float32) * 0.10 * (0.4 + 0.6 * day.mean(-1, keepdims=True))
        # atmosphere fresnel inside the disc
        fres = ((1 - z) ** 2.6)[..., None]
        atm_col = np.array([0.35, 0.62, 1.0], np.float32)
        col = col * (1 - fres * 0.55) + fres * atm_col * (0.12 + 0.95 * np.clip(ndl + 0.25, 0, 1))[..., None]
        col *= P.get("exposure", 1.0)
        alpha = np.clip((1.0 - r) * R + 0.5, 0, 1)[..., None]
        region = img[y0:y1, x0:x1]
        region[:] = region * (1 - alpha) + col * alpha
        # outer atmosphere glow (outside the disc)
        outside = np.clip(r - 1.0, 0, None)
        limb_dir = np.stack([u, v, np.zeros_like(u)], -1) / np.maximum(r, 1e-6)[..., None]
        ldl = np.clip(limb_dir @ sun + 0.35, 0, 1.35)
        glow = np.exp(-outside / 0.022) * 0.85 + np.exp(-outside / 0.07) * 0.25
        glow = glow * (0.10 + ldl) * (r >= 0.985)
        region += glow[..., None] * atm_col * 0.9 * P.get("atmo", 1.0)
        # sunrise glare on the limb
        g = P.get("glare", 0.0)
        if g > 0.001:
            sxy = np.array([sun[0], sun[1]], np.float32)
            sxy = sxy / (np.linalg.norm(sxy) + 1e-6)
            gx, gy = cx + sxy[0] * R * 1.0, cy - sxy[1] * R * 1.0
            self.glare_xy = (gx, gy, g)
        else:
            self.glare_xy = None

    def draw_ring(self, img, P, ring):
        """ring: dict(radius (x R), tilt deg, yaw deg, color, width, progress 0..1, alpha, phase)"""
        prog = ring.get("progress", 1.0)
        if prog <= 0.001 or ring.get("alpha", 1.0) <= 0.001:
            return
        cx, cy, R = P["cx"], P["cy"], P["R"]
        n = 720
        a0 = math.radians(ring.get("phase", 0.0))
        th = a0 + np.linspace(0, 2 * np.pi * prog, max(8, int(n * prog)))
        rr = ring["radius"]
        p = np.stack([np.cos(th) * rr, np.zeros_like(th), np.sin(th) * rr], 1)
        ti, ya = math.radians(ring["tilt"]), math.radians(ring.get("yaw", 0))
        Rx = np.array([[1, 0, 0], [0, math.cos(ti), -math.sin(ti)], [0, math.sin(ti), math.cos(ti)]])
        Rz = np.array([[math.cos(ya), -math.sin(ya), 0], [math.sin(ya), math.cos(ya), 0], [0, 0, 1]])
        p = p @ (Rz @ Rx).T
        sx, sy, sz = cx + p[:, 0] * R, cy - p[:, 1] * R, p[:, 2]
        hidden = (sz < 0) & (p[:, 0] ** 2 + p[:, 1] ** 2 < 1.0)
        layer = np.zeros((H, W), np.float32)
        S = 4                           # sub-pixel shift for AA polylines
        pts = np.stack([sx * S, sy * S], 1).astype(np.int32)
        wpx = ring.get("width", 2.0)
        seg_on = ~hidden
        start = None
        for i in range(len(pts)):
            if seg_on[i] and start is None:
                start = i
            if (not seg_on[i] or i == len(pts) - 1) and start is not None:
                end = i if seg_on[i] else i - 1
                if end > start:
                    cv2.polylines(layer, [pts[start:end + 1]], False, 1.0, max(1, int(round(wpx))), cv2.LINE_AA, shift=2)
                start = None
        # head glint
        if ring.get("head", True) and prog < 0.999 and seg_on[-1]:
            cv2.circle(layer, (int(sx[-1] * S), int(sy[-1] * S)), int(wpx * 2.2 * S), 1.0, -1, cv2.LINE_AA, shift=2)
        glow = cv2.GaussianBlur(layer, (0, 0), 6) * 0.9
        col = np.array(ring["color"], np.float32)
        a = ring.get("alpha", 1.0)
        img += (layer * a)[..., None] * col + (glow * a * ring.get("glow", 0.6))[..., None] * col

    def bloom(self, img, P):
        gl = getattr(self, "glare_xy", None)
        if gl:
            gx, gy, g = gl
            X, Y = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
            d2 = (X - gx) ** 2 + (Y - gy) ** 2
            img += (np.exp(-d2 / (2 * 28.0 ** 2)) * 1.4 * g)[..., None] * np.array([1.0, 0.93, 0.8], np.float32)
            img += (np.exp(-d2 / (2 * 160.0 ** 2)) * 0.35 * g)[..., None] * np.array([1.0, 0.7, 0.45], np.float32)
            # anamorphic streak (horizontal)
            streak = np.exp(-((Y - gy) ** 2) / (2 * 5.0 ** 2)) * np.exp(-np.abs(X - gx) / 240.0)
            img += (streak * 0.55 * g)[..., None] * np.array([0.75, 0.85, 1.0], np.float32)
        small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        bright = np.clip(small - 0.55, 0, None)
        b = cv2.GaussianBlur(bright, (0, 0), 9) * 0.9 + cv2.GaussianBlur(bright, (0, 0), 28) * 0.6
        img = img + cv2.resize(b, (W, H), interpolation=cv2.INTER_LINEAR) * P.get("bloom", 1.0)
        # filmic tone map + gentle vignette
        img = 1 - np.exp(-img * 1.25)
        return img


# ---------------------------------------------------------------- concept A timeline
HOLD = 1.25   # seconds of fully static plate at the end (clean hold frame)
def params_A(t, T, land):
    """Camera/sun/ring keyframes for concept A. land = sting landing time (s). Frozen for the last HOLD s."""
    t = min(t, T - HOLD)
    k = ease(t / max(land, 1e-3))                     # 0 -> 1 by the logo landing
    k2 = ease((t - land) / max(T - HOLD - land, 0.3))  # settle after landing (ends ~0.9 s before T)
    R = lerp(500, 900, k) + 14 * k2
    cy = lerp(1160, 1880, k) + 12 * k2
    lon0 = lerp(82, 30, ease(min(t / (T - HOLD), 1.0)))
    lat0 = lerp(16, 27, k)
    # sun: from behind (night) to front-upper-left (day); grazing the limb ~ 0.62*land
    s = ease(min(t / (land + 0.4), 1.0))
    sun = [lerp(0.05, -0.80, s), lerp(0.35, 0.80, s), lerp(-1.0, 0.16, s)]
    sz = norm(sun)[2]
    glare = float(np.exp(-((sz - 0.02) / 0.16) ** 2)) * 1.0
    beat = 60 / 118
    ring1 = dict(radius=1.16, tilt=72, yaw=-14, color=RED * 1.25, width=2.4, phase=200,
                 progress=eout(t / (3 * beat)), alpha=1.0, glow=0.7)
    ring2 = dict(radius=1.30, tilt=66, yaw=10, color=[0.92, 0.95, 1.0], width=1.5, phase=20,
                 progress=eout((t - 4 * beat) / (3 * beat)), alpha=0.75, glow=0.4)
    ring3 = dict(radius=1.07, tilt=78, yaw=-4, color=[0.55, 0.75, 1.0], width=1.2, phase=120,
                 progress=eout((t - 8 * beat) / (3 * beat)), alpha=0.6, glow=0.4)
    # rings rotate slowly once drawn (phase advance gives the head motion; after drawn, slow drift via yaw)
    # at the sting the orbits tighten onto the limb, so the hold frame keeps the title/date area clean
    lay = ease((t - (land - 1.017)) / 1.6)
    for rg, sp, tight in ((ring1, 2.2, 1.045), (ring2, -1.6, 1.085), (ring3, 1.2, 1.02)):
        rg["yaw"] += sp * t
        rg["radius"] = lerp(rg["radius"], tight, lay)
    exposure = lerp(0.9, 1.0, k)
    return dict(cx=540, cy=cy, R=R, lon0=lon0, lat0=lat0, sun=sun, glare=glare, rings=[ring3, ring2, ring1],
                exposure=exposure, drift=t / T, atmo=1.0, bloom=1.0, stars=1.0 - 0.5 * k)


def render(out, T, fps=30, land=7.129, frames=None, png_dir=None, quality=16):
    G = Globe()
    n = int(round(T * fps))
    idx = range(n) if frames is None else frames
    proc = None
    if out:
        proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                                 "-crf", str(quality), "-pix_fmt", "yuv420p", "-x264-params", "keyint=15",
                                 "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    for i in idx:
        t = i / fps
        img = G.frame(params_A(t, T, land))
        # ordered dither to kill banding in the dark gradient
        fr = min(i, int((T - HOLD) * fps))     # same dither seed in the hold = truly static frames
        rgb = np.clip(img * 255 + np.random.default_rng(fr).uniform(-0.5, 0.5, img.shape), 0, 255).astype(np.uint8)
        if proc:
            proc.stdin.write(rgb.tobytes())
        if png_dir:
            Path(png_dir).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(Path(png_dir) / f"g{i:04d}.png"), rgb[..., ::-1])
        if i % 30 == 0:
            print("frame", i, file=sys.stderr, flush=True)
    if proc:
        proc.stdin.close()
        proc.wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--dur", type=float, required=True)
    ap.add_argument("--land", type=float, default=7.129)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--frames")
    ap.add_argument("--png-dir")
    a = ap.parse_args()
    fr = [int(x) for x in a.frames.split(",")] if a.frames else None
    render(a.out, a.dur, a.fps, a.land, fr, a.png_dir)
