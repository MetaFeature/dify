from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import (
    CampusAuditEvent,
    CampusGatewayBinding,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from services.campus.administration_query_service import CampusAdministrationQueryService
from services.campus.domain import GatewayUsage, ModelUsage, StudentIdentity
from services.campus.student_service import StudentAdministrationService


class FakeModelGateway:
    def get_usage(self, token_id: str) -> GatewayUsage:
        assert token_id == "42"
        return GatewayUsage(
            remaining_quota=2_000,
            used_quota=3_000,
            by_model=(ModelUsage(model="text-model", quota=3_000, requests=4),),
        )

    def adjust_quota(self, token_id: str, delta_quota: int, request_id: str) -> GatewayUsage:
        raise AssertionError("detail queries never adjust quota")


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusWorkspaceBinding.__table__,
        CampusGatewayBinding.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        students = StudentAdministrationService(session=session, default_allowance_yuan=Decimal(20))
        students.sync_students(
            [StudentIdentity(student_number="20260001", display_name="Student One")],
            actor_account_id="admin-1",
        )
        student = students.get_student("20260001")
        session.add(
            CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id="account-1",
                dify_tenant_id="tenant-1",
            )
        )
        session.add(CampusGatewayBinding(student_id=student.id, gateway_token_id="42"))
        session.commit()
        yield session


def test_admin_detail_includes_workspace_and_redacted_allowance(campus_session: Session):
    students = StudentAdministrationService(session=campus_session, default_allowance_yuan=Decimal(20))
    service = CampusAdministrationQueryService(
        session=campus_session,
        students=students,
        gateway=FakeModelGateway(),
        quota_units_per_yuan=100,
    )

    detail = service.get_student_detail("20260001")

    assert detail.status == StudentStatus.ACTIVE
    assert detail.workspace_id == "tenant-1"
    assert detail.allowance is not None
    assert detail.allowance.remaining_yuan == Decimal("20.0000")
    assert not hasattr(detail, "gateway_token_id")
