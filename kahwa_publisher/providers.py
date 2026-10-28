"""Publishing providers for the Kahwa News Facebook Page.

Each provider returns a Result. Errors are classified so the dispatcher can
decide: fail over to the next provider, cool this provider down, or pause ALL
publishing (Facebook identity / spam checks: never retry those automatically).
"""
import json
import os
import time
import urllib.error
import urllib.request

OK = "ok"                # published (or accepted, for fire-and-forget providers)
UNCERTAIN = "uncertain"  # request may have gone through: do NOT fail over (avoid duplicates)
TRANSIENT = "transient"  # network / 5xx / 429: try next provider, short cooldown
AUTH = "auth"            # token expired / account disconnected: next provider, long cooldown, alert
FB_BLOCK = "fb_block"    # Facebook checkpoint / spam block (error 368 etc.): pause EVERYTHING, alert
BAD_POST = "bad_post"    # the post itself is invalid (media too big, too long): skip post, alert

FB_BLOCK_MARKERS = (
    "368", "1390008", "confirm your identity", "checkpoint", "temporarily blocked",
    "spam", "restricted", "we limit how often", "account is restricted",
)
AUTH_MARKERS = (
    "190", "expired", "disconnected", "reconnect", "invalid oauth", "session has been invalidated",
    "unauthorized", "permission", "not authorized", "token",
)
BAD_POST_MARKERS = ("too large", "file size", "duration", "aspect ratio", "unsupported", "invalid media")


class Result:
    def __init__(self, status, provider, detail="", remote_id=None):
        self.status, self.provider, self.detail, self.remote_id = status, provider, str(detail)[:800], remote_id

    def __repr__(self):
        return f"Result({self.status}, {self.provider}, {self.remote_id}, {self.detail[:120]!r})"


def classify_text(text, default=TRANSIENT):
    t = (text or "").lower()
    if any(m in t for m in FB_BLOCK_MARKERS):
        return FB_BLOCK
    if any(m in t for m in BAD_POST_MARKERS):
        return BAD_POST
    if any(m in t for m in AUTH_MARKERS):
        return AUTH
    return default


def http_json(method, url, headers=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "kahwa-news-publisher/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class Provider:
    name = "base"

    def configured(self):
        return False

    def publish(self, post):
        raise NotImplementedError

    def check(self, remote_id):
        """Re-check an UNCERTAIN publish later. Returns Result."""
        return Result(UNCERTAIN, self.name, "no check available", remote_id)


class Zernio(Provider):
    """Primary. Docs: docs.zernio.com/platforms/facebook/posts. HTTP 207 = partial failure."""
    name = "zernio"
    base = "https://zernio.com/api/v1"

    def __init__(self):
        self.key = os.getenv("ZERNIO_API_KEY", "")
        self.account = os.getenv("ZERNIO_FB_ACCOUNT_ID", "")
        self.page = os.getenv("ZERNIO_FB_PAGE_ID", "")

    def configured(self):
        return bool(self.key and self.account)

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    def _payload(self, post):
        entry = {"platform": "facebook", "accountId": self.account}
        psd = {}
        if self.page:
            psd["pageId"] = self.page
        if post.get("kind") == "reel":
            psd["contentType"] = "reel"
            if post.get("title"):
                psd["title"] = post["title"]
        if psd:
            entry["platformSpecificData"] = psd
        body = {"content": post["text"], "platforms": [entry], "publishNow": True}
        if post.get("media"):
            body["mediaItems"] = [{"type": m["type"], "url": m["url"]} for m in post["media"]]
        return body

    def _from_post(self, code, raw):
        try:
            data = json.loads(raw)
        except ValueError:
            return Result(classify_text(raw), self.name, f"HTTP {code}: {raw}")
        p = data.get("post") or {}
        pid = p.get("_id")
        plats = p.get("platforms") or [{}]
        st = (plats[0].get("status") or p.get("status") or "").lower()
        err = json.dumps(plats[0].get("error") or data.get("error") or plats[0].get("errorMessage") or "")
        if code in (200, 201) and st == "published":
            return Result(OK, self.name, plats[0].get("platformPostUrl", ""), pid)
        if st in ("pending", "processing", "publishing", "scheduled", "queued") and pid:
            return Result(UNCERTAIN, self.name, f"status={st}", pid)
        if code == 401:
            return Result(AUTH, self.name, raw, pid)
        if code == 429 or code >= 500:
            return Result(TRANSIENT, self.name, f"HTTP {code}: {raw}", pid)
        return Result(classify_text(err + raw, default=TRANSIENT), self.name, f"HTTP {code} status={st}: {err or raw}", pid)

    def publish(self, post):
        try:
            code, raw = http_json("POST", f"{self.base}/posts", self._h(), self._payload(post), timeout=120)
        except Exception as e:  # timeout after send = maybe published
            return Result(UNCERTAIN, self.name, f"network: {e}")
        res = self._from_post(code, raw)
        # poll a pending post for up to ~3 minutes before deciding
        if res.status == UNCERTAIN and res.remote_id:
            for _ in range(6):
                time.sleep(30)
                res = self.check(res.remote_id)
                if res.status != UNCERTAIN:
                    break
        return res

    def check(self, remote_id):
        if not remote_id:
            return Result(UNCERTAIN, self.name, "no remote id")
        try:
            code, raw = http_json("GET", f"{self.base}/posts/{remote_id}", self._h())
        except Exception as e:
            return Result(UNCERTAIN, self.name, f"network: {e}", remote_id)
        return self._from_post(201 if code == 200 else code, raw)


class Buffer(Provider):
    """Backup 1. GraphQL at https://api.buffer.com, createPost with mode shareNow."""
    name = "buffer"
    url = "https://api.buffer.com"
    MUTATION = """mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}"""

    def __init__(self):
        self.static_key = os.getenv("BUFFER_API_KEY", "")
        self.channel = os.getenv("BUFFER_CHANNEL_ID", "")
        self.oauth = BufferOAuth()
        self.key = ""

    def configured(self):
        return bool(self.channel and (self.oauth.configured() or self.static_key))

    def _token(self):
        """OAuth first (renews itself forever); the 30-day personal key is only a fallback."""
        if self.oauth.configured():
            try:
                return self.oauth.access_token()
            except Exception as e:
                if not self.static_key:
                    raise
                print(json.dumps({"msg": "buffer oauth failed, using static key", "err": str(e)[:200]}))
        return self.static_key

    def publish(self, post):
        try:
            self.key = self._token()
        except Exception as e:
            return Result(AUTH, self.name, f"oauth refresh failed: {e}")
        inp = {
            "text": post["text"], "channelId": self.channel,
            "schedulingType": "automatic", "mode": "shareNow",
            "metadata": {"facebook": {"type": "reel" if post.get("kind") == "reel" else "post"}},
        }
        if post.get("media"):
            inp["assets"] = [{m["type"]: {"url": m["url"]}} for m in post["media"]]
        try:
            code, raw = http_json("POST", self.url, {"Authorization": f"Bearer {self.key}"},
                                  {"query": self.MUTATION, "variables": {"input": inp}}, timeout=90)
        except Exception as e:
            return Result(UNCERTAIN, self.name, f"network: {e}")
        if code == 401:
            return Result(AUTH, self.name, raw)
        if code == 429 or code >= 500:
            return Result(TRANSIENT, self.name, f"HTTP {code}: {raw}")
        try:
            data = json.loads(raw)
        except ValueError:
            return Result(TRANSIENT, self.name, f"HTTP {code}: {raw}")
        cp = (data.get("data") or {}).get("createPost") or {}
        if cp.get("post"):
            st = (cp["post"].get("status") or "").lower()
            if st in ("error", "failed"):
                return Result(TRANSIENT, self.name, f"status={st}", cp["post"]["id"])
            return Result(OK, self.name, f"status={st}", cp["post"]["id"])
        msg = cp.get("message") or json.dumps(data.get("errors") or data)
        return Result(classify_text(msg), self.name, msg)


class BufferOAuth:
    """Buffer OAuth (public PKCE client). Refresh tokens are SINGLE-USE and rotate:
    the newest one is kept encrypted in state/buffer_token.enc (key = GitHub secret
    BUFFER_TOKEN_KEY) and committed by the workflow, so it renews forever with no human.
    Reusing an old refresh token revokes the grant, so we save BEFORE using the access token."""
    TOKEN_URL = "https://auth.buffer.com/token"

    def __init__(self):
        self.client_id = os.getenv("BUFFER_CLIENT_ID", "")
        self.enc_key = os.getenv("BUFFER_TOKEN_KEY", "")
        self.seed = os.getenv("BUFFER_REFRESH_TOKEN", "")  # only used the very first time
        root = os.getenv("KAHWA_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.path = os.path.join(root, "state", "buffer_token.enc")

    def configured(self):
        return bool(self.client_id and self.enc_key and (self.seed or os.path.exists(self.path)))

    def _f(self):
        from cryptography.fernet import Fernet
        return Fernet(self.enc_key.encode())

    def _load(self):
        if os.path.exists(self.path):
            return json.loads(self._f().decrypt(open(self.path, "rb").read()))
        return {"refresh_token": self.seed, "access_token": "", "exp": 0}

    def _save(self, tok):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        open(tmp, "wb").write(self._f().encrypt(json.dumps(tok).encode()))
        os.replace(tmp, self.path)

    def access_token(self):
        import urllib.parse
        tok = self._load()
        if tok.get("access_token") and tok.get("exp", 0) > time.time() + 300:
            return tok["access_token"]
        body = urllib.parse.urlencode({"client_id": self.client_id, "grant_type": "refresh_token",
                                       "refresh_token": tok["refresh_token"]}).encode()
        req = urllib.request.Request(self.TOKEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                new = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}")
        tok = {"refresh_token": new.get("refresh_token") or tok["refresh_token"],
               "access_token": new["access_token"], "exp": time.time() + int(new.get("expires_in", 3600))}
        self._save(tok)
        return tok["access_token"]


class MakeWebhook(Provider):
    """Backup 2. A Make scenario: Custom webhook -> Facebook Pages (photo / video / reel) -> Webhook response."""
    name = "make"

    def __init__(self):
        self.url = os.getenv("MAKE_WEBHOOK_URL", "")

    def configured(self):
        return bool(self.url)

    def publish(self, post):
        body = {"id": post["id"], "text": post["text"], "kind": post.get("kind", "post"),
                "media_type": (post.get("media") or [{}])[0].get("type", "none"),
                "media_url": (post.get("media") or [{}])[0].get("url", "")}
        try:
            code, raw = http_json("POST", self.url, body=body, timeout=280)
        except Exception as e:
            return Result(UNCERTAIN, self.name, f"network: {e}")
        if code == 200 and "error" not in raw.lower():
            return Result(OK, self.name, raw[:200])
        if code == 429 or code >= 500:
            return Result(TRANSIENT, self.name, f"HTTP {code}: {raw}")
        return Result(classify_text(raw), self.name, f"HTTP {code}: {raw}")


def all_providers():
    order = [p.strip() for p in os.getenv("PROVIDER_ORDER", "zernio,buffer,make").split(",") if p.strip()]
    table = {"zernio": Zernio, "buffer": Buffer, "make": MakeWebhook}
    return [table[n]() for n in order if n in table]
