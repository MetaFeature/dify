from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusPortalSession, CampusStudent, StudentStatus
from services.campus.domain import StudentIdentity
from services.campus.errors import CampusValidationError, StudentSuspendedError
from services.campus.student_service import StudentAdministrationService


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusAuditEvent.__table__, CampusPortalSession.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


def test_roster_sync_is_idempotent_and_updates_metadata(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    initial = StudentIdentity(student_number="20260001", display_name="Old Name", cohort="2026-A")
    changed = StudentIdentity(student_number="20260001", display_name="New Name", cohort="2026-B")

    first = service.sync_students([initial], actor_account_id="admin-1")
    second = service.sync_students([changed], actor_account_id="admin-1")

    students = list(campus_session.scalars(select(CampusStudent)).all())
    assert first.created == 1
    assert second.created == 0
    assert len(students) == 1
    assert students[0].display_name == "New Name"
    assert students[0].cohort == "2026-B"
    assert students[0].initial_allowance_usd == Decimal(20)


def test_missing_roster_row_does_not_suspend_existing_student(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="Student One")],
        actor_account_id="admin-1",
    )

    service.sync_students([], actor_account_id="admin-1")

    student = campus_session.scalar(select(CampusStudent).where(CampusStudent.student_number == "20260001"))
    assert student is not None
    assert student.status is StudentStatus.ACTIVE


def test_roster_sync_rejects_duplicate_student_numbers_as_domain_validation(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    duplicate = StudentIdentity(student_number="20260001", display_name="Student One")

    with pytest.raises(CampusValidationError, match="unique"):
        service.sync_students([duplicate, duplicate], actor_account_id="admin-1")


def test_suspend_is_explicit_and_audited(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="Student One")],
        actor_account_id="admin-1",
    )

    student = service.set_status("20260001", StudentStatus.SUSPENDED, actor_account_id="admin-2")

    audit = campus_session.scalar(select(CampusAuditEvent).order_by(CampusAuditEvent.created_at.desc()))
    assert student.status is StudentStatus.SUSPENDED
    assert audit is not None
    assert audit.actor_account_id == "admin-2"
    assert audit.action == "student.status_changed"
    with pytest.raises(StudentSuspendedError):
        service.require_active_student(student.id)


def _roster(*rows: tuple[str, str]) -> list[StudentIdentity]:
    return [StudentIdentity(student_number=number, display_name=name) for number, name in rows]


@pytest.fixture
def searchable(campus_session: Session) -> StudentAdministrationService:
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        _roster(
            ("20260001", "张三"),
            ("20260002", "张小明"),
            ("20260011", "李四"),
            ("2026_00%3", "王五"),
        ),
        actor_account_id="admin-1",
    )
    return service


def test_list_students_matches_a_partial_student_number(searchable: StudentAdministrationService):
    numbers = [student.student_number for student in searchable.list_students(keyword="2026000")]

    assert numbers == ["20260001", "20260002"]


def test_list_students_matches_a_partial_display_name(searchable: StudentAdministrationService):
    numbers = [student.student_number for student in searchable.list_students(keyword="张")]

    assert numbers == ["20260001", "20260002"]


def test_list_students_matches_either_field_without_duplicating_rows(searchable: StudentAdministrationService):
    """`2026` is in every number, and 张三/李四 hit by name too."""
    numbers = [student.student_number for student in searchable.list_students(keyword="2026")]

    assert numbers == ["20260001", "20260002", "20260011", "2026_00%3"]


def test_list_students_treats_like_wildcards_in_the_keyword_as_literal_text(
    searchable: StudentAdministrationService,
):
    """A keyword of `%` must not select every row, and `_` must not match any char."""
    assert [s.student_number for s in searchable.list_students(keyword="%")] == ["2026_00%3"]
    assert [s.student_number for s in searchable.list_students(keyword="2026_00_3")] == []


def test_list_students_without_a_keyword_still_pages_everything(searchable: StudentAdministrationService):
    assert len(searchable.list_students()) == 4
    assert len(searchable.list_students(limit=2, offset=0, keyword="2026")) == 2
    assert len(searchable.list_students(limit=2, offset=2, keyword="2026")) == 2
    assert searchable.list_students(limit=2, offset=4, keyword="2026") == []


def test_blank_keyword_is_ignored_rather_than_matching_nothing(searchable: StudentAdministrationService):
    assert len(searchable.list_students(keyword="   ")) == 4


def test_rename_changes_only_the_display_name(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="旧名字")], actor_account_id="admin-1"
    )

    renamed = service.rename("20260001", display_name="  新名字  ", actor_account_id="admin-1")

    assert renamed.display_name == "新名字"
    assert renamed.student_number == "20260001"
    actions = [event.action for event in campus_session.scalars(select(CampusAuditEvent)).all()]
    assert "student.renamed" in actions


def test_rename_rejects_a_blank_name(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="旧名字")], actor_account_id="admin-1"
    )

    with pytest.raises(CampusValidationError):
        service.rename("20260001", display_name="   ", actor_account_id="admin-1")


def test_soft_delete_is_hidden_from_the_roster_but_keeps_every_row(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students([StudentIdentity(student_number="20260001", display_name="张三")], actor_account_id="admin-1")

    deleted = service.soft_delete("20260001", actor_account_id="admin-1")

    assert deleted.deleted_at is not None
    assert service.list_students() == []
    assert [s.student_number for s in service.list_students(include_deleted=True)] == ["20260001"]


def test_soft_delete_signs_the_student_out(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students([StudentIdentity(student_number="20260001", display_name="张三")], actor_account_id="admin-1")
    student = service.get_student("20260001")
    campus_session.add(
        CampusPortalSession(student_id=student.id, token_hash="hash-1", expires_at=datetime(2030, 1, 1))
    )
    campus_session.commit()

    service.soft_delete("20260001", actor_account_id="admin-1")

    session_row = campus_session.query(CampusPortalSession).one()
    assert session_row.revoked_at is not None


def test_soft_delete_and_restore_are_idempotent(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students([StudentIdentity(student_number="20260001", display_name="张三")], actor_account_id="admin-1")

    first = service.soft_delete("20260001", actor_account_id="admin-1")
    stamp = first.deleted_at
    again = service.soft_delete("20260001", actor_account_id="admin-1")
    assert again.deleted_at == stamp

    service.restore("20260001", actor_account_id="admin-1")
    restored = service.restore("20260001", actor_account_id="admin-1")

    assert restored.deleted_at is None
    assert [s.student_number for s in service.list_students()] == ["20260001"]
    actions = [event.action for event in campus_session.scalars(select(CampusAuditEvent)).all()]
    assert actions.count("student.deleted") == 1
    assert actions.count("student.restored") == 1


def test_a_deleted_student_is_still_searchable_in_the_restore_view(campus_session: Session):
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [
            StudentIdentity(student_number="20260001", display_name="张三"),
            StudentIdentity(student_number="20260002", display_name="李四"),
        ],
        actor_account_id="admin-1",
    )
    service.soft_delete("20260001", actor_account_id="admin-1")

    hidden = [s.student_number for s in service.list_students(keyword="2026")]
    shown = [s.student_number for s in service.list_students(keyword="2026", include_deleted=True)]

    assert hidden == ["20260002"]
    assert shown == ["20260001", "20260002"]


def test_roster_sync_updates_a_class_but_never_blanks_one(campus_session: Session):
    """A roster without 班级 must not wipe a class set elsewhere."""
    service = StudentAdministrationService(session=campus_session, default_allowance_usd=Decimal(20))
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="王一", cohort="一班")],
        actor_account_id="admin-1",
    )

    # An import that omits the column leaves the class alone.
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="王一", cohort=None)],
        actor_account_id="admin-1",
    )
    assert service.get_student("20260001").cohort == "一班"

    # A blank cell is the same as no value.
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="王一", cohort="   ")],
        actor_account_id="admin-1",
    )
    assert service.get_student("20260001").cohort == "一班"

    # A real value still updates it.
    service.sync_students(
        [StudentIdentity(student_number="20260001", display_name="王一", cohort="二班")],
        actor_account_id="admin-1",
    )
    assert service.get_student("20260001").cohort == "二班"
