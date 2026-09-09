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
                ["学号", "姓名", "班级", "密码"],
                [20260001, "王一", "人工智能一班", None],
                ["20260002", "李二", None, "Initial002"],
            ]
        )
    )

    assert [(row.student_number, row.display_name, row.cohort, row.password) for row in rows] == [
        ("20260001", "王一", "人工智能一班", None),
        ("20260002", "李二", None, "Initial002"),
    ]


def test_xlsx_roster_rejects_missing_required_chinese_header() -> None:
    with pytest.raises(CampusValidationError, match="学号 and 姓名"):
        parse_roster_xlsx(_workbook([["学号", "密码"], [20260001, None]]))


def test_xlsx_roster_rejects_duplicate_student_numbers_with_row_number() -> None:
    with pytest.raises(CampusValidationError, match="row 3 repeats student number 20260001"):
        parse_roster_xlsx(_workbook([["学号", "姓名"], [20260001, "王一"], [20260001, "王二"]]))
