"""Graceful degradation + idempotent slots (self-healing pass, 2026-10-31; research §2.4 / §2.5).

1. Degradation ladder: when a video (reel / bulletin) fails AFTER its text was verified, publish a valid card post built from
   the SAME verified lines instead of nothing. Never a broken render, never new unverified text: the card only re-uses lines
   that already passed claim verification and the screen/banned-name gates. Skipped when the story is already covered by a
   queued/published post (no duplicate story on the Page).
2. Idempotent slots: every scheduled product has a slot key (bulletin-YYYY-MM-DD, reel-YYYY-MM-DD-HH, ...). A run whose slot
   already has a queue item (queued, done or failed) exits early, so a duplicated trigger (GitHub schedule + a future
   external cron, or a re-run) can never queue the same slot twice.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import attribution as A

ROOT = Path(os.getenv("KAHWA_ROOT", Path(__file__).resolve().parent.parent))
QUEUE = ROOT / "queue"
REPO = os.getenv("GITHUB_REPOSITORY", "kahouanews/kahwa-news-publisher")
BRANCH = os.getenv("GITHUB_REF_NAME", "main") or "main"


def plain(s):
    return re.sub(r"\[\[|\]\]", "", str(s or "")).strip()


# ------------------------------------------------------------------ slots
def slot_key(kind, now=None, slot=None):
    """slot: explicit ISO time from a dispatcher input (external trigger) wins; else the run's own UTC time.
    bulletin -> one per day; reel / edition -> one per (day, UTC hour of the slot)."""
    t = now or datetime.now(timezone.utc)
    if slot:
        try:
            t = datetime.fromisoformat(slot.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    if kind == "bulletin":
        return f"bulletin-{t:%Y-%m-%d}"
    return f"{kind}-{t:%Y-%m-%d-%H}"


def _items():
    for f in list(QUEUE.glob("*.json")) + list((QUEUE / "done").glob("*.json")) + list((QUEUE / "failed").glob("*.json")):
        try:
            yield json.loads(f.read_text())
        except Exception:
            continue


def slot_done(key):
    return any(it.get("slot") == key for it in _items())


def story_covered(key):
    """A post / reel / card for this story cluster is already queued or published (ids end with the cluster key)."""
    return bool(key) and any(str(it.get("id", "")).split("-", 1)[-1].startswith(key) for it in _items())


# ------------------------------------------------------------------ card fallback
def card_from_reel(S, out_dir, sid):
    """Facts card from a verified reel story S (headline + up to 4 verified fact lines + primary sources)."""
    facts = [plain(f.get("text")) for f in (S.get("facts") or []) if plain(f.get("text"))][:4]
    src = "، ".join((S.get("sources") or [A.GENERIC])[:3])
    p = {"card_type": "facts" if len(facts) >= 3 else "map", "headline": plain(S.get("headline")), "title": plain(S.get("headline")),
         "kicker": plain(S.get("topic_ar") or ""), "facts": facts, "country_en": (S.get("place_terms_en") or [None])[0],
         "source": src, "no_photo": True}
    text = "\n".join([plain(S.get("headline")), plain(S.get("deck")), ""] + [f"- {x}" for x in facts] + ["", "المصدر: " + src])
    return _card(p, text, out_dir, sid, title=plain(S.get("headline")))


def card_from_bulletin(B, spec, caption, out_dir, sid):
    """«قهوة اليوم» headlines card: the bulletin's verified headlines (already through every gate) as a facts card."""
    hls = [plain(s["text"]) for s in B["scenes"] if s["type"] == "headline"][:5]
    p = {"card_type": "facts", "headline": "قهوة اليوم", "title": "أبرز العناوين", "kicker": B.get("date", ""), "facts": hls,
         "source": A.GENERIC, "no_photo": True}
    return _card(p, caption, out_dir, sid, title=f"قهوة اليوم {B.get('date', '')}")


def _card(p, text, out_dir, sid, title):
    from .cards import render_card
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = out_dir / f"{sid}-card.jpg"
    render_card(p, img)
    now = datetime.now(timezone.utc)
    item = {"id": f"{sid}-card", "kind": "post", "text": text, "title": title[:90],
            "media": [{"type": "image", "url": f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/media/{img.name}"}],
            "priority": 6, "fallback_for": "video", "requires_publishing_enabled": True,
            "created": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "not_before": datetime.fromtimestamp(now.timestamp() + 900, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
    return item, img
