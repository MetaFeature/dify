"""Own the administrator-managed platform default model allowance.

The environment value is the platform default; an administrator may replace it in
the Campus administration portal. The default applies to students who do not have
a model account yet -- including the ones a later roster import creates -- while
a student whose model account already exists keeps the allowance that account was
created with. Lowering the default therefore never takes quota away from a
student who is already using the platform.
"""

import json
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from models.campus import (
    CampusAllowanceSetting,
    CampusAuditEvent,
    CampusGatewayBinding,
    CampusStudent,
)
from services.campus.domain import AllowanceApplication, DefaultAllowanceChange, DefaultAllowanceState
from services.campus.errors import CampusValidationError

SETTING_KEY = "platform-default"

# Numeric(14, 4) is the column's own precision, so a longer fraction would be
# rounded by the database and the value an administrator sees would differ from
# the value that was stored.
ALLOWANCE_PRECISION = Decimal("0.0001")


class DefaultAllowanceService:
    def __init__(self, *, session: Session, platform_default_usd: Decimal) -> None:
        if platform_default_usd < 0:
            raise ValueError("platform default allowance cannot be negative")
        self._session = session
        self._platform_default_usd = platform_default_usd

    def state(self) -> DefaultAllowanceState:
        configured = self._configured_default()
        return DefaultAllowanceState(
            default_allowance_usd=configured if configured is not None else self._platform_default_usd,
            platform_default_usd=self._platform_default_usd,
            configured_default_allowance_usd=configured,
        )

    def effective_default(self) -> Decimal:
        """The allowance a student created right now would receive."""
        return self.state().default_allowance_usd

    def set_default(
        self, default_allowance_usd: Decimal, *, actor_account_id: str
    ) -> DefaultAllowanceChange:
        """Replace the platform default and grant it to students without an account."""
        amount = self._validated(default_allowance_usd)
        row = self._row(for_update=True)
        previous_configured = row.default_allowance_usd if row is not None else None
        if row is None:
            self._session.add(
                CampusAllowanceSetting(
                    setting_key=SETTING_KEY,
                    default_allowance_usd=amount,
                    updated_by_account_id=actor_account_id,
                )
            )
        else:
            row.default_allowance_usd = amount
            row.updated_by_account_id = actor_account_id
        application = self._apply_to_students_without_an_account(amount)
        self._audit(
            "allowance_default.changed",
            actor_account_id,
            previous_configured=previous_configured,
            current=amount,
            application=application,
        )
        self._session.commit()
        return DefaultAllowanceChange(
            previous_configured_default_allowance_usd=previous_configured,
            state=self.state(),
            application=application,
        )

    def restore_default(self, *, actor_account_id: str) -> DefaultAllowanceChange:
        """Drop the setting, putting the platform default back on new accounts."""
        row = self._row(for_update=True)
        previous_configured = row.default_allowance_usd if row is not None else None
        if row is not None:
            self._session.delete(row)
        application = self._apply_to_students_without_an_account(self._platform_default_usd)
        self._audit(
            "allowance_default.restored",
            actor_account_id,
            previous_configured=previous_configured,
            current=self._platform_default_usd,
            application=application,
        )
        self._session.commit()
        return DefaultAllowanceChange(
            previous_configured_default_allowance_usd=previous_configured,
            state=self.state(),
            application=application,
        )

    def _apply_to_students_without_an_account(self, amount: Decimal) -> AllowanceApplication:
        """Write the default onto every student whose model account does not exist.

        A student with a gateway binding already holds a managed token whose
        quota was fixed when the account was created; only the per-student
        allowance adjustment can move that quota, so those rows are left exactly
        as they are. Deleted students are skipped -- their allowance is moot and
        a restore would otherwise resurrect a value nobody can see.
        """
        unprovisioned = select(CampusStudent.id).where(
            CampusStudent.deleted_at.is_(None),
            ~exists().where(CampusGatewayBinding.student_id == CampusStudent.id),
        )
        scanned = int(self._session.scalar(select(func.count()).select_from(unprovisioned.subquery())) or 0)
        result = self._session.execute(
            update(CampusStudent)
            .where(CampusStudent.id.in_(unprovisioned), CampusStudent.initial_allowance_usd != amount)
            .values(initial_allowance_usd=amount),
            execution_options={"synchronize_session": False},
        )
        return AllowanceApplication(scanned=scanned, changed=int(result.rowcount or 0))

    def _configured_default(self) -> Decimal | None:
        """Return the stored override, treating a negative row as unset.

        Zero is a meaningful value -- a platform that grants no model quota at
        all -- so only a negative row, which the write path rejects and only a
        manual database edit can produce, falls back to the platform default.
        """
        row = self._row()
        if row is None or row.default_allowance_usd < 0:
            return None
        return row.default_allowance_usd

    def _row(self, *, for_update: bool = False) -> CampusAllowanceSetting | None:
        statement = select(CampusAllowanceSetting).where(CampusAllowanceSetting.setting_key == SETTING_KEY)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    @staticmethod
    def _validated(default_allowance_usd: Decimal) -> Decimal:
        if default_allowance_usd < 0:
            raise CampusValidationError("default allowance cannot be negative")
        return default_allowance_usd.quantize(ALLOWANCE_PRECISION, rounding=ROUND_HALF_UP)

    def _audit(
        self,
        action: str,
        actor_account_id: str,
        *,
        previous_configured: Decimal | None,
        current: Decimal,
        application: AllowanceApplication,
    ) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="allowance_setting",
                target_id=SETTING_KEY,
                details_json=json.dumps(
                    {
                        "previous_configured_default_allowance_usd": _amount(previous_configured),
                        "default_allowance_usd": _amount(current),
                        "scanned_students": application.scanned,
                        "changed_students": application.changed,
                    },
                    separators=(",", ":"),
                ),
            )
        )


def _amount(value: Decimal | None) -> str | None:
    """Render one amount for the audit trail, which stores JSON text."""
    return None if value is None else str(value)
