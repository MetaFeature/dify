"""Own the administrator-managed platform default capacity for access slots.

The environment value stays the platform default; an administrator may replace
it with a single setting that is applied to every slot that has not started yet.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusSlotCapacitySetting
from services.campus.domain import SlotCapacityApplication
from services.campus.errors import CampusValidationError
from services.campus.reservation_service import ReservationService

SETTING_KEY = "platform-default"


@dataclass(frozen=True)
class SlotCapacitySettingState:
    """The capacity in force plus the provenance an administrator needs."""

    capacity: int
    platform_default: int
    configured_capacity: int | None

    @property
    def is_default(self) -> bool:
        return self.configured_capacity is None


@dataclass(frozen=True)
class SlotCapacitySettingChange:
    previous_configured_capacity: int | None
    state: SlotCapacitySettingState
    application: SlotCapacityApplication


class SlotCapacityService:
    def __init__(self, *, session: Session, platform_default: int, booking_days: int) -> None:
        if platform_default < 1:
            raise ValueError("platform default capacity must be positive")
        self._session = session
        self._platform_default = platform_default
        self._booking_days = booking_days

    def state(self) -> SlotCapacitySettingState:
        configured = self._configured_capacity()
        return SlotCapacitySettingState(
            capacity=configured if configured is not None else self._platform_default,
            platform_default=self._platform_default,
            configured_capacity=configured,
        )

    def effective_capacity(self) -> int:
        return self.state().capacity

    def set_default_capacity(
        self, capacity: int, *, actor_account_id: str, now: datetime
    ) -> SlotCapacitySettingChange:
        """Replace the platform default and rewrite every unstarted slot."""
        if capacity < 1:
            raise CampusValidationError("default capacity must be positive")
        row = self._row(for_update=True)
        previous_configured = row.capacity if row is not None else None
        application = self._apply_to_unstarted_slots(capacity, now=now)
        if row is None:
            self._session.add(
                CampusSlotCapacitySetting(
                    setting_key=SETTING_KEY,
                    capacity=capacity,
                    updated_by_account_id=actor_account_id,
                )
            )
        else:
            row.capacity = capacity
            row.updated_by_account_id = actor_account_id
        self._audit(
            "slot_capacity.default_changed",
            actor_account_id,
            previous_configured_capacity=previous_configured,
            capacity=capacity,
            application=application,
        )
        self._session.commit()
        return SlotCapacitySettingChange(
            previous_configured_capacity=previous_configured,
            state=self.state(),
            application=application,
        )

    def restore_default(self, *, actor_account_id: str, now: datetime) -> SlotCapacitySettingChange:
        """Drop the setting and put the platform default back on unstarted slots."""
        row = self._row(for_update=True)
        previous_configured = row.capacity if row is not None else None
        if row is not None:
            self._session.delete(row)
        application = self._apply_to_unstarted_slots(self._platform_default, now=now)
        self._audit(
            "slot_capacity.default_restored",
            actor_account_id,
            previous_configured_capacity=previous_configured,
            capacity=self._platform_default,
            application=application,
        )
        self._session.commit()
        return SlotCapacitySettingChange(
            previous_configured_capacity=previous_configured,
            state=self.state(),
            application=application,
        )

    def _apply_to_unstarted_slots(self, capacity: int, *, now: datetime) -> SlotCapacityApplication:
        # apply_default_capacity takes the capacity explicitly, so the
        # constructor value only has to satisfy its positive invariant.
        reservations = ReservationService(
            session=self._session,
            capacity=capacity,
            booking_days=self._booking_days,
        )
        return reservations.apply_default_capacity(capacity, now=now)

    def _configured_capacity(self) -> int | None:
        """Return the stored override, treating a non-positive row as unset.

        The write path rejects values below one, so a non-positive row can only
        come from manual database edits; falling back keeps the effective
        capacity usable instead of failing every reservation request.
        """

        row = self._row()
        if row is None or row.capacity < 1:
            return None
        return row.capacity

    def _row(self, *, for_update: bool = False) -> CampusSlotCapacitySetting | None:
        statement = select(CampusSlotCapacitySetting).where(CampusSlotCapacitySetting.setting_key == SETTING_KEY)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def _audit(
        self,
        action: str,
        actor_account_id: str,
        *,
        previous_configured_capacity: int | None,
        capacity: int,
        application: SlotCapacityApplication,
    ) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="slot_capacity_setting",
                target_id=SETTING_KEY,
                details_json=json.dumps(
                    {
                        "previous_configured_capacity": previous_configured_capacity,
                        "capacity": capacity,
                        "scanned_slots": application.scanned,
                        "changed_slots": application.changed,
                        "promoted_waiters": application.promoted,
                    },
                    separators=(",", ":"),
                ),
            )
        )
