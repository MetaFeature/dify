"""Campus deployment maintenance commands."""

import json
from dataclasses import asdict

import click
from sqlalchemy import select

from configs import dify_config
from extensions.ext_database import db
from models.campus import CampusGatewayBinding, CampusStudent, CampusWorkspaceBinding
from services.campus.dify_adapters import parse_campus_models
from services.campus.model_provider_reconciliation import CampusModelProviderReconciler


@click.group("campus-model-providers", help="Audit and reconcile Campus model providers and gateway identities.")
def campus_model_providers() -> None:
    pass


def _reconciler(model_spec: str | None = None) -> tuple[CampusModelProviderReconciler, list[str]]:
    tenant_ids = list(
        db.session.scalars(select(CampusWorkspaceBinding.dify_tenant_id).order_by(CampusWorkspaceBinding.id))
    )
    return (
        CampusModelProviderReconciler(
            session=db.session(),
            target_provider=dify_config.CAMPUS_MODEL_PROVIDER,
            credential_name=dify_config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME,
            base_url=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL,
            models=parse_campus_models(model_spec or dify_config.CAMPUS_MODEL_PROVIDER_MODELS),
            vision_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_VISION_MODELS.split(",") if item.strip()
            ),
            audio_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_AUDIO_MODELS.split(",") if item.strip()
            ),
            document_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_DOCUMENT_MODELS.split(",") if item.strip()
            ),
        ),
        tenant_ids,
    )


@campus_model_providers.command("audit")
@click.option("--models", "model_spec", help="Gateway catalog as comma-separated type:name entries.")
def audit_model_providers(model_spec: str | None) -> None:
    reconciler, tenant_ids = _reconciler(model_spec)
    summary = reconciler.audit(tenant_ids)
    click.echo(json.dumps(asdict(summary), sort_keys=True))
    if not summary.clean:
        raise click.ClickException("Campus model providers require reconciliation")


@campus_model_providers.command("reconcile")
@click.option("--models", "model_spec", help="Gateway catalog as comma-separated type:name entries.")
def reconcile_model_providers(model_spec: str | None) -> None:
    reconciler, tenant_ids = _reconciler(model_spec)
    summary = reconciler.reconcile(tenant_ids)
    click.echo(json.dumps(asdict(summary), sort_keys=True))
    if not summary.clean:
        raise click.ClickException("Campus model provider reconciliation did not converge")


@campus_model_providers.command("sync-gateway-identities")
def sync_gateway_identities() -> None:
    from controllers.console.campus_dependencies import newapi_client

    rows = db.session.execute(
        select(CampusStudent, CampusGatewayBinding)
        .join(CampusGatewayBinding, CampusGatewayBinding.student_id == CampusStudent.id)
        .order_by(CampusStudent.id)
    ).all()
    gateway = newapi_client()
    for student, binding in rows:
        gateway.update_managed_identity(
            binding.gateway_token_id,
            student.student_number,
            student.display_name,
        )
    click.echo(json.dumps({"synced_gateway_identities": len(rows)}, sort_keys=True))


@click.group("campus-model-accounts", help="Reconcile one NewAPI model account for every roster student.")
def campus_model_accounts() -> None:
    pass


@campus_model_accounts.command("reconcile")
def reconcile_model_accounts() -> None:
    from controllers.console.campus_dependencies import model_account_service

    summary = model_account_service().reconcile()
    click.echo(json.dumps(asdict(summary), sort_keys=True))
