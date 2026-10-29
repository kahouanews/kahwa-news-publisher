"""«قهوة اليوم» newscast: headline gate, screen gate (banned names / outlets), map camera rules (never centre on a banned shape,
no «الإمارات» on the Yemen maps), safe zones (opener + sting hand-off tab out of the Meta action rail), writer fail-closed."""
import copy
import json
import re
from pathlib import Path

import pytest

from kahwa_publisher.bulletin import rundown as RD
from kahwa_publisher.bulletin import writer as W

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "bulletin-2026-10-29.json"


def spec():
    return json.loads(FIX.read_text())


def test_fixture_builds_with_all_segments_in_headlines():
    B = RD.build(spec())
    hls = [s for s in B["scenes"] if s["type"] == "headline"]
    assert [B["segs"][h["hlseg"]]["k"] for h in hls] == ["spain", "gulf", "eco", "yemen", "clim"]
    assert B["scenes"][0]["type"] == "hltitle"               # body frame 0 = «أبرز العناوين» card, never a story
    assert [s["type"] for s in B["scenes"]][-2:] == ["list", "end"]
    assert [s["id"] for s in B["scenes"] if s["type"] == "bumper"] == ["b1", "b2", "b3", "b4", "b5", "b6"]
    assert not any(s["type"] == "verify" or "ميزان" in json.dumps(s, ensure_ascii=False) for s in B["scenes"])


def _break(B, how):
    B = copy.deepcopy(B)
    hls = [s for s in B["scenes"] if s["type"] == "headline"]
    if how == "drop_yemen":
        B["scenes"] = [s for s in B["scenes"] if not (s["type"] == "headline" and s["segkey"] == "yemen")]
        for i, h in enumerate([s for s in B["scenes"] if s["type"] == "headline"], 1):
            h["n"] = i
    elif how == "swap":
        hls[0]["hlseg"], hls[1]["hlseg"] = hls[1]["hlseg"], hls[0]["hlseg"]
    elif how == "dup":
        hls[1]["hlseg"] = hls[0]["hlseg"]
    elif how == "short":
        hls[2]["d"] = 1.0
    elif how == "novis":
        hls[3]["vis"] = None
    return B


@pytest.mark.parametrize("how,msg", [("drop_yemen", "missing from headlines"), ("swap", "order"), ("dup", "duplicate"),
                                     ("short", "reading time"), ("novis", "no visual")])
def test_headline_gate_fails(how, msg):
    B = RD.build(spec())
    with pytest.raises(RD.RundownError, match=msg):
        RD.check_headlines(_break(B, how))


def test_segment_count_limits():
    s = spec()
    s["segments"] = s["segments"][:2]
    with pytest.raises(RD.RundownError, match="3-5 main segments"):
        RD.build(s)


@pytest.mark.parametrize("text", ["المغرب يعلن [[خطة]]", "وفد إماراتي يصل", "بالجزائر أمس", "قصف إسرائيلي", "رئيس وزراء Israel",
                                  "وفق الجزيرة نت"])
def test_screen_gate_rejects_banned_and_outlets(text):
    s = spec()
    s["segments"][1]["headline"] = text
    with pytest.raises(RD.RundownError, match="SCREEN GATE"):
        RD.build(s)


def test_screen_gate_brief_and_quote():
    s = spec()
    s["brief"][0]["t"] = "قمة في دبي"
    with pytest.raises(RD.RundownError, match="SCREEN GATE"):
        RD.build(s)
    s = spec()
    s["segments"][0]["shots"][4]["role"] = "وزير خارجية الإمارات"
    with pytest.raises(RD.RundownError, match="SCREEN GATE"):
        RD.build(s)


def test_map_never_centres_on_banned_shape():
    st = {"lon": -6.8, "lat": 33.0, "scale": 3000, "cy": 560}     # Rabat area
    with pytest.raises(RD.RundownError):
        RD.tighten(st)
    moved, _ = RD.tighten(st, point={"lon": -3.7, "lat": 40.4})      # re-centred on the story point (Madrid)
    assert not RD.in_no_focus(moved["lon"], moved["lat"])
    s = spec()
    s["segments"][3]["map"] = dict(s["segments"][3]["map"], iso3="ARE")
    with pytest.raises(RD.RundownError, match="map-focus"):
        RD.build(s)


def test_yemen_maps_show_no_emirates():
    B = RD.build(spec())
    yem = []
    for s in B["scenes"]:
        for mp in (s.get("map"), (s.get("vis") or {}).get("map")):
            if mp and mp.get("iso3") == "YEM":
                yem.append((s["id"], mp))
    assert len(yem) >= 3
    for sid, mp in yem:
        for k in ("s0", "s1"):
            assert "ARE" not in RD.visible_no_focus(mp[k]), (sid, k, mp[k])
            assert RD.point_on_screen(mp[k], mp["point"]["lon"], mp["point"]["lat"], box=(0, 0, 1080, 1920))


def _css_top(path, sel):
    css = Path(path).read_text()
    m = re.search(re.escape(sel) + r"\s*\{[^}]*top:\s*(\d+)px[^}]*height:\s*(\d+)px", css)
    return int(m.group(1)), int(m.group(2))


@pytest.mark.parametrize("css", ["kahwa_publisher/bulletin/opener/templates/opener.css", "kahwa_publisher/stings/templates/opener.css"])
def test_handoff_tab_out_of_action_rail(css):
    top, h = _css_top(ROOT / css, ".handoff")
    assert (top, h) == (1010, 64)
    assert top + h <= RD.RAIL["y"]                               # full-width tab: must end above the rail zone (y > 1152)
    assert RD.SAFE["t"] <= top and top + h <= RD.SAFE["b"]


def test_qa_safe_zone_constants_match_template():
    js = (ROOT / "kahwa_publisher/bulletin/qa.mjs").read_text()
    assert "const S={l:%d,r:%d,t:%d,b:%d}" % (RD.SAFE["l"], RD.SAFE["r"], RD.SAFE["t"], RD.SAFE["b"]) in js
    assert "r.bottom>%d&&r.right>%d" % (RD.RAIL["y"], RD.RAIL["x"]) in js
    tpl = (ROOT / "kahwa_publisher/bulletin/templates/body.tpl.js").read_text()
    assert "HL_SEG" not in tpl and "s.hlseg" in tpl and "SPAIN_BG" not in tpl and "115.32" not in tpl


def test_edition_rundown_is_short_and_has_headlines_only():
    s = spec()
    s["segments"] = [dict(g, shots=[]) for g in s["segments"][:3]]
    B = RD.build(s, mode="edition")
    assert [x["type"] for x in B["scenes"]] == ["headline", "headline", "headline", "end"]
    assert 3.56 + B["duration"] <= 60


# ------------------------------------------------------------------ writer (code gates, no network)
SRC = "- [م1] قالت وزارة النقل الإسبانية إن 69 شخصا قتلوا في اصطدام قطارين قرب طليطلة صباح الأربعاء — ..."


def good():
    return {"title": "قطارات إسبانيا", "tag": "حوادث", "headline": "إسبانيا: [[69 قتيلًا]] في اصطدام قطارين",
            "strap": "اصطدام قطارين قرب [[طليطلة]] صباح الأربعاء", "map_line": None,
            "place": {"iso3": "ESP", "lon": -4.02, "lat": 39.86, "label": "طليطلة"},
            "number": {"num": "69", "label": "قتيلًا وفق وزارة النقل", "deck": "اصطدام قطارين [[سريعين]]"},
            "quote": None, "next": None, "brief": "[[69 قتيلًا]] في حادث قطارين بإسبانيا", "sources": ["وزارة النقل الإسبانية"],
            "urgent": False, "safe": True}


def test_writer_segment_ok():
    seg, brief, src0 = W.seg_from(good(), "s1", SRC)
    assert seg["map"]["iso3"] == "ESP" and [s["type"] for s in seg["shots"]] == ["strap", "number"]
    assert seg["shots"][0]["src"] == "المصدر: وزارة النقل الإسبانية"
    assert W.screen_problems(W.seg_lines(seg, brief), SRC) == []


def test_writer_gates():
    d = good()
    d["headline"] = "إسبانيا: [[70 قتيلًا]] في اصطدام قطارين"
    seg, b, _ = W.seg_from(d, "s1", SRC)
    assert any("number 70" in p for p in W.screen_problems(W.seg_lines(seg, b), SRC))
    assert W.screen_problems(["رد من الرباط"], SRC) and W.screen_problems(["بحسب سكاي نيوز عربية"], SRC)
    d = good()
    d["headline"] = "ا" * 60
    with pytest.raises(ValueError, match="too long"):
        W.seg_from(d, "s1", SRC)
    d = good()
    d["place"] = {"iso3": "MAR", "lon": -6.8, "lat": 34.0, "label": "x"}
    seg, _, _ = W.seg_from(d, "s1", SRC)
    assert "map" not in seg                                       # banned location: no map
    d = good()
    d["safe"] = False
    with pytest.raises(ValueError):
        W.seg_from(d, "s1", SRC)
    d = good()
    d["sources"] = ["الجزيرة"]
    seg, _, _ = W.seg_from(d, "s1", SRC)
    assert seg["shots"][0]["src"] == "المصدر: وكالات"             # a competing outlet is never a source tag


def test_writer_fail_closed_without_checker(monkeypatch):
    cl = [{"key": f"k{i}", "sources": ["BBC عربي", "فرانس 24"], "items": [{"title": f"خبر {i}", "summary": SRC, "link": ""}]} for i in range(6)]
    monkeypatch.setattr(W, "select_clusters", lambda n=9: cl)

    class Fake:
        families = set()

        def __init__(self, fn):
            self.fn = fn

        def __call__(self, prompt, temperature=None, **k):
            return self.fn(prompt)

    def bound(task, **k):
        if task == "writer":
            return Fake(lambda p: json.dumps(good(), ensure_ascii=False))
        return Fake(lambda p: "not json")                         # broken fact-checker -> every story must be dropped
    monkeypatch.setattr(W.R, "bound", bound)
    with pytest.raises(RD.RundownError, match="only 0 verified"):
        W.build_spec("2026-10-29", "06:30", fetch_articles=False)


def test_prune_drops_only_optional_unsupported_lines():
    d = good()
    d["next"] = {"title": "ماذا [[بعد؟]]", "rows": ["القضاء يفتح تحقيقاً في الحادث", "تعليق حركة القطارات السريعة", "البرلمان يستدعي وزير النقل"]}
    seg, brief, _ = W.seg_from(d, "s1", SRC)
    pr = W.prune(seg, brief, [{"claim": "البرلمان استدعى وزير النقل", "span": "البرلمان يستدعي وزير النقل"}])
    rows = [r["t"] for s in pr["shots"] if s["type"] == "next" for r in s["rows"]]
    assert "البرلمان يستدعي وزير النقل" not in rows and len(rows) == 2
    assert W.prune(seg, brief, [{"claim": "x", "span": "اصطدام قطارين قرب طليطلة صباح الأربعاء"}]) is None   # required strap


def test_generated_map_zoom_uses_the_part_holding_the_point():
    # France's feature includes French Guiana: zooming on the whole feature gave a hemisphere view (hundreds of labels)
    m = RD.map_from_place({"iso3": "FRA", "lon": 2.03, "lat": 48.77, "label": "تراب"})
    assert m["s0"]["scale"] >= 1500 and m["s1"]["scale"] >= m["s0"]["scale"]
    assert RD.point_on_screen(m["s1"], 2.03, 48.77)
    y = RD.map_from_place({"iso3": "YEM", "lon": 44.02, "lat": 13.58, "label": "تعز"})
    assert not RD.visible_no_focus(y["s0"]) and not RD.visible_no_focus(y["s1"])
    assert RD.map_from_place({"iso3": "MAR", "lon": -6.8, "lat": 34.0, "label": "x"}) is None


def test_spain_map_keeps_western_sahara_out_of_frame():
    m = RD.map_from_place({"iso3": "ESP", "lon": -3.72, "lat": 39.41, "label": "أوردا"})
    for k in ("s0", "s1"):
        assert not RD.visible_no_focus(m[k], isos=("ESH",))


def test_fit_title_trims_small_overruns_at_word_boundaries():
    from kahwa_publisher.bulletin.writer import fit_title, plen
    assert fit_title("قرار البنك المركزي", 22) == "قرار البنك المركزي"
    t = fit_title("البنك المركزي الأوروبي يثبت", 22)            # 27 chars -> word-boundary trim
    assert t and plen(t) <= 22 and "البنك المركزي الأوروبي يثبت".startswith(t) and not t.endswith(" ")
    assert fit_title("هجوم روسي على محطة قطار في", 22) == "هجوم روسي على محطة"   # no dangling particle
    assert fit_title("كلمة " * 12, 22) is None                                   # big overrun: still rejected
    t = fit_title("[[عاصفة]] سياسية في حكومة مدريد", 22)                      # markers dropped, fitted at a word
    assert t and plen(t) <= 22 and "[[" not in t and not t.endswith("في")


def test_seg_from_keeps_titles_whole_up_to_the_hard_cap():
    """Titles are never trimmed (a cut broke «الولايات المتحدة»); <= 26 accepted as written, longer rejected for a rewrite."""
    from kahwa_publisher.bulletin import writer as W
    base = {"tag": "اقتصاد", "headline": "المركزي الأوروبي [[يثبت الفائدة]]", "strap": "أبقى البنك المركزي الأوروبي الفائدة دون تغيير",
            "brief": "تثبيت الفائدة الأوروبية", "number": {"num": "2.5%", "label": "سعر الفائدة", "deck": "دون تغيير"}, "sources": []}
    t = "إيران والولايات المتحدة"
    seg, _, _ = W.seg_from(dict(base, title=t), "s1", "سعر الفائدة 2.5%")
    assert seg["title"] == t
    with pytest.raises(ValueError, match="title too long"):
        W.seg_from(dict(base, title="البنك المركزي الأوروبي يثبت الفائدة"), "s1", "سعر الفائدة 2.5%")


def test_rail_titles_are_plain_text():
    s = json.loads((ROOT / "tests/fixtures/bulletin-2026-10-29.json").read_text())
    s["segments"][0]["title"] = "ضبط [[تماسيح]] بقطار"
    B = RD.build(s)
    assert B["segs"][0]["t"] == "ضبط تماسيح بقطار"


def test_map_label_pill_stays_inside_safe_zone_cairo():
    """Run 40957631882: Egypt story placed at Cairo, label «مصر» drawn 96 px above a point at y=332 -> above the safe zone (269)."""
    m = RD.map_from_place({"iso3": "EGY", "lon": 31.236, "lat": 30.044, "label": "مصر"})
    for k in ("s0", "s1"):
        p = RD.geo._ortho_forward(31.236, 30.044, m[k]["lon"], m[k]["lat"], m[k]["scale"], 540, m[k].get("cy", 560))
        assert p and p[1] - 96 >= 269 + 10, (k, p)


def test_map_label_pill_clears_safe_sides_los_angeles():
    """Run 40963351803: «لوس أنجلس» pill cut at the left edge (USA zoom from the mainland centre)."""
    lab = "لوس أنجلس"
    m = RD.map_from_place({"iso3": "USA", "lon": -118.244, "lat": 34.052, "label": lab})
    half = 40 + 13 * len(lab)
    for k in ("s0", "s1"):
        p = RD.geo._ortho_forward(-118.244, 34.052, m[k]["lon"], m[k]["lat"], m[k]["scale"], 540, m[k].get("cy", 560))
        assert p and 65 <= p[0] - half and p[0] + half <= 1015 and p[1] - 96 >= 279, (k, p)


def test_duplicate_segments_are_detected():
    a = {"title": "هجوم في دنيبرو", "map": {"iso3": "UKR", "point": {"lon": 35.046, "lat": 48.465}}}
    b = {"title": "ضربة على محطة قطار", "map": {"iso3": "UKR", "point": {"lon": 35.0, "lat": 48.5}}}
    c = {"title": "هجوم في [[دنيبرو]]"}
    d = {"title": "ضربة في كييف", "map": {"iso3": "UKR", "point": {"lon": 30.52, "lat": 50.45}}}
    assert W._dup_segment(b, [a]) and W._dup_segment(c, [a]) and not W._dup_segment(d, [a])


# ------------------------------------------------------------------ owner 2026-10-29: «باختصار» = other stories, self-contained lines
def test_brief_gate_rejects_a_segment_story_repeated_in_brief():
    s = spec()
    seg = s["segments"][0]                                        # spain: 69 dead, train crash near Toledo
    s["brief"][0] = {"t": "[[69 قتيلًا]] في اصطدام قطارين قرب طليطلة", "s": "وزارة النقل الإسبانية"}
    with pytest.raises(RD.RundownError, match="BRIEF CHECK FAILED: brief 1 repeats segment spain"):
        RD.build(s)
    s = spec()                                                    # same cluster id, different wording
    s["segments"][1]["cluster"] = "abc123"
    s["brief"][1] = dict(s["brief"][1], cluster="abc123")
    with pytest.raises(RD.RundownError, match="brief 2 repeats segment gulf: same story cluster"):
        RD.build(s)
    s = spec()                                                    # same place + shared topic word
    s["brief"][2] = {"t": "تعليق [[القطارات السريعة]] حتى إشعار آخر", "s": "x",
                     "place": {"iso3": "ESP", "lon": -3.75, "lat": 39.40, "label": "أوردا"}}
    with pytest.raises(RD.RundownError, match="brief 3 repeats segment spain"):
        RD.build(s)


def test_brief_gate_from_run_40953097998_spec():
    """The real failing rundown: all three «باختصار» items were the segments' own one-liners (Trappes, Dnipro, crocodiles)."""
    s = spec()
    s["segments"][0] = dict(s["segments"][0], headline="اعترافات [[المشتبه به]] في هجوم تراب العنصري", title="هجوم تراب العنصري")
    s["brief"][0] = {"t": "المشتبه به أقر بالتخطيط المسبق للهجوم", "s": "النيابة الوطنية لمكافحة الإرهاب"}
    with pytest.raises(RD.RundownError, match="brief 1 repeats segment spain"):
        RD.build(s)


def test_distinct_briefs_pass_and_duplicates_between_briefs_fail():
    B = RD.build(spec())
    assert [r["t"] for r in next(x for x in B["scenes"] if x["type"] == "list")["rows"]] == [r["t"] for r in spec()["brief"]]
    s = spec()
    s["brief"][1] = dict(s["brief"][0])
    with pytest.raises(RD.RundownError, match="same story"):
        RD.build(s)


def test_too_few_briefs_and_section_dropped_gracefully():
    s = spec()
    s["brief"] = s["brief"][:1]
    with pytest.raises(RD.RundownError, match="2-3 items or none"):
        RD.build(s)
    s["brief"] = []
    B = RD.build(s)                                               # no «باختصار»: no bumper, no list, headline gate still holds
    assert "brief" not in [g["k"] for g in B["segs"]]
    assert not any(x["type"] == "list" for x in B["scenes"])
    assert [x["id"] for x in B["scenes"] if x["type"] == "bumper"] == ["b1", "b2", "b3", "b4", "b5"]
    assert RD.check_headlines(B) == ["spain", "gulf", "eco", "yemen", "clim"]


def test_long_brief_two_lines_switches_list_to_small_size():
    s = spec()
    s["brief"][0]["t"] = "كوريا الشمالية تطلق نحو [[10 صواريخ]] باليستية باتجاه البحر الشرقي"
    B = RD.build(s)
    assert next(x for x in B["scenes"] if x["type"] == "list")["small"] is True
    s["brief"][0]["t"] = "ب" * (RD.BRIEF_MAX_CHARS + 1)
    with pytest.raises(RD.RundownError, match="longer than"):
        RD.build(s)
    tpl = (ROOT / "kahwa_publisher/bulletin/templates/body.css").read_text()
    assert ".lrows.lsm .ltx" in tpl


FRA = {"iso3": "FRA", "lon": 2.01, "lat": 48.77, "label": "تراب"}


@pytest.mark.parametrize("line,ok", [
    ("المشتبه به أقر بالتخطيط المسبق للهجوم", False),              # owner screenshot 2:17
    ("الهجوم أوقع إصابات واعتقال شخص", False),
    ("الحادث: توقيف شخصين بعد إطلاق نار", False),
    ("منفذ هجوم تراب في فرنسا يقر بالتخطيط المسبق", True),
    ("المشتبه به في هجوم [[تراب]] يقر بالتخطيط المسبق", True),       # vague opener, qualified at once
    ("القضاء الفرنسي: منفذ الهجوم يقر بالتخطيط المسبق", True),
])
def test_self_contained_gate(line, ok):
    probs = W.self_contained_problems([("brief", line)], W.anchors_for(FRA))
    assert (probs == []) is ok, probs


def test_vague_opener_rejected_even_with_anchor_late_in_line_and_without_anchors():
    an = W.anchors_for(FRA)
    assert W.vague_opener("المشتبه به أقر أمام القضاء بالتخطيط المسبق لما جرى في تراب", an) == "المشتبه به"
    assert W.vague_opener("والهجوم في تراب يثير جدلاً", an) is None
    assert W.vague_opener("[[الهجوم]] على محطة قطار", set()) == "الهجوم"
    assert any("no named place or actor" in p for p in W.self_contained_problems([("brief", "إغلاق طريق رئيسي")], set()))
    assert any("opens with" in p for p in W.self_contained_problems([("title", "الحادث الأخير")], an))


def test_anchors_use_country_name_and_sourced_actor_only():
    an = W.anchors_for({"iso3": "ESP", "lon": -4.0, "lat": 39.9, "label": "طليطلة"})
    assert {"طليطل", "اسبان"} <= an
    assert W._has_anchor("[[69 قتيلًا]] في حادث قطارين بإسبانيا", an)
    assert W.anchors_for(None, "زيلينسكي", src_text="دعا زيلينسكي أوروبا") == {"زيلينسكي"}
    assert W.anchors_for(None, "ماكرون", src_text="دعا زيلينسكي أوروبا") == set()   # actor not in the sources: ignored


class _Fake:
    families = set()

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, prompt, temperature=None, **k):
        return self.fn(prompt)


def test_standalone_checker_fail_closed_and_verdicts():
    items = [("brief", "المشتبه به أقر بالتخطيط", ""), ("headline", "منفذ هجوم تراب في فرنسا يقر", "")]
    bad = W.standalone_check(_Fake(lambda p: '{"results": [{"i": 1, "ok": false, "why": "من المشتبه؟"}, {"i": 2, "ok": true}]}'), items)
    assert [(b[0], b[1]) for b in bad] == [(0, "brief")]
    assert len(W.standalone_check(_Fake(lambda p: '{"results": [{"i": 2, "ok": true}]}'), items)) == 1     # missing verdict = bad
    with pytest.raises(ValueError):
        W.standalone_check(_Fake(lambda p: "not json"), items)


def _cl(key, title, toks):
    return {"key": key, "sources": ["BBC عربي", "فرانس 24"], "tok": set(toks), "items": [{"title": title, "summary": SRC, "link": ""}]}


def test_build_spec_briefs_are_other_stories_never_segment_fallback(monkeypatch):
    """Segments s1-s3 (Spain, Ukraine, Egypt); candidates: a second cluster of the Spain story (token-similar), two other
    stories. «باختصار» = the two other stories only; with < 2 others the section is dropped (no fallback to segment one-liners)."""
    seg_json = {
        "c1": dict(good()),
        "c2": dict(good(), title="هجوم في دنيبرو", headline="أوكرانيا: [[هجوم روسي]] على محطة قطار في دنيبرو",
                   strap="[[هجوم روسي]] على محطة قطار في دنيبرو", brief="هجوم روسي على محطة قطار في [[دنيبرو]] الأوكرانية",
                   place={"iso3": "UKR", "lon": 35.05, "lat": 48.46, "label": "دنيبرو"}, number=None,
                   quote={"text": "نطلب من أوروبا التحرك", "who": "زيلينسكي", "role": "الرئيس الأوكراني"}),
        "c3": dict(good(), title="تماسيح مصر", headline="مصر: ضبط [[تماسيح]] صغيرة في قطار بأسيوط", strap="ضبط [[تماسيح]] صغيرة في قطار بأسيوط",
                   brief="ضبط تماسيح صغيرة في قطار بمصر", place={"iso3": "EGY", "lon": 31.18, "lat": 27.18, "label": "أسيوط"}, number=None,
                   quote={"text": "ضبطنا التماسيح", "who": "وزارة الداخلية", "role": "مصر"}),
    }
    briefs = {"c4": {"brief": "اليابان: [[زلزال]] بقوة 6 درجات قبالة هوكايدو", "headline": "زلزال في اليابان", "source": "",
                     "place": {"iso3": "JPN", "lon": 142.5, "lat": 43.0, "label": "هوكايدو"}},
              "c5": {"brief": "البرازيل تعلن [[خطة]] لحماية غابات الأمازون", "headline": "خطة الأمازون", "source": "",
                     "place": {"iso3": "BRA", "lon": -60.0, "lat": -3.0, "label": "الأمازون"}},
              "c1b": {"brief": "إسبانيا: [[69 قتيلًا]] في اصطدام قطارين قرب طليطلة", "headline": "x", "source": "",
                      "place": {"iso3": "ESP", "lon": -4.02, "lat": 39.86, "label": "طليطلة"}}}
    cl = [_cl("c1", "اصطدام قطارين في إسبانيا", ["اصطدام", "قطار", "اسبان", "طليطل", "قتل"]),
          _cl("c2", "هجوم روسي على دنيبرو", ["هجوم", "روسي", "دنيبرو", "محط"]),
          _cl("c3", "تماسيح في قطار بمصر", ["تمساح", "قطار", "مصر", "اسيوط"]),
          _cl("c1b", "قطارات إسبانيا: حصيلة", ["اصطدام", "قطار", "اسبان", "طليطل", "حصيل"]),
          _cl("c4", "زلزال اليابان", ["زلزال", "يابان", "هوكايدو"]),
          _cl("c5", "غابات الأمازون", ["برازيل", "امازون", "غاب"])]
    monkeypatch.setattr(W, "select_clusters", lambda n=9: cl)
    monkeypatch.setattr(W, "source_block", lambda items, fetch_articles=True: items[0]["title"] + " " + SRC +
                        " دنيبرو أوكرانيا زيلينسكي تماسيح أسيوط مصر وزارة الداخلية هوكايدو اليابان زلزال 6 البرازيل الأمازون")
    monkeypatch.setattr(W, "verify_lines", lambda ch, lines, src: ([{"claim": "x"}], []))
    monkeypatch.setattr(W, "standalone_check", lambda ch, items: [])
    monkeypatch.setattr(W, "screen_problems", lambda lines, src: [])

    def writer(p):
        title = p.split("المصادر:")[-1].strip()
        key = next(c["key"] for c in cl if title.startswith(c["items"][0]["title"]))
        d = briefs.get(key) if p.startswith(W.BRIEF_PROMPT[:40]) else seg_json.get(key)
        return json.dumps(d or {"safe": False}, ensure_ascii=False)
    monkeypatch.setattr(W.R, "bound", lambda task, **k: _Fake(writer))
    sp, rep = W.build_spec("2026-10-29", "06:30", n_main=3, fetch_articles=False)
    assert [g["cluster"] for g in sp["segments"]] == ["c1", "c2", "c3"]
    assert [b["cluster"] for b in sp["brief"]] == ["c4", "c5"]
    B = RD.build(sp)
    assert len(next(x for x in B["scenes"] if x["type"] == "list")["rows"]) == 2
    cl[:] = [c for c in cl if c["key"] != "c5"]                    # only one other story -> no «باختصار», no fallback
    sp, rep = W.build_spec("2026-10-29", "06:30", n_main=3, fetch_articles=False)
    assert sp["brief"] == []
    assert "brief" not in [g["k"] for g in RD.build(sp)["segs"]]


def test_writer_rejects_vague_brief_and_rewrites_once(monkeypatch):
    calls = []

    def writer(p):
        calls.append(p)
        d = good()
        if len(calls) == 1:
            d["headline"] = "الحادث أوقع [[69 قتيلًا]]"               # vague -> rejected, rewrite requested with the reason
        return json.dumps(d, ensure_ascii=False)
    monkeypatch.setattr(W, "verify_lines", lambda ch, lines, src: ([{"claim": "x"}], []))
    monkeypatch.setattr(W, "standalone_check", lambda ch, items: [])
    monkeypatch.setattr(W, "source_block", lambda items, fetch_articles=True: SRC)
    c = _cl("k", "اصطدام قطارين", [])
    seg, br, meta = W.write_segment(_Fake(writer), _Fake(lambda p: ""), c, "s1", fetch_articles=False)
    assert seg and len(calls) == 2 and "الحادث" in calls[1].split("ملاحظة من المدقق")[-1]
    assert seg["headline"] == good()["headline"]


def test_brief_only_vague_line_rewritten_once_then_dropped(monkeypatch):
    monkeypatch.setattr(W, "verify_lines", lambda ch, lines, src: ([{"claim": "x"}], []))
    monkeypatch.setattr(W, "standalone_check", lambda ch, items: [])
    monkeypatch.setattr(W, "source_block", lambda items, fetch_articles=True: SRC)
    vague = {"brief": "المشتبه به أقر بالتخطيط المسبق للهجوم", "headline": "اعترافات المشتبه به", "source": "", "place": None}
    _, br, meta = W.write_segment(_Fake(lambda p: json.dumps(vague, ensure_ascii=False)), _Fake(lambda p: ""), _cl("k", "x", []), "b",
                                  fetch_articles=False, brief_only=True)
    assert br is None and ("self-contained" in meta["error"] or "opens with" in meta["error"])
    long_ = dict(vague, brief="إسبانيا " + "ب" * 70)
    _, br, meta = W.write_segment(_Fake(lambda p: json.dumps(long_, ensure_ascii=False)), _Fake(lambda p: ""), _cl("k", "x", []), "b",
                                  fetch_articles=False, brief_only=True)
    assert br is None and "too long" in meta["error"]


def test_segment_one_liner_is_off_screen_and_never_drops_a_segment():
    d = dict(good(), brief="ب" * 95)                              # run 40969233856: Trappes dropped for «brief too long (77 > 64)»
    seg, b, _ = W.seg_from(d, "s1", SRC)
    assert seg and b not in W.seg_lines(seg, b)


def test_body_audio_mix_without_brief_section(monkeypatch, tmp_path):
    """run 40969233856 crashed with KeyError 'b4' when «باختصار» was dropped (no bumper after the last segment)."""
    from kahwa_publisher.bulletin import render as RN
    s = spec()
    s["brief"] = []
    B = RD.build(s)
    got = {}
    monkeypatch.setattr(RN, "_run_mix", lambda inputs, chains, out, dur: got.update(n=len(inputs)))
    monkeypatch.setattr(RN, "sh", lambda *a, **k: None)
    RN.mix_body(B, tmp_path / "body.wav")
    assert got["n"] > 5


def test_writer_drops_story_when_checker_says_unclear_twice(monkeypatch):
    monkeypatch.setattr(W, "verify_lines", lambda ch, lines, src: ([{"claim": "x"}], []))
    monkeypatch.setattr(W, "source_block", lambda items, fetch_articles=True: SRC)
    chk = _Fake(lambda p: '{"results": [' + ",".join('{"i": %d, "ok": %s}' % (i, "false" if i == 3 else "true") for i in range(1, 9)) + "]}")
    seg, br, meta = W.write_segment(_Fake(lambda p: json.dumps(good(), ensure_ascii=False)), chk, _cl("k", "x", []), "s1", fetch_articles=False)
    assert seg is None and "not understandable" in meta["error"]


def test_outro_card_has_no_brand_tomorrow_or_two_source_line():
    for mode, s in (("bulletin", spec()), ("edition", dict(spec(), segments=[dict(g, shots=[]) for g in spec()["segments"][:3]]))):
        B = RD.build(s, mode=mode)
        end = next(x for x in B["scenes"] if x["type"] == "end")
        txt = json.dumps(end, ensure_ascii=False)
        for w in ("قهوة اليوم", "غدًا", "مصدرين"):
            assert w not in txt, (mode, w)
    assert end and RD.build(spec())["scenes"][-1]["l1"] == "انتهت نشرة اليوم"
    bad = RD.build(spec())
    bad["scenes"][-1]["l2"] = "النشرة القادمة غدًا"
    with pytest.raises(RD.RundownError, match="OUTRO"):
        RD.check_outro(bad)
    tpl = (ROOT / "kahwa_publisher/bulletin/templates/body.tpl.js").read_text()
    assert "مصدرين" not in tpl and "القادمة غد" not in tpl and "انتهت نشرة قهوة" not in tpl
