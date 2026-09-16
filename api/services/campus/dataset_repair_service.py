"""Move knowledge bases off a model the Campus gateway catalog dropped.

The gateway catalog is the authority for what a workspace may use. When a model
leaves it, a knowledge base that still names it keeps failing: an embedding model
that is gone makes every upload fail with the gateway's "no available channel",
and a reranking model that is gone breaks retrieval even after indexing
succeeds. This migrates those knowledge bases onto a published model.

The migration drives Dify's own console endpoints with a session issued for the
owning student, so Dify itself decides the collection binding, clears the old
vectors and re-embeds through the gateway -- the same path the student's browser
takes. The alternative (writing dataset rows and vector collections directly)
would have to reproduce Dify's binding, indexing and summary bookkeeping.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusStudent, CampusWorkspaceBinding
from models.dataset import Dataset, Document
from services.campus.dify_adapters import DifySessionIssuer
from services.campus.errors import CampusValidationError

# The command runs inside the api container, where gunicorn serves this port.
CONSOLE_API = "http://127.0.0.1:5001/console/api"
INDEXING_TECHNIQUE = "high_quality"


class SessionIssuer(Protocol):
    def issue(self, account_id: str, tenant_id: str, *, ip_address: str | None) -> Any: ...


class DatasetRepairService:
    """Migrate one workspace's knowledge bases onto the published models."""

    def __init__(
        self,
        *,
        session: Session,
        embedding_model: str | None,
        rerank_model: str | None,
        provider: str,
        apply: bool = False,
        session_issuer: SessionIssuer | None = None,
    ) -> None:
        self._session = session
        self._embedding_model = embedding_model
        self._rerank_model = rerank_model
        self._provider = provider
        self._apply = apply
        self._issuer = session_issuer or DifySessionIssuer(session=session)

    def target(self, model_type: str) -> str | None:
        return self._embedding_model if model_type == "text-embedding" else self._rerank_model

    def repair(self, dataset_ids: list[str]) -> dict[str, Any]:
        """Report, and when ``apply`` is set perform, the migration of each dataset."""
        report: dict[str, Any] = {
            "requested": len(dataset_ids),
            "migrated": 0,
            "retried": 0,
            "verified": 0,
            "errors": [],
        }
        for dataset_id in dataset_ids:
            dataset = self._session.get(Dataset, dataset_id)
            if dataset is None:
                report["errors"].append(f"{dataset_id}: knowledge base not found")
                continue
            try:
                outcome = self._repair_one(dataset)
            except (CampusValidationError, requests.RequestException) as error:
                report["errors"].append(f"{dataset.name}: {error}")
                continue
            report["migrated"] += int(outcome["migrated"])
            report["retried"] += outcome["retried"]
            report["verified"] += int(outcome["verified"])
        return report

    def _repair_one(self, dataset: Dataset) -> dict[str, Any]:
        payload = self._migration_payload(dataset)
        outcome = {"migrated": False, "retried": 0, "verified": False}
        if not payload and not self._has_unfinished_documents(dataset):
            return outcome
        if not self._apply:
            return {**outcome, "migrated": bool(payload)}

        http = self._console_session(dataset.tenant_id)
        if payload:
            response = http.patch(f"{CONSOLE_API}/datasets/{dataset.id}", json=payload)
            response.raise_for_status()
            outcome["migrated"] = True
            self._session.expire_all()

        unfinished = self._unfinished_documents(dataset)
        if unfinished:
            retry = http.post(
                f"{CONSOLE_API}/datasets/{dataset.id}/retry",
                json={"document_ids": [document.id for document in unfinished]},
            )
            retry.raise_for_status()
            outcome["retried"] = len(unfinished)

        # Retrieval is what a student actually does; a 200 here means both the
        # embedding and the reranking call reached the gateway successfully.
        hit = http.post(f"{CONSOLE_API}/datasets/{dataset.id}/hit-testing", json={"query": dataset.name})
        outcome["verified"] = hit.status_code == 200
        if not outcome["verified"]:
            raise CampusValidationError(f"retrieval still fails: HTTP {hit.status_code} {hit.text[:200]}")
        return outcome

    def _migration_payload(self, dataset: Dataset) -> dict[str, Any]:
        """The PATCH body that moves this dataset onto published models."""
        payload: dict[str, Any] = {}
        if dataset.embedding_model != self._embedding_model:
            if not self._embedding_model:
                raise CampusValidationError("the catalog publishes no embedding model")
            payload["indexing_technique"] = INDEXING_TECHNIQUE
            payload["embedding_model"] = self._embedding_model
            payload["embedding_model_provider"] = self._provider
        retrieval_model = dataset.retrieval_model or {}
        rerank_model = (retrieval_model.get("reranking_model") or {}).get("reranking_model_name")
        if retrieval_model.get("reranking_enable") and rerank_model and rerank_model != self._rerank_model:
            if not self._rerank_model:
                raise CampusValidationError("the catalog publishes no reranking model")
            payload["indexing_technique"] = payload.get("indexing_technique", INDEXING_TECHNIQUE)
            payload["retrieval_model"] = {
                **retrieval_model,
                "reranking_model": {
                    "reranking_provider_name": self._provider,
                    "reranking_model_name": self._rerank_model,
                },
            }
        return payload

    def _unfinished_documents(self, dataset: Dataset) -> list[Document]:
        return list(
            self._session.scalars(
                select(Document).where(Document.dataset_id == dataset.id, Document.indexing_status != "completed")
            )
        )

    def _has_unfinished_documents(self, dataset: Dataset) -> bool:
        return bool(self._unfinished_documents(dataset))

    def _console_session(self, tenant_id: str) -> requests.Session:
        binding = self._session.scalar(
            select(CampusWorkspaceBinding).where(CampusWorkspaceBinding.dify_tenant_id == tenant_id)
        )
        if binding is None:
            raise CampusValidationError(f"tenant {tenant_id} has no Campus workspace binding")
        student = self._session.get(CampusStudent, binding.student_id)
        if student is None:
            raise CampusValidationError(f"tenant {tenant_id} has no Campus student")
        tokens = self._issuer.issue(binding.dify_account_id, binding.dify_tenant_id, ip_address=None)
        session = requests.Session()
        session.cookies.set("access_token", tokens.access_token)
        session.cookies.set("csrf_token", tokens.csrf_token)
        session.headers.update(
            {"Authorization": f"Bearer {tokens.access_token}", "X-CSRF-Token": tokens.csrf_token}
        )
        return session
