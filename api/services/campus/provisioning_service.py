import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import override

from sqlalchemy import or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
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
from services.campus.errors import (
    CampusProvisioningError,
    CampusProvisioningLockError,
    StudentNotFoundError,
    StudentSuspendedError,
)


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
            self._require_unbound_workspace(student.id, workspace)
            workspace_binding = CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id=workspace.dify_account_id,
                dify_tenant_id=workspace.dify_tenant_id,
            )
            self._session.add(workspace_binding)
            try:
                self._session.commit()
            except IntegrityError as error:
                self._session.rollback()
                raise CampusProvisioningError(
                    "Dify account or workspace is already bound to another Campus student"
                ) from error
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
        else:
            self._reconcile_models(student, workspace.dify_tenant_id, gateway_binding.gateway_token_id)

        return ProvisionedPlatform(workspace=workspace, gateway_token_id=gateway_binding.gateway_token_id)

    def _require_unbound_workspace(self, student_id: str, workspace: ProvisionedWorkspace) -> None:
        """Reject cross-student account or tenant reuse before persisting a binding.

        Database uniqueness remains the race-safe backstop. This explicit check
        turns an already-visible conflict into a stable domain error before model
        or gateway configuration can touch another student's workspace.
        """
        conflict = self._session.scalar(
            select(CampusWorkspaceBinding).where(
                CampusWorkspaceBinding.student_id != student_id,
                or_(
                    CampusWorkspaceBinding.dify_account_id == workspace.dify_account_id,
                    CampusWorkspaceBinding.dify_tenant_id == workspace.dify_tenant_id,
                ),
            )
        )
        if conflict is not None:
            raise CampusProvisioningError("Dify account or workspace is already bound to another Campus student")

    def _reconcile_models(self, student: CampusStudent, dify_tenant_id: str, gateway_token_id: str) -> None:
        """Bring an already-provisioned workspace up to the configured model list.

        Widening the deployment's model list otherwise leaves existing students
        unable to select the new models: their workspace was configured once, at
        first sign-in, under the list in force back then.
        """
        if not self._model_configurator.needs_configuration(dify_tenant_id):
            return
        allowance_quota = self._allowance_quota(student.initial_allowance_usd)
        managed_token = self._gateway_provisioner.create_managed_token(student.id, allowance_quota)
        if managed_token.created:
            # The bound token is gone, so this is a brand-new one carrying the
            # initial allowance again. Adopting it would silently refund whatever
            # the student had already spent.
            if managed_token.token_id != gateway_token_id:
                self._gateway_provisioner.delete_managed_token(managed_token.token_id)
            raise CampusProvisioningError(
                f"Campus gateway no longer holds the token bound to student {student.student_number}"
            )
        self._model_configurator.configure(dify_tenant_id, managed_token.secret)
        self._session.commit()

    @contextmanager
    def _provisioning_lock(self, student_id: str) -> Iterator[None]:
        """Pin one connection so the advisory lock survives service commits.

        Portal authentication reads through the same ORM session before calling
        provisioning. End that transaction before waiting so MySQL does not keep
        a pre-lock REPEATABLE READ snapshot. Temporarily binding the ORM session
        to the lock connection also avoids consuming two pool slots per login.
        """
        bind = self._session.get_bind()
        dialect = bind.dialect.name
        lock_key = int.from_bytes(hashlib.sha256(student_id.encode()).digest()[:8], "big", signed=True)
        mysql_lock_name = f"campus:{hashlib.sha256(student_id.encode()).hexdigest()[:40]}"
        if dialect not in {"postgresql", "mysql"}:
            try:
                yield
            except Exception:
                self._session.rollback()
                raise
            return

        engine = bind.engine if isinstance(bind, Connection) else bind
        original_bind = self._session.bind
        self._session.rollback()
        with engine.connect() as lock_connection:
            self._session.bind = lock_connection
            acquired = False
            body_error: BaseException | None = None
            release_error: BaseException | None = None
            release_failed = False
            released: object = None
            try:
                if dialect == "postgresql":
                    lock_connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
                else:
                    acquired_result = lock_connection.scalar(
                        text("SELECT GET_LOCK(:key, 30)"), {"key": mysql_lock_name}
                    )
                    if acquired_result != 1:
                        raise CampusProvisioningLockError("timed out waiting for Campus provisioning lock")
                acquired = True
                lock_connection.commit()
                try:
                    yield
                    self._session.commit()
                except BaseException as error:
                    body_error = error
                    self._session.rollback()
                    raise
            finally:
                if acquired:
                    try:
                        if dialect == "postgresql":
                            released = lock_connection.scalar(
                                text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
                            )
                        else:
                            released = lock_connection.scalar(
                                text("SELECT RELEASE_LOCK(:key)"), {"key": mysql_lock_name}
                            )
                        lock_connection.commit()
                    except BaseException as error:
                        release_error = error
                    release_failed = release_error is not None or released != 1
                    if release_failed:
                        lock_connection.invalidate()
                self._session.bind = original_bind
                if acquired and release_failed and body_error is None:
                    raise CampusProvisioningLockError("could not release Campus provisioning lock") from release_error

    def _allowance_quota(self, allowance_usd: Decimal) -> int:
        raw_quota = allowance_usd * self._quota_units_per_usd
        if raw_quota != raw_quota.to_integral_value():
            raise ValueError("initial allowance is smaller than gateway quota precision")
        return int(raw_quota)
