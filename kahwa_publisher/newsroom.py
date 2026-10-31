"""Kahwa News automatic newsroom.

fetch Arabic world-news RSS -> cluster the same story across outlets -> rank by how many
outlets cover it and freshness -> legal/safety filter -> AI writes an ORIGINAL Arabic post
(no copying, no links) -> branded 1080x1350 card -> queue for the dispatcher.

Edge layer (2026-10-29): early-warning ranking (signals.py: GDELT breadth + Wikipedia pageview spikes; ranking only,
the >=2-outlet rule is unchanged), fixed named series («بالأرقام» numbers cards: series tag on the card + caption first
line "بالأرقام | <subject>"), and a second verification layer (verify.py: claim decomposition, every atomic claim must
be supported by a source text, unsupported claims are repaired once, otherwise the story is dropped - fail closed).

Attribution (2026-10-29, attribution.py): public text credits the PRIMARY source of each fact (official body > wire agency >
«وكالات»), never a competing outlet; the outlet names are hidden from the writer (sources are labelled [م1], [م2] …), any
outlet that still appears is a hard gate failure, and the last line is built by code: «المصدر: …». The >=2-outlet
verification stays internal (run log / queue metadata).

python -m kahwa_publisher.newsroom [--max 2] [--dry] [--out DIR]
  --dry: writes nothing to queue/, state/ or media/ (cards + JSON go to --out, default /tmp/kahwa-dry) - test runs.
"""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import numbers as NUM
from . import signals
from . import safety
from . import attribution as A
from .dispatcher import QUEUE, ROOT
from . import llm as R
from .verify import check as verify_check, verify_text

SERIES_NUMBERS = "بالأرقام"

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
# HARD RULE (user 2026-10-28): never mention Morocco, UAE, Algeria or Israel at all.
BANNED_COUNTRIES = [
    "المغرب", "مغربي", "مغربية", "المغاربة", "الرباط", "الدار البيضاء", "مراكش", "طنجة", "محمد السادس", "أخنوش", "المخزن",
    "البوليساريو", "الصحراء الغربية",
    "الإمارات", "الامارات", "إماراتي", "اماراتي", "إماراتية", "اماراتية", "أبوظبي", "أبو ظبي", "ابوظبي", "دبي", "الشارقة",
    "محمد بن زايد", "بن زايد",
    "الجزائر", "جزائري", "جزائرية", "الجزائريين", "تبون",
    "إسرائيل", "اسرائيل", "إسرائيلي", "اسرائيلي", "إسرائيلية", "اسرائيلية", "الإسرائيليين", "الاسرائيليين",
    "تل أبيب", "تل ابيب", "نتنياهو", "الكنيست", "كاتس", "آيزنكوت", "هرتسوغ", "الجيش الإسرائيلي", "الاحتلال",
    "مغاربة", "مغرب", "إماراتيين", "اماراتيين", "جزائريين", "إسرائيليين", "اسرائيليين",
    "إمارات", "امارات", "جزائر",          # bare stems: «للإمارات» / «للجزائر» (ل + لـ, the article absorbed) were slipping through
    "Morocco", "Moroccan", "UAE", "Emirates", "Emirati", "Dubai", "Abu Dhabi", "Algeria", "Algerian", "Israel", "Israeli",
]

BLOCK = [
    "الجزائر", "جزائري", "جزائرية", "تبون", "شنقريحة", "الحراك", "القبائل", "الماك",
    "حماس تتبنى", "قطع رأس", "إعدام علني", "انتحار", "اغتصاب", "مشاهد مروعة", "فيديو صادم",
    "الإساءة للنبي", "ازدراء الأديان", "تجديف",
]
# Religion (Algerian Law 23-19 risk + project rule "block religion", 2026-10-31): religious debates, clergy, holidays-as-religion
# stories («بين البدعة والانفتاح.. إلغاء الاحتفال بالهالوين») are never covered. Matched as whole words (with clitics) on
# normalised text; deliberately no «الدين» (also "the debt") and no «إسلامي» (the Islamic Republic of Iran is politics).
RELIGION = ["بدعة", "البدعة", "بدع", "حرام", "تحريم", "فتوى", "فتاوى", "الإفتاء", "دار الإفتاء", "مفتي", "المفتي", "الأزهر",
            "شيخ الأزهر", "الكنيسة", "كنيسة", "الكنائس", "كنائس", "الفاتيكان", "البابا", "بابا الفاتيكان", "المسجد", "مسجد",
            "مساجد", "المساجد", "ديني", "دينية", "الدينية", "الديني", "الأديان", "الاديان", "ديانة", "الشريعة", "المسيحية",
            "المسيحيين", "المسيحي", "مسيحي", "مسيحيين", "الهالوين", "هالوين", "عيد الميلاد المجيد", "الكريسماس", "كريسماس",
            "رمضان", "الحج", "العمرة", "الصلاة", "صلاة", "القرآن", "قرآن", "المصحف", "الإنجيل", "التوراة", "حاخام", "الحاخام",
            "القس", "الكاهن", "راهب", "رهبان", "الإلحاد", "ملحد", "ملحدين", "التبشير", "تبشير", "التنصير", "الردة", "اليهودية",
            "السلفية", "سلفي", "الصوفية", "صوفي", "الشيعة", "السنة والشيعة", "طائفي", "طائفية", "الطائفية", "ازدراء", "التجديف",
            "Halloween", "Vatican", "Pope", "church", "mosque", "religious", "religion"]
_RELIGION_RE = None


def religion_hits(text):
    """Religion words in a text (whole words, Arabic clitics allowed, harakat/tatweel ignored)."""
    global _RELIGION_RE
    if _RELIGION_RE is None:
        alts = "|".join(re.escape(norm(k).strip()) for k in sorted(RELIGION, key=len, reverse=True))
        _RELIGION_RE = re.compile(r"(?<![\w])(?:[وفبلك]{0,2})(?:ال)?(?:" + alts + r")(?:ه|ات|ون|ين|يه|ا)?(?![\w])", re.I)
    return sorted({m.group(0).strip() for m in _RELIGION_RE.finditer(norm(text or ""))})


def item_blocked(it):
    """One feed item that must not feed any product: banned country, BLOCK words or religion (its title or summary)."""
    t = (it.get("title") or "") + " " + (it.get("summary") or "")
    return banned_country(t) or any(b in t for b in BLOCK) or bool(religion_hits(t))


def purge_items(c):
    """Drop blocked items (Algeria / banned countries / religion) from a cluster instead of letting a mixed cluster carry them
    to the writer; the cluster's outlet list is recomputed from what is left (the >=2-outlet rule then applies again)."""
    keep = [i for i in c["items"] if not item_blocked(i)]
    if len(keep) == len(c["items"]):
        return c
    d = dict(c, items=keep)
    d["sources"] = sorted({i["src"] for i in keep})
    d["purged"] = len(c["items"]) - len(keep)
    return d


# user rule 2026-10-28: NO sports news on Kahwa News (sports has its own page)
SPORTS = ["كرة القدم", "كرة قدم", "مباراة", "مباريات", "الدوري", "دوري", "كأس", "الكونفيدرالية", "أبطال أفريقيا",
          "دوري أبطال", "منتخب", "مدرب", "لاعب", "لاعبين", "هدف", "أهداف", "ركلة", "ملعب", "الفيفا", "فيفا", "يويفا",
          "كاف", "الأولمبية", "أولمبي", "تنس", "فورمولا", "رالي", "ملاكمة", "سباق", "بطولة", "انتقالات", "ميركاتو",
          "ريال مدريد", "برشلونة", "ليفربول", "مانشستر", "بايرن", "يوفنتوس", "ميسي", "رونالدو", "صلاح", "مبابي",
          "الأهلي", "الزمالك", "الرجاء", "الوداد", "الترجي", "الهلال", "النصر", "الاتحاد السعودي لكرة"]
# unambiguous sports words: ONE hit is enough (weak words like هدف/لاعب/سباق/بطولة also appear in politics)
SPORTS_STRONG = ["كرة القدم", "كرة قدم", "كرة السلة", "كرة اليد", "دوري أبطال", "الدوري الإنجليزي", "الدوري الإسباني", "الفيفا", "فيفا", "يويفا", "ميركاتو",
                 "ريال مدريد", "برشلونة", "ليفربول", "مانشستر", "بايرن", "يوفنتوس", "باريس سان جيرمان", "ميسي", "رونالدو",
                 "مبابي", "الزمالك", "الوداد", "الرجاء", "الترجي", "فورمولا 1", "الفورمولا", "تنس", "ويمبلدون", "الأولمبية",
                 "ركلة جزاء", "مباراة", "مباريات"]
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


def stem(w):
    for pre in ("وال", "بال", "لل", "فال", "كال", "ال"):
        if w.startswith(pre) and len(w) - len(pre) >= 3:
            w = w[len(pre):]
            break
    if w[:1] in "وب" and len(w) >= 5:
        w = w[1:]
    for suf in ("ات", "ون", "ين", "يه", "ه"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w


def tokens(t):
    return {stem(w) for w in norm(t).split() if len(w) > 2 and w not in STOP}


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
                                  "link": e.get("link") or "", "tok": tokens(title + " " + summ[:200])})
        except Exception as ex:
            print(json.dumps({"feed_error": src, "err": str(ex)[:120]}, ensure_ascii=False))
    return items


def cluster(items):
    clusters = []
    for it in sorted(items, key=lambda x: x["age_h"]):
        for c in clusters:
            inter = len(it["tok"] & c["tok"])
            if inter >= 3 and inter / max(1, min(len(it["tok"]), len(c["tok"]))) >= 0.22:
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


_BANNED_RE = re.compile(
    r"(?<![\w])(?:[وفبلك]{0,2})(?:ال)?(?:" + "|".join(re.escape(k) for k in sorted(BANNED_COUNTRIES, key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE)


def banned_country(text):
    t = (text or "").replace("\u0640", "")
    t = re.sub(r"[\u064B-\u0652]", "", t)  # drop harakat
    return bool(_BANNED_RE.search(t))


def blocked(c):
    text = " ".join(i["title"] + " " + i["summary"] for i in c["items"])
    words = set(norm(text).split())
    sport_hits = sum((k in text) if " " in k else (norm(k) in words) for k in SPORTS)
    strong = any((k in text) if " " in k else (norm(k) in words or norm("ال" + k) in words) for k in SPORTS_STRONG)
    return any(b in text for b in BLOCK) or banned_country(text) or sport_hits >= 2 or strong or \
        bool(religion_hits(" ".join(i["title"] for i in c["items"])))      # a religion headline blocks the whole story


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
        if inter >= 3 and inter / max(1, min(len(st), len(c["tok"]))) >= 0.2:
            return True
    return False


# Meta engagement-bait policy: asking for reactions/comments/shares/votes demotes the post and the Page
BAIT = re.compile(r"(?<!\w)(شاركوا|انشروا|علّقوا|علقوا|اكتبوا|صوّتوا|صوتوا|تابعونا|اضغط|اضغطوا|لايك|منشن|شارك المنشور|شارك الخبر|"
                  r"في التعليقات|اكتب رأيك في|تابع الصفحة|تابع قهوة)(?!\w)")
# Meta: imagery of war/violence is never recommended -> such stories get our own visual (map/numbers/facts), no photo
VIOLENT = ["قصف", "غارة", "غارات", "انفجار", "تفجير", "هجوم", "قتلى", "قتيل", "مقتل", "جرحى", "اشتباكات", "معارك",
           "صاروخ", "صواريخ", "مسيّرة", "مسيرة", "مجزرة", "إطلاق نار", "اغتيال", "حرب", "دمار", "جثث", "ضحايا"]


# ---------------------------------------------------------------- AI writer (free LLM failover)
PROMPT = """أنت محرر أول في غرفة أخبار عالمية اسمها "قهوة نيوز Kahwa News".
اكتب منشور فيسبوك أصلياً بالعربية الفصحى المبسطة عن الخبر التالي، اعتماداً فقط على الوقائع الواردة في المصادر أدناه.
قواعد صارمة:
- لا تنسخ أي جملة من المصادر؛ أعد الصياغة كلياً وادمج المعلومات من كل المصادر.
- الدقة أولاً: كل جملة يجب أن تكون واضحة المعنى ومطابقة لما في المصادر حرفياً من حيث الوقائع.
- لا تخترع أي رقم أو اسم أو تصريح غير موجود في المصادر. إذا كانت المعلومات غير مؤكدة قل "بحسب التقارير".
- أخبار الحروب والنزاعات والعمليات العسكرية مسموحة: اكتبها بأسلوب وقائعي هادئ كوكالات الأنباء، بلا أي تفاصيل دموية ولا وصف مروّع (لا دماء ولا أشلاء ولا جثث ولا "مجزرة" ولا "مروع" ولا "بشع")، بلا تهويل، وبلا تبنّي رواية أي طرف: انسب كل حصيلة وكل ادعاء عسكري إلى قائله.
- ممنوع الأخبار الرياضية تماماً. حياد تام، بلا رأي، بلا سخرية، بلا تحريض، بلا روابط. ممنوع منعاً صارماً ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل بأي شكل.
- أسلوب صفحات الأخبار العربية الكبرى على فيسبوك: بلا أي إيموجي إطلاقاً.
- السطر الأول خطّاف قوي بإحدى الصيغ: اقتباس قصير بين علامتي تنصيص ثم ".." ثم الخبر، أو "الجهة: التصريح"، أو سؤال إخباري ثم جوابه. إذا كان عاجلاً ابدأ بـ "#عاجل | ".
- بعده سطران أو ثلاثة قصيرة جداً بالتفاصيل الأهم فقط (المنشور كله بين 250 و550 حرفاً).
- ثم وسم أو وسمان عربيان كحد أقصى في سطر واحد.
- نسبة المعلومات (إلزامي): انسب كل رقم وكل حصيلة ضحايا وكل اتهام وكل ادعاء خلافي داخل النص إلى مصدره الأصلي كما تذكره المصادر:
  الجهة الرسمية أولاً (وزارة، شرطة، دفاع مدني، سلطات صحية، حكومة، وكالة أممية، محكمة، بيان الشركة نفسها، وكالة فضاء، بنك مركزي، مكتب إحصاء)
  بصيغة "قالت وزارة الصحة ..." أو "وفق الدفاع المدني ..."، ثم وكالة الأنباء إن كانت هي الأصل (وكالة الصحافة الفرنسية، رويترز، أسوشيتد برس،
  الوكالة الألمانية، إفي، أنسا، كيودو، شينخوا) بصيغة "وفق وكالة الصحافة الفرنسية". لا تنسب شيئاً إلى جهة لا تذكرها المصادر صراحة.
  ذِكر الجهة في الخبر لا يجعلها مصدراً: انسب المعلومة إلى جهة فقط إذا قالت المصادر إن هذه الجهة هي التي أعلنتها أو صرّحت بها (قال/أعلن/بيان/وفق).
- ممنوع منعاً باتاً ذكر أي قناة تلفزيونية أو موقع إخباري أو صحيفة (مثل الجزيرة، العربية، سكاي نيوز، بي بي سي، فرانس 24، سي إن إن،
  يورونيوز، آر تي، دويتشه فيله، إندبندنت عربية، الشرق...) لا في النص ولا في الوسوم. المصادر أدناه مرقمة [م1] [م2] فقط؛ إن لم تُعرف الجهة
  الأصلية لمعلومة فاكتب "بحسب تقارير" دون تسمية أي وسيلة إعلام.
- لا تكتب سطر "المصادر" أو "المصدر" (نضيفه نحن آلياً)، ولا روابط ولا شعارات ولا حسابات (@) ولا إشارات.
- ممنوع منعاً باتاً طلب التفاعل: لا "شارك" ولا "علّق" ولا "اضغط لايك" ولا "تابعونا" ولا "صوّت". يُسمح نادراً بسؤال رأي محايد واحد في النهاية.
- لغة محايدة: افصل الوقائع عن التصريحات ("قال"، "بحسب")، بلا تهويل ولا صفات انفعالية.
- قيمة مضافة (قاعدة الأصالة في فيسبوك): أجب ضمنياً عن "لماذا يهم هذا؟" أو "ماذا بعد؟" بجملة واحدة مستندة إلى المصادر.
أعد JSON فقط بهذا الشكل:
{"attribution": ["الجهات الأصلية التي نُسبت إليها المعلومات في المصادر بالترتيب: الرسمية أولاً ثم وكالة الأنباء، بالاسم كما ورد (مثال: وزارة الصحة الإسبانية، وكالة الصحافة الفرنسية). قائمة فارغة إن لم تُذكر أي جهة. لا قنوات ولا مواقع إخبارية"], "subject": "سطر موضوع محايد وواضح من 4 إلى 9 كلمات يقول من/ماذا/أين (مثال: حصيلة تصادم قطارين في إسبانيا)، بلا إثارة ولا سؤال", "headline": "عنوان للصورة لا يتجاوز 70 حرفاً", "highlight": "كلمتان أو ثلاث من العنوان نفسه حرفياً هي الأهم", "category": "كلمة واحدة: سياسة/اقتصاد/علوم/صحة/تكنولوجيا/بيئة/دبلوماسية/دولي", "urgent": true/false, "entity": "عنوان مقالة ويكيبيديا الإنجليزية للشخص أو المكان أو المؤسسة الأبرز في الخبر بالضبط (مثال: Emmanuel Macron)", "photo_query": "2-4 English words to find a relevant real photo", "country_en": "English name of the main country", "post": "نص المنشور كاملاً", "safe": true/false, "card_type": "اختر الأنسب: breaking (خبر عاجل حقيقي) / quote (إذا كان جوهر الخبر تصريحاً منقولاً حرفياً عن شخص مسمى) / numbers (إذا احتوت المصادر على 2-4 أرقام مهمة) / facts (خبر معقد يُشرح في 3-4 نقاط) / photo (الباقي)", "kicker": "كلمة أو كلمتان فوق العنوان: اسم البلد أو الملف (مثال: أوكرانيا، الاقتصاد العالمي)", "title": "عنوان قصير جداً للبطاقة لا يتجاوز 28 حرفاً (الخطّاف)", "subline": "سطر تحت العنوان في البطاقة 50-110 حرفاً يضيف معلومتين جديدتين على الأقل غير موجودتين في العنوان (رقم، مكان، نتيجة، موعد) من المصادر حرفياً", "quote": "للتصريح فقط: نص الاقتباس كما ورد في المصادر حرفياً، أقل من 110 حرفاً", "speaker": "اسم صاحب التصريح بالعربية", "role": "صفته", "numbers": [{"value": "رقم كما ورد في المصادر", "label": "ماذا يعني، 2-4 كلمات"}], "facts": ["نقطة قصيرة أقل من 70 حرفاً"]}
فضّل numbers أو facts كلما سمحت المصادر بذلك (بطاقات من تصميمنا تشرح الخبر)، فهي أصلية أكثر من صورة مع عنوان.
البطاقة يجب أن تكون صحيحة 100%: كل رقم واقتباس فيها موجود حرفياً في المصادر، وإلا اختر photo.
اجعل "safe" = false إذا كان الخبر لا يُروى إلا بتفاصيل دموية أو مروّعة، أو كان دعاية لطرف، أو كان رياضياً أو يذكر المغرب أو الإمارات أو الجزائر أو إسرائيل أو عن الدين أو يتضمن عنفاً مصوراً أو معلومات لا يمكن التحقق منها.

المصادر:
"""


# LLM layer (2026-10-29): kahwa_publisher/llm.py routes every task to its own free-model chain (writer / classifier /
# factcheck / utility), tracks quota per provider + model in state/, and holds a story (never publishes it unverified)
# when no fact-checker from a different model family than the writer is available.
def llm(prompt, temperature=0.6, task="writer"):
    """Back-compat entry point (reels, bench): one routed call, returns the validated JSON text. Raises R.Hold."""
    return R.llm(prompt, temperature, task=task)


def writer_quality(d):
    """Cheap Arabic quality gate on the writer's JSON before anything goes to the fact-checker."""
    if not d.get("safe", False):
        return []                                    # unsafe stories are dropped by the caller anyway
    probs = R.arabic_quality(d.get("post") or "", min_len=120)
    for k in ("headline", "title", "subline"):
        if isinstance(d.get(k), str) and d[k].strip():
            probs += [f"{k}: {x}" for x in R.arabic_quality(d[k], min_len=4) if "Latin" not in x]
    return probs


CHECK = """أنت مدقق حقائق صارم في وكالة أنباء دولية. قارن كل جملة في المنشور بالمصادر.
ابحث عن: أي معلومة غير موجودة في المصادر، رقم أو اسم خاطئ، جملة مبهمة أو تغيّر المعنى، مبالغة أو رأي، ركاكة لغوية.
تحقق أيضاً من النسبة: كل "قالت/وفق/أعلنت <جهة>" يجب أن تنسب المصادر المعلومة إلى الجهة نفسها؛ وممنوع ذكر أي قناة أو موقع إخباري أو صحيفة.
إذا صححت، حافظ على نفس البنية تماماً (خطّاف أول بلا إيموجي، تفاصيل قصيرة، وسم أو وسمان، بلا سطر مصادر) وبصياغة أصلية لا تنسخ جمل المصادر.
أعد JSON فقط: {"ok": true/false, "problems": ["..."], "corrected_post": "المنشور بعد التصحيح بنفس البنية إن لزم", "corrected_headline": "العنوان بعد التصحيح إن لزم"}"""


R.register(PROMPT, {"safe": bool})
R.register(CHECK, {"ok": bool}, lambda d: [] if d.get("ok") else
           R.arabic_quality(d.get("corrected_post") or "", min_len=120) if (d.get("corrected_post") or "").strip() else [])

CARD_FIELDS = ("headline", "title", "subline", "kicker", "quote", "speaker", "role", "highlight", "subject")


def write_post(c):
    # outlet names are hidden from the writer: sources are labelled [م1], [م2] … (the outlet list stays internal)
    items = c["items"][:6]
    src = "\n".join(f"- [م{n + 1}] {i['title']} — {i['summary'][:400]}" for n, i in enumerate(items))
    raw, wmeta = R.complete("writer", PROMPT + src, 0.6, validate=writer_quality)
    d = json.loads(raw)
    d["_writer"] = str(wmeta)
    # fact-check / verification must come from a DIFFERENT model family than the writer (Gemma is Google too);
    # if none has a key + quota, R.Hold propagates: the story is held for the next run, never published unverified
    checker = R.bound("factcheck", exclude_families={wmeta.family})
    post = re.sub(r"https?://\S+|www\.\S+", "", d.get("post", "")).strip()
    post = A.sanitize(A.strip_source_lines(post))                   # "بحسب قناة X" -> "بحسب تقارير إعلامية"
    for k in CARD_FIELDS:
        if isinstance(d.get(k), str):
            d[k] = A.sanitize(d[k])
    d["facts"] = [A.sanitize(f) for f in (d.get("facts") or []) if isinstance(f, str)]
    pwords = set(norm(post).split())
    if (d.get("category") or "").strip() in ("رياضة", "رياضي", "رياضية") or \
            sum((k in post) if " " in k else (norm(k) in pwords) for k in SPORTS) >= 2:
        print(json.dumps({"qa_reason": "sports"}))
        return None
    if not d.get("safe", False) or len(post) < 120 or any(b in post for b in BLOCK) or \
            banned_country(post + " " + " ".join(json.dumps(d.get(k, ""), ensure_ascii=False) for k in ("entity", "photo_query", "country_en", "highlight", "title", "subline", "kicker", "quote", "speaker", "role", "numbers", "facts"))):
        print(json.dumps({"qa_reason": "unsafe/short"}))
        return None
    vh = safety.output_hits(post + " " + " ".join(json.dumps(d.get(k, ""), ensure_ascii=False) for k in
                                                 ("headline", "title", "subline", "kicker", "quote", "highlight", "subject",
                                                  "numbers", "facts", "photo_query", "entity")))
    if vh:                                                   # calm factual tone: no graphic / gore wording in what we publish
        print(json.dumps({"qa_reason": "graphic/gore wording in output", "hits": vh[:5]}, ensure_ascii=False))
        return None
    # fact-check pass: a second, independent read compares every claim with the sources
    chk = checker(CHECK + "\n\nالمصادر:\n" + src + "\n\nالمنشور:\n" + post, temperature=0.2)
    chk = json.loads(chk[chk.find("{"): chk.rfind("}") + 1])
    if not chk.get("ok"):
        fixed = (chk.get("corrected_post") or "").strip()
        if len(fixed) < 120:
            print(json.dumps({"qa_reason": "fact-check fix too short"}))
            return None
        post = A.sanitize(A.strip_source_lines(re.sub(r"https?://\S+|www\.\S+", "", fixed)))
        if chk.get("corrected_headline"):
            d["headline"] = chk["corrected_headline"][:80]
    # originality guard (after any correction): reject if any 7-word run is copied from a source
    srcw = norm(src).split()
    grams = {" ".join(srcw[i:i + 7]) for i in range(len(srcw) - 6)}
    pw = norm(post).split()
    if any(" ".join(pw[i:i + 7]) in grams for i in range(len(pw) - 6)):
        print(json.dumps({"qa_reason": "copied 7-gram"}))
        return None
    body = A.strip_source_lines(post)
    if BAIT.search(body):
        print(json.dumps({"qa_reason": "engagement bait"}))
        return None
    if re.search("[\U0001F300-\U0001FAFF\u2600-\u27BF]", post) or re.search(r"(?<!\w)@\w", post) or len(post) > 900:
        print(json.dumps({"qa_reason": "structure"}))
        return None
    # ---- fixed named series: numbers cards are «بالأرقام» (series tag on the card, subject stated on caption line 1)
    if (d.get("card_type") or "") == "numbers" and len(d.get("numbers") or []) >= 2 and (d.get("subject") or "").strip():
        d["series"] = SERIES_NUMBERS
        d["kicker"] = SERIES_NUMBERS
        subj = re.sub(r"[#|\n]+", " ", d["subject"]).strip()[:80]
        post = f"{SERIES_NUMBERS} | {subj}\n" + re.sub(r"^#عاجل \| ", "", post)
    # ---- second verification layer: atomic claims vs source texts (fail closed)
    sources = [f"[م{n + 1}] {i['title']} — {i['summary'][:600]}" for n, i in enumerate(items)]
    v = verify_text(checker, post, sources, kind="post", require_attribution=True)
    if not v:
        print(json.dumps({"qa_reason": "claim verification failed"}))
        return None
    post = A.sanitize(A.strip_source_lines(v["text"]))
    # ---- attribution: «المصدر: <primary sources>» built by code from names the source texts support (else «وكالات»)
    all_src = "\n".join(sources)
    cands = list(d.get("attribution") or []) + [e for e in A.attributions(post) if A.classify(e) in ("official", "wire")]
    line, primary = A.source_line(cands, all_src)
    d["attribution"], d["source_line"] = primary, line
    card_lines = [d.get("headline"), d.get("title"), d.get("subline"), d.get("quote")] + list(d.get("facts") or []) + \
                 [f"{n.get('value', '')} {n.get('label', '')}" for n in (d.get("numbers") or []) if isinstance(n, dict)]
    card_text = "\n".join(x.strip() for x in card_lines if isinstance(x, str) and x.strip())
    if card_text:
        try:
            _claims, bad = verify_check(checker, card_text, sources)
        except R.Hold:
            raise
        except Exception as ex:
            print(json.dumps({"qa_reason": f"card verification error (fail closed): {str(ex)[:120]}"}))
            return None
        if bad:
            print(json.dumps({"qa_reason": "card claims unsupported", "claims": [b["claim"] for b in bad][:4]}, ensure_ascii=False))
            return None
    # the repaired text passes every hard gate again
    pwords = set(norm(post).split())
    pw = norm(post).split()
    if banned_country(post) or any(b in post for b in BLOCK) or BAIT.search(post) or \
            sum((k in post) if " " in k else (norm(k) in pwords) for k in SPORTS) >= 2 or \
            any(" ".join(pw[i:i + 7]) in grams for i in range(len(pw) - 6)) or len(post) > 950 or \
            safety.output_hits(post + " " + (d.get("headline") or "")):
        print(json.dumps({"qa_reason": "verified text failed a hard gate"}))
        return None
    # ---- attribution hard gates (code, after generation): no competing outlet anywhere public, sourced attributions only,
    # numbers / casualty counts / accusations carry an in-text attribution
    public = "\n".join([post, line] + [d.get(k) for k in CARD_FIELDS if isinstance(d.get(k), str)] + list(d.get("facts") or []) +
                       [f"{n.get('value', '')} {n.get('label', '')}" for n in (d.get("numbers") or []) if isinstance(n, dict)])
    hits = A.outlet_hits(public)
    if hits:
        print(json.dumps({"qa_reason": "names a competing outlet", "hits": hits[:5]}, ensure_ascii=False))
        return None
    bad_attr = A.unsupported_attributions(post, all_src)
    if bad_attr:
        print(json.dumps({"qa_reason": "unsupported attribution", "attr": bad_attr[:4]}, ensure_ascii=False))
        return None
    if A.lacks_attribution(post):
        print(json.dumps({"qa_reason": "numbers/casualties/accusations without in-text attribution"}, ensure_ascii=False))
        return None
    post = A.set_source_line(post, line)
    d["verification"] = v["report"]
    d["verification"]["models"] = {"writer": d.pop("_writer", ""), "checker": sorted(set(checker.models))}
    d["post"] = post
    blob = post + " " + (d.get("headline") or "") + " " + (d.get("title") or "")
    d["no_photo"] = sum(w in blob for w in VIOLENT) >= 1 or safety.is_conflict(blob)   # war story -> own visual, no photo
    sub = d.get("subline") or ""
    if NUM.unsupported(sub, src) or BAIT.search(sub):
        sub = ""                                    # every figure on the card must exist in the sources
    d["subline"] = sub
    if sub:
        d["headline_full"], d["headline"] = d.get("headline"), d["subline"][:120]
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


# ---------------------------------------------------------------- reel story choice
REELS_SEEN = ROOT / "state" / "reels_seen.json"


def pick_reel_cluster(min_sources=2):
    """Top verified story for a reel (first of pick_reel_clusters)."""
    cs, seen = pick_reel_clusters(min_sources, n=1)
    return (cs[0] if cs else None), seen


def pick_reel_clusters(min_sources=2, n=3):
    """Ranked verified stories for a reel: >=2 outlets, passes the legal/sports/banned/religion filters, not reeled in 3 days.
    Returns ([up to n allowed clusters], seen). The reel maker tries them in order: a story that fails a gate (numbers,
    primary source, spelling, fact-check) is dropped and the next one is written (2026-10-31)."""
    try:
        seen = json.loads(REELS_SEEN.read_text())
    except Exception:
        seen = {"stories": []}
    cutoff = time.time() - 3 * 86400
    seen["stories"] = [s for s in seen["stories"] if s["t"] > cutoff]
    clusters = cluster(fetch())
    if os.getenv("SIGNALS", "on").lower() != "off":
        try:   # early-warning ranking only; the >=2-outlet rule below is unchanged
            signals.apply(clusters, tokens, blocked, lambda t: banned_country(t) or any(b in t for b in BLOCK) or
                          sum((k in t) if " " in k else (norm(k) in set(norm(t).split())) for k in SPORTS) >= 1)
        except Exception as ex:
            print(json.dumps({"signal_error": "apply", "err": str(ex)[:200]}))
    cands = []
    for c in clusters:
        c = purge_items(c)                      # religion / banned-country items out of mixed clusters
        if not c["items"] or len(c["sources"]) < min_sources or blocked(c) or is_seen(seen, c):
            continue
        text = " ".join(i["title"] + " " + i["summary"] for i in c["items"])
        if not safety.keyword_ok(c):            # war stories allowed (graphic-only reel); graphic/gore sources are not
            continue
        words = set(norm(text).split())
        if any((k in text) if " " in k else (norm(k) in words) for k in SPORTS):   # stricter than posts: any sports term
            continue
        cands.append(c)
        if len(cands) >= 8:
            break
    allowed = safety.classify(R.bound("classifier"), cands) if cands else {}   # LLM category check, fail closed
    return [c for c in cands if allowed.get(c["key"])][:n], seen


def mark_reeled(seen, c):
    seen["stories"].append({"t": time.time(), "tok": sorted(c["tok"])[:40], "key": c["key"]})
    REELS_SEEN.parent.mkdir(exist_ok=True)
    REELS_SEEN.write_text(json.dumps(seen, ensure_ascii=False))


# ---------------------------------------------------------------- main
def main():
    a = argparse.ArgumentParser()
    a.add_argument("--max", type=int, default=int(os.getenv("NEWS_PER_RUN", "2")))
    a.add_argument("--dry", action="store_true", help="test run: nothing written to queue/, state/ or media/")
    a.add_argument("--out", default=os.getenv("KAHWA_DRY_OUT", "/tmp/kahwa-dry"))
    x = a.parse_args()
    out_dir = Path(x.out)
    if x.dry:
        out_dir.mkdir(parents=True, exist_ok=True)
    queued = len(list(QUEUE.glob("*.json"))) if QUEUE.exists() else 0
    if queued >= int(os.getenv("MAX_QUEUE", "6")) and not x.dry:
        print(json.dumps({"skip": "queue full", "queued": queued}))
        return 0
    ok, why = R.preflight()
    print(json.dumps({"llm_preflight": ok, "detail": why}, ensure_ascii=False), flush=True)
    if not ok and not x.dry:          # no writer, or no independent fact-checker: hold everything, spend no quota
        print(json.dumps({"held": why}, ensure_ascii=False))
        return 0
    seen = load_seen()
    clusters = cluster(fetch())
    if os.getenv("SIGNALS", "on").lower() != "off":
        try:
            signals.apply(clusters, tokens, blocked, lambda t: banned_country(t) or any(b in t for b in BLOCK) or
                          sum((k in t) if " " in k else (norm(k) in set(norm(t).split())) for k in SPORTS) >= 1)
        except Exception as ex:      # signals are best-effort: never block the newsroom
            print(json.dumps({"signal_error": "apply", "err": str(ex)[:200]}))
    made = tried = 0
    tier1 = {"BBC عربي", "فرانس 24", "DW عربية", "أخبار الأمم المتحدة"}
    cands = []
    for c in clusters:
        c = purge_items(c)                           # religion / banned-country items out of mixed clusters
        if not c["items"]:
            continue
        multi = len(c["sources"]) >= int(os.getenv("MIN_SOURCES", "2"))
        # fresh story from a tier-1 outlet; judged on the feeds-only score so early-warning boosts never let a
        # single-outlet story through
        trusted_single = c["sources"][0] in tier1 and c.get("base_score", c["score"]) >= 8
        if not (multi or trusted_single) or blocked(c) or is_seen(seen, c):
            continue
        if not safety.keyword_ok(c):                 # graphic / gore sources rejected at selection (war news itself allowed)
            continue
        cands.append(c)
        if len(cands) >= 10:
            break
    try:
        allowed = safety.classify(R.bound("classifier"), cands) if cands else {}    # LLM category check, fail closed
    except Exception as ex:
        print(json.dumps({"safety_classify_error": str(ex)[:200]}))
        allowed = {}
    for c in cands:
        if made >= x.max or tried >= int(os.getenv("MAX_TRIES", "5")):
            break
        if not allowed.get(c["key"]):
            continue
        tried += 1
        try:
            d = write_post(c)
        except R.Hold as ex:          # quota exhausted / no independent checker: not marked seen, next run retries
            print(json.dumps({"held": str(ex)[:400], "title": c["items"][0]["title"][:90]}, ensure_ascii=False))
            break
        except Exception as ex:
            print(json.dumps({"llm_error": str(ex)[:300]}))
            break
        seen["stories"].append({"t": time.time(), "tok": sorted(c["tok"])[:40], "key": c["key"]})
        if not d:
            print(json.dumps({"rejected_by_qa": c["items"][0]["title"][:90]}, ensure_ascii=False))
            continue
        sid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M") + "-" + c["key"]
        img_dir = out_dir if x.dry else MEDIA
        img_dir.mkdir(exist_ok=True)
        img = img_dir / f"{sid}.jpg"
        from .cards import render_card
        ct = d.get("card_type") or "photo"
        if d.get("urgent") and ct == "photo":
            ct = "breaking"
        res = render_card(dict(d, card_type=ct, source="، ".join(d.get("attribution") or []) or A.GENERIC), img)
        credit = res["credit"]
        if credit and not credit.startswith("خريطة"):     # photo credit above the final «المصدر:» line
            d["post"] = A.set_source_line(A.strip_source_lines(d["post"]) + f"\nصورة أرشيفية: {credit}", d["source_line"])
        print(json.dumps({"verified_by_outlets": c["sources"], "attribution": d.get("attribution") or [A.GENERIC]},
                         ensure_ascii=False))              # internal log only (requirement: >=2 outlets, never published)
        url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/media/{img.name}"
        post = {"id": sid, "text": d["post"], "media": [{"type": "image", "url": url}], "kind": "post",
                "title": d.get("headline_full") or d["headline"], "priority": 3 if d.get("urgent") else 5, "sources": c["sources"],   # internal: outlets used for verification, never published
                "attribution": d.get("attribution") or [A.GENERIC],
                "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                # give GitHub time to serve the pushed image before any provider fetches it
                "not_before": datetime.fromtimestamp(time.time() + 900, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        if d.get("series"):
            post["series"] = d["series"]
        post["signals"] = c.get("signals") or {}
        post["verification"] = {"result": d["verification"]["result"], "rounds": d["verification"]["rounds"]}
        if x.dry:
            print(json.dumps(post, ensure_ascii=False, indent=1))
            (out_dir / f"{sid}.json").write_text(json.dumps(dict(post, verification=d["verification"]), ensure_ascii=False, indent=1))
        else:
            QUEUE.mkdir(exist_ok=True)
            (QUEUE / f"{sid}.json").write_text(json.dumps(post, ensure_ascii=False, indent=1))
        made += 1
        # reels are no longer rendered here: .github/workflows/reel.yml (HyperFrames v2 renderer) picks the
        # top verified story itself at the reel slots (python -m kahwa_publisher.reels.make).
    if not x.dry:
        SEEN.parent.mkdir(exist_ok=True)
        SEEN.write_text(json.dumps(seen, ensure_ascii=False))
    print(json.dumps({"clusters": len(clusters), ("written_dry" if x.dry else "queued_now"): made}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except R.Hold as ex:              # quota exhaustion never fails the workflow
        print(json.dumps({"held": str(ex)[:400]}, ensure_ascii=False))
        raise SystemExit(0)
