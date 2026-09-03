from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from graphon.model_runtime.entities.model_entities import ModelType
from models.account import Tenant, TenantAccountJoin, TenantAccountRole
from models.provider import ProviderCredential, ProviderModelCredential
from services.campus import dify_adapters
from services.campus.dify_adapters import (
    CampusModel,
    DifyModelConfigurator,
    DifyWorkspaceProvisioner,
    MarketplaceProviderPluginInstaller,
    parse_campus_models,
)
from services.campus.errors import CampusProvisioningError, CampusValidationError


@pytest.fixture
def tenant_session(sqlite_engine) -> Session:
    tables = [Tenant.__table__, TenantAccountJoin.__table__]
    Tenant.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


def test_existing_workspace_rejects_any_additional_human_member(tenant_session: Session) -> None:
    service_principal_id = str(uuid4())
    student_id = str(uuid4())
    unexpected_member_id = str(uuid4())
    tenant = Tenant(name="Student workspace")
    tenant_session.add(tenant)
    tenant_session.flush()
    tenant_session.add_all(
        [
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=service_principal_id,
                role=TenantAccountRole.OWNER,
            ),
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=student_id,
                role=TenantAccountRole.EDITOR,
            ),
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=unexpected_member_id,
                role=TenantAccountRole.NORMAL,
            ),
        ]
    )
    tenant_session.commit()
    provisioner = DifyWorkspaceProvisioner(
        session=tenant_session,
        service_principal_email="campus-owner@example.invalid",
    )

    with pytest.raises(CampusProvisioningError, match="unexpected human members"):
        provisioner._existing_workspace(student_id, service_principal_id)


def test_model_configurator_installs_provider_plugin_before_credentials(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    events: list[str] = []

    class PluginInstaller:
        def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
            assert tenant_id == "tenant-1"
            assert plugin_unique_identifier == "langgenius/openai:1.0.4@checksum"
            events.append("plugin-installed")

    class ProviderService:
        def create_provider_credential(
            self,
            tenant_id: str,
            provider: str,
            credentials: dict[str, str],
            credential_name: str,
        ) -> None:
            assert tenant_id == "tenant-1"
            assert provider == "langgenius/openai/openai"
            assert credentials == {
                "openai_api_key": "managed-secret",
                "openai_api_base": "http://model-gateway:3000/v1",
                "validate_model": "deepseek-v4-flash",
                "api_protocol": "chat",
            }
            assert credential_name == "Campus managed"
            events.append("provider-credential-created")

        def update_provider_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its provider credential")

        def create_model_credential(
            self,
            tenant_id: str,
            provider: str,
            model_type: str,
            model: str,
            credentials: dict[str, str],
            credential_name: str,
        ) -> None:
            assert tenant_id == "tenant-1"
            assert provider == "langgenius/openai/openai"
            assert model_type == "llm"
            assert model == "deepseek-v4-flash"
            assert credentials == {
                "openai_api_key": "managed-secret",
                "openai_api_base": "http://model-gateway:3000/v1",
                "api_protocol": "chat",
            }
            assert credential_name == "Campus managed"
            events.append("model-credential-created")

        def update_model_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its model credential")

    with Session(sqlite_engine) as session:
        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai/openai",
            provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
            credential_name="Campus managed",
            api_key_field="openai_api_key",
            base_url_field="openai_api_base",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash"),
            api_protocol="chat",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert events == ["plugin-installed", "provider-credential-created", "model-credential-created"]


def test_model_only_configurator_creates_openai_compatible_model_credentials(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    events: list[str] = []

    class PluginInstaller:
        def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
            assert tenant_id == "tenant-1"
            assert plugin_unique_identifier == "langgenius/openai_api_compatible:0.0.64@checksum"
            events.append("plugin-installed")

    class ProviderService:
        def create_provider_credential(self, **_: object) -> None:
            raise AssertionError("the OpenAI-compatible plugin has no provider credential schema")

        def update_provider_credential(self, **_: object) -> None:
            raise AssertionError("the OpenAI-compatible plugin has no provider credential schema")

        def create_model_credential(
            self,
            tenant_id: str,
            provider: str,
            model_type: str,
            model: str,
            credentials: dict[str, str],
            credential_name: str,
        ) -> None:
            assert tenant_id == "tenant-1"
            assert provider == "langgenius/openai_api_compatible/openai_api_compatible"
            assert model_type == "llm"
            assert model == "deepseek-v4-flash"
            assert credentials == {
                "api_key": "managed-secret",
                "endpoint_url": "http://model-gateway:3000/v1",
                "mode": "chat",
                "api_type": "chat_completions",
                "context_size": "4096",
            }
            assert credential_name == "Campus managed"
            events.append("model-credential-created")

        def update_model_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its model credential")

    with Session(sqlite_engine) as session:
        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai_api_compatible/openai_api_compatible",
            provider_plugin_unique_identifier="langgenius/openai_api_compatible:0.0.64@checksum",
            credential_name="Campus managed",
            credential_scope="model",
            api_key_field="api_key",
            base_url_field="endpoint_url",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash"),
            api_protocol="chat",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert events == ["plugin-installed", "model-credential-created"]


def test_model_only_configurator_supplies_required_fields_for_each_model_type(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    credentials_by_type: dict[str, dict[str, str]] = {}

    class ProviderService:
        def create_provider_credential(self, **_: object) -> None:
            raise AssertionError("model-only configuration cannot create provider credentials")

        def update_provider_credential(self, **_: object) -> None:
            raise AssertionError("model-only configuration cannot update provider credentials")

        def create_model_credential(self, *, model_type: str, credentials: dict[str, str], **_: object) -> None:
            credentials_by_type[model_type] = credentials

        def update_model_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its model credentials")

    with Session(sqlite_engine) as session:
        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai_api_compatible/openai_api_compatible",
            provider_plugin_unique_identifier="langgenius/openai_api_compatible:0.0.64@checksum",
            credential_name="Campus managed",
            credential_scope="model",
            api_key_field="api_key",
            base_url_field="endpoint_url",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash,text-embedding:bge-m3,rerank:bge-reranker-v2-m3"),
            api_protocol="chat",
            plugin_installer=SimpleNamespace(ensure_installed=lambda *_: None),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    common = {"api_key": "managed-secret", "endpoint_url": "http://model-gateway:3000/v1"}
    assert credentials_by_type == {
        "llm": {**common, "mode": "chat", "api_type": "chat_completions", "context_size": "4096"},
        "text-embedding": {**common, "max_chunks": "1", "context_size": "4096"},
        "rerank": {**common, "context_size": "4096"},
    }


def test_model_configurator_updates_existing_provider_and_model_credentials(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    events: list[str] = []

    class PluginInstaller:
        def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
            events.append("plugin-installed")

    class ProviderService:
        def create_provider_credential(self, **_: object) -> None:
            raise AssertionError("existing provider credential must be updated")

        def update_provider_credential(self, *, credential_id: str, **_: object) -> None:
            assert credential_id == provider_credential.id
            events.append("provider-credential-updated")

        def create_model_credential(self, **_: object) -> None:
            raise AssertionError("existing model credential must be updated")

        def update_model_credential(self, *, credential_id: str, **_: object) -> None:
            assert credential_id == model_credential.id
            events.append("model-credential-updated")

    with Session(sqlite_engine) as session:
        provider_credential = ProviderCredential(
            tenant_id="tenant-1",
            provider_name="langgenius/openai/openai",
            credential_name="Campus managed",
            encrypted_config="{}",
        )
        model_credential = ProviderModelCredential(
            tenant_id="tenant-1",
            provider_name="langgenius/openai/openai",
            model_name="deepseek-v4-flash",
            model_type=ModelType.LLM,
            credential_name="Campus managed",
            encrypted_config="{}",
        )
        session.add_all([provider_credential, model_credential])
        session.commit()

        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai/openai",
            provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
            credential_name="Campus managed",
            api_key_field="openai_api_key",
            base_url_field="openai_api_base",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash"),
            api_protocol="chat",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert events == ["plugin-installed", "provider-credential-updated", "model-credential-updated"]


def test_marketplace_provider_installer_skips_existing_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    class Installer:
        def list_plugins(self, tenant_id: str):
            assert tenant_id == "tenant-1"
            return [
                SimpleNamespace(
                    plugin_id="langgenius/openai",
                    plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
                )
            ]

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda *_: pytest.fail("existing plugin must not be installed again"),
    )

    MarketplaceProviderPluginInstaller().ensure_installed(
        "tenant-1",
        "langgenius/openai:1.0.4@checksum",
    )


def test_marketplace_provider_installer_waits_until_plugin_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    list_calls = 0
    install_calls: list[tuple[str, list[str]]] = []

    class Installer:
        def list_plugins(self, tenant_id: str):
            nonlocal list_calls
            assert tenant_id == "tenant-1"
            list_calls += 1
            return (
                []
                if list_calls == 1
                else [
                    SimpleNamespace(
                        plugin_id="langgenius/openai",
                        plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
                    )
                ]
            )

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda tenant_id, identifiers: install_calls.append((tenant_id, list(identifiers))),
    )

    MarketplaceProviderPluginInstaller(poll_interval_seconds=0.001).ensure_installed(
        "tenant-1",
        "langgenius/openai:1.0.4@checksum",
    )

    assert install_calls == [("tenant-1", ["langgenius/openai:1.0.4@checksum"])]
    assert list_calls == 2


def test_marketplace_provider_installer_replaces_wrong_plugin_package(monkeypatch: pytest.MonkeyPatch) -> None:
    install_calls: list[tuple[str, list[str]]] = []
    list_calls = 0

    class Installer:
        def list_plugins(self, tenant_id: str):
            nonlocal list_calls
            assert tenant_id == "tenant-1"
            list_calls += 1
            identifier = "langgenius/openai:0.9.0@old" if list_calls == 1 else "langgenius/openai:1.0.4@checksum"
            return [SimpleNamespace(plugin_id="langgenius/openai", plugin_unique_identifier=identifier)]

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda tenant_id, identifiers: install_calls.append((tenant_id, list(identifiers))),
    )

    MarketplaceProviderPluginInstaller(poll_interval_seconds=0.001).ensure_installed(
        "tenant-1",
        "langgenius/openai:1.0.4@checksum",
    )

    assert install_calls == [("tenant-1", ["langgenius/openai:1.0.4@checksum"])]


def test_marketplace_provider_installer_translates_daemon_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Installer:
        def list_plugins(self, tenant_id: str):
            raise RuntimeError(f"daemon unavailable for {tenant_id}")

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)

    with pytest.raises(CampusProvisioningError, match="plugin installation failed") as raised:
        MarketplaceProviderPluginInstaller().ensure_installed(
            "tenant-1",
            "langgenius/openai:1.0.4@checksum",
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert "Failed to install Campus model provider plugin" in caplog.text


def test_marketplace_provider_installer_logs_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Installer:
        def list_plugins(self, tenant_id: str):
            assert tenant_id == "tenant-1"
            return []

    monotonic_values = iter((0.0, 1.0))
    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(dify_adapters.PluginService, "install_from_marketplace_pkg", lambda *_: None)
    monkeypatch.setattr(dify_adapters.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(CampusProvisioningError, match="installation timed out"):
        MarketplaceProviderPluginInstaller(timeout_seconds=0.5).ensure_installed(
            "tenant-1",
            "langgenius/openai:1.0.4@checksum",
        )

    assert "Timed out installing Campus model provider plugin" in caplog.text


def test_provider_installer_prefers_the_local_package_over_the_marketplace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "openai.difypkg"
    package.write_bytes(b"pinned-package-bytes")
    uploaded: list[tuple[str, bytes]] = []
    installed: list[tuple[str, list[str]]] = []
    list_calls = 0

    class Installer:
        def list_plugins(self, tenant_id: str):
            nonlocal list_calls
            list_calls += 1
            return (
                []
                if list_calls == 1
                else [
                    SimpleNamespace(
                        plugin_id="langgenius/openai",
                        plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
                    )
                ]
            )

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "upload_pkg",
        lambda tenant_id, pkg: (
            uploaded.append((tenant_id, pkg)) or SimpleNamespace(unique_identifier="langgenius/openai:1.0.4@checksum")
        ),
    )
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_local_pkg",
        lambda tenant_id, identifiers: installed.append((tenant_id, list(identifiers))),
    )
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda *_: pytest.fail("a usable local package must not reach the marketplace"),
    )

    MarketplaceProviderPluginInstaller(poll_interval_seconds=0.001, local_package_path=str(package)).ensure_installed(
        "tenant-1", "langgenius/openai:1.0.4@checksum"
    )

    assert uploaded == [("tenant-1", b"pinned-package-bytes")]
    assert installed == [("tenant-1", ["langgenius/openai:1.0.4@checksum"])]


def test_provider_installer_rejects_a_local_package_that_does_not_match_the_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "openai.difypkg"
    package.write_bytes(b"some-other-package")

    class Installer:
        def list_plugins(self, tenant_id: str):
            return []

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "upload_pkg",
        lambda tenant_id, pkg: SimpleNamespace(unique_identifier="langgenius/openai:0.9.0@stale"),
    )
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda *_: pytest.fail("a mismatched local package must fail loudly, not silently reach the network"),
    )

    with pytest.raises(CampusProvisioningError, match="does not match the pinned identifier"):
        MarketplaceProviderPluginInstaller(local_package_path=str(package)).ensure_installed(
            "tenant-1",
            "langgenius/openai:1.0.4@checksum",
        )


def test_provider_installer_rejects_a_configured_local_package_that_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Installer:
        def list_plugins(self, tenant_id: str):
            return []

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda *_: pytest.fail("a missing configured package is a deployment fault, not a marketplace fallback"),
    )

    with pytest.raises(CampusProvisioningError, match="local provider plugin package is missing"):
        MarketplaceProviderPluginInstaller(local_package_path=str(tmp_path / "absent.difypkg")).ensure_installed(
            "tenant-1",
            "langgenius/openai:1.0.4@checksum",
        )


def test_provider_installer_uses_the_marketplace_when_no_local_package_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_calls: list[tuple[str, list[str]]] = []
    list_calls = 0

    class Installer:
        def list_plugins(self, tenant_id: str):
            nonlocal list_calls
            list_calls += 1
            return (
                []
                if list_calls == 1
                else [
                    SimpleNamespace(
                        plugin_id="langgenius/openai",
                        plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
                    )
                ]
            )

    monkeypatch.setattr(dify_adapters, "PluginInstaller", Installer)
    monkeypatch.setattr(
        dify_adapters.PluginService,
        "install_from_marketplace_pkg",
        lambda tenant_id, identifiers: install_calls.append((tenant_id, list(identifiers))),
    )

    MarketplaceProviderPluginInstaller(poll_interval_seconds=0.001, local_package_path="").ensure_installed(
        "tenant-1",
        "langgenius/openai:1.0.4@checksum",
    )

    assert install_calls == [("tenant-1", ["langgenius/openai:1.0.4@checksum"])]


def test_campus_model_spec_parses_typed_entries() -> None:
    models = parse_campus_models(
        "llm:deepseek-v4-flash, llm:glm-5.3-flash ,text-embedding:bge-m3,rerank:bge-reranker-v2-m3"
    )

    assert models == (
        CampusModel(name="deepseek-v4-flash", model_type=ModelType.LLM),
        CampusModel(name="glm-5.3-flash", model_type=ModelType.LLM),
        CampusModel(name="bge-m3", model_type=ModelType.TEXT_EMBEDDING),
        CampusModel(name="bge-reranker-v2-m3", model_type=ModelType.RERANK),
    )


def test_campus_model_spec_rejects_entries_it_cannot_route() -> None:
    # An unusable spec must fail at wiring time rather than silently provisioning
    # a workspace whose model list is wrong.
    with pytest.raises(CampusValidationError, match="model type"):
        parse_campus_models("image:doubao-seedream-5.0-pro")
    with pytest.raises(CampusValidationError, match="provider does not support"):
        parse_campus_models("llm:deepseek-v4-flash,moderation:text-moderation-latest")
    with pytest.raises(CampusValidationError, match="type:name"):
        parse_campus_models("deepseek-v4-flash")
    with pytest.raises(CampusValidationError, match="at least one"):
        parse_campus_models("")
    with pytest.raises(CampusValidationError, match="duplicate"):
        parse_campus_models("llm:deepseek-v4-flash,llm:deepseek-v4-flash")


def test_campus_model_spec_requires_an_llm_track() -> None:
    # The large-model experiment requires at least one chat model even when the
    # configured provider also exposes embedding and reranking slots.
    with pytest.raises(CampusValidationError, match="at least one llm"):
        parse_campus_models("text-embedding:bge-m3")


def test_model_configurator_registers_every_configured_model_with_its_type(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    registered: list[tuple[str, str]] = []
    validate_models: list[str] = []

    class PluginInstaller:
        def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
            pass

    class ProviderService:
        def create_provider_credential(self, *, credentials: dict[str, str], **_: object) -> None:
            validate_models.append(credentials["validate_model"])

        def update_provider_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its provider credential")

        def create_model_credential(
            self, *, model_type: str, model: str, credentials: dict[str, str], **_: object
        ) -> None:
            assert "validate_model" not in credentials
            registered.append((model_type, model))

        def update_model_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its model credentials")

    with Session(sqlite_engine) as session:
        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai/openai",
            provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
            credential_name="Campus managed",
            api_key_field="openai_api_key",
            base_url_field="openai_api_base",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash,llm:glm-5.3-flash,text-embedding:bge-m3"),
            api_protocol="chat",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert registered == [
        ("llm", "deepseek-v4-flash"),
        ("llm", "glm-5.3-flash"),
        ("text-embedding", "bge-m3"),
    ]
    # The first LLM is what the provider credential is validated against.
    assert validate_models == ["deepseek-v4-flash"]


def test_model_configurator_updates_only_the_models_already_registered(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    created: list[str] = []
    updated: list[str] = []

    class PluginInstaller:
        def ensure_installed(self, tenant_id: str, plugin_unique_identifier: str) -> None:
            pass

    class ProviderService:
        def create_provider_credential(self, **_: object) -> None:
            raise AssertionError("existing provider credential must be updated")

        def update_provider_credential(self, **_: object) -> None:
            pass

        def create_model_credential(self, *, model: str, **_: object) -> None:
            created.append(model)

        def update_model_credential(self, *, model: str, **_: object) -> None:
            updated.append(model)

    with Session(sqlite_engine) as session:
        session.add_all(
            [
                ProviderCredential(
                    tenant_id="tenant-1",
                    provider_name="langgenius/openai/openai",
                    credential_name="Campus managed",
                    encrypted_config="{}",
                ),
                ProviderModelCredential(
                    tenant_id="tenant-1",
                    provider_name="langgenius/openai/openai",
                    model_name="deepseek-v4-flash",
                    model_type=ModelType.LLM,
                    credential_name="Campus managed",
                    encrypted_config="{}",
                ),
            ]
        )
        session.commit()

        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai/openai",
            provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
            credential_name="Campus managed",
            api_key_field="openai_api_key",
            base_url_field="openai_api_base",
            base_url="http://model-gateway:3000/v1",
            models=parse_campus_models("llm:deepseek-v4-flash,text-embedding:bge-m3"),
            api_protocol="chat",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert updated == ["deepseek-v4-flash"]
    assert created == ["bge-m3"]


def _configurator(session: Session, spec: str) -> DifyModelConfigurator:
    return DifyModelConfigurator(
        session=session,
        provider="langgenius/openai/openai",
        provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
        credential_name="Campus managed",
        api_key_field="openai_api_key",
        base_url_field="openai_api_base",
        base_url="http://model-gateway:3000/v1",
        models=parse_campus_models(spec),
        api_protocol="chat",
        plugin_installer=SimpleNamespace(ensure_installed=lambda *_: None),
        provider_service=SimpleNamespace(),
    )


def _registered(session: Session, model_name: str, model_type: ModelType) -> ProviderModelCredential:
    return ProviderModelCredential(
        tenant_id="tenant-1",
        provider_name="langgenius/openai/openai",
        model_name=model_name,
        model_type=model_type,
        credential_name="Campus managed",
        encrypted_config="{}",
    )


def test_configuration_is_needed_when_a_configured_model_is_not_registered(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    with Session(sqlite_engine) as session:
        session.add(_registered(session, "deepseek-v4-flash", ModelType.LLM))
        session.commit()

        # The workspace was configured under a narrower list than the one in force.
        assert _configurator(session, "llm:deepseek-v4-flash,text-embedding:bge-m3").needs_configuration("tenant-1")


def test_configuration_is_not_needed_when_every_configured_model_is_registered(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    with Session(sqlite_engine) as session:
        session.add_all(
            [
                _registered(session, "deepseek-v4-flash", ModelType.LLM),
                _registered(session, "bge-m3", ModelType.TEXT_EMBEDDING),
            ]
        )
        session.commit()

        configurator = _configurator(session, "llm:deepseek-v4-flash,text-embedding:bge-m3")
        assert not configurator.needs_configuration("tenant-1")


def test_configuration_is_needed_when_a_model_is_registered_under_the_wrong_type(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    with Session(sqlite_engine) as session:
        # bge-m3 registered as a chat model is not the embedding slot a knowledge
        # base needs, so the workspace still has to be reconfigured.
        session.add_all(
            [
                _registered(session, "deepseek-v4-flash", ModelType.LLM),
                _registered(session, "bge-m3", ModelType.LLM),
            ]
        )
        session.commit()

        configurator = _configurator(session, "llm:deepseek-v4-flash,text-embedding:bge-m3")
        assert configurator.needs_configuration("tenant-1")


def test_configuration_is_scoped_to_the_workspace_being_asked_about(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(
        sqlite_engine,
        tables=[ProviderCredential.__table__, ProviderModelCredential.__table__],
    )
    with Session(sqlite_engine) as session:
        session.add_all(
            [
                _registered(session, "deepseek-v4-flash", ModelType.LLM),
                _registered(session, "bge-m3", ModelType.TEXT_EMBEDDING),
            ]
        )
        session.commit()

        configurator = _configurator(session, "llm:deepseek-v4-flash,text-embedding:bge-m3")
        assert configurator.needs_configuration("tenant-2")
