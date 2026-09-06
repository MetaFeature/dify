from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.campus import CampusGatewayBinding, CampusStudent
from services.campus.domain import GatewayProvisioner, ModelAccountReconciliation
from services.campus.errors import CampusProvisioningError


class StudentModelAccountService:
    """Preprovision and reconcile one hidden NewAPI account per roster student."""

    def __init__(self, *, session: Session, gateway: GatewayProvisioner, quota_units_per_usd: int) -> None:
        if quota_units_per_usd < 1:
            raise ValueError("quota_units_per_usd must be positive")
        self._session = session
        self._gateway = gateway
        self._quota_units_per_usd = quota_units_per_usd

    def reconcile(self, *, student_numbers: Sequence[str] | None = None) -> ModelAccountReconciliation:
        statement = select(CampusStudent).order_by(CampusStudent.student_number)
        if student_numbers is not None:
            normalized = tuple(number.strip() for number in student_numbers if number.strip())
            if not normalized:
                return ModelAccountReconciliation(students=0, bindings_created=0, existing_bindings=0)
            statement = statement.where(CampusStudent.student_number.in_(normalized))
        students = list(self._session.scalars(statement))
        created_token_ids: list[str] = []
        bindings_created = 0
        existing_bindings = 0
        try:
            for student in students:
                managed = self._gateway.create_managed_token(
                    student.id,
                    student.student_number,
                    student.display_name,
                    self._allowance_quota(student.initial_allowance_usd),
                )
                if managed.created:
                    created_token_ids.append(managed.token_id)
                binding = self._session.scalar(
                    select(CampusGatewayBinding)
                    .where(CampusGatewayBinding.student_id == student.id)
                    .with_for_update()
                )
                if binding is None:
                    self._session.add(
                        CampusGatewayBinding(
                            student_id=student.id,
                            gateway_token_id=managed.token_id,
                        )
                    )
                    bindings_created += 1
                elif binding.gateway_token_id == managed.token_id:
                    existing_bindings += 1
                else:
                    raise CampusProvisioningError(
                        f"Campus student {student.student_number} is bound to a different managed token"
                    )
            self._session.commit()
        except Exception as error:
            self._session.rollback()
            for token_id in created_token_ids:
                self._gateway.delete_managed_token(token_id)
            if isinstance(error, IntegrityError):
                raise CampusProvisioningError(
                    "Campus model account binding conflicted during reconciliation"
                ) from error
            raise
        return ModelAccountReconciliation(
            students=len(students),
            bindings_created=bindings_created,
            existing_bindings=existing_bindings,
        )

    def _allowance_quota(self, allowance_usd: Decimal) -> int:
        raw_quota = allowance_usd * self._quota_units_per_usd
        if raw_quota != raw_quota.to_integral_value():
            raise ValueError("initial allowance is smaller than gateway quota precision")
        return int(raw_quota)
