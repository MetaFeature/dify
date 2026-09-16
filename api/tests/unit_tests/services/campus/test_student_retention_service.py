from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    CampusAllowanceAdjustment,
    CampusAuditEvent,
    CampusGatewayBinding,
    CampusPortalSession,
    CampusReservation,
    CampusStudent,
    CampusStudentCredential,
    CampusWorkspaceBinding,
)
from services.campus.credential_service import revoke_portal_sessions
from services.campus.domain import GatewayUsage
from services.campus.student_retention_service import StudentRetentionService


class StubGateway:
    def __init__(self, *, fail_for: str | None = None) -> None:
        self.deleted: list[str] = []
        self.fail_for = fail_for

    def get_usage(self, token_id: str) -> GatewayUsage:  # pragma: no cover - unused here
        raise NotImplementedError

    def adjust_quota(self, token_id: str, delta_quota: int, request_id: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def delete_managed_token(self, token_id: str) -> None:
        if self.fail_for == token_id:
            raise RuntimeError("gateway refused")
        self.deleted.append(token_id)


class RecordingAccountDeleter:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def __call__(self, account_id: str) -> None:
        self.deleted.append(account_id)


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusStudentCredential.__table__,
        CampusWorkspaceBinding.__table__,
        CampusGatewayBinding.__table__,
        CampusAllowanceAdjustment.__table__,
        CampusPortalSession.__table__,
        CampusReservation.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


def _student(session: Session, *, number: str, deleted_at: datetime | None = None) -> CampusStudent:
    student = CampusStudent(student_number=number, display_name=f"Name {number}", deleted_at=deleted_at)
    session.add(student)
    session.commit()
    return student


def _full_student(session: Session, *, number: str, deleted_at: datetime | None = None) -> CampusStudent:
    """A student with rows in every table the purge has to clear."""
    student = _student(session, number=number, deleted_at=deleted_at)
    session.add_all(
        [
            CampusStudentCredential(student_id=student.id, password_hashed="h", password_salt="s"),
            CampusWorkspaceBinding(
                student_id=student.id, dify_account_id=f"acct-{number}", dify_tenant_id=f"t-{number}"
            ),
            CampusGatewayBinding(student_id=student.id, gateway_token_id=f"tok-{number}"),
            CampusAllowanceAdjustment(
                student_id=student.id, request_id=f"req-{number}", delta_usd=Decimal(1), reason="seed",
                actor_account_id="admin-1",
            ),
            CampusReservation(
                student_id=student.id, slot_id="slot-1", status="confirmed",
                queued_at=datetime(2026, 1, 1), queue_sequence=1,
            ),
        ]
    )
    revoke_portal_sessions(session, student.id, datetime(2026, 1, 1))
    session.add(
        CampusPortalSession(
            student_id=student.id, token_hash=f"hash-{number}", expires_at=datetime(2030, 1, 1)
        )
    )
    session.commit()
    return student


def _retention(session: Session, *, gateway: StubGateway | None = None, deleter: RecordingAccountDeleter | None = None,
               retention_days: int = 4 * 365) -> tuple[StudentRetentionService, StubGateway, RecordingAccountDeleter]:
    gateway = gateway or StubGateway()
    deleter = deleter or RecordingAccountDeleter()
    service = StudentRetentionService(
        session=session, gateway=gateway, delete_dify_account=deleter, retention_days=retention_days
    )
    return service, gateway, deleter


# --------------------------------------------------------------- eligibility

def test_only_deletions_older_than_the_window_are_eligible(campus_session: Session):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    recent = _student(campus_session, number="recent", deleted_at=now.replace(tzinfo=None) - timedelta(days=10))
    old = _student(campus_session, number="old", deleted_at=now.replace(tzinfo=None) - timedelta(days=4 * 365 + 1))
    _student(campus_session, number="alive")

    service, _, _ = _retention(campus_session)

    assert [student.id for student in service.eligible(now=now)] == [old.id]
    assert recent.deleted_at is not None


def test_a_purge_removes_every_row_and_hands_the_account_to_dify(campus_session: Session):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    student = _full_student(
        campus_session, number="20200001", deleted_at=now.replace(tzinfo=None) - timedelta(days=5 * 365)
    )
    service, gateway, deleter = _retention(campus_session)

    result = service.purge(now=now, actor_account_id="admin-1")

    assert result.purged == ("20200001",)
    assert result.failed == ()
    assert gateway.deleted == ["tok-20200001"]
    assert deleter.deleted == ["acct-20200001"]
    assert campus_session.get(CampusStudent, student.id) is None
    for table in (
        CampusStudentCredential,
        CampusWorkspaceBinding,
        CampusGatewayBinding,
        CampusAllowanceAdjustment,
        CampusPortalSession,
        CampusReservation,
    ):
        assert campus_session.scalars(select(table).where(table.student_id == student.id)).all() == []


def test_the_purge_keeps_the_audit_trail_it_is_removing(campus_session: Session):
    """The record that a student was deleted is the one thing a purge must not erase."""
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _full_student(campus_session, number="20200001", deleted_at=now.replace(tzinfo=None) - timedelta(days=5 * 365))
    service, _, _ = _retention(campus_session)

    service.purge(now=now, actor_account_id="admin-1")

    actions = [event.action for event in campus_session.scalars(select(CampusAuditEvent)).all()]
    assert "student.retention_purged" in actions


def test_a_student_with_nothing_provisioned_is_still_purged(campus_session: Session):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    student = _student(
        campus_session, number="20200002", deleted_at=now.replace(tzinfo=None) - timedelta(days=5 * 365)
    )
    service, gateway, deleter = _retention(campus_session)

    result = service.purge(now=now, actor_account_id="admin-1")

    assert result.purged == ("20200002",)
    assert gateway.deleted == []
    assert deleter.deleted == []
    assert campus_session.get(CampusStudent, student.id) is None


def test_a_failing_account_deletion_is_reported_and_leaves_the_row_alone(campus_session: Session):
    """One unreachable account must not silently look like a successful purge."""
    now = datetime(2026, 9, 15, tzinfo=UTC)
    student = _full_student(
        campus_session, number="20200003", deleted_at=now.replace(tzinfo=None) - timedelta(days=5 * 365)
    )

    class ExplodingDeleter:
        def __call__(self, account_id: str) -> None:
            raise RuntimeError("dify unreachable")

    retention = StudentRetentionService(
        session=campus_session,
        gateway=StubGateway(),
        delete_dify_account=ExplodingDeleter(),
        retention_days=4 * 365,
    )

    result = retention.purge(now=now, actor_account_id="admin-1")

    assert result.purged == ()
    assert result.failed == (("20200003", "dify unreachable"),)
    assert campus_session.get(CampusStudent, student.id) is not None


def test_a_gateway_that_already_forgot_the_token_does_not_block_the_purge(campus_session: Session):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _full_student(campus_session, number="20200004", deleted_at=now.replace(tzinfo=None) - timedelta(days=5 * 365))
    gateway = StubGateway(fail_for="tok-20200004")
    service, _, _ = _retention(campus_session, gateway=gateway)

    result = service.purge(now=now, actor_account_id="admin-1")

    assert result.purged == ("20200004",)


def test_purge_refuses_a_window_that_would_delete_everything(campus_session: Session):
    with pytest.raises(ValueError, match="retention_days must be positive"):
        StudentRetentionService(
            session=campus_session,
            gateway=StubGateway(),
            delete_dify_account=RecordingAccountDeleter(),
            retention_days=0,
        )
