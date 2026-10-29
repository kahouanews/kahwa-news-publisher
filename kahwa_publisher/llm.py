"""Free-LLM router for the Kahwa newsroom (2026-10-29).

Why: every task used to run through Gemini 3.8 / 3.6 Flash (~20 requests/day each on the free tier) -> daily 429s,
and the old fallbacks were dead (Groq llama-3.3-70b-versatile, OpenRouter llama-3.3 :free, GitHub Models).

Tasks and their own model chains ("provider:model", first = preferred; override with env LLM_CHAIN_<TASK>):
  writer      original Arabic post / reel script: gemini-3.8-flash -> 3.6-flash -> 3.1-flash-lite -> gemma-4-31b -> 3.5-flash-lite
  classifier  selection / category check:        gemma-4-26b-a4b -> 3.5-flash-lite -> gemma-4-31b -> 3.1-flash-lite
  utility     line shortening etc.:              gemma-4-31b -> gemma-4-26b-a4b -> 3.1-flash-lite -> 3.5-flash-lite
  factcheck   fact-check / claim verification: MUST be a different model family than the writer.
              Groq qwen3.8-27b -> SambaNova DeepSeek-V3.2 / V3.1 -> OpenRouter free (NVIDIA Nemotron) -> Groq gpt-oss-120b
              -> Google models (usable only when the writer was not Google: Gemma is Google too).
              No independent checker with a key + quota -> Hold: the story is NOT published and NOT marked seen; the next
              run tries again. Never publish unverified.

Router guarantees
  * providers without a key are skipped cleanly (GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, SAMBANOVA_API_KEY,
    CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID).
  * persistent per-provider / per-model quota tracking: state/llm_quota-<workflow>.json (env LLM_QUOTA_FILE; committed by
    the bot). Each workflow writes only its own file (no git conflicts); usage is summed over all state/llm_quota*.json.
    Days follow each provider's reset clock (Gemini: midnight Pacific; others: UTC).
  * per-run budget split: a model with a daily limit gets at most ceil(limit / LLM_RUNS_PER_DAY * LLM_BURST) calls per run,
    so one run can never drain a day's quota (premium writer models are only in the writer chain).
  * pacing for per-minute token limits (Groq free: 8K TPM, 1K output TPM on qwen; Gemma ~15K TPM).
  * circuit breaker: 404 / deprecated / "not available" models are marked dead for 3 days (persisted), a daily 429 marks the
    model exhausted until its day resets (persisted, learned limit kept), 401/403 disables the provider for the run,
    3 transient failures in a row rest the model for the run.
  * strict JSON: every answer must be one JSON object (+ the registered schema of the prompt); one repair try on the same
    model, otherwise the next model.
  * cheap Arabic quality check (code, no LLM) on writer / repair outputs before anything reaches the fact-checker.
  * all models exhausted -> Hold (a RuntimeError): callers skip / hold and the workflow still exits 0.

python -m kahwa_publisher.llm --list-models | --probe a,b | --status
"""
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("KAHWA_ROOT", Path(__file__).resolve().parent.parent))

CHAINS = {
    "writer": ["gemini:gemini-3.8-flash", "gemini:gemini-3.6-flash", "gemini:gemini-3.1-flash-lite",
               "gemini:gemma-4-31b-it", "gemini:gemini-3.5-flash-lite"],
    "classifier": ["gemini:gemma-4-26b-a4b-it", "gemini:gemini-3.5-flash-lite", "gemini:gemma-4-31b-it",
                   "gemini:gemini-3.1-flash-lite"],
    "utility": ["gemini:gemma-4-31b-it", "gemini:gemma-4-26b-a4b-it", "gemini:gemini-3.1-flash-lite",
                "gemini:gemini-3.5-flash-lite"],
    "factcheck": ["groq:qwen/qwen3.8-27b", "sambanova:DeepSeek-V3.2", "sambanova:DeepSeek-V3.1",
                  "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter:nvidia/nemotron-3.5-super-vl:free",
                  "groq:openai/gpt-oss-120b", "cloudflare:@cf/qwen/qwen3.8-27b",
                  "gemini:gemma-4-31b-it", "gemini:gemini-3.1-flash-lite"],
}

# Free-tier limits (Oct 2026). rpd = requests/day, tpd = tokens/day, tpm = tokens/min (in+out), otpm = output tokens/min,
# max_out = max_tokens sent. None = unknown (learned from 429 details when the provider sends them).
LIMITS = {
    "gemini/gemini-3.8-flash": {"rpd": 20},
    "gemini/gemini-3.6-flash": {"rpd": 20},
    "gemini/gemini-3.1-flash-lite": {"rpd": 500},
    "gemini/gemini-3.5-flash-lite": {"rpd": None},
    "gemini/gemma-4-31b-it": {"rpd": 14400, "tpm": 15000},
    "gemini/gemma-4-26b-a4b-it": {"rpd": 14400, "tpm": 15000},
    "groq/qwen/qwen3.8-27b": {"rpd": 1000, "tpd": 200000, "tpm": 8000, "otpm": 1000, "max_out": 1000},
    "groq/openai/gpt-oss-120b": {"rpd": 1000, "tpd": 200000, "tpm": 8000, "max_out": 3000},
    "sambanova/DeepSeek-V3.2": {"rpd": 20, "tpd": 200000, "max_out": 3000},
    "sambanova/DeepSeek-V3.1": {"rpd": 20, "tpd": 200000, "max_out": 3000},
}
PROVIDER_LIMITS = {"openrouter": {"rpd": 50}}           # no credits: 50 :free requests/day for the whole account
EXTRA = {   # provider-specific request options (dropped automatically if the API rejects them)
    "groq/qwen/qwen3.8-27b": {"reasoning_effort": "none"},
    "groq/openai/gpt-oss-120b": {"reasoning_effort": "low"},
}
DEAD_DAYS = 3


class Hold(RuntimeError):
    """No allowed model with a key and quota for this task: hold the work for the next run (never publish unverified)."""


class ApiError(Exception):
    def __init__(self, kind, msg, retry=None, limit=None):
        super().__init__(msg)
        self.kind, self.retry, self.limit = kind, retry, limit   # kind: dead|auth|daily|minute|transient|bad|blocked


def family(model):
    m = model.lower()
    for key, fam in (("gemma", "google"), ("gemini", "google"), ("deepseek", "deepseek"), ("qwen", "qwen"),
                     ("qwq", "qwen"), ("nemotron", "nvidia"), ("gpt-oss", "openai"), ("llama", "meta"),
                     ("mistral", "mistral"), ("glm", "zhipu"), ("kimi", "moonshot"), ("minimax", "minimax")):
        if key in m:
            return fam
    return m.split("/")[0]


# ---------------------------------------------------------------- strict JSON + schemas + Arabic quality
_SCHEMAS = []   # (prompt prefix, {key: type}, validator or None)


def register(prefix, schema=None, validator=None):
    """Attach a schema / quality validator to every call whose prompt starts with `prefix` (keeps callers unchanged)."""
    _SCHEMAS.append((prefix[:80], schema or {}, validator))


def _lookup(prompt):
    for pre, sch, val in _SCHEMAS:
        if prompt.startswith(pre):
            return sch, val
    return {}, None


def parse_json(text, schema=None):
    """-> (obj, None) or (None, error). One JSON object; think blocks / code fences tolerated; schema keys + types enforced."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None, "no JSON object"
    try:
        obj = json.loads(t[a:b + 1])
    except ValueError as ex:
        return None, f"invalid JSON: {str(ex)[:80]}"
    if not isinstance(obj, dict):
        return None, "not an object"
    for k, typ in (schema or {}).items():
        if k not in obj:
            return None, f"missing key {k}"
        if typ is not None and not isinstance(obj[k], typ):
            return None, f"key {k} has type {type(obj[k]).__name__}"
    return obj, None


_AR = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_FOREIGN = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF\u0590-\u05FF\u0E00-\u0E7F\u0900-\u097F]")
REFUSALS = ("as an ai", "i cannot", "i can't", "كنموذج لغوي", "بصفتي نموذج", "لا أستطيع المساعدة", "عذراً، لا يمكنني")


def arabic_quality(text, min_len=60):
    """Cheap code-only check of an Arabic text before it is fact-checked. Returns a list of problems ([] = fine)."""
    t = text or ""
    probs = []
    if len(t.strip()) < min_len:
        probs.append("too short")
    letters = [c for c in t if c.isalpha()]
    if letters:
        ratio = sum(bool(_AR.match(c)) for c in letters) / len(letters)
        if ratio < 0.75:
            probs.append(f"arabic ratio {ratio:.2f}")
    if _FOREIGN.search(t):
        probs.append("foreign script (CJK/Cyrillic/Hebrew/...)")
    if "\ufffd" in t or "<think" in t or "```" in t:
        probs.append("artifact")
    if re.search(r"(?<!\S)(\S+)(?:\s+\1){3,}(?!\S)", t):
        probs.append("repeated word")
    if re.search(r"([^\d\s.,٠-٩])\1{5,}", t):
        probs.append("repeated character")
    if len(re.findall(r"[A-Za-z]{3,}", t)) > 6:
        probs.append("too much Latin text")
    low = t.lower()
    if any(r in low for r in REFUSALS):
        probs.append("refusal / meta text")
    return probs


# ---------------------------------------------------------------- providers
def _http(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "kahwa-news/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})
    except Exception as e:  # timeout / network
        raise ApiError("transient", f"network: {str(e)[:120]}")


DEAD_RE = re.compile(r"not[ _]found|no longer available|deprecated|decommission|does not exist|is not supported for generatecontent|"
                     r"not available on the workers free plan|model_not_found|invalid model|unknown model|no endpoints found", re.I)
DAILY_RE = re.compile(r"per ?day|PerDay|daily|RPD|TPD|free-models-per-day|tokens per day|neurons", re.I)


def classify_error(code, raw, headers=None):
    h = {k.lower(): v for k, v in (headers or {}).items()}
    retry = None
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', raw) or re.search(r"try again in (\d+(?:\.\d+)?)(m?s)", raw)
    if m:
        retry = float(m.group(1)) / (1000 if len(m.groups()) > 1 and m.group(2) == "ms" else 1)
    elif h.get("retry-after", "").replace(".", "").isdigit():
        retry = float(h["retry-after"])
    limit = None
    lm = re.search(r'"quotaValue":\s*"(\d+)"', raw)
    if lm:
        limit = int(lm.group(1))
    if re.search(r"high demand|overloaded|try again later", raw, re.I) and not DAILY_RE.search(raw):
        return ApiError("transient", f"HTTP {code}: {raw[:200]}", retry or 6)
    if code == 404 or (code in (400, 403) and DEAD_RE.search(raw)):
        return ApiError("dead", f"HTTP {code}: {raw[:200]}")
    if code == 429:
        if DAILY_RE.search(raw):
            return ApiError("daily", f"HTTP 429 daily: {raw[:200]}", retry, limit)
        return ApiError("minute", f"HTTP 429: {raw[:200]}", retry or 20, limit)
    if code in (401, 403) or (code == 400 and re.search(r"api key not valid|API_KEY_INVALID|location is not supported", raw, re.I)):
        return ApiError("auth", f"HTTP {code}: {raw[:200]}")
    if code >= 500 or code in (408, 409) or re.search(r"high demand|overloaded|try again later", raw, re.I):
        return ApiError("transient", f"HTTP {code}: {raw[:200]}", retry)
    return ApiError("bad", f"HTTP {code}: {raw[:200]}")


class Gemini:
    name, day_tz = "gemini", "America/Los_Angeles"
    base = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.key = os.getenv("GEMINI_API_KEY", "")
        self.no_json, self.no_think = set(), set()
        self.gemma_thinking = os.getenv("LLM_GEMMA_THINKING", "minimal")   # Gemma 4 thinks by default: 100+ s per call

    def configured(self):
        return bool(self.key)

    def call(self, model, prompt, temperature, max_out=None, timeout=120):
        gen = {"temperature": temperature}
        if max_out:
            gen["maxOutputTokens"] = max_out
        if not model.startswith("gemma") and model not in self.no_json:
            gen["responseMimeType"] = "application/json"   # Gemma on the Gemini API has no JSON mode
        if model.startswith("gemma") and self.gemma_thinking and model not in self.no_think:
            gen["thinkingConfig"] = {"thinkingLevel": self.gemma_thinking}
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
        code, raw, hdr = _http(f"{self.base}/models/{model}:generateContent", body, {"x-goog-api-key": self.key},
                               max(timeout, 180) if model.startswith("gemma") else timeout)
        if code == 400 and "thinkingConfig" in gen and re.search(r"think", raw, re.I):
            self.no_think.add(model)                       # thinking control not supported: plain call
            return self.call(model, prompt, temperature, max_out, timeout)
        if code == 400 and "responseMimeType" in gen and re.search(r"json mode|response_mime_type|responseMimeType", raw, re.I):
            self.no_json.add(model)
            return self.call(model, prompt, temperature, max_out, timeout)
        if code != 200:
            raise classify_error(code, raw, hdr)
        r = json.loads(raw)
        cands = r.get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise ApiError("blocked", f"empty answer: {json.dumps(r.get('promptFeedback') or (cands[0].get('finishReason') if cands else ''))[:160]}")
        um = r.get("usageMetadata") or {}
        return text, {"in": um.get("promptTokenCount") or 0,
                      "out": (um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0)}

    def list_models(self):
        req = urllib.request.Request(f"{self.base}/models?pageSize=1000", headers={"x-goog-api-key": self.key})
        return json.loads(urllib.request.urlopen(req, timeout=60).read()).get("models") or []


class OpenAICompat:
    day_tz = "UTC"

    def __init__(self, name, base, key_env, extra_env=None):
        self.name, self.base, self.key = name, base, os.getenv(key_env, "")
        self.drop_extra = set()

    def configured(self):
        return bool(self.key and self.base)

    def call(self, model, prompt, temperature, max_out=None, timeout=120):
        body = {"model": model, "temperature": temperature, "messages": [{"role": "user", "content": prompt}]}
        key = f"{self.name}/{model}"
        if key not in self.drop_extra:
            body["response_format"] = {"type": "json_object"}
            body.update(EXTRA.get(key, {}))
        if max_out:
            body["max_tokens"] = max_out
        code, raw, hdr = _http(self.base + "/chat/completions", body, {"Authorization": f"Bearer {self.key}"}, timeout)
        if code == 400 and key not in self.drop_extra and not DEAD_RE.search(raw):
            self.drop_extra.add(key)                      # unsupported option (response_format / reasoning): retry plain
            return self.call(model, prompt, temperature, max_out, timeout)
        if code != 200:
            raise classify_error(code, raw, hdr)
        r = json.loads(raw)
        if r.get("error"):
            raise classify_error(int(r["error"].get("code") or 500) if str(r["error"].get("code") or "").isdigit() else 500,
                                 json.dumps(r["error"]), hdr)
        ch = (r.get("choices") or [{}])[0]
        text = ((ch.get("message") or {}).get("content") or "")
        if not text.strip():
            raise ApiError("blocked", f"empty answer ({ch.get('finish_reason')})")
        u = r.get("usage") or {}
        return text, {"in": u.get("prompt_tokens") or 0, "out": u.get("completion_tokens") or 0}


def make_providers():
    acct = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cf = OpenAICompat("cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1" if acct else "",
                      "CLOUDFLARE_API_TOKEN")
    return {"gemini": Gemini(),
            "groq": OpenAICompat("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
            "sambanova": OpenAICompat("sambanova", "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY"),
            "openrouter": OpenAICompat("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
            "cloudflare": cf}


# ---------------------------------------------------------------- quota state
def day_key(tz):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
    except Exception:
        off = -8 * 3600 if tz != "UTC" else 0
        return datetime.fromtimestamp(time.time() + off, timezone.utc).strftime("%Y-%m-%d")


class Meta:
    def __init__(self, provider, model):
        self.provider, self.model, self.family = provider, model, family(model)

    def __repr__(self):
        return f"{self.provider}/{self.model}"


class Router:
    def __init__(self, providers=None, state_file=None, state_dir=None, log=None, sleep=time.sleep):
        self.p = providers if providers is not None else make_providers()
        self.state_dir = Path(state_dir or ROOT / "state")
        self.file = Path(state_file or os.getenv("LLM_QUOTA_FILE") or self.state_dir / "llm_quota-local.json")
        self.log = log or (lambda d: print(json.dumps(d, ensure_ascii=False), flush=True))
        self.sleep = sleep
        self.runs_per_day = float(os.getenv("LLM_RUNS_PER_DAY", "11"))
        self.burst = float(os.getenv("LLM_BURST", "1.5"))
        self.run_req, self.run_dead, self.run_off, self.window = {}, {}, {}, {}
        self.mine = self._read(self.file)
        self.others = [self._read(f) for f in sorted(self.state_dir.glob("llm_quota*.json")) if f.resolve() != self.file.resolve()]
        self.notes = set()

    # -- state
    @staticmethod
    def _read(path):
        try:
            d = json.loads(Path(path).read_text())
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self):
        try:
            self.mine["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.mine, ensure_ascii=False, indent=1, sort_keys=True))
            os.replace(tmp, self.file)
        except Exception as ex:
            self.log({"llm_state_save_error": str(ex)[:120]})

    def _rec(self, section, key, day):
        s = self.mine.setdefault(section, {}).setdefault(key, {})
        if s.get("day") != day:
            s.update(day=day, req=0, tok=0)
            s.pop("exhausted", None)
        return s

    def _all(self, section, key):
        return [d.get(section, {}).get(key) or {} for d in [self.mine] + self.others]

    def used(self, section, key, day):
        recs = [r for r in self._all(section, key) if r.get("day") == day]
        return sum(r.get("req", 0) for r in recs), sum(r.get("tok", 0) for r in recs), any(r.get("exhausted") for r in recs)

    def dead_until(self, key):
        return max([r.get("dead_until", 0) for r in self._all("models", key)] + [0])

    def limit(self, key, field):
        learned = [r.get("learned_" + field) for r in self._all("models", key) if r.get("learned_" + field)]
        return (LIMITS.get(key) or {}).get(field) or (min(learned) if learned else None)

    # -- availability
    def why_not(self, prov, model):
        key = f"{prov}/{model}"
        p = self.p.get(prov)
        if not p or not p.configured():
            return "no key"
        if prov in self.run_off:
            return "provider off this run: " + self.run_off[prov]
        if key in self.run_dead:
            return "resting this run: " + self.run_dead[key]
        if self.dead_until(key) > time.time():
            return "dead (circuit breaker)"
        day = day_key(p.day_tz)
        req, tok, exhausted = self.used("models", key, day)
        if exhausted:
            return "daily quota exhausted"
        rpd, tpd = self.limit(key, "rpd"), self.limit(key, "tpd")
        if rpd and req >= rpd:
            return f"daily budget used ({req}/{rpd})"
        if tpd and tok >= tpd * 0.95:
            return f"daily tokens used ({tok}/{tpd})"
        if rpd:
            cap = max(2, math.ceil(rpd / self.runs_per_day * self.burst))
            if self.run_req.get(key, 0) >= cap:
                return f"per-run budget used ({cap})"
        pl = PROVIDER_LIMITS.get(prov)
        if pl:
            preq, _t, pex = self.used("providers", prov, day)
            if pex or preq >= pl["rpd"]:
                return f"provider daily budget used ({preq}/{pl['rpd']})"
            if self.run_req.get(prov, 0) >= max(2, math.ceil(pl["rpd"] / self.runs_per_day * self.burst)):
                return "provider per-run budget used"
        return None

    def chain(self, task):
        env = os.getenv(f"LLM_CHAIN_{task.upper()}")
        specs = [s.strip() for s in env.split(",") if s.strip()] if env else CHAINS[task]
        out = []
        for s in specs:
            prov, _, model = s.partition(":") if ":" in s else ("gemini", "", s)
            out.append((prov, model))
        return out

    def available(self, task, exclude_families=()):
        return [(p, m) for p, m in self.chain(task) if family(m) not in set(exclude_families) and self.why_not(p, m) is None]

    def preflight(self, writer_task="writer", checker_task="factcheck"):
        """(ok, reason): a writer with quota AND a fact-checker from another family with a key + quota must exist."""
        w = self.available(writer_task)
        if not w:
            return False, f"no {writer_task} model has a key + quota"
        fams = {family(m) for _p, m in w}
        c = self.available(checker_task, exclude_families=fams)
        if not c:
            return False, f"no independent fact-checker (outside {sorted(fams)}) has a key + quota"
        return True, f"writer {w[0][0]}/{w[0][1]}, checker {c[0][0]}/{c[0][1]}"

    # -- pacing + accounting
    def _pace(self, key, est_in, max_out):
        """Wait for per-minute token limits (in-run sliding window). Returns the max_tokens to send: on output-token
        limited models (Groq qwen: 1K output tokens/min, max_tokens is reserved up front) it is shrunk to what is left
        in the window, down to 500, before waiting."""
        lim = LIMITS.get(key) or {}
        while True:
            win = [x for x in self.window.get(key, []) if x[0] > time.time() - 60]
            self.window[key] = win
            wait_field = None
            out = max_out
            if lim.get("otpm") and win:
                avail = lim["otpm"] - sum(x[2] for x in win)
                if avail >= 500:
                    out = min(max_out or avail, avail)
                else:
                    wait_field = "otpm"
            if not wait_field and lim.get("tpm") and win and sum(x[1] for x in win) + est_in + (out or 1500) > lim["tpm"]:
                wait_field = "tpm"
            if not wait_field:
                return out
            wait = max(1.0, win[0][0] + 60.5 - time.time())
            self.log({"llm_pace": key, "field": wait_field, "wait_s": round(wait, 1)})
            self.sleep(min(wait, 61))
            if self.sleep is not time.sleep:          # tests: fake clock, drop the oldest entry
                self.window[key] = win[1:]

    def _account(self, prov, key, usage, max_out):
        day = day_key(self.p[prov].day_tz)
        r = self._rec("models", key, day)
        r["req"] += 1
        r["tok"] += int(usage.get("in", 0)) + int(usage.get("out", 0))
        self.run_req[key] = self.run_req.get(key, 0) + 1
        if prov in PROVIDER_LIMITS:
            pr = self._rec("providers", prov, day)
            pr["req"] += 1
            self.run_req[prov] = self.run_req.get(prov, 0) + 1
        self.window.setdefault(key, []).append((time.time(), int(usage.get("in", 0)) + int(usage.get("out", 0)),
                                                int(usage.get("out", 0))))
        self._save()

    def _breaker(self, prov, key, e):
        day = day_key(self.p[prov].day_tz)
        if e.kind == "dead":
            r = self._rec("models", key, day)
            r["dead_until"], r["dead_reason"] = time.time() + DEAD_DAYS * 86400, str(e)[:200]
        elif e.kind == "daily":
            r = self._rec("models", key, day)
            r["exhausted"] = True
            if e.limit:
                r["learned_rpd"] = e.limit
            if prov in PROVIDER_LIMITS and re.search(r"free-models-per-day|account", str(e), re.I):
                self._rec("providers", prov, day)["exhausted"] = True
        elif e.kind == "auth":
            self.run_off[prov] = str(e)[:120]
        self._save()
        self.log({"llm_skip_model": key, "kind": e.kind, "why": str(e)[:200]})

    # -- the call
    def complete(self, task, prompt, temperature=None, schema=None, validate=None, exclude_families=()):
        """-> (json_text, Meta). Raises Hold when no allowed model with a key + quota answers validly."""
        reg_schema, reg_val = _lookup(prompt)
        schema = schema if schema is not None else reg_schema
        validate = validate or reg_val
        temperature = 0.6 if temperature is None else temperature
        tried = []
        for prov, model in self.chain(task):
            key = f"{prov}/{model}"
            if family(model) in set(exclude_families):
                tried.append(f"{key}: same family as the writer")
                continue
            why = self.why_not(prov, model)
            if why:
                if (task, key, why) not in self.notes and why != "no key":
                    self.notes.add((task, key, why))
                    self.log({"llm_skip_model": key, "task": task, "why": why})
                tried.append(f"{key}: {why}")
                continue
            max_out = (LIMITS.get(key) or {}).get("max_out")
            fails = 0
            text = None
            while fails < 3:
                mo = self._pace(key, len(prompt) // 2, max_out)
                t0 = time.time()
                try:
                    text, usage = self.p[prov].call(model, prompt, temperature, mo)
                except ApiError as e:
                    if e.kind in ("dead", "daily", "auth"):
                        self._breaker(prov, key, e)
                        break
                    if e.kind in ("minute", "transient") and (e.retry or 8) <= 65 and fails < 2:
                        fails += 1
                        self.sleep(min(65, (e.retry or 8 * fails) + 1))
                        continue
                    if e.kind != "bad":                     # a bad request is about this prompt, not the model
                        self.run_dead[key] = f"{e.kind}: {str(e)[:100]}"
                    self.log({"llm_skip_model": key, "task": task, "kind": e.kind, "why": str(e)[:200]})
                    break
                self._account(prov, key, usage, max_out)
                self.log({"llm": key, "task": task, "s": round(time.time() - t0, 1), "in": usage.get("in"), "out": usage.get("out")})
                break
            if text is None:
                tried.append(f"{key}: failed")
                continue
            obj, err = parse_json(text, schema)
            if err and self.why_not(prov, model) is None:              # one repair try on the same model
                fix = ("أصلح النص التالي ليصبح كائن JSON واحداً صالحاً فقط، بلا أي شرح، مع المفاتيح المطلوبة: " +
                       ", ".join(schema or {}) + f"\nالخطأ: {err}\n\nالنص:\n" + text[:8000])
                try:
                    mo = self._pace(key, len(fix) // 2, max_out)
                    text2, usage = self.p[prov].call(model, fix, 0.0, mo)
                    self._account(prov, key, usage, max_out)
                    obj, err = parse_json(text2, schema)
                except ApiError as e:
                    err = f"repair failed: {e}"
            if err:
                self.log({"llm_json_invalid": key, "task": task, "err": err[:160], "head": (text or "")[:160]})
                tried.append(f"{key}: {err[:60]}")
                continue
            if validate:
                probs = validate(obj)
                if probs:
                    self.log({"llm_quality_reject": key, "task": task, "problems": probs[:4]})
                    tried.append(f"{key}: quality {probs[:2]}")
                    continue
            return json.dumps(obj, ensure_ascii=False), Meta(prov, model)
        raise Hold(f"{task}: no model answered ({' | '.join(tried)[:600]})")

    def bound(self, task, exclude_families=(), exclude_from=None):
        return Bound(self, task, exclude_families, exclude_from)


class Bound:
    """llm(prompt, temperature=...) callable for one task. `.families` = families that answered (for exclusion)."""

    def __init__(self, router, task, exclude_families=(), exclude_from=None):
        self.r, self.task, self.excl, self.src = router, task, set(exclude_families), exclude_from
        self.families, self.models = set(), []

    def __call__(self, prompt, temperature=None, schema=None, validate=None):
        excl = set(self.excl) | (set(self.src.families) if self.src is not None else set())
        text, meta = self.r.complete(self.task, prompt, temperature, schema, validate, excl)
        self.families.add(meta.family)
        self.models.append(str(meta))
        return text


_ROUTER = None


def get():
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = Router()
    return _ROUTER


def reset(router=None):
    global _ROUTER
    _ROUTER = router


def complete(task, prompt, temperature=None, **kw):
    return get().complete(task, prompt, temperature, **kw)


def bound(task, **kw):
    return get().bound(task, **kw)


def preflight():
    return get().preflight()


def llm(prompt, temperature=0.6, task="writer"):
    return get().complete(task, prompt, temperature)[0]


# ---------------------------------------------------------------- CLI: model ids, probes, quota status
PROBE = 'أعد JSON فقط بهذا الشكل: {"ok": true, "sentence": "جملة خبرية قصيرة بالعربية الفصحى عن الطقس"}'


def _summary(lines):
    out = os.getenv("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines)
    print(text)
    if out:
        with open(out, "a") as f:
            f.write(text + "\n")


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("--list-models", action="store_true")
    a.add_argument("--probe", default="", help="comma list of provider:model (default provider gemini)")
    a.add_argument("--status", action="store_true")
    x = a.parse_args(argv)
    R = get()
    if x.list_models:
        g = R.p["gemini"]
        if not g.configured():
            _summary(["GEMINI_API_KEY missing: cannot list models"])
        else:
            try:
                ms = g.list_models()
                ids = sorted(m["name"].split("/", 1)[1] for m in ms if "generateContent" in (m.get("supportedGenerationMethods") or []))
                want = sorted({m for t in CHAINS.values() for p, _, m in (s.partition(":") for s in t) if p == "gemini"})
                lines = ["### Gemini ListModels (generateContent)", "```", " ".join(ids), "```", "chain ids:"]
                lines += [f"- {m}: {'FOUND' if m in ids else 'MISSING'}" for m in want]
                _summary(lines)
            except Exception as ex:
                _summary([f"ListModels failed: {str(ex)[:200]}"])
    for spec in [s.strip() for s in x.probe.split(",") if s.strip()]:
        prov, _, model = spec.partition(":") if ":" in spec else ("gemini", "", spec)
        why = R.why_not(prov, model)
        if why:
            _summary([f"probe {prov}/{model}: skipped ({why})"])
            continue
        try:
            R.chain = (lambda pm: (lambda task: [pm]))((prov, model))
            t0 = time.time()
            text, meta = R.complete("probe", PROBE, 0.0, schema={"ok": bool, "sentence": str},
                                    validate=lambda d: arabic_quality(d.get("sentence"), min_len=8))
            _summary([f"probe {meta}: OK {round(time.time() - t0, 1)} s {text[:160]}"])
        except Exception as ex:
            _summary([f"probe {prov}/{model}: FAILED {str(ex)[:300]}"])
        finally:
            R.chain = Router.chain.__get__(R)
    if x.status:
        lines = ["### LLM availability"]
        for task in CHAINS:
            for prov, model in R.chain(task):
                lines.append(f"- {task}: {prov}/{model} -> {R.why_not(prov, model) or 'available'}")
        lines.append(f"- preflight: {R.preflight()}")
        _summary(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
