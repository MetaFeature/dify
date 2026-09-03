"""Retire legacy model providers from Campus student workspaces."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core.plugin.impl.plugin import PluginInstaller
from core.plugin.plugin_service import PluginService
from core.provider_manager import ProviderConfigurationCacheSource, ProviderManager
from graphon.model_runtime.entities.model_entities import ModelType
from models.model import App, AppModelConfig, Conversation
from models.provider import (
    LoadBalancingModelConfig,
    Provider,
    ProviderCredential,
    ProviderModel,
    ProviderModelCredential,
    ProviderModelSetting,
    TenantDefaultModel,
    TenantPreferredModelProvider,
)
from models.workflow import Workflow
from services.campus.dify_adapters import CampusModel
from services.campus.errors import CampusProvisioningError

LEGACY_MODEL_PROVIDERS = frozenset(
    {
        "langgenius/deepseek/deepseek",
        "langgenius/openai/openai",
    }
)
LEGACY_MODEL_PLUGIN_IDS = frozenset({"langgenius/deepseek", "langgenius/openai"})
OPENAI_COMPATIBLE_CONTEXT_SIZE = "4096"


def rewrite_model_provider_references(
    value: Any,
    *,
    target_provider: str,
    allowed_llms: Sequence[str],
) -> tuple[Any, int]:
    """Return a copy with legacy LLM references moved to one compatible provider."""
    if not allowed_llms:
        raise ValueError("at least one target llm is required")
    rewritten = copy.deepcopy(value)
    allowed = frozenset(allowed_llms)
    fallback = allowed_llms[0]

    def visit(item: Any) -> int:
        changed = 0
        if isinstance(item, dict):
            if item.get("provider") in LEGACY_MODEL_PROVIDERS:
                item["provider"] = target_provider
                for model_key in ("name", "model"):
                    model_name = item.get(model_key)
                    if isinstance(model_name, str) and model_name not in allowed:
                        item[model_key] = fallback
                        break
                changed += 1
            for child in item.values():
                changed += visit(child)
        elif isinstance(item, list):
            for child in item:
                changed += visit(child)
        return changed

    return rewritten, visit(rewritten)


class ModelPluginManager(Protocol):
    def list_plugins(self, tenant_id: str) -> Sequence[Any]: ...

    def uninstall(self, tenant_id: str, installation_id: str) -> bool: ...


class DifyModelPluginManager:
    def list_plugins(self, tenant_id: str) -> Sequence[Any]:
        return PluginInstaller().list_plugins(tenant_id)

    def uninstall(self, tenant_id: str, installation_id: str) -> bool:
        return PluginService.uninstall(tenant_id, installation_id)


@dataclass(frozen=True)
class ModelProviderReconciliationSummary:
    tenants: int = 0
    missing_target_plugins: int = 0
    missing_target_credentials: int = 0
    legacy_plugins: int = 0
    legacy_database_rows: int = 0
    legacy_workflow_references: int = 0

    @property
    def clean(self) -> bool:
        return not any(
            (
                self.missing_target_plugins,
                self.missing_target_credentials,
                self.legacy_plugins,
                self.legacy_database_rows,
                self.legacy_workflow_references,
            )
        )


class CampusModelProviderReconciler:
    """Migrate Campus-owned model state without making a billable model request."""

    def __init__(
        self,
        *,
        session: Session,
        target_provider: str,
        credential_name: str,
        base_url: str,
        models: Sequence[CampusModel],
        plugin_manager: ModelPluginManager | None = None,
    ) -> None:
        self._session = session
        self._target_provider = target_provider
        self._target_plugin_id = target_provider.rsplit("/", 1)[0]
        self._credential_name = credential_name
        self._base_url = base_url
        self._models = tuple(models)
        self._llms = tuple(model.name for model in self._models if model.model_type is ModelType.LLM)
        if not self._llms:
            raise ValueError("at least one target llm is required")
        self._plugin_manager = plugin_manager or DifyModelPluginManager()

    def audit(self, tenant_ids: Sequence[str]) -> ModelProviderReconciliationSummary:
        missing_plugins = 0
        legacy_plugins = 0
        for tenant_id in tenant_ids:
            plugins = self._plugin_manager.list_plugins(tenant_id)
            plugin_ids = {plugin.plugin_id for plugin in plugins}
            missing_plugins += self._target_plugin_id not in plugin_ids
            legacy_plugins += sum(plugin_id in LEGACY_MODEL_PLUGIN_IDS for plugin_id in plugin_ids)

        missing_credentials = 0
        for tenant_id in tenant_ids:
            registered = set(
                self._session.execute(
                    select(ProviderModelCredential.model_name, ProviderModelCredential.model_type).where(
                        ProviderModelCredential.tenant_id == tenant_id,
                        ProviderModelCredential.provider_name == self._target_provider,
                        ProviderModelCredential.credential_name == self._credential_name,
                    )
                ).all()
            )
            missing_credentials += sum((model.name, model.model_type) not in registered for model in self._models)

        return ModelProviderReconciliationSummary(
            tenants=len(tenant_ids),
            missing_target_plugins=missing_plugins,
            missing_target_credentials=missing_credentials,
            legacy_plugins=legacy_plugins,
            legacy_database_rows=self._legacy_database_row_count(tenant_ids),
            legacy_workflow_references=self._legacy_workflow_reference_count(tenant_ids),
        )

    def reconcile(self, tenant_ids: Sequence[str]) -> ModelProviderReconciliationSummary:
        for tenant_id in tenant_ids:
            self._require_target_plugin(tenant_id)
            self._ensure_target_credentials(tenant_id)
            self._rewrite_tenant_references(tenant_id)
        self._session.commit()

        for tenant_id in tenant_ids:
            for plugin in self._plugin_manager.list_plugins(tenant_id):
                if plugin.plugin_id in LEGACY_MODEL_PLUGIN_IDS and not self._plugin_manager.uninstall(
                    tenant_id, plugin.installation_id
                ):
                    raise CampusProvisioningError("Campus legacy model provider plugin uninstall failed")

        self._session.expire_all()
        for tenant_id in tenant_ids:
            self._delete_legacy_database_rows(tenant_id)
        self._session.commit()
        for tenant_id in tenant_ids:
            self._invalidate_provider_caches(tenant_id)
        return self.audit(tenant_ids)

    def _require_target_plugin(self, tenant_id: str) -> None:
        if not any(
            plugin.plugin_id == self._target_plugin_id for plugin in self._plugin_manager.list_plugins(tenant_id)
        ):
            raise CampusProvisioningError("Campus OpenAI-compatible model provider plugin is missing")

    def _ensure_target_credentials(self, tenant_id: str) -> None:
        existing = {
            (credential.model_name, credential.model_type): credential
            for credential in self._session.scalars(
                select(ProviderModelCredential).where(
                    ProviderModelCredential.tenant_id == tenant_id,
                    ProviderModelCredential.provider_name == self._target_provider,
                    ProviderModelCredential.credential_name == self._credential_name,
                )
            )
        }
        encrypted_api_key = self._opaque_gateway_key(tenant_id, existing.values())
        for model in self._models:
            key = (model.name, model.model_type)
            credential = existing.get(key)
            if credential is None:
                credential = ProviderModelCredential(
                    tenant_id=tenant_id,
                    provider_name=self._target_provider,
                    model_name=model.name,
                    model_type=model.model_type,
                    credential_name=self._credential_name,
                    encrypted_config=json.dumps(
                        self._compatible_credentials(model, encrypted_api_key),
                        separators=(",", ":"),
                    ),
                )
                self._session.add(credential)
                self._session.flush()
                existing[key] = credential

            provider_model = self._session.scalar(
                select(ProviderModel).where(
                    ProviderModel.tenant_id == tenant_id,
                    ProviderModel.provider_name == self._target_provider,
                    ProviderModel.model_name == model.name,
                    ProviderModel.model_type == model.model_type,
                )
            )
            if provider_model is None:
                self._session.add(
                    ProviderModel(
                        tenant_id=tenant_id,
                        provider_name=self._target_provider,
                        model_name=model.name,
                        model_type=model.model_type,
                        credential_id=credential.id,
                        is_valid=True,
                    )
                )
            else:
                provider_model.credential_id = credential.id
                provider_model.is_valid = True

    def _opaque_gateway_key(
        self,
        tenant_id: str,
        target_credentials: Iterable[ProviderModelCredential],
    ) -> str:
        for credential in target_credentials:
            config = json.loads(credential.encrypted_config)
            value = config.get("api_key")
            if isinstance(value, str) and value:
                return value

        sources = self._session.scalars(
            select(ProviderCredential).where(
                ProviderCredential.tenant_id == tenant_id,
                ProviderCredential.provider_name.in_(LEGACY_MODEL_PROVIDERS),
                ProviderCredential.credential_name == self._credential_name,
            )
        )
        for source in sources:
            config = json.loads(source.encrypted_config)
            for field in ("openai_api_key", "api_key"):
                value = config.get(field)
                if isinstance(value, str) and value:
                    return value
        raise CampusProvisioningError("Campus managed gateway credential is missing")

    def _compatible_credentials(self, model: CampusModel, encrypted_api_key: str) -> dict[str, str]:
        credentials = {
            "api_key": encrypted_api_key,
            "endpoint_url": self._base_url,
        }
        if model.model_type is ModelType.LLM:
            credentials.update(mode="chat", api_type="chat_completions", context_size=OPENAI_COMPATIBLE_CONTEXT_SIZE)
        elif model.model_type is ModelType.TEXT_EMBEDDING:
            credentials.update(max_chunks="1", context_size=OPENAI_COMPATIBLE_CONTEXT_SIZE)
        elif model.model_type is ModelType.RERANK:
            credentials.update(context_size=OPENAI_COMPATIBLE_CONTEXT_SIZE)
        return credentials

    def _rewrite_tenant_references(self, tenant_id: str) -> None:
        for workflow in self._session.scalars(select(Workflow).where(Workflow.tenant_id == tenant_id)):
            graph, changed = rewrite_model_provider_references(
                json.loads(workflow.graph),
                target_provider=self._target_provider,
                allowed_llms=self._llms,
            )
            if changed:
                workflow.graph = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))

        app_ids = select(App.id).where(App.tenant_id == tenant_id)
        for config in self._session.scalars(select(AppModelConfig).where(AppModelConfig.app_id.in_(app_ids))):
            if config.provider in LEGACY_MODEL_PROVIDERS:
                config.provider = self._target_provider
                if config.model_id not in self._llms:
                    config.model_id = self._llms[0]
            if config.model:
                model, changed = rewrite_model_provider_references(
                    json.loads(config.model),
                    target_provider=self._target_provider,
                    allowed_llms=self._llms,
                )
                if changed:
                    config.model = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
            if config.configs:
                configs, changed = rewrite_model_provider_references(
                    config.configs,
                    target_provider=self._target_provider,
                    allowed_llms=self._llms,
                )
                if changed:
                    config.configs = configs

        for conversation in self._session.scalars(select(Conversation).where(Conversation.app_id.in_(app_ids))):
            if conversation.model_provider in LEGACY_MODEL_PROVIDERS:
                conversation.model_provider = self._target_provider
                if conversation.model_id not in self._llms:
                    conversation.model_id = self._llms[0]

        targets_by_type: dict[ModelType, str] = {}
        for model in self._models:
            targets_by_type.setdefault(model.model_type, model.name)
        for default in self._session.scalars(
            select(TenantDefaultModel).where(
                TenantDefaultModel.tenant_id == tenant_id,
                TenantDefaultModel.provider_name.in_(LEGACY_MODEL_PROVIDERS),
            )
        ):
            target_model = targets_by_type.get(default.model_type)
            if target_model is None:
                self._session.delete(default)
            else:
                default.provider_name = self._target_provider
                default.model_name = target_model

    def _delete_legacy_database_rows(self, tenant_id: str) -> None:
        for model in (
            LoadBalancingModelConfig,
            ProviderModelSetting,
            ProviderModel,
            Provider,
            ProviderModelCredential,
            ProviderCredential,
            TenantPreferredModelProvider,
        ):
            self._session.execute(
                delete(model).where(
                    model.tenant_id == tenant_id,
                    model.provider_name.in_(LEGACY_MODEL_PROVIDERS),
                )
            )

    def _legacy_database_row_count(self, tenant_ids: Sequence[str]) -> int:
        if not tenant_ids:
            return 0
        total = 0
        for model in (
            Provider,
            ProviderCredential,
            ProviderModel,
            ProviderModelCredential,
            ProviderModelSetting,
            LoadBalancingModelConfig,
            TenantDefaultModel,
            TenantPreferredModelProvider,
        ):
            total += (
                self._session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(
                        model.tenant_id.in_(tenant_ids),
                        model.provider_name.in_(LEGACY_MODEL_PROVIDERS),
                    )
                )
                or 0
            )
        return total

    def _legacy_workflow_reference_count(self, tenant_ids: Sequence[str]) -> int:
        if not tenant_ids:
            return 0
        total = 0
        for graph in self._session.scalars(select(Workflow.graph).where(Workflow.tenant_id.in_(tenant_ids))):
            parsed = json.loads(graph)
            for provider in LEGACY_MODEL_PROVIDERS:
                total += graph.count(provider)
            # Parsing here also makes malformed persisted graphs fail before migration.
            if not isinstance(parsed, Mapping):
                raise CampusProvisioningError("Campus workflow graph is not an object")
        app_ids = select(App.id).where(App.tenant_id.in_(tenant_ids))
        for config in self._session.scalars(select(AppModelConfig).where(AppModelConfig.app_id.in_(app_ids))):
            total += int(config.provider in LEGACY_MODEL_PROVIDERS)
            for value in (config.model, json.dumps(config.configs) if config.configs else None):
                if value:
                    total += sum(value.count(provider) for provider in LEGACY_MODEL_PROVIDERS)
        total += (
            self._session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(
                    Conversation.app_id.in_(app_ids),
                    Conversation.model_provider.in_(LEGACY_MODEL_PROVIDERS),
                )
            )
            or 0
        )
        return total

    @staticmethod
    def _invalidate_provider_caches(tenant_id: str) -> None:
        ProviderManager.invalidate_configurations_cache(
            tenant_id,
            sources=list(ProviderConfigurationCacheSource),
        )
        PluginService.invalidate_plugin_model_providers_cache(tenant_id)
