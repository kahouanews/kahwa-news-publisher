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


def test_seg_from_accepts_slightly_long_title_after_fitting():
    from kahwa_publisher.bulletin import writer as W
    d = {"title": "البنك المركزي الأوروبي يثبّت", "tag": "اقتصاد", "headline": "المركزي الأوروبي [[يثبت الفائدة]]",
         "strap": "أبقى البنك المركزي الأوروبي الفائدة دون تغيير", "brief": "تثبيت الفائدة الأوروبية",
         "number": {"num": "2.5%", "label": "سعر الفائدة", "deck": "دون تغيير"}, "sources": []}
    seg, _, _ = W.seg_from(d, "s1", "سعر الفائدة 2.5%")
    assert W.plen(seg["title"]) <= W.LIM["title"]


def test_map_label_pill_stays_inside_safe_zone_cairo():
    """Run 40957631882: Egypt story placed at Cairo, label «مصر» drawn 96 px above a point at y=332 -> above the safe zone (269)."""
    m = RD.map_from_place({"iso3": "EGY", "lon": 31.236, "lat": 30.044, "label": "مصر"})
    for k in ("s0", "s1"):
        p = RD.geo._ortho_forward(31.236, 30.044, m[k]["lon"], m[k]["lat"], m[k]["scale"], 540, m[k].get("cy", 560))
        assert p and p[1] - 96 >= 269 + 10, (k, p)
