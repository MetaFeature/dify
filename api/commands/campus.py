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


@campus_model_providers.command("stale-datasets")
@click.option("--models", "model_spec", help="Gateway catalog as comma-separated type:name entries.")
def stale_datasets(model_spec: str | None) -> None:
    """List knowledge bases pinned to a model the gateway catalog dropped.

    `audit` already counts them; this prints the rows an operator has to migrate.
    """
    reconciler, tenant_ids = _reconciler(model_spec)
    rows = reconciler.stale_datasets(tenant_ids)
    click.echo(json.dumps([asdict(row) for row in rows], ensure_ascii=False, sort_keys=True))
    if not rows:
        click.echo("Every high-quality knowledge base uses a published model.")
        return
    click.echo(
        f"{len(rows)} knowledge base entries need migration; "
        "run 'flask campus-model-providers repair-datasets'."
    )


@campus_model_providers.command("repair-datasets")
@click.option("--models", "model_spec", help="Gateway catalog as comma-separated type:name entries.")
@click.option("--apply", "apply_changes", is_flag=True, help="Actually write the migration and re-index.")
def repair_datasets(model_spec: str | None, apply_changes: bool) -> None:
    """Move knowledge bases off models the catalog dropped, then re-index them.

    The migration drives Dify's own console endpoints with a session issued for
    the owning student, so Dify decides the collection binding, clears the old
    vectors and re-embeds through the gateway. Re-embedding costs quota and can
    take minutes on a large knowledge base, which is why nothing runs without
    --apply.
    """
    from graphon.model_runtime.entities.model_entities import ModelType
    from services.campus.dataset_repair_service import DatasetRepairService

    reconciler, tenant_ids = _reconciler(model_spec)
    stale = reconciler.stale_datasets(tenant_ids)
    if not stale:
        click.echo("Every high-quality knowledge base uses a published model.")
        return

    service = DatasetRepairService(
        session=db.session(),
        embedding_model=reconciler.replacement_model(ModelType.TEXT_EMBEDDING),
        rerank_model=reconciler.replacement_model(ModelType.RERANK),
        provider=dify_config.CAMPUS_MODEL_PROVIDER,
        apply=apply_changes,
    )
    for row in stale:
        target = service.target(row.model_type)
        click.echo(f"{row.name}（{row.tenant_id}）：{row.model_type} {row.model_name} → {target}")
    report = service.repair([row.dataset_id for row in stale])
    click.echo(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not apply_changes:
        click.echo("Dry run: nothing was changed. Re-run with --apply to migrate.")


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
