"""Own the administrator-managed platform limits for student knowledge bases.

Two limits are enforced: how many knowledge bases a student workspace may hold
and how many files one of those knowledge bases may hold. The environment values
are the platform defaults; an administrator may replace both with a single
setting row from the Campus administration portal.

The core Dify dataset paths call :func:`ensure_dataset_quota` and
:func:`ensure_document_quota`, both of which are no-ops unless ``CAMPUS_ENABLED``
is on and the tenant is a Campus student workspace.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from configs import dify_config
from models.campus import CampusAuditEvent, CampusKnowledgeLimitSetting, CampusWorkspaceBinding
from models.dataset import Dataset, Document
from services.campus.errors import CampusKnowledgeLimitExceededError, CampusValidationError

SETTING_KEY = "platform-default"

DATASET_LIMIT_MESSAGE = "已达到平台限制：每个用户工作区最多可创建 {limit} 个知识库。"
DOCUMENT_LIMIT_MESSAGE = "已达到平台限制：单个知识库最多可包含 {limit} 个文件。"


@dataclass(frozen=True)
class KnowledgeLimitState:
    """The limits in force plus the provenance an administrator needs."""

    max_datasets_per_workspace: int
    max_documents_per_dataset: int
    platform_max_datasets_per_workspace: int
    platform_max_documents_per_dataset: int
    configured_max_datasets_per_workspace: int | None
    configured_max_documents_per_dataset: int | None

    @property
    def is_default(self) -> bool:
        return (
            self.configured_max_datasets_per_workspace is None
            and self.configured_max_documents_per_dataset is None
        )


class KnowledgeLimitService:
    def __init__(
        self,
        *,
        session: Session,
        platform_max_datasets_per_workspace: int,
        platform_max_documents_per_dataset: int,
    ) -> None:
        if platform_max_datasets_per_workspace < 1 or platform_max_documents_per_dataset < 1:
            raise ValueError("platform knowledge limits must be positive")
        self._session = session
        self._platform_max_datasets_per_workspace = platform_max_datasets_per_workspace
        self._platform_max_documents_per_dataset = platform_max_documents_per_dataset

    def state(self) -> KnowledgeLimitState:
        row = self._row()
        configured_datasets = row.max_datasets_per_workspace if row is not None else None
        configured_documents = row.max_documents_per_dataset if row is not None else None
        return KnowledgeLimitState(
            max_datasets_per_workspace=self._effective(configured_datasets, self._platform_max_datasets_per_workspace),
            max_documents_per_dataset=self._effective(configured_documents, self._platform_max_documents_per_dataset),
            platform_max_datasets_per_workspace=self._platform_max_datasets_per_workspace,
            platform_max_documents_per_dataset=self._platform_max_documents_per_dataset,
            configured_max_datasets_per_workspace=configured_datasets,
            configured_max_documents_per_dataset=configured_documents,
        )

    def effective_limits(self) -> tuple[int, int]:
        state = self.state()
        return state.max_datasets_per_workspace, state.max_documents_per_dataset

    def set_limits(
        self,
        *,
        max_datasets_per_workspace: int,
        max_documents_per_dataset: int,
        actor_account_id: str,
    ) -> KnowledgeLimitState:
        if max_datasets_per_workspace < 1 or max_documents_per_dataset < 1:
            raise CampusValidationError("knowledge limits must be positive")
        row = self._row(for_update=True)
        previous = (row.max_datasets_per_workspace, row.max_documents_per_dataset) if row is not None else (None, None)
        if row is None:
            row = CampusKnowledgeLimitSetting(
                setting_key=SETTING_KEY,
                max_datasets_per_workspace=max_datasets_per_workspace,
                max_documents_per_dataset=max_documents_per_dataset,
                updated_by_account_id=actor_account_id,
            )
            self._session.add(row)
        else:
            row.max_datasets_per_workspace = max_datasets_per_workspace
            row.max_documents_per_dataset = max_documents_per_dataset
            row.updated_by_account_id = actor_account_id
        self._audit(
            "knowledge_limits.changed",
            actor_account_id,
            previous=previous,
            current=(max_datasets_per_workspace, max_documents_per_dataset),
        )
        self._session.commit()
        return self.state()

    def restore_default(self, *, actor_account_id: str) -> KnowledgeLimitState:
        row = self._row(for_update=True)
        previous = (row.max_datasets_per_workspace, row.max_documents_per_dataset) if row is not None else (None, None)
        if row is not None:
            self._session.delete(row)
        self._audit(
            "knowledge_limits.restored",
            actor_account_id,
            previous=previous,
            current=(self._platform_max_datasets_per_workspace, self._platform_max_documents_per_dataset),
        )
        self._session.commit()
        return self.state()

    def count_workspace_datasets(self, tenant_id: str) -> int:
        """Count every knowledge base the workspace holds.

        External knowledge bases and RAG pipeline knowledge bases are Dataset
        rows too, so they hold a place in the same quota; anything else would let
        a student sidestep the limit by picking another creation entry point.
        """
        return int(
            self._session.scalar(
                select(func.count(Dataset.id)).where(Dataset.tenant_id == tenant_id)
            )
            or 0
        )

    def count_dataset_documents(self, dataset_id: str) -> int:
        """Count the files that hold a place in one knowledge base.

        Archived documents release their place; documents that are still
        uploading, disabled, or failed keep theirs, so a batch being indexed
        cannot overshoot the limit.
        """
        return int(
            self._session.scalar(
                select(func.count(Document.id)).where(
                    Document.dataset_id == dataset_id,
                    Document.archived.is_(False),
                )
            )
            or 0
        )

    def _effective(self, configured: int | None, platform_default: int) -> int:
        # The write path rejects values below one, so a non-positive row can only
        # come from manual database edits; falling back keeps the limit usable
        # instead of blocking every creation request.
        if configured is None or configured < 1:
            return platform_default
        return configured

    def _row(self, *, for_update: bool = False) -> CampusKnowledgeLimitSetting | None:
        statement = select(CampusKnowledgeLimitSetting).where(CampusKnowledgeLimitSetting.setting_key == SETTING_KEY)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def _audit(
        self,
        action: str,
        actor_account_id: str,
        *,
        previous: tuple[int | None, int | None],
        current: tuple[int, int],
    ) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="knowledge_limit_setting",
                target_id=SETTING_KEY,
                details_json=json.dumps(
                    {
                        "previous_max_datasets_per_workspace": previous[0],
                        "previous_max_documents_per_dataset": previous[1],
                        "max_datasets_per_workspace": current[0],
                        "max_documents_per_dataset": current[1],
                    },
                    separators=(",", ":"),
                ),
            )
        )


def knowledge_limit_service(session: Session) -> KnowledgeLimitService:
    return KnowledgeLimitService(
        session=session,
        platform_max_datasets_per_workspace=dify_config.CAMPUS_KNOWLEDGE_MAX_DATASETS_PER_WORKSPACE,
        platform_max_documents_per_dataset=dify_config.CAMPUS_KNOWLEDGE_MAX_DOCUMENTS_PER_DATASET,
    )


def is_student_workspace(session: Session, tenant_id: str) -> bool:
    """Report whether the tenant is a Campus student workspace rather than an admin one."""
    return (
        session.scalar(
            select(CampusWorkspaceBinding.id).where(CampusWorkspaceBinding.dify_tenant_id == tenant_id).limit(1)
        )
        is not None
    )


def ensure_dataset_quota(tenant_id: str, *, session: Session) -> None:
    """Reject creating one more knowledge base than the workspace is allowed.

    No-op unless Campus is enabled and the tenant belongs to a student; the
    administration workspace keeps upstream behaviour.
    """
    if not dify_config.CAMPUS_ENABLED:
        return
    if not is_student_workspace(session, tenant_id):
        return
    service = knowledge_limit_service(session)
    limit, _ = service.effective_limits()
    if service.count_workspace_datasets(tenant_id) >= limit:
        raise CampusKnowledgeLimitExceededError(DATASET_LIMIT_MESSAGE.format(limit=limit))


def ensure_document_quota(dataset: Dataset, *, session: Session, incoming: int = 1) -> None:
    """Reject adding files beyond the per-knowledge-base limit.

    ``incoming`` is how many files the caller is about to create; a batch that
    would overshoot in one request is rejected as a whole. Campus callers reach
    this through the two wrappers below so each core call site stays one line.
    """
    if not dify_config.CAMPUS_ENABLED:
        return
    if incoming < 1:
        return
    if not is_student_workspace(session, dataset.tenant_id):
        return
    service = knowledge_limit_service(session)
    _, limit = service.effective_limits()
    if service.count_dataset_documents(dataset.id) + incoming > limit:
        raise CampusKnowledgeLimitExceededError(DOCUMENT_LIMIT_MESSAGE.format(limit=limit))


def ensure_request_document_quota(knowledge_config: Any, dataset: Dataset, *, session: Session) -> None:
    """Enforce the limit for a console/service-API document request.

    Editing an existing document adds no file, so an update keeps its place; the
    batch size comes from the request payload.
    """
    if knowledge_config.original_document_id:
        return
    ensure_document_quota(dataset, session=session, incoming=_incoming_documents(knowledge_config))


def _incoming_documents(knowledge_config: Any) -> int:
    """Count the files one creation request would add.

    Mirrors the upstream billing counter: an unreadable payload still counts as
    one file so an unexpected shape cannot slip past the limit.
    """
    data_source = getattr(knowledge_config, "data_source", None)
    info_list = getattr(data_source, "info_list", None) if data_source is not None else None
    if info_list is None:
        return 1
    if info_list.data_source_type == "upload_file":
        file_info_list = info_list.file_info_list
        return len(file_info_list.file_ids) if file_info_list is not None else 1
    if info_list.data_source_type == "notion_import":
        return sum(len(notion_info.pages) for notion_info in info_list.notion_info_list or []) or 1
    if info_list.data_source_type == "website_crawl":
        website_info = info_list.website_info_list
        return len(website_info.urls) if website_info is not None else 1
    return 1
