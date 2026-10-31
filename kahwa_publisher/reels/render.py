"""Kahwa News reel renderer (production port of reels-proto v2).

story dict (see story.py) -> plates (openly licensed footage/photos, or none) + GL Transitions 2.0 shaders
-> HyperFrames 0.8.214 composition -> own music bed (assets/music, music.json) + CC0 SFX -> H.264/AAC MP4.

Styles: A = explainer (default reel, ~42 s), B = breaking (urgent stories, ~19.5 s).
python -m kahwa_publisher.reels.render --story story.json --style A --out reel.mp4
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import audio, mix, plates
from ..attribution import outlet_hits

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                         # repo root
ASSETS = ROOT / "assets"
MUSIC_DIR = ASSETS / "music"
HF_VERSION = "0.8.214"

# ------------------------------------------------------------------ editorial gate (hard fail)
AR_PREFIX = r"(?:[وفبكل]|وب|ول|فب|فل|وك)?(?:ال|لل)?"      # و ب ل ف ك + ال / لل  (والجزائر, بالمغرب, للإمارات)
AR_SUFFIX = r"(?:ي|ية|يين|يون|يان|يتان|يات|ه|ها|هم)?"
AR_STEMS = [
    "مغرب", "مغاربة", "رباط", "دار البيضاء", "مراكش", "طنجة", "محمد السادس", "أخنوش", "مخزن", "بوليساريو", "صحراء الغربية",
    "[إا]مارات", "[إا]ماراتي", "[أا]بو ?ظبي", "دبي", "شارقة", "بن زايد",
    "جزائر", "وهران", "تبون",
    "[إا]سرائيل", "تل [أا]بيب", "نتنياهو", "كنيست", "احتلال",
]
LATIN = ["morocc\\w*", "rabat", "casablanca", "marrakesh", "emirat\\w*", "uae", "abu dhabi", "dubai", "sharjah",
         "algeri\\w*", "algiers", "oran", "israel\\w*", "tel aviv", "western sahara"]
SPORTS = ["كرة القدم", "مباراة", "مباريات", "الدوري", "منتخب", "football", "soccer", "match", "league", "fifa", "olympic\\w*"]
BANNED_ISO = {"MAR", "ARE", "DZA", "ISR", "ESH", "SAH"}   # story LOCATION ban (text rule); maps draw + label them (geo.py)
AR_LETTER = "\u0600-\u06FF"
BAIT = re.compile(r"(شاركوا|انشروا|علّقوا|علقوا|اكتبوا|صوّتوا|صوتوا|تابعونا|اضغط|لايك|منشن|تابع الصفحة|في التعليقات)")


def banned_hits(text):
    hits = []
    text = re.sub(r"[\u064B-\u0652\u0640]", "", text)
    low = text.lower()
    for st in AR_STEMS:
        if re.search(rf"(?<![{AR_LETTER}]){AR_PREFIX}{st}{AR_SUFFIX}(?![{AR_LETTER}])", text):
            hits.append(st)
    for w in LATIN + SPORTS:
        if re.search(rf"(?<![\w]){w}(?![\w])", low):
            hits.append(w)
    return hits


def gate(story):
    """Hard editorial gate. Raises ValueError on any violation (the reel is then skipped, never posted)."""
    vis = {k: v for k, v in story.items() if k not in ("source_texts", "source_urls", "media_rejected")}
    blob = json.dumps(vis, ensure_ascii=False)
    hits = banned_hits(blob)
    loc = story.get("location", {})
    if loc.get("country_iso3") in BANNED_ISO:
        hits.append("map:" + loc.get("country_iso3", ""))
    mv = loc.get("map") or {}
    from .. import geo
    if mv.get("ok") and loc.get("country_iso3") in geo.NO_FOCUS_ISO:   # never focus a map on a no-focus territory
        hits.append("map focused on a no-focus territory: " + str(loc.get("country_iso3")))
    from .. import safety
    vh = safety.output_hits(blob)
    if vh:                                            # calm factual tone: no graphic / gore wording in reels
        hits.append(f"graphic/gore wording: {vh[:4]}")
    if story.get("media") and safety.is_conflict(blob):   # war story: graphic-only reel (map + text), never photos/footage
        hits.append("war story with photos/footage (must be graphic-only)")
    if story.get("ai_line"):
        hits.append("ai_line present (user hard rule: no AI-assisted line)")
    if BAIT.search(blob):
        hits.append("engagement bait")
    # attribution policy (2026-10-29): never name a competing outlet on screen / in chips (media credits checked below)
    oh = outlet_hits(json.dumps({k: v for k, v in vis.items() if k != "media"}, ensure_ascii=False))
    if oh:
        hits.append(f"competing outlet named: {oh[:4]}")
    if hits:
        raise ValueError(f"GATE FAIL: {hits}")
    for mid, m in story.get("media", {}).items():
        if not m.get("credit") or not m.get("license") or not re.search(r"ويكيميديا|NASA|NOAA", m["credit"]):
            raise ValueError(f"GATE FAIL: media {mid} lacks an open-licence credit")
        if outlet_hits(m.get("credit", "") + " " + m.get("credit_short", "")):
            raise ValueError(f"GATE FAIL: media {mid} is credited to a competing outlet")
        if m.get("archive") and m.get("label") not in ("لقطات أرشيفية", "صورة أرشيفية"):
            raise ValueError(f"GATE FAIL: archive media {mid} lacks its archive label")
    return True


def self_test_gate():
    for w in ["والجزائر", "بالمغرب", "للإمارات", "الإسرائيلية", "فالجزائريون", "Moroccan", "UAE", "وتل أبيب", "المغربية", "الاحتلال"]:
        assert banned_hits(w), w
    for ok in ["أندرا براديش", "الأمطار", "غرب", "الإمارة", "مغادرة", "الفلبين"]:
        assert not banned_hits(ok), ok


# ------------------------------------------------------------------ GL transitions (gl-transitions 2.0, MIT)
def glsl(name):
    src = (HERE / "static" / "glsl" / f"{name}.glsl").read_text()
    params = {}
    for m in re.finditer(r"uniform\s+(float|vec2|int|bool)\s+(\w+);\s*//\s*=\s*([^\n]+)", src):
        typ, nm, val = m.groups()
        params[nm] = dict(type=typ, default=[float(x) for x in re.findall(r"-?\d+\.?\d*", val)])
    lic = re.search(r"License:\s*([^\n]+)", src).group(1).strip()
    auth = re.search(r"Author:\s*([^\n]+)", src).group(1).strip()
    return dict(name=name, glsl=src, params=params, license=lic, author=auth)


# ------------------------------------------------------------------ music library
MUSIC_SOURCES = ("library", "synth")


def music_source():
    """Repo variable MUSIC_SOURCE (one-line switch): "library" = our ACE-Step 1.5 tracks in assets/music (default),
    "synth" = the code-synthesised bed in audio.py (no model, no samples). Anything else -> library."""
    v = (os.getenv("MUSIC_SOURCE") or "library").strip().lower()
    return v if v in MUSIC_SOURCES else "library"


def pick_music(story, style):
    """Mood -> track from assets/music/music.json. Tragic/disaster stories always get the calm bed, low.
    Mood rules: tragic -> calm (lowest level), style B (urgent) -> tension, else the story's mood (serious/light/calm)."""
    src = music_source()
    tracks = []
    if src == "library":
        try:
            lib = json.loads((MUSIC_DIR / "music.json").read_text())
            tracks = [t for t in lib["tracks"] if (MUSIC_DIR / t["file"]).exists()]
        except Exception:
            tracks = []
    mood = story.get("mood") or "serious"
    if story.get("tragic"):
        mood = "calm"
    elif style == "B":
        mood = "tension"
    elif mood not in ("serious", "light", "calm", "tension"):
        mood = "serious"
    level = {"calm": -21.0, "tension": -18.0, "light": -18.0, "serious": -18.5}[mood]
    pool = [t for t in tracks if t["mood"] == mood] or [t for t in tracks if t["mood"] in ("serious", "calm")]
    if not pool:   # MUSIC_SOURCE=synth or library missing: our own code-synthesised bed, never a borrowed track
        return dict(file=None, procedural={"calm": "photo", "tension": "breaking"}.get(mood, "explainer"),
                    bpm={"tension": 124, "calm": 70}.get(mood, 96), offset=0.0, lufs=level, mood=mood, title="procedural",
                    source="synth" if src == "synth" else "synth (library missing)")
    h = int(hashlib.sha1(str(story.get("id", "")).encode()).hexdigest(), 16)
    t = pool[h % len(pool)]
    return dict(file=t["file"], offset=float(t.get("start", 0.0)), lufs=level, mood=mood, title=t.get("id", t["file"]),
                source="library")


# ------------------------------------------------------------------ plans
def P(id, media, t, d, mode="full", move="in", grade="std"):
    return dict(id=id, media=media, t=round(t, 3), d=round(d, 3), mode=mode, move=move, grade=grade)


def T(t, shader, d, **params):
    mirror = params.pop("mirror", False)
    return dict(t=t, shader=shader, d=d, params=params, mirror=mirror)


def _mode_for(m, want, slot=""):
    """Landscape media goes into the framed window (never upscaled), portrait-ish may go full-bleed.
    The hook is always full-bleed (poster frame; a mild upscale under the scrim is acceptable for 3 s)."""
    w, h = m.get("w") or 0, m.get("h") or 0
    if slot == "hook":
        return "full"
    if want == "full" and w and h and (w / h > 0.9 or h < 1500):
        return "framed"
    return want


def plan_for(style, S):
    sc, pl, tr, wipes, cues = [], [], [], [], []
    media_ids = list(S.get("media", {}).keys())

    def add(id, t, d, **kw):
        sc.append(dict(id=id, t=round(t, 3), d=round(d, 3), type=kw.pop("type", id), **kw))

    if style == "A":
        add("hook", 0, 3.0); add("sat", 3.0, 2.5); add("map", 5.5, 5.0)
        for i in range(3):
            add(f"fact{i}", 10.5 + 5 * i, 5.0, type="fact", idx=i)
        add("number", 25.5, 6.0); add("next", 31.5, 6.0); add("end", 37.5, 4.5, type="end")
        # slots in fill priority; graphic background shows wherever a slot stays empty
        slots = [("hook", 0, 3.0, "full", "in"), ("f0a", 10.5, 2.5, "framed", "in"), ("f1a", 15.5, 2.5, "full", "in"),
                 ("f2a", 20.5, 2.5, "framed", "drift"), ("na", 31.5, 3.0, "full", "in"), ("sat", 3.0, 2.5, "framed", "in"),
                 ("f0b", 13.0, 2.5, "framed", "out"), ("f1b", 18.0, 2.5, "framed", "out"), ("f2b", 23.0, 2.5, "framed", "in"),
                 ("nb", 34.5, 3.0, "full", "out")]
        for (sid, t, d, mode, move), mid in zip(slots, media_ids):
            m = S["media"][mid]
            pl.append(P("p-" + sid, mid, t, d, _mode_for(m, mode, sid), move, "sat" if m.get("satellite") else "std"))
        pl.sort(key=lambda p: p["t"])
        want = [(3.0, "CrossZoom", 0.7, dict(strength=0.32, mirror=True)),
                (15.5, "DirectionalWipe", 0.55, dict(direction=[-1.0, 0.0], smoothness=0.3)),
                (20.5, "LinearBlur", 0.6, dict(intensity=0.08)), (34.5, "LinearBlur", 0.7, dict(intensity=0.08))]
        for t, sh, d, kw in want:
            tr.append(T(t, sh, d, **kw))
        wipes += [10.5, 25.5]
        dur = 42.0
        cues += [dict(t=0.17, sfx="hit", gain=0.8)]
        cues += [dict(t=w, sfx="deep", gain=0.9) for w in wipes]
        cues += [dict(t=25.8 + k * 0.09, sfx="tick", gain=0.8) for k in range(18)] + [dict(t=27.9, sfx="stamp")]
        cues += [dict(t=37.62, sfx="hit", gain=0.9)]
    elif style == "B":
        add("hook", 0, 16.0); add("end", 16.0, 3.5, type="end")
        for i in range(4):
            add(f"line{i}", 4.0 + 3 * i, 3.0, type="marker", idx=i)
        slots = [("b0", 0, 4.0, "in"), ("b1", 4.0, 3.0, "out"), ("b2", 7.0, 3.0, "in"), ("b3", 10.0, 3.0, "drift"),
                 ("b4", 13.0, 3.0, "in")]
        for (sid, t, d, move), mid in zip(slots, media_ids):
            pl.append(P("p-" + sid, mid, t, d, "full", move, "dark"))
        tr += [T(4.0 + 3 * i, "DirectionalWipe", 0.45, direction=[-1.0, 0.0], smoothness=0.25) for i in range(4)]
        dur = 19.5
        cues += [dict(t=0.05, sfx="hit", gain=0.9), dict(t=0.17, sfx="tick", gain=1.2), dict(t=16.1, sfx="hit", gain=0.85)]
    else:
        raise ValueError("unknown style " + style)
    # keep only transitions whose both sides are plates; every kept one gets a whoosh
    kept = []
    for x in tr:
        a = next((p for p in pl if abs(p["t"] + p["d"] - x["t"]) < 1e-6), None)
        b = next((p for p in pl if abs(p["t"] - x["t"]) < 1e-6), None)
        if not a or not b:
            continue
        x["from"], x["to"] = a["id"], b["id"]
        x["t0"], x["t1"] = round(x["t"] - x["d"] / 2, 4), round(x["t"] + x["d"] / 2, 4)
        x["grab_from"] = round(x["t0"] - a["t"], 4)
        x["grab_to"] = round(x["t1"] - b["t"], 4)
        kept.append(x)
        cues.append(dict(t=x["t"], sfx="whoosh", gain=0.8 if style == "A" else 0.75))
    if style == "B":   # card changes always get a soft whoosh, even without a picture transition
        for i in range(4):
            if not any(abs(c["t"] - (4.0 + 3 * i)) < 1e-6 for c in cues):
                cues.append(dict(t=4.0 + 3 * i, sfx="whoosh", gain=0.6))
    end = next(s for s in sc if s["type"] == "end")
    segs = [s for s in sc if s["type"] not in ("end", "marker")]
    if style == "B":
        segs = [dict(t=0, d=4.0)] + [dict(t=4.0 + 3 * i, d=3.0) for i in range(4)]
    return dict(style=style, duration=dur, scenes=sc, plates=pl, transitions=kept, wipes=wipes, cues=cues, segments=segs,
                music=pick_music(S, style), end_t=end["t"],
                whoosh_files=["kinoton-whoosh1-427823.mp3", "lloydevans09-whoosh-332003.mp3", "adhdreaming-swoosh-632763.mp3"],
                deep_files=["kinoton-deepwhoosh1-351256.mp3"])


# ------------------------------------------------------------------ prep
def prep(S, style, build, media_root):
    gate(S)
    Pn = plan_for(style, S)
    out = Path(build) / f"style-{style}"
    if out.exists():
        shutil.rmtree(out)
    for d in ("plates", "gltex"):
        (out / d).mkdir(parents=True)
    for f in (HERE / "static").iterdir():
        if f.is_file():
            shutil.copy(f, out / f.name)
    for f in ("kahwa.css", "kahwa.js", "glx.js"):
        shutil.copy(HERE / "templates" / f, out / f)
    cache = Path(build) / "plate-cache"
    cache.mkdir(parents=True, exist_ok=True)
    grabs = {}
    for x in Pn["transitions"]:
        grabs.setdefault(x["from"], []).append(x["grab_from"])
        grabs.setdefault(x["to"], []).append(x["grab_to"])
    timings, media_html = {}, []
    for p in Pn["plates"]:
        m = S["media"][p["media"]]
        key = f"{plates.BOX}-{style}-{p['id']}-{m['file']}-{p['mode']}-{p['move']}-{p['grade']}-{p['d']}-{m.get('in', 0)}-{m.get('crop')}"
        h = hashlib.sha1(key.encode()).hexdigest()[:12]
        clip = cache / f"{h}.mp4"
        need = list(grabs.get(p["id"], []))
        if not clip.exists() or not all((cache / f"{h}-{g:.3f}.jpg").exists() for g in need):
            t0 = time.time()
            plates.render_plate(m, media_root, clip, p["d"], mode=p["mode"], move=p["move"], grade=p["grade"],
                                grabs=need, grab_dir=cache, key=h)
            timings[p["id"]] = round(time.time() - t0, 1)
        shutil.copy(clip, out / "plates" / f"{p['id']}.mp4")
        for g in need:
            shutil.copy(cache / f"{h}-{g:.3f}.jpg", out / "gltex" / f"{p['id']}-{g:.3f}.jpg")
        media_html.append(f'<video id="pl-{p["id"]}" class="clip plate" src="plates/{p["id"]}.mp4" data-start="{p["t"]}" '
                          f'data-duration="{p["d"]}" data-track-index="1" muted playsinline></video>')
    shaders, tex_html = {}, []
    for x in Pn["transitions"]:
        shaders[x["shader"]] = glsl(x["shader"])
        x["tex_from"] = f"gltex/{x['from']}-{x['grab_from']:.3f}.jpg"
        x["tex_to"] = f"gltex/{x['to']}-{x['grab_to']:.3f}.jpg"
        for k in ("tex_from", "tex_to"):
            tex_html.append(f'<img class="gltex" data-key="{x[k]}" src="{x[k]}" alt="" />')
    photos = {k: dict(credit=v["credit"], archive=v.get("archive", False), label=v.get("label", ""), short=v.get("credit_short", ""))
              for k, v in S.get("media", {}).items()}
    vis = {k: v for k, v in S.items() if k not in ("source_texts",)}
    reel = dict(style=style, story=vis, plan=Pn, media=photos, shaders=shaders, box=list(plates.BOX))
    html = (HERE / "templates" / "index.html").read_text()
    html = (html.replace("__STYLE__", style).replace("__DURATION__", str(Pn["duration"]))
            .replace("__MEDIA__", "\n".join(media_html)).replace("__GLTEX__", "\n".join(sorted(set(tex_html))))
            .replace("__REEL_JSON__", json.dumps(reel, ensure_ascii=False).replace("</", "<\\/")))
    (out / "index.html").write_text(html)
    (out / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "."}, "media": {"autoProxy": False}}, indent=1))
    (out / "meta.json").write_text(json.dumps({"id": f"kahwa-{style}", "name": f"Kahwa reel {style}"}))
    (out / "plan.json").write_text(json.dumps(Pn, ensure_ascii=False, indent=1))
    return out, Pn, timings


# ------------------------------------------------------------------ render (HyperFrames)
def hf_env():
    env = dict(os.environ)
    env.update(DO_NOT_TRACK="1", HYPERFRAMES_TELEMETRY="0", HYPERFRAMES_SKIP_SKILLS="1")
    # arm64 sandbox only: Debian Chromium. On the x86 GitHub runner HyperFrames uses its own Chrome Headless Shell.
    if os.uname().machine in ("aarch64", "arm64") and Path("/usr/bin/chromium").exists():
        env.setdefault("HYPERFRAMES_BROWSER_PATH", "/usr/bin/chromium")
    return env


def hf_bin():
    for c in (os.getenv("HYPERFRAMES_BIN"), HERE / "node_modules" / ".bin" / "hyperframes"):
        if c and Path(c).exists():
            return str(c)
    raise FileNotFoundError("hyperframes not installed: npm ci --prefix kahwa_publisher/reels")


def render(out, workers="auto", timeout=1500):
    silent = out / "silent.mp4"
    cmd = [hf_bin(), "render", str(out), "-o", str(silent), "--quality", "high", "--workers", str(workers), "--fps", "30",
           "--no-browser-gpu", "--quiet", "--crf", "20", "--max-bitrate", "9M"]
    t0 = time.time()
    with open(out / "render.log", "w") as lf:
        rc = subprocess.run(cmd, env=hf_env(), stdout=lf, stderr=subprocess.STDOUT, timeout=timeout).returncode
    if rc or not silent.exists():
        tail = (out / "render.log").read_text()[-1500:]
        raise RuntimeError(f"hyperframes render failed rc={rc}: {tail}")
    return silent, round(time.time() - t0, 1)


def finish(out, Pn, out_mp4):
    raw, norm = out / "mix.wav", out / "mix-norm.wav"
    mix.render(Pn, ASSETS, raw)
    audio.loudnorm(str(raw), str(norm), I=-14, TP=-1.5)
    final = out / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "silent.mp4"), "-i", str(norm),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                    "-t", str(Pn["duration"]), "-shortest", "-map_metadata", "-1", "-movflags", "+faststart", str(final)], check=True)
    Path(out_mp4).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(final, out_mp4)
    _, _, tp_steps = audio.fix_true_peak(out_mp4)       # self-heal: AAC overshoot above -1 dBTP -> trim and re-encode audio
    res = qa(out_mp4, Pn)
    if tp_steps:
        res["tp_fix"] = tp_steps
    return res


def qa(path, Pn):
    """Never post a broken reel: hard checks on the finished file."""
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                   capture_output=True, text=True).stdout)
    v = [s for s in pr["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in pr["streams"] if s["codec_type"] == "audio"][0]
    I, TP = audio.measure(str(path))
    dur = float(pr["format"]["duration"])
    size = int(pr["format"]["size"])
    # black-frame / frozen check on a few frames: mean luma must not be ~0 everywhere
    lum = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=1,scale=54:96,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    import numpy as np
    fr = np.frombuffer(lum, np.uint8).reshape(-1, 96 * 54) if lum else np.zeros((0, 1))
    res = dict(file=str(path), w=v["width"], h=v["height"], fps=v["r_frame_rate"], vcodec=v["codec_name"], profile=v.get("profile"),
               acodec=a["codec_name"], ar=a["sample_rate"], duration=dur, size_mb=round(size / 1e6, 2), lufs=I, true_peak=TP,
               mean_luma=[int(x) for x in fr.mean(1)] if len(fr) else [], music=Pn["music"])
    problems = []
    if (v["width"], v["height"]) != (1080, 1920):
        problems.append("resolution")
    if abs(dur - Pn["duration"]) > 0.6:
        problems.append(f"duration {dur} != {Pn['duration']}")
    if not (-16.5 <= I <= -12.0):
        problems.append(f"loudness {I}")
    if TP > -0.5:
        problems.append(f"true peak {TP}")
    if size > 95e6:
        problems.append("file too large")
    if len(fr) and (fr.mean(1) < 6).sum() > 2 + Pn["duration"] * 0.15:
        problems.append("too many black frames")
    res["problems"] = problems
    return res


def render_story(S, style, out_mp4, build=None, media_root=None, workers="auto"):
    self_test_gate()
    build = Path(build or os.getenv("KAHWA_BUILD", "/tmp/kahwa-reel"))
    out, Pn, plate_t = prep(S, style, build, media_root or build / "media")
    silent, hf_s = render(out, workers=workers)
    res = finish(out, Pn, out_mp4)
    res.update(style=style, hyperframes_render_s=hf_s, plate_render_s=plate_t, plates=len(Pn["plates"]),
               transitions=len(Pn["transitions"]))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", required=True)
    ap.add_argument("--style", choices=["A", "B"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--media-root")
    ap.add_argument("--build")
    ap.add_argument("--workers", default="auto")
    a = ap.parse_args()
    S = json.loads(Path(a.story).read_text())
    res = render_story(S, a.style, a.out, build=a.build, media_root=a.media_root or str(Path(a.story).parent), workers=a.workers)
    print(json.dumps(res, ensure_ascii=False))
    return 1 if res["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
