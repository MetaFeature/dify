from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    CampusAuditEvent,
    CampusPortalSession,
    CampusStudent,
    CampusStudentCredential,
    StudentStatus,
)
from services.campus.credential_service import ManagedFirstIdentitySource, StudentCredentialService
from services.campus.domain import StudentIdentity, SyncResult
from services.campus.errors import (
    CampusValidationError,
    CredentialNotFoundError,
    PortalSessionError,
    StudentNotFoundError,
)
from services.campus.student_service import StudentAdministrationService


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusStudentCredential.__table__,
        CampusPortalSession.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add(
            CampusStudent(
                student_number="20260001",
                display_name="Student One",
                status=StudentStatus.ACTIVE,
                initial_allowance_usd=Decimal(0),
            )
        )
        session.commit()
        yield session


def _student(session: Session, student_number: str = "20260001") -> CampusStudent:
    student = session.scalar(select(CampusStudent).where(CampusStudent.student_number == student_number))
    assert student is not None
    return student


NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


def test_set_password_then_authenticate_round_trips(campus_session: Session):
    service = StudentCredentialService(session=campus_session)
    service.set_password("20260001", "Ngc0001", now=NOW)

    identity = service.authenticate("20260001", "Ngc0001")

    assert identity.student_number == "20260001"
    with pytest.raises(PortalSessionError):
        service.authenticate("20260001", "wrong-password")


def test_authenticate_without_credential_row_signals_fallback(campus_session: Session):
    service = StudentCredentialService(session=campus_session)

    with pytest.raises(CredentialNotFoundError):
        service.authenticate("20260001", "anything")
    with pytest.raises(CredentialNotFoundError):
        service.authenticate("99999999", "anything")


def test_set_password_revokes_live_portal_sessions(campus_session: Session):
    student = _student(campus_session)
    campus_session.add(
        CampusPortalSession(
            student_id=student.id,
            token_hash="hash-1",
            expires_at=datetime(2026, 8, 29, 10, 0),
            revoked_at=None,
            last_seen_at=None,
        )
    )
    campus_session.commit()
    service = StudentCredentialService(session=campus_session)

    service.set_password("20260001", "Ngc0001", now=NOW)

    stored = campus_session.scalar(select(CampusPortalSession))
    assert stored is not None
    assert stored.revoked_at is not None


def test_set_password_for_unknown_student_fails(campus_session: Session):
    service = StudentCredentialService(session=campus_session)

    with pytest.raises(StudentNotFoundError):
        service.set_password("99999999", "Ngc0001", now=NOW)


def test_change_password_requires_current_password_and_strength(campus_session: Session):
    student = _student(campus_session)
    service = StudentCredentialService(session=campus_session)
    service.set_password("20260001", "Ngc0001", now=NOW)

    with pytest.raises(PortalSessionError):
        service.change_password(student.id, "wrong", "NewPass1234")
    with pytest.raises(CampusValidationError):
        service.change_password(student.id, "Ngc0001", "short")

    service.change_password(student.id, "Ngc0001", "NewPass1234")
    assert service.authenticate("20260001", "NewPass1234").student_number == "20260001"


def test_managed_source_wins_when_a_credential_row_exists(campus_session: Session):
    class UnexpectedFallback:
        def authenticate(self, subject: str, credential: str) -> StudentIdentity:
            raise AssertionError("fallback must not be consulted when a credential row exists")

    credentials = StudentCredentialService(session=campus_session)
    credentials.set_password("20260001", "Ngc0001", now=NOW)
    source = ManagedFirstIdentitySource(credentials, UnexpectedFallback())

    assert source.authenticate("20260001", "Ngc0001").student_number == "20260001"
    with pytest.raises(PortalSessionError):
        source.authenticate("20260001", "wrong-password")


def test_source_falls_back_only_without_a_credential_row(campus_session: Session):
    class RecordingFallback:
        def authenticate(self, subject: str, credential: str) -> StudentIdentity:
            return StudentIdentity(student_number=subject, display_name="Virtual Student")

    source = ManagedFirstIdentitySource(StudentCredentialService(session=campus_session), RecordingFallback())

    assert source.authenticate("20260001", "virtual-code").display_name == "Virtual Student"


def test_roster_sync_sets_and_resets_credentials(campus_session: Session):
    student = _student(campus_session)
    campus_session.add(
        CampusPortalSession(
            student_id=student.id,
            token_hash="hash-1",
            expires_at=datetime(2026, 8, 29, 10, 0),
            revoked_at=None,
            last_seen_at=None,
        )
    )
    campus_session.commit()
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    credentials = StudentCredentialService(session=campus_session)

    result = service.sync_students(
        [
            StudentIdentity(student_number="20260001", display_name="Student One"),
            StudentIdentity(student_number="20260002", display_name="Student Two"),
        ],
        actor_account_id="admin-1",
        passwords={"20260001": "Ngc0001", "20260002": "Ngc0002"},
    )

    assert result == SyncResult(created=1, updated=1, password_resets=1)
    assert credentials.authenticate("20260001", "Ngc0001").student_number == "20260001"
    assert credentials.authenticate("20260002", "Ngc0002").student_number == "20260002"
    revoked = campus_session.scalar(select(CampusPortalSession))
    assert revoked is not None and revoked.revoked_at is not None


def test_roster_sync_keeps_credentials_when_password_is_omitted(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    credentials = StudentCredentialService(session=campus_session)
    credentials.set_password("20260001", "Ngc0001", now=NOW)

    result = service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="Renamed")],
        actor_account_id="admin-1",
        passwords={},
    )

    assert result.password_resets == 0
    assert credentials.authenticate("20260001", "Ngc0001").student_number == "20260001"


def test_roster_sync_rejects_new_students_without_a_password(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))

    with pytest.raises(CampusValidationError, match="20260009"):
        service.sync_students(
            [StudentIdentity(student_number="20260009", display_name="Student Nine")],
            actor_account_id="admin-1",
            passwords={},
        )


def test_preview_sync_reports_counts_without_writing(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))

    preview = service.preview_sync(
        [
            StudentIdentity(student_number="20260001", display_name="Student One"),
            StudentIdentity(student_number="20260002", display_name="Student Two"),
        ],
        passwords={"20260001": "Ngc0001", "20260002": "Ngc0002"},
    )

    assert preview == SyncResult(created=1, updated=1, password_resets=1)
    assert campus_session.scalar(select(CampusStudentCredential)) is None
    assert (
        campus_session.scalar(select(CampusStudent).where(CampusStudent.student_number == "20260002")) is None
    )
