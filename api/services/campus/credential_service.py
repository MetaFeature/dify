"""Platform-held student credentials and the managed-first identity source.

A credential row is the authoritative portal login for its student. The
managed-first source consults the credential table before any fallback:
when a row exists, a wrong password fails closed instead of falling through
to the virtual identity source.
"""

import base64
import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from libs.password import compare_password, hash_password, valid_password
from models.campus import CampusPortalSession, CampusStudent, CampusStudentCredential
from services.campus.domain import IdentitySource, StudentIdentity
from services.campus.errors import (
    CampusValidationError,
    CredentialNotFoundError,
    PortalSessionError,
    StudentNotFoundError,
)
from services.campus.time_utils import to_naive_utc


def upsert_credential(session: Session, student_id: str, password: str) -> None:
    """Set or replace one student's credential without committing."""
    salt = secrets.token_bytes(16)
    password_hashed = base64.b64encode(hash_password(password, salt)).decode()
    password_salt = base64.b64encode(salt).decode()
    credential = session.scalar(
        select(CampusStudentCredential).where(CampusStudentCredential.student_id == student_id)
    )
    if credential is None:
        session.add(
            CampusStudentCredential(
                student_id=student_id,
                password_hashed=password_hashed,
                password_salt=password_salt,
            )
        )
    else:
        credential.password_hashed = password_hashed
        credential.password_salt = password_salt


def revoke_portal_sessions(session: Session, student_id: str, now_utc: datetime) -> None:
    """Revoke every live portal session of one student without committing."""
    session.execute(
        update(CampusPortalSession)
        .where(
            CampusPortalSession.student_id == student_id,
            CampusPortalSession.revoked_at.is_(None),
        )
        .values(revoked_at=now_utc)
    )


class StudentCredentialService:
    """Authenticate, set, and change platform-held student passwords."""

    _session: Session

    def __init__(self, *, session: Session) -> None:
        self._session = session

    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        """Verify a student number and password against the credential table.

        Raises:
            CredentialNotFoundError: No credential row exists, so a fallback
                identity source may be consulted.
            PortalSessionError: A row exists and the password does not match;
                this fails closed without fallback.
        """
        student = self._session.scalar(
            select(CampusStudent).where(CampusStudent.student_number == subject.strip())
        )
        if student is None:
            raise CredentialNotFoundError("student has no platform credential")
        row = self._session.scalar(
            select(CampusStudentCredential).where(CampusStudentCredential.student_id == student.id)
        )
        if row is None:
            raise CredentialNotFoundError("student has no platform credential")
        if not compare_password(credential, row.password_hashed, row.password_salt):
            raise PortalSessionError("invalid identity")
        return StudentIdentity(
            student_number=student.student_number,
            display_name=student.display_name,
            cohort=student.cohort,
        )

    def set_password(self, student_number: str, password: str, *, now: datetime) -> None:
        """Reset one student's password and revoke every live portal session."""
        if not password:
            raise CampusValidationError("password is required")
        student = self._session.scalar(
            select(CampusStudent).where(CampusStudent.student_number == student_number.strip())
        )
        if student is None:
            raise StudentNotFoundError(student_number)
        upsert_credential(self._session, student.id, password)
        revoke_portal_sessions(self._session, student.id, to_naive_utc(now))
        self._session.commit()

    def change_password(self, student_id: str, current_password: str, new_password: str) -> None:
        """Let a student replace their own password after proving the current one.

        The strength rule applies only to the student-chosen password, not to
        administrator-issued initial passwords.
        """
        row = self._session.scalar(
            select(CampusStudentCredential).where(CampusStudentCredential.student_id == student_id)
        )
        if row is None:
            raise CampusValidationError("student has no platform credential to change")
        if not compare_password(current_password, row.password_hashed, row.password_salt):
            raise PortalSessionError("current password is incorrect")
        try:
            valid_password(new_password)
        except ValueError as error:
            raise CampusValidationError(str(error)) from error
        upsert_credential(self._session, student_id, new_password)
        self._session.commit()

    def credentialed_student_ids(self, student_ids: list[str]) -> set[str]:
        """Return the subset of the given students that hold a credential row."""
        if not student_ids:
            return set()
        return set(
            self._session.scalars(
                select(CampusStudentCredential.student_id).where(
                    CampusStudentCredential.student_id.in_(student_ids)
                )
            ).all()
        )


class ManagedFirstIdentitySource:
    """Consult platform-held credentials first; fall back only when no row exists."""

    _credentials: StudentCredentialService
    _fallback: IdentitySource

    def __init__(self, credentials: StudentCredentialService, fallback: IdentitySource) -> None:
        self._credentials = credentials
        self._fallback = fallback

    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        try:
            return self._credentials.authenticate(subject, credential)
        except CredentialNotFoundError:
            return self._fallback.authenticate(subject, credential)
