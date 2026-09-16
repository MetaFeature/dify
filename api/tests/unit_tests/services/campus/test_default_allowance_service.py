from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from configs import dify_config
from models.campus import (
    CampusAllowanceSetting,
    CampusAuditEvent,
    CampusGatewayBinding,
    CampusStudent,
    StudentStatus,
)
from services.campus.default_allowance_service import DefaultAllowanceService
from services.campus.errors import CampusValidationError

PROVISIONED = "20260001"
WAITING = "20260002"
REMOVED = "20260003"


def _service(session: Session, platform_default: Decimal = Decimal(10)) -> DefaultAllowanceService:
    return DefaultAllowanceService(session=session, platform_default_usd=platform_default)


def _student(number: str, *, allowance: Decimal = Decimal(20), deleted: bool = False) -> CampusStudent:
    student = CampusStudent(
        student_number=number,
        display_name=f"Student {number}",
        status=StudentStatus.ACTIVE,
        initial_allowance_usd=allowance,
    )
    if deleted:
        student.deleted_at = datetime(2026, 9, 1)
    return student


@pytest.fixture
def allowance_session(sqlite_engine) -> Session:
    """One student with a model account, one without, and one already deleted."""
    tables = [
        CampusStudent.__table__,
        CampusGatewayBinding.__table__,
        CampusAllowanceSetting.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        provisioned = _student(PROVISIONED)
        waiting = _student(WAITING)
        removed = _student(REMOVED, deleted=True)
        session.add_all([provisioned, waiting, removed])
        session.flush()
        session.add(CampusGatewayBinding(student_id=provisioned.id, gateway_token_id="token-1"))
        session.commit()
        yield session


def _allowances(session: Session) -> dict[str, Decimal]:
    """Read the stored amounts, bypassing the identity map a bulk update skipped."""
    rows = session.execute(select(CampusStudent.student_number, CampusStudent.initial_allowance_usd)).all()
    return dict(rows)


def test_platform_default_follows_the_campus_configuration():
    """Pins the shipped default: a new student receives 10."""
    assert Decimal(10) == dify_config.CAMPUS_DEFAULT_ALLOWANCE_USD


def test_the_platform_default_applies_until_an_administrator_changes_it(allowance_session: Session):
    service = _service(allowance_session)

    state = service.state()

    assert state.default_allowance_usd == Decimal(10)
    assert state.platform_default_usd == Decimal(10)
    assert state.configured_default_allowance_usd is None
    assert state.is_default is True
    assert service.effective_default() == Decimal(10)


def test_setting_the_default_persists_an_audited_override(allowance_session: Session):
    change = _service(allowance_session).set_default(Decimal(15), actor_account_id="admin-1")

    assert change.state.default_allowance_usd == Decimal(15)
    assert change.state.is_default is False
    assert change.previous_configured_default_allowance_usd is None
    assert allowance_session.query(CampusAllowanceSetting).count() == 1
    event = allowance_session.query(CampusAuditEvent).one()
    assert event.action == "allowance_default.changed"
    assert event.target_type == "allowance_setting"
    assert event.target_id == "platform-default"
    assert '"default_allowance_usd":"15.0000"' in event.details_json


def test_a_new_default_reaches_only_students_without_a_model_account(allowance_session: Session):
    change = _service(allowance_session).set_default(Decimal(15), actor_account_id="admin-1")

    # Only the student whose model account does not exist yet is scanned, and a
    # provisioned or deleted student keeps the allowance they already hold.
    assert (change.application.scanned, change.application.changed) == (1, 1)
    assert _allowances(allowance_session) == {
        PROVISIONED: Decimal(20),
        WAITING: Decimal(15),
        REMOVED: Decimal(20),
    }


def test_setting_the_same_amount_again_changes_nothing(allowance_session: Session):
    service = _service(allowance_session)
    service.set_default(Decimal(15), actor_account_id="admin-1")

    change = service.set_default(Decimal(15), actor_account_id="admin-1")

    assert (change.application.scanned, change.application.changed) == (1, 0)
    assert change.previous_configured_default_allowance_usd == Decimal(15)


def test_restoring_the_default_puts_the_platform_value_back(allowance_session: Session):
    service = _service(allowance_session)
    service.set_default(Decimal(15), actor_account_id="admin-1")

    change = service.restore_default(actor_account_id="admin-1")

    assert change.state.default_allowance_usd == Decimal(10)
    assert change.state.is_default is True
    assert change.previous_configured_default_allowance_usd == Decimal(15)
    assert allowance_session.query(CampusAllowanceSetting).count() == 0
    assert _allowances(allowance_session) == {
        PROVISIONED: Decimal(20),
        WAITING: Decimal(10),
        REMOVED: Decimal(20),
    }
    assert allowance_session.query(CampusAuditEvent).all()[-1].action == "allowance_default.restored"


def test_zero_is_a_real_default_rather_than_a_missing_setting(allowance_session: Session):
    service = _service(allowance_session)

    change = service.set_default(Decimal(0), actor_account_id="admin-1")

    assert change.state.default_allowance_usd == Decimal(0)
    assert change.state.is_default is False
    assert _allowances(allowance_session)[WAITING] == Decimal(0)


def test_a_negative_default_is_rejected(allowance_session: Session):
    with pytest.raises(CampusValidationError):
        _service(allowance_session).set_default(Decimal(-1), actor_account_id="admin-1")


def test_a_negative_platform_default_is_a_configuration_error():
    with pytest.raises(ValueError):
        DefaultAllowanceService(session=Session(), platform_default_usd=Decimal(-1))


def test_a_manually_edited_negative_row_falls_back_to_the_platform_default(allowance_session: Session):
    # The write path rejects negatives, so this row can only come from a manual
    # database edit; the effective default stays usable instead of granting a
    # negative allowance.
    allowance_session.add(
        CampusAllowanceSetting(
            setting_key="platform-default",
            default_allowance_usd=Decimal(-5),
            updated_by_account_id="admin-1",
        )
    )
    allowance_session.commit()

    state = _service(allowance_session).state()

    assert state.default_allowance_usd == Decimal(10)
    assert state.is_default is True
