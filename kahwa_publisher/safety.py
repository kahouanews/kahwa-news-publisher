"""No war / no violence gate (user hard rule 2026-10-29) for posts, cards and reels.

Applied at SELECTION, before anything is written, and again on everything we generate:

1. `violence_hits(text)`  — keyword gate. Arabic stems with every prefixed form (و/ف + ب/ل/ك + ال / لل, harakat- and
   tatweel-proof) + English words (source titles are sometimes English). One hit = the story is rejected.
   Covers wars, missile / drone strikes, interceptions, air defences, armed attacks, bombings, shelling, raids, military
   operations, clashes, armed groups, assassinations, shootings, casualties of attacks.
   Exceptions handled in code: «وقف إطلاق النار» (ceasefire) is not a shooting; a space «صاروخ» (rocket launch with a
   space context and no other hit) is not a missile.
2. `classify(llm, stories)` — one Gemini call classifies the remaining candidates (titles + summaries). Fail closed: any
   LLM / parse error, or a story missing from the answer, means "not allowed".
   Rejected: war_conflict, military (operations, weapons, armed forces), attack_terror, armed_crime, or violence=true, or
   attack_details=true. Diplomacy / ceasefire talks pass only with attack_details=false. Disasters / accidents pass only
   with graphic=false (the card then gets our own visual through the existing VIOLENT → no_photo guard).
3. `output_hits(text)` — the generated post / card fields / reel lines must contain no violence word at all (same list),
   so a diplomacy story cannot carry attack details or the word «حرب» into what we publish.
"""
import json
import re

AR_LETTER = "\u0600-\u06FF"
PREFIX = r"(?:[وف]?[بلك]?(?:ال|لل)?|لل)"
SUFFIX = r"(?:ي|ية|يه|يين|يون|ات|ان|ين|ون|ها|هم|ه|ة|ا)?"

# Arabic stems (bare, no ال). Each matches with any prefix/suffix above.
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


def violence_hits(text):
    """Violence / war terms in `text` (Arabic incl. prefixed forms, and English). [] = clean."""
    t = _ALLOWED.sub(" ", _norm(text))
    hits = [m.group(0).strip() for m in _AR.finditer(t)]
    hits += [m.group(0) for m in _EN.finditer(t)]
    rockets = [m.group(0) for m in _AR_ROCKET.finditer(t)]
    if rockets and not (SPACE_CTX.search(t) and not hits):      # a space launch rocket is fine, a missile is not
        hits += rockets
    return list(dict.fromkeys(hits))


def output_hits(text):
    """Gate for what WE publish (post, card fields, reel lines, caption). Same list: nothing violent, no «حرب»."""
    return violence_hits(text)


def cluster_text(c):
    return " ".join((i.get("title") or "") + " " + (i.get("summary") or "") for i in c.get("items", []))


CLASSIFY = """أنت محرر معايير في صفحة أخبار عامة تمنع منعاً باتاً أخبار الحروب والعنف.
صنّف كل خبر أدناه (العنوان والملخص). أعد JSON فقط بالشكل:
{"stories": [{"id": 1, "category": "war_conflict|military|attack_terror|armed_crime|diplomacy|politics|economy|science|health|technology|environment|disaster_accident|society|other", "violence": true/false, "attack_details": true/false, "graphic": true/false}]}
- war_conflict: أي خبر عن حرب أو نزاع مسلح أو جبهات أو تصعيد بين دول/جماعات مسلحة.
- military: عمليات عسكرية، تحركات قوات، تدريبات، أسلحة وصفقات سلاح وطائرات مقاتلة، صواريخ، دفاع جوي، اعتراض صواريخ أو مسيرات.
- attack_terror: هجمات، تفجيرات، غارات، قصف، ضربات بمسيرات أو صواريخ، اغتيالات، هجمات إرهابية، احتجاز رهائن.
- armed_crime: إطلاق نار، عصابات مسلحة، قتل عمد.
- diplomacy: محادثات ومفاوضات واتفاقات ووقف إطلاق النار (حتى لو كانت عن حرب).
- violence=true إذا ذكر الخبر أي عنف مسلح أو قتلى/جرحى بسبب هجوم أو قصف أو قتال.
- attack_details=true إذا ذكر الخبر أي هجوم أو ضربة أو صاروخ أو مسيرة أو اعتراض أو قصف أو اشتباك أو قتلى بسبب ذلك، ولو في جملة واحدة.
- graphic=true إذا تضمن تفاصيل دموية أو جثثاً أو مشاهد مروعة.
كن متشدداً: عند الشك اجعل violence=true وattack_details=true.

الأخبار:
"""
REJECT_CATS = {"war_conflict", "military", "attack_terror", "armed_crime"}


def verdict(row):
    """Decision for one classifier row. True = allowed."""
    if not isinstance(row, dict):
        return False
    cat = str(row.get("category", "")).strip().lower()
    if not cat or cat in REJECT_CATS:
        return False
    for k in ("violence", "attack_details", "graphic"):     # missing flag = fail closed
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
    """Selection keyword gate on a cluster (all titles + summaries)."""
    hits = violence_hits(cluster_text(c))
    if hits:
        print(json.dumps({"safety_reject": "keywords", "title": c["items"][0]["title"][:90], "hits": hits[:5]},
                         ensure_ascii=False), flush=True)
    return not hits
