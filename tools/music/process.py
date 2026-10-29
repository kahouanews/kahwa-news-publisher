#!/usr/bin/env python3
"""QA + mastering for the ACE-Step generated library (Kahwa News).

in : <raw>/<shard>/<id>/req00.wav + report-*.json (+ understand.json)   (artifacts of .github/workflows/music-library.yml)
out: assets/music/kahwa-<id>.mp3 + assets/music/music.json, and <qa>/qa.json (all candidates, metrics, verdicts)

python tools/music/process.py <raw_dir> [--qa <dir>]      (MUSIC_RUN_ID=<actions run id> for the provenance line)

Mastering: start trim to the first non-silent 50 ms window, end trim after the last one, 40 ms fade-in, 3 s fade-out,
two-pass loudnorm to -16 LUFS integrated, TRUE peak <= -2.0 dBTP measured on the final MP3 (lame adds overshoot, so the
encode is repeated with the measured excess taken off until the decoded MP3 is at or below -2.0 dBTP).

Listen-proxy checks (no ears available, so conservative):
- no clipping (|x| >= 0.999 samples), true peak after mastering <= -1.5 dBTP
- no silence gaps (> 0.6 s below -45 dBFS RMS) after the start trim
- loudness range / crest sane, spectral centroid in a range (not pure noise, not muffled)
- vocals: ACE-Step understand pass must not return sung lyrics; plus a vocal-band modulation heuristic
"""
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyloudnorm as pyln

SR = 48000
HERE = Path(__file__).resolve().parent
PUB = HERE.parent.parent            # repo root (tools/music/process.py)
OUT = PUB / "assets" / "music"
TP_MAX = -2.0


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def rms_db(x, win):
    m = x.mean(1)
    n = len(m) // win
    r = np.sqrt((m[: n * win].reshape(n, win) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(r)


def spectral(x):
    m = x.mean(1)
    seg = m[SR * 5: SR * 35] if len(m) > SR * 40 else m
    n = 8192
    frames = [seg[i:i + n] * np.hanning(n) for i in range(0, len(seg) - n, n)]
    if not frames:
        return 0, 0, 0
    S = np.mean([np.abs(np.fft.rfft(f)) for f in frames], 0)
    f = np.fft.rfftfreq(n, 1 / SR)
    cen = float((S * f).sum() / (S.sum() + 1e-9))
    low = float(S[f < 250].sum() / S.sum())
    high = float(S[f > 6000].sum() / S.sum())
    return cen, low, high


def vocal_heuristic(x):
    """Energy modulation at syllable rate (3-8 Hz) inside 300-3400 Hz relative to total: high values suggest singing."""
    m = x.mean(1)[: SR * 60]
    F = np.fft.rfft(m)
    f = np.fft.rfftfreq(len(m), 1 / SR)
    F[(f < 300) | (f > 3400)] = 0
    band = np.fft.irfft(F, len(m))
    env = np.sqrt(np.convolve(band ** 2, np.ones(480) / 480, "same"))[::480]   # 100 Hz envelope
    env = env - env.mean()
    E = np.abs(np.fft.rfft(env))
    fe = np.fft.rfftfreq(len(env), 1 / 100)
    return float(E[(fe > 3) & (fe < 8)].sum() / (E[(fe > 0.3) & (fe < 20)].sum() + 1e-9))


def bpm_est(x):
    m = np.abs(x.mean(1))
    env = np.convolve(m, np.ones(480) / 480, "same")[::480]
    on = np.maximum(np.diff(env), 0)
    on = on - on.mean()
    ac = np.correlate(on, on, "full")[len(on) - 1:]
    lags = np.arange(len(ac)) / 100
    ok = (lags > 60 / 180) & (lags < 60 / 50)
    if not ok.any():
        return None
    lag = lags[ok][np.argmax(ac[ok])]
    return int(round(60 / lag))


def master(src, dst, fade_out=3.0, start=0.0, I=-16.0, TP=-2.0, length=None):
    x = decode(src)
    dur = min(len(x) / SR - start, length or 1e9)
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", f"{dur:.2f}", "-i", str(src), "-af",
                           f"loudnorm=I={I}:TP={TP}:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    j = json.loads(meas[meas.rfind("{"): meas.rfind("}") + 1])
    af = (f"afade=t=in:st=0:d=0.04,afade=t=out:st={dur - fade_out:.2f}:d={fade_out},"
          f"loudnorm=I={I}:TP={TP}:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true,aresample=48000")
    extra = 0.0
    for _ in range(4):
        vol = f",volume={extra:.2f}dB" if extra else ""
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", f"{dur:.2f}", "-i", str(src), "-af", af + vol,
                        "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "256k", "-ar", "48000", str(dst)], check=True)
        tp = true_peak(dst)
        if tp <= TP_MAX:
            return tp
        extra -= (tp - TP_MAX) + 0.1
    return tp


def true_peak(path):
    """EBU R128 true peak (dBTP, 4x oversampled) of the decoded file."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", err.split("Summary:")[-1])
    return float(m[-1]) if m and m[-1] != "-inf" else -99.0


def main(raw_dir):
    reports = []
    for p in glob.glob(f"{raw_dir}/**/report-*.json", recursive=True):
        base = Path(p).parent
        for r in json.load(open(p)):
            r["_dir"] = str(base / r["id"])
            reports.append(r)
    qa = []
    for r in sorted(reports, key=lambda r: r["id"]):
        rec = {k: r.get(k) for k in ("id", "mood", "bpm", "key", "duration", "seed", "caption", "ok", "lm_s", "synth_s", "understand")}
        wavs = glob.glob(os.path.join(r["_dir"], "req0*.wav"))
        if not r.get("ok") or not wavs:
            rec["verdict"] = "reject: generation failed " + str(r.get("err", ""))[:120]
            qa.append(rec); continue
        x = decode(wavs[0])
        d = rms_db(x, int(0.05 * SR))
        on = int(np.argmax(d > -40)) * 0.05        # first non-silent 50 ms window
        y = x[int(on * SR):]
        tail = rms_db(y, int(0.05 * SR))
        last = len(tail) - int(np.argmax(tail[::-1] > -40))
        y = y[: last * int(0.05 * SR)]
        g = rms_db(y, int(0.1 * SR))
        gaps, run = 0, 0
        for v in g[:-10]:
            run = run + 1 if v < -45 else 0
            gaps = max(gaps, run)
        clip = int((np.abs(x) >= 0.999).sum())
        L = pyln.Meter(SR).integrated_loudness(y.astype(np.float64)) if len(y) > SR else -99
        cen, low, high = spectral(y)
        voc = vocal_heuristic(y)
        lyr = ((r.get("understand") or {}).get("lyrics") or "").strip()
        sung = bool(lyr) and not re.fullmatch(r"\[?\s*instrumental\s*\]?", lyr, re.I) and len(re.sub(r"\[[^\]]*\]", "", lyr).strip()) > 12
        rec.update(file_raw=wavs[0], start_trim=round(float(on), 2), usable_s=round(len(y) / SR, 1), clip_samples=clip,
                   max_gap_s=round(gaps * 0.1, 1), lufs_raw=round(float(L), 1), centroid_hz=round(cen), low_share=round(low, 3),
                   high_share=round(high, 3), vocal_mod=round(voc, 3), understand_lyrics=lyr[:160], bpm_est=bpm_est(y))
        reasons = []
        if rec["usable_s"] < 45: reasons.append("too short")
        if gaps * 0.1 > 0.6: reasons.append("silence gap")
        if clip > 200: reasons.append("clipping")
        if not (250 < cen < 6000): reasons.append("spectrum odd")
        if sung: reasons.append("vocals detected by understand pass")
        rec["verdict"] = "ok" if not reasons else "reject: " + ", ".join(reasons)
        qa.append(rec)
    # pick up to 4 per mood (prefer lowest vocal_mod), master them
    OUT.mkdir(parents=True, exist_ok=True)
    lib = []
    for mood in ("serious", "calm", "tension", "light"):
        ok = sorted([q for q in qa if q["mood"] == mood and q["verdict"] == "ok"], key=lambda q: q["vocal_mod"])
        for q in ok[:4]:
            dst = OUT / f"kahwa-{q['id']}.mp3"
            ttp = master(q["file_raw"], dst, start=q["start_trim"], length=q["usable_s"])
            y = decode(dst)
            Lm = pyln.Meter(SR).integrated_loudness(y.astype(np.float64))
            tp = 20 * np.log10(np.abs(y).max() + 1e-12)
            q["picked"] = True
            lib.append(dict(file=dst.name, id=q["id"], mood=mood, bpm=q["bpm"], bpm_est=q["bpm_est"], key=q["key"],
                            duration=round(len(y) / SR, 1), start=0.0, lufs=round(float(Lm), 1), sample_peak_db=round(float(tp), 2), true_peak_dbtp=round(float(ttp), 2),
                            loop="no (one-shot, 3 s fade-out at end; renderer starts at 0 and fades under the end card)",
                            source="original composition generated for Kahwa News with ACE-Step 1.5 (XL turbo DiT + 1.7B LM), "
                                   "acestep.cpp CPU, GitHub Actions run " + os.getenv("MUSIC_RUN_ID", "?"),
                            license="Own original audio. Model + weights MIT; model card: 'You can strictly use the generated "
                                    "music for commercial purposes.' Not registered with any Content ID / Rights Manager system.",
                            license_url="https://huggingface.co/ACE-Step/Ace-Step1.5",
                            generated_by=dict(model="ACE-Step 1.5 (acestep-v15-xl-turbo Q8_0 GGUF, acestep-5Hz-lm-1.7B Q8_0)",
                                              runtime="ServeurpersoCom/acestep.cpp@de858d8e (MIT)",
                                              weights="https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF (MIT)",
                                              seed=q["seed"], caption=q["caption"], steps=8, shift=3.0, lyrics="[Instrumental]"),
                            qa=dict(clip_samples=q["clip_samples"], max_gap_s=q["max_gap_s"], centroid_hz=q["centroid_hz"],
                                    vocal_mod=q["vocal_mod"], understand_lyrics=q["understand_lyrics"])))
    (OUT / "music.json").write_text(json.dumps({"version": 1, "generated": "2026-10-29", "tracks": lib}, ensure_ascii=False, indent=1))
    (QA_DIR / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=1))
    print(json.dumps([{k: q.get(k) for k in ("id", "verdict", "usable_s", "max_gap_s", "clip_samples", "centroid_hz", "vocal_mod", "bpm", "bpm_est", "picked")} for q in qa], indent=0))


QA_DIR = HERE

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--qa" in args:
        i = args.index("--qa"); QA_DIR = Path(args[i + 1]); del args[i:i + 2]
    main(args[0] if args else str(HERE / "raw"))
