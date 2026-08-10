"""Pure-function tests for the calendar time-expression resolver (V2.2).

``resolve_temporal_expression`` is side-effect free with an injected ``today``,
so every branch here is deterministic and offline.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from styleforge.tools.calendar.festivals import LUNAR_FESTIVAL_DATES
from styleforge.tools.calendar.resolver import resolve_temporal_expression

# 2026-08-12 is a Wednesday.
WED = date(2026, 8, 12)


def resolve(expression: str, today: date = WED):
    return resolve_temporal_expression(expression, today=today)


def assert_day(res, expected: date):
    assert res.status == "resolved"
    assert res.start_date == expected.isoformat()
    assert res.end_date == expected.isoformat()


def assert_range(res, start: date, end: date):
    assert res.status == "resolved"
    assert res.start_date == start.isoformat()
    assert res.end_date == end.isoformat()


class TestExplicitDays:
    def test_today(self):
        assert_day(resolve("今天"), WED)
        assert_day(resolve("今日"), WED)

    def test_tomorrow(self):
        assert_day(resolve("明天"), WED + timedelta(days=1))
        assert_day(resolve("明日"), WED + timedelta(days=1))

    def test_day_after_tomorrow(self):
        assert_day(resolve("后天"), WED + timedelta(days=2))
        assert_day(resolve("大后天"), WED + timedelta(days=3))

    def test_iso_date(self):
        assert_day(resolve("2026-12-31"), date(2026, 12, 31))

    def test_iso_across_year(self):
        assert_day(resolve("2027-01-01"), date(2027, 1, 1))

    def test_yesterday_is_historical(self):
        # Historical dates resolve fine here; the weather tool rejects them.
        assert_day(resolve("昨天"), WED - timedelta(days=1))


class TestWeekdays:
    def test_bare_weekday_when_today_matches(self):
        # Today is Wednesday; 周三 must resolve to today (delta 0).
        assert_day(resolve("周三"), WED)

    def test_bare_weekday_already_passed(self):
        # Monday has passed this week -> next Monday (+5).
        assert_day(resolve("周一"), WED + timedelta(days=5))

    def test_this_weekday_in_future(self):
        # This Friday is still ahead (+2).
        assert_day(resolve("本周五"), WED + timedelta(days=2))

    def test_this_weekday_already_passed(self):
        # This Monday passed -> same day next week (+5).
        assert_day(resolve("本周一"), WED + timedelta(days=5))

    def test_next_weekday(self):
        assert_day(resolve("下周五"), WED + timedelta(days=9))

    def test_sunday_weekday_reference(self):
        # On Sunday, 本周五 already passed -> next Friday.
        sunday = date(2026, 8, 16)
        assert_day(resolve("本周周五", today=sunday), sunday + timedelta(days=5))


class TestWeekend:
    def test_midweek_weekend(self):
        # Wednesday -> upcoming Sat+Sun.
        assert_range(resolve("周末"), WED + timedelta(days=3), WED + timedelta(days=4))
        assert_range(resolve("本周末"), WED + timedelta(days=3), WED + timedelta(days=4))

    def test_saturday_weekend_is_this_weekend(self):
        saturday = date(2026, 8, 15)
        assert_range(resolve("周末", today=saturday), saturday, saturday + timedelta(days=1))

    def test_sunday_weekend_is_next_weekend(self):
        sunday = date(2026, 8, 16)
        assert_range(
            resolve("周末", today=sunday),
            sunday + timedelta(days=6),
            sunday + timedelta(days=7),
        )

    def test_next_weekend(self):
        assert_range(resolve("下周末"), WED + timedelta(days=10), WED + timedelta(days=11))


class TestRanges:
    def test_next_week(self):
        # Wednesday -> next ISO week Mon..Sun.
        assert_range(resolve("下周"), WED + timedelta(days=5), WED + timedelta(days=11))
        assert_range(resolve("下星期"), WED + timedelta(days=5), WED + timedelta(days=11))

    def test_next_month(self):
        assert_range(resolve("下个月"), date(2026, 9, 1), date(2026, 9, 30))

    def test_next_month_rolls_year(self):
        assert_range(resolve("下个月", today=date(2026, 12, 12)), date(2027, 1, 1), date(2027, 1, 31))


class TestGregorianFestivals:
    def test_national_day_this_year(self):
        assert_day(resolve("国庆"), date(2026, 10, 1))

    def test_new_year_passed_rolls_to_next_year(self):
        assert_day(resolve("元旦", today=date(2026, 1, 2)), date(2027, 1, 1))

    def test_new_year_before_uses_this_year(self):
        assert_day(resolve("元旦", today=date(2025, 12, 20)), date(2026, 1, 1))

    def test_christmas_explicit_year(self):
        assert_day(resolve("2026圣诞"), date(2026, 12, 25))


class TestLunarFestivals:
    def test_mid_autumn_2026(self):
        assert_day(resolve("中秋"), date(2026, 9, 25))

    def test_spring_festival_passed_rolls_next_year(self):
        assert_day(resolve("春节", today=date(2026, 2, 20)), date(2027, 2, 6))

    def test_dragon_boat(self):
        assert_day(resolve("端午"), date(2027, 6, 9))

    def test_qingming(self):
        assert_day(resolve("清明"), date(2027, 4, 5))

    def test_lantern(self):
        assert_day(resolve("元宵"), date(2027, 2, 20))

    def test_double_ninth(self):
        assert_day(resolve("重阳"), date(2026, 10, 18))

    def test_explicit_year(self):
        assert_day(resolve("2028中秋"), date(2028, 10, 3))

    def test_next_year_prefix(self):
        assert_day(resolve("明年中秋"), date(2027, 9, 15))

    def test_out_of_table_year_is_honest_unsupported(self):
        res = resolve("2035中秋")
        assert res.status == "unsupported"
        assert res.error_code == "festival_out_of_table"

    def test_lantern_is_spring_festival_plus_fourteen(self):
        # Self-consistency across every year in the static table.
        for year in range(2025, 2031):
            spring = date.fromisoformat(LUNAR_FESTIVAL_DATES["春节"][year])
            lantern = date.fromisoformat(LUNAR_FESTIVAL_DATES["元宵"][year])
            assert lantern - spring == timedelta(days=14), f"year {year}"


class TestPeriods:
    def test_morning_tomorrow(self):
        res = resolve("明早")
        assert res.status == "resolved"
        assert res.precision == "period_day"
        assert res.granularity == "hourly"
        assert res.period == "morning"
        assert res.period_label == "早晨"
        assert res.start_date == (WED + timedelta(days=1)).isoformat()
        assert res.end_date == res.start_date
        assert res.start_at == f"{res.start_date}T06:00:00"
        assert res.end_at == f"{res.start_date}T09:59:59"

    def test_evening_today(self):
        res = resolve("今晚")
        assert res.period == "evening"
        assert res.start_date == WED.isoformat()
        assert res.start_at == f"{WED.isoformat()}T18:00:00"
        assert res.end_at == f"{WED.isoformat()}T21:59:59"

    def test_evening_tomorrow(self):
        res = resolve("明晚")
        assert res.period == "evening"
        assert res.start_date == (WED + timedelta(days=1)).isoformat()

    def test_afternoon_with_explicit_date(self):
        res = resolve("明天下午")
        assert res.period == "afternoon"
        assert res.start_date == (WED + timedelta(days=1)).isoformat()

    def test_lone_period_means_today(self):
        for expression, label, start_hour, end_hour in [
            ("下午", "afternoon", 12, 15),
            ("凌晨", "dawn", 0, 5),
            ("深夜", "night", 22, 23),
        ]:
            res = resolve(expression)
            assert res.status == "resolved"
            assert res.period == label
            assert res.start_date == WED.isoformat()
            assert res.end_at == f"{WED.isoformat()}T{end_hour:02d}:59:59", expression

    def test_period_window_stays_within_single_day(self):
        # start_at and end_at must share the target date (never cross midnight).
        for expression in ("明早", "今晚", "凌晨", "深夜"):
            res = resolve(expression)
            assert res.start_at[:10] == res.end_at[:10] == res.start_date

    def test_last_night_is_historical(self):
        res = resolve("昨晚")
        assert res.status == "resolved"
        assert res.period == "evening"
        assert res.start_date == (WED - timedelta(days=1)).isoformat()

    def test_period_with_range_uses_range_start(self):
        # 周末晚上 -> the weekend's Saturday evening.
        res = resolve("周末晚上")
        assert res.status == "resolved"
        assert res.period == "evening"
        assert res.start_date == (WED + timedelta(days=3)).isoformat()
        assert res.end_date == res.start_date


class TestGranularityAndPrecision:
    def test_daily_default_granularity(self):
        assert resolve("明天").granularity == "daily"
        assert resolve("周末").granularity == "daily"
        assert resolve("下周").granularity == "daily"
        assert resolve("中秋").granularity == "daily"

    def test_hourly_only_for_periods(self):
        assert resolve("明早").granularity == "hourly"

    def test_precision_values(self):
        assert resolve("后天").precision == "day"
        assert resolve("明早").precision == "period_day"
        assert resolve("下周五").precision == "weekday"
        assert resolve("周末").precision == "weekend"
        assert resolve("下周").precision == "week"
        assert resolve("下个月").precision == "month"
        assert resolve("中秋").precision == "festival"


class TestUnsupported:
    @pytest.mark.parametrize("expression", ["上周", "去年", "abc", "前年"])
    def test_unsupported_expression(self, expression):
        res = resolve(expression)
        assert res.status == "unsupported"
        assert res.error_code == "unsupported_expression"

    def test_empty_expression(self):
        res = resolve("")
        assert res.status == "unsupported"

    def test_resolution_basis(self):
        assert resolve("明天").resolution_basis == "relative_to_request_time"
        assert resolve("周末").resolution_basis == "weekday_calendar"
        assert resolve("中秋").resolution_basis == "festival_table"
        assert resolve("上周").resolution_basis == "unsupported_expression"


class TestMonths:
    def test_month_this_year(self):
        res = resolve("9月")
        assert res.status == "resolved"
        assert res.precision == "month"
        assert res.approximate is True
        assert res.start_date == "2026-09-01"
        assert res.end_date == "2026-09-30"

    def test_month_rolled_forward_when_past(self):
        assert_range(resolve("9月", today=date(2026, 10, 5)), date(2027, 9, 1), date(2027, 9, 30))

    def test_current_month_kept(self):
        assert_range(resolve("9月", today=date(2026, 9, 5)), date(2026, 9, 1), date(2026, 9, 30))

    def test_month_word_variant(self):
        assert_range(resolve("9月份"), date(2026, 9, 1), date(2026, 9, 30))

    def test_month_with_year_prefix(self):
        assert_range(resolve("明年3月"), date(2027, 3, 1), date(2027, 3, 31))
        assert_range(resolve("今年3月"), date(2026, 3, 1), date(2026, 3, 31))
        assert_range(resolve("后年1月"), date(2028, 1, 1), date(2028, 1, 31))

    def test_december(self):
        assert_range(resolve("12月"), date(2026, 12, 1), date(2026, 12, 31))


class TestSeasons:
    def test_full_season_this_year(self):
        res = resolve("夏天")
        assert res.status == "resolved"
        assert res.precision == "season"
        assert res.approximate is True
        assert_range(res, date(2026, 6, 1), date(2026, 8, 31))

    def test_season_alias(self):
        assert_range(resolve("秋季"), date(2026, 9, 1), date(2026, 11, 30))

    def test_season_rolled_forward_when_past(self):
        assert_range(resolve("夏天", today=date(2026, 9, 1)), date(2027, 6, 1), date(2027, 8, 31))

    def test_season_edge_month(self):
        # 盛夏 = 夏中段(7月)，2026-08-12 时已完全过去 -> 推到明年盛夏。
        assert_range(resolve("盛夏"), date(2027, 7, 1), date(2027, 7, 31))
        assert_range(resolve("初秋"), date(2026, 9, 1), date(2026, 9, 30))
        assert_range(resolve("秋末"), date(2026, 11, 1), date(2026, 11, 30))

    def test_winter_spans_year(self):
        res = resolve("冬天")
        assert res.status == "resolved"
        assert_range(res, date(2026, 12, 1), date(2027, 2, 28))

    def test_winter_rolled_forward(self):
        assert_range(resolve("冬天", today=date(2027, 3, 1)), date(2027, 12, 1), date(2028, 2, 29))


class TestYears:
    def test_year_words(self):
        assert_range(resolve("明年"), date(2027, 1, 1), date(2027, 12, 31))
        assert_range(resolve("后年"), date(2028, 1, 1), date(2028, 12, 31))
        res = resolve("今年")
        assert res.approximate is True
        assert_range(res, date(2026, 1, 1), date(2026, 12, 31))

    def test_iso_year(self):
        res = resolve("2028年")
        assert res.status == "resolved"
        assert res.precision == "year"
        assert_range(res, date(2028, 1, 1), date(2028, 12, 31))

    def test_last_year_still_unsupported(self):
        assert resolve("去年").status == "unsupported"


class TestMonthParts:
    def test_late_month_this_month(self):
        res = resolve("月底")
        assert res.status == "resolved"
        assert res.precision == "month_part"
        assert res.approximate is True
        assert_range(res, date(2026, 8, 21), date(2026, 8, 31))

    def test_xiaxun_synonym(self):
        assert_range(resolve("下旬"), date(2026, 8, 21), date(2026, 8, 31))

    def test_mid_month_within_current_segment(self):
        # 2026-08-12 falls inside 中旬 (11-20) -> kept this month.
        assert_range(resolve("中旬"), date(2026, 8, 11), date(2026, 8, 20))

    def test_early_month_rolled_forward_when_past(self):
        # 上旬(1-10) fully passed on 08-12 -> next month's early segment.
        assert_range(resolve("上旬"), date(2026, 9, 1), date(2026, 9, 10))
        assert_range(resolve("月初"), date(2026, 9, 1), date(2026, 9, 10))

    def test_month_part_rolls_year(self):
        # 上旬 already passed on 12-25 -> next January.
        assert_range(resolve("上旬", today=date(2026, 12, 25)), date(2027, 1, 1), date(2027, 1, 10))

    def test_late_month_in_february_uses_month_length(self):
        # 月底 on 2026-02-01 -> Feb 21-28.
        assert_range(resolve("月底", today=date(2026, 2, 1)), date(2026, 2, 21), date(2026, 2, 28))


class TestFarFestivalApproximate:
    def test_festival_within_forecast_window_is_not_approximate(self):
        # 中秋 2026-09-25 is only 5 days out -> real forecast available.
        assert resolve("中秋", today=date(2026, 9, 20)).approximate is False

    def test_far_festival_is_approximate(self):
        # 圣诞(12-25)/国庆(10-01) from 08-12 lie beyond the 16-day horizon.
        assert resolve("圣诞").approximate is True
        assert resolve("国庆").approximate is True

    def test_far_festival_with_period(self):
        res = resolve("圣诞晚上")
        assert res.status == "resolved"
        assert res.approximate is True


class TestApproximateFlag:
    def test_approximate_only_for_qualitative(self):
        assert resolve("9月").approximate is True
        assert resolve("夏天").approximate is True
        assert resolve("明年").approximate is True
        assert resolve("月底").approximate is True
        assert resolve("明天").approximate is False
        assert resolve("周末").approximate is False
        # 中秋 2026-09-25 距 08-12 超预报窗 -> 视为远期精确节日, 也走常识。
        assert resolve("中秋").approximate is True
