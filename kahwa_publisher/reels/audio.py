"""Procedural, licence-free audio for Kahwa News reels (no vocals, no samples).

music_bed(duration, bpm, mood)  -> stereo float32 @48k
sfx: whoosh, tick, hit, riser, ding, sting
mix(plan) -> wav, then ffmpeg loudnorm to -14 LUFS / -1.5 dBTP.

Everything is synthesised with numpy from sines/saws/filtered noise and a fixed RNG seed,
so the same plan always yields the same file (deterministic, CI-safe, zero licence risk).
"""
import json
import subprocess
import wave
import numpy as np

SR = 48000
rng = np.random.default_rng(7)


def _t(d):
    return np.arange(int(d * SR)) / SR


def _onepole_lp(x, fc):
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    # vectorised enough for our sizes via scipy-free IIR
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def _lp_fft(x, fc, order=4):
    """Zero-phase Butterworth-shaped low-pass in the frequency domain (fast)."""
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X *= 1 / np.sqrt(1 + (f / fc) ** (2 * order))
    return np.fft.irfft(X, n)


def _bp_fft(x, lo, hi, order=3):
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR) + 1e-6
    X *= 1 / np.sqrt(1 + (f / hi) ** (2 * order)) * (1 / np.sqrt(1 + (lo / f) ** (2 * order)))
    return np.fft.irfft(X, n)


def _env_adsr(n, a, d, s, r):
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    e = np.full(n, s, dtype=np.float64)
    a = min(a, n); e[:a] = np.linspace(0, 1, a)
    d2 = min(d, n - a); e[a:a + d2] = np.linspace(1, s, d2)
    r = min(r, n); e[n - r:] *= np.linspace(1, 0, r)
    return e


def _saw(freq, t, detune=0.0):
    ph = (freq * (1 + detune)) * t
    return 2 * (ph - np.floor(ph + 0.5))


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


# ----------------------------------------------------------------------------- music
PROGS = {
    # minor, restrained "news underscore" progressions (root midi, chord intervals)
    "explainer": [(50, (0, 3, 7, 10)), (46, (0, 4, 7, 11)), (53, (0, 4, 7, 9)), (48, (0, 4, 7, 10))],
    "breaking": [(45, (0, 3, 7)), (45, (0, 3, 7)), (41, (0, 4, 7)), (43, (0, 4, 7))],
    "data": [(52, (0, 3, 7, 10)), (48, (0, 4, 7, 11)), (55, (0, 4, 7)), (50, (0, 3, 7, 10))],
    "photo": [(49, (0, 3, 7, 10)), (45, (0, 4, 7, 11)), (52, (0, 4, 7, 9)), (47, (0, 4, 7, 10))],
}


def music_bed(duration, bpm=120, mood="explainer", end_hit_at=None):
    beat = 60 / bpm
    bar = beat * 4
    n = int(duration * SR) + SR
    L = np.zeros(n); R = np.zeros(n)
    prog = PROGS[mood]
    bars_per_chord = 2 if mood != "breaking" else 1
    # --- pad
    nchords = int(np.ceil(duration / (bar * bars_per_chord))) + 1
    for i in range(nchords):
        root, ivs = prog[i % len(prog)]
        st = i * bar * bars_per_chord
        d = bar * bars_per_chord + 0.6
        s0 = int(st * SR)
        if s0 >= n:
            break
        t = _t(d)
        sig = np.zeros_like(t)
        for k, iv in enumerate(ivs):
            f = midi(root + 12 + iv)
            sig += 0.5 * _saw(f, t, 0.0035) + 0.5 * _saw(f, t, -0.004)
        sig = _lp_fft(sig, 900 if mood != "photo" else 700, 2)
        sig *= _env_adsr(len(t), 0.5, 0.4, 0.8, 0.8) * 0.09
        e = min(n, s0 + len(sig))
        L[s0:e] += sig[: e - s0] * 1.0
        R[s0:e] += np.roll(sig, 240)[: e - s0] * 0.95
        # sub bass, root, pulsing in 8ths (breaking) or on beats
        step = beat / 2 if mood == "breaking" else beat
        nb = int(d / step)
        for b in range(nb):
            bs = s0 + int(b * step * SR)
            if bs >= n:
                break
            tb = _t(step * 0.9)
            fb = midi(root - 12)
            sb = np.sin(2 * np.pi * fb * tb) * np.exp(-tb * (9 if mood == "breaking" else 5)) * (0.22 if mood != "photo" else 0.14)
            eb = min(n, bs + len(sb))
            L[bs:eb] += sb[: eb - bs]; R[bs:eb] += sb[: eb - bs]
    # --- soft kick + ticks (hats)
    nbeats = int(duration / beat) + 1
    for b in range(nbeats):
        st = int(b * beat * SR)
        if st >= n:
            break
        kick_on = (mood == "breaking") or (b % 2 == 0)
        if mood == "photo":
            kick_on = b % 4 == 0
        if kick_on:
            tk = _t(0.35)
            f = 48 + 90 * np.exp(-tk * 30)
            k = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tk * 9) * (0.42 if mood != "photo" else 0.25)
            e = min(n, st + len(k)); L[st:e] += k[: e - st]; R[st:e] += k[: e - st]
        # off-beat hat
        for sub in ((0.5,) if mood != "breaking" else (0.25, 0.5, 0.75)):
            hs = st + int(sub * beat * SR)
            if hs >= n:
                continue
            th = _t(0.06)
            h = _bp_fft(rng.standard_normal(len(th)), 6000, 12000) * np.exp(-th * 70) * np.minimum(1, th / 0.003) * (0.04 if mood != "photo" else 0.025)
            e = min(n, hs + len(h)); L[hs:e] += h[: e - hs] * 0.8; R[hs:e] += h[: e - hs]
        # breaking: clock tick motif on each beat (the classic news-desk tick)
        if mood == "breaking":
            tt = _t(0.03)
            tk = np.sin(2 * np.pi * 2100 * tt) * np.exp(-tt * 260) * 0.12
            e = min(n, st + len(tk)); L[st:e] += tk[: e - st]; R[st:e] += tk[: e - st]
    out = np.stack([L, R], 1)[: int(duration * SR)]
    # fades: start already at level (frame 0 = poster), short fade-out tail
    fo = int(min(1.4, duration * 0.08) * SR)
    out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
    return out


# ----------------------------------------------------------------------------- sfx
def sfx(name):
    if name == "whoosh":
        d = 0.45; t = _t(d)
        nz = rng.standard_normal(len(t))
        # sweep via chunked band-pass
        out = np.zeros_like(t); ch = 1024
        for i in range(0, len(t), ch):
            k = i / len(t)
            c = 500 + 3500 * np.sin(np.pi * k)
            seg = nz[max(0, i - ch):i + ch]
            seg = _bp_fft(seg, c * 0.6, c * 1.5, 2)
            out[i:i + ch] = seg[ch if i >= ch else 0:][: len(out[i:i + ch])]
        env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2
        return out * env * 0.35
    if name == "tick":
        t = _t(0.035)
        return np.sin(2 * np.pi * 3200 * t) * np.exp(-t * 220) * 0.25
    if name == "hit":
        t = _t(0.9)
        f = 38 + 70 * np.exp(-t * 14)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
        nz = _lp_fft(rng.standard_normal(len(t)), 1800) * np.exp(-t * 18) * 0.5
        return (body + nz) * 0.55
    if name == "riser":
        d = 1.0; t = _t(d)
        f = 200 + 900 * (t / d) ** 2
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.15 + _bp_fft(rng.standard_normal(len(t)), 1500, 7000) * 0.2
        return s * (t / d) ** 2 * 0.6
    if name == "ding":
        t = _t(1.2)
        return (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t) + 0.25 * np.sin(2 * np.pi * 1760 * t)) * np.exp(-t * 4) * 0.16
    if name == "stamp":
        t = _t(0.25)
        f = 60 + 120 * np.exp(-t * 40)
        return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 16) + _lp_fft(rng.standard_normal(len(t)), 3000) * np.exp(-t * 40) * 0.3) * 0.5
    if name == "sting":  # breaking bumper: low hit + bright stab chord
        t = _t(1.4)
        stab = sum(np.sin(2 * np.pi * midi(m) * t) for m in (69, 72, 76, 81)) * np.exp(-t * 3.2) * 0.07
        h = sfx("hit"); h = np.pad(h, (0, max(0, len(t) - len(h))))[: len(t)]
        return h * 0.9 + stab
    raise ValueError(name)


SFX_GAIN = {"whoosh": 0.55, "tick": 0.45, "hit": 0.85, "riser": 0.6, "ding": 0.6, "stamp": 0.7, "sting": 0.9}


def render(plan, out_wav):
    """plan = {duration, bpm, mood, cues:[{t, sfx}]}."""
    dur = plan["duration"]
    mix = music_bed(dur, plan.get("bpm", 120), plan.get("mood", "explainer"))
    cache = {}
    for c in plan.get("cues", []):
        name = c["sfx"]
        if name not in cache:
            cache[name] = sfx(name)
        s = cache[name] * SFX_GAIN.get(name, 0.6) * c.get("gain", 1.0)
        st = int(max(0, c["t"]) * SR)
        if st >= len(mix):
            continue
        e = min(len(mix), st + len(s))
        mix[st:e, 0] += s[: e - st]
        mix[st:e, 1] += s[: e - st]
    peak = np.abs(mix).max() or 1
    mix = mix / peak * 0.7
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out_wav


def loudnorm(in_wav, out_wav, I=-14, TP=-1.5, LRA=11):
    """Two-pass EBU R128 normalisation."""
    p1 = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", in_wav, "-af",
                         f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True)
    j = p1.stderr[p1.stderr.rfind("{"):p1.stderr.rfind("}") + 1]
    m = json.loads(j)
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    af += "," + TP_LIMITER       # linear loudnorm cannot hold TP when the gain pushes peaks up (runs 41053869183, 41105257205)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", in_wav, "-af", af, "-ar", "48000", out_wav], check=True)
    return out_wav


# 4x-oversampled brick-wall limiter at -2 dBFS: catches inter-sample peaks before the AAC encode adds its own overshoot.
TP_LIMITER = "aresample=192000,alimiter=limit=0.794:attack=1:release=60:level=0,aresample=48000"


def fix_true_peak(mp4, limit=-1.0, target=-1.5, tries=2):
    """Final-file guard (the encoder adds overshoot after any limiter): if the finished MP4's true peak is above `limit`,
    re-encode only its audio with the excess removed (video copied) and measure again. Returns (lufs, tp, steps)."""
    import os, shutil
    steps = []
    I, TP = measure(str(mp4))
    for _ in range(tries):
        if TP <= limit:
            break
        g = round(target - TP, 2)
        tmp = str(mp4) + ".tp.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-map", "0:v:0?", "-map", "0:a:0", "-c:v", "copy",
                        "-af", f"volume={g}dB," + TP_LIMITER, "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                        "-movflags", "+faststart", tmp], check=True)
        shutil.move(tmp, str(mp4))
        steps.append({"tp_before": TP, "gain_db": g})
        I, TP = measure(str(mp4))
    return I, TP, steps


def measure(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                        "loudnorm=I=-14:TP=-1.5:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    j = p.stderr[p.stderr.rfind("{"):p.stderr.rfind("}") + 1]
    m = json.loads(j)
    return float(m["input_i"]), float(m["input_tp"])
