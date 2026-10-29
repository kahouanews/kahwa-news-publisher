"""Source attribution policy (user-approved 2026-10-29).

We attribute every fact (Berne art. 10bis, Meta originality "credit", reader trust) but we never advertise competing
channels. So the PUBLIC text names only the primary source of a fact:

  official / primary body (ministry, police, civil defence, health authority, government, UN agency, court,
  a company's own statement, space agency, central bank, statistics office ...)  >  wire agency (AFP, Reuters, AP, dpa,
  EFE, ANSA, Kyodo, Xinhua ...)  >  the generic «وكالات».

Competing TV channels / news sites / outlets (Al Jazeera, Al Arabiya, Sky News Arabia, BBC Arabic, France 24, CNN,
Euronews, RT, CGTN, Asharq, TRT, DW, Independent Arabia ...) are NEVER named in a post, card, reel or the «المصدر:» line.
`outlet_hits()` is the hard gate (code, after generation); the >=2-outlet verification stays internal (run logs/state).

Our own value-add (series tags, maps, compilations we computed) is credited «قهوة نيوز»; underlying data keeps its
primary source.
"""
import re

GENERIC = "وكالات"
OURS = "قهوة نيوز"

# ------------------------------------------------------------------ competing outlets (denylist)
# Unambiguous names: a hit anywhere in public text is a violation.
OUTLETS_AR = [
    "الجزيرة نت", "الجزيرة مباشر", "قناة الجزيرة", "شبكة الجزيرة", "الجزيرة الإخبارية", "الجزيرة الانجليزية",
    "العربية نت", "العربية.نت", "قناة العربية", "قناة الحدث", "العربية الحدث",
    "سكاي نيوز", "سكاي نيوز عربية", "بي بي سي", "بي.بي.سي", "بي بي سي عربي", "فرانس 24", "فرانس24",
    "سي إن إن", "سي ان ان", "سي.إن.إن", "يورونيوز", "يورو نيوز", "روسيا اليوم", "آر تي", "ار تي عربي",
    "سي جي تي إن", "تي آر تي", "تي ار تي", "تي آر تي عربي", "دويتشه فيله", "دويتشه فيلله", "دي دبليو", "دي في عربية",
    "إندبندنت عربية", "اندبندنت عربية", "إندبندنت", "اندبندنت", "الإندبندنت", "العربي الجديد", "التلفزيون العربي",
    "تلفزيون العربي", "الميادين", "القاهرة الإخبارية", "قناة الحرة", "الحرة نت", "قناة المنار",
    "الشرق للأخبار", "الشرق بلومبرغ", "الشرق بلومبيرغ", "قناة الشرق", "صحيفة الشرق الأوسط", "قناة الغد",
    "بلومبرغ", "بلومبيرغ", "سبوتنيك", "نيويورك تايمز", "واشنطن بوست", "الغارديان", "غارديان", "فايننشال تايمز",
    "وول ستريت جورنال", "فوكس نيوز", "إن بي سي", "سي بي إس", "أي بي سي نيوز", "أكسيوس", "بوليتيكو", "لوموند",
    "دير شبيغل", "الشرق الأوسط اللندنية", "النهار العربي",
]
# Latin names (case-insensitive) and short all-caps brands (case-sensitive, so "rt"/"dw" inside words never hit).
OUTLETS_LATIN = [
    r"al[\s-]?jazeera", r"aljazeera", r"al[\s-]?arabiya", r"alarabiya", r"al[\s-]?hadath", r"sky\s?news", r"bbc",
    r"france\s?24", r"euronews", r"russia\s+today", r"cgtn", r"asharq", r"deutsche\s+welle", r"independent\s+arabia",
    r"al[\s-]?mayadeen", r"al[\s-]?araby", r"al[\s-]?hurra", r"alhurra", r"bloomberg", r"sputnik", r"new\s+york\s+times",
    r"washington\s+post", r"the\s+guardian", r"financial\s+times", r"wall\s+street\s+journal", r"fox\s+news", r"abc\s+news",
    r"axios", r"politico", r"le\s+monde", r"der\s+spiegel",
]
OUTLETS_CAPS = ["CNN", "RT", "TRT", "DW", "NBC", "CBS", "BBC", "CGTN", "NYT"]
# Ambiguous one-word names that are also ordinary words (الجزيرة = the island / peninsula, العربية = Arabic, الحدث = the
# event, الحرة = free, الشرق = the East ...): a violation only in an attribution context or as a bare source item.
OUTLETS_AMBIG = ["الجزيرة", "العربية", "الحدث", "الحرة", "الشرق", "الغد", "المنار", "النهار", "الأخبار", "المدن", "عربي21"]
ATTR_CTX = (r"(?:وفق(?:ا|اً)?\s*ل?|بحسب|حسب|نقل(?:ا|اً)\s+عن|قالت|قال|ذكرت|ذكر|أفادت|افادت|أوردت|اوردت|نقلت|بثت|"
            r"نشرت|أكدت|اكدت|كشفت|مراسل|مراسلة|قناة|شبكة|صحيفة|تلفزيون|لموقع|لقناة|لشبكة|لصحيفة|لتلفزيون)")

# ------------------------------------------------------------------ wire agencies (allowed, canonical Arabic name)
WIRES = {
    "وكالة الصحافة الفرنسية": ["وكالة الصحافة الفرنسية", "فرانس برس", "فرانس بريس", "أ ف ب", "أ.ف.ب", "ا ف ب", "AFP",
                               "Agence France-Presse"],
    "رويترز": ["رويترز", "وكالة رويترز", "Reuters"],
    "أسوشيتد برس": ["أسوشيتد برس", "اسوشيتد برس", "أسوشييتد برس", "اسوشييتد برس", "أ ب", "Associated Press", "AP"],
    "الوكالة الألمانية": ["وكالة الأنباء الألمانية", "الوكالة الألمانية", "د ب أ", "د.ب.أ", "dpa"],
    "وكالة إفي": ["وكالة إفي", "إفي", "EFE"],
    "وكالة أنسا": ["أنسا", "انسا", "ANSA"],
    "وكالة كيودو": ["كيودو", "Kyodo"],
    "وكالة شينخوا": ["شينخوا", "شينخوا الصينية", "Xinhua"],
    "وكالة تاس": ["تاس", "TASS"],
    "وكالة الأناضول": ["الأناضول", "الاناضول", "Anadolu"],
    "وكالة يونهاب": ["يونهاب", "Yonhap"],
}
# words that mark an official / primary body (classify() -> "official")
OFFICIAL_RE = re.compile(
    r"وزار|وزير|حكوم|رئاس|رئيس|الرئيس|برلمان|مجلس|شرط|الدفاع المدني|الحماية المدنية|الإطفاء|الاطفاء|إسعاف|اسعاف|"
    r"الصحة|هيئ|سلط|مكتب|ديوان|محكم|قضاء|النيابة|المدعي|البنك المركزي|المصرف المركزي|مصرف|بنك|الإحصاء|الاحصاء|"
    r"الأمم المتحدة|الامم المتحدة|منظمة|اليونيسف|يونيسف|مفوضي|برنامج الأغذية|الصليب الأحمر|الهلال الأحمر|الاتحاد الأوروبي|"
    r"المفوضية|حلف|الناتو|البيت الأبيض|الكرملين|الخارجية|الداخلية|الجيش|القوات|الشرطة|بلدية|محافظ|حاكم|ولاية|"
    r"ناسا|وكالة الفضاء|إيسا|معهد|مرصد|المرصد|هيئة الأرصاد|الأرصاد|المسح الجيولوجي|مركز|جامعة|شركة|مجموعة|"
    r"NASA|ESA|WHO|UN|IMF|NOAA|USGS|صندوق النقد|البنك الدولي|منظمة الصحة|منظمة التجارة|أوبك|اوبك|سفارة|متحدث|المتحدث")
GENERIC_ATTR = {"تقارير", "تقارير إعلامية", "وسائل إعلام", "وسائل الإعلام", "مصادر", "مصادر رسمية", "مسؤولون", "مسؤولين",
                "شهود", "شهود عيان", "سكان", "السلطات", "وكالات", "وكالات الأنباء", "بيانات رسمية", "بيان رسمي"}

_HARAKAT = re.compile(r"[\u064B-\u0652\u0640\u0670]")
_AR = "\u0621-\u063A\u0641-\u064A\u066E-\u06D3"
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _n(t):
    """Normalise for matching (positions are not preserved: used for detection only)."""
    t = _HARAKAT.sub("", t or "").translate(_DIG)
    t = re.sub("[إأآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه")
    t = t.replace("\u200f", "").replace("\u200e", "").replace("\u00a0", " ").replace("ـ", "")
    return re.sub(r"[ \t]+", " ", t)


def _ar_pattern(name):
    """Name with every Arabic prefixed form: و/ف + ب/ل/ك + ال, or لل (للجزيرة), e.g. وبسكاي, لبي بي سي, والبي بي سي."""
    n = re.escape(_n(name)).replace(r"\ ", r"\s*")
    if n.startswith("ال"):
        core = n[2:]
        pre = r"(?:[وف]?(?:[بك]?ال|لل))"
        body = pre + core
    else:
        body = r"(?:[وف]?(?:[بلك]?ال|لل|[بلك])?)" + n
    return rf"(?<![{_AR}\w]){body}(?![{_AR}\w])"


_STRICT = re.compile("|".join(_ar_pattern(x) for x in sorted(OUTLETS_AR, key=len, reverse=True)))
_LATIN = re.compile(r"(?<![A-Za-z])(?:" + "|".join(OUTLETS_LATIN) + r")(?![A-Za-z])", re.I)
_CAPS = re.compile(r"(?<![A-Za-z])(?:" + "|".join(OUTLETS_CAPS) + r")(?![A-Za-z])")
_AMBIG_CTX = re.compile(rf"(?<![{_AR}\w]){ATTR_CTX}\s*(?:ل|ال|لل)?\s*(?:" +
                        "|".join(re.escape(_n(x)[2:] if x.startswith("ال") else _n(x)) for x in OUTLETS_AMBIG) +
                        rf")(?![{_AR}\w])")
_AMBIG_BARE = {_n(x) for x in OUTLETS_AMBIG}


def outlet_hits(text):
    """Every competing-outlet mention in public text (the hard gate). [] = clean."""
    t = _n(text)
    hits = [m.group(0).strip() for m in _STRICT.finditer(t)]
    hits += [m.group(0).strip() for m in _LATIN.finditer(t)]
    hits += [m.group(0).strip() for m in _CAPS.finditer(t)]
    for m in _AMBIG_CTX.finditer(t):
        hits.append(m.group(0).strip())
    # a «المصدر:/المصادر:» line or any source list: every item is checked as a bare name
    for ln in t.splitlines():
        s = ln.strip()
        if re.match(r"^(?:المصدر|المصادر)\s*[:：]", s):
            for item in re.split(r"[،,·|/]| و(?=\S)", s.split(":", 1)[1] if ":" in s else s):
                if is_outlet(item):
                    hits.append(item.strip())
    return list(dict.fromkeys(h for h in hits if h))


def is_outlet(name):
    """True if `name` (a source label) is, or contains, a competing outlet."""
    s = _n(name).strip(" .،-")
    if not s:
        return False
    if s in _AMBIG_BARE or re.sub(r"^(?:قناه|موقع|شبكه|صحيفه)\s+", "", s) in _AMBIG_BARE:
        return True
    return bool(_STRICT.search(s) or _LATIN.search(s) or _CAPS.search(s) or _AMBIG_CTX.search(s))


# ------------------------------------------------------------------ primary-source handling
def _wire_re(alias):
    if re.fullmatch(r"[A-Za-z][A-Za-z.\- ]{1,30}", alias):
        flags = 0 if len(alias) <= 5 else re.I          # short Latin codes (AP, AFP, dpa) are case-sensitive
        return re.compile(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", flags), False
    an = _n(alias)
    core = re.escape(an[2:] if an.startswith("ال") else an).replace(r"\ ", r"\s*")
    return re.compile(rf"(?<![{_AR}\w])(?:[وف]?(?:[بلك]?ال|لل|[بلك])?){core}(?![{_AR}\w])"), True


_WIRE_RES = [(canon, [_wire_re(a) for a in [canon] + aliases]) for canon, aliases in WIRES.items()]


def wires_in(text):
    """Canonical names of every wire agency mentioned in `text`."""
    raw, t = text or "", _n(text)
    return [canon for canon, res in _WIRE_RES if any(r.search(t if ar else raw) for r, ar in res)]


def canonical_wire(name):
    w = wires_in(name)
    return w[0] if w else None


def classify(name):
    """outlet | generic | wire | official | other"""
    s = (name or "").strip()
    if not s:
        return "other"
    if is_outlet(s):
        return "outlet"
    if _n(s) in {_n(g) for g in GENERIC_ATTR}:
        return "generic"
    if canonical_wire(s):
        return "wire"
    if OFFICIAL_RE.search(s) or OFFICIAL_RE.search(_n(s)):
        return "official"
    return "other"


def _stem(w):
    for pre in ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ل", "ف"):
        if w.startswith(pre) and len(w) - len(pre) >= 3:
            w = w[len(pre):]
            break
    for suf in ("يين", "يون", "يه", "يا", "ي", "ه", "ين", "ون", "ات"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _toks(t):
    return [_stem(w) for w in re.sub(r"[^\w\s]", " ", _n(t)).lower().split() if len(w) > 1]


STOP_TOKENS = {_stem(w) for w in ("في", "من", "على", "عن", "إلى", "الى", "و", "يوم", "اليوم", "أمس", "امس", "مساء", "صباح")}


def supported(name, sources_text):
    """Does the source text support naming `name` as a source? Wire: any alias of that agency is in the sources.
    Official/other: >=75% of its tokens (clitics + adjective endings stripped) occur in the sources, and at least
    one distinctive token. Generic («وكالات», «تقارير») needs no support. Outlets are never supported."""
    c = classify(name)
    if c == "outlet":
        return False
    if c == "generic" or _n(name).strip() in (_n(GENERIC), _n(OURS)):
        return True
    if c == "wire":
        return canonical_wire(name) in wires_in(sources_text)
    toks = [w for w in _toks(name) if w not in STOP_TOKENS]
    if not toks:
        return False
    src = set(_toks(sources_text))
    hit = sum(w in src for w in toks)
    return hit / len(toks) >= 0.75 and hit >= 1


def rank(names, sources_text, limit=3):
    """Validated, deduplicated, ranked primary sources: official > wire. Outlets, unsupported and generic names drop out."""
    seen, off, wire = set(), [], []
    for raw in names or []:
        nm = re.sub(r"\s+", " ", str(raw or "")).strip(" .،:-«»\"")
        if not nm or len(nm) > 60:
            continue
        c = classify(nm)
        if c in ("outlet", "generic") or not supported(nm, sources_text):
            continue
        if c == "wire":
            nm = canonical_wire(nm)
        key = _n(nm)
        if key in seen:
            continue
        seen.add(key)
        (wire if c == "wire" else off).append(nm)     # official/primary (incl. named people, companies) first
    return (off + wire)[:limit]


def source_line(names, sources_text, limit=3):
    r = rank(names, sources_text, limit)
    return "المصدر: " + ("، ".join(r) if r else GENERIC), r


SOURCE_LINE_RE = re.compile(r"^\s*(?:المصدر|المصادر)\s*[:：]")


def strip_source_lines(text):
    return "\n".join(ln for ln in (text or "").splitlines() if not SOURCE_LINE_RE.match(ln)).rstrip()


def set_source_line(text, line):
    """Remove any source line the writer produced and end the text with our own «المصدر: …» line."""
    return strip_source_lines(text) + "\n" + line


# In-text: "بحسب <outlet>" -> "بحسب تقارير إعلامية" (accurate, names nobody). Anything else naming an outlet stays a
# gate failure (the story is dropped or repaired).
_PREP_OUTLET = None


def sanitize(text):
    """Replace prepositional outlet attributions ("وفق قناة الجزيرة", "بحسب BBC") with a neutral wording."""
    global _PREP_OUTLET
    if _PREP_OUTLET is None:
        names = sorted(OUTLETS_AR + OUTLETS_AMBIG, key=len, reverse=True)
        ar = "|".join(re.escape(x).replace(r"\ ", r"\s*") for x in names)
        lat = "|".join(OUTLETS_LATIN + OUTLETS_CAPS)
        _PREP_OUTLET = re.compile(
            rf"(?<![{_AR}])([وف]?)(?:وفق(?:ا|اً)?\s*ل?|وفقاً\s+ل|بحسب|حسب|نقل(?:ا|اً)\s+عن)\s*(?:ما\s+(?:ذكرت|أوردت|نقلت|أفادت)\s+)?"
            rf"(?:(?:قناة|موقع|شبكة|صحيفة|تلفزيون)\s+)?(?:ل|ال|لل)?(?:{ar}|(?i:{lat}))(?:\s+(?:نت|عربي|عربية|الإخبارية))?(?![{_AR}\w])")
    return _PREP_OUTLET.sub(lambda m: m.group(1) + "بحسب تقارير إعلامية", text or "")


# ------------------------------------------------------------------ in-text attribution checks
ATTR_VERB = (r"(?:وفق(?:ا|اً)?\s*ل?|وفقاً\s+ل|بحسب|حسب|نقل(?:ا|اً)\s+عن|قالت|قال|أعلنت|أعلن|اعلنت|اعلن|أكدت|أكد|ذكرت|ذكر|"
             r"أفادت|أفاد|أوضحت|أوضح|صرحت|صرح|أشارت|أشار|كشفت|كشف|أصدرت|أصدر|اتهمت|اتهم|نفت|نفى)")
ATTR_RE = re.compile(rf"(?<![{_AR}])(?:و|ف)?{ATTR_VERB}\s+([^\n،,.:؛!؟«»\"()]+)")
_ENTITY_STOP = {"إن", "ان", "أن", "إنه", "انه", "أنه", "إنها", "انها", "أنها", "إنهم", "أنهم", "ما", "في", "على", "عن", "من",
                "إلى", "الى", "لـ", "يوم", "اليوم", "أمس", "امس", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت",
                "الأحد", "مساء", "صباح", "بأن", "بان", "لاحقا", "لاحقاً", "مجددا", "مجدداً", "فيه", "فيها", "عليه", "بيان", "بياناً",
                "بيانا", "بدورها", "بدوره", "رسميا", "رسمياً", "كذلك", "أيضا", "أيضاً", "عبر", "خلال", "بعد", "قبل", "مع", "التقارير",
                "الأولية", "تقديرات", "لتقديرات", "أحدث", "آخر", "اخر", "حصيلة"}


def attributions(text):
    """Entities the text attributes facts to ("قالت وزارة الصحة …" -> "وزارة الصحة"). Pronoun subjects are skipped."""
    out = []
    for m in ATTR_RE.finditer(_HARAKAT.sub("", text or "")):
        words = []
        for w in m.group(1).split():
            if w in _ENTITY_STOP or re.match(r"^\d", w):
                break
            words.append(w)
            if len(words) == 5:
                break
        ent = " ".join(words).strip()
        if ent and len(ent) >= 3:
            out.append(ent)
    return out


def unsupported_attributions(text, sources_text):
    """In-text attributions not backed by the sources (don't invent "قالت وزارة …") or naming an outlet."""
    bad = []
    for ent in attributions(text):
        if classify(ent) == "outlet":
            bad.append((ent, "names a competing outlet"))
        elif classify(ent) == "generic":
            continue
        elif not supported(ent, sources_text):
            bad.append((ent, "attribution not found in the sources"))
    return bad


NEEDS_ATTR_RE = re.compile(r"\d|قتل|قتيل|قتلى|مقتل|جرحى|جريح|مصاب|إصابة|إصابات|وفاة|وفيات|ضحايا|مفقود|اتهم|اتهام|يتهم|"
                           r"تتهم|زعم|مزاعم|يُزعم|ينفي|نفت|نفى|مسؤولية|انتهاك|انتهاكات|جرائم")


def lacks_attribution(body):
    """Numbers, casualty counts, accusations and contested claims need an in-text attribution ("وفق …", "قالت …")."""
    b = strip_source_lines(body)
    b = "\n".join(ln for ln in b.splitlines() if not ln.strip().startswith(("#", "صورة", "صور ولقطات", "خريطة")))
    if not NEEDS_ATTR_RE.search(re.sub(r"(?:بالأرقام|العالم في 30 ثانية|فهم الخبر)\s*\|", "", b)):
        return False
    return not ATTR_RE.search(_HARAKAT.sub("", b))


def self_test():
    for bad in ["وفق الجزيرة", "قالت قناة العربية", "بحسب سكاي نيوز عربية", "نقلاً عن BBC", "لبي بي سي", "والبي بي سي",
                "للجزيرة نت", "France 24", "فرانس 24", "CNN", "يورونيوز", "RT", "CGTN", "الشرق للأخبار", "TRT", "DW",
                "إندبندنت عربية", "بسكاي نيوز", "المصدر: الجزيرة، رويترز", "Al Jazeera", "روسيا اليوم", "دويتشه فيله"]:
        assert outlet_hits(bad), bad
    for ok in ["شبه الجزيرة الكورية", "اللغة العربية", "الدول العربية", "المنطقة الحرة", "الشرق الأوسط", "الحدث الأبرز",
               "وفق وكالة الصحافة الفرنسية", "قالت وزارة الصحة", "فرانس برس", "رويترز", "AFP", "سكان الجزيرة",
               "الجزيرة العربية", "المملكة العربية السعودية", "موقع الجزيرة البركانية", "بعيداً عن الجزيرة", "Reuters", "AP", "Xinhua", "تشارك حرة", "مارتن"]:
        assert not outlet_hits(ok), (ok, outlet_hits(ok))
    return True
