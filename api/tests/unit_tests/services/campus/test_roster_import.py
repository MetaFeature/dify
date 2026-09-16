from io import BytesIO

import pytest
from openpyxl import Workbook

from services.campus.errors import CampusValidationError
from services.campus.roster_import import parse_roster_xlsx


def _workbook(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_xlsx_roster_preserves_chinese_and_normalizes_headers() -> None:
    rows = parse_roster_xlsx(
        _workbook(
            [
                ["学号", "姓名"],
                [20260001, "王一"],
                ["20260002", "李二"],
            ]
        )
    )

    assert [(row.student_number, row.display_name) for row in rows] == [
        ("20260001", "王一"),
        ("20260002", "李二"),
    ]


def test_xlsx_roster_accepts_canonical_headers() -> None:
    rows = parse_roster_xlsx(_workbook([["student_number", "display_name"], ["20260003", "Wang San"]]))

    assert [(row.student_number, row.display_name) for row in rows] == [("20260003", "Wang San")]


def test_xlsx_roster_reads_the_optional_cohort_column() -> None:
    rows = parse_roster_xlsx(
        _workbook(
            [
                ["学号", "姓名", "班级"],
                [20260001, "王一", "一班"],
                [20260002, "李二", ""],
            ]
        )
    )

    # 学号 and 姓名 are required; a blank 班级 is simply left unset.
    assert [(row.student_number, row.display_name, row.cohort) for row in rows] == [
        ("20260001", "王一", "一班"),
        ("20260002", "李二", None),
    ]


def test_xlsx_roster_without_a_cohort_column_leaves_it_unset() -> None:
    rows = parse_roster_xlsx(_workbook([["学号", "姓名"], [20260001, "王一"]]))

    assert [row.cohort for row in rows] == [None]


def test_xlsx_roster_rejects_a_column_it_does_not_know() -> None:
    # 学号/姓名/班级 is the whole vocabulary; anything else is a mistake in the
    # file rather than something to ignore silently.
    with pytest.raises(CampusValidationError, match="Unrecognized roster columns: 年级"):
        parse_roster_xlsx(_workbook([["学号", "姓名", "年级"], [20260001, "王一", "2026"]]))


def test_xlsx_roster_rejects_missing_required_chinese_header() -> None:
    with pytest.raises(CampusValidationError, match="学号 and 姓名"):
        parse_roster_xlsx(_workbook([["学号", "密码"], [20260001, None]]))


def test_xlsx_roster_rejects_duplicate_student_numbers_with_row_number() -> None:
    with pytest.raises(CampusValidationError, match="row 3 repeats student number 20260001"):
        parse_roster_xlsx(_workbook([["学号", "姓名"], [20260001, "王一"], [20260001, "王二"]]))
