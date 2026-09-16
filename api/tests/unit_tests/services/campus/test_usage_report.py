"""The usage/billing report: bucketing, the three tables, and both renderings."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta

from openpyxl import load_workbook

from services.campus.domain import GatewayUsageDay, GatewayUsageSeries
from services.campus.usage_report import (
    CAMPUS_UTC_OFFSET_SECONDS,
    UsageReportService,
    render_usage_report_html,
    render_usage_report_xlsx,
)
from services.campus.usage_report_service import (
    UNKNOWN_STUDENT_LABEL,
    UsageDay,
    bucket_key,
    bucket_keys_for,
    build_usage_report,
    describe_span,
    parse_granularity,
    range_bound,
)

OFFSET = CAMPUS_UTC_OFFSET_SECONDS
TODAY = date(2026, 9, 15)


def day_seconds(local_date: date) -> int:
    """The gateway's day key for a local date (whole days since the epoch)."""
    return int(datetime(local_date.year, local_date.month, local_date.day, tzinfo=UTC).timestamp())


#: The two local days the fixtures spend on: yesterday and today.
DAY_A = day_seconds(TODAY) - 86400
DAY_B = day_seconds(TODAY)


def test_a_gateway_day_key_is_already_the_campus_date() -> None:
    # The gateway shifts its boundaries, so the key for a local day is that
    # day's UTC midnight. Reading its date must not move it again.
    key = day_seconds(date(2026, 9, 15))

    assert bucket_key(key, "day") == "2026-09-15"
    assert bucket_key(key, "month") == "2026-09"
    assert bucket_key(key, "year") == "2026"
    # The offset lives on the query boundary instead.
    assert range_bound(date(2026, 9, 15), offset_seconds=OFFSET) == key - OFFSET


def test_bucket_windows_are_oldest_first_and_gapless() -> None:
    assert bucket_keys_for("year", today=TODAY, span=3) == ["2024", "2025", "2026"]
    assert bucket_keys_for("month", today=TODAY, span=3) == [
        "2026-07",
        "2026-08",
        "2026-09",
    ]
    # A span crossing the new year still walks back one month at a time.
    assert bucket_keys_for("month", today=date(2026, 2, 3), span=3) == [
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    assert bucket_keys_for("day", today=TODAY, span=3) == [
        "2026-09-13",
        "2026-09-14",
        "2026-09-15",
    ]


def test_the_range_bounds_are_local_midnights() -> None:
    bound = range_bound(date(2026, 9, 15), offset_seconds=OFFSET)

    # Local midnight in UTC+8 is eight hours before UTC midnight.
    assert bound == day_seconds(date(2026, 9, 15)) - OFFSET


def test_describe_span_covers_the_whole_window() -> None:
    start, end = describe_span("day", today=TODAY, span=3)
    assert (start, end) == (date(2026, 9, 13), date(2026, 9, 16))

    start, end = describe_span("month", today=TODAY, span=3)
    assert (start, end) == (date(2026, 7, 1), date(2026, 10, 1))

    start, end = describe_span("year", today=TODAY, span=2)
    assert (start, end) == (date(2025, 1, 1), date(2027, 1, 1))


def test_parse_granularity_falls_back_to_the_daily_view() -> None:
    assert parse_granularity("month") == "month"
    assert parse_granularity(" YEAR ") == "year"
    assert parse_granularity("weekly") == "day"
    assert parse_granularity(None) == "day"


def _report(granularity="day", *, span=3):
    # 100 + 50 on day A, 30 on day B, all from one student and one model.
    students = [
        UsageDay(day=DAY_A, quota=100, requests=1, student_number="20260001", student_name="王一"),
        UsageDay(day=DAY_A, quota=50, requests=2, student_number="20260001", student_name="王一"),
        UsageDay(day=DAY_B, quota=30, requests=1, student_number="20260002", student_name="李二"),
    ]
    models = [
        UsageDay(day=DAY_A, quota=150, requests=3, model="text-model"),
        UsageDay(day=DAY_B, quota=30, requests=1, model="audio-model"),
    ]
    return build_usage_report(
        by_student=students,
        by_model=models,
        granularity=granularity,
        today=TODAY,
        quota_units_per_usd=500_000,
        span=span,
    )


def test_the_summary_counts_each_request_once() -> None:
    report = _report()

    # 180 units in total, not 360: the model dimension must not be added twice.
    assert report.total_quota == 180
    assert report.total_requests == 4
    assert [bucket.quota for bucket in report.buckets] == [0, 150, 30]
    assert sum(row.total for row in report.students) == report.total_quota


def test_month_and_year_roll_the_same_numbers_up() -> None:
    monthly = _report("month")
    yearly = _report("year")

    assert [bucket.key for bucket in monthly.buckets] == ["2026-07", "2026-08", "2026-09"]
    assert monthly.buckets[-1].quota == 180
    assert monthly.total_quota == 180

    assert [bucket.key for bucket in yearly.buckets] == ["2024", "2025", "2026"]
    assert yearly.buckets[-1].quota == 180


def test_rows_are_largest_first_and_keep_their_bucket() -> None:
    report = _report()

    assert [row.label for row in report.students] == ["王一（20260001）", "李二（20260002）"]
    wang = report.students[0]
    assert wang.total == 150
    # Day A (yesterday) carries it all; today is the third bucket.
    assert wang.quotas == (0, 150, 0)
    assert report.models[0].label == "text-model"


def test_a_label_never_repeats_itself_or_loses_an_unattributed_row() -> None:
    report = build_usage_report(
        by_student=[
            # Teacher keys carry the account id in both fields: printing it
            # twice looks like a rendering bug rather than useful information.
            UsageDay(day=DAY_A, quota=80, requests=1, student_number="teacher001", student_name="teacher001"),
            # Platform calls have no owner, but the money was still spent.
            UsageDay(day=DAY_A, quota=20, requests=1, student_number="", student_name=""),
        ],
        by_model=[],
        granularity="day",
        today=TODAY,
        quota_units_per_usd=500_000,
        span=3,
    )

    assert [row.label for row in report.students] == ["teacher001", "（未归属）"]
    assert report.total_quota == 100


def test_usage_outside_the_window_is_ignored() -> None:
    students = [UsageDay(day=DAY_A - 10 * 86400, quota=999, requests=9, student_number="20260009")]
    report = build_usage_report(
        by_student=students,
        by_model=[],
        granularity="day",
        today=TODAY,
        quota_units_per_usd=500_000,
        span=3,
    )

    assert report.total_quota == 0
    assert report.students == ()


def test_money_is_shown_with_the_platforms_quota_rule() -> None:
    report = _report()

    # 500000 quota units are one 元, exactly as the portal labels a balance.
    assert str(report.to_usd(500_000)) == "1.0000"
    assert str(report.to_usd(180)) == "0.0004"


def test_unattributable_spend_gets_its_own_row_so_the_tables_reconcile() -> None:
    # A gateway call made with no campus token is still spend. Hiding it would
    # make the summary disagree with the per-student table.
    report = build_usage_report(
        by_student=[UsageDay(day=DAY_A, quota=10, requests=1)],
        by_model=[],
        granularity="day",
        today=TODAY,
        quota_units_per_usd=500_000,
        span=3,
    )

    assert [row.label for row in report.students] == [UNKNOWN_STUDENT_LABEL]
    assert report.total_quota == 10
    assert sum(row.total for row in report.students) == report.total_quota


class StubGateway:
    def __init__(self, series: GatewayUsageSeries) -> None:
        self.series = series
        self.calls: list[tuple[int, int, int]] = []

    def usage_series(self, start: int, end: int, offset_seconds: int) -> GatewayUsageSeries:
        self.calls.append((start, end, offset_seconds))
        return self.series


def test_the_service_asks_the_gateway_for_the_local_window() -> None:
    gateway = StubGateway(
        GatewayUsageSeries(
            quota_units_per_usd=500_000,
            by_token=(GatewayUsageDay(day=day_seconds(TODAY), quota=12, requests=1, student_number="20260001"),),
            by_model=(GatewayUsageDay(day=day_seconds(TODAY), quota=12, requests=1, model="text-model"),),
        )
    )
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)

    report = UsageReportService(gateway=gateway).report("day", now=now)

    ((start, end, offset),) = gateway.calls
    assert offset == OFFSET
    assert start == range_bound(date(2026, 8, 17), offset_seconds=OFFSET)
    assert end == range_bound(date(2026, 9, 16), offset_seconds=OFFSET)
    assert report.total_quota == 12
    assert report.granularity == "day"


def test_the_html_report_carries_all_three_tables_and_the_export() -> None:
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    page = render_usage_report_html(_report("month"), now=now)

    assert "一、按时间汇总" in page
    assert "二、按用户" in page
    assert "三、按模型" in page
    assert "2026-09" in page
    assert "0.0004" in page
    # The page is the final artefact: it links nowhere, because a top-level
    # navigation cannot carry the console CSRF header (see CODEBUDDY §31).
    assert "href=" not in page
    assert "usage-report.xlsx" not in page
    assert "打印 / 另存为 PDF" in page


def test_the_workbook_has_one_sheet_per_table_and_a_total_row() -> None:
    payload = render_usage_report_xlsx(_report("month"))

    book = load_workbook(io.BytesIO(payload))
    assert book.sheetnames == ["汇总", "按用户", "按模型"]

    summary = book["汇总"]
    assert [cell.value for cell in summary[1]] == ["时间", "调用次数", "金额（元）", "配额单位"]
    assert summary.cell(row=summary.max_row, column=1).value == "合计"
    assert summary.cell(row=summary.max_row, column=4).value == 180

    users = book["按用户"]
    assert [cell.value for cell in users[1]][0] == "名称"
    assert users.cell(row=2, column=1).value == "王一（20260001）"
    assert users.cell(row=2, column=users.max_column).value == 0.0003


def test_an_empty_window_still_renders_both_outputs() -> None:
    empty = build_usage_report(
        by_student=[], by_model=[], granularity="day",
        today=TODAY, quota_units_per_usd=500_000, span=2,
    )
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)

    page = render_usage_report_html(empty, now=now)
    assert "区间内没有用量。" in page
    assert empty.total_quota == 0

    book = load_workbook(io.BytesIO(render_usage_report_xlsx(empty)))
    assert book.sheetnames == ["汇总", "按用户", "按模型"]
    # Two days, a header and a total row.
    assert book["汇总"].max_row == 4


def test_the_default_window_matches_the_granularity() -> None:
    assert describe_span("day", today=TODAY)[0] == TODAY - timedelta(days=29)
    assert describe_span("month", today=TODAY)[0] == date(2025, 10, 1)
    assert describe_span("year", today=TODAY)[0] == date(2022, 1, 1)
