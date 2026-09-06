from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.campus import (
    CampusGatewayBinding,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from services.campus.domain import ManagedGatewayToken, ProvisionedWorkspace
from services.campus.errors import CampusProvisioningError, CampusProvisioningLockError, StudentSuspendedError
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
        self.identities: list[tuple[str, str, str]] = []

    def create_managed_token(
        self, external_ref: str, student_number: str, student_name: str, allowance_quota: int
    ) -> ManagedGatewayToken:
        self.calls += 1
        return ManagedGatewayToken(token_id="42", secret="secret-for-dify-only", created=True)

    def update_managed_identity(self, token_id: str, student_number: str, student_name: str) -> None:
        self.identities.append((token_id, student_number, student_name))

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
    assert gateway.identities == [("42", "20260001", "Student One")]
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


def test_provisioning_translates_a_concurrent_binding_uniqueness_failure(
    campus_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=FakeGatewayProvisioner(),
        model_configurator=FakeModelConfigurator(calls=[]),
        quota_units_per_usd=100,
    )
    student = campus_session.query(CampusStudent).one()

    def reject_binding_commit() -> None:
        raise IntegrityError("INSERT campus_workspace_bindings", {}, Exception("unique conflict"))

    monkeypatch.setattr(campus_session, "commit", reject_binding_commit)

    with pytest.raises(CampusProvisioningError, match="already bound to another Campus student") as raised:
        service.ensure_ready(student.id)

    assert isinstance(raised.value.__cause__, IntegrityError)


class RecordingLockConnection:
    def __init__(self, events: list[str], *, release_result: int = 1, release_error: Exception | None = None) -> None:
        self.events = events
        self.release_result = release_result
        self.release_error = release_error
        self.invalidated = False

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement, parameters) -> None:
        self.events.append(str(statement))

    def scalar(self, statement, parameters) -> int:
        rendered = str(statement)
        self.events.append(rendered)
        if "RELEASE" in rendered:
            if self.release_error is not None:
                raise self.release_error
            return self.release_result
        return 1

    def commit(self) -> None:
        self.events.append("lock.commit")

    def invalidate(self) -> None:
        self.invalidated = True
        self.events.append("lock.invalidate")


class RecordingLockEngine:
    def __init__(
        self,
        dialect: str,
        events: list[str],
        *,
        release_result: int = 1,
        release_error: Exception | None = None,
    ) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.connection = RecordingLockConnection(
            events,
            release_result=release_result,
            release_error=release_error,
        )

    def connect(self) -> RecordingLockConnection:
        return self.connection


class RecordingSession:
    def __init__(self, engine: RecordingLockEngine, events: list[str]) -> None:
        self.engine = engine
        self.bind = engine
        self.events = events
        self.commits = 0
        self.rollbacks = 0

    def get_bind(self, *args: object, **kwargs: object):
        return self.engine

    def commit(self) -> None:
        self.commits += 1
        self.events.append("session.commit")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("session.rollback")


def _recording_lock_service(session: RecordingSession) -> PlatformProvisioningService:
    service = PlatformProvisioningService(
        session=cast(Session, session),
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=FakeGatewayProvisioner(),
        model_configurator=FakeModelConfigurator(calls=[]),
        quota_units_per_usd=100,
    )
    return service


def test_postgresql_provisioning_lock_pins_the_session_connection_across_commits() -> None:
    events: list[str] = []
    engine = RecordingLockEngine("postgresql", events)
    session = RecordingSession(engine, events)
    service = _recording_lock_service(session)

    with service._provisioning_lock("student-1"):
        assert session.get_bind(mapper=object(), clause=object()) is engine.connection
        session.commit()
        assert session.get_bind(mapper=object(), clause=object()) is engine.connection

    assert session.get_bind(mapper=object(), clause=object()) is engine
    assert session.rollbacks == 1
    assert session.commits == 2
    assert events == [
        "session.rollback",
        "SELECT pg_advisory_lock(:key)",
        "lock.commit",
        "session.commit",
        "session.commit",
        "SELECT pg_advisory_unlock(:key)",
        "lock.commit",
    ]


def test_mysql_provisioning_lock_starts_after_the_caller_snapshot() -> None:
    events: list[str] = []
    engine = RecordingLockEngine("mysql", events)
    session = RecordingSession(engine, events)
    service = _recording_lock_service(session)

    with service._provisioning_lock("student-1"):
        assert session.get_bind(mapper=object(), clause=object()) is engine.connection

    assert session.get_bind(mapper=object(), clause=object()) is engine

    assert events[0] == "session.rollback"
    assert "SELECT GET_LOCK(:key, 30)" in events
    assert "SELECT RELEASE_LOCK(:key)" in events
    assert engine.connection.invalidated is False


def test_mysql_failed_unlock_invalidates_the_pooled_connection() -> None:
    events: list[str] = []
    engine = RecordingLockEngine("mysql", events, release_result=0)
    service = _recording_lock_service(RecordingSession(engine, events))

    with pytest.raises(CampusProvisioningLockError, match="could not release"):
        with service._provisioning_lock("student-1"):
            pass

    assert engine.connection.invalidated is True


def test_mysql_unlock_error_preserves_the_body_error_and_invalidates_connection() -> None:
    events: list[str] = []
    engine = RecordingLockEngine("mysql", events, release_error=RuntimeError("database unavailable"))
    service = _recording_lock_service(RecordingSession(engine, events))

    with pytest.raises(ValueError, match="provisioning failed"):
        with service._provisioning_lock("student-1"):
            raise ValueError("provisioning failed")

    assert engine.connection.invalidated is True


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
        self.identities: list[tuple[str, str, str]] = []

    def create_managed_token(
        self, external_ref: str, student_number: str, student_name: str, allowance_quota: int
    ) -> ManagedGatewayToken:
        self.calls += 1
        return ManagedGatewayToken(token_id="42", secret="existing-secret", created=self.created)

    def update_managed_identity(self, token_id: str, student_number: str, student_name: str) -> None:
        self.identities.append((token_id, student_number, student_name))

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
    assert gateway.identities == [("42", "20260001", "Student One")]


def test_first_workspace_provisioning_adopts_the_preprovisioned_model_account(campus_session: Session) -> None:
    student = campus_session.scalars(select(CampusStudent)).one()
    campus_session.add(CampusGatewayBinding(student_id=student.id, gateway_token_id="42"))
    campus_session.commit()
    gateway = FakeExistingTokenGateway()
    configurator = FakeModelConfigurator(calls=[], drifted=True)
    service = PlatformProvisioningService(
        session=campus_session,
        workspace_provisioner=FakeWorkspaceProvisioner(),
        gateway_provisioner=gateway,
        model_configurator=configurator,
        quota_units_per_usd=500_000,
    )

    provisioned = service.ensure_ready(student.id)

    assert provisioned.gateway_token_id == "42"
    assert gateway.calls == 1
    assert gateway.deleted == []
    assert gateway.identities == [("42", "20260001", "Student One")]
    assert configurator.calls == [("tenant-1", "existing-secret")]


def test_existing_workspace_is_left_alone_when_its_model_list_matches(campus_session: Session) -> None:
    configurator = FakeModelConfigurator(calls=[], drifted=False)
    gateway = FakeExistingTokenGateway()
    service = _provisioned_service(campus_session, gateway, configurator)
    student = campus_session.scalars(select(CampusStudent)).one()

    service.ensure_ready(student.id)

    assert configurator.calls == []
    assert gateway.calls == 0
    assert gateway.identities == [("42", "20260001", "Student One")]


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
