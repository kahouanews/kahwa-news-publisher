#!/usr/bin/env python3
"""Kahwa News — Concept B «الشبكة» SHORT STINGS (variant module, copied from work/opener/src/opener.py 2026-10-29;
the originals in work/opener/ are untouched). Adds concepts:
  E  edition sting for the multi-headline reel series «العالم في 30 ثانية» (3.56 s, 7 beats)
  X  alert sting «عاجل» for verified top-tier alerts (3.05 s, 6 beats)
    python opener_b.py all --concept E --time 14:00 --out ../B-edition-sting.mp4
    python opener_b.py all --concept X --time 14:37 --out ../B-alert-sting.mp4
Original module doc follows.
Kahwa News — newscast opener module («قهوة اليوم»). Self-contained, parameterised, CPU-only.

    python opener.py all --concept A --out ../concept-A.mp4 [--date 2026-10-29] [--time 15:47] [--title "قهوة اليوم"]
    python opener.py build|render|finish|qa --concept A ...      (steps; render runs detached with --detach)

Concepts
  A «المدار»  globe at dawn (NASA Black Marble -> Blue Marble), 3D orbit rings, sting on the breaking bed. 10.17 s
  B «الشبكة»  Natural-Earth dot world on a tilted plane, grid, clock that becomes the time chip, explainer bed. 9.15 s
Both end on the SAME static hold frame (logo, title, date/time row, «أبرز العناوين» hand-off tab) for >= 1.2 s.

Music: work/music/identity/global (118 bpm). Sting logo notes D4 A4 G#4 E5 D5 at +0.000 +0.254 +0.636 +0.890 +1.017 s.
Output: 1080x1920, 30 fps, H.264 High yuv420p, AAC 48 kHz, -14 LUFS integrated, true peak <= -1 dBTP.
Text rules: never writes a country name; time zone shown as Greenwich; Arabic animated per word only.
"""
import argparse, datetime as dt, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = Path(__file__).resolve().parents[1] if HERE.name == "stings" else Path(__file__).resolve().parents[2]   # kahwa_publisher/
REPO = PKG.parent
# repo wiring (2026-10-29): global sonic identity (own composition, CC0 samples) in assets/music/identity (FLAC, lossless),
# shared fonts/GSAP/logos from kahwa_publisher/reels/static, pinned HyperFrames from kahwa_publisher/reels/node_modules.
MUSIC = REPO / "assets" / "music" / "identity"
REELS = PKG / "reels"
HF_BIN = Path(os.getenv("HF_BIN") or REELS / "node_modules" / ".bin" / "hyperframes")   # pinned hyperframes@0.8.214
BUILD = Path(os.getenv("KAHWA_OPENER_BUILD", "/tmp/kahwa-opener"))
PLATE_CACHE = Path(os.getenv("KAHWA_PLATE_CACHE", str(BUILD / "cache")))
STATIC = REELS / "static"
STATIC_FILES = ["Almarai-Bold.ttf", "Almarai-ExtraBold.ttf", "Almarai-Light.ttf", "Almarai-Regular.ttf", "Barlow-BlackItalic.ttf",
                "CustomEase.min.js", "grain.png", "gsap.min.js", "logo-band.svg", "logo-kahwa.svg"]


def music_file(name):
    """bed-breaking.wav -> assets/music/identity/bed-breaking.flac (the repo keeps lossless FLAC copies)."""
    p = MUSIC / name
    return p if p.exists() else p.with_suffix(".flac")
BPM = 118.0
BEAT = 60.0 / BPM
NOTES = [0.0, 0.5 * BEAT, 1.25 * BEAT, 1.75 * BEAT, 2.0 * BEAT]   # from global_id.py MOTIF
CONCEPTS = {
    #        bed file            bed beats before sting   total beats
    "A": dict(bed="bed-breaking.wav", pre_beats=12, total_beats=20),   # 6.102 s pre-roll, 10.169 s
    "B": dict(bed="bed-explainer.wav", pre_beats=10, total_beats=18),  # 5.085 s pre-roll (before the bed's own logo), 9.153 s
    # short stings (JS branch "S"): clock+dot-world pre-roll, then the 5-note sting; red bar -> logo band; clock -> time chip
    "E": dict(bed="bed-explainer.wav", pre_beats=2, total_beats=7, npings=3,           # 1.017 s pre-roll, 3.559 s
              title="العالم في 30 ثانية", handoff="3 أخبار الآن", clock_label="آخر الأخبار · توقيت غرينتش", title_px=100),
    "X": dict(bed="bed-breaking.wav", pre_beats=1, total_beats=6, npings=1,            # 0.508 s pre-roll, 3.051 s
              title="عاجل", handoff="التفاصيل", clock_label="عاجل · توقيت غرينتش", title_px=140),
}
WD = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MO = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
FORBIDDEN = ["المغرب", "مغربي", "الإمارات", "إماراتي", "الجزائر", "جزائري", "إسرائيل", "إسرائيلي",
             "Morocco", "UAE", "Emirates", "Algeria", "Israel"]


def timing(concept):
    c = CONCEPTS[concept]
    S = c["pre_beats"] * BEAT
    T = round(c["total_beats"] * BEAT, 3)
    return S, T, S + NOTES[4]


def params(a):
    now = dt.datetime.now(dt.timezone.utc)
    d = dt.date.fromisoformat(a.date) if a.date else now.date()
    hhmm = a.time or now.strftime("%H:%M")
    if not re.fullmatch(r"\d{2}:\d{2}", hhmm):
        raise SystemExit("--time must be HH:MM (24 h, Greenwich)")
    S, T, land = timing(a.concept)
    C = CONCEPTS[a.concept]
    if a.concept in "EX":                           # variant defaults unless the caller overrode them
        if a.title == "قهوة اليوم": a.title = C["title"]
        if a.handoff == "أبرز العناوين": a.handoff = C["handoff"]
        if a.clock_label == "توقيت غرينتش": a.clock_label = C["clock_label"]
    P = dict(concept=a.concept, variant=("S" if a.concept in "EX" else a.concept), title_px=C.get("title_px", 128), T=T, S=round(S, 4), beat=BEAT, notes=NOTES,
             title=a.title.split(), tagline=a.tagline.split(), handoff=a.handoff,
             date_ar=f"{WD[d.weekday()]} {d.day} {MO[d.month - 1]} {d.year}",
             hud_right=d.strftime("%d.%m.%Y"), time_hhmm=hhmm, time_hms=hhmm + ":00",
             tz_label=a.tz_label, clock_label=a.clock_label, pings=[])
    if a.concept in "BEX":
        allp = json.loads((HERE / "assets" / "pings.json").read_text())["pings"]
        if a.pings_ll:                               # story locations as lon,lat (same equirectangular 76N..56S as dotmap.py)
            P["pings"] = [dict(x=(float(lo) + 180.0) / 360.0, y=(76.0 - float(la)) / 132.0)
                          for lo, la in (q.split(",") for q in a.pings_ll.split(";"))]
        elif a.pings:                                # story locations as x,y in the 2400x880 dot-plane (0..1)
            P["pings"] = [dict(x=float(x), y=float(y)) for x, y in (q.split(",") for q in a.pings.split(";"))]
        elif a.concept == "B":
            P["pings"] = allp
        else:
            P["pings"] = [allp[4]] if a.concept == "X" else [allp[i] for i in (7, 3, 1)]
    blob = json.dumps(P, ensure_ascii=False)
    bad = [w for w in FORBIDDEN if w in blob]
    if bad:
        raise SystemExit(f"editorial gate: forbidden term(s) {bad}")
    return P


# ------------------------------------------------------------------ build
def ensure_plate(T, land):
    key = hashlib.sha1((HERE / "globe.py").read_bytes() + f"|{T}|{land}".encode()).hexdigest()[:10]
    out = PLATE_CACHE / f"plate-A-{key}.mp4"
    if not out.exists():
        if True:
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, str(HERE / "globe.py"), "--out", str(out), "--dur", str(T), "--land", str(land)], check=True)
    return out


def build(a):
    P = params(a)
    S, T, land = timing(a.concept)
    out = BUILD / f"comp-{a.concept}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in STATIC_FILES:
        shutil.copy(STATIC / f, out / f)
    for f in ("opener.css", "opener.js"):
        shutil.copy(HERE / "templates" / f, out / f)
    plate_html = ""
    if a.concept == "A":
        plate = ensure_plate(T, round(land, 3))
        shutil.copy(plate, out / "plate.mp4")
        plate_html = (f'<video id="plate" class="clip plate" src="plate.mp4" data-start="0" data-duration="{T}" '
                      f'data-track-index="1" muted playsinline></video>')
    else:   # B, E, X: pure HTML over the dot world
        for f in ("dots.png", "dots_lit.png"):
            shutil.copy(HERE / "assets" / f, out / f)
    html = (HERE / "templates" / "index.html").read_text()
    html = (html.replace("__CONCEPT__", P["variant"] + " c-" + a.concept).replace("__DURATION__", str(T)).replace("__PLATE__", plate_html)
            .replace("__OPENER_JSON__", json.dumps(P, ensure_ascii=False)))
    (out / "index.html").write_text(html)
    (out / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "."}, "media": {"autoProxy": False}}, indent=1))
    (out / "meta.json").write_text(json.dumps({"id": f"kahwa-opener-{a.concept}", "name": f"Kahwa opener {a.concept}"}))
    (out / "params.json").write_text(json.dumps(P, ensure_ascii=False, indent=1))
    print(json.dumps(dict(built=str(out), T=T, sting=round(S, 3), land=round(land, 3)), ensure_ascii=False))
    return out


# ------------------------------------------------------------------ render (HyperFrames)
def hf_env():
    env = dict(os.environ)
    env.update(DO_NOT_TRACK="1", HYPERFRAMES_TELEMETRY="0", HYPERFRAMES_SKIP_SKILLS="1")
    for b in ("/usr/bin/chromium", "/workspace/work/chromium-arm/chrome"):
        if Path(b).exists() and "HYPERFRAMES_BROWSER_PATH" not in env:
            env["HYPERFRAMES_BROWSER_PATH"] = b
    return env


def render(a):
    out = BUILD / f"comp-{a.concept}"
    silent = out / "silent.mp4"
    cmd = [str(HF_BIN), "render", str(out), "-o", str(silent), "--quality", "high", "--workers", str(a.workers),
           "--fps", "30", "--no-browser-gpu", "--quiet"]
    log = out / "render.log"
    for f in ("render.rc", "render.time"):
        (out / f).unlink(missing_ok=True)
    if a.detach:
        sh = (f"start=$(date +%s.%N); {' '.join(cmd)} > {log} 2>&1; echo $? > {out}/render.rc; "
              f"end=$(date +%s.%N); echo \"$start $end\" > {out}/render.time")
        subprocess.Popen(["setsid", "-f", "bash", "-c", sh], env=hf_env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("detached:", log)
        return None
    t0 = time.time()
    with open(log, "w") as lf:
        rc = subprocess.run(cmd, env=hf_env(), stdout=lf, stderr=subprocess.STDOUT).returncode
    (out / "render.time").write_text(f"{t0} {time.time()}")
    (out / "render.rc").write_text(str(rc))
    if rc:
        raise SystemExit(f"render failed rc={rc}, see {log}")
    return silent


# ------------------------------------------------------------------ audio
def mix(a, wav_out):
    import numpy as np, soundfile as sf
    S, T, land = timing(a.concept)
    bed, sr = sf.read(music_file(CONCEPTS[a.concept]["bed"]), always_2d=True)
    st, sr2 = sf.read(music_file("sting.wav"), always_2d=True)
    assert sr == sr2 == 48000
    n = int(round(T * sr))
    y = np.zeros((n, 2))
    i_s = int(round(S * sr))
    b = bed[:i_s + int(0.03 * sr)].copy()          # bed up to the sting hit (+30 ms overlap)
    fade = int(0.06 * sr)
    b[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    y[:len(b)] += b
    s = st[: n - i_s]
    y[i_s:i_s + len(s)] += s * 1.0
    tail = int(0.35 * sr)                           # gentle end so the cut to headlines is clean
    y[-tail:] *= np.linspace(1, 0, tail)[:, None]
    sf.write(wav_out, y.astype(np.float32), sr, subtype="FLOAT")


def loudnorm(src, dst, I=-14.0, TP=-1.5):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af", f"loudnorm=I={I}:TP={TP}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    blob = r.stderr[r.stderr.rfind("{"):]
    m = json.loads(blob[:blob.find("}") + 1])
    af = (f"loudnorm=I={I}:TP={TP}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "48000",
                    str(dst)], check=True)


def measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    t = r.stderr[r.stderr.rfind("Summary:"):]
    I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", t).group(1))
    TPk = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", t[t.find("True peak"):]).group(1))
    return I, TPk


def finish(a):
    out = BUILD / f"comp-{a.concept}"
    S, T, land = timing(a.concept)
    raw, norm = out / "mix.wav", out / "mix-norm.wav"
    mix(a, raw)
    loudnorm(raw, norm)
    final = Path(a.out).resolve()
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp") / f"kahwa-opener-{a.concept}-{os.getpid()}.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(out / "silent.mp4"), "-i", str(norm),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-profile:v", "high", "-preset", "slow",
                    "-crf", "16", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                    "-t", f"{T:.3f}", "-movflags", "+faststart", str(tmp)], check=True)
    shutil.copy(tmp, final)          # encode on local disk, then copy (network FS can fail the faststart rewrite)
    tmp.unlink()
    I, TPk = measure(final)
    if abs(I + 14.0) > 0.1 and TPk + (-14.0 - I) <= -1.0:   # short clips: loudnorm lands ~0.2 LU off; one linear trim
        trim = out / "mix-trim.wav"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(norm), "-af",
                        f"volume={-14.0 - I:.2f}dB", "-ar", "48000", str(trim)], check=True)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(out / "silent.mp4"), "-i", str(trim),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-profile:v", "high", "-preset", "slow",
                        "-crf", "16", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                        "-t", f"{T:.3f}", "-movflags", "+faststart", str(tmp)], check=True)
        shutil.copy(tmp, final); tmp.unlink()
        I, TPk = measure(final)
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final)],
                                   capture_output=True, text=True).stdout)
    v = [s for s in pr["streams"] if s["codec_type"] == "video"][0]
    rep = dict(file=str(final), duration=float(pr["format"]["duration"]), size=f'{v["width"]}x{v["height"]}',
               fps=v["r_frame_rate"], profile=v.get("profile"), pix_fmt=v["pix_fmt"], lufs=I, true_peak_dbtp=TPk,
               sting_s=round(S, 3), land_s=round(land, 3), hold_from_s=round(T - 1.25, 3))
    (out / "qa.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, ensure_ascii=False))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "render", "finish", "all"])
    ap.add_argument("--concept", choices=["A", "B", "E", "X"], required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, Greenwich)")
    ap.add_argument("--time", help="HH:MM Greenwich (default: now)")
    ap.add_argument("--title", default="قهوة اليوم")
    ap.add_argument("--tagline", default="العالم في فنجان")
    ap.add_argument("--handoff", default="أبرز العناوين")
    ap.add_argument("--tz-label", dest="tz_label", default="بتوقيت غرينتش")
    ap.add_argument("--clock-label", dest="clock_label", default="توقيت غرينتش")
    ap.add_argument("--pings", default=None, help='story locations "x,y;x,y" on the dot plane (0..1)')
    ap.add_argument("--pings-ll", dest="pings_ll", default=None, help='story locations "lon,lat;lon,lat" (no labels drawn)')
    ap.add_argument("--workers", default="2")
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args()
    a.out = a.out or str(HERE.parent / f"B-{a.concept}.mp4")
    if a.cmd in ("build", "all"):
        build(a)
    if a.cmd in ("render", "all"):
        render(a)
    if a.cmd in ("finish", "all"):
        finish(a)


if __name__ == "__main__":
    main()
