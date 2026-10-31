"""Daily newscast «قهوة اليوم» and the «العالم في 30 ثانية» edition reel, end to end.

python -m kahwa_publisher.bulletin.make --out out [--dry] [--fixture tests/fixtures/bulletin-2026-10-29.json] [--mode bulletin|edition]

Safety: while PUBLISHING_ENABLED != "true" (or --dry / a fixture) the run renders + QA's only: the MP4, rundown, key frames and
QA reports go to --out (workflow artifact); nothing is queued, state/ is not written. A queue item is written only on a
scheduled run with publishing enabled and every gate green (headlines gate, screen gate, DOM safe-zone QA, file QA,
frame #0 = opener). A fixture run is never queued.
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import attribution as A
from .. import llm as R
from ..stings import policy as SP
from . import render as RN
from . import rundown as RD

REPO = os.getenv("GITHUB_REPOSITORY", "kahouanews/kahwa-news-publisher")
MEDIA_BASE = os.getenv("REELS_MEDIA_BASE", "https://{o}.github.io/{r}/").format(o=REPO.split("/")[0], r=REPO.split("/")[1])
BEAT = 60.0 / 118.0
HEAD_BEATS = {"A": 20, "E": 7, "X": 6}       # cut on the bar line (pilot: A = 20 beats -> 305 frames)
EDITION_MAX_S = 60.0


def log(o):
    print(json.dumps(o, ensure_ascii=False)[:3000], flush=True)


def caption(B, spec, mode):
    hls = [s["text"].replace("[[", "").replace("]]", "") for s in B["scenes"] if s["type"] == "headline"]
    srcs = []
    for g in spec["segments"]:
        for s in g.get("shots", []):
            t = (s.get("src") or "").replace("المصدر:", "").strip()
            srcs += [x.strip() for x in t.split("·") if x.strip()]
    srcs = [s for s in dict.fromkeys(srcs) if not A.is_outlet(s)][:4] or [A.GENERIC]
    head = f"قهوة اليوم | {B['date']}" if mode == "bulletin" else f"العالم في 30 ثانية | {B['date']}"
    return "\n".join([head, ""] + [f"{i}. {h}" for i, h in enumerate(hls, 1)] + ["", "المصدر: " + "، ".join(srcs)])


def contact_sheet(paths, out, cols=4):
    from PIL import Image
    ims = [Image.open(p).resize((270, 480)) for p in paths]
    rows = (len(ims) + cols - 1) // cols
    W = Image.new("RGB", (270 * cols, 480 * rows), (0, 0, 0))
    for i, im in enumerate(ims):
        W.paste(im, ((i % cols) * 270, (i // cols) * 480))
    W.save(out, quality=82)


def frame_diff(a, b):
    import numpy as np
    from PIL import Image
    x = np.asarray(Image.open(a).convert("L").resize((108, 192)), np.float32)
    y = np.asarray(Image.open(b).convert("L").resize((108, 192)), np.float32)
    return float(abs(x - y).mean())


SAFETY_FLAGS = {"BANNED", "PSE", "PAIR"}      # data / content rules: never auto-healed, always fail closed
MAX_HEAL_ROUNDS = 7


def scene_at(B, t):
    for sc in B["scenes"]:
        if sc["t"] - 0.01 <= t < sc["t"] + sc["d"] + 0.01:
            return sc
    return None


def run_dom_qa(comp, times, out, tag):
    dq = {"violations": [], "console_errors": [], "map_labels": [], "checked": 0}
    for i in range(0, len(times), 40):
        r = RN.dom_qa(comp, times[i:i + 40], out / f"qa_dom_{tag}_{i // 40}.json")
        for k in ("violations", "console_errors"):
            dq[k] += r[k]
        dq["map_labels"] = sorted(set(dq["map_labels"]) | set(r["map_labels"]))
        dq["checked"] += r["checked"]
    return dq


def _seg_of(sid):
    import re as _re
    m = _re.fullmatch(r"(?:hl|b|s)(\d+)[a-z]?", sid)
    return int(m.group(1)) if m else sid


def drop_for(spec, sid, mode):
    """Graceful degradation once a scene is still broken at the top fit level: drop the smallest unit that removes it while the
    rundown stays valid (a shot if its segment keeps >= 2, else the segment if enough remain, else the «باختصار» list).
    Returns a description, or None when nothing can be dropped (the run then fails closed)."""
    import re as _re
    segs = spec["segments"]
    min_main = RD.MIN_MAIN if mode == "bulletin" else 2
    m = _re.fullmatch(r"s(\d+)([a-z])", sid)
    if m:
        si, j = int(m.group(1)) - 1, ord(m.group(2)) - 97
        if si < len(segs) and len(segs[si].get("shots", [])) > 2 and j < len(segs[si]["shots"]):
            segs[si]["shots"].pop(j)
            return f"dropped shot {sid}"
    if sid == "brief" or (sid == f"b{len(segs) + 1}" and spec.get("brief")):
        spec["brief"] = []
        return "dropped «باختصار»"
    m = _re.fullmatch(r"(?:hl|b|s)(\d+)[a-z]?", sid)
    if m and sid != "hl0":
        si = int(m.group(1)) - 1
        if si < len(segs) and len(segs) > min_main:
            k = segs.pop(si).get("key")
            if spec.get("pings_ll") and si < len(spec["pings_ll"]):
                spec["pings_ll"].pop(si)
            return f"dropped segment {k}"
    return None


def heal_layout(spec, mode, build, media_dir, out):
    """Build + DOM QA, then heal what QA finds instead of failing the run (2026-10-31: 4 of 6 bulletin failures in 14 days were
    one long generated line overlapping / leaving the safe zone). Per broken scene: fit level 1 -> 2 -> 3 (smaller type, level 3
    also hides map pills), re-checked by the same QA on that scene; still broken -> drop that shot / segment / brief and re-check
    everything. Banned-name / map-rule hits are never healed (fail closed). A DOM QA crash or console error is retried once."""
    heal, fit = [], dict(spec.get("_fit") or {})
    full = True
    B = comp = dq = None
    retried_console = False
    for rnd in range(MAX_HEAL_ROUNDS):
        spec["_fit"] = fit
        B = RD.build(spec, mode=mode)
        comp = RN.build_body(B, build / "comp-body", media_dir)
        allt = RN.qa_times(B)
        if full or dq is None:
            times = allt
        else:
            ids = {h for h in pending}
            times = [t for t in allt if (scene_at(B, t) or {}).get("id") in ids] or allt
        try:
            dq_r = run_dom_qa(comp, times, out, f"r{rnd}")
        except RuntimeError as ex:                      # puppeteer navigation timeout etc. (run 40945212474): one retry
            if "crashed" in str(ex) and rnd == 0:
                heal.append({"round": rnd, "retry": "dom qa crashed", "why": str(ex)[:200]})
                full = True
                continue
            raise
        if full:
            dq = dq_r
        else:                                           # merge: re-checked scenes replace their old results
            keep = [v for v in dq["violations"] if (scene_at(B, v["t"]) or {}).get("id") not in ids]
            dq = dict(dq_r, violations=keep + dq_r["violations"], checked=dq["checked"])
        bad_safety = [v for v in dq["violations"] if SAFETY_FLAGS & set(v.get("v", []))]
        if bad_safety:
            raise RuntimeError(f"DOM QA failed (content rule, not healable): {bad_safety[:5]}")
        if dq["console_errors"]:
            if not retried_console:
                retried_console, full = True, True
                heal.append({"round": rnd, "retry": "console errors", "errors": dq["console_errors"][:3]})
                continue
            raise RuntimeError(f"DOM QA failed: console errors {dq['console_errors'][:3]}")
        if not dq["violations"]:
            log({"dom_qa_heal": heal or "clean"})
            return B, comp, dq, heal
        pending, full, dropped = set(), False, []
        for v in dq["violations"]:
            sc = scene_at(B, v["t"])
            if not sc:
                raise RuntimeError(f"DOM QA failed outside any scene: {v}")
            pending.add(sc["id"])
        for sid in sorted(pending):
            lv = int(fit.get(sid, 0))
            if lv < 3:
                fit[sid] = lv + 1
                heal.append({"round": rnd, "scene": sid, "fit": lv + 1})
            else:
                what = drop_for(spec, sid, mode)
                if not what:
                    raise RuntimeError(f"DOM QA failed and could not be healed: {[v for v in dq['violations'] if (scene_at(B, v['t']) or {}).get('id') == sid][:3]}")
                heal.append({"round": rnd, "scene": sid, "action": what})
                dropped.append(what)
        if dropped:                                     # timings shift: re-check everything; ids shift only after the drop
            if any(d.startswith("dropped segment") for d in dropped):
                fit = {}
            else:                                       # a shot / the brief: keep fits of untouched segments
                segs_hit = {_seg_of(sid) for sid in pending if int(fit.get(sid, 0)) >= 3}
                fit = {k: v for k, v in fit.items() if _seg_of(k) not in segs_hit and k != "brief"}
            full = True
        log({"dom_qa_heal_round": rnd, "violations": len(dq["violations"]), "scenes": sorted(pending)})
    raise RuntimeError(f"DOM QA failed after {MAX_HEAL_ROUNDS} heal rounds: {dq['violations'][:5]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--fixture", help="saved rundown spec (render test: no news fetch, no LLM, never queued)")
    ap.add_argument("--mode", choices=["bulletin", "edition"], default="bulletin")
    ap.add_argument("--workers", default=os.getenv("HF_WORKERS", "auto"))
    ap.add_argument("--no-articles", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    build = Path(os.getenv("KAHWA_BUILD", "/tmp/kahwa-bulletin")) / a.mode
    build.mkdir(parents=True, exist_ok=True)
    publishing = os.getenv("PUBLISHING_ENABLED", "false").lower() == "true"
    dry = a.dry or not publishing or bool(a.fixture)
    now = datetime.now(timezone.utc)
    summary = {"mode": a.mode, "dry": dry, "publishing_enabled": publishing, "fixture": a.fixture or None}
    t0 = time.time()
    from .. import fallback as FB
    slot = FB.slot_key(a.mode, slot=os.getenv("KAHWA_SLOT"))
    spec = B = None

    def done(code, result):
        summary["result"] = result
        summary["t_total_s"] = round(time.time() - t0, 1)
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        if summary.get("error"):                 # own line: the summary line is cut at 3000 chars (ci_doctor reads this)
            log({"run_error": summary["error"]})
        log({"summary": summary})
        return code

    try:
        if not dry and not a.fixture and FB.slot_done(slot):   # idempotent: duplicated trigger / re-run never queues twice
            return done(0, f"skipped: slot {slot} already done")
        st = SP.load()
        if a.mode == "edition" and not a.fixture:
            ok, why = SP.edition_allowed(st, now.timestamp())
            summary["edition_policy"] = why
            if not ok:
                return done(0, f"skipped: {why}")
        # ---------------------------------------------------------- spec
        if a.fixture:
            fx = Path(a.fixture)
            spec = json.loads(fx.read_text())
            media_dir = fx.parent / spec.get("media_dir", ".")
            if a.mode == "edition":
                spec = dict(spec, segments=[dict(g, shots=[]) for g in spec["segments"][:3]])
        else:
            ok, why = R.preflight()
            log({"llm_preflight": ok, "detail": why})
            if not ok:
                return done(0, f"held: {why}")
            from . import writer as W
            spec, rep = W.build_spec(now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), mode=a.mode, fetch_articles=not a.no_articles)
            summary["stories"] = rep
            media_dir = build
        # ---------------------------------------------------------- rundown + body composition + self-healing DOM QA
        B, comp, dq, heal = heal_layout(spec, a.mode, build, media_dir, out)
        summary["headlines"] = [s["text"] for s in B["scenes"] if s["type"] == "headline"]
        summary["segments"] = [g["t"] for g in B["segs"]]
        summary["body_duration"] = B["duration"]
        summary["dom_qa"] = {"checked": dq["checked"], "violations": dq["violations"][:20], "console_errors": dq["console_errors"][:5],
                             "map_labels": dq["map_labels"], "heal": heal}
        (out / "rundown.json").write_text(json.dumps(B, ensure_ascii=False, indent=1))
        (out / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
        # ---------------------------------------------------------- head (opener A / edition sting E) + body render
        hhmm = spec.get("asof") or now.strftime("%H:%M")
        head_mp4 = build / "head.mp4"
        if a.mode == "bulletin":
            summary["t_opener_s"], mixw = RN.opener_A(head_mp4, build / "opener", spec["date"], hhmm, a.workers)
            concept = "A"
        else:
            summary["t_sting_s"], mixw = RN.sting("E", head_mp4, build / "sting", hhmm, a.workers, spec["date"],
                                                  pings_ll=(spec.get("pings_ll") or [])[:len(spec["segments"])] or None,
                                                  n_stories=len(spec["segments"]))
            concept = "E"
        body_silent = build / "body_silent.mp4"
        summary["t_body_render_s"] = RN.hf_render(comp, body_silent, workers=a.workers)
        # ---------------------------------------------------------- audio + bridge + assemble
        body_wav = RN.mix_body(B, build / "body.wav")
        hs, hw, n = RN.prep_head(head_mp4, mixw, build)
        n_head = min(n, round(HEAD_BEATS[concept] * BEAT * 30))
        br = RN.bridge(hs, body_silent, build / "bridge.mp4", build)
        sid = now.strftime("%Y%m%d%H%M") + ("-qahwat-alyawm" if a.mode == "bulletin" else "-edition")
        name = f"{sid}-bulletin.mp4" if a.mode == "bulletin" else f"{sid}-reel.mp4"
        final = build / name
        summary["assemble"] = RN.assemble(hs, hw, n_head, body_silent, body_wav, br, final, build,
                                          head_over_body_lu=3.2 if a.mode == "bulletin" else 2.0,
                                          fade_end=0.8 if a.mode == "edition" else 0.0)
        from ..reels import audio as AU
        _, _, tp_fix = AU.fix_true_peak(final, limit=-1.0, target=-1.2)   # self-heal AAC overshoot (never fails a run on it)
        if tp_fix:
            summary["tp_fix"] = tp_fix
        expect = n_head / 30 + B["duration"]
        q = RN.qa_file(final, expect_dur=expect, max_dur=EDITION_MAX_S if a.mode == "edition" else None)
        # frame #0 must be the head's frame #0 (opener / sting first), never a story
        kd = out / "keyframes"
        f0 = RN.frames_at(final, [0], kd, "frame0")[0]
        RN.sh(["ffmpeg", "-v", "error", "-y", "-i", hs, "-frames:v", "1", "-q:v", "3", build / "head0.jpg"])
        q["frame0_vs_head_diff"] = round(frame_diff(f0, build / "head0.jpg"), 2)
        if q["frame0_vs_head_diff"] > 6:
            q["problems"].append("frame #0 is not the opener")
        T_H = n_head / 30
        hl_times = [round(T_H + s["t"] + s["d"] * 0.7, 2) for s in B["scenes"] if s["type"] == "headline"]
        beats = [0.5, 3.0, 6.5, max(0.1, T_H - 0.3)] if a.mode == "bulletin" else [0.5, 1.5, max(0.1, T_H - 0.3)]
        other = [round(T_H + s["t"] + s["d"] * 0.6, 2) for s in B["scenes"] if s["type"] in ("bumper", "list", "end", "map", "number")][:14]
        kfs = RN.frames_at(final, sorted(set(beats + hl_times + other)), kd, "kf")
        contact_sheet([f0] + kfs[:23], out / "contact.jpg")
        summary["keyframes_headlines_t"] = hl_times
        summary["qa"] = q
        if q["problems"]:
            raise RuntimeError(f"file QA failed: {q['problems']}")
        import shutil
        shutil.copy(final, out / name)
        # ---------------------------------------------------------- queue (scheduled + publishing enabled only)
        if not dry:
            url = MEDIA_BASE + name
            item = {"id": sid + ("-bulletin" if a.mode == "bulletin" else "-reel"), "kind": "video" if a.mode == "bulletin" else "reel",
                    "text": caption(B, spec, a.mode), "title": ("قهوة اليوم " if a.mode == "bulletin" else "العالم في 30 ثانية ") + B["date"],
                    "media": [{"type": "video", "url": url}], "priority": 5, "series": "قهوة اليوم" if a.mode == "bulletin" else "العالم في 30 ثانية",
                    "requires_publishing_enabled": True,
                    "verified_by_outlets": [r.get("outlets") for r in summary.get("stories", []) if r.get("ok")],   # internal only
                    "created": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "not_before": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00"), "slot": slot}
            (out / "queue-item.json").write_text(json.dumps(item, ensure_ascii=False, indent=1))
            if a.mode == "edition":
                SP.record(st, "edition", item["id"])
                SP.save(st)
            summary["queued_as"] = item["id"]
        summary["caption"] = caption(B, spec, a.mode)
        return done(0, "ok (dry run: not queued)" if dry else "ok")
    except R.Hold as ex:
        return done(0, f"held: {str(ex)[:400]}")
    except RD.RundownError as ex:
        if "only" in str(ex) and "verified stories" in str(ex):
            return done(0, f"skipped: {ex}")              # editorial fail-closed: not enough verified stories today
        summary["error"] = f"RundownError: {ex}"
        (out / "error.txt").write_text(f"Kahwa {a.mode} failed and was skipped (nothing was posted).\n\n{ex}\n\n{traceback.format_exc()[-3000:]}")
        return done(1, "failed")
    except Exception as ex:
        summary["error"] = f"{type(ex).__name__}: {str(ex)[:1500]}"
        (out / "error.txt").write_text(f"Kahwa {a.mode} failed and was skipped (nothing was posted).\n\n{summary['error']}\n\n{traceback.format_exc()[-3000:]}")
        # degradation ladder (bulletin only): the verified headlines as a card post instead of nothing. Never after a
        # content-rule failure; the rundown is rebuilt so every gate (headlines, screen/banned names) runs again on it.
        if a.mode == "bulletin" and spec is not None and not a.fixture and "not healable" not in summary["error"]:
            try:
                Bf = B or RD.build(spec, mode=a.mode)
                sid = now.strftime("%Y%m%d%H%M") + "-qahwat-alyawm"
                item, img = FB.card_from_bulletin(Bf, spec, caption(Bf, spec, a.mode), out, sid)
                item["slot"] = slot
                summary["fallback"] = {"card": img.name, "queued": not dry}
                if not dry:
                    (out / "fallback-item.json").write_text(json.dumps(item, ensure_ascii=False, indent=1))
                (out / "degraded.txt").write_text(f"Kahwa bulletin degraded (video failed, headlines card used).\n\n{summary['error']}\n")
                return done(0, "degraded: video failed, card fallback")
            except Exception as ex2:
                summary["fallback_error"] = f"{type(ex2).__name__}: {str(ex2)[:300]}"
        return done(1, "failed")


if __name__ == "__main__":
    sys.exit(main())
