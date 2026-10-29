"""Reel story writer: shorten-and-retry for overlong lines, unverifiable decorative captions dropped instead of failing."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kahwa_publisher.reels import story  # noqa: E402


def test_shorten_overlong_lines():
    d = {"headline": "عنوان طويل " * 6, "facts": [{"text": "نقطة طويلة جداً " * 6}, {"text": "قصيرة"}], "ticker": ["س" * 100]}

    def llm(p, temperature=0):
        return json.dumps({"lines": ["عنوان [[قصير]]"] * p.count("الطول الحالي")})
    story.shorten_fields(llm, d, log=lambda *a: None)
    assert d["headline"] == "عنوان [[قصير]]" and d["facts"][0]["text"] == "عنوان [[قصير]]" and d["facts"][1]["text"] == "قصيرة"


def test_shorten_never_changes_numbers():
    d = {"headline": "ارتفاع عدد السكان إلى 120 مليون نسمة في البلاد هذا العام وفق الإحصاء"}
    story.shorten_fields(lambda p, temperature=0: json.dumps({"lines": ["ارتفاع السكان إلى 150 مليون"]}), d, log=lambda *a: None)
    assert "120" in d["headline"]                           # a shortened line with a different figure is refused


def test_overlong_decorative_lines_dropped_when_llm_fails():
    d = {"visual_caption": "ص" * 80, "ticker": ["ت" * 120, "قصير"], "headline": "ع" * 70}
    story.shorten_fields(lambda p, temperature=0: "{}", d, log=lambda *a: None)
    assert d["visual_caption"] == "" and d["ticker"] == ["قصير"] and len(d["headline"]) == 70   # headline left to build_story


def test_drop_unverifiable_caption():
    S = {"visual_caption": "طائرة عسكرية في [[المعرض]] الدولي", "deck": "الخميس في لندن", "ticker": ["سطر أ", "سطر ب"],
         "facts": [{"text": "نقطة"}]}
    ok = story.drop_unverifiable_captions(S, ["طائرة عسكرية في المعرض الدولي: معلومة غير موجودة في المصادر"], log=lambda *a: None)
    assert ok and S["visual_caption"] == ""


def test_other_problems_not_dropped():
    S = {"visual_caption": "تعليق الصورة هنا", "deck": "x", "ticker": [], "facts": []}
    assert not story.drop_unverifiable_captions(S, ["العنوان الرئيسي: رقم خاطئ"], log=lambda *a: None)
    assert S["visual_caption"] == "تعليق الصورة هنا"
