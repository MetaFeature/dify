import json
from types import SimpleNamespace

from sqlalchemy.orm import Session

from graphon.model_runtime.entities.model_entities import ModelType
from models.dataset import Dataset
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
    ModelProviderReconciliationSummary,
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
        # The audit also reports knowledge bases pinned to a dropped model.
        Dataset.__table__,
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
    decrypted_cache_drops: list[str] = []
    monkeypatch.setattr(
        CampusModelProviderReconciler,
        "_invalidate_decrypted_credentials_caches",
        lambda _self, tenant_id, provider_model_ids: decrypted_cache_drops.extend(provider_model_ids),
    )

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
                TenantDefaultModel(
                    tenant_id="tenant-1",
                    provider_name="other/speech/speech",
                    model_name="old-transcriber",
                    model_type=ModelType.SPEECH2TEXT,
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
            models=parse_campus_models(
                "llm:deepseek-v4-flash,llm:glm-5.3-flash,speech2text:qwen-audio-3.0-asr-flash"
            ),
            plugin_manager=plugins,
        )
        summary = reconciler.reconcile(["tenant-1"])

        assert summary.clean
        assert plugins.uninstalled == ["openai-installation", "deepseek-installation"]
        assert session.query(Provider).count() == 0
        target_credentials = session.query(ProviderModelCredential).order_by(ProviderModelCredential.model_name).all()
        assert [credential.model_name for credential in target_credentials] == [
            "deepseek-v4-flash",
            "glm-5.3-flash",
            "qwen-audio-3.0-asr-flash",
        ]
        assert {json.loads(credential.encrypted_config)["api_key"] for credential in target_credentials} == {
            "opaque-encrypted-gateway-key"
        }
        defaults = session.query(TenantDefaultModel).order_by(TenantDefaultModel.model_type).all()
        assert {(default.model_type, default.provider_name, default.model_name) for default in defaults} == {
            (ModelType.LLM, TARGET_PROVIDER, "deepseek-v4-flash"),
            (ModelType.SPEECH2TEXT, TARGET_PROVIDER, "qwen-audio-3.0-asr-flash"),
        }
        workflow = session.query(Workflow).one()
        assert json.loads(workflow.graph)["nodes"][0]["data"]["model"] == {
            "provider": TARGET_PROVIDER,
            "name": "deepseek-v4-flash",
        }

        second_summary = reconciler.reconcile(["tenant-1"])

        assert second_summary.clean
        assert plugins.uninstalled == ["openai-installation", "deepseek-installation"]
        assert session.query(ProviderModelCredential).count() == 3

        # Capability flags live in the credential payload, so a later reconcile
        # must converge existing rows instead of skipping them.
        existing = (
            session.query(ProviderModelCredential)
            .filter(ProviderModelCredential.model_name == "deepseek-v4-flash")
            .one()
        )
        existing_config = json.loads(existing.encrypted_config)
        existing_config["vision_support"] = "no_support"
        existing_config["stream_mode_delimiter"] = "\n\n"
        existing.encrypted_config = json.dumps(existing_config)
        session.commit()

        capabilities = CampusModelProviderReconciler(
            session=session,
            target_provider=TARGET_PROVIDER,
            credential_name="Campus managed",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models(
                "llm:deepseek-v4-flash,llm:glm-5.3-flash,speech2text:qwen-audio-3.0-asr-flash"
            ),
            vision_models=("deepseek-v4-flash",),
            document_models=("deepseek-v4-flash",),
            plugin_manager=plugins,
        )

        drops_before = len(decrypted_cache_drops)
        assert capabilities.reconcile(["tenant-1"]).clean

        converged = json.loads(
            session.query(ProviderModelCredential)
            .filter(ProviderModelCredential.model_name == "deepseek-v4-flash")
            .one()
            .encrypted_config
        )
        assert converged["vision_support"] == "support"
        assert converged["document_support"] == "support"
        assert converged["audio_support"] == "no_support"
        # Plugin-managed keys survive the campus-owned field rewrite.
        assert converged["stream_mode_delimiter"] == "\n\n"

        # Dify caches decrypted credentials per provider model id for 24h, so a
        # converging reconcile must drop every target model's entry.
        assert sorted(decrypted_cache_drops[drops_before:]) == sorted(
            provider_model.id for provider_model in session.query(ProviderModel).all()
        )

        non_llm = json.loads(
            session.query(ProviderModelCredential)
            .filter(ProviderModelCredential.model_name == "qwen-audio-3.0-asr-flash")
            .one()
            .encrypted_config
        )
        assert "vision_support" not in non_llm

        workflow.graph = json.dumps(
            {
                "nodes": [
                    {
                        "data": {
                            "model": {
                                "provider": TARGET_PROVIDER,
                                "name": "glm-5.3-flash",
                            }
                        }
                    }
                ]
            }
        )
        session.commit()
        shrunk = CampusModelProviderReconciler(
            session=session,
            target_provider=TARGET_PROVIDER,
            credential_name="Campus managed",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash"),
            plugin_manager=plugins,
        )

        shrunk_summary = shrunk.reconcile(["tenant-1"])

        assert shrunk_summary.clean
        assert [
            credential.model_name
            for credential in session.query(ProviderModelCredential)
            .order_by(ProviderModelCredential.model_name)
            .all()
        ] == ["deepseek-v4-flash"]
        assert json.loads(workflow.graph)["nodes"][0]["data"]["model"] == {
            "provider": TARGET_PROVIDER,
            "name": "deepseek-v4-flash",
        }


def _high_quality_dataset(
    name: str,
    *,
    embedding_model: str,
    rerank_model: str | None = None,
    technique: str = "high_quality",
) -> Dataset:
    retrieval_model = (
        {
            "search_method": "semantic_search",
            "reranking_enable": True,
            "reranking_mode": "reranking_model",
            "reranking_model": {
                "reranking_provider_name": TARGET_PROVIDER,
                "reranking_model_name": rerank_model,
            },
            "top_k": 3,
            "score_threshold_enabled": False,
        }
        if rerank_model
        else None
    )
    return Dataset(
        tenant_id="tenant-1",
        name=name,
        data_source_type="upload_file",
        created_by="account-1",
        indexing_technique=technique,
        embedding_model=embedding_model,
        embedding_model_provider=TARGET_PROVIDER,
        retrieval_model=retrieval_model,
    )


def test_reports_knowledge_bases_pinned_to_a_model_the_catalog_dropped(sqlite_engine) -> None:
    Dataset.metadata.create_all(sqlite_engine, tables=[Dataset.__table__])
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                _high_quality_dataset("嵌入已下线", embedding_model="BGE-m3"),
                _high_quality_dataset(
                    "重排已下线", embedding_model="text-embedding-v4", rerank_model="BGE-Reranker-V2-m3"
                ),
                _high_quality_dataset("正常", embedding_model="text-embedding-v4", rerank_model="qwen3-rerank"),
                _high_quality_dataset("经济模式", embedding_model="BGE-m3", technique="economy"),
            ]
        )
        session.commit()
        reconciler = CampusModelProviderReconciler(
            session=session,
            target_provider=TARGET_PROVIDER,
            credential_name="campus",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4.1-flash,text-embedding:text-embedding-v4,rerank:qwen3-rerank"),
        )
        rows = reconciler.stale_datasets(["tenant-1"])
        embedding_target = reconciler.replacement_model(ModelType.TEXT_EMBEDDING)
        rerank_target = reconciler.replacement_model(ModelType.RERANK)

    assert {(row.name, row.model_type, row.model_name) for row in rows} == {
        ("嵌入已下线", "text-embedding", "BGE-m3"),
        ("重排已下线", "rerank", "BGE-Reranker-V2-m3"),
    }
    assert (embedding_target, rerank_target) == ("text-embedding-v4", "qwen3-rerank")


def test_dataset_drift_is_reported_without_making_the_reconciler_unclean() -> None:
    # The heartbeat treats a non-clean reconciliation as a control-plane failure
    # and restarts containers, while migrating a knowledge base spends quota and
    # needs an operator. So this one is reported and left out of `clean`.
    assert ModelProviderReconciliationSummary(stale_dataset_models=2).clean is True
    assert ModelProviderReconciliationSummary(missing_target_plugins=1).clean is False
