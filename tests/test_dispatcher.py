import json, os, tempfile, importlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def setup(tmp):
    os.environ.update(PUBLISHING_ENABLED="true", KAHWA_ROOT=tmp, QUIET_HOURS_UTC="25-25", MIN_GAP_MINUTES="0", JITTER_MINUTES="0")
    import kahwa_publisher.providers as P, kahwa_publisher.dispatcher as D
    importlib.reload(P); importlib.reload(D)
    (Path(tmp)/"queue").mkdir(exist_ok=True)
    (Path(tmp)/"queue"/"a.json").write_text(json.dumps({"id":"a","text":"t","media":[]}))
    return P, D

class Fake:
    def __init__(s, name, status): s.name, s.status, s.calls = name, status, 0
    def configured(s): return True
    def publish(s, post):
        s.calls += 1
        import kahwa_publisher.providers as P
        return P.Result(s.status, s.name, "x", "rid")
    def check(s, rid):
        import kahwa_publisher.providers as P
        return P.Result(P.OK, s.name, "", rid)

def run(statuses):
    with tempfile.TemporaryDirectory() as tmp:
        P, D = setup(tmp)
        fakes = [Fake(f"p{i}", s) for i, s in enumerate(statuses)]
        P.all_providers = lambda: fakes
        rc = D.run()
        st = json.loads((Path(tmp)/"state"/"state.json").read_text())
        return rc, fakes, st, sorted(p.name for p in (Path(tmp)/"queue").rglob("*.json"))

def test_failover_to_second():
    rc, f, st, files = run(["transient", "ok"])
    assert rc == 0 and f[1].calls == 1 and st["published"][0]["provider"] == "p1"

def test_fb_block_pauses_all_and_stops():
    rc, f, st, _ = run(["fb_block", "ok"])
    assert f[1].calls == 0 and st["global_pause_until"]

def test_uncertain_no_failover():
    rc, f, st, _ = run(["uncertain", "ok"])
    assert f[1].calls == 0 and not st["published"]

def test_auth_alert_and_failover():
    rc, f, st, _ = run(["auth", "ok"])
    assert f[1].calls == 1 and st["alerts"]

def test_classify():
    import kahwa_publisher.providers as P
    assert P.classify_text("(#368) Confirm your identity") == P.FB_BLOCK
    assert P.classify_text("Error validating access token: Session has expired") == P.AUTH
