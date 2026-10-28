"""Add a post to the queue. Used by the news pipeline (or manually).

python -m kahwa_publisher.enqueue --id <unique> --text "..." [--image URL | --video URL] [--reel] [--priority 1-9]
"""
import argparse
import json
import re
from datetime import datetime, timezone

from .dispatcher import QUEUE

URL_RE = re.compile(r"https?://\S+")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--id", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--image")
    a.add_argument("--video")
    a.add_argument("--reel", action="store_true")
    a.add_argument("--title")
    a.add_argument("--priority", type=int, default=5)
    a.add_argument("--not-before")
    x = a.parse_args()
    text = URL_RE.sub("", x.text).strip()  # rule: no links in posts (Meta link cap)
    media = []
    if x.image:
        media.append({"type": "image", "url": x.image})
    if x.video:
        media.append({"type": "video", "url": x.video})
    post = {"id": re.sub(r"[^A-Za-z0-9_-]", "-", x.id)[:80], "text": text, "media": media,
            "kind": "reel" if x.reel else "post", "title": x.title, "priority": x.priority,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"), "not_before": x.not_before}
    QUEUE.mkdir(exist_ok=True)
    out = QUEUE / f"{post['created'][:19].replace(':', '')}_{post['id']}.json"
    out.write_text(json.dumps(post, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main()
