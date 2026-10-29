"""Attribution policy (2026-10-29): primary sources only, competing outlets never named (hard gate in code)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kahwa_publisher import attribution as A  # noqa: E402

OUTLET_MENTIONS = [
    # Arabic, with prefixed forms و / ب / ل / ال / لل
    "قناة الجزيرة", "للجزيرة نت", "والجزيرة مباشر", "وفق الجزيرة", "بحسب العربية", "قالت قناة العربية", "العربية نت",
    "سكاي نيوز عربية", "وسكاي نيوز", "بسكاي نيوز", "لسكاي نيوز", "بي بي سي", "لبي بي سي", "والبي بي سي", "للبي بي سي",
    "فرانس 24", "وفرانس 24", "فرانس ٢٤", "سي إن إن", "لسي إن إن", "يورونيوز", "ليورونيوز", "روسيا اليوم", "آر تي",
    "سي جي تي إن", "تي آر تي", "دويتشه فيله", "دي دبليو", "إندبندنت عربية", "والإندبندنت", "الشرق للأخبار",
    "قناة الحدث", "العربي الجديد", "الميادين", "بلومبرغ", "نقلاً عن الحرة", "أفادت الجزيرة،",
    # Latin
    "Al Jazeera", "Al-Arabiya", "Sky News Arabia", "BBC Arabic", "France 24", "France24", "CNN", "Euronews", "RT",
    "CGTN", "Asharq", "TRT", "DW", "Deutsche Welle", "Independent Arabia",
    # inside a source line, as bare names
    "المصدر: الجزيرة", "المصدر: رويترز، العربية", "المصادر: الحدث",
]
SAFE_TEXT = [
    "شبه الجزيرة الكورية", "سكان الجزيرة البركانية", "اللغة العربية", "الدول العربية", "المملكة العربية السعودية",
    "المنطقة الحرة", "التجارة الحرة", "الشرق الأوسط", "شرق آسيا", "الحدث الأبرز", "وفق وكالة الصحافة الفرنسية",
    "قالت وزارة الصحة", "فرانس برس", "رويترز", "AFP", "Reuters", "AP", "Xinhua", "dpa", "EFE", "قهوة نيوز",
    "مارتن", "شرطة ولاية فيكتوريا", "قرطبة", "المصدر: وكالات", "المصدر: وزارة الصحة الإسبانية، وكالة الصحافة الفرنسية",
]


@pytest.mark.parametrize("text", OUTLET_MENTIONS)
def test_denylist_catches_outlets(text):
    assert A.outlet_hits(text), text


@pytest.mark.parametrize("text", SAFE_TEXT)
def test_denylist_no_false_positives(text):
    assert not A.outlet_hits(text), (text, A.outlet_hits(text))


def test_harakat_and_tatweel_do_not_bypass_gate():
    assert A.outlet_hits("قناةُ الجـزيرة")
    assert A.outlet_hits("سكاي نيوز\u00a0عربية")


def test_self_test():
    assert A.self_test()


SRC = ("[م1] أعلنت وزارة الصحة في إسبانيا، الخميس، وفاة 12 شخصاً في الفيضانات، بحسب ما نقلت وكالة فرانس برس. "
       "[م2] قال الدفاع المدني الإسباني إن فرق الإنقاذ تبحث عن مفقودين، وفق رويترز. وقالت قناة الجزيرة إن الأمطار مستمرة.")


def test_ranking_official_then_wire_and_outlets_dropped():
    line, r = A.source_line(["رويترز", "الجزيرة", "وزارة الصحة الإسبانية", "BBC", "فرانس برس", "الدفاع المدني"], SRC)
    assert r[:2] == ["وزارة الصحة الإسبانية", "الدفاع المدني"]
    assert r[2] in ("رويترز", "وكالة الصحافة الفرنسية")
    assert line.startswith("المصدر: ") and not A.outlet_hits(line)


def test_unsupported_names_dropped_and_generic_fallback():
    line, r = A.source_line(["وزارة الدفاع اليابانية", "الجزيرة", "سكاي نيوز"], SRC)
    assert r == [] and line == "المصدر: وكالات"


def test_wire_aliases_canonical():
    assert A.canonical_wire("أ ف ب") == "وكالة الصحافة الفرنسية"
    assert A.canonical_wire("فرانس برس") == "وكالة الصحافة الفرنسية"
    assert A.canonical_wire("Reuters") == "رويترز"
    assert A.classify("وكالة شينخوا") == "wire"
    assert A.classify("الجزيرة") == "outlet"
    assert A.classify("وزارة الصحة") == "official"


def test_sanitize_prepositional_outlet_attribution():
    t = "توفي 5 أشخاص بحسب قناة الجزيرة، ووفقاً لسكاي نيوز عربية تستمر الأمطار، وفق BBC."
    s = A.sanitize(t)
    assert not A.outlet_hits(s), s
    assert "بحسب تقارير إعلامية" in s


def test_invented_attribution_flagged():
    assert A.unsupported_attributions("قالت وزارة الدفاع اليابانية إن 12 شخصاً توفوا", SRC)
    assert not A.unsupported_attributions("قالت وزارة الصحة الإسبانية إن 12 شخصاً توفوا، وفق وكالة الصحافة الفرنسية", SRC)
    assert A.unsupported_attributions("وفق الجزيرة، توفي 12 شخصاً", SRC)[0][1] == "names a competing outlet"
    assert not A.unsupported_attributions("وقال إن البحث مستمر بحسب تقارير", SRC)


def test_numbers_need_in_text_attribution():
    assert A.lacks_attribution("توفي 12 شخصاً في الفيضانات\n#إسبانيا\nالمصدر: وكالات")
    assert not A.lacks_attribution("توفي 12 شخصاً في الفيضانات، وفق وزارة الصحة\nالمصدر: وزارة الصحة")
    assert not A.lacks_attribution("افتتح المتحف أبوابه للزوار\nالمصدر: وكالات")


def test_set_source_line_replaces_writer_line():
    t = A.set_source_line("نص الخبر\nالمصادر: الجزيرة، سكاي نيوز", "المصدر: وكالات")
    assert t.splitlines()[-1] == "المصدر: وكالات" and not A.outlet_hits(t)


def test_verify_check_flags_attribution_and_outlets():
    from kahwa_publisher import verify

    def fake_llm(prompt, temperature=0):
        if prompt.startswith(verify.DECOMPOSE[:20]):
            return json.dumps({"claims": [{"id": 1, "claim": "توفي 12 شخصاً", "check_worthy": True}]}, ensure_ascii=False)
        return json.dumps({"results": [{"id": 1, "verdict": "supported", "source": 1,
                                        "evidence": "أعلنت وزارة الصحة في إسبانيا، الخميس، وفاة 12 شخصاً"}]}, ensure_ascii=False)

    _, bad = verify.check(fake_llm, "قالت وزارة الصحة الإسبانية إن 12 شخصاً توفوا.\nالمصدر: وزارة الصحة الإسبانية", [SRC])
    assert bad == []
    _, bad = verify.check(fake_llm, "قالت وزارة الداخلية الفرنسية إن 12 شخصاً توفوا.", [SRC])
    assert any("الداخلية" in b["claim"] for b in bad)
    _, bad = verify.check(fake_llm, "توفي 12 شخصاً، بحسب سكاي نيوز عربية.", [SRC])
    assert any(b["note"] == "names a competing outlet" for b in bad)
    _, bad = verify.check(fake_llm, "توفي 12 شخصاً في الفيضانات.", [SRC], require_attribution=True)
    assert any("نسبة" in b["claim"] for b in bad)


def test_card_source_label_never_outlet():
    pytest.importorskip("PIL")
    from kahwa_publisher.cards import source_label
    assert source_label("الجزيرة، رويترز") == "المصدر: وكالات"
    assert source_label("وزارة الصحة") == "المصدر: وزارة الصحة"
    assert source_label("وزارة الصحة", compiled=True) == "إعداد: قهوة نيوز · المصدر: وزارة الصحة"


def test_reel_caption_attribution():
    pytest.importorskip("cv2")
    from kahwa_publisher.reels.make import post_text
    S = {"headline": "فيضانات [[إسبانيا]]", "deck": "أمطار غزيرة", "sources": ["وزارة الصحة الإسبانية", "رويترز"],
         "facts": [{"text": "توفي 12 شخصاً", "source": "وزارة الصحة الإسبانية"},
                   {"text": "البحث عن 3 مفقودين", "source": "وكالات"},
                   {"text": "الأمطار مستمرة", "source": "وكالات"}]}
    cap = post_text(S, "A")
    assert cap.splitlines()[-1] == "المصدر: وزارة الصحة الإسبانية، رويترز"
    assert "(وفق وزارة الصحة الإسبانية)" in cap and "(وفق وكالات)" in cap and "- الأمطار مستمرة\n" in cap + "\n"
    assert not A.outlet_hits(cap)


def test_reel_gate_rejects_outlet():
    pytest.importorskip("cv2")
    from kahwa_publisher.reels.render import gate
    with pytest.raises(ValueError):
        gate({"headline": "x", "sources": ["سكاي نيوز عربية"], "location": {}, "media": {}})
    assert gate({"headline": "x", "sources": ["وكالات"], "location": {}, "media": {}})
