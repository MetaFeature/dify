import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal

from pypinyin import lazy_pinyin
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusStudent, CampusWorkspaceBinding, StudentStatus
from services.campus.credential_service import revoke_portal_sessions, upsert_credential
from services.campus.domain import StudentIdentity, SyncResult
from services.campus.errors import CampusValidationError, StudentNotFoundError, StudentSuspendedError
from services.campus.time_utils import to_naive_utc

_PASSWORD_NAME_SEGMENT = re.compile(r"[^a-z0-9]+")

# ESCAPE for the administration list's `contains` search, so a keyword typed as
# `%` or `_` is looked up literally instead of matching every row.
_LIKE_ESCAPE = "\\"


def _contains_pattern(value: str) -> str:
    escaped = (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


# Surnames whose pinyin letters differ from the most common reading of the same
# character (rime-only differences like 华 hua/huà do not matter here because the
# derived password is toneless). A single character carries no context, so the
# pinyin table alone reads 翟 as "di" instead of the surname reading "zhai".
_SURNAME_READINGS = {
    "翟": "zhai",
    "单": "shan",
    "仇": "qiu",
    "解": "xie",
    "查": "zha",
    "区": "ou",
    "曾": "zeng",
    "覃": "qin",
    "朴": "piao",
    "缪": "miao",
    "乐": "yue",
    "折": "she",
    "盖": "ge",
    "种": "chong",
    "员": "yun",
    "都": "du",
    "繁": "po",
    "隗": "wei",
    "尉": "wei",
}


def derive_initial_password(display_name: str, student_number: str) -> str:
    """Return the derived initial password: name pinyin head plus the last four digits.

    The head is the pinyin of the name's first character (``张三`` → ``zhang``,
    ``欧阳娜娜`` → ``ou``). A name without a Chinese character has no head to
    prefix, so its password is the student number's last four characters alone
    (``Student Nine`` with ``20260009`` → ``0009``); a first character missing
    from the pinyin table falls back to the same suffix rather than failing the
    whole roster import, because the credential still has to be issued.
    """
    if len(student_number) < 4:
        raise CampusValidationError(f"student_number must contain at least four characters: {student_number}")
    suffix = student_number[-4:]
    head = display_name.strip()[:1]
    if not head or not _has_chinese(display_name):
        # Names without a Chinese character have no pinyin head to prefix, so the
        # account's own last four characters are the whole password.
        return suffix
    if head in _SURNAME_READINGS:
        return f"{_SURNAME_READINGS[head]}{suffix}"
    syllables = lazy_pinyin(head)
    if not syllables:
        return suffix
    segment = _PASSWORD_NAME_SEGMENT.sub("", syllables[0].lower())
    return f"{segment}{suffix}" if segment else suffix


def _has_chinese(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


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
                        upsert_credential(
                            self._session,
                            student.id,
                            derive_initial_password(student.display_name, student_number),
                        )
                        default_passwords += 1
            else:
                student.display_name = identity.display_name.strip()
                # A roster that omits 班级 — or leaves a cell blank — must not
                # wipe a class the administrator set elsewhere, so only a real
                # value updates it. New students still start with none.
                if identity.cohort and identity.cohort.strip():
                    student.cohort = identity.cohort.strip()
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
        for identity, number in zip(records, normalized_numbers, strict=True):
            if number not in existing_by_number and not passwords.get(number):
                # Validate the derived credential here so the preview and the
                # sync reject exactly the same rosters.
                derive_initial_password(identity.display_name, number)
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

    def rename(self, student_number: str, *, display_name: str, actor_account_id: str) -> CampusStudent:
        """Change the display name only; the number and everything keyed on it stay put."""
        trimmed = display_name.strip()
        if not trimmed:
            raise CampusValidationError("display_name is required")
        if len(trimmed) > 255:
            raise CampusValidationError("display_name is too long")
        student = self.get_student(student_number)
        previous = student.display_name
        if previous == trimmed:
            return student
        student.display_name = trimmed
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.renamed",
                target_type="student",
                target_id=student.id,
                details_json=json.dumps({"from": previous, "to": trimmed}, separators=(",", ":")),
            )
        )
        self._session.commit()
        return student

    def soft_delete(self, student_number: str, *, actor_account_id: str) -> CampusStudent:
        """Hide a student and sign them out, without destroying anything.

        The row, the credential and the gateway token all survive, so `restore`
        is a one-column change. The retention job is what actually removes data.
        """
        student = self.get_student(student_number)
        if student.deleted_at is not None:
            return student
        now_utc = to_naive_utc(datetime.now(UTC))
        student.deleted_at = now_utc
        revoke_portal_sessions(self._session, student.id, now_utc)
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.deleted",
                target_type="student",
                target_id=student.id,
                details_json=json.dumps({"student_number": student.student_number}, separators=(",", ":")),
            )
        )
        self._session.commit()
        return student

    def restore(self, student_number: str, *, actor_account_id: str) -> CampusStudent:
        """Undo a soft delete. The student can sign in again immediately."""
        student = self.get_student(student_number)
        if student.deleted_at is None:
            return student
        student.deleted_at = None
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.restored",
                target_type="student",
                target_id=student.id,
                details_json=json.dumps({"student_number": student.student_number}, separators=(",", ":")),
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

    def list_cohorts(self) -> list[str]:
        """Every class the active roster mentions, for the per-class view."""
        rows = self._session.scalars(
            select(CampusStudent.cohort)
            .where(CampusStudent.cohort.is_not(None), CampusStudent.deleted_at.is_(None))
            .distinct()
            .order_by(CampusStudent.cohort)
        )
        return [cohort for cohort in rows if cohort]

    def provisioning_progress(self) -> tuple[int, int]:
        """How many roster students exist, and how many have a workspace yet.

        The roster import returns before the workspaces are built (one background
        task per student), so the administration page needs a number to show
        while it waits: a student counts as ready once their workspace binding
        exists, which is the step that needs the Dify account and the models.
        """
        students = int(
            self._session.scalar(
                select(func.count())
                .select_from(CampusStudent)
                .where(CampusStudent.deleted_at.is_(None))
            )
            or 0
        )
        ready = int(
            self._session.scalar(
                select(func.count())
                .select_from(CampusStudent)
                .join(CampusWorkspaceBinding, CampusWorkspaceBinding.student_id == CampusStudent.id)
                .where(CampusStudent.deleted_at.is_(None))
            )
            or 0
        )
        return students, ready

    def list_students(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        keyword: str | None = None,
        cohort: str | None = None,
        include_deleted: bool = False,
        deleted_only: bool = False,
    ) -> list[CampusStudent]:
        """One page of students, narrowed by keyword, class, or deletion state.

        Soft-deleted students stay in the table but are hidden, unless the caller
        asks for them: the deleted view wants exactly those rows (`deleted_only`),
        while `include_deleted` keeps both. `cohort` matches one class exactly, so
        an administrator can work through a roster class by class.
        """
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid pagination")
        statement = select(CampusStudent).order_by(CampusStudent.student_number)
        if deleted_only:
            statement = statement.where(CampusStudent.deleted_at.is_not(None))
        elif not include_deleted:
            statement = statement.where(CampusStudent.deleted_at.is_(None))
        selected_cohort = (cohort or "").strip()
        if selected_cohort:
            statement = statement.where(CampusStudent.cohort == selected_cohort)
        needle = (keyword or "").strip()
        if needle:
            pattern = _contains_pattern(needle)
            statement = statement.where(
                or_(
                    CampusStudent.student_number.ilike(pattern, escape=_LIKE_ESCAPE),
                    CampusStudent.display_name.ilike(pattern, escape=_LIKE_ESCAPE),
                )
            )
        return list(self._session.scalars(statement.limit(limit).offset(offset)).all())
