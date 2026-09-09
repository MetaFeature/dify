"""Adapters that apply Campus isolation policy through Dify's service layer."""

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from configs import dify_config
from configs.extra.campus_config import ModelApiProtocol, ModelCredentialScope
from core.plugin.impl.plugin import PluginInstaller
from core.plugin.plugin_service import PluginService
from graphon.model_runtime.entities.model_entities import ModelType
from models.account import Account, AccountStatus, Tenant, TenantAccountJoin, TenantAccountRole
from models.provider import ProviderCredential, ProviderModelCredential
from services.account_service import AccountService, TenantService, TokenPair
from services.campus.domain import ProvisionedWorkspace
from services.campus.errors import CampusProvisioningError, CampusValidationError
from services.enterprise.rbac_service import RBACService
from services.model_provider_service import ModelProviderService

logger = logging.getLogger(__name__)

OPENAI_COMPATIBLE_MODEL_TYPES = frozenset(
    {ModelType.LLM, ModelType.RERANK, ModelType.TEXT_EMBEDDING, ModelType.SPEECH2TEXT, ModelType.TTS}
)
OPENAI_COMPATIBLE_DEFAULT_CONTEXT_SIZE = "4096"


@dataclass(frozen=True)
class CampusModel:
    """One gateway model exposed inside every student workspace."""

    name: str
    model_type: ModelType


def parse_campus_models(spec: str) -> tuple[CampusModel, ...]:
    """Parse a ``type:name`` comma-separated model spec.

    A deployment lists the gateway models students may use; the type decides
    which Dify model slot each one fills, so a knowledge base gets a real
    embedding model instead of a second chat model. The platform requires an
    LLM track, and anything unroutable is rejected before a student's sign-in.
    """
    models: list[CampusModel] = []
    seen: set[tuple[str, str]] = set()
    for entry in (part.strip() for part in spec.split(",")):
        if not entry:
            continue
        raw_type, separator, raw_name = entry.partition(":")
        if not separator or not raw_name.strip():
            raise CampusValidationError(f"Campus model entry must be written as type:name, got {entry!r}")
        name = raw_name.strip()
        try:
            model_type = ModelType(raw_type.strip().lower())
        except ValueError:
            raise CampusValidationError(f"Campus model entry has an unknown model type: {entry!r}") from None
        if model_type not in OPENAI_COMPATIBLE_MODEL_TYPES:
            raise CampusValidationError(
                f"Campus OpenAI-compatible provider does not support model type: {model_type.value!r}"
            )
        key = (model_type.value, name)
        if key in seen:
            raise CampusValidationError(f"Campus model list has a duplicate entry: {entry!r}")
        seen.add(key)
        models.append(CampusModel(name=name, model_type=model_type))
    if not models:
        raise CampusValidationError("Campus model list must name at least one model")
    if not any(model.model_type is ModelType.LLM for model in models):
        raise CampusValidationError("Campus model list must name at least one llm to validate credentials against")
    return tuple(models)


class ProviderPluginInstaller(Protocol):
    def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None: ...


type ModelCredentialPayload = dict[str, str]


class ModelProviderCredentialService(Protocol):
    def create_provider_credential(
        self,
        tenant_id: str,
        provider: str,
        credentials: ModelCredentialPayload,
        credential_name: str | None,
    ) -> None: ...

    def update_provider_credential(
        self,
        tenant_id: str,
        provider: str,
        credentials: ModelCredentialPayload,
        credential_id: str,
        credential_name: str | None,
    ) -> None: ...

    def create_model_credential(
        self,
        tenant_id: str,
        provider: str,
        model_type: str,
        model: str,
        credentials: ModelCredentialPayload,
        credential_name: str | None,
    ) -> None: ...

    def update_model_credential(
        self,
        tenant_id: str,
        provider: str,
        model_type: str,
        model: str,
        credentials: ModelCredentialPayload,
        credential_id: str,
        credential_name: str | None,
    ) -> None: ...


class MarketplaceProviderPluginInstaller:
    """Install one pinned provider plugin per tenant and wait for visibility.

    A student's first sign-in provisions their workspace, so reaching the public
    Marketplace on that path makes every new student depend on outbound network
    at the worst possible moment. When a local package is configured it is used
    instead, and a package that is missing or does not decode to the pinned
    identifier fails loudly rather than silently falling back to the network.
    """

    _timeout_seconds: float
    _poll_interval_seconds: float
    _local_package_path: str

    def __init__(
        self,
        *,
        timeout_seconds: float = 120,
        poll_interval_seconds: float = 2,
        local_package_path: str = "",
    ) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("plugin installation timing must be positive")
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._local_package_path = local_package_path.strip()

    def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
        """Install the pinned provider plugin once and wait for the daemon task."""
        plugin_id = plugin_unique_identifier.split(":", 1)[0].strip()
        if not plugin_id or "/" not in plugin_id:
            raise CampusProvisioningError("Campus model provider plugin identifier is invalid")
        try:
            if self._is_installed(tenant_id, plugin_unique_identifier):
                return

            self._install(tenant_id, plugin_unique_identifier)
            deadline = time.monotonic() + self._timeout_seconds
            while time.monotonic() < deadline:
                if self._is_installed(tenant_id, plugin_unique_identifier):
                    return
                time.sleep(self._poll_interval_seconds)
        except CampusProvisioningError:
            raise
        except Exception as error:
            logger.error(
                "Failed to install Campus model provider plugin. tenant_id=%s plugin_id=%s",
                tenant_id,
                plugin_id,
                exc_info=True,
            )
            raise CampusProvisioningError("Campus model provider plugin installation failed") from error
        logger.error(
            "Timed out installing Campus model provider plugin. tenant_id=%s plugin_id=%s",
            tenant_id,
            plugin_id,
        )
        raise CampusProvisioningError("Campus model provider plugin installation timed out")

    def _install(self, tenant_id: str, plugin_unique_identifier: str) -> None:
        if not self._local_package_path:
            PluginService.install_from_marketplace_pkg(tenant_id, [plugin_unique_identifier])
            return
        self._install_from_local_package(tenant_id, plugin_unique_identifier)

    def _install_from_local_package(self, tenant_id: str, plugin_unique_identifier: str) -> None:
        package = Path(self._local_package_path)
        if not package.is_file():
            raise CampusProvisioningError(
                f"Campus local provider plugin package is missing at {self._local_package_path}"
            )
        decoded = PluginService.upload_pkg(tenant_id, package.read_bytes())
        # The identifier embeds the package checksum, so an identifier match is
        # also an integrity check on the file that was shipped with the deployment.
        if decoded.unique_identifier != plugin_unique_identifier:
            raise CampusProvisioningError("Campus local provider plugin package does not match the pinned identifier")
        PluginService.install_from_local_pkg(tenant_id, [plugin_unique_identifier])

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
    """Install the plugin and upsert the workspace's opaque gateway credentials.

    The approved OpenAI-compatible plugin has only model-level credentials, so
    every Campus model is saved as a tenant custom model with the gateway token
    and endpoint. Server-side provisioning must include the schema's required
    context and batching defaults because it does not pass through the UI form
    that normally supplies them. Provider scope remains available solely for
    rollback to a plugin that declares a provider credential schema.
    """

    _session: Session
    _provider: str
    _provider_plugin_unique_identifier: str
    _credential_name: str
    _credential_scope: ModelCredentialScope
    _api_key_field: str
    _base_url_field: str
    _base_url: str
    _models: tuple[CampusModel, ...]
    _api_protocol: ModelApiProtocol
    _plugin_installer: ProviderPluginInstaller
    _provider_service: ModelProviderCredentialService

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
        models: Sequence[CampusModel],
        api_protocol: ModelApiProtocol,
        credential_scope: ModelCredentialScope = "provider",
        plugin_package_path: str = "",
        plugin_installer: ProviderPluginInstaller | None = None,
        provider_service: ModelProviderCredentialService | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._provider_plugin_unique_identifier = provider_plugin_unique_identifier
        self._credential_name = credential_name
        self._credential_scope = credential_scope
        self._api_key_field = api_key_field
        self._base_url_field = base_url_field
        self._base_url = base_url
        self._models = tuple(models)
        self._api_protocol = api_protocol
        self._plugin_installer = plugin_installer or MarketplaceProviderPluginInstaller(
            local_package_path=plugin_package_path
        )
        self._provider_service = provider_service or ModelProviderService()

    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None:
        self._plugin_installer.ensure_installed(
            dify_tenant_id,
            self._provider_plugin_unique_identifier,
        )
        if self._credential_scope == "provider":
            self._upsert_provider_credential(dify_tenant_id, gateway_secret)

        registered = self._registered_models(dify_tenant_id)
        for model in self._models:
            model_credentials = self._model_credentials(model, gateway_secret)
            self._upsert_model_credential(model, model_credentials, dify_tenant_id, registered)
        self._synchronize_default_speech_model(dify_tenant_id)

    def _upsert_provider_credential(self, dify_tenant_id: str, gateway_secret: str) -> None:
        """Persist a provider credential only for rollback plugins that declare one."""
        provider_credentials = {
            self._api_key_field: gateway_secret,
            self._base_url_field: self._base_url,
            "validate_model": self._validate_model(),
            "api_protocol": self._api_protocol,
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
                credentials=provider_credentials,
                credential_name=self._credential_name,
            )
        else:
            self._provider_service.update_provider_credential(
                tenant_id=dify_tenant_id,
                provider=self._provider,
                credentials=provider_credentials,
                credential_id=existing.id,
                credential_name=self._credential_name,
            )

    def _model_credentials(self, model: CampusModel, gateway_secret: str) -> ModelCredentialPayload:
        """Build the credential schema expected by the configured plugin scope."""
        credentials = {
            self._api_key_field: gateway_secret,
            self._base_url_field: self._base_url,
        }
        if self._credential_scope == "provider":
            credentials["api_protocol"] = self._api_protocol
        elif model.model_type is ModelType.LLM:
            credentials["mode"] = "chat"
            credentials["api_type"] = "responses" if self._api_protocol == "responses" else "chat_completions"
            credentials["context_size"] = OPENAI_COMPATIBLE_DEFAULT_CONTEXT_SIZE
        elif model.model_type is ModelType.TEXT_EMBEDDING:
            credentials["max_chunks"] = "1"
            credentials["context_size"] = OPENAI_COMPATIBLE_DEFAULT_CONTEXT_SIZE
        elif model.model_type is ModelType.RERANK:
            credentials["context_size"] = OPENAI_COMPATIBLE_DEFAULT_CONTEXT_SIZE
        elif model.model_type is ModelType.SPEECH2TEXT:
            credentials["language"] = "zh"
        return credentials

    def needs_configuration(self, dify_tenant_id: str) -> bool:
        """Report whether this workspace is missing any configured model.

        Answering in one query keeps this cheap enough to ask on every sign-in,
        which is what lets a widened model list reach existing workspaces without
        a migration step.
        """
        registered = self._registered_models(dify_tenant_id)
        if any((model.name, model.model_type) not in registered for model in self._models):
            return True
        speech_model = next((model for model in self._models if model.model_type is ModelType.SPEECH2TEXT), None)
        default_reader = getattr(self._provider_service, "get_default_model_of_model_type", None)
        if speech_model is None or not callable(default_reader):
            return False
        current = default_reader(dify_tenant_id, ModelType.SPEECH2TEXT.value)
        current_provider = getattr(getattr(current, "provider", None), "provider", None)
        return (
            current is None
            or getattr(current, "model", None) != speech_model.name
            or current_provider != self._provider
        )

    def _synchronize_default_speech_model(self, dify_tenant_id: str) -> None:
        speech_model = next((model for model in self._models if model.model_type is ModelType.SPEECH2TEXT), None)
        default_writer = getattr(self._provider_service, "update_default_model_of_model_type", None)
        if speech_model is None or not callable(default_writer):
            return
        default_writer(
            dify_tenant_id,
            ModelType.SPEECH2TEXT.value,
            self._provider,
            speech_model.name,
        )

    def _registered_models(self, dify_tenant_id: str) -> dict[tuple[str, ModelType], str]:
        """Map this workspace's managed model registrations to their credential ids."""
        rows = self._session.execute(
            select(
                ProviderModelCredential.model_name,
                ProviderModelCredential.model_type,
                ProviderModelCredential.id,
            ).where(
                ProviderModelCredential.tenant_id == dify_tenant_id,
                ProviderModelCredential.provider_name == self._provider,
                ProviderModelCredential.credential_name == self._credential_name,
            )
        ).all()
        return {(name, ModelType(model_type)): credential_id for name, model_type, credential_id in rows}

    def _validate_model(self) -> str:
        """Name the LLM the plugin probes when validating the credential."""
        for model in self._models:
            if model.model_type is ModelType.LLM:
                return model.name
        raise CampusProvisioningError("Campus model list has no llm to validate credentials against")

    def _upsert_model_credential(
        self,
        model: CampusModel,
        model_credentials: ModelCredentialPayload,
        dify_tenant_id: str,
        registered: dict[tuple[str, ModelType], str],
    ) -> None:
        credential_id = registered.get((model.name, model.model_type))
        if credential_id is None:
            self._provider_service.create_model_credential(
                tenant_id=dify_tenant_id,
                provider=self._provider,
                model_type=model.model_type.value,
                model=model.name,
                credentials=model_credentials,
                credential_name=self._credential_name,
            )
        else:
            self._provider_service.update_model_credential(
                tenant_id=dify_tenant_id,
                provider=self._provider,
                model_type=model.model_type.value,
                model=model.name,
                credentials=model_credentials,
                credential_id=credential_id,
                credential_name=self._credential_name,
            )


class RefreshingModelConfigurator:
    """Resolve the gateway-owned model catalog only when provisioning needs it."""

    def __init__(self, factory: Callable[[], DifyModelConfigurator]) -> None:
        self._factory = factory

    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None:
        self._factory().configure(dify_tenant_id, gateway_secret)

    def needs_configuration(self, dify_tenant_id: str) -> bool:
        return self._factory().needs_configuration(dify_tenant_id)


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
