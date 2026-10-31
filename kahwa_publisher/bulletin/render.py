"""Render «قهوة اليوم» (and the «العالم في 30 ثانية» edition reel) on CPU: HyperFrames (pinned 0.8.214, headless Chrome) + ffmpeg.

Ported from the approved pilot v4 scripts (work/bulletin-pilot-v4/src: build.py, mix.py, bridge.py, assemble.py):
  opener  : Concept A «المدار» (bulletin/opener/opener.py; globe plate cached)  |  edition: Concept B edition sting (stings/sting_b.py, E)
  body    : templates/body.tpl.js driven by rundown.build() + makeMap() copied at build time from reels/templates/kahwa.js
  bridge  : 0.40 s red-line wipe right->left from the opener hold frame into the body (never two logos, no black frame)
  audio   : bulletin = headlines on bars 1-4 of bed-breaking (logo-free loop) then segment beds, CC0 whooshes on bumpers,
            sting-outro on the sign-off; edition reel = bars 1-4 of bed-breaking only, ends on a fade. One -14 LUFS master.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..reels import render as RR

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
REPO = PKG.parent
REELS = PKG / "reels"
MUSIC = REPO / "assets" / "music" / "identity"
SFX = REPO / "assets" / "sfx"
SHARED = ["Almarai-Bold.ttf", "Almarai-ExtraBold.ttf", "Almarai-Regular.ttf", "Barlow-BlackItalic.ttf", "CustomEase.min.js",
          "d3-array.min.js", "d3-geo.min.js", "grain.png", "gsap.min.js", "logo-band.svg", "logo-kahwa.svg", "world.js"]
BPM = 118.0
BEAT = 60.0 / BPM
LOOP4 = 16 * BEAT          # 4 bars = 8.1356 s, a manifest edit point of bed-breaking (bars 1-4 carry no logo statement)
PICK = 1.5 * BEAT
BRIDGE = 12                # frames (0.40 s)
FPS = 30
FMT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def sh(cmd, **k):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True, **k)


def mus(name):
    return MUSIC / f"{name}.flac"


def nframes(p):
    return int(sh(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                   "-of", "csv=p=0", p]).stdout.strip())


def lufs(p):
    e = subprocess.run(["ffmpeg", "-nostats", "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    s = e[e.rindex("Summary:"):]
    I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", s).group(1))
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", s[s.find("True peak"):])
    return I, float(tp.group(1)) if tp else None


# ------------------------------------------------------------------ composition
def makemap_js():
    K = (REELS / "templates" / "kahwa.js").read_text()
    a = K.index("  // ---------------------------------------------------------------- map (D3 orthographic")
    b = K.index("  // ---------------------------------------------------------------- shared scenes")
    return K[a:b]


def page(title, dur, data_js):
    return f'''<!doctype html>
<html lang="ar" dir="rtl" data-resolution="portrait"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" /><title>{title}</title>
<script src="gsap.min.js"></script><script src="CustomEase.min.js"></script><script src="d3-array.min.js"></script>
<script src="d3-geo.min.js"></script><script src="world.js"></script>
<link rel="stylesheet" href="kahwa.css" /><link rel="stylesheet" href="body.css" /></head>
<body><div id="root" data-composition-id="main" data-start="0" data-duration="{dur}" data-width="1080" data-height="1920" data-fps="30"></div>
<script>{data_js}</script>
<script src="body.js"></script>
<script>window.__timelines = window.__timelines || {{}}; window.__timelines["main"] = window.KAHWA_TL;</script>
</body></html>'''


def build_body(B, comp, media_dir=None):
    """Write the body composition for bulletin B into comp/ (wiped first)."""
    if comp.exists():
        shutil.rmtree(comp)
    comp.mkdir(parents=True)
    for f in SHARED:
        shutil.copy(REELS / "static" / f, comp / f)
    shutil.copy(REELS / "templates" / "kahwa.css", comp / "kahwa.css")
    tpl = (HERE / "templates" / "body.tpl.js").read_text()
    if "HL_SEG" in tpl:
        raise RuntimeError("body.tpl.js has a hard-coded headline->segment map (HL_SEG); headlines must use scene.hlseg")
    (comp / "body.js").write_text(tpl.replace("/*__MAKEMAP__*/", makemap_js()))
    shutil.copy(HERE / "templates" / "body.css", comp / "body.css")
    imgs = set(re.findall(r'"img": "([^"]+)"', json.dumps(B, ensure_ascii=False)))
    for im in imgs:
        src = Path(media_dir or ".") / im
        if not src.exists():
            raise RuntimeError(f"missing media {src}")
        shutil.copy(src, comp / im)
    (comp / "bulletin.json").write_text(json.dumps(B, ensure_ascii=False, indent=1))
    data = "window.BUL = " + json.dumps(B, ensure_ascii=False).replace("</", "<\\/") + ";"
    (comp / "index.html").write_text(page("Kahwa newscast body", B["duration"], data))
    (comp / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "."}, "media": {"autoProxy": False}}))
    (comp / "meta.json").write_text(json.dumps({"id": "kahwa-newscast-body", "name": "qahwat alyawm body"}))
    return comp


def hf_render(comp, out_mp4, workers="auto", timeout=2400, crf=19):
    cmd = [RR.hf_bin(), "render", str(comp), "-o", str(out_mp4), "--quality", "high", "--workers", str(workers), "--fps", "30",
           "--no-browser-gpu", "--quiet", "--crf", str(crf), "--max-bitrate", "12M"]
    t0 = time.time()
    with open(comp / "render.log", "w") as lf:
        rc = subprocess.run(cmd, env=RR.hf_env(), stdout=lf, stderr=subprocess.STDOUT, timeout=timeout).returncode
    if rc or not Path(out_mp4).exists():
        raise RuntimeError(f"hyperframes render failed rc={rc}: {(comp / 'render.log').read_text()[-1500:]}")
    return round(time.time() - t0, 1)


# ------------------------------------------------------------------ opener / stings (module CLIs, own build dirs)
def render_module(script, concept, out_mp4, build_dir, workers, date=None, hhmm=None, pings_ll=None, extra=None):
    env = dict(os.environ, KAHWA_OPENER_BUILD=str(build_dir))
    cmd = [sys.executable, "-B", str(script), "all", "--concept", concept, "--out", str(out_mp4), "--workers", str(workers)]
    if date:
        cmd += ["--date", date]
    if hhmm:
        cmd += ["--time", hhmm]
    if pings_ll:
        cmd += ["--pings-ll=" + ";".join(f"{lo:.2f},{la:.2f}" for lo, la in pings_ll)]   # "=" form: values may start with "-"
    cmd += list(extra or [])
    t0 = time.time()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500)
    if r.returncode:
        log = ""
        rl = Path(build_dir) / f"comp-{concept}" / "render.log"
        if rl.exists():
            log = rl.read_text()[-1200:]
        raise RuntimeError(f"{script.name} {concept} failed: {r.stderr[-1500:]} {log}")
    return round(time.time() - t0, 1), Path(build_dir) / f"comp-{concept}" / "mix-norm.wav"


def opener_A(out_mp4, build_dir, date, hhmm, workers):
    return render_module(HERE / "opener" / "opener.py", "A", out_mp4, build_dir, workers, date, hhmm)


def sting(concept, out_mp4, build_dir, hhmm, workers, date=None, pings_ll=None, n_stories=None):
    """concept "E" = edition sting «العالم في 30 ثانية» (3.56 s), "X" = alert sting «عاجل» (3.05 s). Pings = real story lon/lat,
    drawn as dots only (no labels, no borders)."""
    extra = []
    if concept == "E" and n_stories:
        extra = ["--handoff", "خبران الآن" if n_stories == 2 else f"{n_stories} أخبار الآن"]
    return render_module(PKG / "stings" / "sting_b.py", concept, out_mp4, build_dir, workers, date, hhmm, pings_ll, extra)


def prep_head(head_mp4, mix_wav, work):
    """Head (opener/sting) render -> silent video + its lossless mix trimmed to the video length."""
    silent, wav = work / "head_silent.mp4", work / "head.wav"
    sh(["ffmpeg", "-y", "-v", "error", "-i", head_mp4, "-map", "0:v:0", "-c", "copy", "-an", silent])
    n = nframes(silent)
    src = mix_wav if mix_wav and Path(mix_wav).exists() else head_mp4
    sh(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-af", f"atrim=0:{n / FPS:.6f}", "-ar", "48000", "-c:a", "pcm_s24le", wav])
    return silent, wav, n


# ------------------------------------------------------------------ audio
def _run_mix(inputs, chains, out, dur):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for i in inputs:
        cmd += ["-i", str(i)]
    labels = "".join(f"[a{k}]" for k in range(len(chains)))
    fc = ";".join(f"[{k}:a]{c},{FMT}[a{k}]" for k, c in enumerate(chains))
    fc += f";{labels}amix=inputs={len(chains)}:normalize=0:duration=longest,apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[m]"
    cmd += ["-filter_complex", fc, "-map", "[m]", "-ar", "48000", "-c:a", "pcm_s24le", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


def loop4(dur, a0=0.0, fade_out=0.7):
    return (f"atrim=0:{LOOP4:.5f},aloop=loop=-1:size={round(LOOP4 * 48000)},atrim=0:{dur:.3f},volume=-9dB,afade=t=in:st=0:d=0.005,"
            f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out},adelay={int(a0 * 1000)}|{int(a0 * 1000)}")


def mix_body(B, out_wav):
    T = {s["id"]: s["t"] for s in B["scenes"]}
    D = B["duration"]
    raw = Path(out_wav).with_name("body_raw.wav")
    inputs, chains = [], []
    if B["mode"] == "edition":
        # reel body: bars 1-4 of bed-breaking only (logo-free, so the sting's motif is never repeated), fade at the end
        inputs.append(mus("bed-breaking")); chains.append(loop4(D, 0.0, fade_out=1.2))
        inputs.append(SFX / "lloydevans09-whoosh-332003.mp3"); chains.append(f"volume=-12dB,adelay={int(max(0, T['end'] - 0.25) * 1000)}|{int(max(0, T['end'] - 0.25) * 1000)}")
    else:
        nseg = len([g for g in B["segs"] if g["k"] != "brief"])
        bumpers = [f"b{i}" for i in range(1, nseg + 2) if f"b{i}" in T]   # b{nseg+1} = «باختصار», absent when dropped
        beds = ["bed-calm", "bed-explainer"]
        cues = [("bed-breaking", 0.0, T["b1"])]
        for i in range(nseg):
            cues.append((beds[i % 2], T[bumpers[i]], T[bumpers[i + 1]] if i + 1 < len(bumpers) else T["end"] + 1.0))
        if len(bumpers) > nseg:
            cues.append(("bed-light", T[bumpers[-1]], T["end"] + 1.0))
        for name, a, b in cues:
            a0 = max(0.0, a - 0.3)
            dur = b - a0 + 0.4
            inputs.append(mus(name))
            if a == 0:
                chains.append(loop4(dur, a0))
            else:
                chains.append(f"aloop=loop=-1:size=2147483647,atrim=0:{dur:.3f},volume=-9dB,afade=t=in:st=0:d=0.6,"
                              f"afade=t=out:st={dur - 0.7:.3f}:d=0.7,adelay={int(a0 * 1000)}|{int(a0 * 1000)}")
        wh = [SFX / "lloydevans09-whoosh-332003.mp3", SFX / "adhdreaming-swoosh-632763.mp3"]
        for i, k in enumerate(bumpers + ["end"]):
            at = max(0, T[k] - 0.25)
            inputs.append(wh[i % 2]); chains.append(f"volume=-12dB,adelay={int(at * 1000)}|{int(at * 1000)}")
        inputs.append(mus("sting-outro")); chains.append(f"volume=-2dB,adelay={int((T['end'] + 0.1) * 1000)}|{int((T['end'] + 0.1) * 1000)}")
    _run_mix(inputs, chains, raw, D)
    sh(["ffmpeg", "-y", "-v", "error", "-i", raw, "-af", f"afade=t=out:st={D - 0.6:.3f}:d=0.6", "-c:a", "pcm_s24le", out_wav])
    return out_wav


# ------------------------------------------------------------------ bridge + assemble
def bridge(head_silent, body_silent, out_mp4, work):
    """0.40 s red-line wipe right->left: left of the line the frozen head hold frame, right of it the body playing."""
    import numpy as np
    from PIL import Image
    tmp = work / "bridge"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "out").mkdir(parents=True)
    sh(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i", head_silent, "-update", "1", "-frames:v", "99", tmp / "hold.png"])
    sh(["ffmpeg", "-v", "error", "-y", "-i", body_silent, "-frames:v", str(BRIDGE), tmp / "b%03d.png"])
    W, BAR, GLOW = 1080, 10, 70
    RED = np.array([0xDC, 0x18, 0x0C], np.float32)
    hold = np.asarray(Image.open(tmp / "hold.png").convert("RGB"), np.float32)
    xs = np.arange(W, dtype=np.float32)[None, :, None]
    ease = lambda t: 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
    for k in range(BRIDGE):
        body = np.asarray(Image.open(tmp / f"b{k + 1:03d}.png").convert("RGB"), np.float32)
        e = (W + BAR) - ease((k + 1) / BRIDGE) * (W + 2 * BAR + GLOW)
        right = (xs >= e).astype(np.float32)
        img = hold * (1 - right) + body * right
        g = np.clip(1 - (e - xs) / GLOW, 0, 1) * (xs < e)
        img = img * (1 - 0.55 * g ** 2) + RED * 0.55 * g ** 2
        bar = ((xs >= e - BAR) & (xs < e)).astype(np.float32)
        img = img * (1 - bar) + RED * bar
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(tmp / "out" / f"f{k:03d}.png")
    sh(["ffmpeg", "-v", "error", "-y", "-framerate", "30", "-i", tmp / "out" / "f%03d.png", "-c:v", "libx264", "-preset", "medium",
        "-crf", "12", "-pix_fmt", "yuv420p", out_mp4])
    return out_mp4


def assemble(head_silent, head_wav, n_head, body_silent, body_wav, bridge_mp4, out_mp4, work, head_over_body_lu=3.2,
             pickup=True, fade_end=0.0, TP=-2.5, crf=20):
    """head ++ bridge (replaces body frames 0..BRIDGE-1) ++ body; one two-pass linear loudnorm to -14 LUFS."""
    T_H = n_head / FPS
    g = (lufs(body_wav)[0] + head_over_body_lu) - lufs(head_wav)[0]
    full_raw, full = work / "full_raw.wav", work / "full.wav"
    fc = (f"[0:a]volume={g:.2f}dB,atrim=0:{T_H:.6f},aformat=sample_fmts=fltp:channel_layouts=stereo[o];"
          f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo[b];[o][b]concat=n=2:v=0:a=1[ob];")
    if pickup:   # last 1.5 beats of bars 1-4 under the head tail: runs sample-exactly into the body's bar-1 downbeat
        pk = T_H - PICK
        fc += (f"[2:a]atrim={LOOP4 - PICK:.6f}:{LOOP4:.6f},asetpts=PTS-STARTPTS,volume=-9dB,afade=t=in:st=0:d={PICK:.4f}:curve=qsin,"
               f"aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={round(pk * 48000)}S|{round(pk * 48000)}S[p];"
               f"[ob][p]amix=inputs=2:normalize=0:duration=first[a]")
    else:
        fc += "[ob]anull[a]"
    sh(["ffmpeg", "-y", "-v", "error", "-i", head_wav, "-i", body_wav, "-i", mus("bed-breaking"), "-filter_complex", fc,
        "-map", "[a]", "-ar", "48000", "-c:a", "pcm_s24le", full_raw])
    m = subprocess.run(["ffmpeg", "-v", "info", "-i", str(full_raw), "-af", f"loudnorm=I=-14:TP={TP}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    j = json.loads(m[m.rindex("{"):m.rindex("}") + 1])
    af = (f"loudnorm=I=-14:TP={TP}:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    total = T_H + nframes(body_silent) / FPS
    if fade_end:
        af += f",afade=t=out:st={total - fade_end:.3f}:d={fade_end}"
    sh(["ffmpeg", "-y", "-v", "error", "-i", full_raw, "-af", af, "-ar", "48000", "-c:a", "pcm_s24le", full])
    f = "fps=30,format=yuv420p,setsar=1"
    vf = f"[v0][v1][v2]concat=n=3:v=1:a=0[vc];[vc]" + (f"fade=t=out:st={total - fade_end:.3f}:d={fade_end}" if fade_end else "null") + "[v]"
    sh(["ffmpeg", "-y", "-v", "error", "-i", head_silent, "-i", bridge_mp4, "-i", body_silent, "-i", full, "-filter_complex",
        f"[0:v]trim=end_frame={n_head},setpts=PTS-STARTPTS,{f}[v0];[1:v]{f}[v1];[2:v]trim=start_frame={BRIDGE},setpts=PTS-STARTPTS,{f}[v2];{vf}",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-shortest", out_mp4])
    return {"head_frames": n_head, "head_gain_db": round(g, 2), "pass1": [j["input_i"], j["input_tp"]]}


def prepend_sting(sting_mp4, sting_mix, reel_mp4, out_mp4, work):
    """Alert sting «عاجل» in front of a finished reel (B style): sting -> red-line bridge -> reel, one loudness master."""
    work.mkdir(parents=True, exist_ok=True)
    hs, hw, n = prep_head(sting_mp4, sting_mix, work)
    rs = work / "reel_silent.mp4"
    rw = work / "reel.wav"
    sh(["ffmpeg", "-y", "-v", "error", "-i", reel_mp4, "-map", "0:v:0", "-c", "copy", "-an", rs])
    sh(["ffmpeg", "-y", "-v", "error", "-i", reel_mp4, "-vn", "-ar", "48000", "-c:a", "pcm_s24le", rw])
    br = bridge(hs, rs, work / "bridge.mp4", work)
    return assemble(hs, hw, n, rs, rw, br, out_mp4, work, head_over_body_lu=1.5, pickup=False, TP=-2.0)


# ------------------------------------------------------------------ QA
def qa_file(path, expect_dur=None, max_dur=None):
    pr = json.loads(sh(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout)
    v = [s for s in pr["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in pr["streams"] if s["codec_type"] == "audio"][0]
    I, TP = lufs(path)
    dur = float(pr["format"]["duration"])
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True).stderr.strip()
    res = dict(w=v["width"], h=v["height"], fps=v["r_frame_rate"], profile=v.get("profile"), pix_fmt=v["pix_fmt"], acodec=a["codec_name"],
               duration=round(dur, 3), size_mb=round(int(pr["format"]["size"]) / 1e6, 2), lufs=I, true_peak=TP, decode_errors=dec[:300])
    p = []
    if (v["width"], v["height"]) != (1080, 1920):
        p.append("resolution")
    if v["pix_fmt"] != "yuv420p":
        p.append("pix_fmt")
    if not (-15.5 <= I <= -12.5):
        p.append(f"loudness {I}")
    if TP is not None and TP > -1.0:
        p.append(f"true peak {TP}")
    if dec:
        p.append("decode errors")
    if expect_dur and abs(dur - expect_dur) > 0.7:
        p.append(f"duration {dur} != {expect_dur}")
    if max_dur and dur > max_dur:
        p.append(f"duration {dur} > {max_dur}")
    if int(pr["format"]["size"]) > 95e6:
        p.append("file too large")
    res["problems"] = p
    return res


def frames_at(path, times, outdir, prefix="kf"):
    """Extract full-res JPG frames (t in seconds; t == 0 -> decoded frame #0)."""
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for t in times:
        f = outdir / f"{prefix}_{t:07.2f}.jpg"
        if t == 0:
            sh(["ffmpeg", "-v", "error", "-y", "-i", path, "-frames:v", "1", "-q:v", "3", f])
        else:
            sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-q:v", "3", f])
        out.append(str(f))
    return out


def dom_qa(comp, times, outfile, shots=None, timeout=1500):
    """DOM safe-zone QA (qa.mjs): Meta 9:16 safe zone, action rail, logo bug, overflow, overlaps, banned names, map label rules."""
    chrome = os.getenv("KAHWA_CHROME") or os.getenv("HYPERFRAMES_BROWSER_PATH")
    if not chrome:
        try:
            chrome = subprocess.run([RR.hf_bin(), "browser", "path"], capture_output=True, text=True, env=RR.hf_env(),
                                    timeout=60).stdout.strip().splitlines()[-1]
        except Exception:
            chrome = ""
    env = dict(os.environ, CHROME=chrome, NODE_PATH=str(REELS / "node_modules"))
    cmd = ["node", str(HERE / "qa.mjs"), str(comp), ",".join(f"{t:.2f}" for t in times), str(shots or "")]
    for attempt in range(2):         # a navigation timeout / stuck port (run 40945212474) is transient: one retry, new port
        env["PORT"] = str(8766 + attempt * 17 + (os.getpid() % 13))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as ex:
            r = subprocess.CompletedProcess(cmd, 124, "", f"timeout after {timeout}s: {ex}")
        if not r.returncode:
            break
        time.sleep(5)
    if r.returncode:
        raise RuntimeError(f"DOM QA crashed: {r.stderr[:900]}")
    Path(outfile).write_text(r.stdout)
    d = json.loads(r.stdout)
    viol = [dict(t=x["t"], **v) for x in d["rep"] for v in x["violations"]]
    labels = sorted({l for x in d["rep"] for l in x["mapLabels"]})
    return {"violations": viol, "console_errors": d["errors"], "map_labels": labels, "checked": len(d["rep"])}


def qa_times(B):
    ts = []
    for s in B["scenes"]:
        d = s["d"]
        ts += [round(s["t"] + min(0.9, d * 0.3), 2), round(s["t"] + d * 0.6, 2), round(s["t"] + d - 0.25, 2)]
    return sorted(set(t for t in ts if 0 <= t < B["duration"]))
