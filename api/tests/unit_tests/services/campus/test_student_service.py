from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusStudent, StudentStatus
from services.campus.domain import StudentIdentity
from services.campus.errors import CampusValidationError, StudentSuspendedError
from services.campus.student_service import StudentAdministrationService


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusAuditEvent.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


def test_roster_sync_is_idempotent_and_updates_metadata(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    initial = StudentIdentity(student_number="20260001", display_name="Old Name", cohort="2026-A")
    changed = StudentIdentity(student_number="20260001", display_name="New Name", cohort="2026-B")

    first = service.sync_students([initial], actor_account_id="admin-1")
    second = service.sync_students([changed], actor_account_id="admin-1")

    students = list(campus_session.scalars(select(CampusStudent)).all())
    assert first.created == 1
    assert second.created == 0
    assert len(students) == 1
    assert students[0].display_name == "New Name"
    assert students[0].cohort == "2026-B"
    assert students[0].initial_allowance_usd == Decimal(20)


def test_missing_roster_row_does_not_suspend_existing_student(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="Student One")],
        actor_account_id="admin-1",
    )

    service.sync_students([], actor_account_id="admin-1")

    student = campus_session.scalar(select(CampusStudent).where(CampusStudent.student_number == "20260001"))
    assert student is not None
    assert student.status is StudentStatus.ACTIVE


def test_roster_sync_rejects_duplicate_student_numbers_as_domain_validation(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    duplicate = StudentIdentity(student_number="20260001", display_name="Student One")

    with pytest.raises(CampusValidationError, match="unique"):
        service.sync_students([duplicate, duplicate], actor_account_id="admin-1")


def test_suspend_is_explicit_and_audited(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="Student One")],
        actor_account_id="admin-1",
    )

    student = service.set_status("20260001", StudentStatus.SUSPENDED, actor_account_id="admin-2")

    audit = campus_session.scalar(select(CampusAuditEvent).order_by(CampusAuditEvent.created_at.desc()))
    assert student.status is StudentStatus.SUSPENDED
    assert audit is not None
    assert audit.actor_account_id == "admin-2"
    assert audit.action == "student.status_changed"
    with pytest.raises(StudentSuspendedError):
        service.require_active_student(student.id)
