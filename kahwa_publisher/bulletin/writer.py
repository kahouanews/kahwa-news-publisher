"""«قهوة اليوم» writer: the newsroom's verified stories of the day -> rundown spec (segments + «باختصار»).

Same gates as the posts and reels: >= 2 outlets per story, legal/sports/banned-country filters, graphic-source keyword gate,
LLM category classifier (fail closed), outlet names hidden from the writer ([م1] …), every on-screen number must be in the
sources, on-screen source tags = primary sources only (attribution.rank, never a competing outlet), and a claim-level
verification of every on-screen line by a fact-checker of a DIFFERENT model family (verify.check). A story that fails is
dropped (fail closed); fewer than MIN_MAIN verified stories -> no bulletin today. Writing goes through the LLM router
(kahwa_publisher/llm.py: task "writer"; checks: "factcheck" excluding the writer family; "classifier").
"""
import json
import os
import math
import re

from .. import attribution as A
from .. import llm as R
from .. import safety
from .. import spelling
from ..reels.render import BANNED_ISO, banned_hits
from ..reels.story import number_ok, numbers_in, point_in_country, source_block
from ..verify import check as vcheck
from . import rundown as RD

TIER1 = {"BBC عربي", "فرانس 24", "DW عربية", "أخبار الأمم المتحدة"}

SEG_PROMPT = """أنت محرر أول في نشرة أخبار تلفزيونية عالمية اسمها «قهوة اليوم» من "قهوة نيوز". اكتب فقرة مرئية بلا صوت (نصوص على الشاشة)
عن الخبر التالي بالعربية الفصحى المبسطة، اعتماداً فقط على الوقائع الواردة في المصادر أدناه.
قواعد صارمة:
- كل رقم تكتبه موجود حرفياً في المصادر بالأرقام نفسها (لا حساب ولا تقريب ولا تحويل). لا أرقام غير موجودة.
- لا تخترع اسماً أو مكاناً أو تصريحاً أو سبباً أو سياقاً، ولا تضف أي معلومة خلفية غير مكتوبة نصاً في المصادر (مثل وصف مدينة
  أو تاريخها أو أهميتها، أو "تستمر التحقيقات"، أو زيارات وتصريحات لم تُذكر). كل سطر يجب أن يقابله نص صريح في المصادر. حياد تام بلا رأي ولا تهويل ولا صفات انفعالية. انسب كل حصيلة وكل ادعاء إلى قائله.
- أخبار الحروب مسموحة بأسلوب وقائعي هادئ بلا أي تفاصيل دموية (لا دماء ولا أشلاء ولا جثث ولا "مجزرة" ولا "مروع").
- ممنوع منعاً باتاً ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل بأي صيغة، وممنوع الرياضة، وممنوع طلب التفاعل.
- ممنوع ذكر أي قناة أو موقع إخباري أو صحيفة في أي حقل. "sources" = الجهات الأصلية التي تقول المصادر صراحة إنها أعلنت المعلومة
  (جهة رسمية أولاً، ثم وكالة أنباء: رويترز، أسوشيتد برس، الوكالة الفرنسية…) وإلا قائمة فارغة. المصادر مرقمة [م1] [م2] فقط.
- ضع الكلمة الأهم (كلمة إلى ثلاث) بين [[ ]] مرة واحدة على الأكثر في كل سطر.
- كل سطر على الشاشة يجب أن يُفهم وحده: مشاهد يرى السطر منفرداً يعرف مَن وأين وماذا. اذكر الفاعل المسمّى أو المكان/البلد صراحة في
  headline و brief (مثال صحيح: «منفذ هجوم تراب في فرنسا يقر بالتخطيط المسبق»، خطأ: «المشتبه به أقر بالتخطيط المسبق للهجوم»).
  لا تبدأ سطراً بـ«المشتبه به» أو «الحادث» أو «الهجوم» أو «الضحايا» أو «السلطات» دون أن تحدد مباشرة أي هجوم/حادث وأين.
- الأطوال إلزامية (تُعدّ الحروف بدون [[ ]]): title حتى 16 (كلمتان أو ثلاث)، tag حتى 12، headline حتى 52، strap حتى 58، map_line حتى 58،
  number.label حتى 30، number.deck حتى 52، quote.text حتى 52، quote.who حتى 24، quote.role حتى 30، كل سطر في next.rows حتى 50، brief حتى 64 (جملة كاملة في سطرين على الأكثر).
- place: المكان الرئيسي للخبر (مدينة أو منطقة) بإحداثيات دقيقة داخل البلد، أو null إن لم يكن للخبر مكان واحد واضح.
  label اسم المكان بالعربية حتى 20 حرفاً.
- number: أهم رقم في المصادر (num بالأرقام كما ورد) أو null. quote: تصريح منقول حرفياً تقريباً من المصادر مع قائله أو null.
- next.rows: سطران أو ثلاثة عما سيحدث لاحقاً أو ما لا يُعرف بعد، فقط إن ذكرته المصادر، وإلا null.
- urgent=true فقط لخبر عاجل حقيقي في الساعات الأخيرة.
أعد JSON فقط:
{"title": "عنوان الفقرة 2-3 كلمات", "tag": "تصنيف بكلمة", "headline": "عنوان مع [[...]]",
 "strap": "سطر الخبر الأساسي مع [[...]]", "map_line": "سطر يرافق الخريطة: أين ولماذا يهم المكان، أو null",
 "place": {"iso3": "ISO3", "lon": 0.0, "lat": 0.0, "label": "..."},
 "number": {"num": "...", "label": "ماذا يعني الرقم", "deck": "سطر توضيحي مع [[...]]"},
 "quote": {"text": "...", "who": "...", "role": "..."},
 "next": {"title": "ماذا [[بعد؟]]", "rows": ["...", "..."]},
 "brief": "الخبر في جملة كاملة قصيرة تذكر مَن/أين مع [[...]]", "actor": "الفاعل الرئيسي المسمّى في المصادر (شخص أو جهة أو بلد) أو null",
 "sources": ["..."], "urgent": false, "safe": true}
اجعل "safe" = false إذا كان الخبر رياضياً أو لا يُروى دون الدول الممنوعة أو بلا تفاصيل دموية أو كان دعاية أو لا يمكن التحقق منه.

المصادر:
"""

BRIEF_PROMPT = """أنت محرر نشرة أخبار. اكتب الخبر التالي في جملة كاملة على الشاشة حتى 64 حرفاً (سطران على الأكثر) بالعربية الفصحى، مع الكلمة الأهم بين [[ ]]،
اعتماداً فقط على المصادر: كل رقم موجود حرفياً فيها، بلا رأي ولا تفاصيل دموية، ممنوع ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل،
وممنوع ذكر أي قناة أو موقع إخباري. "source" = الجهة الأصلية التي تقول المصادر إنها أعلنت الخبر (رسمية أو وكالة أنباء) أو "".
للعنوان القصير (headline) حتى 52 حرفاً مع [[...]]، و place كما في الخبر أو null. "actor" = الفاعل الرئيسي المسمّى في المصادر أو null.
- كل سطر على الشاشة يجب أن يُفهم وحده: مشاهد يرى السطر منفرداً يعرف مَن وأين وماذا. اذكر الفاعل المسمّى أو المكان/البلد صراحة في
  headline و brief (مثال صحيح: «منفذ هجوم تراب في فرنسا يقر بالتخطيط المسبق»، خطأ: «المشتبه به أقر بالتخطيط المسبق للهجوم»).
  لا تبدأ سطراً بـ«المشتبه به» أو «الحادث» أو «الهجوم» أو «الضحايا» أو «السلطات» دون أن تحدد مباشرة أي هجوم/حادث وأين.
أعد JSON فقط: {"brief": "...", "headline": "...", "source": "...", "actor": "...", "place": {"iso3": "ISO3", "lon": 0.0, "lat": 0.0, "label": "..."}, "urgent": false, "safe": true}

المصادر:
"""

R.register(SEG_PROMPT, {"headline": str, "title": str})
R.register(BRIEF_PROMPT, {"brief": str})

# title: the prompt asks for <= 16; 26 is the hard cap (bumper + rail shrink long titles to one line). Titles are never trimmed:
# a word-boundary cut broke «الولايات المتحدة» (run 40960800162), and 22 dropped a whole story on 23 chars (run 40953097998).
LIM = {"title": 26, "tag": 12, "headline": 52, "strap": 58, "map_line": 58, "label": 30, "deck": 52, "qtext": 52, "who": 24,
       "role": 30, "row": 50, "brief": RD.BRIEF_MAX_CHARS, "place_label": 20}
# brief: 44 dropped complete sentences (owner 2026-10-29); now up to 2 lines, the list shrinks its font for long rows.


def plen(s):
    return len(re.sub(r"\[\[|\]\]", "", s or ""))


_TAIL_PARTICLES = {"في", "من", "على", "إلى", "عن", "مع", "و", "بعد", "قبل", "بين", "عند", "حول", "ضد", "لـ", "بـ", "أو", "ثم"}


def fit_title(t, lim, max_over=8):
    """Short label (bumper title / edition title) -> fitted to `lim` chars at a word boundary, or None.
    Only for small overruns (<= max_over): trailing words are dropped, never a word cut, never a dangling particle;
    the fitted title is still screened and claim-verified with the other lines."""
    t = re.sub(r"\s+", " ", (t or "").strip())
    if plen(t) <= lim:
        return t
    if plen(t) > lim + max_over:
        return None
    words = re.sub(r"\[\[|\]\]", "", t).split(" ")
    while words and (len(" ".join(words)) > lim or words[-1] in _TAIL_PARTICLES):
        words.pop()
    out = " ".join(words).strip(" .،,:-–…")
    return out if len(out.split(" ")) >= 2 and plen(out) <= lim else None


def log(o):
    print(json.dumps(o, ensure_ascii=False)[:1500], flush=True)


# ------------------------------------------------------------------ story selection
def select_clusters(n=9, min_sources=2):
    """Top stories of the day for the bulletin: >= min_sources outlets, newsroom legal/sports/banned filters, graphic-source
    gate, LLM classifier (fail closed). Ranked by the newsroom score (+ early-warning signals, ranking only)."""
    from .. import newsroom as N
    from .. import signals
    clusters = N.cluster(N.fetch())
    if os.getenv("SIGNALS", "on").lower() != "off":
        try:
            signals.apply(clusters, N.tokens, N.blocked, lambda t: N.banned_country(t) or any(b in t for b in N.BLOCK) or
                          sum((k in t) if " " in k else (N.norm(k) in set(N.norm(t).split())) for k in N.SPORTS) >= 1)
        except Exception as ex:
            log({"signal_error": str(ex)[:200]})
    cands = []
    for c in clusters:
        c = N.purge_items(c)                    # religion / banned-country items out of mixed clusters (2026-10-31)
        if not c["items"] or len(c["sources"]) < min_sources or N.blocked(c) or not safety.keyword_ok(c):
            continue
        text = " ".join(i["title"] + " " + i["summary"] for i in c["items"])
        words = set(N.norm(text).split())
        if any((k in text) if " " in k else (N.norm(k) in words) for k in N.SPORTS):
            continue
        cands.append(c)
        if len(cands) >= max(12, n + 3):
            break
    allowed = safety.classify(R.bound("classifier"), cands) if cands else {}
    out = [c for c in cands if allowed.get(c["key"])][:n]
    log({"bulletin_candidates": len(cands), "allowed": len(out), "titles": [c["items"][0]["title"][:70] for c in out]})
    return out


# ------------------------------------------------------------------ validation
def _json(raw):
    obj, err = R.parse_json(raw)
    if obj is None:
        raise ValueError(f"writer JSON: {err}")
    return obj


def screen_problems(lines, src):
    """Hard gates on on-screen lines (code, no LLM): length is checked by the caller; here numbers, banned, outlets, graphic."""
    from ..newsroom import banned_country
    p = []
    for ln in lines:
        t = ln.replace("[[", "").replace("]]", "")
        for n in numbers_in(t):
            if not number_ok(n, src):
                p.append(f"number {n} not in sources: {t[:50]}")
        if banned_hits(t) or banned_country(t) or RD.LATIN_BANNED.search(t):
            p.append(f"banned name: {t[:50]}")
        if A.outlet_hits(t):
            p.append(f"outlet named: {t[:50]}")
        if safety.graphic_hits(t):
            p.append(f"graphic wording: {t[:50]}")
        if t.count("[[") > 1:
            p.append("more than one keyword highlight")
    return p


def valid_place(pl):
    try:
        iso, lon, lat = str(pl["iso3"]).upper(), float(pl["lon"]), float(pl["lat"])
    except Exception:
        return None
    if iso in BANNED_ISO or iso in RD.geo.NO_FOCUS_ISO or not point_in_country(lon, lat, iso) or RD.in_no_focus(lon, lat):
        return None
    lab = (pl.get("label") or "").strip()
    if plen(lab) > LIM["place_label"] or banned_hits(lab):
        lab = ""
    return {"iso3": iso, "lon": round(lon, 3), "lat": round(lat, 3), "label": lab}


def seg_from(d, key, src_text):
    """Writer JSON -> rundown SEG (+ list of on-screen lines). Optional parts that break a rule are dropped; required parts raise."""
    if d.get("safe") is False:
        raise ValueError("writer marked the story unsafe")
    for k in ("title", "headline", "strap"):     # "brief" = the story's one-liner: never on screen (only used for overlap)
        if not (d.get(k) or "").strip():
            raise ValueError(f"missing {k}")
        if plen(d[k]) > LIM[k]:
            raise ValueError(f"{k} too long ({plen(d[k])} > {LIM[k]})")
    names = d.get("sources") or []
    srcs = A.rank(names, src_text, limit=2)
    tag_src = "المصدر: " + (" · ".join(srcs) if srcs else A.GENERIC)
    place = valid_place(d["place"]) if isinstance(d.get("place"), dict) else None
    mp = RD.map_from_place(place) if place else None
    seg = {"key": key, "title": d["title"].strip(), "tag": (d.get("tag") or "").strip()[:LIM["tag"]] or None,
           "headline": d["headline"].strip(), "shots": []}
    if mp:
        seg["map"] = mp
    seg["shots"].append({"type": "strap", "text": d["strap"].strip(), "vis": "map" if mp else None, "src": tag_src})
    ml = (d.get("map_line") or "")
    if mp and isinstance(ml, str) and ml.strip() and plen(ml) <= LIM["map_line"]:
        seg["shots"].append({"type": "map", "tab": "أين", "text": ml.strip(), "src": tag_src})
    nb = d.get("number")
    if isinstance(nb, dict) and str(nb.get("num") or "").strip() and plen(nb.get("label")) <= LIM["label"] and \
            plen(nb.get("deck")) <= LIM["deck"] and (nb.get("label") or "").strip() and number_ok(str(nb["num"]).strip(), src_text):
        seg["shots"].append({"type": "number", "num": str(nb["num"]).strip(), "label": nb["label"].strip(),
                             "deck": (nb.get("deck") or "").strip(), "vis": "map" if mp else None, "src": tag_src})
    q = d.get("quote")
    if isinstance(q, dict) and (q.get("text") or "").strip() and (q.get("who") or "").strip() and plen(q["text"]) <= LIM["qtext"] and \
            plen(q["who"]) <= LIM["who"] and plen(q.get("role")) <= LIM["role"]:
        seg["shots"].append({"type": "quote", "text": q["text"].strip(), "who": q["who"].strip(), "role": (q.get("role") or "").strip(),
                             "vis": "map" if mp else None, "src": tag_src})
    nx = d.get("next")
    if isinstance(nx, dict) and isinstance(nx.get("rows"), list):
        rows = [r.strip() for r in nx["rows"] if isinstance(r, str) and r.strip() and plen(r) <= LIM["row"]][:3]
        if len(rows) >= 2:
            seg["shots"].append({"type": "next", "title": "ماذا [[بعد؟]]", "rows": [{"i": str(i + 1), "t": r} for i, r in enumerate(rows)],
                                 "src": tag_src})
    if len(seg["shots"]) < 2:
        raise ValueError("fewer than 2 usable shots")
    return seg, (d.get("brief") or "").strip() if isinstance(d.get("brief"), str) else "", (srcs[0] if srcs else A.GENERIC)


def seg_lines(seg, brief=None):
    """On-screen lines of a main segment. The segment's own one-liner (`brief`) is NOT on screen since 2026-10-29
    («باختصار» carries other stories only), so it is neither shown nor verified."""
    out = [seg["title"], seg["headline"]]
    for s in seg["shots"]:
        out += RD._shot_texts(s)
    if seg.get("map") and seg["map"]["point"].get("label"):
        out.append(seg["map"]["point"]["label"])
    return [x for x in out if x and x.strip()]


def _owner_lines(seg, brief):
    """[(line, owner)]: owner None = required (title/headline/brief/first strap/place label), else ("shot", i) or ("row", i, j)."""
    out = [(seg["title"], None), (seg["headline"], None)]
    for i, s in enumerate(seg["shots"]):
        if s["type"] == "next":
            out.append((s["title"], ("shot", i)))
            out += [(r["t"], ("row", i, j)) for j, r in enumerate(s["rows"])]
        else:
            out += [(t, None if i == 0 else ("shot", i)) for t in RD._shot_texts(s) if t and t.strip()]
    if seg.get("map") and seg["map"]["point"].get("label"):
        out.append((seg["map"]["point"]["label"], None))
    return out


def prune(seg, brief, bad):
    """Drop the OPTIONAL shots / «ماذا بعد» rows that carry unsupported claims (like the reel caption drop). Returns a new seg,
    or None when an unsupported claim sits in a required line or fewer than 2 shots would remain."""
    from ..verify import _clitic, _toks
    owners = _owner_lines(seg, brief)

    def overlap(a, b):
        A_ = {_clitic(w) for w in _toks(a)}
        B_ = [_clitic(w) for w in _toks(b)]
        return sum(w in A_ for w in B_) / max(1, len(B_))
    drop_shots, drop_rows = set(), set()
    for b in bad:
        probe = b.get("span") or b.get("claim") or ""
        best = max(owners, key=lambda o: overlap(o[0].replace("[[", "").replace("]]", ""), probe))
        if overlap(best[0], probe) < 0.5 or best[1] is None:
            return None
        if best[1][0] == "shot":
            drop_shots.add(best[1][1])
        else:
            drop_rows.add(best[1][1:])
    seg = json.loads(json.dumps(seg, ensure_ascii=False))
    for i, s in enumerate(seg["shots"]):
        if s["type"] == "next":
            s["rows"] = [r for j, r in enumerate(s["rows"]) if (i, j) not in drop_rows]
            for k, r in enumerate(s["rows"]):
                r["i"] = str(k + 1)
            if len(s["rows"]) < 2:
                drop_shots.add(i)
    seg["shots"] = [s for i, s in enumerate(seg["shots"]) if i not in drop_shots]
    return seg if len(seg["shots"]) >= 2 else None


def verify_lines(checker, lines, src_text):
    srcs = [p.strip() for p in re.split(r"\n(?=- \[)", src_text) if p.strip()]
    txt = "\n".join(dict.fromkeys(l.replace("[[", "").replace("]]", "") for l in lines))
    claims, bad = vcheck(checker, txt, srcs)
    return claims, bad


# ------------------------------------------------------------------ self-contained lines (owner 2026-10-29)
# «المشتبه به أقر بالتخطيط المسبق للهجوم» on «باختصار»: who? which attack? where? Every headline / brief must name the story's
# place/country or its named actor; a vague opener must be qualified at once.
VAGUE_OPENERS = ("المشتبه به", "المشتبه بهم", "المشتبه", "المتهم", "المتهمون", "الحادث", "الحادثة", "الهجوم", "الضحايا", "الضحية",
                 "القتلى", "السلطات", "الانفجار", "الضربة", "الجاني", "المنفذ", "الحكومة", "الرئيس", "الشرطة", "الجيش", "الوزير",
                 "المحكمة", "العملية", "الاتفاق", "القرار", "التحقيق", "المسؤولون", "الطرفان")
_GENERIC_ANCHOR = {"مدينه", "محافظه", "منطقه", "ولايه", "اقليم", "شمال", "جنوب", "شرق", "غرب", "وسط", "ساحل", "وزاره", "وزير", "رئيس",
                   "الرئيس", "حكومه", "الحكومه", "هيئه", "مكتب", "مجلس", "وكاله", "قوات", "جيش", "شرطه", "السلطات", "جهاز", "النيابه",
                   "الوطنيه", "العامه", "المحليه", "الدوليه", "الامم", "المتحده", "قرب", "بلده", "مطار", "ميناء", "جزيره"}


def _core(w):
    from ..newsroom import norm
    w = norm(w).strip()
    if w.startswith("ال") and len(w) > 4:
        w = w[2:]
    for suf in ("يه", "يا", "ه", "ا"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w


def anchors_for(place=None, actor=None, who=None, src_text=""):
    """Words that tell a viewer who/where: place label, the place's country (Arabic name from the map data), the named actor
    (only when it is in the sources), a quoted speaker. -> set of normalised cores (>= 3 chars, generic words out)."""
    from ..newsroom import norm
    names = []
    if place:
        names.append(place.get("label") or "")
        iso = place.get("iso3") or (place.get("iso") if isinstance(place, dict) else None)
        f = RD._feat(iso) if iso else None
        if f and RD.geo.label_of(f):
            names.append(RD.geo.label_of(f))
    nsrc = norm(src_text or "")
    for a in (actor, who):
        if isinstance(a, str) and a.strip() and (not src_text or all(_core(w) in nsrc for w in a.split()
                                                                         if len(_core(w)) >= 3 and norm(w).strip() not in _GENERIC_ANCHOR)):
            names.append(a)
    for lab in _source_countries(src_text):        # a country the sources name is a valid «where» (country-wide stories: no point)
        names.append(lab)
    out = set()
    for n in names:
        for w in re.split(r"[\s·،,/-]+", n or ""):
            c = _core(w)
            if len(c) >= 3 and norm(w).strip() not in _GENERIC_ANCHOR and c not in _GENERIC_ANCHOR:
                out.add(c)
    return out


COUNTRY_ALIASES = {"الولايات المتحدة": ("أمريكا", "الأمريكية", "الأمريكي", "واشنطن"), "المملكة المتحدة": ("بريطانيا", "البريطاني", "لندن")}


def _source_countries(src_text):
    """Arabic country names (map labels + a few aliases) that appear in the sources; banned / no-focus shapes excluded."""
    from ..newsroom import norm
    if not src_text:
        return []
    ns = norm(src_text)
    out = []
    for f in RD.geo.features():
        lab = RD.geo.label_of(f)
        if not lab or RD.geo.is_no_focus(f) or f["properties"].get("a") in BANNED_ISO:
            continue
        for name in (lab,) + COUNTRY_ALIASES.get(lab, ()):
            if norm(name).strip() and norm(name).strip() in ns:
                out.append(name)
                out += [a for a in COUNTRY_ALIASES.get(lab, ())]
                break
    return out


def _has_anchor(text, anchors):
    from ..newsroom import norm
    t = norm(re.sub(r"\[\[|\]\]", "", text or ""))
    return any(a in t for a in anchors)


def vague_opener(line, anchors):
    """The vague opener a line starts with when it is not qualified (no place/actor within the next 4 words), else None."""
    t = re.sub(r"\[\[|\]\]", "", line or "").strip(" «»\"'.:،-–")
    t = re.sub(r"^و(?=ال)", "", t)
    for v in sorted(VAGUE_OPENERS, key=len, reverse=True):
        if t == v or t.startswith(v + " "):
            head = " ".join(t.split()[: len(v.split()) + 4])
            return None if _has_anchor(head, anchors) else v
    return None


def self_contained_problems(lines, anchors, need_anchor=("headline", "brief")):
    """Deterministic gate. lines = [(kind, text)]. Headline + brief must carry an anchor; title/headline/brief must not open
    vaguely. No anchors known at all -> the brief/headline cannot be shown (fail closed)."""
    p = []
    for kind, txt in lines:
        if not (txt or "").strip():
            continue
        plain = txt.replace("[[", "").replace("]]", "")
        if kind in need_anchor and not anchors:
            p.append(f"{kind}: story has no named place or actor to anchor the line: {plain[:60]}")
        elif kind in need_anchor and not _has_anchor(txt, anchors):
            p.append(f"{kind} not self-contained (name who/where): {plain[:60]}")
        if kind in ("headline", "brief", "title"):
            v = vague_opener(txt, anchors)
            if v:
                p.append(f"{kind} opens with «{v}» without saying which/where: {plain[:60]}")
    return p


STANDALONE_PROMPT = """أنت مدقق تحرير في نشرة أخبار مرئية بلا صوت. السطور التالية تظهر على الشاشة. لكل سطر احكم: هل يفهمه مشاهد يراه وحده
(أو داخل فقرته إن ذُكرت الفقرة) فيعرف عن أي خبر يتحدث، دون أن يحتاج إلى سطر آخر؟
يكفي أن يحدد السطر الحدث مع فاعل مسمّى (شخص أو جهة أو بلد) أو مع المكان؛ لا يُشترط ذكر الاثنين ولا ذكر مكان وفاة أو تاريخ.
ارفض فقط السطر الذي يترك المشاهد يسأل «مَن؟» أو «أي حدث؟»، مثل سطر يبدأ بـ«المشتبه به» أو «الهجوم» أو «الحادث» دون تحديد أي هجوم وأين.
عنوان الفقرة القصير (2-3 كلمات) مقبول إن حدّد الخبر. سطر داخل فقرة مقبول إن فُهم مع عنوان فقرته. لا تحكم على الصحة، فقط على الوضوح.
أعد JSON فقط: {"results": [{"i": 1, "ok": true, "why": "سبب قصير إن كان غير مفهوم"}]}

السطور:
"""
R.register(STANDALONE_PROMPT, {"results": list})


def standalone_check(checker, items):
    """Different-family LLM: is each line understandable on its own? items = [(kind, text, context or "")].
    Returns [(index, kind, text, why)] of lines judged not self-contained. Fail closed: unparseable / missing verdict = bad."""
    if not items:
        return []
    body = "\n".join(f"{i + 1}. [{k}] {t.replace('[[', '').replace(']]', '')}" + (f"   (داخل فقرة «{ctx}»)" if ctx else "")
                     for i, (k, t, ctx) in enumerate(items))
    obj = _json(checker(STANDALONE_PROMPT + body, temperature=0.0))
    res = {}
    for r in obj.get("results") or []:
        if isinstance(r, dict):
            try:
                res[int(r.get("i"))] = r
            except (TypeError, ValueError):
                pass
    bad = []
    for i, (k, t, _) in enumerate(items):
        r = res.get(i + 1)
        if r is None or r.get("ok") is not True:
            bad.append((i, k, t, (r or {}).get("why") or "no verdict"))
    return bad


def write_segment(writer, checker, c, key, fetch_articles=True, brief_only=False):
    """One story -> (seg or None, brief dict, meta). Fail closed: any failure after one rewrite -> None."""
    src = source_block(c["items"], fetch_articles=fetch_articles)
    prompt = (BRIEF_PROMPT if brief_only else SEG_PROMPT) + src
    hint = ""
    last = None
    for attempt in range(2):
        try:
            d = _json(writer(prompt + hint, temperature=0.4))
            if brief_only:
                if d.get("safe") is False:
                    raise ValueError("writer marked the story unsafe")
                if not (d.get("brief") or "").strip() or plen(d["brief"]) > LIM["brief"]:
                    raise ValueError(f"brief missing or too long ({plen(d.get('brief'))} > {LIM['brief']} حرفاً): اكتب جملة أقصر "
                                     "تذكر الفاعل أو المكان")
                if plen(d.get("headline")) > LIM["headline"] or not (d.get("headline") or "").strip():
                    raise ValueError("headline missing/too long")
                srcs = A.rank([d.get("source") or ""], src, limit=1)
                place = valid_place(d["place"]) if isinstance(d.get("place"), dict) else None
                seg = None
                lines = [d["brief"], d["headline"]] + ([place["label"]] if place and place["label"] else [])
                brief = {"t": d["brief"].strip(), "s": srcs[0] if srcs else A.GENERIC}
                extra = {"headline": d["headline"].strip(), "place": place, "urgent": bool(d.get("urgent"))}
                anchors = anchors_for(place, d.get("actor"), src_text=src)
                sc_lines = [("brief", brief["t"]), ("headline", extra["headline"])]
                sa_items = [("brief", brief["t"], "")]
            else:
                seg, b, s0 = seg_from(d, key, src)
                lines = seg_lines(seg, b)
                brief = {"t": b, "s": s0}
                pt = (seg.get("map") or {}).get("point")
                extra = {"urgent": bool(d.get("urgent")), "place": dict(pt, iso3=seg["map"]["iso3"]) if pt else None}
                q = d.get("quote") if isinstance(d.get("quote"), dict) else {}
                anchors = anchors_for(extra["place"] or (valid_place(d["place"]) if isinstance(d.get("place"), dict) else None),
                                      d.get("actor"), q.get("who"), src_text=src)
                sc_lines = [("title", seg["title"]), ("headline", seg["headline"])]
                # bumper titles (2-3 words) only get the deterministic vague-opener gate: the LLM judge rejected fine
                # titles such as «هجوم في دنيبرو» / «هجوم تراب الفرنسي» (run 40971888923) and cost whole segments.
                sa_items = [("headline", seg["headline"], "")] + \
                           [(sh["type"], sh["text"], seg["title"]) for sh in seg["shots"] if sh["type"] in ("strap", "map")]
            probs = screen_problems(lines, src) + self_contained_problems(sc_lines, anchors)
            if probs:
                raise ValueError("; ".join(probs[:4]))
            # Arabic spelling sanity check (2026-10-31): safe typo fixes applied, anything else fails closed (rewrite/drop)
            fixes, unresolved = spelling.proofread(R.bound("utility"), lines, src)
            if unresolved:
                raise ValueError("spelling: " + "، ".join(unresolved[:4]))
            if fixes:
                brief, extra = spelling.apply(brief, fixes), spelling.apply(extra, fixes)
                if seg is not None:
                    seg = spelling.apply(seg, fixes)
                    lines = seg_lines(seg, brief["t"])
                else:
                    lines = spelling.apply(lines, fixes)
                sa_items = spelling.apply(sa_items, fixes)
            unclear = standalone_check(checker, sa_items)
            if unclear and seg is not None and all(k == "map" for _, k, _, _ in unclear):
                seg = dict(seg, shots=[sh for sh in seg["shots"] if sh["type"] != "map"])   # optional «أين» line: drop it
                lines = seg_lines(seg, brief["t"])
                log({"bulletin_unclear_map_line_dropped": c["items"][0]["title"][:70]})
                unclear = [] if len(seg["shots"]) >= 2 else unclear
            if unclear:
                raise ValueError("not understandable on its own (name who/where/what): " +
                                 " | ".join(f"{k}: «{t.replace('[[', '').replace(']]', '')[:50]}» ({w[:60]})" for _, k, t, w in unclear[:4]))
            claims, bad = verify_lines(checker, lines, src)
            if bad and seg is not None:
                pr = prune(seg, brief["t"], bad)
                if pr is not None:                # unsupported optional lines dropped: verify what is left once more
                    log({"bulletin_pruned": c["items"][0]["title"][:70], "dropped": [b.get("claim", "")[:80] for b in bad[:5]]})
                    seg = pr
                    lines = seg_lines(seg, brief["t"])
                    claims, bad = verify_lines(checker, lines, src)
            if bad:
                raise ValueError("unsupported: " + " | ".join(b["claim"] for b in bad[:5]))
            meta = {"outlets": c["sources"], "claims": len(claims), "attempts": attempt + 1, **extra}
            return seg, brief, meta
        except R.Hold:
            raise
        except Exception as ex:
            last = str(ex)[:400]
            log({"bulletin_story_retry" if attempt == 0 else "bulletin_story_dropped": c["items"][0]["title"][:70], "why": last})
            hint = ("\n\nملاحظة من المدقق: النسخة السابقة رُفضت لهذه الأسباب، أصلحها والتزم بالمصادر حرفياً واحذف ما لا تدعمه: " + last + "\n")
    return None, None, {"error": last, "outlets": c["sources"]}


def _dup_segment(seg, segs, km=25.0):
    """Same story clustered twice (run 40963351803: two «هجوم في دنيبرو» segments, 13 vs 14 dead): identical plain title, or the
    same country with story points within `km`."""
    plain = lambda t: re.sub(r"\[\[|\]\]|\s+", "", t or "")
    p = (seg.get("map") or {}).get("point")
    for g in segs:
        if plain(g["title"]) == plain(seg["title"]):
            return True
        q = (g.get("map") or {}).get("point")
        if p and q and (seg.get("map") or {}).get("iso3") == (g.get("map") or {}).get("iso3"):
            dlat = math.radians(q["lat"] - p["lat"])
            dlon = math.radians(q["lon"] - p["lon"]) * math.cos(math.radians((p["lat"] + q["lat"]) / 2))
            if 6371.0 * math.hypot(dlat, dlon) < km:
                return True
    return False


def _cluster_sim(a, b, min_inter=3, ratio=0.22):
    """Same story in two clusters (newsroom.cluster rule, on the clusters' token sets)."""
    ta, tb = a.get("tok") or set(), b.get("tok") or set()
    inter = len(ta & tb)
    return inter >= min_inter and inter / max(1, min(len(ta), len(tb))) >= ratio


def build_spec(date, asof, n_main=5, n_brief=3, fetch_articles=True, mode="bulletin"):
    """Pick, write and verify the day's stories. Returns (spec, report). Raises RundownError when too few stories verify."""
    writer = R.bound("writer")
    checker = R.bound("factcheck", exclude_from=writer)
    want_main = n_main if mode == "bulletin" else 3
    clusters = select_clusters(n=want_main + n_brief + 6)
    segs, briefs, rep, pings, used, seg_clusters, unusable = [], [], [], [], set(), [], set()
    for c in clusters:
        if len(segs) >= want_main:
            break
        key = f"s{len(segs) + 1}"
        if mode == "edition":
            seg, br, meta = write_segment(writer, checker, c, key, fetch_articles, brief_only=True)
            if br:
                pl = meta.get("place")
                mp = RD.map_from_place(pl) if pl else None
                seg = {"key": key, "title": fit_title(br["t"], 18, max_over=999) or br["t"][:18], "headline": meta["headline"], "shots": []}
                if mp:
                    seg["map"] = mp
        else:
            seg, br, meta = write_segment(writer, checker, c, key, fetch_articles)
        if seg and _dup_segment(seg, segs):
            log({"bulletin_story_duplicate": c["items"][0]["title"][:70], "title": seg["title"]})
            meta = dict(meta, error="duplicate of an earlier segment (same title or same place)")
            seg = None
        rep.append({"title": c["items"][0]["title"][:90], "ok": bool(seg), **{k: v for k, v in meta.items() if k != "place"}})
        used.add(c["key"])
        if not seg and re.search(r"duplicate|unsafe", str(meta.get("error") or "")):
            unusable.add(c["key"])
        if seg:
            seg = dict(seg, cluster=c["key"], brief_t=br["t"])
            segs.append(seg)
            seg_clusters.append(c)
            pl = meta.get("place")
            if pl:
                pings.append([pl["lon"], pl["lat"]])
    if mode == "bulletin":
        # «باختصار» = OTHER stories, never a repeat/update of a segment (owner 2026-10-29: the Trappes confession came back as
        # brief 1). Candidates: clusters not used as a segment (a story that failed as a full segment may still be a short item),
        # not token-similar to a segment's cluster, and after writing not overlapping a segment or another brief (RD.brief_overlap).
        seg_keys = {g["cluster"] for g in segs}
        for c in clusters:
            if len(briefs) >= n_brief:
                break
            if c["key"] in seg_keys or c["key"] in unusable:
                continue
            sim = next((g for g in seg_clusters if _cluster_sim(c, g)), None)
            if sim is not None:
                log({"brief_candidate_same_story": c["items"][0]["title"][:70], "as": sim["items"][0]["title"][:70]})
                continue
            _, br, meta = write_segment(writer, checker, c, "b", fetch_articles, brief_only=True)
            why = None
            if br:
                br = dict(br, cluster=c["key"], place=meta.get("place"))
                why = next((f"{g['key']}: {w}" for g in segs if (w := RD.brief_overlap(br, g))), None) or \
                    next((f"brief: {w}" for o in briefs if (w := RD.brief_overlap(br, {"title": o["t"], "map": (
                        {"iso3": o["place"]["iso3"], "point": o["place"]} if o.get("place") else None)}))), None)
                if why:
                    log({"brief_overlaps": br["t"], "why": why})
            rep.append({"title": c["items"][0]["title"][:90], "ok": bool(br) and not why, "brief": True,
                        **({"error": "overlaps " + why} if why else {}), **{k: v for k, v in meta.items() if k != "place"}})
            if br and not why:
                log({"brief_ok": br["t"], "s": br["s"]})
                briefs.append(br)
        if len(briefs) < RD.MIN_BRIEF:    # never fall back to the segments' own one-liners: no section today
            log({"brief_section_dropped": len(briefs), "kept_off_screen": [b["t"] for b in briefs]})
            briefs = []
        need = RD.MIN_MAIN
    else:
        need = 2
    if len(segs) < need:
        raise RD.RundownError(f"only {len(segs)} verified stories (need {need}); no {mode} today")
    spec = {"date": date, "asof": asof, "segments": segs, "brief": briefs, "media": {}, "pings_ll": pings[:4]}
    return spec, rep
