"""Turn the gateway's daily usage numbers into the three report tables.

The gateway reports one row per local day, per student key and per model. This
module rolls those days up into the granularity the administrator asked for and
shapes them into:

* a summary table — one row per bucket;
* a per-student table — one row per student key;
* a per-model table — one row per model.

The cell values stay in quota units here; the rendering layer converts them for
display so the arithmetic has a single source of truth. Bucketing is a pure
function of the input series, which is what makes month and year totals
checkable rather than merely plausible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

Granularity = Literal["day", "month", "year"]

GRANULARITIES: Final[tuple[Granularity, ...]] = ("day", "month", "year")

#: How many buckets each granularity shows by default, counting back from today.
DEFAULT_SPAN: Final[dict[str, int]] = {"day": 30, "month": 12, "year": 5}

#: Spend the gateway cannot attribute to a student — a call made with no campus
#: token, or one whose label was never set. It is still money spent, so it gets
#: its own row: dropping it would make the summary disagree with the table.
UNKNOWN_STUDENT_LABEL: Final = "（未归属）"


@dataclass(frozen=True)
class UsageDay:
    """One gateway day row, already reduced to quota units."""

    day: int
    quota: int
    requests: int
    student_number: str = ""
    student_name: str = ""
    model: str = ""


@dataclass(frozen=True)
class UsageBucket:
    key: str
    quota: int
    requests: int


@dataclass(frozen=True)
class UsageReportRow:
    label: str
    #: Aligned with the report's buckets; missing buckets are zero.
    quotas: tuple[int, ...]
    requests: int

    @property
    def total(self) -> int:
        return sum(self.quotas)


@dataclass(frozen=True)
class UsageReport:
    granularity: Granularity
    buckets: tuple[UsageBucket, ...]
    students: tuple[UsageReportRow, ...]
    models: tuple[UsageReportRow, ...]
    quota_units_per_usd: int

    @property
    def total_quota(self) -> int:
        return sum(bucket.quota for bucket in self.buckets)

    @property
    def total_requests(self) -> int:
        return sum(bucket.requests for bucket in self.buckets)

    def to_usd(self, quota: int) -> Decimal:
        """Quota units as the amount the platform shows (see the 元 display rule)."""
        if self.quota_units_per_usd <= 0:
            return Decimal(0)
        return (Decimal(quota) / Decimal(self.quota_units_per_usd)).quantize(Decimal("0.0001"))


def bucket_key(day: int, granularity: Granularity) -> str:
    """The bucket a gateway day key belongs to.

    The gateway already shifted its day boundaries, so a key's own UTC date *is*
    the campus date. Subtracting the offset here would move every figure a day
    earlier than the day it was actually spent on.
    """
    local = datetime.fromtimestamp(day, tz=UTC).date()
    if granularity == "year":
        return f"{local.year:04d}"
    if granularity == "month":
        return f"{local.year:04d}-{local.month:02d}"
    return local.isoformat()


def bucket_keys_for(granularity: Granularity, *, today: date, span: int | None = None) -> list[str]:
    """The buckets to show, oldest first, so an empty period is still visible."""
    count = span or DEFAULT_SPAN[granularity]
    keys: list[str] = []
    if granularity == "year":
        keys = [f"{today.year - back:04d}" for back in range(count - 1, -1, -1)]
    elif granularity == "month":
        cursor = date(today.year, today.month, 1)
        for _ in range(count):
            keys.append(f"{cursor.year:04d}-{cursor.month:02d}")
            cursor = date(cursor.year - 1, 12, 1) if cursor.month == 1 else date(cursor.year, cursor.month - 1, 1)
        keys.reverse()
    else:
        keys = [(today - timedelta(days=back)).isoformat() for back in range(count - 1, -1, -1)]
    return keys


def build_usage_report(
    *,
    by_student: Iterable[UsageDay],
    by_model: Iterable[UsageDay],
    granularity: Granularity,
    today: date,
    quota_units_per_usd: int,
    span: int | None = None,
) -> UsageReport:
    """Roll the gateway's daily rows up into the three tables.

    The summary comes from the student dimension alone: every consumed
    request belongs to exactly one token, so summing tokens counts each
    request once. Adding the model dimension as well would double every
    figure in the report.
    """
    keys = bucket_keys_for(granularity, today=today, span=span)
    position = {key: index for index, key in enumerate(keys)}

    summary = [[0, 0] for _ in keys]  # quota, requests, per bucket
    students: dict[str, list[int]] = {}
    models: dict[str, list[int]] = {}

    def index_of(day: int) -> int | None:
        return position.get(bucket_key(day, granularity))

    for row in by_student:
        index = index_of(row.day)
        if index is None:
            continue
        summary[index][0] += row.quota
        summary[index][1] += row.requests
        label = _student_label(row) or UNKNOWN_STUDENT_LABEL
        students.setdefault(label, [0] * len(keys))[index] += row.quota

    for row in by_model:
        index = index_of(row.day)
        if index is None or not row.model:
            continue
        models.setdefault(row.model, [0] * len(keys))[index] += row.quota

    return UsageReport(
        granularity=granularity,
        buckets=tuple(
            UsageBucket(key=key, quota=summary[index][0], requests=summary[index][1])
            for index, key in enumerate(keys)
        ),
        students=_rows(students),
        models=_rows(models),
        quota_units_per_usd=quota_units_per_usd,
    )


def _student_label(row: UsageDay) -> str:
    name = row.student_name.strip()
    number = row.student_number.strip()
    # Teacher keys carry the account id as both fields; repeating it reads like
    # a rendering bug, so the parenthetical is only added when it says something.
    if name and number and name != number:
        return f"{name}（{number}）"
    return name or number


def _rows(values: dict[str, list[int]]) -> tuple[UsageReportRow, ...]:
    """Rows largest-total first, which is the order an administrator reads."""
    rows = [UsageReportRow(label=label, quotas=tuple(quotas), requests=0) for label, quotas in values.items()]
    return tuple(sorted(rows, key=lambda row: (-row.total, row.label)))


def describe_span(granularity: Granularity, *, today: date, span: int | None = None) -> tuple[date, date]:
    """The half-open [start, end) date range a report of this granularity covers."""
    count = span or DEFAULT_SPAN[granularity]
    if granularity == "year":
        start = date(today.year - count + 1, 1, 1)
        end = date(today.year + 1, 1, 1)
    elif granularity == "month":
        months = (today.year * 12 + today.month - 1) - (count - 1)
        start = date(months // 12, months % 12 + 1, 1)
        end = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
    else:
        start = today - timedelta(days=count - 1)
        end = today + timedelta(days=1)
    return start, end


def range_bound(day: date, *, offset_seconds: int) -> int:
    """The timestamp of local midnight on a date, for the gateway's range.

    The gateway compares raw timestamps, and its day keys are shifted by the
    same offset — so a local day starts ``offset`` *before* its UTC midnight.
    """
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) - offset_seconds


def parse_granularity(value: str | None) -> Granularity:
    """Default to the daily view: it is the one with the most detail."""
    normalized = (value or "").strip().lower()
    return normalized if normalized in GRANULARITIES else "day"  # type: ignore[return-value]
