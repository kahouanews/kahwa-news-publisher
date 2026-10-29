"""Concept B sting rules: alert «عاجل» only for top-tier stories, max 1/day and 3/week, never back-to-back with an edition reel;
edition sting max 4/day. Plus: verify.source_numbers (spelled-out numbers, root cause of reel run 40929624756)."""
from datetime import datetime, timedelta, timezone

from kahwa_publisher.stings import policy as P
from kahwa_publisher.verify import source_numbers

ALG = timezone(timedelta(hours=1))
T0 = datetime(2026, 10, 29, 12, 0, tzinfo=ALG).timestamp()
TOP = (True, ["BBC عربي", "سكاي نيوز عربية"])


def st(*events):
    return {"events": [{"t": t, "kind": k, "id": str(i)} for i, (t, k) in enumerate(events)]}


def test_top_tier_required():
    assert P.alert_allowed(st(), *TOP, now=T0)[0]
    assert not P.alert_allowed(st(), False, TOP[1], now=T0)[0]                       # not urgent
    assert not P.alert_allowed(st(), True, ["BBC عربي"], now=T0)[0]                  # one outlet
    assert not P.alert_allowed(st(), True, ["سكاي نيوز عربية", "الجزيرة"], now=T0)[0]  # no tier-1 outlet


def test_alert_daily_cap():
    ok, why = P.alert_allowed(st((T0 - 3600, "alert")), *TOP, now=T0)
    assert not ok and "today" in why
    # yesterday's alert (Algeria calendar day) does not count for today
    assert P.alert_allowed(st((T0 - 14 * 3600, "alert")), *TOP, now=T0)[0]


def test_alert_weekly_cap():
    s = st((T0 - 1 * 86400, "alert"), (T0 - 2 * 86400, "alert"), (T0 - 3 * 86400, "alert"))
    ok, why = P.alert_allowed(s, *TOP, now=T0)
    assert not ok and "7 days" in why
    s = st((T0 - 1 * 86400, "alert"), (T0 - 2 * 86400, "alert"), (T0 - 8 * 86400, "alert"))
    assert P.alert_allowed(s, *TOP, now=T0)[0]


def test_never_back_to_back_with_edition():
    ok, why = P.alert_allowed(st((T0 - 1800, "edition")), *TOP, now=T0)
    assert not ok and "edition" in why
    assert P.alert_allowed(st((T0 - 4 * 3600, "edition")), *TOP, now=T0)[0]
    assert P.alert_allowed(st((T0 - 3600, "edition"), (T0 - 1200, "reel")), *TOP, now=T0)[0]   # a plain reel in between
    ok, why = P.edition_allowed(st((T0 - 1800, "alert")), now=T0)
    assert not ok and "alert" in why


def test_edition_daily_cap():
    s = st(*[(T0 - 3600 * (i + 1) + 100, "edition") for i in range(4)])
    ok, why = P.edition_allowed(s, now=T0)
    assert not ok and "edition cap" in why
    assert P.edition_allowed(st(*[(T0 - 3600 * (i + 1), "edition") for i in range(3)]), now=T0)[0]


def test_record_and_save(tmp_path):
    s = P.record(P.load(tmp_path / "x.json"), "alert", "r1", now=T0)
    P.save(s, tmp_path / "x.json")
    assert P.counts(P.load(tmp_path / "x.json"), now=T0 + 60)["alert_today"] == 1


def test_source_numbers_spelled_out():
    src = "تعرضت المنطقة للهجوم بأربع طائرات مسيّرة نفاثة، تمكنت الدفاعات الجوية من إسقاط ثلاث منها"
    assert {"3", "4"} <= source_numbers(src)
    assert "6" not in source_numbers("ستة عشر قتيلا") and "10" not in source_numbers("ستة عشر قتيلا")
    assert source_numbers("قتل 14 شخصا") == {"14"}
