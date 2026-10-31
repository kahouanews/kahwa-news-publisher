"""Number extraction + normalisation for the "every on-screen number must be in the sources" gates (2026-10-31).

Root cause it fixes (reel runs 41197869535 / 41198098858 / 41199797479): the source said «تعبئة 70 ألف رجل أمن», the writer
showed the counter as 70000 / «70,000», and the old check was a raw digit SUBSTRING test ("70000" in source text) -> the
same figure was rejected three times in a row and the whole reel job failed.

Both sides are now reduced to canonical VALUES:
  * Arabic-Indic (٠-٩) and Persian (۰-۹) digits, Arabic decimal «٫» and thousands «٬» separators;
  * thousands separators: 70,000 / 70.000 / 70 000 (also NBSP / thin space) / 70٬000 (a lone «70.5» stays a decimal);
  * magnitude words after a number: ألف / آلاف / ألفاً / الف, مليون / ملايين / مليوناً, مليار / مليارات, بليون, تريليون,
    thousand / million / billion / trillion, k / m / bn  («70 ألف» = 70000, «1.5 مليون» = 1500000);
  * ranges «10-15» / «10 إلى 15» are two numbers; «30%», «30 في المئة», «30 بالمئة» all give 30;
  * SOURCE side only: numbers spelled out in words («سبعين ألف», «ثلاثة آلاف», «مليون», «نصف مليون», «أربع طائرات»),
    so a figure the source states in words still counts. The claim side is digits only, so this can only accept a figure
    the source really states.
A source mention like «70 ألف» supports both 70000 and 70 (the bare digits), so a counter «70» + label «ألف رجل أمن» passes.
"""
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache

_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIAC = re.compile(r"[\u064B-\u0652\u0640\u0670]")
_SPACES = "\u00a0\u202f\u2009\u2007 "
_AL = "\u0621-\u064A"                       # Arabic letters (word boundary)

MAG = [  # (regex of the magnitude word, multiplier); Arabic forms incl. tanween-stripped endings, duals handled separately
    (r"(?:تريليون|ترليون)(?:ات|ا)?", 10 ** 12),
    (r"(?:مليار|بليون)(?:ات|ا)?", 10 ** 9),
    (r"(?:مليون|ملايين)(?:ا)?", 10 ** 6),
    (r"(?:ألف|الف|آلاف|الاف|ألوف|الوف)(?:ا)?", 10 ** 3),
]
MAG_EN = [(r"trillion", 10 ** 12), (r"billion|bn", 10 ** 9), (r"million|mn", 10 ** 6), (r"thousand|k", 10 ** 3)]   # no bare m / tn (metres, tonnes)
_MAG_AR_RE = "|".join(f"(?:{p})" for p, _ in MAG)
_MAG_EN_RE = "|".join(f"(?:{p})" for p, _ in MAG_EN)

# a number token: grouped thousands (comma / dot / space / Arabic separators) or a plain number with an optional decimal part
_GROUP_SEP = r"[,\u066C.'" + _SPACES + r"]"
_NUM = (r"(?<![\d.,])(?:\d{1,3}(?:(?P<sep>" + _GROUP_SEP + r")\d{3})(?:(?P=sep)\d{3})*(?![\d])"
        r"|\d+(?:[.\u066B]\d+|,\d{1,2}(?!\d))?)")         # «2,5%» (decimal comma) is 2.5
_TOKEN = re.compile(r"(?P<num>" + _NUM + r")"
                    r"(?:\s?(?P<mag_ar>(?:" + _MAG_AR_RE + r"))(?![" + _AL + r"])"
                    r"|\s?(?P<mag_en>(?:" + _MAG_EN_RE + r"))(?![A-Za-z]))?", re.I)


def norm(text):
    """Digits -> ASCII, Arabic separators -> ASCII, tashkeel/tatweel dropped, Arabic percent sign -> %."""
    t = (text or "").translate(_DIG)
    t = _DIAC.sub("", t)
    return t.replace("\u066A", "%").replace("\u066B", ".").replace("\u066C", ",")


def canon(x):
    """Decimal -> canonical string ('70000', '1.5')."""
    try:
        d = Decimal(x)
    except (InvalidOperation, TypeError, ValueError):
        return str(x)
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def _mult(mag_ar, mag_en):
    if mag_ar:
        for p, m in MAG:
            if re.fullmatch(p, mag_ar):
                return m
    if mag_en:
        for p, m in MAG_EN:
            if re.fullmatch(p, mag_en, re.I):
                return m
    return 1


def _readings(raw, sep):
    """Possible plain values of a digit token (before the magnitude word)."""
    out = []
    if sep:
        grouped = re.sub(r"[^\d]", "", raw)
        out.append(Decimal(grouped))
        if sep == "." and raw.count(sep) == 1:            # «70.000» could also be a decimal 70.000 (= 70)
            try:
                out.append(Decimal(raw))
            except InvalidOperation:
                pass
    else:
        out.append(Decimal(raw.replace(",", ".")))
    return out


def mentions(text):
    """[(raw text, {canonical values})] for every number written with digits in `text` (claim side)."""
    t = norm(text)
    out = []
    for m in _TOKEN.finditer(t):
        raw = m.group("num")
        try:
            base = _readings(raw, m.group("sep"))
        except InvalidOperation:
            continue
        mul = _mult(m.group("mag_ar"), m.group("mag_en"))
        vals = {canon(b * mul) for b in base}
        out.append((m.group(0).strip(), vals))
    return out


# --------------------------------------------------------------------------------------- spelled-out numbers (source side)
_UNITS = {
    1: ["واحد", "واحدة"], 2: ["اثنان", "اثنين", "اثنتان", "اثنتين", "شخصان", "شخصين"], 3: ["ثلاث", "ثلاثة"], 4: ["أربع", "اربع", "أربعة", "اربعة"],
    5: ["خمس", "خمسة"], 6: ["ست", "ستة"], 7: ["سبع", "سبعة"], 8: ["ثماني", "ثمانية", "ثمان"], 9: ["تسع", "تسعة"],
    10: ["عشر", "عشرة"],
}
_TENS = {20: ["عشرون", "عشرين"], 30: ["ثلاثون", "ثلاثين"], 40: ["أربعون", "أربعين", "اربعون", "اربعين"],
         50: ["خمسون", "خمسين"], 60: ["ستون", "ستين"], 70: ["سبعون", "سبعين"], 80: ["ثمانون", "ثمانين"],
         90: ["تسعون", "تسعين"]}
_HUNDREDS = {100: ["مئة", "مائة", "مئات"], 200: ["مئتان", "مئتين", "مائتان", "مائتين"], 300: ["ثلاثمئة", "ثلاثمائة"],
             400: ["أربعمئة", "أربعمائة", "اربعمئة", "اربعمائة"], 500: ["خمسمئة", "خمسمائة"], 600: ["ستمئة", "ستمائة"],
             700: ["سبعمئة", "سبعمائة"], 800: ["ثمانمئة", "ثمانمائة", "ثمانيمئة"], 900: ["تسعمئة", "تسعمائة"]}
_TEENS = {11: ["أحد عشر", "إحدى عشرة", "احد عشر", "احدى عشرة"], 12: ["اثنا عشر", "اثني عشر", "اثنتا عشرة", "اثنتي عشرة"]}
for _n, _u in ((13, "ثلاثة"), (14, "أربعة"), (15, "خمسة"), (16, "ستة"), (17, "سبعة"), (18, "ثمانية"), (19, "تسعة")):
    _TEENS[_n] = [f"{_u} عشر", f"{_u[:-1]} عشرة"]
_FRACTIONS = {"نصف": Decimal("0.5"), "ربع": Decimal("0.25"), "ثلث": Decimal(1) / 3}
_DUAL_MAG = {"ألفين": 2000, "ألفان": 2000, "الفين": 2000, "الفان": 2000, "مليونين": 2 * 10 ** 6, "مليونان": 2 * 10 ** 6,
             "مليارين": 2 * 10 ** 9, "ملياران": 2 * 10 ** 9}

_PRE = r"(?<![" + _AL + r"])(?:[وفبل]|ال|وال|بال|لل)?"
_POST = r"(?:ة|ا)?(?![" + _AL + r"])"


def _alt(words):
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_WORD_VALUES = []           # (compiled regex, value)
for _d, _ws in list(_TEENS.items()):
    _WORD_VALUES.append((re.compile(_PRE + "(?:" + _alt(_ws) + ")" + _POST), _d))
for table in (_HUNDREDS, _TENS, _UNITS):
    for _d, _ws in table.items():
        _WORD_VALUES.append((re.compile(_PRE + "(?:" + _alt(_ws) + ")" + _POST), _d))
_MAG_WORD = re.compile(r"\s+(?P<mag>" + _MAG_AR_RE + r")(?![" + _AL + r"])")
_BARE_MAG = re.compile(_PRE + r"(?P<mag>" + _MAG_AR_RE + r"|" + _alt(_DUAL_MAG) + r")(?![" + _AL + r"])")
_FRAC_MAG = re.compile(_PRE + r"(?P<f>" + _alt(_FRACTIONS) + r")\s+(?P<mag>" + _MAG_AR_RE + r")(?![" + _AL + r"])")
# «سبعين ألف», «ثلاثة آلاف», «خمسة وعشرين ألفاً»: unit (+ و + tens) followed by a magnitude word
_COMPOUND = re.compile(_PRE + r"(?P<u>" + _alt([w for ws in _UNITS.values() for w in ws]) + r")" + _POST +
                       r"\s+و(?P<t>" + _alt([w for ws in _TENS.values() for w in ws]) + r")(?![" + _AL + r"])")


def _word_value(word):
    for rx, v in _WORD_VALUES:
        if rx.fullmatch(word) or rx.match(word):
            return v
    return None


def spelled_values(text):
    """Values stated in words (source side only)."""
    t = norm(text)
    out = set()
    teen_spans = []
    for rx, v in _WORD_VALUES:
        for m in rx.finditer(t):
            if any(a <= m.start() < b for a, b in teen_spans):
                continue
            if v in _TEENS:
                teen_spans.append((m.start(), m.end()))
            out.add(v)
            mm = _MAG_WORD.match(t, m.end())
            if mm:
                out.add(v * _mult(mm.group("mag"), None))
    # «13-19» spelled out is not also 3 or 10 (only the source side, so extra values are harmless but keep it tight)
    for m in _COMPOUND.finditer(t):
        u, tt = _word_value(m.group("u")), _word_value(m.group("t"))
        if u and tt:
            v = u + tt
            out.add(v)
            mm = _MAG_WORD.match(t, m.end())
            if mm:
                out.add(v * _mult(mm.group("mag"), None))
    for m in _FRAC_MAG.finditer(t):
        out.add(canon(_FRACTIONS[m.group("f")] * _mult(m.group("mag"), None)))
    for m in _BARE_MAG.finditer(t):
        g = m.group("mag")
        out.add(_DUAL_MAG.get(g) or _mult(g, None))
    return {canon(v) for v in out}


@lru_cache(maxsize=64)
def source_values(text):
    """Every value the source text states: digit mentions (all readings + the bare digits before a magnitude word, and each
    part of a range) plus spelled-out numbers."""
    t = norm(text)
    out = set()
    for raw, vals in mentions(t):
        out |= vals
        m = re.match(_NUM, norm(raw))
        if m:
            try:
                out |= {canon(b) for b in _readings(m.group(0), m.group("sep"))}
            except InvalidOperation:
                pass
    # every bare digit run too (keeps the old substring-era behaviour for things like «2026/10/31» or «A320»)
    out |= {canon(d) for d in re.findall(r"\d+", t)}
    out |= spelled_values(t)
    return frozenset(out)


def unsupported(text, src):
    """Raw number mentions in `text` that the source does not state (any reading of the mention may match)."""
    sv = source_values(src or "")
    bad = []
    for raw, vals in mentions(text):
        if not (vals & sv):
            bad.append(raw)
    return bad


def values(text):
    """Set of canonical values written with digits in `text`, primary reading only (70.000 = 70000): used to compare two
    versions of the same line (a shortened line may change a figure's format, never its value)."""
    out = set()
    t = norm(text)
    for m in _TOKEN.finditer(t):
        try:
            base = _readings(m.group("num"), m.group("sep"))[0]
        except InvalidOperation:
            continue
        out.add(canon(base * _mult(m.group("mag_ar"), m.group("mag_en"))))
    return out
