"""Audio for Kahwa reels v2: real CC0 music bed + CC0 whooshes + numpy-synthesised soft ticks/hits.

music : one CC0 track per style (Freesound, licence recorded in NOTES.md), trimmed from a chosen
        offset, already at full level on frame 0 (no fade-in), 1.6 s fade-out under the end card.
sfx   : CC0 whooshes (Freesound) rotated so no two neighbouring cues use the same take;
        ticks / low hits are synthesised (filtered noise + sine sub, no tonal chimes).
master: two-pass loudnorm to -14 LUFS / -1.5 dBTP (audio.loudnorm).
"""
import subprocess
import wave
from pathlib import Path

import numpy as np

from . import audio as synth   # synthesised ticks / hits

SR = 48000


def decode(path, start=0.0, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path)]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def lufs(x):
    import pyloudnorm as pyln
    return pyln.Meter(SR).integrated_loudness(x.astype(np.float64))


def gain_to(x, target):
    L = lufs(x)
    if not np.isfinite(L):
        return x
    return x * (10 ** ((target - L) / 20))


def render(plan, assets, out_wav):
    dur = plan["duration"]
    n = int(dur * SR)
    m = plan["music"]
    if m.get("file"):
        bed = decode(Path(assets) / "music" / m["file"], m.get("offset", 0.0), dur + 0.5)[:n]
    else:
        bed = synth.music_bed(dur, bpm=m.get("bpm", 96), mood=m.get("procedural", "explainer")).astype(np.float32)[:n]
    if len(bed) < n:
        bed = np.pad(bed, ((0, n - len(bed)), (0, 0)))
    bed = gain_to(bed, m.get("lufs", -17.0))
    # fade-out under end card
    fo = int(1.6 * SR)
    end_t = plan.get("end_t", dur - 3)
    fstart = int(min(dur - 1.6, end_t + 1.6) * SR)
    env = np.ones(n, np.float32)
    env[fstart:fstart + fo] = np.linspace(1, 0, min(fo, n - fstart))
    env[fstart + fo:] = 0
    bed *= env[:, None]
    mix = bed.copy()
    whooshes = [decode(Path(assets) / "sfx" / f) for f in plan.get("whoosh_files", [])]
    deep = [decode(Path(assets) / "sfx" / f) for f in plan.get("deep_files", [])]
    wi = 0
    rng = np.random.default_rng(11)

    def peak(x, p):
        m = np.abs(x).max() or 1.0
        return x * (p / m)

    def tick():
        t = np.arange(int(0.012 * SR)) / SR
        nz = rng.standard_normal(len(t))
        nz = np.diff(np.concatenate([[0], nz]))          # crude high-pass: clicky, not tonal
        s = nz * np.exp(-t * 400)
        return np.stack([s, s], 1).astype(np.float32)

    for c in plan["cues"]:
        kind = c["sfx"]
        g = c.get("gain", 1.0)
        lead = 0.0
        if kind == "whoosh" and whooshes:
            s = peak(whooshes[wi % len(whooshes)], 0.26) * g; wi += 1
            lead = c.get("lead", float(np.argmax(np.abs(whooshes[(wi - 1) % len(whooshes)]).sum(1))) / SR)
        elif kind == "deep" and deep:
            d0 = deep[0][: int(2.0 * SR)]
            s = peak(d0, 0.30) * g
            lead = float(np.argmax(np.abs(d0).sum(1))) / SR
        elif kind == "tick":
            s = peak(tick(), 0.07) * g
        else:   # hit / stamp: synthesised sub thump (no tonal chime)
            mono = synth.sfx(kind)
            s = peak(np.stack([mono, mono], 1).astype(np.float32), {"hit": 0.34, "stamp": 0.22}.get(kind, 0.2)) * g
        i0 = int(max(0.0, c["t"] - lead + 1 / 30) * SR)     # sound peak one frame after the picture
        i1 = min(n, i0 + len(s))
        if i1 > i0:
            mix[i0:i1] += s[: i1 - i0]
    mix = np.clip(mix, -1.0, 1.0)
    pcm = (mix * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out_wav
