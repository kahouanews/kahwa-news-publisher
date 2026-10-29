"""Story contract for reels: source texts -> LLM (Gemini free tier first) -> validated renderer story dict.

Every number shown on screen must exist in the source texts (same digit check as the card subline in newsroom),
no banned country / sports / engagement bait (render.gate), location point must fall inside the named country,
tragic stories are forced to the calm music bed.
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .render import ASSETS, BANNED_ISO, banned_hits

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
TRAGIC_RE = re.compile(r"قتل|قتيل|قتلى|مقتل|ضحايا|ضحية|وفاة|وفيات|مصرع|غرق|زلزال|إعصار|اعصار|فيضان|فيضانات|كارثة|انهيار|"
                       r"حريق|حرائق|جرحى|مفقود|مفقودين|إصابات|مأساة|تفشي|وباء|جفاف|مجاعة|انفجار|تحطم|موت|ماتوا|نزوح")
DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]

PROMPT = """أنت محرر فيديو في غرفة أخبار عالمية اسمها "قهوة نيوز". حوّل الخبر التالي إلى نص ريل شرح قصير بالعربية الفصحى المبسطة،
اعتماداً فقط على الوقائع الواردة في المصادر أدناه.
قواعد صارمة:
- كل رقم تكتبه يجب أن يكون موجوداً حرفياً في المصادر بنفس الأرقام (لا تحسب ولا تقرّب ولا تحوّل الوحدات ولا تكتب أرقاماً غير موجودة). إن لم توجد أرقام كافية فاكتب الوقائع بلا أرقام.
- لا تخترع أي اسم أو مكان أو تصريح، ولا تضف أسباباً أو تفسيرات أو اتهامات (مثل الإهمال أو التقصير) غير مذكورة حرفياً في المصادر. حياد تام، بلا رأي ولا تهويل ولا صفات انفعالية، بلا إيموجي، بلا روابط.
- ممنوع منعاً باتاً ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل بأي شكل، وممنوع الرياضة، وممنوع طلب التفاعل (شارك، علّق، تابع...).
- ضع الكلمة أو العبارة الأهم (كلمة إلى ثلاث كلمات) بين [[ ]] مرة واحدة على الأكثر في كل سطر.
- الأطوال: العنوان حتى 45 حرفاً، كل نقطة حتى 60 حرفاً، كل سطر عاجل حتى 55 حرفاً.
- footage_queries: 3 إلى 5 عبارات بحث إنجليزية قصيرة (2-4 كلمات) لإيجاد صور/لقطات أرشيفية حقيقية في ويكيميديا كومنز للمكان أو الظاهرة
  (مثال: "Luzon rice fields", "volcano eruption ash cloud", "satellite image typhoon"). ممنوع: أشخاص بأسمائهم، حرب، أسلحة، جثث، دماء، احتجاجات، شرطة.
- lat/lon: إحداثيات المكان الرئيسي للخبر (مدينة أو منطقة) بدقة، داخل البلد المذكور.
- mood: "serious" للخبر العادي، "calm" للمآسي والكوارث والوفيات، "light" للعلوم والاقتصاد والاكتشافات الإيجابية فقط. tragic=true إذا كان في الخبر وفيات أو كارثة أو معاناة.
- urgent=true فقط إذا كان خبراً عاجلاً حقيقياً حدث في الساعات الأخيرة.
أعد JSON فقط بهذا الشكل بالضبط:
{"topic_ar": "كلمتان: الملف (مثال: طقس متطرف، اقتصاد عالمي)",
 "headline": "عنوان الخطّاف مع [[كلمة مفتاحية]]",
 "headline_breaking": "صيغة عاجلة قصيرة للعنوان مع [[فعل أو كلمة]]",
 "deck": "سطر تحت العنوان حتى 60 حرفاً: متى/أين",
 "visual_caption": "تعليق على الصورة الثانية حتى 50 حرفاً مع [[...]]",
 "location": {"country_en": "English country name", "country_iso3": "ISO3", "region_ar": "الولاية أو المنطقة", "place_ar": "المدينة أو المكان", "lat": 0.0, "lon": 0.0},
 "where_line": "جملة تعرّف بالمكان أو السياق حتى 70 حرفاً مع [[...]]",
 "lower_third": {"name_ar": "المكان أو الجهة حتى 35 حرفاً", "role_ar": "لماذا هذا المكان مهم في الخبر حتى 50 حرفاً"},
 "facts": [{"text": "نقطة 1 مع [[...]]", "source": "اسم المؤسسة"}, {"text": "...", "source": "..."}, {"text": "...", "source": "..."}],
 "counter": {"display": "الرقم الأهم كما ورد في المصادر بالأرقام فقط", "prefix_ar": "مثل: أكثر من / نحو / أو فارغ", "label_ar": "ماذا يعني حتى 35 حرفاً", "sub_ar": "تفصيل حتى 45 حرفاً", "source": "المؤسسة"},
 "next": {"lines": ["ماذا بعد 1 حتى 55 حرفاً مع [[...]]", "ماذا بعد 2"], "sources": ["المؤسسة", "المؤسسة"]},
 "breaking_lines": [{"text": "سطر عاجل 1 مع [[...]]", "source": "المؤسسة"}, {"text": "...", "source": "..."}, {"text": "...", "source": "..."}, {"text": "...", "source": "..."}],
 "ticker": ["جملة قصيرة 1", "جملة 2", "جملة 3"],
 "mood": "serious|calm|light", "tragic": true, "urgent": false,
 "footage_queries": ["...", "..."],
 "place_terms_en": ["أسماء المكان بالإنجليزية وبلغة البلد كما تظهر في عناوين صور ويكيميديا، مثل: Spain, Spanish, España, Toledo"],
 "safe": true}
اجعل "safe" = false إذا كان الخبر رياضياً أو يذكر الدول الممنوعة أو عن الدين أو يتطلب صوراً عنيفة أو لا يمكن التحقق منه.
إذا لم يوجد في المصادر رقم واضح للعداد فاجعل counter.display فارغاً "".

المصادر:
"""


def norm_digits(s):
    return (s or "").translate(AR_DIGITS)


def numbers_in(text):
    return [re.sub(r"[.,]$", "", n) for n in re.findall(r"\d[\d.,]*", norm_digits(text))]


def number_ok(n, src):
    """Same idea as newsroom's subline check (substring of the source texts), comma-insensitive."""
    flat = re.sub(r"(?<=\d)[,٬](?=\d)", "", norm_digits(src))
    return n in norm_digits(src) or n.replace(",", "") in flat


def date_ar(now=None):
    d = (now or datetime.now(timezone.utc)) + timedelta(hours=1)      # Algeria time for the on-screen date
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


# ------------------------------------------------------------------ source texts
def article_text(url, limit=3500):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KahwaNewsBot"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return ""
    raw = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", raw)
    paras = [re.sub(r"<[^>]+>", " ", p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", raw)]
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    paras = [p for p in paras if len(p) > 60 and re.search(r"[\u0600-\u06FF]", p)]
    return " ".join(paras)[:limit]


def source_block(items, fetch_articles=True):
    parts, n = [], 0
    for it in items[:6]:
        body = ""
        if fetch_articles and it.get("link") and n < 4:
            body = article_text(it["link"])
            n += 1 if body else 0
        parts.append(f"- [{it['src']}] {it['title']} — {it.get('summary', '')[:500]}" + (f"\n  النص: {body}" if body else ""))
    return "\n".join(parts)


# ------------------------------------------------------------------ geography
_GEO = None


def countries():
    global _GEO
    if _GEO is None:
        _GEO = {f["properties"]["a"]: f for f in json.loads((ASSETS / "countries.geojson").read_text())["features"]}
    return _GEO


def _rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    return [p[0] for p in geom["coordinates"]]


def _inside(lon, lat, ring):
    c = False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
            c = not c
    return c


def point_in_country(lon, lat, iso3, buffer_deg=0.75):
    f = countries().get(iso3)
    if not f:
        return False
    rings = _rings(f["geometry"])
    if any(_inside(lon, lat, r) for r in rings):
        return True
    return any(abs(x - lon) < buffer_deg and abs(y - lat) < buffer_deg for r in rings for x, y in r)


def centroid(iso3):
    f = countries()[iso3]
    pts = max(_rings(f["geometry"]), key=len)
    return [round(sum(p[0] for p in pts) / len(pts), 3), round(sum(p[1] for p in pts) / len(pts), 3)]


# ------------------------------------------------------------------ build + validate
class StoryError(ValueError):
    pass


def _clean(s, limit):
    s = re.sub(r"https?://\S+", "", str(s or "")).strip()
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", s)
    if s.count("[[") != s.count("]]") or s.count("[[") > 1:
        s = s.replace("[[", "").replace("]]", "")
    if len(s.replace("[[", "").replace("]]", "")) > limit:
        raise StoryError(f"too long ({limit}): {s[:60]}")
    return s


def build_story(d, items, src_text, sid, outlets):
    """LLM JSON -> renderer story dict. Raises StoryError on any rule violation."""
    if not d.get("safe", False):
        raise StoryError("llm marked unsafe")
    outl = list(dict.fromkeys(outlets))
    def src_of(x):
        x = str(x or "").strip()
        return x if any(o in x or x in o for o in outl) and len(x) < 40 else " · ".join(outl[:2])
    loc = d.get("location") or {}
    iso = str(loc.get("country_iso3", "")).upper()
    if iso in BANNED_ISO or iso not in countries():
        raise StoryError(f"country {iso} banned or not on the map")
    try:
        lat, lon = float(loc.get("lat")), float(loc.get("lon"))
    except (TypeError, ValueError):
        lat = lon = None
    place = _clean(loc.get("place_ar"), 30)
    if lat is None or not point_in_country(lon, lat, iso):
        lon, lat = centroid(iso)
        place = countries()[iso]["properties"].get("ar") or place
    facts = d.get("facts") or []
    if len(facts) < 3:
        raise StoryError("need 3 facts")
    bl = d.get("breaking_lines") or []
    if len(bl) < 4:
        bl = (bl + [{"text": f["text"], "source": f.get("source")} for f in facts])[:4]
    nxt = d.get("next") or {}
    lines = (nxt.get("lines") or [])[:2]
    if len(lines) < 2:
        raise StoryError("need 2 next lines")
    nsrc = (nxt.get("sources") or []) + [None, None]
    cnt = d.get("counter") or {}
    disp = norm_digits(str(cnt.get("display", "")).strip())
    if not re.fullmatch(r"\d[\d,.]*", disp or "-"):
        disp = ""
    country_ar = countries()[iso]["properties"].get("ar") or ""
    S = {
        "id": sid, "date_ar": date_ar(), "topic_ar": _clean(d.get("topic_ar"), 24),
        "headline": _clean(d.get("headline"), 50), "headline_breaking": _clean(d.get("headline_breaking") or d.get("headline"), 50),
        "deck": _clean(d.get("deck"), 64), "visual_caption": _clean(d.get("visual_caption") or d.get("deck"), 56),
        "visual_tab": _clean(d.get("topic_ar"), 24),
        "location": {"country_iso3": iso, "label_ar": country_ar, "region_ar": _clean(loc.get("region_ar"), 30),
                     "point": {"lon": lon, "lat": lat, "label_ar": place or country_ar}},
        "where_line": _clean(d.get("where_line"), 80),
        "lower_third": {"name_ar": _clean((d.get("lower_third") or {}).get("name_ar") or place, 38),
                        "role_ar": _clean((d.get("lower_third") or {}).get("role_ar"), 56), "source": " · ".join(outl[:2])},
        "facts": [{"text": _clean(f.get("text"), 64), "source": src_of(f.get("source"))} for f in facts[:3]],
        "next": {"title_ar": "ماذا بعد؟", "lines": [_clean(x, 60) for x in lines], "sources": [src_of(nsrc[0]), src_of(nsrc[1])]},
        "breaking_lines": [{"text": _clean(x.get("text"), 60), "source": src_of(x.get("source"))} for x in bl[:4]],
        "ticker": [_clean(x, 90) for x in (d.get("ticker") or [])[:5] if x] or [_clean(f.get("text"), 64).replace("[[", "").replace("]]", "") for f in facts[:3]],
        "sources": outl[:4], "tagline": "أخبار العالم… باختصار",
        "mood": d.get("mood") if d.get("mood") in ("serious", "calm", "light") else "serious",
        "tragic": bool(d.get("tragic")), "urgent": bool(d.get("urgent")),
        "footage_queries": [str(q)[:60] for q in (d.get("footage_queries") or [])[:5]],
        "place_terms_en": [str(q)[:30] for q in (d.get("place_terms_en") or [])[:8] if len(str(q)) >= 3] or [str(loc.get("country_en", ""))],
        "media": {},
    }
    if disp:
        S["big_number"] = {"value": float(disp.replace(",", "")), "display": disp, "prefix_ar": _clean(cnt.get("prefix_ar"), 14),
                           "label_ar": _clean(cnt.get("label_ar"), 40), "sub_ar": _clean(cnt.get("sub_ar"), 50), "source": src_of(cnt.get("source"))}
    else:   # no sourced figure: the counter scene shows the key fact instead (no invented number)
        k = S["facts"][0]["text"]
        S["big_number"] = {"value": 0, "display": "", "prefix_ar": "", "label_ar": k, "sub_ar": S["deck"], "source": S["facts"][0]["source"]}
    # ---- every visible number must exist in the sources (date_ar is generated by code, not the LLM)
    vis = json.dumps({k: v for k, v in S.items() if k not in ("id", "date_ar", "location", "footage_queries", "media", "mood",
                                                              "tragic", "urgent", "sources", "tagline")}, ensure_ascii=False)
    vis = re.sub(r'"value": [\d.]+', "", vis)
    bad = [n for n in numbers_in(vis) if not number_ok(n, src_text)]
    if bad:
        raise StoryError(f"numbers not in sources: {bad}")
    # ---- tragedy: never upbeat music on deaths/disasters
    if TRAGIC_RE.search(vis + " " + src_text[:1500]):
        S["tragic"] = True
    if S["tragic"] and S["mood"] == "light":
        S["mood"] = "calm"
    hits = banned_hits(vis + " " + " ".join(S["footage_queries"] + S["place_terms_en"]))
    if hits:
        raise StoryError(f"banned: {hits}")
    return S


CHECK = """أنت مدقق حقائق صارم في وكالة أنباء دولية. أمامك نصوص ريل إخباري قصير والمصادر.
قارن كل سطر بالمصادر: هل يحتوي معلومة أو سبباً أو اتهاماً أو اسماً أو رقماً غير موجود في المصادر؟ هل يغيّر المعنى أو يبالغ؟
أعد JSON فقط: {"ok": true/false, "problems": ["السطر: المشكلة"]}"""


def fact_check(llm, S, src):
    lines = [S["headline"], S["headline_breaking"], S["deck"], S["visual_caption"], S["where_line"], S["lower_third"]["role_ar"]]
    lines += [f["text"] for f in S["facts"]] + S["next"]["lines"] + [b["text"] for b in S["breaking_lines"]] + S["ticker"]
    bn = S.get("big_number") or {}
    lines.append(" ".join(str(bn.get(k, "")) for k in ("prefix_ar", "display", "label_ar", "sub_ar")))
    txt = "\n".join("- " + l.replace("[[", "").replace("]]", "") for l in lines if l)
    raw = llm(CHECK + "\n\nالمصادر:\n" + src[:9000] + "\n\nنصوص الريل:\n" + txt)
    d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    return bool(d.get("ok")), [str(p)[:160] for p in (d.get("problems") or [])][:6]


def write_story(llm, items, outlets, sid, fetch_articles=True, log=print):
    """Run the LLM (one retry with the validator's complaint) and return (story, source_text)."""
    src = source_block(items, fetch_articles)
    err = None
    for attempt in range(2):
        prompt = PROMPT + src + (f"\n\nملاحظة: المحاولة السابقة رُفضت لهذا السبب: {err}. صحّح ذلك." if err else "")
        raw = llm(prompt)
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        try:
            d = json.loads(raw)
            S = build_story(d, items, src, sid, outlets)
            ok, problems = fact_check(llm, S, src)
            if not ok:
                raise StoryError("fact-check: " + " | ".join(problems))
            S["source_texts"] = src[:12000]
            S["source_urls"] = [it.get("link") for it in items if it.get("link")][:6]
            return S, src
        except (StoryError, ValueError) as ex:
            err = str(ex)[:200]
            log(json.dumps({"story_reject": err, "attempt": attempt}, ensure_ascii=False))
    raise StoryError(err or "story failed")
