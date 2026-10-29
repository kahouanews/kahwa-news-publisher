"""«قهوة اليوم» rundown: spec (segments written + verified by writer.py, or a saved fixture) -> timed scene list for the body
composition (templates/body.tpl.js). Ported from the approved pilot work/bulletin-pilot-v4/src/rundown.py.

Structure (owner, 2026-10-29): opener A «المدار» (separate module) -> «أبرز العناوين» (one headline per main segment, same
order, permanent gate) -> numbered segments, each opened by a bumper -> «باختصار» round-up -> sign-off. Voiceless, no
verification card. Every on-screen string passes the banned-name gate; map cameras never centre on a no-focus shape.

spec = {"date": "YYYY-MM-DD", "asof": "HH:MM", "segments": [SEG, ...],
        "brief": [{"t": "...", "s": "source", "cluster"?: id, "place"?: {iso3, lon, lat, label}}, ...] (0 or 2-3 items),
        "media": {name: {"img": file, "credit": "...", "archive": "...", "pos": "50% 50%"}}}
SEG  = {"key", "title", "tag", "headline", "hl_vis"?, "map"?, "cluster"?, "brief_t"?, "shots": [SHOT, ...]}
SHOT = {"type": strap|map|number|grid|timeline|quote|next|price|bars|rates, "tab", "src", ... type fields,
        "vis": "map" | "photo:<media name>" | {"kind": ...} | None}
"""
import copy
import json
import math
import re

from .. import geo
from ..reels.render import banned_hits
from .. import attribution as A
from .. import safety

SHOT_TYPES = {"strap", "map", "number", "grid", "timeline", "quote", "next", "price", "bars", "rates"}
MIN_MAIN, MAX_MAIN = 3, 5
MIN_BRIEF, MAX_BRIEF = 2, 3            # «باختصار»: 2-3 OTHER stories, or no section at all (owner 2026-10-29)
BRIEF_MAX_CHARS = 64                   # 2 lines at the shrunk list sizes (templates/body.css .lrows.lsm / .lxs); 75+ wrapped to 3
# Outro card (owner 2026-10-29): «انتهت نشرة اليوم» + credits only. The two-source rule stays internal, never on screen.
END_BULLETIN = "انتهت نشرة اليوم"
END_FORBIDDEN = ("قهوة اليوم", "غدًا", "غدا", "مصدرين")
# Layout contract shared with templates/body.tpl.js and qa.mjs (Meta 9:16 safe zone + the lower-right action rail)
SAFE = {"l": 65, "r": 1015, "t": 269, "b": 1248}
RAIL = {"x": 853, "y": 1152}
LATIN_BANNED = re.compile(r"(?i)morocc|algeri|israel|emirat|\buae\b")
TAB_DEFAULT = {"strap": "الخبر", "map": "أين", "number": "بالأرقام", "grid": "بالأرقام", "timeline": "كيف وقع",
               "quote": "تصريح", "next": "ماذا بعد", "price": "اقتصاد", "bars": "اقتصاد", "rates": "اقتصاد"}
NUMS = ["الخبر الأول", "الخبر الثاني", "الخبر الثالث", "الخبر الرابع", "الخبر الخامس"]
WD = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MO = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


class RundownError(ValueError):
    pass


def words(s):
    s = re.sub(r"\[\[|\]\]", "", s or "")
    return [w for w in s.split() if w.strip("·:،.-–«»")]


def rt(*texts, lead=0.9, floor=3.4):
    """Reading rule (research-newscast-anatomy §4.6-5): >= 1.2 s + 0.35 s/word, floor, + lead for the words to land."""
    n = sum(len(words(t)) for t in texts if t)
    return round(max(floor, 1.2 + 0.35 * n + lead), 2), n


def date_ar(iso):
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{WD[d.weekday()]} {d.day} {MO[d.month - 1]} {d.year}"


# ------------------------------------------------------------------ maps
_LABELLED_NO_FOCUS = ("MAR", "DZA", "ARE")          # drawn + labelled on maps, but a camera never centres on them


def _feat(iso3):
    return next((f for f in geo.features() if f["properties"].get("a") == iso3), None)


def _rings(f):
    g = f["geometry"]
    return [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]


def _inside(lon, lat, ring):
    c = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
            c = not c
    return c


def in_no_focus(lon, lat):
    """True when (lon, lat) lies inside a no-focus shape (MAR, DZA, ARE, ISR, merged PSE)."""
    for f in geo.features():
        if geo.is_no_focus(f) and any(_inside(lon, lat, r) for r in _rings(f)):
            return True
    return False


def visible_no_focus(st, w=1080, h=1920, margin=40, isos=_LABELLED_NO_FOCUS):
    """ISO codes of labelled no-focus countries (or `isos`) with any vertex on screen for camera state st."""
    out = []
    for iso in isos:
        f = _feat(iso)
        if not f:
            continue
        for r in _rings(f):
            if any((p := geo._ortho_forward(x, y, st["lon"], st["lat"], st["scale"], 540, st.get("cy", 560))) and
                   -margin <= p[0] <= w + margin and -margin <= p[1] <= h + margin for x, y in r[::2]):
                out.append(iso)
                break
    return out


# top 380: the place-label pill is drawn 96 px above the point (body.tpl.js mapVis) and must stay below the safe-zone top (269);
# a point at y=332 put «مصر» above the safe zone in run 40957631882.
POINT_BOX = (90, 380, 990, 1200)


def point_on_screen(st, lon, lat, box=POINT_BOX, label=""):
    """Point inside `box`; with a label, the centred label pill (~40 + 13 px per char each side) must also clear the safe zone
    sides (65..1015) — «لوس أنجلس» was cut at the left edge in run 40963351803."""
    p = geo._ortho_forward(lon, lat, st["lon"], st["lat"], st["scale"], 540, st.get("cy", 560))
    x0, x1 = box[0], box[2]
    if label:
        half = 40 + 13 * len(label)
        x0, x1 = max(x0, SAFE["l"] + 10 + half), min(x1, SAFE["r"] - 10 - half)
    return bool(p) and x0 <= p[0] <= x1 and box[1] <= p[1] <= box[3]


def tighten(st, point=None, ks=(1.0, 1.25, 1.55, 1.9, 2.3)):
    """Never-centre rule + the Yemen fix (owner 2026-10-29: no «الإمارات» label on a Yemen map): zoom in step by step until
    no labelled no-focus country is on screen, as long as the story point stays framed. Returns (state, cleared)."""
    st = dict(st)
    if in_no_focus(st["lon"], st["lat"]):
        if not point:
            raise RundownError(f"map camera centred on a no-focus shape at {st['lon']},{st['lat']}")
        st["lon"], st["lat"] = point["lon"], point["lat"]
        if in_no_focus(st["lon"], st["lat"]):
            raise RundownError("story point inside a no-focus shape")
    base = st["scale"]
    fallback = None
    for k in ks:
        cand = dict(st, scale=round(base * k))
        if point and not point_on_screen(cand, point["lon"], point["lat"], label=point.get("label") or ""):
            break
        if not visible_no_focus(cand):
            return cand, True
        if fallback is None and not visible_no_focus(cand, isos=("ESH",)):
            fallback = cand
    # Western Sahara in view forces the «الصحراء الغربية» + «المغرب» label pair (owner rule); when the no-focus shapes cannot
    # all be cleared, at least keep Western Sahara out of frame (QA rejects a frame where the pair is incomplete).
    if visible_no_focus(st, isos=("ESH",)) and fallback is not None:
        return fallback, False
    return st, not visible_no_focus(st)


def safe_map(mp):
    """Apply tighten() to both camera states of a map dict (s0 = opening, s1 = final)."""
    mp = copy.deepcopy(mp)
    pt = mp.get("point")
    for k in ("s0", "s1"):
        if k in mp:
            mp[k], _ = tighten(mp[k], pt)
    if "s0" in mp and "s1" in mp:
        if mp["s0"]["scale"] > mp["s1"]["scale"]:
            mp["s0"]["scale"] = round(mp["s1"]["scale"] * 0.85)      # keep the push-in direction
        if visible_no_focus(mp["s0"], isos=("ESH",)) and not visible_no_focus(mp["s1"], isos=("ESH",)):
            mp["s0"] = dict(mp["s1"], scale=round(mp["s1"]["scale"] * 0.94))
        if visible_no_focus(mp["s0"]) and not visible_no_focus(mp["s1"]):
            # the opening frame would still show a no-focus label (Yemen -> «الإمارات»): open from the final framing instead
            for k in (0.88, 0.94, 1.0):
                cand = dict(mp["s1"], scale=round(mp["s1"]["scale"] * k))
                if not visible_no_focus(cand):
                    mp["s0"] = cand
                    break
    return mp


def map_from_place(place):
    """{"iso3","lon","lat","label"} (validated by the writer) -> map dict for the body template, or None.
    Zoom from the country part that holds the story point (not the whole feature: France + French Guiana would give a
    hemisphere view with hundreds of labels), pilot-like framing: s0 = that part's centre, s1 = pushed towards the point."""
    if not place or place.get("iso3") in geo.NO_FOCUS_ISO:
        return None
    f = _feat(place["iso3"])
    if not f or geo.is_no_focus(f):
        return None
    lon, lat = float(place["lon"]), float(place["lat"])
    rings = _rings(f)
    holder = [r for r in rings if _inside(lon, lat, r)]
    if holder:
        ring = holder[0]
    else:
        ring = min(rings, key=lambda r: min((x - lon) ** 2 + (y - lat) ** 2 for x, y in r))
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    cx, cy_ = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    ext = max((max(xs) - min(xs)) * math.cos(math.radians(cy_)), max(ys) - min(ys), 2.0)
    s1 = max(1500, min(8000, 1.6 * 560 / math.radians(ext)))
    st1 = {"lon": round((cx + lon) / 2, 3), "lat": round((cy_ + lat) / 2, 3), "scale": round(s1), "cy": 560, "tfo": 0.08}
    lab = place.get("label") or ""
    if not point_on_screen(st1, lon, lat, label=lab):     # keep the story point (and its label pill) framed: pan, keep the zoom
        st1.update(lon=round(lon, 3), lat=round(lat, 3))
    st0 = dict(st1, lon=round(cx, 3), lat=round(cy_, 3), scale=round(st1["scale"] * 0.85))
    if in_no_focus(st0["lon"], st0["lat"]) or not point_on_screen(st0, lon, lat, label=lab):
        st0.update(lon=st1["lon"], lat=st1["lat"])          # zooming out about s1's centre keeps the point (and its label) framed
    pt = {"lon": lon, "lat": lat, "label": place.get("label") or ""}
    return safe_map({"iso3": place["iso3"], "point": pt, "s0": st0, "s1": st1})


# ------------------------------------------------------------------ build
def _vis(v, seg, media):
    if v is None:
        return None
    if v == "map":
        return {"kind": "map", "map": seg["map"]} if seg.get("map") else None
    if isinstance(v, str) and v.startswith("photo:"):
        m = media.get(v[6:])
        if not m:
            raise RundownError(f"unknown media {v}")
        return dict(kind="photo", **m)
    if isinstance(v, dict) and v.get("kind") == "map":
        return {"kind": "map", "map": safe_map(v["map"])}
    return v


def _shot_texts(s):
    t = s["type"]
    if t in ("strap", "map"):
        return [s["text"]]
    if t == "number":
        return [s["num"], s["label"], s.get("deck", "")]
    if t == "grid":
        return [s["title"]] + [x["v"] + " " + x["l"] for x in s["tiles"]]
    if t == "timeline":
        return [s["title"]] + [x["t"] for x in s["steps"]]
    if t == "quote":
        return [s["text"], s["who"] + " " + s.get("role", "")]
    if t == "next":
        return [s["title"]] + [r["t"] for r in s["rows"]]
    if t == "price":
        return [s["num"] + " " + s.get("unit", ""), s["label"], s.get("delta", "") + " " + s.get("asof", "")] + s.get("extra_txt", [])
    if t == "bars":
        return [s["title"]] + [" ".join(f"{b['lab']} {b['disp']}" for b in s["bars"])]
    if t == "rates":
        return [s["title"]] + [x["v"] + " " + x["l"] + " " + x.get("s", "") for x in s["tiles"]]
    return []


LEAD = {"timeline": 1.6, "next": 1.4, "price": 1.6, "bars": 1.6, "rates": 1.4}


def build(spec, mode="bulletin"):
    """mode "bulletin": full newscast body. mode "edition": «العالم في 30 ثانية» reel body (headlines + short outro)."""
    segs_in = spec["segments"]
    if mode == "bulletin" and not (MIN_MAIN <= len(segs_in) <= MAX_MAIN):
        raise RundownError(f"need {MIN_MAIN}-{MAX_MAIN} main segments, got {len(segs_in)}")
    if mode == "edition" and not (2 <= len(segs_in) <= 4):
        raise RundownError(f"edition reel needs 2-4 stories, got {len(segs_in)}")
    keys = [g["key"] for g in segs_in]
    if len(set(keys)) != len(keys):
        raise RundownError("duplicate segment keys")
    media = spec.get("media") or {}
    date = date_ar(spec["date"])
    segs_in = [dict(g, map=safe_map(g["map"])) if g.get("map") else g for g in segs_in]
    SC = []

    def add(**k):
        txt = k.pop("_txt", None)
        lead = k.pop("lead", 0.9)
        if "d" not in k:
            k["d"], k["words"] = rt(*txt, lead=lead)
        SC.append(k)

    SEGS = [dict(k=g["key"], t=re.sub(r"\[\[|\]\]", "", g["title"])) for g in segs_in]   # rail/summary: plain text, no [[ ]]
    BR = [dict(r) for r in (spec.get("brief") or [])] if mode == "bulletin" else []
    if BR:
        check_brief(segs_in, BR)
    if mode == "bulletin" and BR:
        SEGS.append(dict(k="brief", t="باختصار"))
        add(id="hl0", type="hltitle", d=2.6, title="أبرز العناوين", sub=date)
    for i, g in enumerate(segs_in, 1):
        hv = _vis(g.get("hl_vis") or ("map" if g.get("map") else None), g, media) or {"kind": "ink"}
        add(id=f"hl{i}", type="headline", n=i, hlseg=i - 1, segkey=g["key"], text=g["headline"], vis=hv, _txt=[g["headline"]])
    if mode == "bulletin":
        for si, g in enumerate(segs_in):
            add(id=f"b{si + 1}", type="bumper", seg=si, d=2.2, num=f"{si + 1:02d}", title=g["title"], tag=g.get("tag") or NUMS[si])
            if not (2 <= len(g["shots"]) <= 7):
                raise RundownError(f"segment {g['key']}: needs 2-7 shots")
            for j, s in enumerate(g["shots"]):
                s = copy.deepcopy(s)
                t = s.pop("type")
                if t not in SHOT_TYPES:
                    raise RundownError(f"unknown shot type {t}")
                sc = dict(id=f"s{si + 1}{chr(97 + j)}", type=t, seg=si, tab=s.pop("tab", None) or (NUMS[si] if j == 0 else TAB_DEFAULT[t]),
                          src=s.pop("src", None) or "المصدر: " + A.GENERIC)
                if "vis" in s:
                    s["vis"] = _vis(s["vis"], g, media)
                if t == "map":
                    mp = s.pop("map", None) or g.get("map")
                    if not mp:
                        raise RundownError(f"segment {g['key']}: map shot without a map")
                    s["map"] = safe_map(mp)
                if t == "grid" and "bgmap" not in s and g.get("map"):
                    s["bgmap"] = g["map"]
                sc.update(s)
                add(**sc, _txt=_shot_texts(dict(sc, type=t)), lead=LEAD.get(t, 0.9))
        if BR:                                      # no distinct verified short stories today -> no «باختصار» section
            rows = [{"t": r["t"], "s": r["s"]} for r in BR]
            add(id=f"b{len(segs_in) + 1}", type="bumper", seg=len(segs_in), d=2.0, num=f"{len(segs_in) + 1:02d}", title="باختصار", tag="جولة سريعة")
            times = [round(max(2.6, 1.2 + 0.35 * len(words(r["t"])) + 0.5), 2) for r in rows]
            add(id="brief", type="list", seg=len(segs_in), tab="باختصار", rows=rows, row_d=times, d=round(sum(times) + 0.8, 2),
                words=sum(len(words(r["t"])) for r in rows), small=max(len(re.sub(r"\[\[|\]\]", "", r["t"])) for r in rows) > 40,
                xsmall=max(len(re.sub(r"\[\[|\]\]", "", r["t"])) for r in rows) > 52)
        credits = ["خرائط: Natural Earth"]
        used = [m for m in media.values() if any(json.dumps(x, ensure_ascii=False).find(m["img"]) >= 0 for x in SC)]
        if used:
            credits.insert(0, "صور: " + " · ".join(dict.fromkeys(m.get("credit_short") or "Wikimedia Commons" for m in used)))
        add(id="end", type="end", d=5.0, l1=END_BULLETIN, credits="<br>".join(credits))
    else:
        add(id="end", type="end", d=3.4, l1="العالم في 30 ثانية", l2="قهوة نيوز", credits="خرائط: Natural Earth")
    t0 = 0.0
    for s in SC:
        s["t"] = round(t0, 3)
        t0 += s["d"]
    dur = round(t0, 2)
    spans = {}
    for s in SC:
        if "seg" in s:
            a = spans.setdefault(s["seg"], [s["t"], s["t"] + s["d"]])
            a[1] = s["t"] + s["d"]
    B = dict(mode=mode, duration=dur, scenes=SC, segs=SEGS, asof=f"آخر تحديث {spec.get('asof', '')} ت.غ".replace("  ", " "),
             date=date, segments=[dict(t=v[0], d=v[1] - v[0]) for k, v in sorted(spans.items())])
    check_headlines(B)
    check_outro(B)
    gate_screen(B)
    return B


# ------------------------------------------------------------------ gates
def check_headlines(B):
    """Permanent gate (pilot v4, owner-flagged after v3 skipped Yemen): every main segment has exactly one «أبرز العناوين»
    headline, in bulletin order, numbered 1..N, above its reading time, with a visual. Raises RundownError."""
    errs = []
    hls = [s for s in B["scenes"] if s["type"] == "headline"]
    main = [g["k"] for g in B["segs"] if g["k"] != "brief"]
    if B.get("mode", "bulletin") == "bulletin":
        order = []
        for s in B["scenes"]:
            if "seg" in s and B["segs"][s["seg"]]["k"] in main and B["segs"][s["seg"]]["k"] not in order:
                order.append(B["segs"][s["seg"]]["k"])
    else:
        order = main
    got = [B["segs"][h["hlseg"]]["k"] for h in hls]
    missing = [k for k in order if k not in got]
    extra = [k for k in got if k not in order]
    dup = sorted({k for k in got if got.count(k) > 1})
    if missing:
        errs.append(f"main segment(s) missing from headlines: {missing}")
    if extra:
        errs.append(f"headline(s) without a body segment: {extra}")
    if dup:
        errs.append(f"duplicate headline(s): {dup}")
    if not missing and not extra and got != order:
        errs.append(f"headline order {got} != bulletin order {order}")
    if order != main:
        errs.append(f"segments in body {order} != rundown {main}")
    if [h["n"] for h in hls] != list(range(1, len(hls) + 1)):
        errs.append("headline numbering not 1..N")
    for h in hls:
        if h["d"] < max(1.8, 1.2 + 0.35 * h["words"]):
            errs.append(f"headline {h['n']} below reading time")
        if not h.get("vis"):
            errs.append(f"headline {h['n']} has no visual")
    if errs:
        raise RundownError("HEADLINES CHECK FAILED: " + " | ".join(errs))
    return got


def check_outro(B):
    """Owner 2026-10-29: the closing card shows «انتهت نشرة اليوم» (+ credits) only: no «قهوة اليوم», no «غدًا», no
    two-source line (the rule stays internal)."""
    for s in B["scenes"]:
        if s["type"] == "end":
            txt = " ".join(str(s.get(k) or "") for k in ("l1", "l2", "l3", "credits"))
            hit = [w for w in END_FORBIDDEN if w in txt]
            if hit:
                raise RundownError(f"OUTRO CHECK FAILED: {hit}")
    return True


# ------------------------------------------------------------------ «باختصار» must be OTHER stories (owner 2026-10-29)
_AR_STOP = {"في", "من", "على", "إلى", "الى", "عن", "مع", "بعد", "قبل", "بين", "عند", "حول", "ضد", "أو", "ثم", "التي", "الذي",
            "بعد", "خلال", "منذ", "نحو", "إثر", "اثر", "وسط", "أن", "إن", "قد", "كما", "هذا", "هذه", "ذلك", "شخصا", "شخصاً"}


def _tok(t):
    """Story-content tokens of a line: normalised Arabic, light stemming (newsroom.stem), stop words and digits out."""
    from ..newsroom import norm, stem
    t = re.sub(r"\[\[|\]\]", "", t or "")
    return {stem(w) for w in norm(t).split() if len(w) > 2 and w not in _AR_STOP and not w.isdigit()}


def _km(p, q):
    dlat = math.radians(q["lat"] - p["lat"])
    dlon = math.radians(q["lon"] - p["lon"]) * math.cos(math.radians((p["lat"] + q["lat"]) / 2))
    return 6371.0 * math.hypot(dlat, dlon)


def brief_overlap(br, seg, km=60.0):
    """Why a «باختصار» item repeats a main segment's story, or None. Same cluster id; or the brief line shares the story
    (content-token overlap with the segment's title/headline/strap/own one-liner, >= 2 tokens and >= 40 % of the brief's);
    or same country with points within `km` plus a shared content token (same event, other wording)."""
    if br.get("cluster") and br.get("cluster") == seg.get("cluster"):
        return "same story cluster"
    bt = _tok(br.get("t"))
    seg_txt = [seg.get("title"), seg.get("headline"), seg.get("brief_t")] + \
              [s.get("text") for s in seg.get("shots", []) if s.get("type") in ("strap", "map")]
    st = set().union(*[_tok(x) for x in seg_txt if x]) if any(seg_txt) else set()
    inter = bt & st
    if len(inter) >= 2 and len(inter) / max(1, len(bt)) >= 0.4:
        return f"same wording as segment ({' '.join(sorted(inter))[:60]})"
    p, q = br.get("place"), (seg.get("map") or {}).get("point")
    if p and q and (p.get("iso3") or "") == ((seg.get("map") or {}).get("iso3") or "-") and _km(p, q) < km and inter:
        return f"same place ({int(_km(p, q))} km) and topic"
    return None


def check_brief(segs, BR):
    """Gate: 2-3 items, short enough for two lines, all different from each other and from every main segment."""
    errs = []
    if not (MIN_BRIEF <= len(BR) <= MAX_BRIEF):
        errs.append(f"«باختصار» needs {MIN_BRIEF}-{MAX_BRIEF} items or none, got {len(BR)}")
    for i, r in enumerate(BR):
        if len(re.sub(r"\[\[|\]\]", "", r.get("t") or "")) > BRIEF_MAX_CHARS:
            errs.append(f"brief {i + 1} longer than {BRIEF_MAX_CHARS} chars")
        for g in segs:
            why = brief_overlap(r, g)
            if why:
                errs.append(f"brief {i + 1} repeats segment {g['key']}: {why}")
        for j in range(i):
            o = BR[j]
            if (r.get("cluster") and r.get("cluster") == o.get("cluster")) or brief_overlap(r, {"title": o["t"], "map": (
                    {"iso3": o["place"]["iso3"], "point": o["place"]} if o.get("place") else None)}):
                errs.append(f"briefs {j + 1} and {i + 1} are the same story")
    if errs:
        raise RundownError("BRIEF CHECK FAILED: " + " | ".join(errs))
    return True


SKIP_KEYS = ("img", "cluster", "type", "id", "iso3", "kind", "pos", "segkey", "k", "credits")


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k not in SKIP_KEYS:
                yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def gate_screen(B):
    """Every on-screen string: banned names (Arabic stems incl. prefixed forms + Latin), competing outlets, graphic wording."""
    from ..newsroom import banned_country
    bad = []
    for s in strings(B):
        plain = s.replace("[[", "").replace("]]", "")
        h = banned_hits(plain) or ([banned_country(plain)] if banned_country(plain) else []) or LATIN_BANNED.findall(plain)
        if h:
            bad.append(("banned", plain[:60], h[:3]))
        o = A.outlet_hits(plain)
        if o:
            bad.append(("outlet", plain[:60], o[:3]))
        g = safety.graphic_hits(plain)
        if g:
            bad.append(("graphic", plain[:60], g[:3]))
    for s in B["scenes"]:
        for mp in [s.get("map"), (s.get("vis") or {}).get("map"), s.get("bgmap")]:
            if mp:
                for k in ("s0", "s1"):
                    if k in mp and in_no_focus(mp[k]["lon"], mp[k]["lat"]):
                        bad.append(("map-centre", s["id"], [mp[k]["lon"], mp[k]["lat"]]))
                if mp.get("iso3") in geo.NO_FOCUS_ISO:
                    bad.append(("map-focus", s["id"], mp["iso3"]))
    if bad:
        raise RundownError(f"SCREEN GATE FAILED: {bad[:6]}")
    return True
