"""Slot dedupe for three clocks (owner decision 2026-10-31): cron-job.org (primary, exact minute, no slot input), a Cloudflare
Worker backup (~3 min later, WITH inputs.slot) and GitHub's own `schedule:` (fallback, often hours late). Each workflow must be
safe to run three times per slot.

    python -m kahwa_publisher.slots gate <workflow> [--slot ISO] [--test true|false] [--mode auto|single|edition]
        -> GITHUB_OUTPUT: run=true|false, slot=<ISO UTC>, key=<workflow>@<YYYY-MM-DDTHH:MMZ>, reason=..., mode=...
    python -m kahwa_publisher.slots done <workflow> <key> [--run-id N]

Design
- The slot grid is read from the workflow's own `schedule:` crons (.github/workflows/<workflow>.yml), so GitHub's schedule,
  cron-job.org and the Worker share one definition. cron-job.org / the Worker must use the same minutes.
- Slot = inputs.slot when given (snapped onto the grid), else the run start time snapped back to the most recent grid time.
- Stale guard: a slot older than WINDOW[workflow] when the run starts is skipped (a morning bulletin never airs at 18:00;
  JHBot pattern). Within the window, the first successful run of a slot marks it done in state/slots.json; later runs of the
  same slot (the other clocks) skip. A FAILED run does not mark the slot, so the backup clock / rerun.yml can still deliver it.
- The gate job checks out `main` at job start (not the trigger SHA), so a run queued behind another run of the same workflow
  (per-workflow concurrency group) always sees the marker the first run pushed.
- Test runs (dry_run / fixtures) are never gated and never mark a slot.
- Inside the products, idempotency stays as a second wall: queue items carry `slot` (bulletin/reel/card), and publish keeps the
  (item id, platform) ledger state/posted.json + the 75-min spacing, so even two runs that both pass the gate cannot double-post.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.getenv("KAHWA_ROOT", Path(__file__).resolve().parent.parent))
WF_DIR = Path(os.getenv("KAHWA_WF_DIR", Path(__file__).resolve().parent.parent / ".github" / "workflows"))
STATE = ROOT / "state" / "slots.json"
# how long after its scheduled time a slot may still run (minutes)
WINDOW = {"publish": 20, "newsroom": 120, "watchdog": 120, "reel": 150, "bulletin": 360}
KEEP_DAYS = 10
EDITION_TIMES = {(8, 11), (14, 11)}      # reel.yml "11 8,14 * * *" = «العالم في 30 ثانية» edition reels


# ------------------------------------------------------------------ cron grid
def _field(spec, lo, hi):
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
            if step > 1:
                b = hi
        out.update(range(a, b + 1, step))
    return out


def crons(workflow):
    t = (WF_DIR / f"{workflow}.yml").read_text()
    return re.findall(r'-\s*cron:\s*["\']([^"\']+)["\']', t)


def grid(workflow):
    """[(minutes set, hours set)] for each cron (all our crons use * for day/month/weekday)."""
    g = []
    for c in crons(workflow):
        m, h, dom, mon, dow = c.split()[:5]
        if (dom, mon, dow) != ("*", "*", "*"):
            raise ValueError(f"{workflow}: unsupported cron {c}")
        g.append((_field(m, 0, 59), _field(h, 0, 23)))
    return g


def snap(workflow, t):
    """Most recent grid time <= t (UTC, minute precision)."""
    g = grid(workflow)
    if not g:
        raise ValueError(f"{workflow}: no schedule")
    x = t.astimezone(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 2):
        if any(x.minute in ms and x.hour in hs for ms, hs in g):
            return x
        x -= timedelta(minutes=1)
    raise ValueError(f"{workflow}: no slot in 48 h")


def key(workflow, slot):
    return f"{workflow}@{slot:%Y-%m-%dT%H:%MZ}"


# ------------------------------------------------------------------ state
def load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def is_done(k, st=None):
    return k in (st if st is not None else load())


def mark_done(k, run_id=None, now=None):
    now = now or datetime.now(timezone.utc)
    st = load()
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    st = {kk: v for kk, v in st.items() if kk.split("@", 1)[-1] >= cutoff}
    st[k] = {"t": now.isoformat(timespec="seconds"), "run": run_id}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, sort_keys=True))
    return st


# ------------------------------------------------------------------ gate
def parse_iso(s):
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def gate(workflow, slot_in="", test=False, mode="auto", now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out = {"run": "true", "slot": "", "key": "", "reason": "", "mode": ""}
    if test:
        out.update(reason="test run: not gated, never marks a slot")
    else:
        try:
            base = parse_iso(slot_in) if slot_in else now
        except ValueError:
            base, out["reason"] = now, f"bad slot input {slot_in!r}: using run time; "
        s = snap(workflow, base)
        k = key(workflow, s)
        out.update(slot=s.strftime("%Y-%m-%dT%H:%M:00Z"), key=k)
        age = (now - s).total_seconds() / 60
        if age > WINDOW.get(workflow, 120):
            out.update(run="false", reason=out["reason"] + f"stale: slot {k} is {age:.0f} min old (> {WINDOW.get(workflow)} min)")
        elif is_done(k):
            out.update(run="false", reason=out["reason"] + f"slot {k} already done by another clock")
        else:
            out["reason"] += f"slot {k} not done yet: running"
    if workflow == "reel":
        if mode in ("single", "edition"):
            out["mode"] = mode
        elif out["slot"]:
            s = parse_iso(out["slot"])
            out["mode"] = "edition" if (s.hour, s.minute) in EDITION_TIMES else "single"
        else:
            out["mode"] = "single"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "done"])
    ap.add_argument("workflow")
    ap.add_argument("key", nargs="?")
    ap.add_argument("--slot", default="")
    ap.add_argument("--test", default="false")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID"))
    a = ap.parse_args()
    if a.cmd == "gate":
        r = gate(a.workflow, a.slot, a.test.lower() == "true", a.mode)
        print(json.dumps(r))
        gh = os.getenv("GITHUB_OUTPUT")
        if gh:
            with open(gh, "a") as f:
                for k, v in r.items():
                    f.write(f"{k}={v}\n")
        return 0
    if not a.key:
        print("no slot key (test run): nothing marked")
        return 0
    mark_done(a.key, a.run_id)
    print(json.dumps({"marked": a.key}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
