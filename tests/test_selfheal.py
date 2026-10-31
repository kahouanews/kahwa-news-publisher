"""Regression tests for every CI failure root cause of 2026-10-17 .. 2026-10-31 (see work/ci-fix-2026-10-31.md).

  layout  : bulletin DOM QA overlap / rail / top / left (runs 41180526256, 40963351803, 40960800162, 40957631882)
            -> make.heal_layout: fit levels per broken scene, then drop shot/segment/brief; content rules never healed
  qa-crash: DOM QA puppeteer navigation timeout (run 40945212474) -> render.dom_qa retries once on a new port
  tp      : reel QA "true peak 0.07" (runs 41053869183, 41105257205) -> limiter after loudnorm + final-file TP guard
  keys    : publisher key expiry (Buffer 2026-11-27) -> dispatcher skips expired / demotes expiring providers
"""
import copy
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "bulletin-2026-10-29.json"
HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ------------------------------------------------------------------ layout self-healing
def _spec():
    return json.loads(FIX.read_text())


def _patch(monkeypatch, judge):
    """judge(B, times) -> list of violations; DOM QA replaced by a pure function of the rundown (no browser)."""
    from kahwa_publisher.bulletin import make as MK
    calls = []
    monkeypatch.setattr(MK.RN, "build_body", lambda B, comp, media_dir=None: B)
    def fake_qa(comp, times, outfile, shots=None, timeout=1500):
        calls.append(len(times))
        return {"violations": judge(comp, times), "console_errors": [], "map_labels": [], "checked": len(times)}
    monkeypatch.setattr(MK.RN, "dom_qa", fake_qa)
    return MK, calls


def _viol(B, times, sid, flags=("overlap",), below=1):
    sc = next((s for s in B["scenes"] if s["id"] == sid), None)
    if not sc or int(sc.get("fit", 0)) >= below:
        return []
    return [{"t": t, "text": "x <> y", "box": [0, 0, 1, 1], "v": list(flags)} for t in times if sc["t"] <= t < sc["t"] + sc["d"]]


def test_fit_level_heals_overlap_without_dropping_anything(monkeypatch, tmp_path):
    MK, calls = _patch(monkeypatch, lambda B, times: _viol(B, times, "s2a", below=2))   # needs fit 2 (like the quote overlap)
    spec = _spec()
    B, comp, dq, heal = MK.heal_layout(spec, "bulletin", tmp_path, FIX.parent, tmp_path)
    s2a = next(s for s in B["scenes"] if s["id"] == "s2a")
    assert s2a["fit"] == 2 and not dq["violations"]
    assert [h.get("fit") for h in heal if h.get("scene") == "s2a"] == [1, 2]
    assert len(B["segs"]) == len(_spec()["segments"]) + 1          # all segments + «باختصار» kept
    assert calls[-1] == 3 and sum(calls) > 40                       # re-check only the healed scene's 3 times


def test_unhealable_shot_is_dropped_and_the_bulletin_still_ships(monkeypatch, tmp_path):
    def judge(B, times):        # the marked shot breaks at every fit level, whatever its id is after a drop
        sc = next((s for s in B["scenes"] if s.get("_bad")), None)
        return _viol(B, times, sc["id"], below=99) if sc else []
    MK, _ = _patch(monkeypatch, judge)
    spec = _spec()
    spec["segments"][0]["shots"][2]["_bad"] = True
    n0 = len(spec["segments"][0]["shots"])
    B, comp, dq, heal = MK.heal_layout(spec, "bulletin", tmp_path, FIX.parent, tmp_path)
    assert any(h.get("action") == "dropped shot s1c" for h in heal)
    assert len(spec["segments"][0]["shots"]) == n0 - 1 and not dq["violations"]


def test_broken_headline_drops_its_segment_when_enough_remain(monkeypatch, tmp_path):
    def judge(B, times):        # the headline of the 4th story ("hl4") breaks at every fit level, whatever story is 4th
        keys = [s.get("segkey") for s in B["scenes"] if s["type"] == "headline"]
        return _viol(B, times, f"hl{keys.index(k4) + 1}", below=99) if k4 in keys else []
    k4 = _spec()["segments"][3]["key"]
    MK, _ = _patch(monkeypatch, judge)
    spec = _spec()
    B, comp, dq, heal = MK.heal_layout(spec, "bulletin", tmp_path, FIX.parent, tmp_path)
    assert k4 not in [g["key"] for g in spec["segments"]] and len(spec["segments"]) == 4 and not dq["violations"]


def test_banned_name_or_map_rule_is_never_healed(monkeypatch, tmp_path):
    for flag in ("BANNED", "PSE", "PAIR"):
        MK, _ = _patch(monkeypatch, lambda B, times, f=flag: _viol(B, times, "s2a", flags=(f,), below=99))
        with pytest.raises(RuntimeError, match="not healable"):
            MK.heal_layout(_spec(), "bulletin", tmp_path, FIX.parent, tmp_path)


def test_heal_gives_up_cleanly_when_nothing_can_be_dropped(monkeypatch, tmp_path):
    MK, _ = _patch(monkeypatch, lambda B, times: _viol(B, times, "end", below=99))
    with pytest.raises(RuntimeError, match="could not be healed"):
        MK.heal_layout(_spec(), "bulletin", tmp_path, FIX.parent, tmp_path)


def test_fit_levels_reach_the_template():
    from kahwa_publisher.bulletin import rundown as RD
    spec = dict(_spec(), _fit={"s2a": 2, "hl1": 1})
    B = RD.build(spec)
    fit = {s["id"]: s.get("fit") for s in B["scenes"]}
    assert fit["s2a"] == 2 and fit["hl1"] == 1 and fit.get("s2b") is None
    tpl = (ROOT / "kahwa_publisher" / "bulletin" / "templates" / "body.tpl.js").read_text()
    assert "const FIT = [1, 0.86, 0.74, 0.64]" in tpl and 'data-scene="' in tpl
    assert tpl.index("const FIT") < tpl.index("MAPS.forEach((f) => f());")   # scaled before map pills are placed


def test_dom_qa_crash_is_retried_once(monkeypatch, tmp_path):
    from kahwa_publisher.bulletin import render as RN
    ok = json.dumps({"errors": [], "rep": [{"t": 1.0, "violations": [], "mapLabels": []}]})
    seq = [subprocess.CompletedProcess([], 1, "", "TimeoutError: Navigation timeout of 600000 ms exceeded"),
           subprocess.CompletedProcess([], 0, ok, "")]
    ports = []
    def fake_run(cmd, capture_output=True, text=True, env=None, timeout=None):
        ports.append(env["PORT"])
        return seq.pop(0)
    monkeypatch.setenv("KAHWA_CHROME", "/bin/true")
    monkeypatch.setattr(RN.subprocess, "run", fake_run)
    monkeypatch.setattr(RN.time, "sleep", lambda s: None)
    r = RN.dom_qa(tmp_path, [1.0], tmp_path / "q.json")
    assert r["checked"] == 1 and len(ports) == 2 and ports[0] != ports[1]


# ------------------------------------------------------------------ true peak
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_true_peak_guard_fixes_a_hot_reel(tmp_path):
    from kahwa_publisher.reels import audio
    hot = tmp_path / "hot.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='0.98*sgn(sin(2*PI*220*t))*(0.6+0.4*sin(2*PI*0.7*t))':s=48000:d=5",
                    "-f", "lavfi", "-i", "color=c=gray:s=108x192:d=5", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(hot)], check=True)
    assert audio.measure(str(hot))[1] > -1.0
    I, TP, steps = audio.fix_true_peak(hot)
    assert TP <= -1.0 and steps
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                           str(hot)], capture_output=True, text=True).stdout.strip() == "h264"   # video kept


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_loudnorm_master_has_true_peak_headroom(tmp_path):
    from kahwa_publisher.reels import audio
    src, out = tmp_path / "in.wav", tmp_path / "out.wav"
    # quiet bed with sparse full-scale transients: linear loudnorm alone pushes these over 0 dBTP (the 2026-10-30 failure)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    r"aevalsrc='0.05*sin(2*PI*330*t)+0.95*lt(mod(t\,1)\,0.004)*sin(2*PI*3000*t)':s=48000:d=8", str(src)], check=True)
    audio.loudnorm(str(src), str(out))
    assert audio.measure(str(out))[1] <= -1.0


# ------------------------------------------------------------------ publisher key expiry
class _P:
    def __init__(self, name):
        self.name = name


def test_expired_key_skips_provider_and_expiring_one_is_demoted(monkeypatch):
    import importlib
    import kahwa_publisher.dispatcher as D
    importlib.reload(D)
    t = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    provs = [_P("zernio"), _P("buffer"), _P("make")]
    order, expired = D.order_by_key_expiry(provs, t, env="BUFFER_API_KEY=2026-11-27 SAMBANOVA_API_KEY=2027-01-29")
    assert [p.name for p in order] == ["zernio", "make", "buffer"] and not expired
    order, expired = D.order_by_key_expiry(provs, datetime(2026, 11, 28, tzinfo=timezone.utc), env="BUFFER_API_KEY=2026-11-27")
    assert [p.name for p in order] == ["zernio", "make"] and expired == ["buffer"]
    order, expired = D.order_by_key_expiry(provs, t, env="garbage BUFFER_API_KEY=not-a-date")
    assert [p.name for p in order] == ["zernio", "buffer", "make"]


# ------------------------------------------------------------------ CI doctor: classification + transient-only re-run
SAMPLES = {   # error lines taken from the real failed runs of 2026-10-29 .. 2026-10-31
    "layout-qa": "RuntimeError: DOM QA failed: [{'t': 66.71, 'text': 'الكنديون <> مارك كارني', 'v': ['overlap']}] []",
    "qa-crash": "RuntimeError: DOM QA crashed: .../puppeteer-core/lib/puppeteer/cdp/LifecycleWatcher.js:70:46) at CdpFrame.goto",
    "audio-qa": "RuntimeError: QA failed, reel NOT queued: ['true peak 0.07']",
    "editorial-skip": "RuntimeError: claim verification failed, reel NOT made: [{'id': 3, 'verdict': 'unsupported'}]",
    "code-bug": "KeyError: 'b4'\nTraceback (most recent call last):",
    "llm-quota-daily": "Hold: HTTP 429 daily: Rate limit reached for model on tokens per day (TPD): Limit 200000",
    "llm-rate-minute": "HTTP 429: Rate limit reached for model on tokens per minute (TPM); please retry in 7.5s",
    "account-suspended": "remote: Your account is suspended. Please visit https://support.github.com for more information.",
    "secret-auth": "HTTPError: 401 Unauthorized: invalid api key",
    "disk-memory": "OSError: [Errno 28] No space left on device",
    "network-source": "URLError: <urlopen error Temporary failure in name resolution>",
    "timeout": "subprocess.TimeoutExpired: Command 'node qa.mjs' timed out after 1500 seconds",
    "deps-drift": "ModuleNotFoundError: No module named 'pyloudnorm'",
    "git-race": "! [rejected] main -> main (fetch first) error: failed to push some refs",
    "content-rule": "RuntimeError: DOM QA failed (content rule, not healable): [{'v': ['BANNED']}]",
}


@pytest.mark.parametrize("cause", sorted(SAMPLES))
def test_ci_doctor_classifies_each_root_cause(cause):
    sys.path.insert(0, str(ROOT / "tools"))
    import ci_doctor as C
    assert C.classify(SAMPLES[cause])[0] == cause


def test_only_transient_causes_are_rerun():
    sys.path.insert(0, str(ROOT / "tools"))
    import ci_doctor as C
    assert C.TRANSIENT == {"qa-crash", "timeout", "network-source", "git-race", "render-failed", "llm-rate-minute"}
    for never in ("layout-qa", "audio-qa", "code-bug", "llm-quota-daily", "secret-auth", "content-rule", "editorial-skip"):
        assert never not in C.TRANSIENT and not C.is_transient(never, incident=True)
    assert C.is_transient("account-suspended", incident=True) and not C.is_transient("account-suspended", incident=False)


def test_rerun_covers_schedule_and_dispatch_once():
    t = (ROOT / ".github" / "workflows" / "rerun.yml").read_text()
    assert "run_attempt == 1" in t and "'schedule'" in t and "'workflow_dispatch'" in t and "publish" not in t.split("workflows:")[1].split("]")[0]


def test_doctor_reads_the_run_level_error_not_a_story_error():
    sys.path.insert(0, str(ROOT / "tools"))
    import ci_doctor as C
    log = ('2026-10-31T07:57:00Z {"summary": {"stories": [{"ok": false, "error": "brief too long (77 > 64)"}]}}\n'
           '2026-10-31T07:57:00Z {"run_error": "RuntimeError: DOM QA failed: [overlap]"}\n')
    assert C.error_text(log).startswith("RuntimeError: DOM QA failed")


def test_no_keepalive_hack_in_any_workflow():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        t = wf.read_text().lower()
        assert "keepalive" not in t.replace("keepalive-workflow", "") or "no keepalive" in t, wf.name
    assert "KEEPALIVE" not in (ROOT / "tools" / "ci_doctor.py").read_text().replace("No keepalive", "")


def test_workflows_pinned_runner_and_action_shas_and_npm_ci():
    import re
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        t = wf.read_text()
        assert "ubuntu-latest" not in t, wf.name
        if "schedule:" in t:
            assert "workflow_dispatch" in t, f"{wf.name}: must be dispatchable (external clock)"
        lock = (ROOT / ".github" / "workflows" / "actions.lock").read_text()
        for u in re.findall(r"uses:\s*(\S+)", t):        # pinned by the GitHub workflow lockfile (GA 2026-10-28) or a SHA
            assert re.search(r"@[0-9a-f]{40}$", u) or u in lock, f"{wf.name}: {u} not in actions.lock"
        assert "npm install --prefix" not in t, f"{wf.name}: use npm ci from the lockfile"
    assert (ROOT / "kahwa_publisher" / "reels" / "npm-shrinkwrap.json").exists()


# ------------------------------------------------------------------ idempotent slots + card fallback
def test_slot_keys_and_idempotency(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("KAHWA_ROOT", str(tmp_path))
    import kahwa_publisher.fallback as FB
    importlib.reload(FB)
    t = datetime(2026, 11, 1, 6, 31, tzinfo=timezone.utc)
    assert FB.slot_key("bulletin", t) == "bulletin-2026-11-01"
    assert FB.slot_key("reel", t) == "reel-2026-11-01-06"
    assert FB.slot_key("reel", t, slot="2026-11-01T17:41:00Z") == "reel-2026-11-01-17"     # external trigger input wins
    assert not FB.slot_done("bulletin-2026-11-01")
    (tmp_path / "queue" / "done").mkdir(parents=True)
    (tmp_path / "queue" / "done" / "x.json").write_text(json.dumps({"id": "202611010631-qahwat-alyawm-bulletin",
                                                                      "slot": "bulletin-2026-11-01"}))
    assert FB.slot_done("bulletin-2026-11-01") and not FB.slot_done("bulletin-2026-11-02")
    (tmp_path / "queue" / "202611010813-abc123.json").write_text(json.dumps({"id": "202611010813-abc123"}))
    assert FB.story_covered("abc123") and not FB.story_covered("zzz")


def test_reel_card_fallback_reuses_only_verified_lines(tmp_path, monkeypatch):
    import kahwa_publisher.fallback as FB
    import kahwa_publisher.cards as CARDS
    seen = {}
    monkeypatch.setattr(CARDS, "render_card", lambda p, out: (seen.update(p), Path(out).write_bytes(b"jpg"), {"credit": ""})[-1])
    S = json.loads((ROOT / "tests" / "fixtures" / "reel-map-tunisia.json").read_text())
    item, img = FB.card_from_reel(S, tmp_path, S["id"])
    facts = [FB.plain(f["text"]) for f in S["facts"]][:4]
    assert seen["facts"] == facts and seen["no_photo"] is True and img.exists()
    assert item["kind"] == "post" and item["fallback_for"] == "video" and item["requires_publishing_enabled"]
    for line in item["text"].splitlines():          # nothing but the headline, deck, verified facts and the source line
        assert not line or line.lstrip("- ") in facts + [FB.plain(S["headline"]), FB.plain(S["deck"])] or line.startswith("المصدر:")


# ------------------------------------------------------------------ Zernio connection health -> circuit -> failover
def test_zernio_health_assess():
    from kahwa_publisher import health as H
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    acc = {"accountId": "A1", "platform": "facebook", "status": "healthy", "canPost": True, "tokenValid": True,
           "tokenExpiresAt": "2027-01-01T00:00:00Z", "needsReconnect": False}
    assert H.assess(200, {"accounts": [acc]}, "A1", now)[0] == "ok"
    assert H.assess(200, {"accounts": [dict(acc, tokenExpiresAt="2026-11-06T00:00:00Z")]}, "A1", now)[0] == "warn"
    assert H.assess(200, {"accounts": [dict(acc, tokenExpiresAt="2026-10-31T00:00:00Z")]}, "A1", now)[0] == "down"
    assert H.assess(200, {"accounts": [dict(acc, needsReconnect=True, disconnectReason="Token expired")]}, "A1", now)[0] == "down"
    assert H.assess(200, {"accounts": [dict(acc, canPost=False)]}, "A1", now)[0] == "down"
    assert H.assess(200, {"accounts": []}, "A1", now)[0] == "down"
    assert H.assess(401, {}, "A1", now)[0] == "down"
    assert H.assess(503, {}, "A1", now)[0] == "unknown" and H.assess(0, {}, "A1", now)[0] == "unknown"


def test_health_circuit_opens_issues_once_and_closes_by_itself(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("KAHWA_ROOT", str(tmp_path))
    monkeypatch.setenv("ZERNIO_API_KEY", "k")
    monkeypatch.setenv("ZERNIO_FB_ACCOUNT_ID", "A1")
    import kahwa_publisher.health as H
    importlib.reload(H)
    acc = {"accountId": "A1", "platform": "facebook", "status": "error", "canPost": False, "tokenValid": False, "needsReconnect": True}
    monkeypatch.setattr(H, "_get", lambda url, key: (200, {"accounts": [acc]}))
    r = H.check()
    assert r["zernio"] == "down" and H.is_open("zernio") and "reconnect Zernio" in r["issues"][0]["title"]
    monkeypatch.setattr(H, "_get", lambda url, key: (503, {}))           # our network problem: no verdict change
    assert H.check()["zernio"] == "unknown" and H.is_open("zernio")
    monkeypatch.setattr(H, "_get", lambda url, key: (200, {"accounts": [dict(acc, status="healthy", canPost=True, tokenValid=True,
                                                                              needsReconnect=False)]}))
    assert H.check()["zernio"] == "ok" and not H.is_open("zernio")       # auto re-enable


def test_dispatcher_fails_over_when_health_circuit_is_open(tmp_path):
    import importlib, os
    os.environ.update(PUBLISHING_ENABLED="true", KAHWA_ROOT=str(tmp_path), QUIET_HOURS_UTC="25-25", MIN_GAP_MINUTES="0",
                      JITTER_MINUTES="0")
    import kahwa_publisher.providers as P, kahwa_publisher.dispatcher as D, kahwa_publisher.health as H
    importlib.reload(P); importlib.reload(D); importlib.reload(H)
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "a.json").write_text(json.dumps({"id": "a", "text": "t", "media": []}))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "provider_health.json").write_text(json.dumps({"zernio": {"open": True}}))
    calls = []
    class F:
        def __init__(s, n): s.name = n
        def configured(s): return True
        def publish(s, post):
            calls.append(s.name); return P.Result(P.OK, s.name, "x", "rid")
    P.all_providers = lambda: [F("zernio"), F("buffer"), F("make")]
    assert D.run() == 0 and calls == ["buffer"]


def test_watchdog_is_silent_while_publishing_is_disabled(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("KAHWA_ROOT", str(tmp_path))
    monkeypatch.setenv("PUBLISHING_ENABLED", "false")
    import kahwa_publisher.dispatcher as D
    importlib.reload(D)
    (tmp_path / "queue").mkdir()
    for i in range(3):
        (tmp_path / "queue" / f"{i}.json").write_text("{}")
    assert D.watchdog() == 0 and not (tmp_path / "state" / "ALERT.txt").exists()


def test_doctor_does_not_send_the_token_to_the_log_blob_redirect():
    sys.path.insert(0, str(ROOT / "tools"))
    import ci_doctor as C
    assert C._NoRedirect().redirect_request() is None and "fetch_log" in C.job_log.__code__.co_names



# ------------------------------------------------------------------ publish: (item, platform) guard + confirmed heartbeat
def _disp(tmp_path, fakes):
    import importlib, os
    os.environ.update(PUBLISHING_ENABLED="true", KAHWA_ROOT=str(tmp_path), QUIET_HOURS_UTC="25-25", MIN_GAP_MINUTES="0",
                      JITTER_MINUTES="0")
    import kahwa_publisher.providers as P, kahwa_publisher.dispatcher as D
    importlib.reload(P); importlib.reload(D)
    P.all_providers = lambda: fakes
    return P, D


def test_same_item_is_never_posted_twice_even_if_requeued(tmp_path):
    calls = []
    class F:
        name = "zernio"
        def configured(s): return True
        def publish(s, post):
            import kahwa_publisher.providers as P
            calls.append(post["id"]); r = P.Result(P.OK, "zernio", "https://www.facebook.com/x/posts/1", "z1")
            r.platform_post_id = "1"; return r
    P, D = _disp(tmp_path, [F()])
    (tmp_path / "queue").mkdir()
    item = json.dumps({"id": "same", "text": "t", "media": []})
    (tmp_path / "queue" / "same.json").write_text(item)
    assert D.run() == 0 and calls == ["same"]
    (tmp_path / "queue" / "same.json").write_text(item)            # duplicate trigger / re-run / external clock re-queues it
    st = json.loads((tmp_path / "state" / "state.json").read_text()); st["published"] = []; st["next_allowed_at"] = None
    (tmp_path / "state" / "state.json").write_text(json.dumps(st))  # even with the in-state list gone, the ledger holds
    assert D.run() == 0 and calls == ["same"]
    assert "same|facebook" in json.loads((tmp_path / "state" / "posted.json").read_text())
    hb = json.loads((tmp_path / "state" / "heartbeat.json").read_text())
    assert hb["id"] == "same" and hb["platform_post"] == "1"


def test_heartbeat_only_on_confirmed_facebook_post(tmp_path):
    class F:
        name = "make"
        def configured(s): return True
        def publish(s, post):
            import kahwa_publisher.providers as P
            return P.Result(P.OK, "make", "accepted", None)             # webhook accepted: no Facebook post id
    P, D = _disp(tmp_path, [F()])
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "a.json").write_text(json.dumps({"id": "a", "text": "t", "media": []}))
    assert D.run() == 0 and not (tmp_path / "state" / "heartbeat.json").exists()


# ------------------------------------------------------------------ 3 clocks, 1 slot: kahwa_publisher/slots.py
def _slots(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("KAHWA_ROOT", str(tmp_path))
    import kahwa_publisher.slots as SL
    importlib.reload(SL)
    return SL


def test_slot_grid_comes_from_the_workflow_crons(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    U = lambda h, m: datetime(2026, 11, 1, h, m, 40, tzinfo=timezone.utc)
    assert SL.snap("publish", U(10, 30)) == U(10, 27).replace(second=0)
    assert SL.snap("newsroom", U(8, 0)) == U(7, 13).replace(second=0)
    assert SL.snap("bulletin", U(6, 33)) == U(6, 30).replace(second=0)
    assert SL.snap("reel", U(8, 14)) == U(8, 11).replace(second=0) and SL.snap("reel", U(12, 0)) == U(11, 41).replace(second=0)
    assert SL.snap("watchdog", U(9, 0)) == U(8, 33).replace(second=0)


def test_three_clocks_run_a_slot_once(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    t0 = datetime(2026, 11, 1, 6, 30, 5, tzinfo=timezone.utc)
    a = SL.gate("bulletin", "", now=t0)                                         # cron-job.org, exact minute, no slot
    assert a["run"] == "true" and a["key"] == "bulletin@2026-11-01T06:30Z"
    SL.mark_done(a["key"], 1, now=t0)
    b = SL.gate("bulletin", "2026-11-01T06:30Z", now=t0 + timedelta(minutes=3))  # Cloudflare Worker backup, with slot
    c = SL.gate("bulletin", "", now=t0 + timedelta(hours=3))                     # GitHub schedule, 3 h late
    assert b["run"] == "false" and "already done" in b["reason"] and c["run"] == "false"
    d = SL.gate("bulletin", "", now=t0 + timedelta(hours=7))                     # 7 h late: stale, never airs
    assert d["run"] == "false" and "stale" in d["reason"]


def test_failed_first_clock_leaves_the_slot_to_the_backup(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    t0 = datetime(2026, 11, 1, 11, 41, tzinfo=timezone.utc)
    assert SL.gate("reel", "", now=t0)["run"] == "true"                          # run 1 fails: nothing marked
    b = SL.gate("reel", "2026-11-01T11:41:00Z", now=t0 + timedelta(minutes=3))
    assert b["run"] == "true" and b["mode"] == "single"


def test_reel_mode_auto_from_slot_and_explicit_input_wins(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    t = datetime(2026, 11, 1, 8, 11, tzinfo=timezone.utc)
    assert SL.gate("reel", "", now=t)["mode"] == "edition"
    assert SL.gate("reel", "", mode="single", now=t)["mode"] == "single"
    assert SL.gate("reel", "", now=t + timedelta(hours=3, minutes=30))["mode"] == "single"   # 11:41 slot


def test_test_runs_are_not_gated(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    SL.mark_done("bulletin@2026-11-01T06:30Z")
    r = SL.gate("bulletin", "", test=True, now=datetime(2026, 11, 1, 6, 31, tzinfo=timezone.utc))
    assert r["run"] == "true" and r["key"] == ""


def test_every_scheduled_workflow_is_gated_and_dispatchable_with_a_bare_body():
    import yaml
    for wf in ("publish", "newsroom", "watchdog", "reel", "bulletin"):
        d = yaml.safe_load((ROOT / ".github" / "workflows" / f"{wf}.yml").read_text())
        on = d[True]
        assert "schedule" in on and "workflow_dispatch" in on and "slot" in on["workflow_dispatch"]["inputs"]
        assert d["concurrency"]["group"] == wf and d["concurrency"]["cancel-in-progress"] is False
        main = [j for n, j in d["jobs"].items() if n != "gate"][0]
        assert main["needs"] == "gate" and "needs.gate.outputs.run == 'true'" in main["if"]
        assert any("slots done" in (st.get("run") or "") for st in main["steps"])
        for k, v in on["workflow_dispatch"]["inputs"].items():                 # {"ref":"main"} = production behaviour
            assert not (k == "dry_run" and v.get("default") is True), f"{wf}: bare dispatch would be a dry run"
    assert yaml.safe_load((ROOT / ".github/workflows/reel.yml").read_text())[True]["workflow_dispatch"]["inputs"]["mode"]["default"] == "auto"


def test_slot_markers_are_per_workflow_files(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)                  # run 41190343334: one shared file -> rebase conflict between workflows
    SL.mark_done("publish@2026-11-01T10:47Z")
    SL.mark_done("watchdog@2026-11-01T09:33Z")
    files = sorted(p.name for p in (tmp_path / "state" / "slots").glob("*.json"))
    assert files == ["publish.json", "watchdog.json"]
    assert SL.is_done("publish@2026-11-01T10:47Z") and not SL.is_done("publish@2026-11-01T11:07Z")
    for wf in ("publish", "newsroom", "watchdog", "reel", "bulletin"):
        t = (ROOT / ".github" / "workflows" / f"{wf}.yml").read_text()
        assert "git reset -q --hard origin/main" in t and "git add state/slots" in t
