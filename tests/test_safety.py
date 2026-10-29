"""No war / no violence gate (user hard rule 2026-10-29): rejected at selection (keywords + Gemini category check, fail
closed) and on every generated output."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kahwa_publisher import safety  # noqa: E402

VIOLENT = [
    "مباشر: التحالف يتصدى لهجمات حوثية على السعودية... وإيران تبحث مع الوسطاء خطة لإنهاء الحرب",
    "اعتراض صاروخ باليستي فوق السعودية", "الدفاعات الجوية تعترض مسيرة", "وبالقصف المدفعي على المدينة", "للغارات الجوية",
    "والاشتباكات مستمرة", "مقتل 12 في هجوم بطائرة مسيّرة", "تفجير انتحاري في سوق", "عملية عسكرية واسعة في الشمال",
    "اغتيال قيادي بارز", "إطلاق نار في مدرسة", "مسيرات وقتلة مأجورون.. هل نقلت إيران حربها إلى أوروبا؟",
    "محادثات إيران حول الحرب", "الحرْب", "الحــرب", "صفقة طائرة مقاتلة", "سقوط شهداء", "قوات الاحتلال", "حزب الله يعلن",
    "صاروخ يسقط قرب الحدود", "Houthi missile intercepted over Saudi Arabia", "Iran war talks", "drone strike kills 5",
    "airstrikes on the capital", "gunmen attack a village",
]
SAFE = [
    "محادثات وقف إطلاق النار تستأنف في القاهرة", "ناسا تطلق صاروخاً يحمل قمراً صناعياً إلى المدار",
    "زلزال بقوة 6 درجات ومقتل 5 أشخاص", "فيضانات في البرازيل", "البنك المركزي يرفع الفائدة", "انتخابات رئاسية في تشيلي",
    "نيويورك تقيد استخدام الأطفال لروبوتات الدردشة", "محاربة الفساد في البلديات", "حريق غابات في كاليفورنيا",
    "SpaceX rocket launches a satellite", "inflation slows in Europe",
]


@pytest.mark.parametrize("t", VIOLENT)
def test_keywords_reject(t):
    assert safety.violence_hits(t)


@pytest.mark.parametrize("t", SAFE)
def test_keywords_pass(t):
    assert not safety.violence_hits(t)


def test_output_gate_blocks_war_word_in_diplomacy_post():
    assert safety.output_hits("محادثات لإنهاء الحرب في السودان")
    assert not safety.output_hits("محادثات وقف إطلاق النار في السودان")


def _cl(key, title, summary=""):
    return {"key": key, "items": [{"title": title, "summary": summary}]}


def test_keyword_ok_cluster():
    assert not safety.keyword_ok(_cl("a", "اعتراض صاروخ فوق الرياض"))
    assert safety.keyword_ok(_cl("b", "ارتفاع أسعار الذهب"))


def test_classify_fail_closed_on_error():
    def boom(*a, **k):
        raise RuntimeError("429")
    assert safety.classify(boom, [_cl("a", "x"), _cl("b", "y")]) == {"a": False, "b": False}


def test_classify_fail_closed_on_garbage_and_missing_rows():
    assert safety.classify(lambda *a, **k: "not json", [_cl("a", "x")]) == {"a": False}
    ans = json.dumps({"stories": [{"id": 1, "category": "economy", "violence": False, "attack_details": False, "graphic": False}]})
    assert safety.classify(lambda *a, **k: ans, [_cl("a", "x"), _cl("b", "y")]) == {"a": True, "b": False}


@pytest.mark.parametrize("row,ok", [
    ({"category": "economy", "violence": False, "attack_details": False, "graphic": False}, True),
    ({"category": "diplomacy", "violence": False, "attack_details": False, "graphic": False}, True),
    ({"category": "diplomacy", "violence": False, "attack_details": True, "graphic": False}, False),
    ({"category": "disaster_accident", "violence": False, "attack_details": False, "graphic": False}, True),
    ({"category": "disaster_accident", "violence": False, "attack_details": False, "graphic": True}, False),
    ({"category": "war_conflict", "violence": False, "attack_details": False, "graphic": False}, False),
    ({"category": "military", "violence": False, "attack_details": False, "graphic": False}, False),
    ({"category": "attack_terror", "violence": True, "attack_details": True, "graphic": False}, False),
    ({"category": "politics", "violence": True, "attack_details": False, "graphic": False}, False),
    ({"category": "politics"}, False),                       # missing flags -> fail closed
    ({}, False), (None, False),
])
def test_verdict(row, ok):
    assert safety.verdict(row) is ok


def test_reel_gate_rejects_violence():
    from kahwa_publisher.reels.render import gate
    with pytest.raises(ValueError):
        gate({"id": "x", "headline": "اعتراض [[صاروخ]] فوق المدينة", "location": {"country_iso3": "SAU"}, "media": {}})


def test_reel_story_rejects_violence_output():
    from kahwa_publisher.reels import story
    with pytest.raises(story.StoryError):
        story.write_story(lambda *a, **k: json.dumps({"safe": True, "headline": "الحرب"}), [], [], "x", src="- [م1] نص")


def test_newsroom_selection_uses_gate():
    src = (Path(__file__).resolve().parents[1] / "kahwa_publisher/newsroom.py").read_text()
    assert "safety.keyword_ok(c)" in src and "safety.classify(llm, cands)" in src and "safety.output_hits(" in src
