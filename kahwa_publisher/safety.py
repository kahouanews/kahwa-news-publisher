"""Editorial safety for posts, cards and reels (owner decision 2026-10-29: WAR NEWS IS ALLOWED).

War / conflict / military news is covered like any other world news, in a factual, calm news tone. What is enforced:

1. `graphic_hits(text)` - graphic / gore / sensational wording (bodies torn apart, blood, beheadings, «مجزرة مروعة», «مشاهد
   مروعة» ...). Never in anything we publish (`output_hits`); a story whose SOURCE titles/summaries carry unmistakable gore
   (`graphic_source_hits`) is rejected at selection (`keyword_ok`).
2. `classify(llm, stories)` - one Gemini call over the candidates. Fail closed (LLM / parse error or a missing row = not
   allowed). Rejected ONLY for: graphic/gore content, propaganda (one side's claims presented as fact / incitement),
   unverified claims (unconfirmed reports, rumours, single-party claims with no confirmation), sports, or a story that
   cannot be told without naming a banned country (Morocco, UAE, Algeria, Israel). Wars, strikes, military, diplomacy,
   disasters all pass when none of these flags is set.
3. `conflict_hits(text)` - detects a war / armed-violence story (Arabic stems with every prefixed form + English). It no
   longer rejects anything: it decides the PICTURE - such stories never get a photo or footage (Meta does not recommend
   war/violent imagery): cards fall back to map / numbers / facts / text, reels are graphic-only (map + text).
The banned-countries TEXT ban is not here and is unchanged (newsroom.banned_country, reels.render.banned_hits).
"""
import json
import re

AR_LETTER = "\u0600-\u06FF"
PREFIX = r"(?:[وف]?[بلك]?(?:ال|لل)?|لل)"
SUFFIX = r"(?:ي|ية|يه|يين|يون|ات|ان|ين|ون|ها|هم|ه|ة|ا)?"

# Arabic stems (bare, no ال). Each matches with any prefix/suffix above. CONFLICT detection (picture choice), not a ban.
AR_STEMS = [
    # war / conflict
    "حرب", "حروب", "الحرب", "معركة", "معارك", "قتال", "اقتتال", "اشتباك", "اشتباكات", "مواجهات مسلحة", "نزاع مسلح",
    "جبهة القتال", "خطوط المواجهة", "غزو", "اجتياح", "توغل", "احتلال",
    # strikes / weapons
    "قصف", "غارة", "غارات", "ضربة جوية", "ضربات جوية", "ضربة عسكرية", "ضربات عسكرية", "ضربة صاروخية", "ضربات صاروخية",
    "صاروخ باليستي", "صواريخ باليستية", "صاروخ كروز", "صواريخ كروز", "قذيفة", "قذائف", "مدفعية", "راجمات",
    "طائرة مسيرة", "طائرات مسيرة", "طائرة مسيّرة", "طائرات مسيّرة", "مسيّرة", "مسيّرات", "مسيرات", "درون",
    "اعتراض صاروخ", "اعتراض صواريخ", "اعتراض مسيرة", "اعتراض مسيرات", "اعتراض طائرة", "اعترضت الدفاعات", "اعتراضه",
    "الدفاعات الجوية", "دفاعات جوية", "الدفاع الجوي", "دفاع جوي", "منظومة دفاع", "القبة الحديدية", "باتريوت",
    "تفجير", "تفجيرات", "انتحاري", "عبوة ناسفة", "عبوات ناسفة", "مفخخة", "مفخخ", "لغم", "ألغام",
    # attacks / armed violence
    "هجوم", "هجمات", "هجوما", "هجوماً", "اعتداء مسلح", "مسلح", "مسلحون", "مسلحين", "جماعة مسلحة", "فصائل مسلحة", "ميليشيا",
    "ميليشيات", "اغتيال", "اغتيالات", "إطلاق نار", "اطلاق نار", "إطلاق النار", "اطلاق النار", "مجزرة", "مذبحة", "إرهاب",
    "إرهابي", "ارهاب", "ارهابي", "رهائن", "اختطاف", "قناص", "قنص",
    # military operations / forces in action
    "عملية عسكرية", "عمليات عسكرية", "العملية العسكرية", "حملة عسكرية", "تصعيد عسكري", "تحرك عسكري", "قوات الاحتلال",
    "حوثي", "الحوثي", "الحوثيين", "الحوثيون", "حزب الله", "داعش", "تنظيم الدولة", "حركة حماس", "طالبان",
    "مقاتلة", "مقاتلات", "دبابة", "دبابات", "قاذفة", "قاذفات", "سلاح نووي", "أسلحة", "اسلحة", "ذخائر", "ذخيرة",
    # casualties of attacks (generic casualty words like قتلى/ضحايا are NOT here: disasters may pass, without graphic detail)
    "شهيد", "شهداء", "استشهاد", "استشهد",
]
# ceasefire phrases are allowed (diplomacy), they are removed before matching
AR_ALLOWED = ["وقف إطلاق النار", "وقف اطلاق النار", "لوقف إطلاق النار", "لوقف اطلاق النار", "بوقف إطلاق النار", "بوقف اطلاق النار",
              "ووقف إطلاق النار", "ووقف اطلاق النار"]
SPACE_CTX = re.compile(r"فضاء|الفضاء|فضائي|ناسا|مركبة|قمر صناعي|أقمار|اقمار|سبيس إكس|سبيس اكس|ستارشيب|محطة الفضاء|مدار|"
                       r"nasa|spacex|starship|orbit|satellite|space", re.I)
AR_ROCKET = ["صاروخ", "صواريخ"]
EN = [r"wars?", r"warfare", r"missiles?", r"ballistic", r"air ?strikes?", r"strikes? on", r"drone (?:strike|attack)s?",
      r"drones?", r"intercept\w*", r"air defen[cs]e", r"shell(?:ing|ed)", r"bomb\w*", r"explosives?", r"attack\w*",
      r"militants?", r"militia\w*", r"gunm[ae]n", r"shooting", r"assassinat\w*", r"terror\w*", r"massacre", r"clash\w*",
      r"fighting", r"invasion", r"invad\w*", r"offensive", r"military operation", r"troops?", r"artillery", r"rockets? fire",
      r"houthis?", r"hezbollah", r"hamas", r"isis", r"killed in (?:an? )?(?:attack|strike|raid)", r"raids?", r"weapons?",
      r"fighter jets?", r"casualties"]

_DIAC = re.compile(r"[\u064B-\u0652\u0640]")


def _norm(t):
    t = _DIAC.sub("", t or "")
    return re.sub("[إأآ]", "ا", t)


def _ar_re(stems):
    alts = sorted({_norm(s) for s in stems}, key=len, reverse=True)
    body = "|".join(re.escape(a).replace(r"\ ", r"\s+") for a in alts)
    return re.compile(rf"(?<![{AR_LETTER}]){PREFIX}(?:{body}){SUFFIX}(?![{AR_LETTER}])")


_AR = _ar_re([s[2:] if s.startswith("ال") and len(s) > 4 else s for s in AR_STEMS])
_AR_ROCKET = _ar_re(AR_ROCKET)
_EN = re.compile(r"(?<![a-z])(?:" + "|".join(EN) + r")(?![a-z])", re.I)
_ALLOWED = re.compile("|".join(re.escape(_norm(a)) for a in AR_ALLOWED))

# ---- graphic / gore / sensational wording (never published; graphic sources rejected at selection)
GRAPHIC_AR = [
    "أشلاء", "اشلاء", "جثث متفحمة", "جثث ممزقة", "جثث مشوهة", "جثة متفحمة", "جثة مشوهة", "جثث متناثرة", "تمثيل بالجثث",
    "التمثيل بالجثث", "قطع رأس", "قطع الرأس", "قطع رؤوس", "مقطوع الرأس", "مقطوعة الرأس", "ذبح", "نحر", "بركة دماء",
    "برك الدماء", "بحر من الدماء", "حمام دم", "حمام دماء", "مضرج بالدماء", "مضرجة بالدماء", "مضرجين بالدماء",
    "غارقين في دمائهم", "غارق في دمائه", "سالت الدماء", "دماء", "دموي", "دموية", "مجزرة مروعة", "مشاهد مروعة",
    "صور مروعة", "فيديو مروع", "مروع", "مروعة", "مرعب", "مرعبة", "بشع", "بشعة", "وحشي", "وحشية", "تقشعر",
    "مجزرة", "مجازر", "مذبحة", "مذابح",
]
GRAPHIC_EN = [r"gor(?:e|y)", r"gruesome", r"grisly", r"graphic (?:video|footage|images?|photos?|content)", r"behead\w*",
              r"decapitat\w*", r"dismember\w*", r"mutilat\w*", r"charred bod(?:y|ies)", r"body parts", r"bloodbath",
              r"pools? of blood", r"blood-?soaked", r"horrific", r"massacre\w*"]
# unmistakable gore: a source built on these is rejected at selection (tone words like «مجزرة» / «مروع» are only rewritten
# out of OUR text by the output gate, the story itself can still be told calmly)
GRAPHIC_STRONG_AR = ["أشلاء", "اشلاء", "جثث متفحمة", "جثث ممزقة", "جثث مشوهة", "جثة متفحمة", "جثة مشوهة", "تمثيل بالجثث",
                     "التمثيل بالجثث", "قطع رأس", "قطع الرأس", "قطع رؤوس", "مقطوع الرأس", "مقطوعة الرأس", "مشاهد مروعة",
                     "صور مروعة", "فيديو مروع"]
GRAPHIC_STRONG_EN = [r"gor(?:e|y)", r"gruesome", r"grisly", r"graphic (?:video|footage|images?|photos?|content)", r"behead\w*",
                     r"decapitat\w*", r"dismember\w*", r"mutilat\w*", r"charred bod(?:y|ies)", r"body parts"]
_GR_AR = _ar_re(GRAPHIC_AR)
_GRS_AR = _ar_re(GRAPHIC_STRONG_AR)
_GRS_EN = re.compile(r"(?<![a-z])(?:" + "|".join(GRAPHIC_STRONG_EN) + r")(?![a-z])", re.I)
_GR_EN = re.compile(r"(?<![a-z])(?:" + "|".join(GRAPHIC_EN) + r")(?![a-z])", re.I)


def conflict_hits(text):
    """War / armed-violence terms in `text` (Arabic incl. prefixed forms, and English). [] = not a conflict story.
    Used only to choose the picture (no photo / footage for such stories), never to reject a story."""
    t = _ALLOWED.sub(" ", _norm(text))
    hits = [m.group(0).strip() for m in _AR.finditer(t)]
    hits += [m.group(0) for m in _EN.finditer(t)]
    rockets = [m.group(0) for m in _AR_ROCKET.finditer(t)]
    if rockets and not (SPACE_CTX.search(t) and not hits):      # a space launch rocket is not a conflict term
        hits += rockets
    return list(dict.fromkeys(hits))


def is_conflict(text):
    return bool(conflict_hits(text))


def graphic_hits(text):
    """Graphic / gore / sensational wording in `text`. [] = clean."""
    t = _norm(text)
    hits = [m.group(0).strip() for m in _GR_AR.finditer(t)] + [m.group(0) for m in _GR_EN.finditer(t)]
    return list(dict.fromkeys(hits))


def graphic_source_hits(text):
    """Unmistakable gore in SOURCE text (selection gate)."""
    t = _norm(text)
    hits = [m.group(0).strip() for m in _GRS_AR.finditer(t)] + [m.group(0) for m in _GRS_EN.finditer(t)]
    return list(dict.fromkeys(hits))


def output_hits(text):
    """Gate for what WE publish (post, card fields, reel lines, caption): calm factual tone, no graphic / gore wording.
    War words themselves («حرب», «قصف», «صاروخ» ...) are allowed."""
    return graphic_hits(text)


def cluster_text(c):
    return " ".join((i.get("title") or "") + " " + (i.get("summary") or "") for i in c.get("items", []))


CLASSIFY = """أنت محرر معايير في صفحة أخبار عالمية عامة. أخبار الحروب والنزاعات والعمليات العسكرية مسموحة وتُغطّى بأسلوب وقائعي هادئ.
صنّف كل خبر أدناه (العنوان والملخص). أعد JSON فقط بالشكل:
{"stories": [{"id": 1, "category": "war_conflict|military|attack|diplomacy|politics|economy|science|health|technology|environment|disaster_accident|society|sports|other", "graphic": true/false, "propaganda": true/false, "unverified": true/false, "needs_banned_country": true/false}]}
- graphic=true فقط إذا كان جوهر الخبر تفاصيل دموية أو جثثاً أو تمثيلاً أو قطع رؤوس أو مشاهد مروعة لا يمكن روايته من دونها.
- propaganda=true إذا كان الخبر دعاية لطرف (ادعاءات طرف واحد تُقدَّم كحقائق، تمجيد أو تحريض، بيانات تعبئة) لا خبراً وقائعياً.
- unverified=true إذا كان قائماً على ادعاءات غير مؤكدة أو شائعات أو "أنباء غير مؤكدة" أو رواية طرف واحد دون تأكيد مستقل.
- needs_banned_country=true إذا كان لا يمكن رواية الخبر دون ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل (بأي صيغة).
- الحرب والقصف والضربات والصواريخ والمسيرات والعمليات العسكرية والدبلوماسية والكوارث ليست سبباً للرفض بحد ذاتها.
عند الشك في أي علامة من العلامات الأربع اجعلها true.

الأخبار:
"""
REJECT_CATS = {"sports"}
FLAGS = ("graphic", "propaganda", "unverified", "needs_banned_country")


def verdict(row):
    """Decision for one classifier row. True = allowed. War / military / attack stories pass unless a flag is set."""
    if not isinstance(row, dict):
        return False
    cat = str(row.get("category", "")).strip().lower()
    if not cat or cat in REJECT_CATS:
        return False
    for k in FLAGS:                                       # missing flag = fail closed
        if row.get(k) is not False:
            return False
    return True


def classify(llm, clusters, limit=12):
    """One Gemini call for up to `limit` candidate clusters -> {cluster key: allowed bool}. Fail closed on any error."""
    out = {c["key"]: False for c in clusters[:limit]}
    if not clusters:
        return out
    lines = []
    for n, c in enumerate(clusters[:limit], 1):
        it = c["items"][:3]
        lines.append(f"[{n}] " + " | ".join(((i.get("title") or "") + " — " + (i.get("summary") or "")[:220]) for i in it))
    try:
        raw = llm(CLASSIFY + "\n".join(lines), temperature=0.0)
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        rows = {int(r.get("id")): r for r in d.get("stories", []) if isinstance(r, dict) and str(r.get("id", "")).isdigit()}
    except Exception as ex:
        print(json.dumps({"safety_classify_error": str(ex)[:200]}), flush=True)
        return out
    for n, c in enumerate(clusters[:limit], 1):
        out[c["key"]] = verdict(rows.get(n))
        if not out[c["key"]]:
            print(json.dumps({"safety_reject": "classifier", "title": c["items"][0]["title"][:90], "row": rows.get(n)},
                             ensure_ascii=False), flush=True)
    return out


def keyword_ok(c):
    """Selection keyword gate on a cluster (all titles + summaries): only graphic / gore sources are rejected."""
    hits = graphic_source_hits(cluster_text(c))
    if hits:
        print(json.dumps({"safety_reject": "graphic", "title": c["items"][0]["title"][:90], "hits": hits[:5]},
                         ensure_ascii=False), flush=True)
    return not hits
