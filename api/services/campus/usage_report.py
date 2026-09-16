"""Build, render and export the campus token usage / billing report.

The gateway answers with daily quota per student key and per model; this module
fetches that series for the requested window and turns it into the three tables
the administrator reads, as a printable HTML page and as a workbook.

Money is shown in 元 using the same rule as the rest of the platform: the
gateway's quota units are divided by ``quota_units_per_usd`` and the result is
labelled 元 (see the allowance display note in CODEBUDDY §20 #6). No exchange
rate is involved anywhere.

The campus runs on Asia/Shanghai time, matching the tenant timezone the
provisioner sets, so a "day" in the report is a local day rather than a UTC one.
"""

from __future__ import annotations

import html
import io
from datetime import UTC, date, datetime, timedelta
from typing import Final

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from services.campus.domain import GatewayUsageDay, ModelGateway
from services.campus.usage_report_service import (
    Granularity,
    UsageDay,
    UsageReport,
    UsageReportRow,
    build_usage_report,
    describe_span,
    range_bound,
)

#: Asia/Shanghai. The campus reports on local days, not UTC ones.
CAMPUS_UTC_OFFSET_SECONDS: Final = 8 * 3600

_GRANULARITY_LABELS: Final = {"day": "按日", "month": "按月", "year": "按年"}


class UsageReportService:
    """Fetch one usage window from the gateway and shape it into a report."""

    _gateway: ModelGateway

    def __init__(self, *, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def report(self, granularity: Granularity, *, now: datetime) -> UsageReport:
        offset = CAMPUS_UTC_OFFSET_SECONDS
        today = now.astimezone(UTC).date()
        start, end = describe_span(granularity, today=today)
        series = self._gateway.usage_series(
            range_bound(start, offset_seconds=offset),
            range_bound(end, offset_seconds=offset),
            offset,
        )
        return build_usage_report(
            by_student=_as_days(series.by_token),
            by_model=_as_days(series.by_model),
            granularity=granularity,
            today=today,
            quota_units_per_usd=series.quota_units_per_usd,
        )


def _as_days(entries: tuple[GatewayUsageDay, ...]) -> list[UsageDay]:
    return [
        UsageDay(
            day=entry.day,
            quota=entry.quota,
            requests=entry.requests,
            student_number=entry.student_number,
            student_name=entry.student_name,
            model=entry.model,
        )
        for entry in entries
    ]


def coverage(report: UsageReport, *, now: datetime) -> tuple[date, date]:
    """The inclusive local dates the report covers."""
    today = now.astimezone(UTC).date()
    start, end = describe_span(report.granularity, today=today)
    return start, end - timedelta(days=1)


def render_usage_report_html(report: UsageReport, *, now: datetime) -> str:
    """A standalone, printable report page."""
    label = _GRANULARITY_LABELS[report.granularity]
    generated = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    start, end = coverage(report, now=now)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>token 使用与计费报告 · {label}</title>
<style>
  :root {{ --brand: #4d6bfe; --ink: #1b2430; --muted: #7b8598; --line: rgba(15,23,42,.08); }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 40px 32px 64px; color: var(--ink); background: #f7f9ff;
    font-family: "DM Sans", system-ui, -apple-system, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif; }}
  h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: -.02em; }}
  h2 {{ margin: 40px 0 12px; font-size: 20px; }}
  p.sub {{ margin: 0; color: var(--muted); }}
  .hint {{ margin: 22px 0 0; padding: 14px 18px; border: 1px solid var(--line); border-radius: 14px;
    background: #fff; color: var(--muted); font-size: 13px; }}
  .card {{ padding: 24px; border: 1px solid var(--line); border-radius: 20px; background: #fff; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }}
  thead th {{ color: var(--muted); font-weight: 600; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tfoot td {{ font-weight: 700; }}
  .empty {{ color: var(--muted); }}
  p.note {{ margin: 28px 0 0; color: var(--muted); font-size: 13px; line-height: 1.7; }}
  @media print {{ body {{ background: #fff; padding: 0; }} .bar {{ display: none; }} }}
</style>
</head>
<body>
<h1>token 使用与计费报告 · {label}</h1>
<p class="sub">统计区间 {start} 至 {end}（本地日）　·　生成时间 {generated}</p>
<p class="hint">本页是最终报告，可直接用浏览器的「打印 / 另存为 PDF」保存。
  切换粒度或导出 Excel 请在管理后台的「模型与 API」页签操作。</p>

<h2>一、按时间汇总</h2>
<div class="card"><table>
<thead><tr><th>时间</th><th class="num">调用次数</th><th class="num">金额（元）</th>\
<th class="num">配额单位</th></tr></thead>
<tbody>{_summary_rows(report)}</tbody>
<tfoot><tr><td>合计</td><td class="num">{report.total_requests:,}</td>
<td class="num">{report.to_usd(report.total_quota)}</td><td class="num">{report.total_quota:,}</td></tr></tfoot>
</table></div>

{_matrix_section('二、按用户', report.students, report)}
{_matrix_section('三、按模型', report.models, report)}

<p class="note">
  金额按「配额单位 ÷ {report.quota_units_per_usd:,}」换算并以元显示，与平台其它位置的口径一致，不涉及汇率。
  调用次数取自网关的消费日志，充值与管理类日志不计入。
  区间内没有用量的时段同样列出，便于确认是「没有用量」而不是「漏统计」。
</p>
</body>
</html>"""


def _summary_rows(report: UsageReport) -> str:
    return "".join(
        '<tr><th>{}</th><td class="num">{:,}</td><td class="num">{}</td><td class="num">{:,}</td></tr>'.format(
            html.escape(bucket.key), bucket.requests, report.to_usd(bucket.quota), bucket.quota
        )
        for bucket in report.buckets
    )


def _matrix_section(title: str, rows: tuple[UsageReportRow, ...], report: UsageReport) -> str:
    head = "".join(f'<th class="num">{html.escape(bucket.key)}</th>' for bucket in report.buckets)
    if rows:
        body = "".join(
            '<tr><th>{}</th>{}<td class="num">{}</td></tr>'.format(
                html.escape(row.label),
                "".join(
                    f'<td class="num">{report.to_usd(value) if value else "—"}</td>' for value in row.quotas
                ),
                report.to_usd(row.total),
            )
            for row in rows
        )
    else:
        body = f'<tr><td class="empty" colspan="{len(report.buckets) + 2}">区间内没有用量。</td></tr>'
    return (
        f"<h2>{title}</h2>"
        '<div class="card"><table>'
        f'<thead><tr><th>名称</th>{head}<th class="num">合计（元）</th></tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def render_usage_report_xlsx(report: UsageReport) -> bytes:
    """The same three tables as a workbook, one sheet each."""
    book = Workbook()
    summary = book.active
    summary.title = "汇总"
    summary.append(["时间", "调用次数", "金额（元）", "配额单位"])
    for bucket in report.buckets:
        summary.append([bucket.key, bucket.requests, float(report.to_usd(bucket.quota)), bucket.quota])
    summary.append(["合计", report.total_requests, float(report.to_usd(report.total_quota)), report.total_quota])

    _matrix_sheet(book, "按用户", report.students, report)
    _matrix_sheet(book, "按模型", report.models, report)
    _style_workbook(book)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _matrix_sheet(book: Workbook, title: str, rows: tuple[UsageReportRow, ...], report: UsageReport) -> None:
    sheet = book.create_sheet(title)
    sheet.append(["名称", *[bucket.key for bucket in report.buckets], "合计（元）"])
    for row in rows:
        sheet.append(
            [
                row.label,
                *[float(report.to_usd(value)) if value else 0 for value in row.quotas],
                float(report.to_usd(row.total)),
            ]
        )


def _style_workbook(book: Workbook) -> None:
    for sheet in book.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(1)].width = 28
        for column in range(2, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 16
        sheet.freeze_panes = "B2"
