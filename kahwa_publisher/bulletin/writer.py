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
- الأطوال إلزامية (تُعدّ الحروف بدون [[ ]]): title حتى 16 (كلمتان أو ثلاث)، tag حتى 12، headline حتى 52، strap حتى 58، map_line حتى 58،
  number.label حتى 30، number.deck حتى 52، quote.text حتى 52، quote.who حتى 24، quote.role حتى 30، كل سطر في next.rows حتى 50، brief حتى 44.
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
 "brief": "الخبر في سطر قصير مع [[...]]", "sources": ["..."], "urgent": false, "safe": true}
اجعل "safe" = false إذا كان الخبر رياضياً أو لا يُروى دون الدول الممنوعة أو بلا تفاصيل دموية أو كان دعاية أو لا يمكن التحقق منه.

المصادر:
"""

BRIEF_PROMPT = """أنت محرر نشرة أخبار. اكتب الخبر التالي في سطر واحد على الشاشة حتى 44 حرفاً بالعربية الفصحى، مع الكلمة الأهم بين [[ ]]،
اعتماداً فقط على المصادر: كل رقم موجود حرفياً فيها، بلا رأي ولا تفاصيل دموية، ممنوع ذكر المغرب أو الإمارات أو الجزائر أو إسرائيل،
وممنوع ذكر أي قناة أو موقع إخباري. "source" = الجهة الأصلية التي تقول المصادر إنها أعلنت الخبر (رسمية أو وكالة أنباء) أو "".
للعنوان القصير (headline) حتى 52 حرفاً مع [[...]]، و place كما في الخبر أو null.
أعد JSON فقط: {"brief": "...", "headline": "...", "source": "...", "place": {"iso3": "ISO3", "lon": 0.0, "lat": 0.0, "label": "..."}, "urgent": false, "safe": true}

المصادر:
"""

R.register(SEG_PROMPT, {"headline": str, "title": str})
R.register(BRIEF_PROMPT, {"brief": str})

# title: the prompt asks for <= 16; 26 is the hard cap (bumper + rail shrink long titles to one line). Titles are never trimmed:
# a word-boundary cut broke «الولايات المتحدة» (run 40960800162), and 22 dropped a whole story on 23 chars (run 40953097998).
LIM = {"title": 26, "tag": 12, "headline": 52, "strap": 58, "map_line": 58, "label": 30, "deck": 52, "qtext": 52, "who": 24,
       "role": 30, "row": 50, "brief": 44, "place_label": 20}


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
        if len(c["sources"]) < min_sources or N.blocked(c) or not safety.keyword_ok(c):
            continue
        text = " ".join(i["title"] + " " + i["summary"] for i in c["items"])
        words = set(N.norm(text).split())
        if any((k in text) if " " in k else (N.norm(k) in words) for k in N.SPORTS):
            continue
        cands.append(c)
        if len(cands) >= 12:
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
    for k in ("title", "headline", "strap", "brief"):
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
    return seg, d["brief"].strip(), (srcs[0] if srcs else A.GENERIC)


def seg_lines(seg, brief):
    out = [seg["title"], seg["headline"], brief]
    for s in seg["shots"]:
        out += RD._shot_texts(s)
    if seg.get("map") and seg["map"]["point"].get("label"):
        out.append(seg["map"]["point"]["label"])
    return [x for x in out if x and x.strip()]


def _owner_lines(seg, brief):
    """[(line, owner)]: owner None = required (title/headline/brief/first strap/place label), else ("shot", i) or ("row", i, j)."""
    out = [(seg["title"], None), (seg["headline"], None), (brief, None)]
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
                if d.get("safe") is False or not (d.get("brief") or "").strip() or plen(d["brief"]) > LIM["brief"]:
                    raise ValueError("brief missing/too long/unsafe")
                if plen(d.get("headline")) > LIM["headline"] or not (d.get("headline") or "").strip():
                    raise ValueError("headline missing/too long")
                srcs = A.rank([d.get("source") or ""], src, limit=1)
                place = valid_place(d["place"]) if isinstance(d.get("place"), dict) else None
                seg = None
                lines = [d["brief"], d["headline"]] + ([place["label"]] if place and place["label"] else [])
                brief = {"t": d["brief"].strip(), "s": srcs[0] if srcs else A.GENERIC}
                extra = {"headline": d["headline"].strip(), "place": place, "urgent": bool(d.get("urgent"))}
            else:
                seg, b, s0 = seg_from(d, key, src)
                lines = seg_lines(seg, b)
                brief = {"t": b, "s": s0}
                extra = {"urgent": bool(d.get("urgent")), "place": (seg.get("map") or {}).get("point")}
            probs = screen_problems(lines, src)
            if probs:
                raise ValueError("; ".join(probs[:4]))
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


def build_spec(date, asof, n_main=5, n_brief=3, fetch_articles=True, mode="bulletin"):
    """Pick, write and verify the day's stories. Returns (spec, report). Raises RundownError when too few stories verify."""
    writer = R.bound("writer")
    checker = R.bound("factcheck", exclude_from=writer)
    want_main = n_main if mode == "bulletin" else 3
    clusters = select_clusters(n=want_main + n_brief + 3)
    segs, briefs, rep, pings, used, main_briefs = [], [], [], [], set(), []
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
        if seg:
            segs.append(seg)
            main_briefs.append(br)
            pl = meta.get("place")
            if pl:
                pings.append([pl["lon"], pl["lat"]])
    if mode == "bulletin":
        for c in clusters:
            if len(briefs) >= n_brief:
                break
            if c["key"] in used:
                continue
            _, br, meta = write_segment(writer, checker, c, "b", fetch_articles, brief_only=True)
            rep.append({"title": c["items"][0]["title"][:90], "ok": bool(br), "brief": True, **{k: v for k, v in meta.items() if k != "place"}})
            if br:
                briefs.append(br)
        if not briefs:                 # «باختصار» falls back to the main stories' own verified one-liners
            briefs = main_briefs[:3]
        need = RD.MIN_MAIN
    else:
        need = 2
    if len(segs) < need:
        raise RD.RundownError(f"only {len(segs)} verified stories (need {need}); no {mode} today")
    spec = {"date": date, "asof": asof, "segments": segs, "brief": briefs, "media": {}, "pings_ll": pings[:4]}
    return spec, rep
