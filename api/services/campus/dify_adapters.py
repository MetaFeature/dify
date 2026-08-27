"""Adapters that apply Campus isolation policy through Dify's service layer."""

import hashlib
import logging
import time
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from configs import dify_config
from core.plugin.impl.plugin import PluginInstaller
from core.plugin.plugin_service import PluginService
from models.account import Account, AccountStatus, Tenant, TenantAccountJoin, TenantAccountRole
from models.provider import ProviderCredential
from services.account_service import AccountService, TenantService, TokenPair
from services.campus.domain import ProvisionedWorkspace
from services.campus.errors import CampusProvisioningError
from services.enterprise.rbac_service import RBACService
from services.model_provider_service import ModelProviderService

logger = logging.getLogger(__name__)


class ProviderPluginInstaller(Protocol):
    def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None: ...


class ProviderCredentialService(Protocol):
    def create_provider_credential(
        self,
        tenant_id: str,
        provider: str,
        credentials: dict[str, str],
        credential_name: str | None,
    ) -> None: ...

    def update_provider_credential(
        self,
        tenant_id: str,
        provider: str,
        credentials: dict[str, str],
        credential_id: str,
        credential_name: str | None,
    ) -> None: ...


class MarketplaceProviderPluginInstaller:
    """Install one pinned Marketplace plugin per tenant and wait for visibility."""

    _timeout_seconds: float
    _poll_interval_seconds: float

    def __init__(self, *, timeout_seconds: float = 120, poll_interval_seconds: float = 2) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("plugin installation timing must be positive")
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
        """Install the pinned provider plugin once and wait for the daemon task."""
        plugin_id = plugin_unique_identifier.split(":", 1)[0].strip()
        if not plugin_id or "/" not in plugin_id:
            raise CampusProvisioningError("Campus model provider plugin identifier is invalid")
        try:
            if self._is_installed(tenant_id, plugin_unique_identifier):
                return

            PluginService.install_from_marketplace_pkg(tenant_id, [plugin_unique_identifier])
            deadline = time.monotonic() + self._timeout_seconds
            while time.monotonic() < deadline:
                if self._is_installed(tenant_id, plugin_unique_identifier):
                    return
                time.sleep(self._poll_interval_seconds)
        except CampusProvisioningError:
            raise
        except Exception as error:
            logger.warning(
                "Failed to install Campus model provider plugin. tenant_id=%s plugin_id=%s",
                tenant_id,
                plugin_id,
                exc_info=True,
            )
            raise CampusProvisioningError("Campus model provider plugin installation failed") from error
        raise CampusProvisioningError("Campus model provider plugin installation timed out")

    @staticmethod
    def _is_installed(tenant_id: str, plugin_unique_identifier: str) -> bool:
        return any(
            plugin.plugin_unique_identifier == plugin_unique_identifier
            for plugin in PluginInstaller().list_plugins(tenant_id)
        )


class DifyWorkspaceProvisioner:
    """Create or safely reuse a tenant with exactly one student and one owner."""

    _session: Session
    _service_principal_email: str

    def __init__(self, *, session: Session, service_principal_email: str) -> None:
        self._session = session
        self._service_principal_email = service_principal_email.strip().lower()

    def provision(self, student_number: str, display_name: str) -> ProvisionedWorkspace:
        service_principal = AccountService.get_account_by_email(self._service_principal_email, session=self._session)
        if service_principal is None or service_principal.status is not AccountStatus.ACTIVE:
            raise CampusProvisioningError("Campus service principal is missing or inactive")

        internal_email = self._internal_email(student_number)
        student_account = AccountService.get_account_by_email(internal_email, session=self._session)
        if student_account is None:
            student_account = AccountService.create_account(
                email=internal_email,
                name=display_name,
                interface_language="zh-Hans",
                timezone="Asia/Shanghai",
                is_setup=True,
                session=self._session,
            )

        existing = self._existing_workspace(student_account.id, service_principal.id)
        if existing is not None:
            TenantService.switch_tenant(student_account, existing.id, session=self._session)
            return ProvisionedWorkspace(dify_account_id=student_account.id, dify_tenant_id=existing.id)

        tenant = TenantService.create_owner_tenant(
            service_principal,
            name=f"Student {student_number}",
            is_setup=True,
            session=self._session,
        )
        TenantService.create_tenant_member(tenant, student_account, self._session, role=TenantAccountRole.EDITOR)
        if dify_config.RBAC_ENABLED:
            editor_role_id = AccountService._resolve_legacy_role_id(
                str(tenant.id), service_principal.id, TenantAccountRole.EDITOR
            )
            RBACService.MemberRoles.replace(
                tenant_id=str(tenant.id),
                account_id=service_principal.id,
                member_account_id=student_account.id,
                role_ids=[editor_role_id],
                session=self._session,
            )
        TenantService.switch_tenant(student_account, tenant.id, session=self._session)
        return ProvisionedWorkspace(dify_account_id=student_account.id, dify_tenant_id=tenant.id)

    def _existing_workspace(self, student_account_id: str, service_principal_id: str) -> Tenant | None:
        memberships = self._session.execute(
            select(Tenant, TenantAccountJoin)
            .join(TenantAccountJoin, TenantAccountJoin.tenant_id == Tenant.id)
            .where(TenantAccountJoin.account_id == student_account_id)
        ).all()
        if not memberships:
            return None
        if len(memberships) != 1:
            raise CampusProvisioningError("Campus student Dify account belongs to more than one workspace")
        tenant, student_join = memberships[0]
        owner_id = self._session.scalar(
            select(TenantAccountJoin.account_id).where(
                TenantAccountJoin.tenant_id == tenant.id,
                TenantAccountJoin.role == TenantAccountRole.OWNER,
            )
        )
        if owner_id != service_principal_id or student_join.role != TenantAccountRole.EDITOR:
            raise CampusProvisioningError("Existing Campus workspace ownership or student role is invalid")
        member_ids = set(
            self._session.scalars(
                select(TenantAccountJoin.account_id).where(TenantAccountJoin.tenant_id == tenant.id)
            ).all()
        )
        if member_ids != {student_account_id, service_principal_id}:
            raise CampusProvisioningError("Existing Campus workspace has unexpected human members")
        return tenant

    @staticmethod
    def _internal_email(student_number: str) -> str:
        digest = hashlib.sha256(student_number.encode()).hexdigest()[:32]
        return f"campus-{digest}@invalid.local"


class DifyModelConfigurator:
    """Install the opaque per-workspace gateway credential in Dify."""

    _session: Session
    _provider: str
    _provider_plugin_unique_identifier: str
    _credential_name: str
    _api_key_field: str
    _base_url_field: str
    _base_url: str
    _plugin_installer: ProviderPluginInstaller
    _provider_service: ProviderCredentialService

    def __init__(
        self,
        *,
        session: Session,
        provider: str,
        provider_plugin_unique_identifier: str,
        credential_name: str,
        api_key_field: str,
        base_url_field: str,
        base_url: str,
        plugin_installer: ProviderPluginInstaller | None = None,
        provider_service: ProviderCredentialService | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._provider_plugin_unique_identifier = provider_plugin_unique_identifier
        self._credential_name = credential_name
        self._api_key_field = api_key_field
        self._base_url_field = base_url_field
        self._base_url = base_url
        self._plugin_installer = plugin_installer or MarketplaceProviderPluginInstaller()
        self._provider_service = provider_service or ModelProviderService()

    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None:
        self._plugin_installer.ensure_installed(
            dify_tenant_id,
            self._provider_plugin_unique_identifier,
        )
        credentials = {
            self._api_key_field: gateway_secret,
            self._base_url_field: self._base_url,
        }
        existing = self._session.scalar(
            select(ProviderCredential).where(
                ProviderCredential.tenant_id == dify_tenant_id,
                ProviderCredential.provider_name == self._provider,
                ProviderCredential.credential_name == self._credential_name,
            )
        )
        if existing is None:
            self._provider_service.create_provider_credential(
                tenant_id=dify_tenant_id,
                provider=self._provider,
                credentials=credentials,
                credential_name=self._credential_name,
            )
            return
        self._provider_service.update_provider_credential(
            tenant_id=dify_tenant_id,
            provider=self._provider,
            credentials=credentials,
            credential_id=existing.id,
            credential_name=self._credential_name,
        )


class DifySessionIssuer:
    """Issue a stock Dify console session only for the bound student account."""

    _session: Session

    def __init__(self, *, session: Session) -> None:
        self._session = session

    def issue(self, account_id: str, tenant_id: str, *, ip_address: str | None) -> TokenPair:
        account = self._session.get(Account, account_id)
        if account is None or account.status is not AccountStatus.ACTIVE:
            raise CampusProvisioningError("Campus student Dify account is missing or inactive")
        TenantService.switch_tenant(account, tenant_id, session=self._session)
        return AccountService.login(account, session=self._session, ip_address=ip_address)
