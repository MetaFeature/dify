from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import (
    CampusGatewayBinding,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from services.campus.domain import ManagedGatewayToken, ProvisionedWorkspace
from services.campus.errors import StudentSuspendedError
from services.campus.provisioning_service import PlatformProvisioningService


class FakeWorkspaceProvisioner:
    def __init__(self) -> None:
        self.calls = 0

    def provision(self, student_number: str, display_name: str) -> ProvisionedWorkspace:
        self.calls += 1
        return ProvisionedWorkspace(dify_account_id="account-1", dify_tenant_id="tenant-1")


class FakeGatewayProvisioner:
    def __init__(self) -> None:
        self.calls = 0
        self.deleted: list[str] = []

    def create_managed_token(self, external_ref: str, allowance_quota: int) -> ManagedGatewayToken:
        self.calls += 1
        return ManagedGatewayToken(token_id="42", secret="secret-for-dify-only", created=True)

    def delete_managed_token(self, token_id: str) -> None:
        self.deleted.append(token_id)


@dataclass
class FakeModelConfigurator:
    calls: list[tuple[str, str]]
    fail: bool = False

    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None:
        if self.fail:
            raise RuntimeError("plugin unavailable")
        self.calls.append((dify_tenant_id, gateway_secret))


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusWorkspaceBinding.__table__, CampusGatewayBinding.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add(
            CampusStudent(
                student_number="20260001",
                display_name="Student One",
                status=StudentStatus.ACTIVE,
                initial_allowance_yuan=Decimal(20),
            )
        )
        session.commit()
        yield session


def test_lazy_provisioning_creates_one_workspace_and_one_gateway_token(campus_session: Session):
    workspace = FakeWorkspaceProvisioner()
    gateway = FakeGatewayProvisioner()
    configurator = FakeModelConfigurator(calls=[])
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=workspace,
        gateway_provisioner=gateway,
        model_configurator=configurator,
        quota_units_per_yuan=100,
    )
    student = campus_session.query(CampusStudent).one()

    first = service.ensure_ready(student.id)
    second = service.ensure_ready(student.id)

    assert first == second
    assert workspace.calls == 1
    assert gateway.calls == 1
    assert configurator.calls == [("tenant-1", "secret-for-dify-only")]
    assert campus_session.query(CampusWorkspaceBinding).count() == 1
    assert campus_session.query(CampusGatewayBinding).count() == 1


def test_gateway_token_is_compensated_when_dify_configuration_fails(campus_session: Session):
    gateway = FakeGatewayProvisioner()
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=gateway,
        model_configurator=FakeModelConfigurator(calls=[], fail=True),
        quota_units_per_yuan=100,
    )
    student = campus_session.query(CampusStudent).one()

    with pytest.raises(RuntimeError, match="plugin unavailable"):
        service.ensure_ready(student.id)

    assert gateway.deleted == ["42"]
    assert campus_session.query(CampusGatewayBinding).count() == 0


def test_suspended_student_is_not_provisioned(campus_session: Session):
    student = campus_session.query(CampusStudent).one()
    student.status = StudentStatus.SUSPENDED
    campus_session.commit()
    workspace = FakeWorkspaceProvisioner()
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=workspace,
        gateway_provisioner=FakeGatewayProvisioner(),
        model_configurator=FakeModelConfigurator(calls=[]),
        quota_units_per_yuan=100,
    )

    with pytest.raises(StudentSuspendedError):
        service.ensure_ready(student.id)

    assert workspace.calls == 0
