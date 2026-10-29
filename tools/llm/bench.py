"""Fallback-writer bench (run by .github/workflows/llm-fallback-bench.yml). Publishes nothing.

For each GGUF: start llama-server (--jinja, CPU), send the real newsroom writing prompt for the current top verified
story (reasoning_effort low and medium), record wall time, completion tokens, tokens/s, JSON validity, banned/sports
gate result, and the text. Gemini on the same prompt is recorded as the reference.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kahwa_publisher import newsroom as N  # noqa: E402


def post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_up(port, proc, secs=600):
    for _ in range(secs):
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
            return True
        except Exception:
            time.sleep(1)
    return False


def gate(text):
    try:
        raw = text[text.find("{"): text.rfind("}") + 1]
        d = json.loads(raw)
    except Exception as ex:
        return {"json_ok": False, "err": str(ex)[:120]}
    post_ = d.get("post", "")
    return {"json_ok": True, "post_len": len(post_), "banned_country": N.banned_country(post_), "has_sources_line": "المصادر" in post_,
            "bait": bool(N.BAIT.search(post_)), "keys": sorted(d)[:20], "post": post_[:1200], "headline": d.get("headline")}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--server"); a.add_argument("--models"); a.add_argument("--out", default="bench")
    x = a.parse_args()
    out = Path(x.out); out.mkdir(exist_ok=True)
    clusters = [c for c in N.cluster(N.fetch()) if len(c["sources"]) >= 2 and not N.blocked(c)]
    if not clusters:
        sys.exit("no verified story right now")
    c = clusters[0]
    src = "\n".join(f"- [{i['src']}] {i['title']} — {i['summary'][:400]}" for i in c["items"][:6])
    prompt = N.PROMPT + src
    rep = {"story": [i["title"] for i in c["items"][:4]], "outlets": c["sources"], "prompt_chars": len(prompt), "runs": []}
    t0 = time.time()
    try:
        g = N.llm(prompt)
        rep["runs"].append({"model": "gemini (reference)", "seconds": round(time.time() - t0, 1), **gate(g)})
    except Exception as ex:
        rep["runs"].append({"model": "gemini (reference)", "error": str(ex)[:200]})
    for i, m in enumerate(x.models.split()):
        f = Path("models") / m.split(":", 1)[1]
        port = 8090 + i
        log = open(out / f"server-{f.stem}.log", "w")
        t_load = time.time()
        proc = subprocess.Popen([x.server, "-m", str(f), "--jinja", "-c", "16384", "-t", str(os.cpu_count()), "--port", str(port),
                                 "--host", "127.0.0.1"], stdout=log, stderr=subprocess.STDOUT)
        up = wait_up(port, proc)
        load_s = round(time.time() - t_load, 1)
        if not up:
            rep["runs"].append({"model": f.name, "error": "llama-server did not start (architecture unsupported?)", "load_s": load_s})
            proc.kill(); continue
        for effort, max_tok in (("low", 2048), ("medium", 6144)):
            t1 = time.time()
            try:
                r = post(f"http://127.0.0.1:{port}/v1/chat/completions",
                         {"messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "top_p": 0.95, "top_k": 20,
                          "max_tokens": max_tok, "chat_template_kwargs": {"reasoning_effort": effort}}, timeout=1500)
                msg = r["choices"][0]["message"]
                u = r.get("usage") or {}
                dt = time.time() - t1
                rep["runs"].append({"model": f.name, "effort": effort, "load_s": load_s, "seconds": round(dt, 1),
                                    "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                                    "tok_per_s": round((u.get("completion_tokens") or 0) / max(dt, 0.1), 1),
                                    "timings": r.get("timings"), "finish": r["choices"][0].get("finish_reason"),
                                    "reasoning_chars": len(msg.get("reasoning_content") or ""), **gate(msg.get("content") or "")})
            except Exception as ex:
                rep["runs"].append({"model": f.name, "effort": effort, "error": str(ex)[:200], "seconds": round(time.time() - t1, 1)})
            (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
        proc.terminate(); proc.wait(timeout=30)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:20000])


if __name__ == "__main__":
    main()
