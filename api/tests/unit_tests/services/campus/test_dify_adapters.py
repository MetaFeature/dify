from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models.account import Tenant, TenantAccountJoin, TenantAccountRole
from models.provider import ProviderCredential
from services.campus import dify_adapters
from services.campus.dify_adapters import (
    DifyModelConfigurator,
    DifyWorkspaceProvisioner,
    MarketplaceProviderPluginInstaller,
)
from services.campus.errors import CampusProvisioningError


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


def test_model_configurator_installs_provider_plugin_before_credential(sqlite_engine) -> None:
    ProviderCredential.metadata.create_all(sqlite_engine, tables=[ProviderCredential.__table__])
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
            }
            assert credential_name == "Campus managed"
            events.append("credential-created")

        def update_provider_credential(self, **_: object) -> None:
            raise AssertionError("new workspace must create its provider credential")

    with Session(sqlite_engine) as session:
        configurator = DifyModelConfigurator(
            session=session,
            provider="langgenius/openai/openai",
            provider_plugin_unique_identifier="langgenius/openai:1.0.4@checksum",
            credential_name="Campus managed",
            api_key_field="openai_api_key",
            base_url_field="openai_api_base",
            base_url="http://model-gateway:3000/v1",
            plugin_installer=PluginInstaller(),
            provider_service=ProviderService(),
        )

        configurator.configure("tenant-1", "managed-secret")

    assert events == ["plugin-installed", "credential-created"]


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


def test_marketplace_provider_installer_translates_daemon_failure(monkeypatch: pytest.MonkeyPatch) -> None:
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
