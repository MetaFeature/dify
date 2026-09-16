from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from models.campus import (
    CampusAccessSlot,
    CampusAuditEvent,
    CampusReservation,
    CampusSlotCapacitySetting,
    CampusStudent,
    ReservationStatus,
    StudentStatus,
)
from services.campus.errors import CampusValidationError
from services.campus.reservation_service import ReservationService
from services.campus.slot_capacity_service import SETTING_KEY, SlotCapacityService

NOW = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
# 02:00 UTC is 10:00 in the campus (UTC+8) timezone, so it sits on a two-hour
# boundary and still lies ahead of NOW.
UPCOMING_SLOT = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
PLATFORM_DEFAULT = 500
SLOT_DURATION = timedelta(hours=2)


@pytest.fixture
def capacity_session(sqlite_engine) -> Session:
    tables = [
        CampusSlotCapacitySetting.__table__,
        CampusStudent.__table__,
        CampusAccessSlot.__table__,
        CampusReservation.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusAccessSlot.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                CampusStudent(student_number="20260001", display_name="Student One", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260002", display_name="Student Two", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260003", display_name="Student Three", status=StudentStatus.ACTIVE),
            ]
        )
        session.commit()
        yield session


def _student_id(session: Session, student_number: str) -> str:
    return next(
        student.id for student in session.query(CampusStudent).all() if student.student_number == student_number
    )


def _capacity_service(session: Session) -> SlotCapacityService:
    return SlotCapacityService(session=session, platform_default=PLATFORM_DEFAULT, booking_days=7)


def _reservations(session: Session, *, capacity: int) -> ReservationService:
    return ReservationService(session=session, capacity=capacity, booking_days=7)


def _add_slot(session: Session, starts_at: datetime, capacity: int) -> CampusAccessSlot:
    slot = CampusAccessSlot(starts_at=starts_at, ends_at=starts_at + SLOT_DURATION, capacity=capacity)
    session.add(slot)
    session.commit()
    return slot


def test_effective_capacity_falls_back_to_the_platform_default(capacity_session: Session) -> None:
    state = _capacity_service(capacity_session).state()

    assert state.capacity == PLATFORM_DEFAULT
    assert state.platform_default == PLATFORM_DEFAULT
    assert state.configured_capacity is None
    assert state.is_default is True


def test_set_default_capacity_persists_and_reports_the_new_effective_value(capacity_session: Session) -> None:
    change = _capacity_service(capacity_session).set_default_capacity(
        120, actor_account_id="admin-account", now=NOW
    )

    assert change.previous_configured_capacity is None
    assert change.state.capacity == 120
    assert change.state.configured_capacity == 120
    assert change.state.is_default is False
    assert _capacity_service(capacity_session).effective_capacity() == 120
    stored = capacity_session.query(CampusSlotCapacitySetting).filter_by(setting_key=SETTING_KEY).one()
    assert stored.capacity == 120
    assert stored.updated_by_account_id == "admin-account"


def test_set_default_capacity_only_rewrites_slots_that_have_not_started(capacity_session: Session) -> None:
    ended = _add_slot(capacity_session, NOW - timedelta(hours=4), 10)
    running = _add_slot(capacity_session, NOW - timedelta(hours=1), 10)
    upcoming = _add_slot(capacity_session, NOW + timedelta(hours=2), 10)

    application = _capacity_service(capacity_session).set_default_capacity(
        7, actor_account_id="admin-account", now=NOW
    ).application

    assert application.scanned == 1
    assert application.changed == 1
    assert capacity_session.get(CampusAccessSlot, ended.id).capacity == 10
    assert capacity_session.get(CampusAccessSlot, running.id).capacity == 10
    assert capacity_session.get(CampusAccessSlot, upcoming.id).capacity == 7


def test_set_default_capacity_does_not_materialize_missing_slots(capacity_session: Session) -> None:
    application = _capacity_service(capacity_session).set_default_capacity(
        9, actor_account_id="admin-account", now=NOW
    ).application

    assert application.scanned == 0
    assert application.changed == 0
    assert capacity_session.query(CampusAccessSlot).count() == 0


def test_lowering_default_capacity_keeps_confirmed_reservations(capacity_session: Session) -> None:
    reservations = _reservations(capacity_session, capacity=2)
    reservations.reserve(_student_id(capacity_session, "20260001"), UPCOMING_SLOT, now=NOW)
    reservations.reserve(_student_id(capacity_session, "20260002"), UPCOMING_SLOT, now=NOW)

    _capacity_service(capacity_session).set_default_capacity(1, actor_account_id="admin-account", now=NOW)

    stored = capacity_session.query(CampusAccessSlot).one()
    assert stored.capacity == 1
    statuses = [reservation.status for reservation in capacity_session.query(CampusReservation).all()]
    assert statuses == [ReservationStatus.CONFIRMED, ReservationStatus.CONFIRMED]


def test_raising_default_capacity_promotes_waiters_in_fifo_order(capacity_session: Session) -> None:
    reservations = _reservations(capacity_session, capacity=1)
    reservations.reserve(_student_id(capacity_session, "20260001"), UPCOMING_SLOT, now=NOW)
    first_waiter = reservations.reserve(_student_id(capacity_session, "20260002"), UPCOMING_SLOT, now=NOW)
    second_waiter = reservations.reserve(_student_id(capacity_session, "20260003"), UPCOMING_SLOT, now=NOW)

    application = _capacity_service(capacity_session).set_default_capacity(
        2, actor_account_id="admin-account", now=NOW
    ).application

    assert application.promoted == 1
    promoted = capacity_session.get(CampusReservation, first_waiter.id)
    still_waiting = capacity_session.get(CampusReservation, second_waiter.id)
    assert promoted is not None
    assert promoted.status is ReservationStatus.CONFIRMED
    assert still_waiting is not None
    assert still_waiting.status is ReservationStatus.WAITLISTED


def test_restore_default_drops_the_setting_and_reapplies_the_platform_default(capacity_session: Session) -> None:
    upcoming = _add_slot(capacity_session, UPCOMING_SLOT, 10)
    service = _capacity_service(capacity_session)
    service.set_default_capacity(3, actor_account_id="admin-account", now=NOW)
    assert capacity_session.get(CampusAccessSlot, upcoming.id).capacity == 3

    change = service.restore_default(actor_account_id="admin-account", now=NOW)

    assert change.previous_configured_capacity == 3
    assert change.state.is_default is True
    assert change.state.capacity == PLATFORM_DEFAULT
    assert capacity_session.query(CampusSlotCapacitySetting).count() == 0
    assert capacity_session.get(CampusAccessSlot, upcoming.id).capacity == PLATFORM_DEFAULT
    assert service.effective_capacity() == PLATFORM_DEFAULT


def test_set_and_restore_append_one_audit_event_each(capacity_session: Session) -> None:
    service = _capacity_service(capacity_session)

    service.set_default_capacity(42, actor_account_id="admin-account", now=NOW)
    service.restore_default(actor_account_id="admin-account", now=NOW)

    audits = capacity_session.query(CampusAuditEvent).order_by(CampusAuditEvent.created_at).all()
    assert [audit.action for audit in audits] == [
        "slot_capacity.default_changed",
        "slot_capacity.default_restored",
    ]
    assert {audit.actor_account_id for audit in audits} == {"admin-account"}
    assert {audit.target_type for audit in audits} == {"slot_capacity_setting"}
    assert {audit.target_id for audit in audits} == {SETTING_KEY}


@pytest.mark.parametrize("capacity", [0, -1])
def test_set_default_capacity_rejects_non_positive_values(capacity_session: Session, capacity: int) -> None:
    with pytest.raises(CampusValidationError):
        _capacity_service(capacity_session).set_default_capacity(
            capacity, actor_account_id="admin-account", now=NOW
        )


def test_effective_capacity_ignores_a_non_positive_stored_row(capacity_session: Session) -> None:
    capacity_session.add(
        CampusSlotCapacitySetting(
            setting_key=SETTING_KEY,
            capacity=0,
            updated_by_account_id="admin-account",
        )
    )
    capacity_session.commit()

    state = _capacity_service(capacity_session).state()

    assert state.configured_capacity is None
    assert state.is_default is True
    assert state.capacity == PLATFORM_DEFAULT
