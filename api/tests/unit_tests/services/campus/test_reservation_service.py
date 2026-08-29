from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from models.campus import (
    CampusAccessSlot,
    CampusAuditEvent,
    CampusReservation,
    CampusStudent,
    ReservationStatus,
    StudentStatus,
)
from services.campus.errors import (
    CurrentSlotLoadUnavailableError,
    DuplicateSlotClaimError,
    PendingReservationExistsError,
    ReservationCancellationError,
    ReservationNotFoundError,
    ReservationWindowError,
)
from services.campus.reservation_service import ReservationService


class FixedLoadAdmission:
    allowed: bool

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def allows_current_slot_reservation(self) -> bool:
        return self.allowed


class UnexpectedLoadAdmission:
    def allows_current_slot_reservation(self) -> bool:
        raise AssertionError("load admission must not run for a full current slot")


class CountingLoadAdmission:
    calls: int

    def __init__(self) -> None:
        self.calls = 0

    def allows_current_slot_reservation(self) -> bool:
        self.calls += 1
        return True


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusAccessSlot.__table__,
        CampusReservation.__table__,
        CampusAuditEvent.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                CampusStudent(student_number="20260001", display_name="Student One", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260002", display_name="Student Two", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260003", display_name="Student Three", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260004", display_name="Student Four", status=StudentStatus.ACTIVE),
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


def test_current_slot_supplemental_reservation_grants_immediate_access(campus_session: Session) -> None:
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260001")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)

    reservation = service.reserve(student_id, starts_at, now=now)
    access = service.access_decision(student_id, now=now)

    assert reservation.status is ReservationStatus.CONFIRMED
    assert reservation.ends_at == datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
    assert access.allowed is True
    assert access.ends_at == reservation.ends_at


def test_current_slot_load_rejection_creates_no_claim(campus_session: Session) -> None:
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=False),
    )
    student_id = _student_id(campus_session, "20260001")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)

    with pytest.raises(CurrentSlotLoadUnavailableError):
        service.reserve(student_id, starts_at, now=now)

    assert service.list_student_reservations(student_id, now=now) == ()


def test_full_current_slot_creates_fifo_waitlist_entry_without_load_admission(campus_session: Session) -> None:
    service = ReservationService(
        session=campus_session,
        capacity=1,
        booking_days=7,
        current_slot_load_admission=UnexpectedLoadAdmission(),
    )
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(
        _student_id(campus_session, "20260001"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    waiting = service.reserve(
        _student_id(campus_session, "20260002"),
        starts_at,
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )

    assert waiting.status is ReservationStatus.WAITLISTED
    assert waiting.waitlist_position == 1


def test_waitlist_retains_fifo_priority_until_current_slot_ends(campus_session: Session) -> None:
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

    reservations = service.list_student_reservations(
        _student_id(campus_session, "20260002"),
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )

    assert reservations[0].id == waiting.id
    assert reservations[0].status is ReservationStatus.WAITLISTED
    assert reservations[0].waitlist_position == 1


def test_existing_waiter_is_admitted_before_a_new_current_slot_request(campus_session: Session) -> None:
    advance_service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    advance_service.reserve(
        _student_id(campus_session, "20260001"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    waiting = advance_service.reserve(
        _student_id(campus_session, "20260002"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    slot = campus_session.query(CampusAccessSlot).filter_by(starts_at=datetime(2026, 8, 11, 2, 0)).one()
    slot.capacity = 2
    campus_session.commit()
    current_service = ReservationService(
        session=campus_session,
        capacity=2,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )

    newcomer = current_service.reserve(
        _student_id(campus_session, "20260003"),
        starts_at,
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )
    waiting_result = current_service.list_student_reservations(
        _student_id(campus_session, "20260002"),
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )[0]

    assert waiting_result.id == waiting.id
    assert waiting_result.status is ReservationStatus.CONFIRMED
    assert newcomer.status is ReservationStatus.WAITLISTED
    assert newcomer.waitlist_position == 1


def test_one_load_decision_admits_at_most_one_existing_waiter(campus_session: Session) -> None:
    advance_service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    for student_number in ("20260001", "20260002", "20260003"):
        advance_service.reserve(
            _student_id(campus_session, student_number),
            starts_at,
            now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
        )
    slot = campus_session.query(CampusAccessSlot).filter_by(starts_at=datetime(2026, 8, 11, 2, 0)).one()
    slot.capacity = 4
    campus_session.commit()
    load_admission = CountingLoadAdmission()
    current_service = ReservationService(
        session=campus_session,
        capacity=4,
        booking_days=7,
        current_slot_load_admission=load_admission,
    )

    newcomer = current_service.reserve(
        _student_id(campus_session, "20260004"),
        starts_at,
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )
    second_student = current_service.list_student_reservations(
        _student_id(campus_session, "20260002"),
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )[0]
    third_student = current_service.list_student_reservations(
        _student_id(campus_session, "20260003"),
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
    )[0]

    assert load_admission.calls == 1
    assert second_student.status is ReservationStatus.CONFIRMED
    assert third_student.status is ReservationStatus.WAITLISTED
    assert newcomer.status is ReservationStatus.WAITLISTED
    assert newcomer.waitlist_position == 2


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


def test_in_progress_cancellation_revokes_access_and_promotes_first_waiter(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    first_student = _student_id(campus_session, "20260001")
    second_student = _student_id(campus_session, "20260002")
    confirmed = service.reserve(first_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))
    waiting = service.reserve(second_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)

    service.cancel(first_student, confirmed.id, now=now)

    promoted = campus_session.get(CampusReservation, waiting.id)
    assert promoted is not None
    assert promoted.status is ReservationStatus.CONFIRMED
    assert service.access_decision(first_student, now=now).allowed is False


def test_cancellation_after_slot_end_is_rejected(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    student_id = _student_id(campus_session, "20260001")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    confirmed = service.reserve(student_id, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))

    with pytest.raises((ReservationCancellationError, ReservationNotFoundError)):
        service.cancel(student_id, confirmed.id, now=datetime(2026, 8, 11, 4, 0, tzinfo=UTC))


def test_student_can_rebook_the_same_slot_after_in_progress_cancellation(campus_session: Session):
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260001")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
    first = service.reserve(student_id, starts_at, now=now)
    service.cancel(student_id, first.id, now=now)

    rebooked = service.reserve(student_id, starts_at, now=now)

    assert rebooked.status is ReservationStatus.CONFIRMED


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


def test_student_cannot_hold_two_pending_claims(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    student_id = _student_id(campus_session, "20260001")
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    service.reserve(student_id, datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)

    with pytest.raises(PendingReservationExistsError):
        service.reserve(student_id, datetime(2026, 8, 11, 4, 0, tzinfo=UTC), now=now)


def test_effective_reservation_allows_booking_the_next_slot(campus_session: Session):
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260001")
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
    supplemental = service.reserve(student_id, datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)

    upcoming = service.reserve(student_id, datetime(2026, 8, 11, 4, 0, tzinfo=UTC), now=now)

    assert supplemental.status is ReservationStatus.CONFIRMED
    assert upcoming.status is ReservationStatus.CONFIRMED
    assert service.access_decision(student_id, now=now).allowed is True


def test_duplicate_claim_on_the_same_slot_is_rejected(campus_session: Session):
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260001")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
    service.reserve(student_id, starts_at, now=now)

    with pytest.raises(DuplicateSlotClaimError):
        service.reserve(student_id, starts_at, now=now)


def test_current_slot_waitlist_entry_blocks_a_second_pending_claim(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    waitlisted_student = _student_id(campus_session, "20260002")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(
        _student_id(campus_session, "20260001"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    service.reserve(waitlisted_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))

    with pytest.raises(PendingReservationExistsError):
        service.reserve(
            waitlisted_student,
            datetime(2026, 8, 11, 4, 0, tzinfo=UTC),
            now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
        )


def test_pending_future_claim_allows_a_confirming_supplemental(campus_session: Session):
    service = ReservationService(
        session=campus_session,
        capacity=500,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260001")
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
    service.reserve(student_id, datetime(2026, 8, 11, 4, 0, tzinfo=UTC), now=now)

    supplemental = service.reserve(student_id, datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)

    assert supplemental.status is ReservationStatus.CONFIRMED


def test_pending_future_claim_blocks_a_waitlisting_supplemental(campus_session: Session):
    service = ReservationService(
        session=campus_session,
        capacity=1,
        booking_days=7,
        current_slot_load_admission=FixedLoadAdmission(allowed=True),
    )
    student_id = _student_id(campus_session, "20260002")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(
        _student_id(campus_session, "20260001"),
        starts_at,
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)
    service.reserve(student_id, datetime(2026, 8, 11, 4, 0, tzinfo=UTC), now=now)

    with pytest.raises(PendingReservationExistsError):
        service.reserve(student_id, starts_at, now=now)


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


def test_waitlist_expires_at_slot_end_so_student_can_book_a_later_slot(campus_session: Session):
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
        now=datetime(2026, 8, 11, 4, 1, tzinfo=UTC),
    )

    expired = campus_session.get(CampusReservation, waiting.id)
    assert expired is not None
    assert expired.status is ReservationStatus.EXPIRED
    assert later.status is ReservationStatus.CONFIRMED


def test_listing_materializes_waitlist_expiry_at_slot_end(campus_session: Session):
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    first_student = _student_id(campus_session, "20260001")
    waitlisted_student = _student_id(campus_session, "20260002")
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    service.reserve(first_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))
    waiting = service.reserve(waitlisted_student, starts_at, now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))

    reservations = service.list_student_reservations(
        waitlisted_student,
        now=datetime(2026, 8, 11, 4, 0, tzinfo=UTC),
    )

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

    slots = service.list_slots(
        datetime(2026, 8, 11).date(),
        now=datetime(2026, 8, 11, 4, 0, tzinfo=UTC),
    )

    matching = next(slot for slot in slots if slot.starts_at == starts_at)
    stored = campus_session.get(CampusReservation, waiting.id)
    assert matching.waitlisted == 0
    assert stored is not None
    assert stored.status is ReservationStatus.EXPIRED


def test_slot_listing_reports_the_stored_slot_capacity(campus_session: Session) -> None:
    campus_session.add(
        CampusAccessSlot(
            starts_at=datetime(2026, 8, 11, 2, 0),
            ends_at=datetime(2026, 8, 11, 4, 0),
            capacity=3,
        )
    )
    campus_session.commit()
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)

    slots = service.list_slots(datetime(2026, 8, 11).date(), now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC))

    by_start = {slot.starts_at: slot for slot in slots}
    assert by_start[datetime(2026, 8, 11, 2, 0, tzinfo=UTC)].capacity == 3
    assert by_start[datetime(2026, 8, 11, 4, 0, tzinfo=UTC)].capacity == 500


def test_closed_slot_is_not_reservable_and_rejects_claims(campus_session: Session) -> None:
    campus_session.add(
        CampusAccessSlot(
            starts_at=datetime(2026, 8, 11, 2, 0),
            ends_at=datetime(2026, 8, 11, 4, 0),
            capacity=0,
        )
    )
    campus_session.commit()
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)

    slots = service.list_slots(datetime(2026, 8, 11).date(), now=now)

    by_start = {slot.starts_at: slot for slot in slots}
    assert by_start[datetime(2026, 8, 11, 2, 0, tzinfo=UTC)].reservable is False
    with pytest.raises(ReservationWindowError):
        service.reserve(_student_id(campus_session, "20260001"), datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)


def test_set_slot_capacity_materializes_the_slot_and_appends_audit(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)

    change = service.set_slot_capacity(
        starts_at,
        120,
        actor_account_id="admin-account",
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    assert change.capacity == 120
    assert change.previous_capacity == 500
    stored = campus_session.query(CampusAccessSlot).filter_by(starts_at=datetime(2026, 8, 11, 2, 0)).one()
    assert stored.capacity == 120
    audit = campus_session.query(CampusAuditEvent).one()
    assert audit.action == "slot.capacity_changed"
    assert audit.actor_account_id == "admin-account"
    assert audit.target_id == stored.id


def test_set_slot_capacity_rejects_ended_slots(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)

    with pytest.raises(ReservationWindowError):
        service.set_slot_capacity(
            datetime(2026, 8, 11, 2, 0, tzinfo=UTC),
            120,
            actor_account_id="admin-account",
            now=datetime(2026, 8, 11, 4, 0, tzinfo=UTC),
        )


def test_lowering_capacity_keeps_existing_confirmed_reservations(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=2, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    service.reserve(_student_id(campus_session, "20260001"), starts_at, now=now)
    service.reserve(_student_id(campus_session, "20260002"), starts_at, now=now)

    change = service.set_slot_capacity(starts_at, 1, actor_account_id="admin-account", now=now)

    assert change.capacity == 1
    assert change.confirmed == 2
    statuses = [reservation.status for reservation in campus_session.query(CampusReservation).all()]
    assert statuses == [ReservationStatus.CONFIRMED, ReservationStatus.CONFIRMED]


def test_raising_capacity_before_start_promotes_waiters_in_fifo_order(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=1, booking_days=7)
    starts_at = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    now = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)
    service.reserve(_student_id(campus_session, "20260001"), starts_at, now=now)
    first_waiter = service.reserve(_student_id(campus_session, "20260002"), starts_at, now=now)
    second_waiter = service.reserve(_student_id(campus_session, "20260003"), starts_at, now=now)

    change = service.set_slot_capacity(starts_at, 2, actor_account_id="admin-account", now=now)

    promoted = campus_session.get(CampusReservation, first_waiter.id)
    still_waiting = campus_session.get(CampusReservation, second_waiter.id)
    assert promoted is not None
    assert promoted.status is ReservationStatus.CONFIRMED
    assert still_waiting is not None
    assert still_waiting.status is ReservationStatus.WAITLISTED
    assert change.confirmed == 2
    assert change.waitlisted == 1


def test_admin_slot_listing_covers_days_outside_the_student_booking_window(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)

    slots = service.admin_list_slots(
        datetime(2026, 9, 30).date(),
        now=datetime(2026, 8, 11, 0, 30, tzinfo=UTC),
    )

    assert len(slots) == 12


def test_slot_listing_keeps_current_slot_reservable_until_its_fixed_end(campus_session: Session) -> None:
    service = ReservationService(session=campus_session, capacity=500, booking_days=7)
    now = datetime(2026, 8, 11, 2, 30, tzinfo=UTC)

    slots = service.list_slots(datetime(2026, 8, 11).date(), now=now)

    by_start = {slot.starts_at: slot for slot in slots}
    assert by_start[datetime(2026, 8, 11, 0, 0, tzinfo=UTC)].reservable is False
    assert by_start[datetime(2026, 8, 11, 2, 0, tzinfo=UTC)].reservable is True
    assert by_start[datetime(2026, 8, 11, 4, 0, tzinfo=UTC)].reservable is True


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
    assert before.server_now == datetime(2026, 8, 11, 1, 59, tzinfo=UTC)
    assert during.server_now == datetime(2026, 8, 11, 2, 0, tzinfo=UTC)


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
