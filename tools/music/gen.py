#!/usr/bin/env python3
"""One-off: render Kahwa News' own instrumental library with ACE-Step 1.5 (MIT; outputs usable commercially).

python tools/music/gen.py --ace ace --shard 0 --of 6 --out out
Each track: ace-lm (1.7B, audio codes) -> ace-synth (XL turbo DiT + VAE, WAV 48 kHz) -> ace-understand (vocal check).
"""
import argparse, json, subprocess, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--ace", default="ace"); ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--of", type=int, default=1); ap.add_argument("--out", default="out")
ap.add_argument("--dit", default="")
a = ap.parse_args()
cfg = json.loads((Path(__file__).parent / "prompts.json").read_text())
if a.dit:
    cfg["model"]["synth_model"] = a.dit
ace = Path(a.ace).resolve(); out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
models = str(ace / "models"); b = ace / "build"
tracks = [t for i, t in enumerate(cfg["tracks"]) if i % a.of == a.shard]
report = []
for t in tracks:
    d = out / t["id"]; d.mkdir(exist_ok=True)
    req = dict(cfg["model"], caption=t["caption"] + cfg["common_caption_tail"], lyrics="[Instrumental]", bpm=t["bpm"],
               duration=t["duration"], keyscale=t["key"], timesignature="4", vocal_language="unknown", seed=t["seed"],
               output_format="wav16")
    rp = d / "req.json"; rp.write_text(json.dumps(req, indent=1))
    rec = dict(t, request=req)
    try:
        t0 = time.time()
        subprocess.run([str(b / "ace-lm"), "--models", models, "--request", str(rp)], check=True, timeout=3600,
                       stdout=open(d / "lm.log", "w"), stderr=subprocess.STDOUT)
        t1 = time.time()
        subprocess.run([str(b / "ace-synth"), "--models", models, "--request", str(d / "req0.json")], check=True, timeout=3600,
                       stdout=open(d / "synth.log", "w"), stderr=subprocess.STDOUT)
        t2 = time.time()
        wav = next(d.glob("req0*.wav"))
        und = {"lm_model": cfg["model"]["lm_model"], "synth_model": cfg["model"]["synth_model"]}
        (d / "und.json").write_text(json.dumps(und))
        r = subprocess.run([str(b / "ace-understand"), "--models", models, "--src-audio", str(wav), "--request", str(d / "und.json"),
                            "-o", str(d / "understand.json")], timeout=3600, stdout=open(d / "und.log", "w"), stderr=subprocess.STDOUT)
        t3 = time.time()
        rec.update(ok=True, wav=wav.name, lm_s=round(t1 - t0), synth_s=round(t2 - t1), understand_s=round(t3 - t2),
                   understand_rc=r.returncode)
        try:
            u = json.loads((d / "understand.json").read_text())
            rec["understand"] = {k: u.get(k) for k in ("caption", "lyrics", "bpm", "keyscale", "vocal_language")}
        except Exception as ex:
            rec["understand_err"] = str(ex)[:200]
    except Exception as ex:
        rec.update(ok=False, err=str(ex)[:300])
    report.append(rec)
    print(json.dumps({k: rec.get(k) for k in ("id", "ok", "lm_s", "synth_s", "understand_s", "err")}), flush=True)
(out / f"report-{a.shard}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
