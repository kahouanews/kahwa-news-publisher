"""Regression tests for the 2026-10-31 reel failures (runs 41197869535 / 41198098858 / 41199797479):
StoryError «numbers not in sources: ['70000']» on «فرنسا: تعبئة 70 ألف رجل أمن» failed the whole reel three times.

Covers: number normalisation (kahwa_publisher/numbers.py) on both sides, the per-story drop + next candidate in the reel maker,
the per-slot failed-attempt guard, the primary-sources-only gate, the religion filter and the Arabic spelling check."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kahwa_publisher import attribution as A
from kahwa_publisher import newsroom as N
from kahwa_publisher import numbers as NUM
from kahwa_publisher import safety
from kahwa_publisher import spelling
from kahwa_publisher import verify as V
from kahwa_publisher.reels import make as MK
from kahwa_publisher.reels import story

ROOT = Path(__file__).resolve().parents[1]

SRC = ("- [م1] فرنسا: تعبئة 70 ألف رجل أمن وسط تحذيرات من \"خطر كبير\" — أعلن وزير الداخلية الفرنسي تعبئة 70 ألف رجل أمن "
       "في أنحاء فرنسا خلال عطلة نهاية الأسبوع، وقال الوزير إن الخطر كبير.\n"
       "- [م2] فرنسا: موجة احتجاجات جديدة والحكومة تهدد برد قاس — قالت الحكومة الفرنسية إنها سترد بحزم على أعمال الشغب "
       "في عدة مدن، وفق وكالة الصحافة الفرنسية.")


def llm_json(display="70000", fact0="فرنسا تعبئ [[70 ألف]] رجل أمن", attribution=("وزير الداخلية الفرنسي",)):
    src = "وزير الداخلية الفرنسي"
    return {
        "safe": True, "attribution": list(attribution), "topic_ar": "أمن فرنسا",
        "headline": "فرنسا تعبئ [[70 ألف]] رجل أمن", "headline_breaking": "فرنسا [[تعبئ]] 70 ألف رجل أمن",
        "deck": "تعبئة أمنية في أنحاء فرنسا", "visual_caption": "",
        "location": {"country_en": "France", "country_iso3": "FRA", "region_ar": "إيل دو فرانس", "place_ar": "باريس",
                     "lat": 48.85, "lon": 2.35},
        "where_line": "تعبئة في [[أنحاء فرنسا]] خلال عطلة نهاية الأسبوع",
        "lower_third": {"name_ar": "فرنسا", "role_ar": "تعبئة أمنية واسعة"},
        "facts": [{"text": fact0, "source": src}, {"text": "الوزير: [[الخطر كبير]]", "source": src},
                  {"text": "الحكومة تتوعد [[بالرد بحزم]] على الشغب", "source": "الحكومة الفرنسية"}],
        "counter": {"display": display, "prefix_ar": "", "label_ar": "رجل أمن", "sub_ar": "في أنحاء فرنسا", "source": src},
        "next": {"lines": ["الحكومة [[سترد بحزم]] على الشغب", "انتشار أمني [[في عدة مدن]]"], "sources": [src, src]},
        "breaking_lines": [{"text": "تعبئة [[70 ألف]] رجل أمن", "source": src}, {"text": "الوزير: [[الخطر كبير]]", "source": src},
                           {"text": "الحكومة [[سترد بحزم]]", "source": "الحكومة الفرنسية"},
                           {"text": "شغب [[في عدة مدن]]", "source": "الحكومة الفرنسية"}],
        "ticker": ["تعبئة 70 ألف رجل أمن في فرنسا"], "mood": "serious", "tragic": False, "urgent": False,
        "footage_queries": ["Paris skyline"], "place_terms_en": ["France", "Paris"],
    }


# ------------------------------------------------------------------ 1) number normalisation
@pytest.mark.parametrize("claim", ["70000", "70,000", "70.000", "70 000", "70\u00a0000", "70٬000", "٧٠٠٠٠", "70 ألف",
                                   "70 ألفاً", "٧٠ ألف", "70ألف", "70 الف"])
def test_70_thousand_all_spellings_match_the_source(claim):
    assert NUM.unsupported(claim, "فرنسا: تعبئة 70 ألف رجل أمن") == []
    assert NUM.unsupported(claim, "France mobilises 70,000 police") == []
    assert NUM.unsupported(claim, "تعبئة سبعين ألف رجل أمن") == []          # spelled out in the source


def test_old_substring_check_was_the_root_cause():
    src = "فرنسا: تعبئة 70 ألف رجل أمن"
    assert "70000" not in src                                           # what the old number_ok tested
    assert story.number_ok("70000", src) and story.number_ok("70,000", src)


@pytest.mark.parametrize("claim,src", [
    ("1.5 مليون", "نزح 1,500,000 شخص"), ("1500000", "نحو 1.5 مليون نازح"), ("3 ملايين", "3,000,000"),
    ("2000000000", "2 مليار دولار"), ("500 ألف", "نصف مليون زائر"),
    ("3000", "ثلاثة آلاف متظاهر"), ("25000", "خمسة وعشرين ألف عامل"), ("2.5%", "بنسبة 2,5 في المئة"),
    ("30%", "30 بالمئة"), ("٣٠٪", "ارتفع 30%"), ("10", "بين 10 و15 مدينة"), ("15", "10-15 مدينة"),
    ("4", "بأربع طائرات مسيّرة"), ("2000", "ألفين شخص"), ("12", "اثنا عشر جريحا"), ("5 billion", "5 مليارات"),
])
def test_value_equivalences(claim, src):
    assert NUM.unsupported(claim, src) == [], (claim, src)


@pytest.mark.parametrize("claim,src", [
    ("71000", "70 ألف"), ("7000", "70 ألف"), ("700000", "70 ألف"), ("70 مليون", "70 ألف"), ("15", "ستة عشر قتيلا"),
    ("2.6%", "2,5%"), ("16", "6 قتلى"), ("5", "15 مدينة"),
])
def test_wrong_figures_still_fail(claim, src):
    assert NUM.unsupported(claim, src), (claim, src)


def test_verify_layer_uses_values_too():
    assert {"70000", "70"} <= V.source_numbers("تعبئة 70 ألف رجل أمن")
    assert {"3", "4"} <= V.source_numbers("بأربع طائرات، أسقطت ثلاث منها")


def test_counter_display_folds_magnitude_words():
    assert story.counter_display("70 ألف") == ("70,000", 70000.0)
    assert story.counter_display("٧٠ ألفاً") == ("70,000", 70000.0)
    assert story.counter_display("70.000") == ("70,000", 70000.0)
    assert story.counter_display("1.5") == ("1.5", 1.5)
    assert story.counter_display("30%")[0] == "" and story.counter_display("نحو 70")[0] == ""


@pytest.mark.parametrize("display", ["70000", "70,000", "70.000", "70 ألف", "٧٠ ألفاً", "70 000"])
def test_build_story_exact_failing_case_now_passes(display):
    S = story.build_story(llm_json(display=display, fact0="تعبئة [[70,000]] رجل أمن"), [], SRC, "t1", ["x", "y"])
    assert S["big_number"]["value"] == 70000.0 and S["big_number"]["display"] in ("70000", "70,000")
    assert "وزير الداخلية الفرنسي" in S["sources"]


def test_build_story_still_rejects_an_invented_figure():
    with pytest.raises(story.StoryError, match="numbers not in sources"):
        story.build_story(llm_json(display="71000"), [], SRC, "t1", ["x", "y"])


def test_shorten_keeps_same_value_in_another_format():
    d = {"headline": "فرنسا تعبئ 70 ألف رجل أمن في أنحاء البلاد خلال عطلة نهاية الأسبوع وسط مخاوف"}
    story.shorten_fields(lambda p, temperature=0: json.dumps({"lines": ["فرنسا تعبئ 70,000 رجل أمن"]}), d, log=lambda *a: None)
    assert d["headline"] == "فرنسا تعبئ 70,000 رجل أمن"


# ------------------------------------------------------------------ 2) one bad story never fails the reel
def _cl(key, title):
    return {"key": key, "items": [{"title": title, "summary": "", "src": "a", "link": ""}], "sources": ["a", "b"], "tok": {key}}


def test_failing_story_is_dropped_and_next_candidate_used(monkeypatch):
    calls, marked = [], []

    def fake_make(c, *a, **k):
        calls.append(c["key"])
        if c["key"] == "c1":
            raise story.StoryError("numbers not in sources: ['70000']")
        return {"id": "S2"}, c
    monkeypatch.setattr(MK, "make_story", fake_make)
    summary = {}
    S, c, dropped = MK.choose_story([_cl("c1", "فرنسا: تعبئة 70 ألف رجل أمن"), _cl("c2", "قصة ثانية")], None, None, None,
                                    summary, {"stories": []}, dry=False, mark=lambda seen, c: marked.append(c["key"]))
    assert calls == ["c1", "c2"] and c["key"] == "c2" and S == {"id": "S2"}
    assert dropped[0]["why"].startswith("StoryError: numbers not in sources") and marked == ["c1"]
    assert summary["dropped_stories"] == dropped


def test_dry_runs_never_mark_dropped_stories(monkeypatch):
    marked = []
    monkeypatch.setattr(MK, "make_story", lambda c, *a, **k: (_ for _ in ()).throw(story.StoryError("too long (50): x")))
    with pytest.raises(story.StoryError, match="no candidate passed the gates"):
        MK.choose_story([_cl("c1", "a"), _cl("c2", "b")], None, None, None, {}, {"stories": []}, dry=True,
                        mark=lambda s, c: marked.append(c))
    assert marked == []


def test_all_editorial_rejections_are_a_skip_not_a_failure(monkeypatch):
    errs = iter([MK.Rejected("claim verification failed, reel NOT made"), story.StoryError("no primary source: x")])
    monkeypatch.setattr(MK, "make_story", lambda c, *a, **k: (_ for _ in ()).throw(next(errs)))
    with pytest.raises(MK.Rejected, match="editorial gates"):
        MK.choose_story([_cl("c1", "a"), _cl("c2", "b")], None, None, None, {}, None, dry=True)


def test_reel_tries_several_candidates():
    src = (ROOT / "kahwa_publisher" / "reels" / "make.py").read_text()
    assert "pick_reel_clusters(n=int(os.getenv(\"REEL_CANDIDATES\", \"3\")))" in src and "choose_story(" in src


# ------------------------------------------------------------------ 3) per-slot failed-attempt guard
def _slots(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("KAHWA_ROOT", str(tmp_path))
    import kahwa_publisher.slots as SL
    importlib.reload(SL)
    return SL


def test_deterministic_failure_stops_after_two_attempts(tmp_path, monkeypatch):
    SL = _slots(tmp_path, monkeypatch)
    t0 = datetime(2026, 10, 31, 11, 41, 5, tzinfo=timezone.utc)
    a = SL.gate("reel", "", now=t0)                                     # cron-job.org
    assert a["run"] == "true"
    assert SL.mark_failed(a["key"], 41197869535, now=t0) == 1
    b = SL.gate("reel", a["slot"], now=t0 + timedelta(minutes=3))        # Worker backup: 2nd attempt allowed
    assert b["run"] == "true"
    assert SL.mark_failed(b["key"], 41198098858, now=t0) == 2
    c = SL.gate("reel", "", now=t0 + timedelta(minutes=27))              # GitHub schedule, late: gives up
    assert c["run"] == "false" and "gave up" in c["reason"]
    nxt = SL.gate("reel", "", now=datetime(2026, 10, 31, 14, 11, 5, tzinfo=timezone.utc))   # next slot starts fresh
    assert nxt["run"] == "true"
    assert sorted(p.name for p in (tmp_path / "state" / "slots").glob("*.json")) == []        # markers file untouched
    assert (tmp_path / "state" / "slots" / "attempts" / "reel.json").exists()


def test_test_runs_never_count_and_reel_yml_records_failures():
    import yaml
    d = yaml.safe_load((ROOT / ".github" / "workflows" / "reel.yml").read_text())
    steps = d["jobs"]["reel"]["steps"]
    fail = [s for s in steps if "slots fail reel" in (s.get("run") or "")]
    assert fail and fail[0]["if"].startswith("failure()") and "needs.gate.outputs.key != ''" in fail[0]["if"]
    assert "git reset -q --hard origin/main" in fail[0]["run"]
    quota = [s for s in steps if "reel llm quota" in (s.get("run") or "")][0]
    assert "state/reels_seen.json" in quota["run"]


# ------------------------------------------------------------------ 4) editorial: primary sources, religion, spelling
def test_no_primary_source_no_reel():
    src = "- [م1] فرنسا: تعبئة 70 ألف رجل أمن — تعبئة 70 ألف رجل أمن في أنحاء فرنسا خلال عطلة نهاية الأسبوع وسط مخاوف."
    with pytest.raises(story.StoryError, match="no primary source"):
        story.build_story(llm_json(attribution=()), [], src, "t1", ["x", "y"])


def test_primary_sources_found_in_texts_when_writer_gives_none():
    assert "وزير الداخلية الفرنسي" in " ".join(A.primary_in_sources(SRC)) or A.primary_in_sources(SRC)
    assert all(A.classify(x) in ("official", "wire", "other") for x in A.primary_in_sources(SRC))
    assert not any(A.is_outlet(x) for x in A.primary_in_sources(SRC + " وقالت قناة الجزيرة إن"))


def test_religion_items_are_purged_from_mixed_clusters():
    items = [
        {"src": "DW عربية", "title": "فرنسا: تعبئة 70 ألف رجل أمن وسط تحذيرات من \"خطر كبير\"", "summary": ""},
        {"src": "الجزيرة", "title": "فرنسا: موجة احتجاجات جديدة والحكومة تهدد برد قاس", "summary": ""},
        {"src": "سكاي نيوز عربية", "title": "غضب المعيشة يجتاح أوروبا.. والهالوين يضاعف مخاوف الشغب", "summary": ""},
        {"src": "فرانس 24", "title": "بين \"البدعة\" و\"الانفتاح\".. إلغاء الاحتفال بالهالوين \"تمصير\" للمدارس الدولية في مصر؟",
         "summary": ""},
    ]
    c = N.purge_items({"items": items, "sources": sorted({i["src"] for i in items}), "key": "k", "tok": set()})
    assert [i["src"] for i in c["items"]] == ["DW عربية", "الجزيرة"] and c["sources"] == ["DW عربية", "الجزيرة"]
    assert N.religion_hits("الدين العام في اليابان") == [] and N.religion_hits("الجمهورية الإسلامية") == []
    assert N.blocked({"items": [items[3]]})
    assert "religion" in safety.REJECT_CATS and not safety.verdict(
        {"category": "religion", "graphic": False, "propaganda": False, "unverified": False, "needs_banned_country": False})


def test_known_typos_from_the_failing_titles_are_fixed_without_llm():
    fixes, unresolved = spelling.proofread(lambda p, temperature=None: '{"typos": []}',
                                           ["فرنسا: موجة احتجاجت [[جديدية]]"], "")
    assert fixes == {"احتجاجت": "احتجاجات", "جديدية": "جديدة"} and unresolved == []
    assert spelling.apply({"headline": "موجة احتجاجت [[جديدية]]", "source_texts": "احتجاجت"}, fixes) == \
        {"headline": "موجة احتجاجات [[جديدة]]", "source_texts": "احتجاجت"}


@pytest.mark.real_spelling
def test_llm_proofreader_guards():
    lines = ["مسؤلون فرنسيون يحذرون", "زيارة ريتايو إلى ليون"]
    ans = {"typos": [{"wrong": "مسؤلون", "right": "مسؤولون"},            # safe fix
                     {"wrong": "ريتايو", "right": "روتايو"},              # name spelled so twice in the sources: left alone
                     {"wrong": "غيرموجودة", "right": "غير موجودة"},       # not in the lines: ignored
                     {"wrong": "ليون", "right": "مدينة ليون الفرنسية"}]}  # a rewrite, not a typo fix: unresolved
    fixes, unresolved = spelling.proofread(lambda p, temperature=None: json.dumps(ans, ensure_ascii=False), lines,
                                           "قال ريتايو ... وأكد ريتايو", log=lambda *a: None)
    assert fixes == {"مسؤلون": "مسؤولون"} and len(unresolved) == 1 and "ليون" in unresolved[0]


@pytest.mark.real_spelling
def test_proofreader_error_fails_closed():
    fixes, unresolved = spelling.proofread(lambda p, temperature=None: "not json", ["سطر عادي"], "", log=lambda *a: None)
    assert unresolved and unresolved[0].startswith("proofreader error")


@pytest.mark.real_spelling
def test_write_story_fixes_typos_before_the_fact_check(monkeypatch):
    d = llm_json()
    d["headline"] = "فرنسا: موجة [[احتجاجت]] جديدية"
    seen = {}

    def writer(p, temperature=None):
        return json.dumps(d, ensure_ascii=False)

    def utility(p, temperature=None):
        return '{"typos": []}' if p.startswith(spelling.PROMPT[:30]) else '{"lines": []}'

    def checker(p, temperature=None):
        seen["checked"] = p
        return '{"ok": true, "problems": []}'
    S, _ = story.write_story(writer, [], ["x", "y"], "t1", fetch_articles=False, log=lambda *a: None, src=SRC,
                             checker=checker, shortener=utility)
    assert S["headline"] == "فرنسا: موجة [[احتجاجات]] جديدة" and "احتجاجت" not in seen["checked"]
