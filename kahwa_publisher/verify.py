"""Second verification layer: claim decomposition + per-claim evidence (Loki / OpenFactVerification style, Gemini).

1. decompose : the generated text -> atomic factual claims (one checkable fact each), with a check-worthiness flag.
2. verify    : every claim is judged ONLY against the numbered source texts; a "supported" verdict must quote the
               evidence verbatim from the cited source. The quote is then checked in code (normalised token overlap);
               a quote that is not really in that source turns the claim into "unsupported" (guards a lenient verifier).
               Any figure in a claim must also appear in the sources (code check).
3. repair    : unsupported / contradicted claims are dropped or rewritten from supported facts only, then the WHOLE
               text is decomposed + verified again (one repair round).
Attribution (2026-10-29): every in-text attribution ("قالت وزارة …", "وفق وكالة …") must itself be backed by the source
text (code check, attribution.unsupported_attributions); a competing outlet named anywhere counts as unsupported; with
require_attribution, numbers / casualty counts / accusations without any in-text attribution are flagged for repair.
Fail closed: any LLM / parse error, an empty claim list, or a claim still unsupported after the repair -> None
(the caller skips the story; nothing is queued).

verify_text(llm, text, sources, kind) -> {"text": final_text, "report": {...}} or None
"""
import json
import re

from . import attribution as A
from . import llm as R

DECOMPOSE = """أنت محلل تحقق في وكالة أنباء. فكّك النص التالي إلى ادعاءات واقعية ذرية: كل ادعاء جملة عربية قصيرة مستقلة فيها واقعة واحدة قابلة للتحقق (من/ماذا/أين/متى/كم/من قال ماذا).
- غطِّ كل معلومة في النص: الأرقام، الأسماء، الأماكن، التواريخ، الصفات، التصريحات المنسوبة، النتائج والأسباب.
- إذا نُسبت واقعة إلى جهة ("وفق ..."، "قالت ..."، "أعلنت ...") فاجعل الجهة جزءاً من الادعاء نفسه (مثال: "وزارة الصحة قالت إن 12 شخصاً توفوا").
- النسبة العامة ("وفق وكالات"، "بحسب تقارير"، "بحسب تقارير إعلامية") ليست ادعاءً: احذفها من الادعاء واحتفظ بالواقعة فقط.
- لا تُدرج: سطر "المصدر:" أو "المصادر:"، الوسوم (#)، اسم السلسلة قبل "|"، أسطر حقوق الصور.
- check_worthy = true إذا كان الادعاء رقماً أو اسماً أو تصريحاً أو واقعة يمكن أن تكون خاطئة؛ false فقط للعبارات العامة جداً.
أعد JSON فقط: {"claims": [{"id": 1, "claim": "...", "span": "الجزء من النص الذي جاء منه الادعاء حرفياً", "check_worthy": true}]}

النص:
"""

VERIFY = """أنت مدقق حقائق صارم. لديك مصادر مرقمة وقائمة ادعاءات. احكم على كل ادعاء اعتماداً على نصوص المصادر فقط (لا على معرفتك).
- supported: المصادر تقول ذلك صراحة (يُقبل اختلاف الصياغة لا اختلاف المعنى). يجب أن تنسخ في evidence عبارة من المصدر المذكور حرفياً كما هي (4 إلى 25 كلمة).
- contradicted: المصادر تقول عكس ذلك أو رقماً/اسماً مختلفاً.
- unsupported: غير موجود في المصادر، أو مبالغ فيه، أو أكثر تحديداً مما تقوله المصادر، أو استنتاج غير مذكور.
- النسبة جزء من الادعاء: إذا قال الادعاء إن جهة محددة (وزارة، شرطة، شركة، وكالة أنباء...) قالت شيئاً، فهو supported فقط إذا كانت المصادر تنسبه إلى الجهة نفسها.
  أما النسبة العامة ("وفق وكالات"، "بحسب تقارير") فتجاهلها واحكم على الواقعة وحدها.
أعد JSON فقط: {"results": [{"id": 1, "verdict": "supported|contradicted|unsupported", "source": رقم المصدر, "evidence": "اقتباس حرفي من ذلك المصدر", "note": "سبب قصير"}]}
"""

REPAIR = """أنت محرر أول. النص التالي يحتوي ادعاءات غير مدعومة بالمصادر (قائمة أدناه). أعد كتابة النص بحيث:
- تحذف كل ادعاء غير مدعوم أو تعيد صياغته ليطابق ما في المصادر حرفياً من حيث الوقائع فقط.
- لا تضف أي معلومة جديدة غير موجودة في المصادر.
- تحافظ على نفس البنية والأسطر (السطر الأول، التفاصيل، الوسوم، أي سطر يبدأ بـ "المصدر" أو "صورة" يبقى كما هو) وعلى نفس الأسلوب، بلا إيموجي وبلا روابط.
- النسبة: انسب الأرقام والحصائل والاتهامات إلى الجهة الأصلية التي تذكرها المصادر (وزارة، شرطة، وكالة أنباء...) بصيغة "وفق ..." أو "قالت ..."؛
  إن لم تذكر المصادر الجهة فاكتب "بحسب تقارير". لا تنسب شيئاً لجهة لا تذكرها المصادر، ولا تذكر أبداً أي قناة أو موقع إخباري أو صحيفة.
أعد JSON فقط: {"text": "النص المصحح كاملاً"}
"""


R.register(DECOMPOSE, {"claims": list})
R.register(VERIFY, {"results": list})
R.register(REPAIR, {"text": str}, lambda d: R.arabic_quality(d.get("text") or "", min_len=40))


def _json(raw):
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)


def _norm(t):
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t or "")
    t = re.sub("[إأآا]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    t = re.sub(r"[\"'«»“”‘’]", " ", t)
    return re.sub(r"[^\w\s]", " ", t).lower()


def _toks(t):
    return [w for w in _norm(t).split() if len(w) > 1]


def _clitic(w):
    for pre in ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ل", "ف"):
        if w.startswith(pre) and len(w) - len(pre) >= 3:
            return w[len(pre):]
    return w


def quote_in_source(quote, source):
    """True if the evidence quote really is in the source (>=85% of its tokens, clitics stripped, order-free)."""
    q = [_clitic(w) for w in _toks(quote)]
    if len(q) < 2:
        return False
    s = {_clitic(w) for w in _toks(source)}
    return sum(w in s for w in q) / len(q) >= 0.85


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def numbers(t):
    t = (t or "").translate(_AR_DIGITS)
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d.,]*", t)}


# Sources often spell small numbers out («بأربع طائرات», «ثلاثة قتلى», «عشرات»…): a claim that writes the digit must not be
# rejected as "figures not in sources" (reel run 40929624756 failed on «3 مسيرات من أصل 4» vs «بأربع طائرات»).
# Only the SOURCE side gets the word forms; claim numbers are still digits, so this can only accept a figure the source states.
_AR_NUM_WORDS = {
    "1": ["واحد", "واحدة"], "2": ["اثنان", "اثنين", "اثنتان", "اثنتين", "شخصان", "شخصين"],
    "3": ["ثلاث", "ثلاثة"], "4": ["أربع", "اربع", "أربعة", "اربعة"], "5": ["خمس", "خمسة"], "6": ["ست", "ستة"],
    "7": ["سبع", "سبعة"], "8": ["ثماني", "ثمانية", "ثمان"], "9": ["تسع", "تسعة"], "10": ["عشر", "عشرة"],
    "11": ["أحد عشر", "إحدى عشرة"], "12": ["اثنا عشر", "اثني عشر", "اثنتا عشرة"], "20": ["عشرون", "عشرين"],
    "30": ["ثلاثون", "ثلاثين"], "40": ["أربعون", "أربعين"], "50": ["خمسون", "خمسين"], "100": ["مئة", "مائة"],
    "1000": ["ألف"],
}
_AR_NUM_RE = [(d, re.compile(r"(?<![\u0621-\u064A])(?:[وفبل]|ال|وال|بال)?(?:" + "|".join(map(re.escape, ws)) +
                             r")(?:ة|ا)?(?![\u0621-\u064A])")) for d, ws in _AR_NUM_WORDS.items()]


def source_numbers(t):
    """Numbers stated in source text: digits plus small numbers spelled out in Arabic words."""
    out = numbers(t)
    flat = re.sub(r"[\u064B-\u0652\u0640]", "", t or "")
    if re.search(r"(?:أحد|إحدى|اثنا|اثني|اثنتا) عشر", flat):
        out.update(d for d, w in (("11", "أحد عشر"), ("11", "إحدى عشرة"), ("12", "اثنا عشر"), ("12", "اثني عشر"), ("12", "اثنتا عشرة")) if w in flat)
    flat = re.sub(r"[\u0621-\u064A]+ عشرة?(?![\u0621-\u064A])", " ", flat)   # 13-19 («ستة عشر») is not 6 or 10
    for d, rx in _AR_NUM_RE:
        if rx.search(flat):
            out.add(d)
    return out


SERIES_PREFIXES = ("فهم الخبر", "العالم في 30 ثانية", "بالأرقام", "#عاجل", "عاجل")


def _strip_for_claims(text):
    """Lines that are not claims: sources line, archive-photo credit lines, hashtags-only lines. A series label
    ("فهم الخبر | ...") is a programme name, not a claim: only the subject after "|" is checked."""
    keep = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if "|" in s and s.split("|", 1)[0].strip() in SERIES_PREFIXES:
            s = ln = s.split("|", 1)[1].strip()
        if not s or s.startswith(("المصادر", "المصدر:", "المصدر :", "إعداد:", "صورة أرشيفية", "صور ولقطات أرشيفية", "صورة:", "خريطة:")) or \
                re.fullmatch(r"(#\S+\s*)+", s):
            continue
        keep.append(ln)
    return "\n".join(keep)


GENERIC_ATTR_RE = re.compile(r"\s*[(（]\s*(?:وفق|بحسب|حسب)\s+(?:وكالات|تقارير(?:\s+إعلامية)?|وسائل\s+إعلام)\s*[)）]|"
                             r"[،,]?\s*(?:وفق|بحسب|حسب)\s+(?:وكالات|تقارير\s+إعلامية)(?=\s*[.\n،,]|$)")


def check(llm, text, sources, require_attribution=False):
    """One decomposition + verification pass. Returns (claims_with_verdicts, bad_list).
    Attribution checks run in code on top of the LLM verdicts (an invented or outlet attribution is a bad claim)."""
    body = _strip_for_claims(text)
    # generic framing ("(وفق وكالات)", "بحسب تقارير إعلامية") is policy wording, not a claim: hide it from the LLM judge
    claims = _json(llm(DECOMPOSE + GENERIC_ATTR_RE.sub("", body), temperature=0.0)).get("claims") or []
    claims = [c for c in claims if isinstance(c, dict) and (c.get("claim") or "").strip()]
    if not claims:
        raise ValueError("decomposition returned no claims")
    src_block = "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources))
    payload = json.dumps([{"id": c.get("id", i + 1), "claim": c["claim"]} for i, c in enumerate(claims)], ensure_ascii=False)
    res = _json(llm(VERIFY + "\nالمصادر:\n" + src_block + "\n\nالادعاءات:\n" + payload, temperature=0.0)).get("results") or []
    by_id = {r.get("id"): r for r in res if isinstance(r, dict)}
    all_src = " ".join(sources)
    src_nums = source_numbers(all_src)
    bad = []
    for i, c in enumerate(claims):
        cid = c.get("id", i + 1)
        r = by_id.get(cid) or {"verdict": "unsupported", "note": "no verdict returned"}
        v = (r.get("verdict") or "unsupported").lower()
        if v == "supported":
            try:
                si = int(r.get("source")) - 1
                src = sources[si] if 0 <= si < len(sources) else all_src
            except (TypeError, ValueError):
                src = all_src
            if not (quote_in_source(r.get("evidence", ""), src) or quote_in_source(r.get("evidence", ""), all_src)):
                v, r["note"] = "unsupported", "evidence quote not found in the sources"
        missing = [n for n in numbers(c["claim"]) if n not in src_nums]
        if v == "supported" and missing:
            v, r["note"] = "unsupported", f"figures not in sources: {missing}"
        c.update(verdict=v, source=r.get("source"), evidence=(r.get("evidence") or "")[:200], note=(r.get("note") or "")[:160])
        if v != "supported":
            bad.append(c)
    for ent, why in A.unsupported_attributions(body, all_src):
        bad.append({"claim": f"النسبة إلى «{ent}»", "verdict": "unsupported", "note": why, "check_worthy": True})
    for h in A.outlet_hits(text):
        bad.append({"claim": f"ذكر وسيلة إعلام: {h}", "verdict": "unsupported", "note": "names a competing outlet", "check_worthy": True})
    if require_attribution and A.lacks_attribution(body):
        bad.append({"claim": "أرقام أو حصيلة أو اتهامات بلا نسبة إلى جهة", "verdict": "unsupported",
                    "note": "in-text attribution required (وفق …)", "check_worthy": True})
    return claims, bad


def verify_text(llm, text, sources, kind="post", log=print, require_attribution=False):
    sources = [s for s in sources if s and s.strip()]
    report = {"kind": kind, "rounds": []}
    try:
        claims, bad = check(llm, text, sources, require_attribution)
        report["rounds"].append({"claims": len(claims), "unsupported": [{k: b.get(k) for k in ("claim", "verdict", "note")} for b in bad]})
        if bad:
            fix = _json(llm(REPAIR + "\nالمصادر:\n" + "\n".join(sources) + "\n\nالادعاءات غير المدعومة:\n" +
                            "\n".join(f"- {b['claim']} ({b['verdict']}: {b['note']})" for b in bad) +
                            "\n\nالنص:\n" + text, temperature=0.2)).get("text", "").strip()
            if len(fix) < 0.4 * len(text):
                raise ValueError("repair removed too much")
            text = re.sub(r"https?://\S+|www\.\S+", "", fix)
            claims, bad = check(llm, text, sources, require_attribution)
            report["rounds"].append({"claims": len(claims), "unsupported": [{k: b.get(k) for k in ("claim", "verdict", "note")} for b in bad]})
            if bad:
                raise ValueError(f"{len(bad)} claim(s) still unsupported after repair")
        report["result"] = "ok"
        report["checked_claims"] = [{k: c.get(k) for k in ("claim", "check_worthy", "source", "evidence")} for c in claims]
        log(json.dumps({"verify": {k: report[k] for k in ("kind", "rounds", "result")}}, ensure_ascii=False))
        return {"text": text, "report": report}
    except R.Hold:                       # no independent checker / quota: hold (caller keeps the story for the next run)
        report["result"] = "held: no fact-checker available"
        log(json.dumps({"verify": report}, ensure_ascii=False))
        raise
    except Exception as ex:
        report["result"] = f"rejected (fail closed): {type(ex).__name__}: {str(ex)[:200]}"
        log(json.dumps({"verify": report}, ensure_ascii=False))
        return None
