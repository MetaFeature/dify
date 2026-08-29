import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import override

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from models.campus import (
    CampusGatewayBinding,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from services.campus.domain import (
    GatewayProvisioner,
    ModelConfigurator,
    PlatformProvisioner,
    ProvisionedPlatform,
    ProvisionedWorkspace,
    WorkspaceProvisioner,
)
from services.campus.errors import CampusProvisioningLockError, StudentNotFoundError, StudentSuspendedError


class PlatformProvisioningService(PlatformProvisioner):
    """Lazily creates the student's isolated Dify workspace and managed gateway token."""

    _session: Session
    _workspace_provisioner: WorkspaceProvisioner
    _gateway_provisioner: GatewayProvisioner
    _model_configurator: ModelConfigurator
    _quota_units_per_usd: int

    def __init__(
        self,
        *,
        session: Session,
        workspace_provisioner: WorkspaceProvisioner,
        gateway_provisioner: GatewayProvisioner,
        model_configurator: ModelConfigurator,
        quota_units_per_usd: int,
    ) -> None:
        if quota_units_per_usd < 1:
            raise ValueError("quota_units_per_usd must be positive")
        self._session = session
        self._workspace_provisioner = workspace_provisioner
        self._gateway_provisioner = gateway_provisioner
        self._model_configurator = model_configurator
        self._quota_units_per_usd = quota_units_per_usd

    @override
    def ensure_ready(self, student_id: str) -> ProvisionedPlatform:
        with self._provisioning_lock(student_id):
            return self._ensure_ready_locked(student_id)

    def _ensure_ready_locked(self, student_id: str) -> ProvisionedPlatform:
        student = self._session.scalar(select(CampusStudent).where(CampusStudent.id == student_id).with_for_update())
        if student is None:
            raise StudentNotFoundError(student_id)
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)

        workspace_binding = self._session.scalar(
            select(CampusWorkspaceBinding).where(CampusWorkspaceBinding.student_id == student.id)
        )
        if workspace_binding is None:
            workspace = self._workspace_provisioner.provision(student.student_number, student.display_name)
            workspace_binding = CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id=workspace.dify_account_id,
                dify_tenant_id=workspace.dify_tenant_id,
            )
            self._session.add(workspace_binding)
            self._session.commit()
        else:
            workspace = ProvisionedWorkspace(
                dify_account_id=workspace_binding.dify_account_id,
                dify_tenant_id=workspace_binding.dify_tenant_id,
            )

        gateway_binding = self._session.scalar(
            select(CampusGatewayBinding).where(CampusGatewayBinding.student_id == student.id)
        )
        if gateway_binding is None:
            allowance_quota = self._allowance_quota(student.initial_allowance_usd)
            managed_token = self._gateway_provisioner.create_managed_token(student.id, allowance_quota)
            try:
                self._model_configurator.configure(workspace.dify_tenant_id, managed_token.secret)
            except Exception:
                self._session.rollback()
                if managed_token.created:
                    self._gateway_provisioner.delete_managed_token(managed_token.token_id)
                raise
            gateway_binding = CampusGatewayBinding(
                student_id=student.id,
                gateway_token_id=managed_token.token_id,
            )
            self._session.add(gateway_binding)
            self._session.commit()

        return ProvisionedPlatform(workspace=workspace, gateway_token_id=gateway_binding.gateway_token_id)

    @contextmanager
    def _provisioning_lock(self, student_id: str) -> Iterator[None]:
        """Serialize provisioning across commits made by Dify's account services."""
        dialect = self._session.get_bind().dialect.name
        lock_key = int.from_bytes(hashlib.sha256(student_id.encode()).digest()[:8], "big", signed=True)
        mysql_lock_name = f"campus:{hashlib.sha256(student_id.encode()).hexdigest()[:40]}"
        if dialect == "postgresql":
            self._session.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        elif dialect == "mysql":
            acquired = self._session.scalar(text("SELECT GET_LOCK(:key, 30)"), {"key": mysql_lock_name})
            if acquired != 1:
                raise CampusProvisioningLockError("timed out waiting for Campus provisioning lock")
        try:
            yield
        except Exception:
            self._session.rollback()
            raise
        finally:
            if dialect == "postgresql":
                self._session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            elif dialect == "mysql":
                self._session.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": mysql_lock_name})

    def _allowance_quota(self, allowance_usd: Decimal) -> int:
        raw_quota = allowance_usd * self._quota_units_per_usd
        if raw_quota != raw_quota.to_integral_value():
            raise ValueError("initial allowance is smaller than gateway quota precision")
        return int(raw_quota)
