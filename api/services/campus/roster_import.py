"""Parse administrator-uploaded Campus roster workbooks into canonical rows."""

from dataclasses import dataclass
from io import BytesIO
from typing import Final

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from services.campus.errors import CampusValidationError

MAX_ROSTER_BYTES: Final = 5 * 1024 * 1024
MAX_ROSTER_ROWS: Final = 5_000
HEADER_ALIASES: Final = {
    "学号": "student_number",
    "姓名": "display_name",
    "班级": "cohort",
    "密码": "password",
    "student_number": "student_number",
    "display_name": "display_name",
    "cohort": "cohort",
    "password": "password",
}


@dataclass(frozen=True)
class ImportedRosterRow:
    student_number: str
    display_name: str
    cohort: str | None = None
    password: str | None = None


def parse_roster_xlsx(data: bytes) -> list[ImportedRosterRow]:
    """Read the first XLSX worksheet and normalize Chinese or canonical headers."""
    if not data:
        raise CampusValidationError("Roster workbook is empty")
    if len(data) > MAX_ROSTER_BYTES:
        raise CampusValidationError("Roster workbook is larger than 5 MB")
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except (InvalidFileException, OSError, ValueError) as error:
        raise CampusValidationError("Roster workbook is not a valid XLSX file") from error
    try:
        sheet = workbook.active
        if sheet is None:
            raise CampusValidationError("Roster workbook has no active worksheet")
        values = sheet.iter_rows(values_only=True)
        raw_header_values = next(values, None)
        if raw_header_values is None:
            raise CampusValidationError("Roster workbook is empty")
        last_header = max(
            (index for index, value in enumerate(raw_header_values) if value is not None and str(value).strip()),
            default=-1,
        )
        header_values = raw_header_values[: last_header + 1]
        headers = [_header(value) for value in header_values]
        if "student_number" not in headers or "display_name" not in headers:
            raise CampusValidationError("Roster header must contain 学号 and 姓名")
        unknown = [str(value).strip() for value, header in zip(header_values, headers, strict=True) if header is None]
        if unknown:
            raise CampusValidationError(f"Unrecognized roster columns: {', '.join(unknown)}")
        canonical_headers = [header for header in headers if header is not None]
        if len(canonical_headers) != len(set(canonical_headers)):
            raise CampusValidationError("Roster header contains duplicate columns")

        rows: list[ImportedRosterRow] = []
        seen: set[str] = set()
        for row_number, cells in enumerate(values, start=2):
            if all(value is None or str(value).strip() == "" for value in cells):
                continue
            record = {
                header: _cell(cells[index] if index < len(cells) else None)
                for index, header in enumerate(canonical_headers)
            }
            student_number = record["student_number"]
            display_name = record["display_name"]
            if not student_number or not display_name:
                raise CampusValidationError(f"Roster row {row_number} is missing 学号 or 姓名")
            if student_number in seen:
                raise CampusValidationError(f"Roster row {row_number} repeats student number {student_number}")
            seen.add(student_number)
            rows.append(
                ImportedRosterRow(
                    student_number=student_number,
                    display_name=display_name,
                    cohort=record.get("cohort") or None,
                    password=record.get("password") or None,
                )
            )
            if len(rows) > MAX_ROSTER_ROWS:
                raise CampusValidationError(f"Roster workbook contains more than {MAX_ROSTER_ROWS} students")
        if not rows:
            raise CampusValidationError("Roster workbook has no student rows")
        return rows
    finally:
        workbook.close()


def _header(value: object) -> str | None:
    if value is None:
        return None
    return HEADER_ALIASES.get(str(value).strip().lower())


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
