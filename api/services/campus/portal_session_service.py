import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusPortalSession, CampusStudent, StudentStatus
from services.campus.domain import IdentitySource, IssuedPortalSession, PlatformProvisioner
from services.campus.errors import PortalSessionError, StudentNotFoundError, StudentSuspendedError
from services.campus.time_utils import to_naive_utc


class PortalSessionService:
    """Authenticate a roster identity and manage the separate Campus portal session."""

    _session: Session
    _identity_source: IdentitySource
    _platform_provisioner: PlatformProvisioner
    _session_ttl: timedelta
    _token_factory: Callable[[], str]

    def __init__(
        self,
        *,
        session: Session,
        identity_source: IdentitySource,
        platform_provisioner: PlatformProvisioner,
        session_ttl: timedelta,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self._session = session
        self._identity_source = identity_source
        self._platform_provisioner = platform_provisioner
        self._session_ttl = session_ttl
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(48))

    def authenticate(self, subject: str, credential: str, *, now: datetime) -> IssuedPortalSession:
        identity = self._identity_source.authenticate(subject, credential)
        student = self._session.scalar(
            select(CampusStudent).where(CampusStudent.student_number == identity.student_number.strip())
        )
        if student is None:
            raise StudentNotFoundError(identity.student_number)
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)

        self._platform_provisioner.ensure_ready(student.id)
        raw_token = self._token_factory()
        now_utc = to_naive_utc(now)
        expires_at = now_utc + self._session_ttl
        self._session.add(
            CampusPortalSession(
                student_id=student.id,
                token_hash=self._hash_token(raw_token),
                expires_at=expires_at,
                revoked_at=None,
                last_seen_at=now_utc,
            )
        )
        self._session.commit()
        return IssuedPortalSession(
            token=raw_token,
            student_id=student.id,
            expires_at=expires_at.replace(tzinfo=UTC),
        )

    def resolve(self, raw_token: str, *, now: datetime) -> CampusStudent:
        now_utc = to_naive_utc(now)
        portal_session = self._session.scalar(
            select(CampusPortalSession).where(CampusPortalSession.token_hash == self._hash_token(raw_token))
        )
        if portal_session is None or portal_session.revoked_at is not None or portal_session.expires_at <= now_utc:
            raise PortalSessionError("portal session is invalid or expired")
        student = self._session.get(CampusStudent, portal_session.student_id)
        if student is None:
            raise PortalSessionError("portal session student does not exist")
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)
        if portal_session.last_seen_at is None or portal_session.last_seen_at <= now_utc - timedelta(minutes=5):
            portal_session.last_seen_at = now_utc
            self._session.commit()
        return student

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()
