from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import CampusAllowanceAdjustment, CampusGatewayBinding, CampusStudent, StudentStatus
from services.campus.allowance_service import AllowanceService
from services.campus.domain import GatewayUsage, ModelUsage
from services.campus.errors import CampusConflictError


class FakeModelGateway:
    def __init__(self) -> None:
        self.remaining_quota = 2_000
        self.used_quota = 3_000
        self.adjustments: list[tuple[str, int, str]] = []

    def get_usage(self, token_id: str) -> GatewayUsage:
        return GatewayUsage(
            remaining_quota=self.remaining_quota,
            used_quota=self.used_quota,
            by_model=(ModelUsage(model="text-model", quota=self.used_quota, requests=4),),
        )

    def adjust_quota(self, token_id: str, delta_quota: int, request_id: str) -> GatewayUsage:
        self.adjustments.append((token_id, delta_quota, request_id))
        self.remaining_quota += delta_quota
        return self.get_usage(token_id)


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusGatewayBinding.__table__, CampusAllowanceAdjustment.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        student = CampusStudent(student_number="20260001", display_name="Student One", status=StudentStatus.ACTIVE)
        session.add(student)
        session.flush()
        session.add(CampusGatewayBinding(student_id=student.id, gateway_token_id="42"))
        session.commit()
        yield session


def test_student_summary_uses_usd_and_never_exposes_gateway_credentials(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    student = campus_session.query(CampusStudent).one()

    summary = service.get_summary(student.id)

    assert summary.remaining_usd == Decimal("20.0000")
    assert summary.used_usd == Decimal("30.0000")
    assert summary.total_usd == Decimal("50.0000")
    assert summary.by_model[0].model == "text-model"
    assert not hasattr(summary, "token")
    assert not hasattr(summary, "channel")


def test_student_summary_matches_the_four_decimal_gateway_balance_display(campus_session: Session):
    gateway = FakeModelGateway()
    gateway.remaining_quota = 9_991_584
    gateway.used_quota = 8_416
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=500_000)
    student = campus_session.query(CampusStudent).one()

    summary = service.get_summary(student.id)

    assert summary.remaining_usd == Decimal("19.9832")
    assert summary.used_usd == Decimal("0.0168")
    assert summary.total_usd == Decimal("20.0000")
    assert summary.by_model[0].used_usd == Decimal("0.0168")


def test_admin_adjustment_is_idempotent_and_audited(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    student = campus_session.query(CampusStudent).one()

    first = service.adjust(
        student.id,
        delta_usd=Decimal(5),
        reason="course allocation",
        actor_account_id="admin-1",
        request_id="adjustment-1",
    )
    second = service.adjust(
        student.id,
        delta_usd=Decimal(5),
        reason="course allocation",
        actor_account_id="admin-1",
        request_id="adjustment-1",
    )

    assert first.remaining_usd == Decimal("25.0000")
    assert second.remaining_usd == Decimal("25.0000")
    assert gateway.adjustments == [("42", 500, "adjustment-1")]
    assert campus_session.query(CampusAllowanceAdjustment).count() == 1


def test_adjustment_request_id_cannot_be_reused_with_different_parameters(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    student = campus_session.query(CampusStudent).one()
    service.adjust(
        student.id,
        delta_usd=Decimal(5),
        reason="course allocation",
        actor_account_id="admin-1",
        request_id="adjustment-1",
    )

    with pytest.raises(CampusConflictError, match="different parameters"):
        service.adjust(
            student.id,
            delta_usd=Decimal(6),
            reason="course allocation",
            actor_account_id="admin-1",
            request_id="adjustment-1",
        )

    assert gateway.adjustments == [("42", 500, "adjustment-1")]


def test_gate_opens_while_quota_remains_and_closes_when_it_is_spent(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    student = campus_session.query(CampusStudent).one()

    assert service.allows_model_calls(student.id) is True

    gateway.remaining_quota = 0

    assert service.allows_model_calls(student.id) is False


def test_gate_stays_open_for_a_student_without_a_gateway_binding(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    unprovisioned = CampusStudent(
        student_number="20260002", display_name="Student Two", status=StudentStatus.ACTIVE
    )
    campus_session.add(unprovisioned)
    campus_session.commit()

    assert service.allows_model_calls(unprovisioned.id) is True


class FlakyModelGateway(FakeModelGateway):
    """A gateway that refuses one token, to prove one bad row cannot blank a page."""

    def __init__(self, broken_token_id: str) -> None:
        super().__init__()
        self.broken_token_id = broken_token_id

    def get_usage(self, token_id: str) -> GatewayUsage:
        if token_id == self.broken_token_id:
            raise ModelGatewayError("model gateway transport failed")
        return super().get_usage(token_id)


def _student_with_binding(session: Session, *, number: str, token_id: str) -> CampusStudent:
    student = CampusStudent(student_number=number, display_name=number, status=StudentStatus.ACTIVE)
    session.add(student)
    session.flush()
    session.add(CampusGatewayBinding(student_id=student.id, gateway_token_id=token_id))
    session.commit()
    return student


def test_page_summaries_cover_every_bound_student(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    first = campus_session.query(CampusStudent).one()
    second = _student_with_binding(campus_session, number="20260002", token_id="43")

    summaries = service.summaries_for([first.id, second.id])

    assert set(summaries) == {first.id, second.id}
    assert summaries[first.id].remaining_usd == Decimal("20.0000")


def test_page_summaries_skip_students_without_a_binding(campus_session: Session):
    gateway = FakeModelGateway()
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    bound = campus_session.query(CampusStudent).one()
    unprovisioned = CampusStudent(
        student_number="20260009", display_name="Unprovisioned", status=StudentStatus.ACTIVE
    )
    campus_session.add(unprovisioned)
    campus_session.commit()

    summaries = service.summaries_for([bound.id, unprovisioned.id])

    assert set(summaries) == {bound.id}


def test_page_summaries_omit_a_row_the_gateway_refused(campus_session: Session):
    gateway = FlakyModelGateway(broken_token_id="43")
    service = AllowanceService(session=campus_session, gateway=gateway, quota_units_per_usd=100)
    working = campus_session.query(CampusStudent).one()
    broken = _student_with_binding(campus_session, number="20260002", token_id="43")

    summaries = service.summaries_for([working.id, broken.id])

    assert set(summaries) == {working.id}


def test_page_summaries_are_empty_for_an_empty_page(campus_session: Session):
    service = AllowanceService(session=campus_session, gateway=FakeModelGateway(), quota_units_per_usd=100)

    assert service.summaries_for([]) == {}
