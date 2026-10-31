"""Publisher connection health (watchdog, every run; research work/research-facebook-token-longevity-2026-10-31.md §5).

python -m kahwa_publisher.health          env: ZERNIO_API_KEY, ZERNIO_FB_ACCOUNT_ID

Zernio GET /v1/accounts/health?platform=facebook (docs.zernio.com/accounts/get-all-accounts-health) for the Kahwa news
account: tokenValid, tokenExpiresAt, needsReconnect, canPost, status, disconnectReason.

- Disconnected (needsReconnect / !tokenValid / !canPost / status=error / token already expired / our key gets 401):
  the circuit for "zernio" opens in state/provider_health.json -> the dispatcher skips Zernio and posts through Buffer, then
  Make (automatic failover). One issue "reconnect Zernio" right away. Reconnecting is a human Facebook login by design.
- tokenExpiresAt within EXPIRY_WARN_DAYS (7): one issue, Zernio stays in use until it really expires.
- Healthy again: the circuit closes by itself on the next check (auto re-enable), and the issue can be closed.
- Health endpoint unreachable / 5xx: no change (never open a circuit on our own network problem).
Writes only state/provider_health.json (own file: no merge conflicts with the publisher's state.json). Prints JSON with the
issues to file; the workflow files them deduplicated by title.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.getenv("KAHWA_ROOT", Path(__file__).resolve().parent.parent))
HEALTH = ROOT / "state" / "provider_health.json"
EXPIRY_WARN_DAYS = int(os.getenv("EXPIRY_WARN_DAYS", "7"))
BASE = "https://zernio.com/api/v1"


def load():
    try:
        return json.loads(HEALTH.read_text())
    except Exception:
        return {}


def is_open(name, h=None):
    return bool((h if h is not None else load()).get(name, {}).get("open"))


def _get(url, key, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                               "User-Agent": "kahwa-news-publisher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:                       # network: unknown, never a verdict
        return 0, {"error": str(e)[:200]}


def assess(code, body, account_id, now):
    """Pure decision: returns (state: ok|warn|down|unknown, detail dict)."""
    if code == 401:
        return "down", {"why": "Zernio API key rejected (401)", "reconnect": "Zernio API key"}
    if code != 200:
        return "unknown", {"why": f"health HTTP {code}", "body": str(body)[:200]}
    accs = [a for a in body.get("accounts", []) if a.get("platform") == "facebook"]
    acc = next((a for a in accs if a.get("accountId") == account_id), None) or (accs[0] if len(accs) == 1 else None)
    if not acc:
        return "down", {"why": f"Kahwa news Facebook account {account_id or '?'} not in Zernio (disconnected/removed)",
                        "reconnect": "Zernio -> Facebook"}
    d = {k: acc.get(k) for k in ("accountId", "username", "status", "canPost", "tokenValid", "tokenExpiresAt", "needsReconnect",
                                 "disconnectReason", "disconnectionType", "unhealthySince", "issues")}
    exp = None
    if acc.get("tokenExpiresAt"):
        try:
            exp = datetime.fromisoformat(acc["tokenExpiresAt"].replace("Z", "+00:00"))
        except ValueError:
            exp = None
    if acc.get("needsReconnect") or acc.get("tokenValid") is False or acc.get("canPost") is False \
            or acc.get("status") == "error" or (exp and exp <= now):
        d["why"] = acc.get("disconnectReason") or ("token expired" if exp and exp <= now else "ACCOUNT_DISCONNECTED")
        d["reconnect"] = "Zernio -> Facebook"
        return "down", d
    if exp and exp - now <= timedelta(days=EXPIRY_WARN_DAYS):
        d["why"] = f"token expires {exp:%Y-%m-%d %H:%M} UTC ({(exp - now).days} days)"
        return "warn", d
    return "ok", d


def check(now=None):
    now = now or datetime.now(timezone.utc)
    key, acct = os.getenv("ZERNIO_API_KEY", ""), os.getenv("ZERNIO_FB_ACCOUNT_ID", "")
    h = load()
    issues = []
    if not key:
        return {"zernio": "not configured", "issues": issues}
    code, body = _get(f"{BASE}/accounts/health?platform=facebook", key)
    state, d = assess(code, body, acct, now)
    z = h.setdefault("zernio", {})
    z.update(checked=now.isoformat(timespec="seconds"), state=state, detail=d)
    if state == "down":
        if not z.get("open"):
            z["opened"] = now.isoformat(timespec="seconds")
        z["open"] = True                                  # dispatcher skips Zernio -> Buffer -> Make
        issues.append({"title": "Kahwa: reconnect Zernio (Facebook disconnected)",
                       "body": f"Zernio reports the Kahwa news Facebook connection is down: {d.get('why')}.\n\n"
                               "Automatic action: Zernio is skipped, posts go through Buffer, then Make, until a check sees it healthy.\n"
                               "Human step (cannot be automated: Facebook login): open Zernio -> Accounts -> reconnect Facebook "
                               "(Kahwa news). The circuit closes by itself at the next watchdog run.\n\n```\n"
                               + json.dumps(d, ensure_ascii=False, indent=1)[:1500] + "\n```"})
    elif state in ("ok", "warn"):
        if z.get("open"):
            z["closed"] = now.isoformat(timespec="seconds")   # recovered: auto re-enable
        z["open"] = False
        if state == "warn":
            issues.append({"title": f"Kahwa: Zernio Facebook token expires {d.get('tokenExpiresAt', '')[:10]}",
                           "body": f"{d.get('why')}. Zernio stays in use until then; at expiry it is skipped automatically "
                                   "(Buffer/Make take over). Reconnect Facebook in Zernio before that date to avoid the switch."})
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH.write_text(json.dumps(h, ensure_ascii=False, indent=1))
    return {"zernio": state, "detail": d, "issues": issues}


if __name__ == "__main__":
    r = check()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    Path("health-issues.json").write_text(json.dumps(r["issues"], ensure_ascii=False))
    sys.exit(0)
