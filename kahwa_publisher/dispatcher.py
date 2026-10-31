"""Kahwa News publisher: one run = publish at most one due post, with automatic failover.

Safety rules (from the user's hard requirements):
- Facebook must never be pushed into an identity check: natural cadence (min gap + random jitter,
  daily cap), and on any Facebook block (error 368 / spam / checkpoint) pause ALL providers for
  48h with no automatic retry, and alert.
- Never publish the same post twice: an UNCERTAIN result stops failover for that post and is
  re-checked on the next run.
- Every failure is logged in state/state.json and raised as an alert (GitHub issue + failed job email).
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import providers as P

ROOT = Path(os.getenv("KAHWA_ROOT", Path(__file__).resolve().parent.parent))
QUEUE = ROOT / "queue"
DONE = ROOT / "queue" / "done"
FAILED = ROOT / "queue" / "failed"
STATE = ROOT / "state" / "state.json"

MIN_GAP_MIN = int(os.getenv("MIN_GAP_MINUTES", "75"))   # research 2026-10-28: 75-90 min spacing
JITTER_MIN = int(os.getenv("JITTER_MINUTES", "15"))
DAILY_CAP = min(10, int(os.getenv("DAILY_CAP", "10")))  # hard ceiling 10/day
RAMP_CAP, RAMP_DAYS = int(os.getenv("RAMP_CAP", "6")), int(os.getenv("RAMP_DAYS", "14"))  # weeks 1-2: <=6/day
REEL_DAILY_CAP = int(os.getenv("REEL_DAILY_CAP", "2"))
FB_BLOCK_PAUSE_H = int(os.getenv("FB_BLOCK_PAUSE_HOURS", "48"))
AUTH_COOLDOWN_H = int(os.getenv("AUTH_COOLDOWN_HOURS", "12"))
TRANSIENT_COOLDOWN_MIN = int(os.getenv("TRANSIENT_COOLDOWN_MINUTES", "30"))
QUIET_HOURS = os.getenv("QUIET_HOURS_UTC", "00:30-06:00")  # no posts 01:30-07:00 Algeria (UTC+1)
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "8"))


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S+00:00").replace(tzinfo=timezone.utc) if s else None


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"providers": {}, "global_pause_until": None, "next_allowed_at": None,
            "last_published_at": None, "published": [], "log": [], "alerts": []}


def save_state(st):
    st["log"] = st["log"][-300:]
    st["published"] = st["published"][-2000:]
    st["alerts"] = st["alerts"][-100:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def log(st, msg, **kw):
    entry = {"t": iso(now()), "msg": msg, **kw}
    st["log"].append(entry)
    print(json.dumps(entry, ensure_ascii=False))


def alert(st, msg):
    st["alerts"].append({"t": iso(now()), "msg": msg})
    log(st, "ALERT", detail=msg)
    (ROOT / "state").mkdir(parents=True, exist_ok=True)
    Path(ROOT / "state" / "ALERT.txt").write_text(msg + "\n", encoding="utf-8")


def in_quiet_hours(t):
    def m(x):
        h, _, mi = x.partition(":")
        return int(h) * 60 + int(mi or 0)
    a, b = (m(x) for x in QUIET_HOURS.split("-"))
    cur = t.hour * 60 + t.minute
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def cap_for(st, t):
    """Young Page ramp: RAMP_CAP/day for the first RAMP_DAYS after the first publish, then DAILY_CAP."""
    first = parse(st["published"][0]["t"]) if st.get("published") else None
    if not first or t - first < timedelta(days=RAMP_DAYS):
        return min(RAMP_CAP, DAILY_CAP)
    return DAILY_CAP


def todays_counts(st, t):
    day = t.date().isoformat()
    posts = [p for p in st["published"] if p["t"][:10] == day]
    return len(posts), sum(1 for p in posts if p.get("kind") == "reel")


def due_posts():
    QUEUE.mkdir(exist_ok=True)
    items = []
    for f in sorted(QUEUE.glob("*.json")):
        try:
            post = json.loads(f.read_text())
        except ValueError:
            continue
        nb = parse(post.get("not_before"))
        if nb and nb > now():
            continue
        items.append((post.get("priority", 5), post.get("created", ""), f, post))
    items.sort(key=lambda x: (x[0], x[1]))
    return [(f, p) for _, _, f, p in items]


def move(f, post, folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f.name).write_text(json.dumps(post, ensure_ascii=False, indent=1))
    f.unlink()


def schedule_next(st):
    gap = MIN_GAP_MIN + random.randint(0, JITTER_MIN)
    st["next_allowed_at"] = iso(now() + timedelta(minutes=gap))


def provider_ready(st, name):
    until = parse(st["providers"].get(name, {}).get("cooldown_until"))
    return not until or until <= now()


def cool(st, name, minutes, why):
    pst = st["providers"].setdefault(name, {})
    pst["cooldown_until"] = iso(now() + timedelta(minutes=minutes))
    pst["fails"] = pst.get("fails", 0) + 1
    pst["last_error"] = why[:300]


def mark_ok(st, name):
    pst = st["providers"].setdefault(name, {})
    pst.update({"fails": 0, "cooldown_until": None, "last_ok": iso(now())})


def record_published(st, f, post, res):
    post.setdefault("history", []).append({"t": iso(now()), "provider": res.provider, "status": res.status,
                                           "remote_id": res.remote_id, "detail": res.detail[:200]})
    st["published"].append({"t": iso(now()), "id": post["id"], "kind": post.get("kind", "post"),
                            "provider": res.provider, "remote_id": res.remote_id})
    st["last_published_at"] = iso(now())
    mark_ok(st, res.provider)
    move(f, post, DONE)
    schedule_next(st)
    log(st, "published", id=post["id"], provider=res.provider, remote_id=res.remote_id)


def handle_uncertain(st, provs):
    """Re-check posts whose earlier publish was uncertain. Returns True if one is still pending."""
    pending = False
    for f, post in due_posts():
        u = post.get("uncertain")
        if not u:
            continue
        prov = next((p for p in provs if p.name == u["provider"]), None)
        res = prov.check(u.get("remote_id")) if prov else P.Result(P.UNCERTAIN, u["provider"], "provider gone")
        if res.status == P.OK:
            post.pop("uncertain")
            record_published(st, f, post, res)
        elif res.status == P.UNCERTAIN:
            age = now() - parse(u["t"])
            if age > timedelta(hours=6):
                # Never auto-republish (duplicate risk); park it and alert a human.
                post.pop("uncertain")
                move(f, post, FAILED)
                alert(st, f"Post {post['id']} stayed uncertain on {u['provider']} for 6h; parked in queue/failed. Check the Page.")
            else:
                pending = True
        else:
            post.pop("uncertain")
            f.write_text(json.dumps(post, ensure_ascii=False, indent=1))
            log(st, "uncertain resolved as failure; will retry", id=post["id"], provider=res.provider)
    return pending


# Self-healing publisher order (2026-10-31): KEY_EXPIRIES = "NAME=YYYY-MM-DD ..." (repo variable, also read by the watchdog).
# A provider whose secret has expired is skipped (no failed call, no cooldown churn); one expiring within KEY_DEMOTE_DAYS is
# moved to the end so the next publisher takes over before the key dies. Prefix match: BUFFER_API_KEY -> provider "buffer".
KEY_PREFIX = {"zernio": "ZERNIO_", "buffer": "BUFFER_", "make": "MAKE_"}


def key_expiries(env=None):
    out = {}
    for kv in (env if env is not None else os.getenv("KEY_EXPIRIES", "")).split():
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                out[k.strip()] = datetime.strptime(v.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return out


def order_by_key_expiry(provs, t, env=None):
    exp = key_expiries(env)
    demote_days = int(os.getenv("KEY_DEMOTE_DAYS", "3"))
    keep, late, expired = [], [], []
    for p in provs:
        pre = KEY_PREFIX.get(p.name, p.name.upper() + "_")
        dates = [d for k, d in exp.items() if k.startswith(pre)]
        first = min(dates) if dates else None
        if first and first <= t:
            expired.append(p.name)
        elif first and first - t <= timedelta(days=demote_days):
            late.append(p)
        else:
            keep.append(p)
    return keep + late, expired


def run():
    st = load_state()
    t = now()
    if (ROOT / "state" / "PAUSE").exists() or os.getenv("PUBLISHING_ENABLED", "false").lower() != "true":
        log(st, "publishing disabled (kill switch: state/PAUSE or repo variable PUBLISHING_ENABLED!=true)")
        save_state(st)
        return 0
    gp = parse(st.get("global_pause_until"))
    if gp and gp > t:
        log(st, "global pause active (Facebook protection)", until=st["global_pause_until"])
        save_state(st)
        return 0
    provs = [p for p in P.all_providers() if p.configured()]
    provs, expired = order_by_key_expiry(provs, t)
    if expired:
        log(st, "provider key expired: skipped (automatic failover)", providers=expired)
    from . import health as H                     # watchdog's connection health check (Zernio /accounts/health)
    hh = H.load()
    down = [p.name for p in provs if H.is_open(p.name, hh)]
    if down and len(down) < len(provs):           # never skip the last one on a health verdict alone
        provs = [p for p in provs if p.name not in down]
        log(st, "provider connection down (health check): skipped (automatic failover)", providers=down)
    if not provs:
        alert(st, "No publisher is configured (missing API keys in GitHub Secrets).")
        save_state(st)
        return 1
    if handle_uncertain(st, provs):
        log(st, "a post is still uncertain; holding to avoid duplicates")
        save_state(st)
        return 0
    if in_quiet_hours(t):
        save_state(st)
        return 0
    na = parse(st.get("next_allowed_at"))
    if na and na > t:
        save_state(st)
        return 0
    total, reels = todays_counts(st, t)
    if total >= cap_for(st, t):
        save_state(st)
        return 0
    queue = [(f, p) for f, p in due_posts() if not p.get("uncertain")]
    if reels >= REEL_DAILY_CAP:
        queue = [(f, p) for f, p in queue if p.get("kind") != "reel"]
    if not queue:
        save_state(st)
        return 0
    if any(x["id"] == queue[0][1]["id"] for x in st["published"]):
        move(queue[0][0], queue[0][1], DONE)  # already published (dedupe)
        save_state(st)
        return 0

    f, post = queue[0]
    post["attempts"] = post.get("attempts", 0) + 1
    errors = []
    for prov in provs:
        if not provider_ready(st, prov.name):
            continue
        res = prov.publish(post)
        log(st, "attempt", id=post["id"], provider=prov.name, status=res.status, detail=res.detail[:200])
        if res.status == P.OK:
            record_published(st, f, post, res)
            save_state(st)
            return 0
        if res.status == P.UNCERTAIN:
            post["uncertain"] = {"provider": prov.name, "remote_id": res.remote_id, "t": iso(now())}
            f.write_text(json.dumps(post, ensure_ascii=False, indent=1))
            schedule_next(st)
            save_state(st)
            return 0
        if res.status == P.FB_BLOCK:
            st["global_pause_until"] = iso(now() + timedelta(hours=FB_BLOCK_PAUSE_H))
            f.write_text(json.dumps(post, ensure_ascii=False, indent=1))
            alert(st, f"FACEBOOK BLOCK via {prov.name}: {res.detail[:300]} -> ALL publishing paused {FB_BLOCK_PAUSE_H}h. "
                      "Do not retry; open Facebook on the phone and check the Page/account for a notice.")
            save_state(st)
            return 1
        if res.status == P.BAD_POST:
            post.setdefault("history", []).append({"t": iso(now()), "provider": prov.name, "status": res.status,
                                                   "detail": res.detail[:200]})
            move(f, post, FAILED)
            alert(st, f"Post {post['id']} rejected as invalid by {prov.name}: {res.detail[:200]}")
            save_state(st)
            return 1
        errors.append(f"{prov.name}:{res.status}:{res.detail[:150]}")
        if res.status == P.AUTH:
            cool(st, prov.name, AUTH_COOLDOWN_H * 60, res.detail)
            alert(st, f"{prov.name} lost its Facebook/API authorization ({res.detail[:200]}). "
                      f"Failing over; reconnect Kahwa news in {prov.name}.")
        else:
            cool(st, prov.name, TRANSIENT_COOLDOWN_MIN, res.detail)

    post.setdefault("history", []).append({"t": iso(now()), "status": "all_failed", "errors": errors})
    if post["attempts"] >= MAX_ATTEMPTS:
        move(f, post, FAILED)
        alert(st, f"Post {post['id']} failed on every provider {MAX_ATTEMPTS} times; parked. Last: {errors}")
    else:
        post["not_before"] = iso(now() + timedelta(minutes=20 * post["attempts"]))
        f.write_text(json.dumps(post, ensure_ascii=False, indent=1))
        if not any(provider_ready(st, p.name) for p in provs) or len(errors) == len(provs):
            alert(st, f"All providers failed for {post['id']} (attempt {post['attempts']}): {errors}")
    save_state(st)
    return 1 if errors else 0


def watchdog():
    """Alert if nothing was published for too long while posts are waiting."""
    if (ROOT / "state" / "PAUSE").exists() or os.getenv("PUBLISHING_ENABLED", "false").lower() != "true":
        return 0                                   # publishing is off by choice: a waiting queue is expected, not an alert
    st = load_state()
    hours = int(os.getenv("WATCHDOG_HOURS", "5"))
    last = parse(st.get("last_published_at"))
    waiting = len(list(QUEUE.glob("*.json")))
    gp = parse(st.get("global_pause_until"))
    if waiting and (not last or now() - last > timedelta(hours=hours)) and not (gp and gp > now()):
        if not in_quiet_hours(now()):
            alert(st, f"Watchdog: nothing published for {hours}h+ with {waiting} posts waiting.")
            save_state(st)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(watchdog() if "watchdog" in sys.argv else run())
