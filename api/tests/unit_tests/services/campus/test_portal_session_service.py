from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import CampusPortalSession, CampusStudent, StudentStatus
from services.campus.domain import StudentIdentity
from services.campus.errors import (
    IdentitySourceNotConfiguredError,
    PortalSessionError,
    StudentDeletedError,
    StudentSuspendedError,
)
from services.campus.identity_source import UnconfiguredExcelRosterSource, UnconfiguredSsoIdentitySource
from services.campus.portal_session_service import PortalSessionService


class FakeIdentitySource:
    def authenticate(self, subject: str, credential: str) -> StudentIdentity:
        if subject != "20260001" or credential != "valid-code":
            raise PortalSessionError("invalid identity")
        return StudentIdentity(student_number=subject, display_name="Student One")


class FakePlatformProvisioner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure_ready(self, student_id: str):
        self.calls.append(student_id)


def test_real_excel_and_sso_seams_fail_closed_until_schemas_are_known():
    with pytest.raises(IdentitySourceNotConfiguredError, match="Excel roster source is not configured"):
        UnconfiguredExcelRosterSource().load_students()
    with pytest.raises(PortalSessionError, match="SSO identity source is not configured"):
        UnconfiguredSsoIdentitySource().authenticate("student", "credential")


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusPortalSession.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add(
            CampusStudent(
                student_number="20260001",
                display_name="Student One",
                status=StudentStatus.ACTIVE,
                initial_allowance_usd=Decimal(20),
            )
        )
        session.commit()
        yield session


def test_authentication_issues_only_a_hashed_portal_session_and_provisions_lazily(campus_session: Session):
    provisioner = FakePlatformProvisioner()
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=provisioner,
        session_ttl=timedelta(hours=12),
        token_factory=lambda: "raw-portal-token",
    )

    issued = service.authenticate(
        "20260001",
        "valid-code",
        now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
    )

    stored = campus_session.query(CampusPortalSession).one()
    assert issued.token == "raw-portal-token"
    assert stored.token_hash != issued.token
    assert "raw-portal-token" not in stored.token_hash
    assert provisioner.calls == [issued.student_id]


def test_expired_or_suspended_session_fails_closed(campus_session: Session):
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=FakePlatformProvisioner(),
        session_ttl=timedelta(hours=1),
        token_factory=lambda: "raw-portal-token",
    )
    issued = service.authenticate(
        "20260001",
        "valid-code",
        now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(PortalSessionError):
        service.resolve(issued.token, now=datetime(2026, 8, 11, 1, 0, tzinfo=UTC))

    student = campus_session.get(CampusStudent, issued.student_id)
    assert student is not None
    student.status = StudentStatus.SUSPENDED
    campus_session.commit()
    with pytest.raises(StudentSuspendedError):
        service.resolve(issued.token, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))


def test_revoke_ends_the_server_side_portal_session_immediately(campus_session: Session):
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=FakePlatformProvisioner(),
        session_ttl=timedelta(hours=12),
        token_factory=lambda: "raw-portal-token",
    )
    issued = service.authenticate(
        "20260001",
        "valid-code",
        now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
    )
    revoked_at = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)

    assert service.revoke(issued.token, now=revoked_at) is True
    assert service.revoke(issued.token, now=revoked_at) is False

    stored = campus_session.query(CampusPortalSession).one()
    assert stored.revoked_at == revoked_at.replace(tzinfo=None)
    with pytest.raises(PortalSessionError):
        service.resolve(issued.token, now=revoked_at)


def test_authenticate_admits_a_student_whose_allowance_is_exhausted(campus_session: Session):
    """An exhausted student may still sign in to read manuals and book slots.

    Only the Dify workspace is closed to them, and SessionLaunchService
    enforces that separately — so this path must not consult the allowance.
    """
    provisioner = FakePlatformProvisioner()
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=provisioner,
        session_ttl=timedelta(hours=12),
        token_factory=lambda: "raw-portal-token",
    )

    issued = service.authenticate("20260001", "valid-code", now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC))

    assert issued.token == "raw-portal-token"
    assert provisioner.calls == [issued.student_id]
    assert campus_session.query(CampusPortalSession).count() == 1


def test_authenticate_refuses_a_soft_deleted_student(campus_session: Session):
    """A deleted account is not "suspended": it must not even resolve a session."""
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=FakePlatformProvisioner(),
        session_ttl=timedelta(hours=12),
        token_factory=lambda: "raw-portal-token",
    )
    student = campus_session.query(CampusStudent).one()
    student.deleted_at = datetime(2026, 8, 11, 0, 0)
    campus_session.commit()

    with pytest.raises(StudentDeletedError):
        service.authenticate("20260001", "valid-code", now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC))


def test_a_session_issued_before_deletion_stops_resolving(campus_session: Session):
    service = PortalSessionService(
        session=campus_session,
        identity_source=FakeIdentitySource(),
        platform_provisioner=FakePlatformProvisioner(),
        session_ttl=timedelta(hours=12),
        token_factory=lambda: "raw-portal-token",
    )
    issued = service.authenticate("20260001", "valid-code", now=datetime(2026, 8, 11, 0, 0, tzinfo=UTC))
    student = campus_session.get(CampusStudent, issued.student_id)
    student.deleted_at = datetime(2026, 8, 11, 0, 30)
    campus_session.commit()

    with pytest.raises(StudentDeletedError):
        service.resolve(issued.token, now=datetime(2026, 8, 11, 1, 0, tzinfo=UTC))
