"""CI doctor (runs inside watchdog.yml every 2 h): classify every failed workflow run, file ONE issue per (workflow, cause),
and self-heal the platform-level things nobody should have to touch.

python tools/ci_doctor.py [--hours 26] [--dry]       env: GH_TOKEN / GITHUB_TOKEN, GITHUB_REPOSITORY

- Root cause = first matching rule in RULES on the failed job's log (the renderer's summary "error" field is preferred).
- A scheduled run's first attempt is ignored for 45 min (auto-retry.yml re-runs it once); only the final failure is filed.
- Issue title "Kahwa CI: <workflow> - <cause>"; an open issue with that title gets a comment per new run instead of a new issue,
  so a recurring cause is one thread with a counter, not an inbox flood. The body says what the code already does about it.
- Disabled workflows (disabled_inactivity / disabled_manually) are reported in one issue. No keepalive hack, no auto re-enable.
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

REPO = os.getenv("GITHUB_REPOSITORY", "kahouanews/kahwa-news-publisher")
API = f"https://api.github.com/repos/{REPO}"
TOKEN = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""

# (cause, regex, what the pipeline already does about it / what clears it). First match wins: specific before generic.
RULES = [
    ("content-rule", r"not healable|BANNED|'PSE'|'PAIR'",
     "Fail-closed by design (banned name / map rule on screen). The story is skipped; no action unless it repeats daily."),
    ("layout-qa", r"DOM QA failed",
     "make.heal_layout shrinks the broken scene (3 fit levels) then drops that shot/segment/brief. Reaching this means even "
     "dropping could not fix it: check the scene named in the error."),
    ("qa-crash", r"DOM QA crashed|Navigation timeout|ProtocolError|Target closed",
     "Transient headless-Chrome failure: dom_qa retries once on a new port and auto-retry.yml re-runs the job once."),
    ("audio-qa", r"true peak|loudness -?\d",
     "audio.fix_true_peak trims the final file; reaching this means the trim could not land inside the loudness window."),
    ("render-failed", r"hyperframes render failed|opener\.py|sting_b\.py .* failed",
     "HyperFrames/Chrome render error; auto-retried once. Repeats = a template/asset regression (see render.log tail)."),
    ("timeout", r"exceeded the maximum execution time|has timed out|TimeoutExpired|timed out after",
     "A step ran past its time budget (timeout-minutes). Auto-retried once; repeats = a source or model got slow."),
    ("disk-memory", r"No space left on device|ENOSPC|out of memory|MemoryError|exit code 137|Killed",
     "Runner disk/RAM. The preflight step frees ~20 GB when < 12 GB is free; repeats = output growth (check out/ size)."),
    ("secret-auth", r"\b401\b|Unauthorized|invalid[_ ]api[_ ]key|API key not valid|PERMISSION_DENIED|token (?:has )?expired|Bad credentials",
     "A key was revoked/expired. LLM router skips the provider; publishers fail over (dispatcher). Needs a human to renew the key."),
    ("llm-quota", r"HTTP 429 daily|quota|RESOURCE_EXHAUSTED|llm_preflight\": false|rate limit",
     "All LLM providers exhausted. Runs hold (exit 0) when preflight fails; this means quota ran out mid-run. Clears at the next "
     "quota window; add a provider key to widen the pool."),
    ("deps-drift", r"ModuleNotFoundError|ImportError|No matching distribution|ERESOLVE|npm ERR!|Could not find a version",
     "A dependency changed or vanished. Deps are pinned (requirements-ci.txt, package.json) with cache restore-keys; check the "
     "last Dependabot merge."),
    ("network-source", r"URLError|Connection (?:reset|refused|aborted)|Temporary failure in name resolution|HTTP Error 5\d\d|"
                       r"ETIMEDOUT|ECONNRESET|RemoteDisconnected|SSLError",
     "A source/API was unreachable. Signals and feeds are best-effort; auto-retried once."),
    ("git-race", r"failed to push some refs|\[rejected\]|non-fast-forward|could not apply",
     "Two bot commits raced. Push steps pull --rebase and retry 4x; auto-retried once."),
    ("editorial-skip", r"claim verification failed|fact-check:|StoryError",
     "Fail-closed editorial gate (unsupported claim). Should exit 0 (skip); if it failed the run, that is a code bug."),
    ("code-bug", r"Traceback|Error: |Exception",
     "Unhandled exception in our code: the error and traceback are below. Auto-retried once (fresh news may avoid it)."),
]


# Only these are re-run (once, by rerun.yml). Everything else would fail the same way again or is a fail-closed decision.
TRANSIENT = {"qa-crash", "timeout", "network-source", "git-race", "render-failed"}


def classify(text):
    for cause, rx, fix in RULES:
        if re.search(rx, text or "", re.I if cause not in ("content-rule",) else 0):
            return cause, fix
    return "unknown", "No rule matched: read the run log."


def api(method, path, body=None, raw=False):
    url = path if path.startswith("http") else API + path
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {TOKEN}",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if raw:
        return data
    return json.loads(data) if data else {}


def error_text(log):
    """Prefer the renderer's own summary error; else the last lines before '##[error]'."""
    lines = [re.sub(r"^\S+Z ", "", l) for l in log.splitlines()]
    for l in reversed(lines):           # the run-level error is always "<ExceptionName>: ..." (per-story errors are not)
        m = re.search(r'"(?:run_)?error": "([A-Za-z_]*(?:Error|Exception|Rejected|Hold)\b(?:[^"\\]|\\.){0,1500})', l)
        if m:
            return m.group(1)
    idx = [i for i, l in enumerate(lines) if "##[error]" in l]
    if idx:
        i = idx[0]
        return "\n".join(lines[max(0, i - 25):i + 1])[-3000:]
    return "\n".join(lines[-40:])[-3000:]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch_log(job_id):
    """The logs endpoint answers 302 to a signed blob URL; our Bearer header must NOT follow the redirect (the blob store
    rejects it with an auth error, which once got misread as a 'secret-auth' failure)."""
    req = urllib.request.Request(f"{API}/actions/jobs/{job_id}/logs", headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {TOKEN}", "X-GitHub-Api-Version": "2022-11-28"})
    op = urllib.request.build_opener(_NoRedirect)
    try:
        with op.open(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
        if not loc:
            raise
    with urllib.request.urlopen(loc, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def job_log(run_id):
    jobs = api("GET", f"/actions/runs/{run_id}/jobs?filter=latest").get("jobs", [])
    bad = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out", "cancelled")] or jobs
    out = []
    for j in bad[:2]:
        step = next((s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"), "?")
        try:
            log = fetch_log(j["id"])
        except Exception as ex:
            log = f"(log unavailable: {type(ex).__name__})"
        out.append((j["name"], step, log))
    return out


def issue_for(title):
    q = urllib.request.quote(f'repo:{REPO} is:issue is:open in:title "{title}"')
    items = api("GET", f"https://api.github.com/search/issues?q={q}").get("items", [])
    return next((i for i in items if i["title"] == title), None)


def workflow_states():
    """No keepalive hacks (GitHub disabled keepalive-workflow for ToS; research 2026-10-31 §2.6): our real queue/state commits
    are the repository activity. We only DETECT a disabled workflow and file an issue for the human to re-enable it."""
    return [f"{w['name']} is {w['state']}" for w in api("GET", "/actions/workflows?per_page=100").get("workflows", [])
            if w.get("state", "active") != "active"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--rerun-check", type=int, metavar="RUN_ID", help="print the cause; exit 0 only if it is transient")
    a = ap.parse_args()
    if a.rerun_check:
        logs = job_log(a.rerun_check)
        err = "\n".join(error_text(l) for _, _, l in logs)
        cause, _ = classify(err)
        print(json.dumps({"run": a.rerun_check, "cause": cause, "transient": cause in TRANSIENT, "error": err[:500]},
                         ensure_ascii=False))
        return 0 if cause in TRANSIENT else 3
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = api("GET", f"/actions/runs?per_page=100&created=>={since}").get("workflow_runs", [])
    report = {"checked": len(runs), "filed": [], "skipped_pending_retry": [], "disabled_workflows": workflow_states()}
    if report["disabled_workflows"] and not a.dry:
        title = "Kahwa CI: a scheduled workflow is disabled"
        if not issue_for(title):
            api("POST", "/issues", {"title": title, "body": "GitHub disabled: " + "; ".join(report["disabled_workflows"]) +
                                    "\n\nRe-enable it under Actions -> the workflow -> Enable workflow (human step by design)."})
    for r in runs:
        if r.get("status") != "completed" or r.get("conclusion") not in ("failure", "timed_out", "startup_failure"):
            continue
        upd = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        if r["event"] == "schedule" and r.get("run_attempt", 1) < 2 and now - upd < timedelta(minutes=45):
            report["skipped_pending_retry"].append(r["id"])
            continue
        if r["event"] == "workflow_dispatch":
            continue                    # manual tests fail loudly in the run itself
        logs = job_log(r["id"])
        err = "\n".join(error_text(l) for _, _, l in logs) or r.get("conclusion", "")
        steps = ", ".join(f"{j}/{s}" for j, s, _ in logs)
        if not logs or all(l.startswith("(log unavailable") for _, _, l in logs):
            cause, fix = "unknown", "The run log could not be read; open the run."
        else:
            cause, fix = classify(err if err.strip() else "\n".join(l for _, _, l in logs))
        title = f"Kahwa CI: {r['name']} - {cause}"
        url = r["html_url"]
        body = (f"Run: {url} (attempt {r.get('run_attempt', 1)}, {r['event']}, {r['head_sha'][:7]}, {r['created_at']})\n"
                f"Failed step: {steps}\n\nRoot cause class: **{cause}**\nWhat the pipeline does about it: {fix}\n\n"
                f"```\n{err[:2500]}\n```")
        report["filed"].append({"run": r["id"], "workflow": r["name"], "cause": cause})
        if a.dry:
            continue
        q = urllib.request.quote(f'repo:{REPO} is:issue "{r["id"]}"')
        if api("GET", f"https://api.github.com/search/issues?q={q}").get("total_count"):
            continue                    # this run is already filed (open or closed issue, body or comment)
        iss = issue_for(title)
        if iss:
            comments = api("GET", iss["comments_url"] + "?per_page=100")
            if url in (iss.get("body") or "") or any(url in (c.get("body") or "") for c in comments):
                continue
            api("POST", iss["comments_url"], {"body": body})
        else:
            api("POST", "/issues", {"title": title, "body": body})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
