import json
from types import SimpleNamespace

from sqlalchemy.orm import Session

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
from models.workflow import Workflow, WorkflowType
from services.campus.dify_adapters import parse_campus_models
from services.campus.model_provider_reconciliation import (
    CampusModelProviderReconciler,
    rewrite_model_provider_references,
)

TARGET_PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"


def test_rewrites_legacy_llm_references_to_the_openai_compatible_provider() -> None:
    graph = {
        "nodes": [
            {
                "data": {
                    "model": {
                        "provider": "langgenius/deepseek/deepseek",
                        "name": "deepseek-v4-flash",
                    }
                }
            },
            {
                "data": {
                    "model": {
                        "provider": "langgenius/openai/openai",
                        "name": "gpt-5.6",
                    }
                }
            },
            {
                "data": {
                    "model": {
                        "provider": TARGET_PROVIDER,
                        "name": "glm-5.3-flash",
                    }
                }
            },
        ]
    }

    rewritten, changed = rewrite_model_provider_references(
        graph,
        target_provider=TARGET_PROVIDER,
        allowed_llms=("deepseek-v4-flash", "glm-5.3-flash"),
    )

    assert changed == 2
    assert rewritten["nodes"][0]["data"]["model"] == {
        "provider": TARGET_PROVIDER,
        "name": "deepseek-v4-flash",
    }
    assert rewritten["nodes"][1]["data"]["model"] == {
        "provider": TARGET_PROVIDER,
        "name": "deepseek-v4-flash",
    }
    assert rewritten["nodes"][2]["data"]["model"] == {
        "provider": TARGET_PROVIDER,
        "name": "glm-5.3-flash",
    }


def test_model_reference_rewrite_is_idempotent() -> None:
    graph = {
        "model": {
            "provider": TARGET_PROVIDER,
            "name": "deepseek-v4-flash",
        }
    }

    rewritten, changed = rewrite_model_provider_references(
        graph,
        target_provider=TARGET_PROVIDER,
        allowed_llms=("deepseek-v4-flash",),
    )

    assert rewritten == graph
    assert changed == 0


def test_reconciler_migrates_encrypted_credentials_and_retires_legacy_plugins(
    sqlite_engine,
    monkeypatch,
) -> None:
    tables = [
        App.__table__,
        AppModelConfig.__table__,
        Conversation.__table__,
        Provider.__table__,
        ProviderCredential.__table__,
        ProviderModel.__table__,
        ProviderModelCredential.__table__,
        ProviderModelSetting.__table__,
        LoadBalancingModelConfig.__table__,
        TenantDefaultModel.__table__,
        TenantPreferredModelProvider.__table__,
        Workflow.__table__,
    ]
    Provider.metadata.create_all(sqlite_engine, tables=tables)

    class PluginManager:
        def __init__(self) -> None:
            self.plugins = [
                SimpleNamespace(
                    plugin_id="langgenius/openai_api_compatible",
                    installation_id="compatible-installation",
                ),
                SimpleNamespace(plugin_id="langgenius/openai", installation_id="openai-installation"),
                SimpleNamespace(plugin_id="langgenius/deepseek", installation_id="deepseek-installation"),
            ]
            self.uninstalled: list[str] = []

        def list_plugins(self, tenant_id: str):
            assert tenant_id == "tenant-1"
            return list(self.plugins)

        def uninstall(self, tenant_id: str, installation_id: str) -> bool:
            assert tenant_id == "tenant-1"
            self.plugins = [plugin for plugin in self.plugins if plugin.installation_id != installation_id]
            self.uninstalled.append(installation_id)
            return True

    plugins = PluginManager()
    monkeypatch.setattr(CampusModelProviderReconciler, "_invalidate_provider_caches", lambda *_: None)

    with Session(sqlite_engine, expire_on_commit=False) as session:
        legacy_credential = ProviderCredential(
            tenant_id="tenant-1",
            provider_name="langgenius/openai/openai",
            credential_name="Campus managed",
            encrypted_config=json.dumps(
                {
                    "openai_api_key": "opaque-encrypted-gateway-key",
                    "openai_api_base": "http://model-gateway:3000/v1",
                }
            ),
        )
        session.add(legacy_credential)
        session.flush()
        session.add_all(
            [
                Provider(
                    tenant_id="tenant-1",
                    provider_name="langgenius/openai/openai",
                    credential_id=legacy_credential.id,
                    is_valid=True,
                ),
                TenantDefaultModel(
                    tenant_id="tenant-1",
                    provider_name="langgenius/openai/openai",
                    model_name="gpt-5.6",
                    model_type=ModelType.LLM,
                ),
                Workflow(
                    tenant_id="tenant-1",
                    app_id="app-1",
                    type=WorkflowType.WORKFLOW,
                    version=Workflow.VERSION_DRAFT,
                    graph=json.dumps(
                        {
                            "nodes": [
                                {
                                    "data": {
                                        "model": {
                                            "provider": "langgenius/deepseek/deepseek",
                                            "name": "deepseek-v4-flash",
                                        }
                                    }
                                }
                            ]
                        }
                    ),
                    features="{}",
                    created_by="account-1",
                    environment_variables=[],
                    conversation_variables=[],
                    rag_pipeline_variables=[],
                ),
            ]
        )
        session.commit()

        reconciler = CampusModelProviderReconciler(
            session=session,
            target_provider=TARGET_PROVIDER,
            credential_name="Campus managed",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash,llm:glm-5.3-flash"),
            plugin_manager=plugins,
        )
        summary = reconciler.reconcile(["tenant-1"])

        assert summary.clean
        assert plugins.uninstalled == ["openai-installation", "deepseek-installation"]
        assert session.query(Provider).count() == 0
        target_credentials = session.query(ProviderModelCredential).order_by(ProviderModelCredential.model_name).all()
        assert [credential.model_name for credential in target_credentials] == ["deepseek-v4-flash", "glm-5.3-flash"]
        assert {json.loads(credential.encrypted_config)["api_key"] for credential in target_credentials} == {
            "opaque-encrypted-gateway-key"
        }
        default = session.query(TenantDefaultModel).one()
        assert (default.provider_name, default.model_name) == (TARGET_PROVIDER, "deepseek-v4-flash")
        workflow = session.query(Workflow).one()
        assert json.loads(workflow.graph)["nodes"][0]["data"]["model"] == {
            "provider": TARGET_PROVIDER,
            "name": "deepseek-v4-flash",
        }

        second_summary = reconciler.reconcile(["tenant-1"])

        assert second_summary.clean
        assert plugins.uninstalled == ["openai-installation", "deepseek-installation"]
        assert session.query(ProviderModelCredential).count() == 2
