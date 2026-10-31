"""Arabic spelling sanity check for on-screen lines (2026-10-31).

Why: outlet headlines carry typos («احتجاجت», «جديدية» in the 2026-10-31 France story) and the writer copies source words, so
a typo in a source can reach the screen. Every reel / bulletin line now goes through one cheap proofreading pass:

1. code floor: KNOWN typos seen in the wild (no LLM) are always caught;
2. LLM proofreader (router task "utility": Gemma first, high free quota) lists OBVIOUS misspellings only, never style,
   grammar, punctuation, numbers or transliterated names;
3. guards in code: a reported word must really be in the lines (whole word), the correction must be Arabic letters only,
   no digits, and within 2 letter edits of the word (a typo fix, not a rewrite). A word the sources spell the same way at
   least twice while never using the "correction" is treated as a name / accepted variant and left alone.
Safe fixes are applied in place (same facts, same figures). A misspelling without a safe fix is unresolved: the caller
fails closed (the story is rewritten or dropped), never published with it. A proofreader error (bad JSON) is also
unresolved (fail closed); R.Hold (no model with quota) propagates like every other LLM step.
"""
import json
import re

from . import llm as R

_AL = "\u0621-\u064A"
_DIAC = re.compile(r"[\u064B-\u0652\u0640\u0670]")

# typos actually seen in outlet headlines (regression floor, no LLM needed)
KNOWN = {
    "احتجاجت": "احتجاجات",
    "جديدية": "جديدة",
}

PROMPT = """أنت مدقق إملائي في وكالة أنباء عربية. أمامك أسطر ستظهر على الشاشة.
اذكر فقط الأخطاء الإملائية الواضحة في كلمات عربية: حرف ناقص أو زائد أو مكرر أو مقلوب، أو تاء مربوطة/هاء، أو ألف مقصورة/ياء،
أو همزة خاطئة بوضوح (أمثلة: «احتجاجت» ← «احتجاجات»، «جديدية» ← «جديدة»، «مسؤلون» ← «مسؤولون»).
لا تذكر: الأسلوب، النحو، علامات الترقيم، الأرقام، أسماء الأعلام الأجنبية المعرّبة (تُكتب بأكثر من صيغة)، الكلمات الصحيحة.
"wrong" = الكلمة كما وردت حرفياً في السطر، "right" = الكلمة نفسها بعد التصحيح فقط (كلمة واحدة).
أعد JSON فقط: {"typos": [{"wrong": "...", "right": "..."}]} وإن لم توجد أخطاء: {"typos": []}

الأسطر:
"""
R.register(PROMPT, {"typos": list})


def _plain(s):
    return _DIAC.sub("", (s or "").replace("[[", "").replace("]]", "")).strip()


def _word_re(w):
    return re.compile(r"(?<![" + _AL + r"])" + re.escape(w) + r"(?![" + _AL + r"])")


def edit_distance(a, b):
    a, b = _plain(a), _plain(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _count(word, text):
    return len(_word_re(word).findall(_plain(text)))


def known_typos(text):
    t = _plain(text)
    return {w: r for w, r in KNOWN.items() if _word_re(w).search(t)}


def judge(typos, text, src_text=""):
    """LLM typo list -> (fixes {wrong: right}, unresolved [str]) after the code guards."""
    fixes, unresolved = {}, []
    t = _plain(text)
    for x in typos or []:
        if not isinstance(x, dict):
            continue
        w, r = _plain(str(x.get("wrong") or "")), _plain(str(x.get("right") or ""))
        if not w or w == r or not _word_re(w).search(t):
            continue                                            # not in our lines (hallucinated) or no change
        if src_text and _count(w, src_text) >= 2 and not _count(r, src_text):
            continue                                            # the sources consistently spell it so: name / variant
        ok = bool(r) and re.fullmatch(r"[" + _AL + r"]+", r) and not re.search(r"\d", w) and \
            len(r) >= 2 and edit_distance(w, r) <= 2
        if ok:
            fixes[w] = r
        else:
            unresolved.append(f"«{w}» → «{r}»")
    return fixes, unresolved


def proofread(llm, lines, src_text="", log=print):
    """-> (fixes {wrong: right}, unresolved [str]). `lines`: on-screen strings. Raises R.Hold (no model with quota)."""
    lines = [_plain(l) for l in lines if isinstance(l, str) and _plain(l)]
    if not lines:
        return {}, []
    text = "\n".join(lines)
    fixes = dict(known_typos(text))
    try:
        raw = llm(PROMPT + "\n".join(f"- {l}" for l in dict.fromkeys(lines)), temperature=0.0)
        d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        f2, unresolved = judge(d.get("typos"), text, src_text)
    except R.Hold:
        raise
    except Exception as ex:                                     # fail closed: an unchecked line is not published
        return fixes, [f"proofreader error: {str(ex)[:120]}"]
    fixes.update(f2)
    if fixes or unresolved:
        log(json.dumps({"spelling": {"fixed": fixes, "unresolved": unresolved}}, ensure_ascii=False))
    return fixes, unresolved


def apply(obj, fixes, skip_keys=("source_texts", "source_urls", "media", "media_rejected", "footage_queries", "place_terms_en")):
    """Replace each misspelled whole word in every string of obj (dict / list / str). Returns the new object."""
    if not fixes:
        return obj
    if isinstance(obj, str):
        for w, r in fixes.items():
            obj = _word_re(w).sub(r, _DIAC.sub("", obj) if _word_re(w).search(_DIAC.sub("", obj)) else obj)
        return obj
    if isinstance(obj, (list, tuple)):
        return type(obj)(apply(x, fixes, skip_keys) for x in obj)
    if isinstance(obj, dict):
        return {k: (v if k in skip_keys else apply(v, fixes, skip_keys)) for k, v in obj.items()}
    return obj


def story_lines(S):
    """Every on-screen / caption line of a reel story dict."""
    out = [S.get(k) for k in ("topic_ar", "headline", "headline_breaking", "deck", "visual_caption", "where_line")]
    lt = S.get("lower_third") or {}
    out += [lt.get("name_ar"), lt.get("role_ar")]
    out += [f.get("text") for f in S.get("facts") or []] + list((S.get("next") or {}).get("lines") or [])
    out += [b.get("text") for b in S.get("breaking_lines") or []] + list(S.get("ticker") or [])
    bn = S.get("big_number") or {}
    out += [bn.get("prefix_ar"), bn.get("label_ar"), bn.get("sub_ar")]
    loc = S.get("location") or {}
    out += [loc.get("region_ar"), (loc.get("point") or {}).get("label_ar")]
    return [x for x in out if isinstance(x, str) and x.strip()]
