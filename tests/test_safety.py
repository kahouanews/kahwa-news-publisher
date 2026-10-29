"""Editorial safety (owner decision 2026-10-29): war news is ALLOWED in a calm factual tone. Rejected: graphic / gore,
propaganda, unverified, sports, stories that need a banned country. War stories never get photos / footage."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kahwa_publisher import safety  # noqa: E402

WAR_NEWS = [
    "مباشر: التحالف يتصدى لهجمات حوثية على السعودية... وإيران تبحث مع الوسطاء خطة لإنهاء الحرب",
    "اعتراض صاروخ باليستي فوق السعودية", "الدفاعات الجوية تعترض مسيرة", "وبالقصف المدفعي على المدينة", "للغارات الجوية",
    "والاشتباكات مستمرة", "مقتل 12 في هجوم بطائرة مسيّرة", "عملية عسكرية واسعة في الشمال", "محادثات إيران حول الحرب",
    "الحرْب", "الحــرب", "صفقة طائرة مقاتلة", "Houthi missile intercepted over Saudi Arabia", "Iran war talks",
    "drone strike kills 5", "airstrikes on the capital",
]
GRAPHIC = ["أشلاء الضحايا في الشارع", "جثث متفحمة بعد القصف", "مشاهد مروعة من المدينة", "فيديو مروع يظهر", "قطع رأس رهينة",
           "graphic video shows the attack", "beheaded hostage", "gruesome scenes"]
GRAPHIC_TONE = ["مجزرة في القرية", "سالت الدماء", "هجوم دموي", "مشهد بشع", "massacre in the village", "horrific attack"]
CALM = ["محادثات وقف إطلاق النار تستأنف في القاهرة", "ناسا تطلق صاروخاً يحمل قمراً صناعياً إلى المدار",
        "زلزال بقوة 6 درجات ومقتل 5 أشخاص", "البنك المركزي يرفع الفائدة", "دمار واسع في المباني", "مشروع جديد في دمشق",
        "قتلى وجرحى في قصف على المدينة بحسب وزارة الصحة", "inflation slows in Europe", "missile strike on a port, officials say"]


@pytest.mark.parametrize("t", WAR_NEWS)
def test_war_news_passes_the_text_gates(t):
    assert not safety.output_hits(t)                          # war words themselves are allowed
    assert safety.keyword_ok(_cl("w", t))                      # no blanket war rejection at selection
    assert safety.is_conflict(t)                               # ...but it is a conflict story -> no photo / footage


@pytest.mark.parametrize("t", GRAPHIC)
def test_graphic_sources_rejected_at_selection(t):
    assert not safety.keyword_ok(_cl("g", t))
    assert safety.output_hits(t)


@pytest.mark.parametrize("t", GRAPHIC_TONE)
def test_graphic_tone_never_published(t):
    assert safety.output_hits(t)


@pytest.mark.parametrize("t", CALM)
def test_calm_text_passes(t):
    assert not safety.output_hits(t)
    assert safety.keyword_ok(_cl("c", t))


def test_space_rocket_is_not_conflict():
    assert not safety.is_conflict("ناسا تطلق صاروخاً يحمل قمراً صناعياً إلى المدار")
    assert not safety.is_conflict("SpaceX rocket launches a satellite")


def _cl(key, title, summary=""):
    return {"key": key, "items": [{"title": title, "summary": summary}]}


def test_classify_fail_closed_on_error():
    def boom(*a, **k):
        raise RuntimeError("429")
    assert safety.classify(boom, [_cl("a", "x"), _cl("b", "y")]) == {"a": False, "b": False}


def test_classify_fail_closed_on_garbage_and_missing_rows():
    assert safety.classify(lambda *a, **k: "not json", [_cl("a", "x")]) == {"a": False}
    row = {"id": 1, "category": "war_conflict", "graphic": False, "propaganda": False, "unverified": False,
           "needs_banned_country": False}
    ans = json.dumps({"stories": [row]})
    assert safety.classify(lambda *a, **k: ans, [_cl("a", "x"), _cl("b", "y")]) == {"a": True, "b": False}


OK = {"graphic": False, "propaganda": False, "unverified": False, "needs_banned_country": False}


@pytest.mark.parametrize("row,ok", [
    (dict(OK, category="war_conflict"), True),                 # war news allowed
    (dict(OK, category="military"), True),
    (dict(OK, category="attack"), True),
    (dict(OK, category="diplomacy"), True),
    (dict(OK, category="economy"), True),
    (dict(OK, category="disaster_accident"), True),
    (dict(OK, category="war_conflict", graphic=True), False),
    (dict(OK, category="war_conflict", propaganda=True), False),
    (dict(OK, category="military", unverified=True), False),
    (dict(OK, category="politics", needs_banned_country=True), False),
    (dict(OK, category="sports"), False),
    ({"category": "war_conflict", "graphic": False}, False),   # missing flags -> fail closed
    ({}, False), (None, False),
])
def test_verdict(row, ok):
    assert safety.verdict(row) is ok


def test_classifier_prompt_allows_war():
    assert "مسموحة" in safety.CLASSIFY and "war_conflict" not in safety.REJECT_CATS


def test_reel_gate_allows_calm_war_reel_without_media():
    from kahwa_publisher.reels.render import gate
    assert gate({"id": "x", "headline": "اعتراض [[صاروخ]] فوق المدينة", "location": {"country_iso3": "SAU"}, "media": {}})


def test_reel_gate_rejects_graphic_wording_and_war_footage():
    from kahwa_publisher.reels.render import gate
    with pytest.raises(ValueError):
        gate({"id": "x", "headline": "[[مجزرة]] في المدينة", "location": {"country_iso3": "SAU"}, "media": {}})
    m = {"m1": {"credit": "ويكيميديا كومنز", "license": "CC BY 4.0", "credit_short": "ويكيميديا"}}
    with pytest.raises(ValueError):
        gate({"id": "x", "headline": "قصف على [[الميناء]]", "location": {"country_iso3": "SAU"}, "media": m})


def test_reel_story_rejects_graphic_output():
    from kahwa_publisher.reels import story
    with pytest.raises(story.StoryError):
        story.write_story(lambda *a, **k: json.dumps({"safe": True, "headline": "مشاهد مروعة"}), [], [], "x", src="- [م1] نص")


def test_banned_country_text_ban_unchanged():
    from kahwa_publisher.newsroom import banned_country
    from kahwa_publisher.reels.render import banned_hits
    for w in ["والجزائر", "بالمغرب", "للإمارات", "الإسرائيلية", "Moroccan", "UAE"]:
        assert banned_hits(w), w
    for w in ["والجزائر", "بالمغرب", "للإمارات", "إسرائيل"]:
        assert banned_country(w), w


def test_newsroom_selection_uses_gate_and_no_war_ban():
    src = (Path(__file__).resolve().parents[1] / "kahwa_publisher/newsroom.py").read_text()
    assert "safety.keyword_ok(c)" in src and "safety.classify(R.bound(\"classifier\"), cands)" in src and "safety.output_hits(" in src
    assert "WAR_WORDS" not in src and "ممنوع منعاً باتاً أي خبر عن حرب" not in src
    assert "safety.is_conflict(blob)" in src                   # war story -> no photo
