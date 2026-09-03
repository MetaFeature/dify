"""Campus deployment maintenance commands."""

import json
from dataclasses import asdict

import click
from sqlalchemy import select

from configs import dify_config
from extensions.ext_database import db
from models.campus import CampusWorkspaceBinding
from services.campus.dify_adapters import parse_campus_models
from services.campus.model_provider_reconciliation import CampusModelProviderReconciler


@click.group("campus-model-providers", help="Audit and reconcile Campus model providers.")
def campus_model_providers() -> None:
    pass


def _reconciler() -> tuple[CampusModelProviderReconciler, list[str]]:
    tenant_ids = list(
        db.session.scalars(select(CampusWorkspaceBinding.dify_tenant_id).order_by(CampusWorkspaceBinding.id))
    )
    return (
        CampusModelProviderReconciler(
            session=db.session(),
            target_provider=dify_config.CAMPUS_MODEL_PROVIDER,
            credential_name=dify_config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME,
            base_url=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL,
            models=parse_campus_models(dify_config.CAMPUS_MODEL_PROVIDER_MODELS),
        ),
        tenant_ids,
    )


@campus_model_providers.command("audit")
def audit_model_providers() -> None:
    reconciler, tenant_ids = _reconciler()
    summary = reconciler.audit(tenant_ids)
    click.echo(json.dumps(asdict(summary), sort_keys=True))
    if not summary.clean:
        raise click.ClickException("Campus model providers require reconciliation")


@campus_model_providers.command("reconcile")
def reconcile_model_providers() -> None:
    reconciler, tenant_ids = _reconciler()
    summary = reconciler.reconcile(tenant_ids)
    click.echo(json.dumps(asdict(summary), sort_keys=True))
    if not summary.clean:
        raise click.ClickException("Campus model provider reconciliation did not converge")
