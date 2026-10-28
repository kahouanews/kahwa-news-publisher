"""Kahwa News automatic newsroom.

fetch Arabic world-news RSS -> cluster the same story across outlets -> rank by how many
outlets cover it and freshness -> legal/safety filter -> AI writes an ORIGINAL Arabic post
(no copying, no links) -> branded 1080x1350 card -> queue for the dispatcher.

python -m kahwa_publisher.newsroom [--max 2] [--dry]
"""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .dispatcher import QUEUE, ROOT

FEEDS = {
    "BBC عربي": "https://feeds.bbci.co.uk/arabic/rss.xml",
    "فرانس 24": "https://www.france24.com/ar/rss",
    "DW عربية": "https://rss.dw.com/rdf/rss-ar-all",
    "يورونيوز": "https://arabic.euronews.com/rss",
    "أخبار الأمم المتحدة": "https://news.un.org/feed/subscribe/ar/news/all/rss.xml",
    "الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
    "سكاي نيوز عربية": "https://www.skynewsarabia.com/rss",
    "إندبندنت عربية": "https://www.independentarabia.com/rss.xml",
}

# Algerian Law 23-19 + Meta policy kill-list: never touch these subjects automatically.
BLOCK = [
    "الجزائر", "جزائري", "جزائرية", "تبون", "شنقريحة", "الحراك", "القبائل", "الماك",
    "حماس تتبنى", "قطع رأس", "إعدام علني", "انتحار", "اغتصاب", "مشاهد مروعة", "فيديو صادم",
    "الإساءة للنبي", "ازدراء الأديان", "تجديف",
]
STOP = set("في من على إلى عن مع بعد قبل أن إن كان قد التي الذي هذه هذا و أو ثم لا ما بين خلال حول ضد عند".split())
SEEN = ROOT / "state" / "seen.json"
MEDIA = ROOT / "media"
ASSETS = ROOT / "assets"
REPO = os.getenv("GITHUB_REPOSITORY", "kahouanews/kahwa-news-publisher")
BRANCH = os.getenv("GITHUB_REF_NAME", "main")


# ---------------------------------------------------------------- fetch & cluster
def norm(t):
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)          # tashkeel, tatweel
    t = re.sub("[إأآا]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[^\w\s]", " ", t)


def tokens(t):
    return {w for w in norm(t).split() if len(w) > 2 and w not in STOP}


def fetch():
    import feedparser
    items = []
    for src, url in FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KahwaNewsBot"})
            raw = urllib.request.urlopen(req, timeout=25).read()
            for e in feedparser.parse(raw).entries[:40]:
                title = (e.get("title") or "").strip()
                summ = re.sub(r"<[^>]+>", " ", e.get("summary") or "").strip()
                ts = e.get("published_parsed") or e.get("updated_parsed")
                age_h = (time.time() - time.mktime(ts)) / 3600 if ts else 6
                if title and age_h < 12:
                    items.append({"src": src, "title": title, "summary": summ[:600], "age_h": max(age_h, 0),
                                  "tok": tokens(title + " " + summ[:200])})
        except Exception as ex:
            print(json.dumps({"feed_error": src, "err": str(ex)[:120]}, ensure_ascii=False))
    return items


def cluster(items):
    clusters = []
    for it in sorted(items, key=lambda x: x["age_h"]):
        for c in clusters:
            inter = len(it["tok"] & c["tok"])
            if inter >= 3 and inter / max(1, min(len(it["tok"]), len(c["tok"]))) >= 0.3:
                c["items"].append(it)
                c["tok"] |= it["tok"]
                break
        else:
            clusters.append({"items": [it], "tok": set(it["tok"])})
    for c in clusters:
        srcs = {i["src"] for i in c["items"]}
        fresh = min(i["age_h"] for i in c["items"])
        c["sources"] = sorted(srcs)
        c["score"] = len(srcs) * 3 + max(0, 6 - fresh)
        c["key"] = hashlib.sha1(" ".join(sorted(list(c["tok"]))[:12]).encode()).hexdigest()[:12]
    return sorted(clusters, key=lambda c: -c["score"])


def blocked(c):
    text = " ".join(i["title"] + " " + i["summary"] for i in c["items"])
    return any(b in text for b in BLOCK)


def load_seen():
    try:
        d = json.loads(SEEN.read_text())
    except Exception:
        d = {"stories": []}
    cutoff = time.time() - 3 * 86400
    d["stories"] = [s for s in d["stories"] if s["t"] > cutoff]
    return d


def is_seen(seen, c):
    for s in seen["stories"]:
        st = set(s["tok"])
        inter = len(st & c["tok"])
        if inter >= 3 and inter / max(1, min(len(st), len(c["tok"]))) >= 0.35:
            return True
    return False


# ---------------------------------------------------------------- AI writer (free LLM failover)
PROMPT = """أنت محرر أول في غرفة أخبار عالمية اسمها "قهوة نيوز Kahwa News".
اكتب منشور فيسبوك أصلياً بالعربية الفصحى المبسطة عن الخبر التالي، اعتماداً فقط على الوقائع الواردة في المصادر أدناه.
قواعد صارمة:
- لا تنسخ أي جملة من المصادر؛ أعد الصياغة كلياً وادمج المعلومات من كل المصادر.
- الدقة أولاً: كل جملة يجب أن تكون واضحة المعنى ومطابقة لما في المصادر حرفياً من حيث الوقائع.
- لا تخترع أي رقم أو اسم أو تصريح غير موجود في المصادر. إذا كانت المعلومات غير مؤكدة قل "بحسب التقارير".
- حياد تام، بلا رأي، بلا سخرية، بلا تحريض، بلا روابط، بلا ذكر للجزائر.
- البنية: سطر أول جذاب يبدأ بـ 🔴 (أو 🟡 إن لم يكن عاجلاً)، ثم 3 إلى 5 أسطر قصيرة بالتفاصيل الأهم، ثم سطر "لماذا يهم؟" بجملة واحدة، ثم سؤال قصير يدعو للنقاش، ثم 3 وسوم عربية.
- اذكر في سطر أخير: "المصادر: " ثم أسماء المؤسسات فقط بدون روابط.
أعد JSON فقط بهذا الشكل:
{"headline": "عنوان للصورة لا يتجاوز 70 حرفاً", "category": "كلمة واحدة: سياسة/اقتصاد/علوم/صحة/تكنولوجيا/بيئة/عالم", "urgent": true/false, "post": "نص المنشور كاملاً", "safe": true/false}
اجعل "safe" = false إذا كان الخبر عن الجزائر أو الدين أو يتضمن عنفاً مصوراً أو معلومات لا يمكن التحقق منها.

المصادر:
"""


def llm(prompt):
    """Gemini free tier first, then any OpenAI-compatible free endpoint (Groq, OpenRouter)."""
    errs = []
    gk = os.getenv("GEMINI_API_KEY")
    if gk:
        for model in os.getenv("GEMINI_MODELS", "gemini-flash-latest,gemini-2.5-flash,gemini-flash-lite-latest").split(","):
          for attempt in range(3):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                body = {"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.6, "responseMimeType": "application/json"}}
                req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                             headers={"Content-Type": "application/json", "x-goog-api-key": gk})
                r = json.loads(urllib.request.urlopen(req, timeout=90).read())
                return r["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as ex:
                errs.append(f"gemini/{model}#{attempt}: {str(ex)[:100]}")
                if any(c in str(ex) for c in ("503", "429", "500", "timed out")):
                    time.sleep(8 * (attempt + 1))
                    continue
                break
    for name, base, key_env, model_env, default in [
        ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
         "meta-llama/llama-3.3-70b-instruct:free"),
    ]:
        k = os.getenv(key_env)
        if not k:
            continue
        try:
            body = {"model": os.getenv(model_env, default), "temperature": 0.6,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}]}
            req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {k}"})
            r = json.loads(urllib.request.urlopen(req, timeout=90).read())
            return r["choices"][0]["message"]["content"]
        except Exception as ex:
            errs.append(f"{name}: {str(ex)[:120]}")
    raise RuntimeError("all LLMs failed: " + " | ".join(errs) if errs else "no LLM key configured")


CHECK = """أنت مدقق حقائق صارم في وكالة أنباء دولية. قارن كل جملة في المنشور بالمصادر.
ابحث عن: أي معلومة غير موجودة في المصادر، رقم أو اسم خاطئ، جملة مبهمة أو تغيّر المعنى، مبالغة أو رأي، ركاكة لغوية.
أعد JSON فقط: {"ok": true/false, "problems": ["..."], "corrected_post": "المنشور بعد التصحيح بنفس البنية إن لزم", "corrected_headline": "العنوان بعد التصحيح إن لزم"}"""


def write_post(c):
    src = "\n".join(f"- [{i['src']}] {i['title']} — {i['summary'][:400]}" for i in c["items"][:6])
    raw = llm(PROMPT + src)
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    d = json.loads(raw)
    post = re.sub(r"https?://\S+|www\.\S+", "", d.get("post", "")).strip()
    if not d.get("safe", False) or len(post) < 120 or any(b in post for b in BLOCK[:8]):
        return None
    # originality guard: reject if any 8-word run is copied from a source
    srcw = norm(src).split()
    grams = {" ".join(srcw[i:i + 8]) for i in range(len(srcw) - 7)}
    pw = norm(post).split()
    if any(" ".join(pw[i:i + 8]) in grams for i in range(len(pw) - 7)):
        return None
    # fact-check pass: a second, independent read compares every claim with the sources
    chk = llm(CHECK + "\n\nالمصادر:\n" + src + "\n\nالمنشور:\n" + post)
    chk = json.loads(chk[chk.find("{"): chk.rfind("}") + 1])
    if not chk.get("ok"):
        fixed = (chk.get("corrected_post") or "").strip()
        if len(fixed) < 120:
            return None
        post = re.sub(r"https?://\S+|www\.\S+", "", fixed)
        if chk.get("corrected_headline"):
            d["headline"] = chk["corrected_headline"][:80]
    d["post"] = post
    return d


# ---------------------------------------------------------------- branded card
def shape(t):
    return t  # Pillow with libraqm shapes Arabic natively (layout_engine RAQM)


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font, direction="rtl", language="ar") <= width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def card(headline, category, urgent, out):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1080, 1350
    NAVY, RED, WHITE, GREY = (10, 16, 28), (220, 24, 12), (245, 245, 245), (150, 160, 178)
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    # subtle diagonal light bands
    for i, a in enumerate((18, 12, 8)):
        d.polygon([(W - 520 + i * 140, 0), (W - 380 + i * 140, 0), (W - 780 + i * 140, H), (W - 920 + i * 140, H)],
                  fill=(10 + a, 16 + a, 28 + a + 6))
    R = ImageFont.Layout.RAQM
    f_tag = ImageFont.truetype(str(ASSETS / "Tajawal-Black.ttf"), 46, layout_engine=R)
    f_h = ImageFont.truetype(str(ASSETS / "Tajawal-Black.ttf"), 84, layout_engine=R)
    f_s = ImageFont.truetype(str(ASSETS / "Tajawal-Medium.ttf"), 34, layout_engine=R)
    f_logo = ImageFont.truetype(str(ASSETS / "Barlow-BlackItalic.ttf"), 64)
    f_logo2 = ImageFont.truetype(str(ASSETS / "Barlow-BlackItalic.ttf"), 34)
    # tag (top right)
    tag = ("عاجل" if urgent else category or "عالم")
    tw = d.textlength(tag, font=f_tag, direction="rtl", language="ar")
    x1 = W - 70
    d.polygon([(x1 - tw - 70, 90), (x1 + 12, 90), (x1 - 8, 170), (x1 - tw - 90, 170)], fill=RED)
    d.text((x1 - 30, 128), tag, font=f_tag, fill=WHITE, anchor="rm", direction="rtl", language="ar")
    # headline, right-aligned, auto-fit
    size = 92
    while True:
        f_h = ImageFont.truetype(str(ASSETS / "Tajawal-Black.ttf"), size, layout_engine=R)
        lines = wrap(d, headline, f_h, W - 150)
        if len(lines) <= 5 or size <= 58:
            break
        size -= 6
    lh = int(size * 1.38)
    y = 300 + max(0, (5 - len(lines)) * lh // 2)
    for ln in lines[:6]:
        d.text((W - 75, y), ln, font=f_h, fill=WHITE, anchor="ra", direction="rtl", language="ar")
        y += lh
    # red rule under headline
    d.rectangle([W - 75 - 160, y + 20, W - 75, y + 32], fill=RED)
    # footer brand bar
    d.rectangle([0, H - 170, W, H], fill=(6, 10, 18))
    d.text((70, H - 120), "KAHWA", font=f_logo, fill=WHITE, anchor="lm")
    lw = d.textlength("KAHWA", font=f_logo)
    d.polygon([(70 + lw + 22, H - 140), (70 + lw + 190, H - 140), (70 + lw + 178, H - 98), (70 + lw + 10, H - 98)],
              fill=RED)
    d.text((70 + lw + 100, H - 119), "NEWS", font=f_logo2, fill=NAVY, anchor="mm")
    date = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    d.text((W - 70, H - 118), "قهوة نيوز · " + date, font=f_s, fill=GREY, anchor="rm", direction="rtl", language="ar")
    im.save(out, "JPEG", quality=92, optimize=True, progressive=True)


# ---------------------------------------------------------------- main
def main():
    a = argparse.ArgumentParser()
    a.add_argument("--max", type=int, default=int(os.getenv("NEWS_PER_RUN", "2")))
    a.add_argument("--dry", action="store_true", help="print, don't queue")
    x = a.parse_args()
    queued = len(list(QUEUE.glob("*.json"))) if QUEUE.exists() else 0
    if queued >= int(os.getenv("MAX_QUEUE", "6")):
        print(json.dumps({"skip": "queue full", "queued": queued}))
        return 0
    seen = load_seen()
    clusters = cluster(fetch())
    made = 0
    for c in clusters:
        if made >= x.max:
            break
        if len(c["sources"]) < int(os.getenv("MIN_SOURCES", "2")) or blocked(c) or is_seen(seen, c):
            continue
        try:
            d = write_post(c)
        except Exception as ex:
            print(json.dumps({"llm_error": str(ex)[:300]}))
            break
        seen["stories"].append({"t": time.time(), "tok": sorted(c["tok"])[:40], "key": c["key"]})
        if not d:
            continue
        sid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M") + "-" + c["key"]
        MEDIA.mkdir(exist_ok=True)
        img = MEDIA / f"{sid}.jpg"
        card(d["headline"], d.get("category"), d.get("urgent"), img)
        url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/media/{img.name}"
        post = {"id": sid, "text": d["post"], "media": [{"type": "image", "url": url}], "kind": "post",
                "title": d["headline"], "priority": 3 if d.get("urgent") else 5, "sources": c["sources"],
                "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                # give GitHub time to serve the pushed image before any provider fetches it
                "not_before": datetime.fromtimestamp(time.time() + 900, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        if x.dry:
            print(json.dumps(post, ensure_ascii=False, indent=1))
        else:
            QUEUE.mkdir(exist_ok=True)
            (QUEUE / f"{sid}.json").write_text(json.dumps(post, ensure_ascii=False, indent=1))
        made += 1
    SEEN.parent.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False))
    print(json.dumps({"clusters": len(clusters), "queued_now": made}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
