from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    CampusGatewayBinding,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from services.campus.domain import ManagedGatewayToken, ProvisionedWorkspace
from services.campus.errors import CampusProvisioningError, StudentSuspendedError
from services.campus.provisioning_service import PlatformProvisioningService


class FakeWorkspaceProvisioner:
    def __init__(self) -> None:
        self.calls = 0

    def provision(self, student_number: str, display_name: str) -> ProvisionedWorkspace:
        self.calls += 1
        return ProvisionedWorkspace(dify_account_id="account-1", dify_tenant_id="tenant-1")


class SharedAccountWorkspaceProvisioner:
    """Simulate a broken Dify boundary returning one account for two students."""

    def provision(self, student_number: str, display_name: str) -> ProvisionedWorkspace:
        return ProvisionedWorkspace(
            dify_account_id="shared-account",
            dify_tenant_id=f"tenant-{student_number}",
        )


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
    drifted: bool = False

    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None:
        if self.fail:
            raise RuntimeError("plugin unavailable")
        self.calls.append((dify_tenant_id, gateway_secret))

    def needs_configuration(self, dify_tenant_id: str) -> bool:
        return self.drifted


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
                initial_allowance_usd=Decimal(20),
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
        quota_units_per_usd=100,
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


def test_provisioning_rejects_account_already_bound_to_another_student(campus_session: Session) -> None:
    gateway = FakeGatewayProvisioner()
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=SharedAccountWorkspaceProvisioner(),
        gateway_provisioner=gateway,
        model_configurator=FakeModelConfigurator(calls=[]),
        quota_units_per_usd=100,
    )
    first_student = campus_session.query(CampusStudent).one()
    service.ensure_ready(first_student.id)
    second_student = CampusStudent(
        student_number="20260002",
        display_name="Student Two",
        status=StudentStatus.ACTIVE,
        initial_allowance_usd=Decimal(20),
    )
    campus_session.add(second_student)
    campus_session.commit()

    with pytest.raises(CampusProvisioningError, match="already bound to another Campus student"):
        service.ensure_ready(second_student.id)

    assert gateway.calls == 1
    assert campus_session.query(CampusWorkspaceBinding).count() == 1


def test_gateway_token_is_compensated_when_dify_configuration_fails(campus_session: Session):
    gateway = FakeGatewayProvisioner()
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=gateway,
        model_configurator=FakeModelConfigurator(calls=[], fail=True),
        quota_units_per_usd=100,
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
        quota_units_per_usd=100,
    )

    with pytest.raises(StudentSuspendedError):
        service.ensure_ready(student.id)

    assert workspace.calls == 0


class FakeExistingTokenGateway:
    """Returns the already-issued token, the way create-or-get does."""

    def __init__(self, *, created: bool = False) -> None:
        self.created = created
        self.calls = 0
        self.deleted: list[str] = []

    def create_managed_token(self, external_ref: str, allowance_quota: int) -> ManagedGatewayToken:
        self.calls += 1
        return ManagedGatewayToken(token_id="42", secret="existing-secret", created=self.created)

    def delete_managed_token(self, token_id: str) -> None:
        self.deleted.append(token_id)


def _provisioned_service(campus_session: Session, gateway, configurator) -> PlatformProvisioningService:
    student = campus_session.scalars(select(CampusStudent)).one()
    campus_session.add_all(
        [
            CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id="account-1",
                dify_tenant_id="tenant-1",
            ),
            CampusGatewayBinding(student_id=student.id, gateway_token_id="42"),
        ]
    )
    campus_session.commit()
    return PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=gateway,
        model_configurator=configurator,
        quota_units_per_usd=500_000,
    )


def test_existing_workspace_is_reconfigured_when_its_model_list_drifted(campus_session: Session) -> None:
    # Widening CAMPUS_MODEL_PROVIDER_MODELS must reach workspaces that were
    # provisioned under the old list, otherwise the new models exist in the
    # gateway but no student can select them.
    configurator = FakeModelConfigurator(calls=[], drifted=True)
    gateway = FakeExistingTokenGateway()
    service = _provisioned_service(campus_session, gateway, configurator)
    student = campus_session.scalars(select(CampusStudent)).one()

    service.ensure_ready(student.id)

    assert configurator.calls == [("tenant-1", "existing-secret")]
    assert gateway.deleted == []


def test_existing_workspace_is_left_alone_when_its_model_list_matches(campus_session: Session) -> None:
    configurator = FakeModelConfigurator(calls=[], drifted=False)
    gateway = FakeExistingTokenGateway()
    service = _provisioned_service(campus_session, gateway, configurator)
    student = campus_session.scalars(select(CampusStudent)).one()

    service.ensure_ready(student.id)

    assert configurator.calls == []
    assert gateway.calls == 0


def test_reconfiguration_refuses_a_freshly_minted_token(campus_session: Session) -> None:
    # A create-or-get that reports "created" means the bound token is gone. Using
    # it would silently restore the student's spent allowance, so fail instead.
    configurator = FakeModelConfigurator(calls=[], drifted=True)
    gateway = FakeExistingTokenGateway(created=True)
    service = _provisioned_service(campus_session, gateway, configurator)
    student = campus_session.scalars(select(CampusStudent)).one()

    with pytest.raises(CampusProvisioningError, match="no longer holds"):
        service.ensure_ready(student.id)

    assert configurator.calls == []
