from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.campus import (
    ACTIVE_RESERVATION_STATUSES,
    CampusAccessSlot,
    CampusReservation,
    CampusStudent,
    ReservationStatus,
    StudentStatus,
)
from services.campus.domain import AccessDecision, ReservationResult, SlotAvailability
from services.campus.errors import (
    ActiveReservationExistsError,
    ReservationCancellationError,
    ReservationNotFoundError,
    ReservationWindowError,
    StudentNotFoundError,
    StudentSuspendedError,
)
from services.campus.time_utils import to_aware_utc, to_naive_utc

CAMPUS_TIMEZONE = timezone(timedelta(hours=8))
SLOT_DURATION = timedelta(hours=2)


class ReservationService:
    """Owns fixed-slot booking, wait-list promotion, and time-bound access."""

    _session: Session
    _capacity: int
    _booking_days: int

    def __init__(self, *, session: Session, capacity: int, booking_days: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if booking_days < 1:
            raise ValueError("booking_days must be positive")
        self._session = session
        self._capacity = capacity
        self._booking_days = booking_days

    def reserve(self, student_id: str, starts_at: datetime, *, now: datetime) -> ReservationResult:
        """Lock student and slot, allocate a monotonic queue position, then commit one claim."""
        starts_at_utc = self._validate_starts_at(starts_at, now=now)
        now_utc = to_naive_utc(now)
        student = self._session.scalar(select(CampusStudent).where(CampusStudent.id == student_id).with_for_update())
        if student is None:
            raise StudentNotFoundError(student_id)
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)

        self._finish_elapsed_claims(student_id, now_utc)
        active_claim = self._session.scalar(
            select(CampusReservation)
            .where(
                CampusReservation.student_id == student_id,
                CampusReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
            .limit(1)
        )
        if active_claim is not None:
            raise ActiveReservationExistsError(student.student_number)

        slot = self._session.scalar(
            select(CampusAccessSlot).where(CampusAccessSlot.starts_at == starts_at_utc).with_for_update()
        )
        if slot is None:
            slot = CampusAccessSlot(
                starts_at=starts_at_utc,
                ends_at=starts_at_utc + SLOT_DURATION,
                capacity=self._capacity,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(slot)
                    self._session.flush()
            except IntegrityError:
                slot = self._session.scalar(
                    select(CampusAccessSlot).where(CampusAccessSlot.starts_at == starts_at_utc).with_for_update()
                )
                if slot is None:
                    raise

        confirmed_count = self._session.scalar(
            select(func.count(CampusReservation.id)).where(
                CampusReservation.slot_id == slot.id,
                CampusReservation.status == ReservationStatus.CONFIRMED,
            )
        )
        status = (
            ReservationStatus.CONFIRMED if int(confirmed_count or 0) < slot.capacity else ReservationStatus.WAITLISTED
        )
        last_queue_sequence = self._session.scalar(
            select(func.max(CampusReservation.queue_sequence)).where(CampusReservation.slot_id == slot.id)
        )
        reservation = CampusReservation(
            student_id=student_id,
            slot_id=slot.id,
            status=status,
            queued_at=now_utc,
            queue_sequence=int(last_queue_sequence or 0) + 1,
            confirmed_at=now_utc if status is ReservationStatus.CONFIRMED else None,
            cancelled_at=None,
        )
        self._session.add(reservation)
        self._session.commit()

        waitlist_position = None
        if status is ReservationStatus.WAITLISTED:
            waitlist_position = self._waitlist_position(reservation)
        return ReservationResult(
            id=reservation.id,
            status=status,
            starts_at=to_aware_utc(slot.starts_at),
            ends_at=to_aware_utc(slot.ends_at),
            waitlist_position=waitlist_position,
        )

    def cancel(self, student_id: str, reservation_id: str, *, now: datetime) -> None:
        """Cancel under the slot lock, promote the first queued waiter, and commit atomically."""
        now_utc = to_naive_utc(now)
        reservation = self._session.scalar(
            select(CampusReservation)
            .where(CampusReservation.id == reservation_id, CampusReservation.student_id == student_id)
            .with_for_update()
        )
        if reservation is None or reservation.status not in ACTIVE_RESERVATION_STATUSES:
            raise ReservationNotFoundError(reservation_id)

        slot = self._session.scalar(
            select(CampusAccessSlot).where(CampusAccessSlot.id == reservation.slot_id).with_for_update()
        )
        if slot is None:
            raise ReservationNotFoundError(reservation_id)
        if slot.starts_at <= now_utc:
            raise ReservationCancellationError("reservation cannot be cancelled after the slot starts")

        was_confirmed = reservation.status is ReservationStatus.CONFIRMED
        reservation.status = ReservationStatus.CANCELLED
        reservation.cancelled_at = now_utc
        if was_confirmed:
            waiter = self._session.scalar(
                select(CampusReservation)
                .where(
                    CampusReservation.slot_id == slot.id,
                    CampusReservation.status == ReservationStatus.WAITLISTED,
                )
                .order_by(CampusReservation.queue_sequence)
                .with_for_update()
                .limit(1)
            )
            if waiter is not None:
                waiter.status = ReservationStatus.CONFIRMED
                waiter.confirmed_at = now_utc
        self._session.commit()

    def access_decision(self, student_id: str, *, now: datetime) -> AccessDecision:
        """Materialize elapsed claims and return access only inside a confirmed slot."""
        now_utc = to_naive_utc(now)
        self._finish_elapsed_claims(student_id, now_utc)
        row = self._session.execute(
            select(CampusReservation, CampusAccessSlot)
            .join(CampusAccessSlot, CampusAccessSlot.id == CampusReservation.slot_id)
            .where(
                CampusReservation.student_id == student_id,
                CampusReservation.status == ReservationStatus.CONFIRMED,
                CampusAccessSlot.starts_at <= now_utc,
                CampusAccessSlot.ends_at > now_utc,
            )
            .limit(1)
        ).first()
        self._session.commit()
        if row is None:
            return AccessDecision(allowed=False)
        reservation, slot = row
        return AccessDecision(
            allowed=True,
            reservation_id=reservation.id,
            ends_at=to_aware_utc(slot.ends_at),
        )

    def list_slots(self, day: date, *, now: datetime) -> tuple[SlotAvailability, ...]:
        """Expire elapsed waiters before returning the twelve-slot UTC+8 availability view."""
        self._expire_elapsed_waiters(to_naive_utc(now))
        self._session.commit()
        now_local = now.astimezone(CAMPUS_TIMEZONE)
        last_bookable_date = now_local.date() + timedelta(days=self._booking_days - 1)
        if not now_local.date() <= day <= last_bookable_date:
            raise ReservationWindowError("day is outside the booking window")
        local_midnight = datetime.combine(day, time.min, tzinfo=CAMPUS_TIMEZONE)
        starts = [local_midnight + timedelta(hours=hour) for hour in range(0, 24, 2)]
        starts_utc = [to_naive_utc(value) for value in starts]
        counts: dict[datetime, dict[ReservationStatus, int]] = {}
        rows = self._session.execute(
            select(CampusAccessSlot.starts_at, CampusReservation.status, func.count(CampusReservation.id))
            .outerjoin(CampusReservation, CampusReservation.slot_id == CampusAccessSlot.id)
            .where(CampusAccessSlot.starts_at.in_(starts_utc))
            .group_by(CampusAccessSlot.starts_at, CampusReservation.status)
        ).all()
        for starts_at, status, count in rows:
            if status is not None:
                counts.setdefault(starts_at, {})[status] = int(count)

        return tuple(
            SlotAvailability(
                starts_at=start.astimezone(UTC),
                ends_at=(start + SLOT_DURATION).astimezone(UTC),
                capacity=self._capacity,
                confirmed=counts.get(starts_utc[index], {}).get(ReservationStatus.CONFIRMED, 0),
                waitlisted=counts.get(starts_utc[index], {}).get(ReservationStatus.WAITLISTED, 0),
                reservable=start > now.astimezone(CAMPUS_TIMEZONE),
            )
            for index, start in enumerate(starts)
        )

    def list_student_reservations(self, student_id: str, *, now: datetime) -> tuple[ReservationResult, ...]:
        """Return reservations after materializing all elapsed terminal states."""
        self._finish_elapsed_claims(student_id, to_naive_utc(now))
        self._session.commit()
        rows = self._session.execute(
            select(CampusReservation, CampusAccessSlot)
            .join(CampusAccessSlot, CampusAccessSlot.id == CampusReservation.slot_id)
            .where(CampusReservation.student_id == student_id)
            .order_by(CampusAccessSlot.starts_at.desc())
        ).all()
        return tuple(
            ReservationResult(
                id=reservation.id,
                status=reservation.status,
                starts_at=to_aware_utc(slot.starts_at),
                ends_at=to_aware_utc(slot.ends_at),
                waitlist_position=(
                    self._waitlist_position(reservation) if reservation.status is ReservationStatus.WAITLISTED else None
                ),
            )
            for reservation, slot in rows
        )

    def _validate_starts_at(self, starts_at: datetime, *, now: datetime) -> datetime:
        starts_local = starts_at.astimezone(CAMPUS_TIMEZONE)
        now_local = now.astimezone(CAMPUS_TIMEZONE)
        if starts_local.minute != 0 or starts_local.second != 0 or starts_local.microsecond != 0:
            raise ReservationWindowError("slot must start on a two-hour boundary")
        if starts_local.hour % 2 != 0:
            raise ReservationWindowError("slot must start on a two-hour boundary")
        if starts_at <= now:
            raise ReservationWindowError("slot has already started")
        last_bookable_date = now_local.date() + timedelta(days=self._booking_days - 1)
        if not now_local.date() <= starts_local.date() <= last_bookable_date:
            raise ReservationWindowError("slot is outside the booking window")
        return to_naive_utc(starts_at)

    def _finish_elapsed_claims(self, student_id: str, now_utc: datetime) -> None:
        rows = self._session.execute(
            select(CampusReservation, CampusAccessSlot)
            .join(CampusAccessSlot, CampusAccessSlot.id == CampusReservation.slot_id)
            .where(
                CampusReservation.student_id == student_id,
                CampusReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                or_(
                    and_(
                        CampusReservation.status == ReservationStatus.CONFIRMED,
                        CampusAccessSlot.ends_at <= now_utc,
                    ),
                    and_(
                        CampusReservation.status == ReservationStatus.WAITLISTED,
                        CampusAccessSlot.starts_at <= now_utc,
                    ),
                ),
            )
        ).all()
        for reservation, _slot in rows:
            reservation.status = (
                ReservationStatus.COMPLETED
                if reservation.status is ReservationStatus.CONFIRMED
                else ReservationStatus.EXPIRED
            )

    def _expire_elapsed_waiters(self, now_utc: datetime) -> None:
        elapsed_slot_ids = select(CampusAccessSlot.id).where(CampusAccessSlot.starts_at <= now_utc)
        self._session.execute(
            update(CampusReservation)
            .where(
                CampusReservation.status == ReservationStatus.WAITLISTED,
                CampusReservation.slot_id.in_(elapsed_slot_ids),
            )
            .values(status=ReservationStatus.EXPIRED)
        )

    def _waitlist_position(self, reservation: CampusReservation) -> int:
        preceding = self._session.scalar(
            select(func.count(CampusReservation.id)).where(
                CampusReservation.slot_id == reservation.slot_id,
                CampusReservation.status == ReservationStatus.WAITLISTED,
                CampusReservation.queue_sequence <= reservation.queue_sequence,
            )
        )
        return int(preceding or 0)
