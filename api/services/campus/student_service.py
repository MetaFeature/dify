import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusStudent, StudentStatus
from services.campus.credential_service import revoke_portal_sessions, upsert_credential
from services.campus.domain import StudentIdentity, SyncResult
from services.campus.errors import CampusValidationError, StudentNotFoundError, StudentSuspendedError
from services.campus.time_utils import to_naive_utc


class StudentAdministrationService:
    """Synchronize roster metadata and explicitly manage student lifecycle state."""

    _session: Session
    _default_allowance_usd: Decimal

    def __init__(self, *, session: Session, default_allowance_usd: Decimal) -> None:
        if default_allowance_usd < 0:
            raise ValueError("default_allowance_usd cannot be negative")
        self._session = session
        self._default_allowance_usd = default_allowance_usd

    def sync_students(
        self,
        identities: Iterable[StudentIdentity],
        *,
        actor_account_id: str,
        passwords: Mapping[str, str] | None = None,
    ) -> SyncResult:
        """Upsert only supplied identities, append one audit event, and commit atomically.

        With ``passwords`` (a student_number → password mapping, possibly empty),
        the sync also manages platform credentials: a supplied password sets or
        resets that student's credential (revoking live portal sessions on a
        reset), an omitted password leaves an existing credential untouched,
        and gives a new student a derived initial credential that must be changed.
        ``passwords=None``
        keeps the legacy metadata-only behavior.
        """
        records = list(identities)
        normalized_numbers = [record.student_number.strip() for record in records]
        if len(normalized_numbers) != len(set(normalized_numbers)):
            raise CampusValidationError("student_number must be unique in one sync request")

        existing_by_number = self._existing_by_number(normalized_numbers)

        created = 0
        updated = 0
        password_resets = 0
        default_passwords = 0
        for identity, student_number in zip(records, normalized_numbers, strict=True):
            student = existing_by_number.get(student_number)
            if student is None:
                student = CampusStudent(
                    student_number=student_number,
                    display_name=identity.display_name.strip(),
                    cohort=identity.cohort.strip() if identity.cohort else None,
                    status=StudentStatus.ACTIVE,
                    initial_allowance_usd=self._default_allowance_usd,
                )
                self._session.add(student)
                self._session.flush()
                created += 1
                if passwords is not None:
                    supplied_password = passwords.get(student_number)
                    if supplied_password:
                        upsert_credential(self._session, student.id, supplied_password)
                    else:
                        if len(student_number) < 4:
                            raise CampusValidationError(
                                f"student_number must contain at least four characters: {student_number}"
                            )
                        upsert_credential(
                            self._session,
                            student.id,
                            student_number[-4:],
                            must_change_password=True,
                        )
                        default_passwords += 1
            else:
                student.display_name = identity.display_name.strip()
                student.cohort = identity.cohort.strip() if identity.cohort else None
                updated += 1
                password = passwords.get(student_number) if passwords is not None else None
                if password:
                    upsert_credential(self._session, student.id, password)
                    revoke_portal_sessions(self._session, student.id, to_naive_utc(datetime.now(UTC)))
                    password_resets += 1

        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.roster_synced",
                target_type="student_roster",
                target_id="current",
                details_json=json.dumps(
                    {
                        "created": created,
                        "updated": updated,
                        "password_resets": password_resets,
                        "default_passwords": default_passwords,
                    },
                    separators=(",", ":"),
                ),
            )
        )
        self._session.commit()
        return SyncResult(
            created=created,
            updated=updated,
            password_resets=password_resets,
            default_passwords=default_passwords,
        )

    def preview_sync(
        self,
        identities: Iterable[StudentIdentity],
        *,
        passwords: Mapping[str, str],
    ) -> SyncResult:
        """Report what a roster import would do — including password resets — without writing."""
        records = list(identities)
        normalized_numbers = [record.student_number.strip() for record in records]
        if len(normalized_numbers) != len(set(normalized_numbers)):
            raise CampusValidationError("student_number must be unique in one sync request")
        existing_by_number = self._existing_by_number(normalized_numbers)
        created = sum(1 for number in normalized_numbers if number not in existing_by_number)
        updated = len(normalized_numbers) - created
        password_resets = sum(
            1 for number in normalized_numbers if number in existing_by_number and passwords.get(number)
        )
        default_passwords = sum(
            1 for number in normalized_numbers if number not in existing_by_number and not passwords.get(number)
        )
        too_short = [number for number in normalized_numbers if number not in existing_by_number and len(number) < 4]
        if too_short:
            raise CampusValidationError(
                f"student_number must contain at least four characters: {', '.join(sorted(too_short))}"
            )
        return SyncResult(
            created=created,
            updated=updated,
            password_resets=password_resets,
            default_passwords=default_passwords,
        )

    def _existing_by_number(self, normalized_numbers: list[str]) -> dict[str, CampusStudent]:
        if not normalized_numbers:
            return {}
        return {
            student.student_number: student
            for student in self._session.scalars(
                select(CampusStudent).where(CampusStudent.student_number.in_(normalized_numbers))
            ).all()
        }

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
