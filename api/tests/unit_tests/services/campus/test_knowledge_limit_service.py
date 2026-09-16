from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from configs import dify_config
from models.campus import (
    CampusAuditEvent,
    CampusKnowledgeLimitSetting,
    CampusStudent,
    CampusWorkspaceBinding,
    StudentStatus,
)
from models.dataset import Dataset, Document
from services.campus.errors import CampusKnowledgeLimitExceededError, CampusValidationError
from services.campus.knowledge_limit_service import (
    KnowledgeLimitService,
    ensure_dataset_quota,
    ensure_document_quota,
    ensure_request_document_quota,
)

STUDENT_TENANT = "tenant-student"
ADMIN_TENANT = "tenant-admin"


def _dataset(tenant_id: str, name: str) -> Dataset:
    return Dataset(
        tenant_id=tenant_id,
        name=name,
        data_source_type="upload_file",
        created_by="account-1",
        provider="vendor",
    )


def _document(tenant_id: str, dataset_id: str, name: str, *, archived: bool = False) -> Document:
    return Document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        position=1,
        data_source_type="upload_file",
        data_source_info="{}",
        batch="batch-1",
        name=name,
        created_from="web",
        created_by="account-1",
        indexing_status="completed",
        doc_form="text_model",
        archived=archived,
    )


class _InfoList:
    def __init__(self, data_source_type: str, file_ids: list[str] | None = None) -> None:
        self.data_source_type = data_source_type
        self.file_info_list = type("_Files", (), {"file_ids": file_ids or []})() if file_ids is not None else None
        self.notion_info_list = None
        self.website_info_list = None


class _KnowledgeConfig:
    def __init__(self, data_source_type: str, file_ids: list[str] | None = None, original_document_id=None) -> None:
        self.data_source = type("_Source", (), {"info_list": _InfoList(data_source_type, file_ids)})()
        self.original_document_id = original_document_id


@pytest.fixture
def limit_session(sqlite_engine, monkeypatch) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusWorkspaceBinding.__table__,
        CampusKnowledgeLimitSetting.__table__,
        CampusAuditEvent.__table__,
        Dataset.__table__,
        Document.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    monkeypatch.setattr(dify_config, "CAMPUS_ENABLED", True)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        student = CampusStudent(
            student_number="20260001",
            display_name="Student One",
            status=StudentStatus.ACTIVE,
            initial_allowance_usd=Decimal(20),
        )
        session.add(student)
        session.flush()
        session.add(
            CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id="account-1",
                dify_tenant_id=STUDENT_TENANT,
            )
        )
        session.commit()
        yield session


def _service(session: Session, datasets: int = 1, documents: int = 3) -> KnowledgeLimitService:
    return KnowledgeLimitService(
        session=session,
        platform_max_datasets_per_workspace=datasets,
        platform_max_documents_per_dataset=documents,
    )


def _override_limits(session: Session, datasets: int, documents: int) -> None:
    _service(session, datasets, documents).set_limits(
        max_datasets_per_workspace=datasets,
        max_documents_per_dataset=documents,
        actor_account_id="admin-1",
    )


def test_platform_defaults_follow_the_campus_configuration(monkeypatch):
    """Pins the shipped defaults: 1 knowledge base, 5 files per knowledge base."""
    assert dify_config.CAMPUS_KNOWLEDGE_MAX_DATASETS_PER_WORKSPACE == 1
    assert dify_config.CAMPUS_KNOWLEDGE_MAX_DOCUMENTS_PER_DATASET == 5


def test_falls_back_to_the_platform_defaults_until_an_administrator_sets_limits(limit_session: Session):
    state = _service(limit_session).state()

    assert state.max_datasets_per_workspace == 1
    assert state.max_documents_per_dataset == 3
    assert state.is_default is True


def test_setting_limits_persists_an_audited_override(limit_session: Session):
    state = _service(limit_session).set_limits(
        max_datasets_per_workspace=2,
        max_documents_per_dataset=5,
        actor_account_id="admin-1",
    )

    assert (state.max_datasets_per_workspace, state.max_documents_per_dataset) == (2, 5)
    assert state.is_default is False
    assert limit_session.query(CampusKnowledgeLimitSetting).count() == 1
    event = limit_session.query(CampusAuditEvent).one()
    assert event.action == "knowledge_limits.changed"
    assert event.target_type == "knowledge_limit_setting"


def test_restoring_limits_drops_the_override(limit_session: Session):
    service = _service(limit_session)
    service.set_limits(max_datasets_per_workspace=2, max_documents_per_dataset=5, actor_account_id="admin-1")

    state = service.restore_default(actor_account_id="admin-1")

    assert (state.max_datasets_per_workspace, state.max_documents_per_dataset) == (1, 3)
    assert state.is_default is True
    assert limit_session.query(CampusKnowledgeLimitSetting).count() == 0


@pytest.mark.parametrize(("datasets", "documents"), [(0, 3), (1, 0), (-1, 3)])
def test_setting_rejects_non_positive_limits(limit_session: Session, datasets: int, documents: int):
    with pytest.raises(CampusValidationError):
        _service(limit_session).set_limits(
            max_datasets_per_workspace=datasets,
            max_documents_per_dataset=documents,
            actor_account_id="admin-1",
        )


def test_dataset_quota_blocks_the_second_knowledge_base_and_ignores_other_workspaces(limit_session: Session):
    ensure_dataset_quota(STUDENT_TENANT, session=limit_session)

    limit_session.add(_dataset(STUDENT_TENANT, "first"))
    limit_session.commit()

    with pytest.raises(CampusKnowledgeLimitExceededError, match="最多可创建 1 个知识库"):
        ensure_dataset_quota(STUDENT_TENANT, session=limit_session)

    # The administrator workspace has no binding, so it keeps upstream behaviour.
    ensure_dataset_quota(ADMIN_TENANT, session=limit_session)


def test_dataset_quota_is_a_no_op_when_campus_is_disabled(limit_session: Session, monkeypatch):
    monkeypatch.setattr(dify_config, "CAMPUS_ENABLED", False)
    limit_session.add(_dataset(STUDENT_TENANT, "first"))
    limit_session.commit()

    ensure_dataset_quota(STUDENT_TENANT, session=limit_session)


def test_document_quota_counts_the_whole_batch_and_ignores_archived_files(limit_session: Session):
    _override_limits(limit_session, 1, 3)
    dataset = _dataset(STUDENT_TENANT, "only")
    limit_session.add(dataset)
    limit_session.commit()

    ensure_request_document_quota(_KnowledgeConfig("upload_file", ["f1"]), dataset, session=limit_session)

    limit_session.add_all(
        [
            _document(STUDENT_TENANT, dataset.id, "one"),
            _document(STUDENT_TENANT, dataset.id, "two"),
            _document(STUDENT_TENANT, dataset.id, "archived", archived=True),
        ]
    )
    limit_session.commit()

    # Two live files exist and one is archived, so one more still fits.
    ensure_request_document_quota(_KnowledgeConfig("upload_file", ["f4"]), dataset, session=limit_session)

    limit_session.add(_document(STUDENT_TENANT, dataset.id, "three"))
    limit_session.commit()

    with pytest.raises(CampusKnowledgeLimitExceededError, match="最多可包含 3 个文件"):
        ensure_request_document_quota(_KnowledgeConfig("upload_file", ["f4"]), dataset, session=limit_session)

    # A batch that would overshoot in one request is rejected too.
    with pytest.raises(CampusKnowledgeLimitExceededError):
        ensure_request_document_quota(_KnowledgeConfig("upload_file", ["f4", "f5"]), dataset, session=limit_session)


def test_document_quota_skips_updates_and_unknown_data_sources_count_as_one(limit_session: Session):
    _override_limits(limit_session, 1, 3)
    dataset = _dataset(STUDENT_TENANT, "only")
    limit_session.add(dataset)
    limit_session.commit()
    limit_session.add_all([_document(STUDENT_TENANT, dataset.id, name) for name in ("one", "two", "three")])
    limit_session.commit()

    # Editing an existing document adds no file, so it keeps its place.
    ensure_request_document_quota(
        _KnowledgeConfig("upload_file", ["f4"], original_document_id="doc-1"), dataset, session=limit_session
    )

    with pytest.raises(CampusKnowledgeLimitExceededError):
        ensure_request_document_quota(_KnowledgeConfig("website_crawl"), dataset, session=limit_session)


def test_document_quota_primitive_backs_the_rag_pipeline_batch(limit_session: Session):
    _override_limits(limit_session, 1, 3)
    """The pipeline creates Document rows directly, so it passes an explicit count."""
    dataset = _dataset(STUDENT_TENANT, "pipeline")
    dataset.runtime_mode = "rag_pipeline"
    limit_session.add(dataset)
    limit_session.commit()

    # A batch smaller than the limit fits...
    ensure_document_quota(dataset, session=limit_session, incoming=3)
    limit_session.add_all([_document(STUDENT_TENANT, dataset.id, "one")])
    limit_session.commit()

    # ...but a batch that would overshoot is rejected as a whole.
    with pytest.raises(CampusKnowledgeLimitExceededError, match="最多可包含 3 个文件"):
        ensure_document_quota(dataset, session=limit_session, incoming=3)

    # An empty batch and non-Campus tenants stay unguarded.
    ensure_document_quota(dataset, session=limit_session, incoming=0)
    ensure_document_quota(_dataset(ADMIN_TENANT, "admin"), session=limit_session, incoming=99)
