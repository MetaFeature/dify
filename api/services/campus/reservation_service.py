import json
from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.campus import (
    ACTIVE_RESERVATION_STATUSES,
    CampusAccessSlot,
    CampusAuditEvent,
    CampusReservation,
    CampusStudent,
    ReservationStatus,
    StudentStatus,
)
from services.campus.domain import (
    AccessDecision,
    CurrentSlotLoadAdmission,
    ReservationResult,
    SlotAvailability,
    SlotCapacityChange,
)
from services.campus.errors import (
    CampusValidationError,
    CurrentSlotLoadUnavailableError,
    DuplicateSlotClaimError,
    PendingReservationExistsError,
    ReservationCancellationError,
    ReservationNotFoundError,
    ReservationWindowError,
    StudentNotFoundError,
    StudentSuspendedError,
)
from services.campus.time_utils import to_aware_utc, to_naive_utc

CAMPUS_TIMEZONE = timezone(timedelta(hours=8))
SLOT_DURATION = timedelta(hours=2)


class ClosedCurrentSlotLoadAdmission:
    """Fail closed when no runtime load-admission source is configured."""

    def allows_current_slot_reservation(self) -> bool:
        return False


class ReservationService:
    """Owns fixed-slot booking, current-slot admission, FIFO priority, and time-bound access."""

    _session: Session
    _capacity: int
    _booking_days: int
    _current_slot_load_admission: CurrentSlotLoadAdmission

    def __init__(
        self,
        *,
        session: Session,
        capacity: int,
        booking_days: int,
        current_slot_load_admission: CurrentSlotLoadAdmission | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if booking_days < 1:
            raise ValueError("booking_days must be positive")
        self._session = session
        self._capacity = capacity
        self._booking_days = booking_days
        self._current_slot_load_admission = current_slot_load_admission or ClosedCurrentSlotLoadAdmission()

    def reserve(self, student_id: str, starts_at: datetime, *, now: datetime) -> ReservationResult:
        """Create and commit one fixed-slot claim under student and slot locks.

        Args:
            student_id: Active Campus student requesting the claim.
            starts_at: Time-zone-aware start of a fixed two-hour slot.
            now: Time-zone-aware command time used for booking and load-admission boundaries.

        Returns:
            The confirmed reservation or FIFO waitlist entry created for the student.

        Raises:
            StudentNotFoundError: The Campus student does not exist.
            StudentSuspendedError: The Campus student is not active.
            DuplicateSlotClaimError: The student already holds an unfinished claim on this slot.
            PendingReservationExistsError: The student already holds a claim without access and
                this request would add a second one.
            ReservationWindowError: The requested slot is not a current or future bookable slot.
            CurrentSlotLoadUnavailableError: Current capacity exists but load admission fails closed.

        Side effects:
            Locks the student and slot, materializes the student's elapsed claims, creates the slot when absent,
            promotes eligible FIFO waiters without bypassing load admission, inserts the new claim, and commits.
        """
        starts_at_utc = self._validate_starts_at(starts_at, now=now)
        now_utc = to_naive_utc(now)
        student = self._session.scalar(select(CampusStudent).where(CampusStudent.id == student_id).with_for_update())
        if student is None:
            raise StudentNotFoundError(student_id)
        if student.status is not StudentStatus.ACTIVE:
            raise StudentSuspendedError(student.student_number)

        self._finish_elapsed_claims(student_id, now_utc)
        active_claims = self._session.execute(
            select(CampusReservation, CampusAccessSlot)
            .join(CampusAccessSlot, CampusAccessSlot.id == CampusReservation.slot_id)
            .where(
                CampusReservation.student_id == student_id,
                CampusReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
        ).all()
        if any(claim_slot.starts_at == starts_at_utc for _claim, claim_slot in active_claims):
            raise DuplicateSlotClaimError(student.student_number)
        # A claim is pending until it grants access: every waitlist entry, and every
        # confirmed claim whose slot has not started. A confirmed claim inside its
        # running slot is the student's effective reservation and occupies the other bound.
        has_pending_claim = any(
            claim.status is ReservationStatus.WAITLISTED or claim_slot.starts_at > now_utc
            for claim, claim_slot in active_claims
        )

        slot = self._locked_slot(starts_at_utc)
        if slot.capacity == 0:
            raise ReservationWindowError("slot is closed")

        confirmed_count = (
            self._session.scalar(
                select(func.count(CampusReservation.id)).where(
                    CampusReservation.slot_id == slot.id,
                    CampusReservation.status == ReservationStatus.CONFIRMED,
                )
            )
            or 0
        )
        is_current_slot = starts_at_utc <= now_utc < slot.ends_at
        if has_pending_claim and not self._grants_immediate_access(
            slot, is_current_slot=is_current_slot, confirmed_count=confirmed_count
        ):
            raise PendingReservationExistsError(student.student_number)
        if is_current_slot and confirmed_count < slot.capacity:
            if not self._current_slot_load_admission.allows_current_slot_reservation():
                raise CurrentSlotLoadUnavailableError("current slot admission is unavailable")
            promoted_count = self._promote_waiters(slot, available=1, confirmed_at=now_utc)
            status = ReservationStatus.WAITLISTED if promoted_count else ReservationStatus.CONFIRMED
        else:
            if not is_current_slot:
                confirmed_count += self._promote_waiters(
                    slot,
                    available=slot.capacity - confirmed_count,
                    confirmed_at=now_utc,
                )
            status = ReservationStatus.CONFIRMED if confirmed_count < slot.capacity else ReservationStatus.WAITLISTED
        last_queue_sequence = self._session.scalar(
            select(func.max(CampusReservation.queue_sequence)).where(CampusReservation.slot_id == slot.id)
        )
        reservation = CampusReservation(
            student_id=student_id,
            slot_id=slot.id,
            status=status,
            queued_at=now_utc,
            queue_sequence=(last_queue_sequence or 0) + 1,
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
        """Cancel under the slot lock, promote the first queued waiter, and commit atomically.

        An in-progress confirmed reservation may be cancelled; the student immediately
        loses access for the remainder of the slot. Claims on ended slots are not
        cancellable — they materialize as completed or expired instead.
        """
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
        if slot.ends_at <= now_utc:
            raise ReservationCancellationError("reservation cannot be cancelled after the slot ends")

        was_confirmed = reservation.status is ReservationStatus.CONFIRMED
        reservation.status = ReservationStatus.CANCELLED
        reservation.cancelled_at = now_utc
        if was_confirmed:
            self._promote_waiters(slot, available=1, confirmed_at=now_utc)
        self._session.commit()

    def access_decision(self, student_id: str, *, now: datetime) -> AccessDecision:
        """Materialize elapsed claims and return access only inside a confirmed slot.

        The decision carries the server instant so the portal can anchor its
        countdowns to server time instead of the browser clock.
        """
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
        server_now = to_aware_utc(now_utc)
        if row is None:
            return AccessDecision(allowed=False, server_now=server_now)
        reservation, slot = row
        return AccessDecision(
            allowed=True,
            reservation_id=reservation.id,
            ends_at=to_aware_utc(slot.ends_at),
            server_now=server_now,
        )

    def list_slots(self, day: date, *, now: datetime) -> tuple[SlotAvailability, ...]:
        """Expire elapsed waiters before returning the twelve-slot UTC+8 availability view."""
        self._expire_elapsed_waiters(to_naive_utc(now))
        self._session.commit()
        now_local = now.astimezone(CAMPUS_TIMEZONE)
        last_bookable_date = now_local.date() + timedelta(days=self._booking_days - 1)
        if not now_local.date() <= day <= last_bookable_date:
            raise ReservationWindowError("day is outside the booking window")
        return self._day_availability(day, now=now)

    def admin_list_slots(self, day: date, *, now: datetime) -> tuple[SlotAvailability, ...]:
        """Return the availability view for any day, unconstrained by the student booking window."""
        self._expire_elapsed_waiters(to_naive_utc(now))
        self._session.commit()
        return self._day_availability(day, now=now)

    def set_slot_capacity(
        self, starts_at: datetime, capacity: int, *, actor_account_id: str, now: datetime
    ) -> SlotCapacityChange:
        """Set one slot's capacity under the slot lock, audit the change, and commit.

        Capacity zero closes the slot to new claims. Lowering capacity never
        revokes confirmed reservations; the slot simply stops confirming new
        claims until attrition brings it under the boundary. Raising capacity
        on a slot that has not started promotes FIFO waiters into the new
        room; a running slot keeps promoting one waiter per admitted action
        so load admission is never bypassed.
        """
        if capacity < 0:
            raise CampusValidationError("capacity cannot be negative")
        starts_local = starts_at.astimezone(CAMPUS_TIMEZONE)
        if (
            starts_local.minute != 0
            or starts_local.second != 0
            or starts_local.microsecond != 0
            or starts_local.hour % 2 != 0
        ):
            raise ReservationWindowError("slot must start on a two-hour boundary")
        if starts_at + SLOT_DURATION <= now:
            raise ReservationWindowError("slot has already ended")

        now_utc = to_naive_utc(now)
        starts_at_utc = to_naive_utc(starts_at)
        slot = self._locked_slot(starts_at_utc)
        previous_capacity = slot.capacity
        slot.capacity = capacity
        confirmed_count = self._slot_status_count(slot, ReservationStatus.CONFIRMED)
        if starts_at_utc > now_utc and capacity > confirmed_count:
            confirmed_count += self._promote_waiters(
                slot,
                available=capacity - confirmed_count,
                confirmed_at=now_utc,
            )
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="slot.capacity_changed",
                target_type="access_slot",
                target_id=slot.id,
                details_json=json.dumps(
                    {
                        "starts_at": to_aware_utc(slot.starts_at).isoformat(),
                        "previous_capacity": previous_capacity,
                        "capacity": capacity,
                    }
                ),
            )
        )
        self._session.commit()
        return SlotCapacityChange(
            starts_at=to_aware_utc(slot.starts_at),
            ends_at=to_aware_utc(slot.ends_at),
            capacity=capacity,
            previous_capacity=previous_capacity,
            confirmed=confirmed_count,
            waitlisted=self._slot_status_count(slot, ReservationStatus.WAITLISTED),
        )

    def _day_availability(self, day: date, *, now: datetime) -> tuple[SlotAvailability, ...]:
        local_midnight = datetime.combine(day, time.min, tzinfo=CAMPUS_TIMEZONE)
        starts = [local_midnight + timedelta(hours=hour) for hour in range(0, 24, 2)]
        starts_utc = [to_naive_utc(value) for value in starts]
        counts: dict[datetime, dict[ReservationStatus, int]] = {}
        capacities: dict[datetime, int] = {}
        rows = self._session.execute(
            select(
                CampusAccessSlot.starts_at,
                CampusAccessSlot.capacity,
                CampusReservation.status,
                func.count(CampusReservation.id),
            )
            .outerjoin(CampusReservation, CampusReservation.slot_id == CampusAccessSlot.id)
            .where(CampusAccessSlot.starts_at.in_(starts_utc))
            .group_by(CampusAccessSlot.starts_at, CampusAccessSlot.capacity, CampusReservation.status)
        ).all()
        for starts_at, capacity, status, count in rows:
            capacities[starts_at] = capacity
            if status is not None:
                counts.setdefault(starts_at, {})[status] = int(count)

        return tuple(
            SlotAvailability(
                starts_at=start.astimezone(UTC),
                ends_at=(start + SLOT_DURATION).astimezone(UTC),
                capacity=capacities.get(starts_utc[index], self._capacity),
                confirmed=counts.get(starts_utc[index], {}).get(ReservationStatus.CONFIRMED, 0),
                waitlisted=counts.get(starts_utc[index], {}).get(ReservationStatus.WAITLISTED, 0),
                reservable=(
                    start + SLOT_DURATION > now.astimezone(CAMPUS_TIMEZONE)
                    and capacities.get(starts_utc[index], self._capacity) > 0
                ),
            )
            for index, start in enumerate(starts)
        )

    def _locked_slot(self, starts_at_utc: datetime) -> CampusAccessSlot:
        """Fetch the slot row under lock, materializing it at the default capacity when absent."""
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
        return slot

    def _slot_status_count(self, slot: CampusAccessSlot, status: ReservationStatus) -> int:
        return (
            self._session.scalar(
                select(func.count(CampusReservation.id)).where(
                    CampusReservation.slot_id == slot.id,
                    CampusReservation.status == status,
                )
            )
            or 0
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

    def _grants_immediate_access(
        self, slot: CampusAccessSlot, *, is_current_slot: bool, confirmed_count: int
    ) -> bool:
        """Return whether a new claim on this slot would confirm with immediate access.

        Only a current-slot claim can confirm immediately, and only when confirmed
        capacity remains and no FIFO waiter is queued ahead of the newcomer.
        """
        if not is_current_slot or confirmed_count >= slot.capacity:
            return False
        waiting_count = (
            self._session.scalar(
                select(func.count(CampusReservation.id)).where(
                    CampusReservation.slot_id == slot.id,
                    CampusReservation.status == ReservationStatus.WAITLISTED,
                )
            )
            or 0
        )
        return waiting_count == 0

    def _validate_starts_at(self, starts_at: datetime, *, now: datetime) -> datetime:
        starts_local = starts_at.astimezone(CAMPUS_TIMEZONE)
        now_local = now.astimezone(CAMPUS_TIMEZONE)
        if starts_local.minute != 0 or starts_local.second != 0 or starts_local.microsecond != 0:
            raise ReservationWindowError("slot must start on a two-hour boundary")
        if starts_local.hour % 2 != 0:
            raise ReservationWindowError("slot must start on a two-hour boundary")
        if starts_at + SLOT_DURATION <= now:
            raise ReservationWindowError("slot has already ended")
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
                CampusAccessSlot.ends_at <= now_utc,
            )
        ).all()
        for reservation, _slot in rows:
            reservation.status = (
                ReservationStatus.COMPLETED
                if reservation.status is ReservationStatus.CONFIRMED
                else ReservationStatus.EXPIRED
            )

    def _expire_elapsed_waiters(self, now_utc: datetime) -> None:
        elapsed_slot_ids = select(CampusAccessSlot.id).where(CampusAccessSlot.ends_at <= now_utc)
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
        return preceding or 0

    def _promote_waiters(
        self,
        slot: CampusAccessSlot,
        *,
        available: int,
        confirmed_at: datetime,
    ) -> int:
        """Lock and confirm up to ``available`` FIFO waiters, returning the promoted count."""

        if available <= 0:
            return 0
        waiters = self._session.scalars(
            select(CampusReservation)
            .where(
                CampusReservation.slot_id == slot.id,
                CampusReservation.status == ReservationStatus.WAITLISTED,
            )
            .order_by(CampusReservation.queue_sequence)
            .with_for_update()
            .limit(available)
        ).all()
        for waiter in waiters:
            waiter.status = ReservationStatus.CONFIRMED
            waiter.confirmed_at = confirmed_at
        return len(waiters)
