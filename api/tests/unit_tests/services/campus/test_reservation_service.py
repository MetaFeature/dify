from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from models.campus import CampusAccessSlot, CampusReservation, CampusStudent, ReservationStatus, StudentStatus
from services.campus.errors import ActiveReservationExistsError, ReservationWindowError
from services.campus.reservation_service import ReservationService


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusAccessSlot.__table__,
        CampusReservation.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
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


def test_capacity_overflow_enters_fifo_waitlist(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)

    first = service.reserve(_student_id(campus_session, "20260001"), starts_at, now=now)
    second = service.reserve(_student_id(campus_session, "20260002"), starts_at, now=now)

    assert first.status is ReservationStatus.CONFIRMED
    assert second.status is ReservationStatus.WAITLISTED
    assert second.waitlist_position == 1


def test_pre_start_cancellation_promotes_first_waiter(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    first_student = _student_id(campus_session, "20260001")
    second_student = _student_id(campus_session, "20260002")
    confirmed = service.reserve(first_student, starts_at, now=now)
    waiting = service.reserve(second_student, starts_at, now=now)

    service.cancel(first_student, confirmed.id, now=now)

    promoted = campus_session.get(CampusReservation, waiting.id)
    assert promoted is not None
    assert promoted.status is ReservationStatus.CONFIRMED


def test_fifo_promotion_uses_monotonic_sequence_when_waiters_share_a_timestamp(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    first_student = _student_id(campus_session, "20260001")
    confirmed = service.reserve(first_student, starts_at, now=now)
    first_waiter = service.reserve(_student_id(campus_session, "20260002"), starts_at, now=now)
    second_waiter = service.reserve(_student_id(campus_session, "20260003"), starts_at, now=now)

    service.cancel(first_student, confirmed.id, now=now)

    first_stored = campus_session.get(CampusReservation, first_waiter.id)
    second_stored = campus_session.get(CampusReservation, second_waiter.id)
    assert first_stored is not None
    assert second_stored is not None
    assert first_stored.status is ReservationStatus.CONFIRMED
    assert second_stored.status is ReservationStatus.WAITLISTED
    assert second_waiter.waitlist_position == 2


def test_student_cannot_hold_two_unfinished_claims(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    student_id = _student_id(campus_session, "20260001")
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    service.reserve(student_id, datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)

    with pytest.raises(ActiveReservationExistsError):
        service.reserve(student_id, datetime(2026, 8, 11, 4, 0, tzinfo=UTC), now=now)


def test_student_can_book_later_slot_after_previous_slot_ends(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    student_id = _student_id(campus_session, "20260001")
    service.reserve(
        student_id,
        datetime(2026, 8, 11, 2, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    later = service.reserve(
        student_id,
        datetime(2026, 8, 11, 6, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 4, 1, tzinfo=UTC),
    )

    assert later.status is ReservationStatus.CONFIRMED


def test_waitlist_expires_at_slot_start_so_student_can_book_a_later_slot(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    first_student = _student_id(campus_session, "20260001")
    waitlisted_student = _student_id(campus_session, "20260002")
    service.reserve(
        first_student,
        datetime(2026, 8, 11, 2, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    waiting = service.reserve(
        waitlisted_student,
        datetime(2026, 8, 11, 2, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    later = service.reserve(
        waitlisted_student,
        datetime(2026, 8, 11, 6, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 2, 1, tzinfo=UTC),
    )

    expired = campus_session.get(CampusReservation, waiting.id)
    assert expired is not None
    assert expired.status is ReservationStatus.EXPIRED
    assert later.status is ReservationStatus.CONFIRMED


def test_listing_materializes_waitlist_expiry_at_slot_start(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    first_student = _student_id(campus_session, "20260001")
    waitlisted_student = _student_id(campus_session, "20260002")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(first_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))
    waiting = service.reserve(waitlisted_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))

    reservations = service.list_student_reservations(waitlisted_student, now=starts_at)

    assert reservations[0].status is ReservationStatus.EXPIRED
    stored = campus_session.get(CampusReservation, waiting.id)
    assert stored is not None
    assert stored.status is ReservationStatus.EXPIRED


def test_slot_listing_expires_elapsed_waiters_before_counting(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(
        _student_id(campus_session, "20260001"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    waiting = service.reserve(
        _student_id(campus_session, "20260002"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    slots = service.list_slots(datetime(2026, 8, 11).date(), now=starts_at)

    matching = next(slot for slot in slots if slot.starts_at == starts_at)
    stored = campus_session.get(CampusReservation, waiting.id)
    assert matching.waitlisted == 0
    assert stored is not None
    assert stored.status is ReservationStatus.EXPIRED


def test_access_is_only_granted_inside_confirmed_slot(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    student_id = _student_id(campus_session, "20260001")
    service.reserve(
        student_id,
        datetime(2026, 8, 11, 2, 0, tzinfo=UTC),
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    before = service.access_decision(student_id, now=datetime(2026, 8, 11, 1, 59, tzinfo=UTC))
    during = service.access_decision(student_id, now=datetime(2026, 8, 11, 2, 0, tzinfo=UTC))
    after = service.access_decision(student_id, now=datetime(2026, 8, 11, 4, 0, tzinfo=UTC))

    assert before.allowed is False
    assert during.allowed is True
    assert after.allowed is False


@pytest.mark.parametrize(
    "starts_at",
    [
        datetime(2026, 8, 11, 3, 0, tzinfo=UTC),
        datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
        datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
    ],
)
def test_only_fixed_two_hour_slots_in_rolling_seven_day_window_are_bookable(
    campus_session: Session, starts_at: datetime
):
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)

    with pytest.raises(ReservationWindowError):
        service.reserve(
            _student_id(campus_session, "20260001"),
            starts_at,
            now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
        )
