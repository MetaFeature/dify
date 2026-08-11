import json
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusStudent, StudentStatus
from services.campus.domain import StudentIdentity, SyncResult
from services.campus.errors import CampusValidationError, StudentNotFoundError, StudentSuspendedError


class StudentAdministrationService:
    """Synchronize roster metadata and explicitly manage student lifecycle state."""

    _session: Session
    _default_allowance_yuan: Decimal

    def __init__(self, *, session: Session, default_allowance_yuan: Decimal) -> None:
        if default_allowance_yuan < 0:
            raise ValueError("default_allowance_yuan cannot be negative")
        self._session = session
        self._default_allowance_yuan = default_allowance_yuan

    def sync_students(self, identities: Iterable[StudentIdentity], *, actor_account_id: str) -> SyncResult:
        """Upsert only supplied identities, append one audit event, and commit atomically."""
        records = list(identities)
        normalized_numbers = [record.student_number.strip() for record in records]
        if len(normalized_numbers) != len(set(normalized_numbers)):
            raise CampusValidationError("student_number must be unique in one sync request")

        existing_by_number: dict[str, CampusStudent] = {}
        if normalized_numbers:
            existing_by_number = {
                student.student_number: student
                for student in self._session.scalars(
                    select(CampusStudent).where(CampusStudent.student_number.in_(normalized_numbers))
                ).all()
            }

        created = 0
        updated = 0
        for identity, student_number in zip(records, normalized_numbers, strict=True):
            student = existing_by_number.get(student_number)
            if student is None:
                student = CampusStudent(
                    student_number=student_number,
                    display_name=identity.display_name.strip(),
                    cohort=identity.cohort.strip() if identity.cohort else None,
                    status=StudentStatus.ACTIVE,
                    initial_allowance_yuan=self._default_allowance_yuan,
                )
                self._session.add(student)
                created += 1
            else:
                student.display_name = identity.display_name.strip()
                student.cohort = identity.cohort.strip() if identity.cohort else None
                updated += 1

        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.roster_synced",
                target_type="student_roster",
                target_id="current",
                details_json=json.dumps({"created": created, "updated": updated}, separators=(",", ":")),
            )
        )
        self._session.commit()
        return SyncResult(created=created, updated=updated)

    def set_status(self, student_number: str, status: StudentStatus, *, actor_account_id: str) -> CampusStudent:
        """Lock one identity, persist its explicit status transition and audit, then commit."""
        student = self.get_student(student_number, for_update=True)

        previous_status = student.status
        student.status = status
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.status_changed",
                target_type="student",
                target_id=student.id,
                details_json=json.dumps({"from": previous_status.value, "to": status.value}, separators=(",", ":")),
            )
        )
        self._session.commit()
        return student

    def get_student(self, student_number: str, *, for_update: bool = False) -> CampusStudent:
        """Return one student by stable student number or raise a domain error."""
        statement = select(CampusStudent).where(CampusStudent.student_number == student_number)
        if for_update:
            statement = statement.with_for_update()
        student = self._session.scalar(statement)
        if student is None:
            raise StudentNotFoundError(student_number)
        return student

    def require_active_student(self, student_id: str) -> CampusStudent:
        student = self._session.get(CampusStudent, student_id)
        if student is None:
            raise StudentNotFoundError(student_id)
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)
        return student

    def list_students(self, *, limit: int = 100, offset: int = 0) -> list[CampusStudent]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid pagination")
        return list(
            self._session.scalars(
                select(CampusStudent).order_by(CampusStudent.student_number).limit(limit).offset(offset)
            ).all()
        )
