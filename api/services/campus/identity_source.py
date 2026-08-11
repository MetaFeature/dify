"""Identity-source adapters kept deliberately narrow until campus schemas exist.

Virtual identities are the only configured implementation in phase 1. The
named Excel and SSO seams intentionally fail closed so a future integration
cannot silently guess column names, identity claims, or credential semantics.
"""

import json
import secrets
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from services.campus.domain import StudentIdentity
from services.campus.errors import IdentitySourceNotConfiguredError, PortalSessionError


class VirtualIdentityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_number: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    cohort: str | None = Field(default=None, max_length=128)
    login_code: str = Field(min_length=1)


_records_adapter = TypeAdapter(list[VirtualIdentityRecord])


@dataclass(frozen=True)
class UnconfiguredIdentitySource:
    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        raise IdentitySourceNotConfiguredError("identity source is not configured")


@dataclass(frozen=True)
class UnconfiguredExcelRosterSource:
    def load_students(self) -> tuple[StudentIdentity, ...]:
        raise IdentitySourceNotConfiguredError("Excel roster source is not configured")


@dataclass(frozen=True)
class UnconfiguredSsoIdentitySource:
    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        raise IdentitySourceNotConfiguredError("SSO identity source is not configured")


class VirtualIdentitySource:
    _records: dict[str, VirtualIdentityRecord]

    def __init__(self, records_json: str) -> None:
        try:
            payload = json.loads(records_json)
            records = _records_adapter.validate_python(payload)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid CAMPUS_VIRTUAL_IDENTITIES_JSON") from error
        self._records = {record.student_number: record for record in records}
        if len(self._records) != len(records):
            raise ValueError("virtual identity student_number values must be unique")

    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        record = self._records.get(subject.strip())
        if record is None or not secrets.compare_digest(record.login_code, credential):
            raise PortalSessionError("invalid identity")
        return StudentIdentity(
            student_number=record.student_number,
            display_name=record.display_name,
            cohort=record.cohort,
        )
