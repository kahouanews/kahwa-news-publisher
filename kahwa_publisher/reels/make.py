"""Make one reel end to end (run by .github/workflows/reel.yml at the reel slots).

pick the top verified story (>=2 outlets, legal/sports/banned filters, no war stories) -> LLM story.json with
digit check -> openly licensed footage/photos (Wikimedia Commons, NASA) or graphic-only -> style B if urgent else A
-> HyperFrames render + own music -> hard QA -> out/<id>-reel.mp4 + out/queue-item.json (the workflow uploads the MP4
to the `reels-media` branch first and only then moves the queue item into queue/).

Dry run (--dry, or automatically while PUBLISHING_ENABLED != "true"): render + QA only, nothing is queued.

python -m kahwa_publisher.reels.make --out out [--dry] [--story story.json --style A]
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import media as M
from .render import gate, render_story
from .story import write_story

REPO = os.getenv("GITHUB_REPOSITORY", "kahouanews/kahwa-news-publisher")
MEDIA_BRANCH = os.getenv("REELS_MEDIA_BRANCH", "reels-media")
SCIENCE = re.compile(r"space|nasa|satellite|planet|moon|mars|rocket launch|asteroid|comet|telescope|hurricane|typhoon|cyclone|"
                     r"storm|volcano|earth|climate|glacier|ice|ocean|wildfire|flood", re.I)


def log(o):
    print(o if isinstance(o, str) else json.dumps(o, ensure_ascii=False), flush=True)


def plain(s):
    return (s or "").replace("[[", "").replace("]]", "")


def post_text(S):
    lines = [plain(S["headline"]), plain(S["deck"]), ""]
    lines += [f"- {plain(f['text'])}" for f in S["facts"]]
    lines += ["", "المصادر: " + "، ".join(S["sources"][:4])]
    if S.get("media"):
        names = list(dict.fromkeys(m["credit_short"] for m in S["media"].values()))[:4]
        lines.append("صور ولقطات أرشيفية: " + "، ".join(names))
    return "\n".join(lines).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--story", help="render an existing story.json (local tests); media files next to it")
    ap.add_argument("--style", choices=["A", "B"])
    ap.add_argument("--workers", default=os.getenv("HF_WORKERS", "auto"))
    ap.add_argument("--no-articles", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    publishing = os.getenv("PUBLISHING_ENABLED", "false").lower() == "true"
    dry = a.dry or not publishing
    t0 = time.time()
    summary = {"dry": dry, "publishing_enabled": publishing}
    try:
        build = Path(os.getenv("KAHWA_BUILD", "/tmp/kahwa-reel"))
        if a.story:
            S = json.loads(Path(a.story).read_text())
            media_root = Path(a.story).resolve().parent
            seen = c = None
        else:
            from ..newsroom import llm, mark_reeled, pick_reel_cluster
            c, seen = pick_reel_cluster()
            if not c:
                log({"skip": "no eligible story"})
                summary["result"] = "skipped: no eligible story"
                (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
                return 0
            sid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M") + "-" + c["key"]
            summary["story_titles"] = [i["title"] for i in c["items"][:4]]
            summary["outlets"] = c["sources"]
            S, _src = write_story(llm, c["items"], c["sources"], sid, fetch_articles=not a.no_articles, log=log)
            summary["t_story_s"] = round(time.time() - t0, 1)
            style0 = "B" if S.get("urgent") else "A"
            media_root = build / "media"
            qs = S.get("footage_queries") or []
            got, rejected = M.gather(qs, media_root, want=10 if style0 == "A" else 5, story_date=datetime.now(timezone.utc),
                                     use_nasa=bool(SCIENCE.search(" ".join(qs))), log=log,
                                     places=S.get("place_terms_en"))
            S["media"], S["media_rejected"] = got, rejected[:40]
            summary["media"] = {k: {"page": v["page"], "license": v["license"], "author": v["author"], "archive": v["archive"]}
                                for k, v in got.items()}
            summary["media_rejected_n"] = len(rejected)
            summary["t_media_s"] = round(time.time() - t0, 1)
        style = a.style or ("B" if S.get("urgent") else "A")
        gate(S)
        (out / "story.json").write_text(json.dumps(S, ensure_ascii=False, indent=1))
        mp4 = out / f"{S['id']}-reel.mp4"
        res = render_story(S, style, mp4, build=build, media_root=media_root, workers=a.workers)
        summary.update(style=style, qa=res, t_total_s=round(time.time() - t0, 1))
        if res["problems"]:
            raise RuntimeError(f"QA failed, reel NOT queued: {res['problems']}")
        if not dry and c is not None:
            url = f"https://raw.githubusercontent.com/{REPO}/{MEDIA_BRANCH}/{mp4.name}"
            now = datetime.now(timezone.utc)
            item = {"id": S["id"] + "-reel", "kind": "reel", "text": post_text(S), "title": plain(S["headline"])[:90],
                    "media": [{"type": "video", "url": url}], "priority": 4 if style == "B" else 6, "sources": S["sources"],
                    "created": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "not_before": (now + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%S+00:00"), "style": style}
            (out / "queue-item.json").write_text(json.dumps(item, ensure_ascii=False, indent=1))
            mark_reeled(seen, c)
            summary["queued_as"] = item["id"]
        summary["result"] = "ok (dry run: not queued)" if dry else "ok"
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        log(summary)
        return 0
    except Exception as ex:
        summary["result"] = "failed"
        summary["error"] = f"{type(ex).__name__}: {str(ex)[:1500]}"
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        (out / "error.txt").write_text(
            "Kahwa reel failed and was skipped (nothing was posted).\n\n" + summary["error"] + "\n\n" + traceback.format_exc()[-3000:])
        log(summary)
        return 1


if __name__ == "__main__":
    sys.exit(main())
