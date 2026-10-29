"""LLM router (kahwa_publisher/llm.py): task chains, family rule, quota state, circuit breaker, JSON repair, quality gate."""
import json

import pytest

from kahwa_publisher import llm as R

GOOD_AR = "أعلنت الحكومة الإسبانية اليوم خطة جديدة لدعم المزارعين المتضررين من الجفاف، وتشمل مساعدات مالية مباشرة وقروضاً ميسرة."


class Fake:
    """Scripted provider: answers[model] = list of str (answer) or R.ApiError (raised), consumed in order."""

    def __init__(self, name, answers=None, configured=True, day_tz="UTC"):
        self.name, self.answers, self._c, self.day_tz, self.calls = name, answers or {}, configured, day_tz, []

    def configured(self):
        return self._c

    def call(self, model, prompt, temperature, max_out=None, timeout=120):
        self.calls.append(model)
        q = self.answers.get(model) or ['{"ok": true}']
        a = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(a, Exception):
            raise a
        return a, {"in": 100, "out": 50}


def mk(tmp_path, providers, name="t.json"):
    return R.Router(providers=providers, state_file=tmp_path / name, state_dir=tmp_path, log=lambda d: None,
                    sleep=lambda s: None)


def provs(**kw):
    base = {n: Fake(n, configured=False) for n in ("gemini", "groq", "sambanova", "openrouter", "cloudflare")}
    base.update(kw)
    return base


def test_task_chains_split():
    assert R.CHAINS["writer"][:5] == ["gemini:gemini-3.8-flash", "gemini:gemini-3.6-flash", "gemini:gemini-3.1-flash-lite",
                                      "gemini:gemma-4-31b-it", "gemini:gemini-3.5-flash-lite"]
    assert R.CHAINS["classifier"][0] == "gemini:gemma-4-26b-a4b-it"
    fc = R.CHAINS["factcheck"]
    assert fc[0] == "groq:qwen/qwen3.8-27b" and fc[1].startswith("sambanova:DeepSeek") and "openrouter" in fc[3]
    assert not any("llama-3.3" in s for c in R.CHAINS.values() for s in c)          # dead defaults gone
    assert all("gemini-3.8-flash" not in s and "gemini-3.6-flash" not in s for s in R.CHAINS["classifier"] + R.CHAINS["utility"])


def test_families():
    assert R.family("gemma-4-31b-it") == R.family("gemini-3.8-flash") == "google"
    assert R.family("qwen/qwen3.8-27b") == "qwen" and R.family("DeepSeek-V3.2") == "deepseek"
    assert R.family("nvidia/nemotron-3-ultra-550b-a55b:free") == "nvidia" and R.family("openai/gpt-oss-120b") == "openai"


def test_classifier_uses_gemma_first(tmp_path):
    g = Fake("gemini", day_tz="America/Los_Angeles")
    r = mk(tmp_path, provs(gemini=g))
    text, meta = r.complete("classifier", "x", schema={"ok": bool})
    assert meta.model == "gemma-4-26b-a4b-it" and json.loads(text) == {"ok": True}


def test_factcheck_never_same_family_hold_when_only_google(tmp_path):
    r = mk(tmp_path, provs(gemini=Fake("gemini")))
    ok, why = r.preflight()
    assert not ok and "independent" in why
    with pytest.raises(R.Hold):
        r.complete("factcheck", "x", exclude_families={"google"})


def test_factcheck_prefers_groq_qwen_and_bound_excludes_writer(tmp_path):
    g, q = Fake("gemini"), Fake("groq")
    r = mk(tmp_path, provs(gemini=g, groq=q))
    assert r.preflight()[0]
    w = r.bound("writer")
    w("اكتب")
    c = r.bound("factcheck", exclude_from=w)
    c("تحقق")
    assert q.calls == ["qwen/qwen3.8-27b"] and c.families == {"qwen"}


def test_missing_key_provider_skipped(tmp_path):
    s = Fake("sambanova")
    r = mk(tmp_path, provs(gemini=Fake("gemini"), sambanova=s))       # groq has no key
    _t, meta = r.complete("factcheck", "x", exclude_families={"google"})
    assert meta.provider == "sambanova" and meta.model == "DeepSeek-V3.2"


def test_daily_429_persisted_and_next_model(tmp_path):
    g = Fake("gemini", {"gemini-3.8-flash": [R.ApiError("daily", "PerDay", limit=20)]})
    r = mk(tmp_path, provs(gemini=g))
    _t, meta = r.complete("writer", "x")
    assert meta.model == "gemini-3.6-flash"
    r2 = mk(tmp_path, provs(gemini=Fake("gemini")))                   # next run reads the committed state
    assert r2.why_not("gemini", "gemini-3.8-flash") == "daily quota exhausted"


def test_dead_model_circuit_breaker(tmp_path):
    g = Fake("gemini", {"gemini-3.8-flash": [R.ApiError("dead", "404 not found")]})
    r = mk(tmp_path, provs(gemini=g))
    r.complete("writer", "x")
    assert "dead" in mk(tmp_path, provs(gemini=Fake("gemini"))).why_not("gemini", "gemini-3.8-flash")


def test_auth_disables_provider_for_run(tmp_path):
    q = Fake("groq", {"qwen/qwen3.8-27b": [R.ApiError("auth", "401")]})
    s = Fake("sambanova")
    r = mk(tmp_path, provs(groq=q, sambanova=s))
    _t, meta = r.complete("factcheck", "x")
    assert meta.provider == "sambanova" and r.why_not("groq", "openai/gpt-oss-120b").startswith("provider off")


def test_per_run_budget_and_daily_budget(tmp_path):
    r = mk(tmp_path, provs(sambanova=Fake("sambanova")))
    cap = max(2, -(-20 * 1.5 // 11))                                  # rpd 20 over 11 runs, burst 1.5 -> 3
    for _ in range(int(cap)):
        assert r.complete("factcheck", "x")[1].model == "DeepSeek-V3.2"
    assert r.complete("factcheck", "x")[1].model == "DeepSeek-V3.1"   # per-run budget of V3.2 used
    st = json.loads((tmp_path / "t.json").read_text())
    assert st["models"]["sambanova/DeepSeek-V3.2"]["req"] == cap
    # another workflow's file counts too: day budget shared
    other = {"models": {"sambanova/DeepSeek-V3.1": {"day": R.day_key("UTC"), "req": 20, "tok": 0}}}
    (tmp_path / "llm_quota-reel.json").write_text(json.dumps(other))
    r2 = mk(tmp_path, provs(sambanova=Fake("sambanova")))
    assert "daily budget" in r2.why_not("sambanova", "DeepSeek-V3.1")


def test_openrouter_provider_daily_limit(tmp_path):
    st = {"providers": {"openrouter": {"day": R.day_key("UTC"), "req": 50, "tok": 0}}}
    (tmp_path / "t.json").write_text(json.dumps(st))
    r = mk(tmp_path, provs(openrouter=Fake("openrouter")))
    assert "provider daily budget" in r.why_not("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free")


def test_json_repair_once_then_next_model(tmp_path):
    g = Fake("gemini", {"gemini-3.8-flash": ["not json", '{"ok": true}']})
    r = mk(tmp_path, provs(gemini=g))
    _t, meta = r.complete("writer", "x", schema={"ok": bool})
    assert meta.model == "gemini-3.8-flash" and g.calls == ["gemini-3.8-flash", "gemini-3.8-flash"]
    g2 = Fake("gemini", {"gemini-3.8-flash": ["bad", "still bad"]})
    r2 = mk(tmp_path, provs(gemini=g2), "u.json")
    assert r2.complete("writer", "x", schema={"ok": bool})[1].model == "gemini-3.6-flash"


def test_schema_types_enforced():
    assert R.parse_json('```json\n{"ok": "yes"}\n```', {"ok": bool})[1]
    assert R.parse_json('<think>hmm</think>{"ok": false}', {"ok": bool}) == ({"ok": False}, None)


def test_quality_reject_moves_on(tmp_path):
    bad = json.dumps({"safe": True, "post": "这是中文 " * 30})
    good = json.dumps({"safe": True, "post": GOOD_AR})
    g = Fake("gemini", {"gemini-3.8-flash": [bad], "gemini-3.6-flash": [good]})
    r = mk(tmp_path, provs(gemini=g))
    _t, meta = r.complete("writer", "x", validate=lambda d: R.arabic_quality(d["post"]))
    assert meta.model == "gemini-3.6-flash"


def test_arabic_quality():
    assert R.arabic_quality(GOOD_AR) == []
    assert R.arabic_quality(GOOD_AR + " 中文")
    assert R.arabic_quality("The government announced a new plan for farmers affected by the drought today and more")
    assert R.arabic_quality(GOOD_AR + " جديدة جديدة جديدة جديدة")
    assert R.arabic_quality("قصير")
    assert R.arabic_quality(GOOD_AR + " بلغ العدد 1000000 شخص") == []


def test_minute_limit_retries_then_succeeds(tmp_path):
    q = Fake("groq", {"qwen/qwen3.8-27b": [R.ApiError("minute", "OTPM", retry=2), '{"ok": true}']})
    r = mk(tmp_path, provs(groq=q))
    assert r.complete("factcheck", "x")[1].model == "qwen/qwen3.8-27b"


def test_classify_error_shapes():
    gem = '{"error":{"code":429,"details":[{"violations":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier","quotaValue":"20"}]},{"retryDelay":"41s"}]}}'
    e = R.classify_error(429, gem)
    assert e.kind == "daily" and e.limit == 20
    assert R.classify_error(429, "Rate limit reached on output tokens per minute (OTPM). Please try again in 960ms").kind == "minute"
    assert R.classify_error(429, "Rate limit reached on requests per day (RPD)").kind == "daily"
    assert R.classify_error(404, "model not found").kind == "dead"
    assert R.classify_error(400, '{"message":"The model `x` has been decommissioned"}').kind == "dead"
    assert R.classify_error(401, "Invalid API Key").kind == "auth"
    assert R.classify_error(410, "DeepSeek-V3.2-16k is currently experiencing high demand. Please try again later!").kind == "transient"


def test_verify_text_reraises_hold():
    from kahwa_publisher import verify

    def no_checker(prompt, temperature=0):
        raise R.Hold("factcheck: none")
    with pytest.raises(R.Hold):
        verify.verify_text(no_checker, GOOD_AR, ["[م1] " + GOOD_AR], log=lambda *a: None)


def test_newsroom_write_post_holds_without_independent_checker(tmp_path, monkeypatch):
    from kahwa_publisher import newsroom as N
    post = {"safe": True, "post": GOOD_AR + "\n#الجفاف", "headline": "خطة لدعم المزارعين", "category": "اقتصاد"}
    g = Fake("gemini", {m: [json.dumps(post, ensure_ascii=False)] for m in ("gemini-3.8-flash",)})
    R.reset(mk(tmp_path, provs(gemini=g)))
    try:
        c = {"items": [{"title": "خطة إسبانية لدعم المزارعين", "summary": GOOD_AR}]}
        with pytest.raises(R.Hold):
            N.write_post(c)
    finally:
        R.reset(None)


def test_registered_schemas_for_pipeline_prompts():
    from kahwa_publisher import newsroom as N, verify, safety  # noqa: F401  (imports register the prompts)
    assert R._lookup(N.PROMPT + "x")[0] == {"safe": bool}
    assert R._lookup(verify.DECOMPOSE + "x")[0] == {"claims": list}
    assert R._lookup(safety.CLASSIFY + "x")[0] == {"stories": list}


def test_pacing_shrinks_output_budget_then_waits(tmp_path):
    waits = []
    r = R.Router(providers=provs(groq=Fake("groq")), state_file=tmp_path / "p.json", state_dir=tmp_path,
                 log=lambda d: None, sleep=lambda s: waits.append(s))
    key = "groq/qwen/qwen3.8-27b"
    import time as _t
    r.window[key] = [(_t.time(), 500, 300)]
    assert r._pace(key, 100, 1000) == 700 and not waits          # 1000 - 300 left in the minute
    r.window[key] = [(_t.time(), 900, 600)]
    assert r._pace(key, 100, 1000) == 1000 and waits             # <500 left: waited for the window
