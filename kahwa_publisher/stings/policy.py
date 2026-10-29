"""Concept B sting rules (owner-approved package 2026-10-29, research-opener-b-uses-2026-10-29.md).

- Edition sting «العالم في 30 ثانية» (3.56 s): opens every multi-headline reel (edition reel), at most EDITION_DAILY_MAX a day.
- Alert sting «عاجل» (3.05 s): only in front of a TOP-TIER story (writer flagged urgent, confirmed by >= 2 outlets, at least
  one of them a tier-1 outlet), at most ALERT_DAILY_MAX per day (Algeria calendar day) and ALERT_WEEKLY_MAX per rolling 7 days.
- Never back-to-back with an edition reel: no alert sting when the previous reel was an edition reel less than
  BACK_TO_BACK_H hours ago, and an edition slot is skipped when the previous reel was an alert less than BACK_TO_BACK_H ago.
- Counts live in state/stings.json (bot-owned; written only by scheduled, non-dry runs).
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STATE = ROOT / "state" / "stings.json"
ALERT_DAILY_MAX = 1
ALERT_WEEKLY_MAX = 3
EDITION_DAILY_MAX = 4
BACK_TO_BACK_H = 3.0
TIER1 = {"BBC عربي", "فرانس 24", "DW عربية", "أخبار الأمم المتحدة"}
ALGERIA = timezone(timedelta(hours=1))


def load(path=None):
    p = Path(path or STATE)
    try:
        d = json.loads(p.read_text())
        if isinstance(d.get("events"), list):
            return d
    except Exception:
        pass
    return {"events": []}


def save(state, path=None):
    p = Path(path or STATE)
    cutoff = time.time() - 14 * 86400
    state["events"] = [e for e in state.get("events", []) if e.get("t", 0) > cutoff]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _day(t):
    return datetime.fromtimestamp(t, ALGERIA).date()


def counts(state, now=None):
    now = now or time.time()
    ev = state.get("events", [])
    today = _day(now)
    alerts = [e for e in ev if e.get("kind") == "alert"]
    return {
        "alert_today": sum(1 for e in alerts if _day(e["t"]) == today),
        "alert_7d": sum(1 for e in alerts if e["t"] > now - 7 * 86400),
        "edition_today": sum(1 for e in ev if e.get("kind") == "edition" and _day(e["t"]) == today),
    }


def last_reel(state):
    ev = sorted(state.get("events", []), key=lambda e: e.get("t", 0))
    return ev[-1] if ev else None


def is_top_tier(story_urgent, outlets):
    outlets = list(outlets or [])
    return bool(story_urgent) and len(set(outlets)) >= 2 and any(o in TIER1 for o in outlets)


def alert_allowed(state, story_urgent, outlets, now=None):
    """(ok, reason). ok=True -> the «عاجل» sting may open this reel."""
    now = now or time.time()
    if not is_top_tier(story_urgent, outlets):
        return False, "not top-tier (needs urgent + >=2 outlets incl. a tier-1 outlet)"
    c = counts(state, now)
    if c["alert_today"] >= ALERT_DAILY_MAX:
        return False, f"alert cap: {c['alert_today']}/{ALERT_DAILY_MAX} today"
    if c["alert_7d"] >= ALERT_WEEKLY_MAX:
        return False, f"alert cap: {c['alert_7d']}/{ALERT_WEEKLY_MAX} in 7 days"
    lr = last_reel(state)
    if lr and lr.get("kind") == "edition" and now - lr["t"] < BACK_TO_BACK_H * 3600:
        return False, "previous reel was an edition reel (no back-to-back stings)"
    return True, "ok"


def edition_allowed(state, now=None):
    now = now or time.time()
    c = counts(state, now)
    if c["edition_today"] >= EDITION_DAILY_MAX:
        return False, f"edition cap: {c['edition_today']}/{EDITION_DAILY_MAX} today"
    lr = last_reel(state)
    if lr and lr.get("kind") == "alert" and now - lr["t"] < BACK_TO_BACK_H * 3600:
        return False, "previous reel was an alert reel (no back-to-back stings)"
    return True, "ok"


def record(state, kind, rid, now=None):
    """kind: 'alert' | 'edition' | 'reel' (a reel without a sting; it breaks a back-to-back sequence)."""
    state.setdefault("events", []).append({"t": now or time.time(), "kind": kind, "id": rid})
    return state
