from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAllowanceAdjustment, CampusGatewayBinding
from services.campus.domain import AllowanceSummary, GatewayUsage, ModelGateway, ModelUsageSummary
from services.campus.errors import CampusConflictError, CampusValidationError, GatewayBindingNotFoundError


class AllowanceService:
    """Translate opaque gateway quota units into redacted RMB allowance views."""

    _session: Session
    _gateway: ModelGateway
    _quota_units_per_yuan: int

    def __init__(self, *, session: Session, gateway: ModelGateway, quota_units_per_yuan: int) -> None:
        if quota_units_per_yuan < 1:
            raise ValueError("quota_units_per_yuan must be positive")
        self._session = session
        self._gateway = gateway
        self._quota_units_per_yuan = quota_units_per_yuan

    def get_summary(self, student_id: str) -> AllowanceSummary:
        """Fetch gateway usage and return only the redacted RMB-denominated view."""
        binding = self._binding(student_id)
        return self._to_summary(self._gateway.get_usage(binding.gateway_token_id))

    def adjust(
        self,
        student_id: str,
        *,
        delta_yuan: Decimal,
        reason: str,
        actor_account_id: str,
        request_id: str,
    ) -> AllowanceSummary:
        """Apply one idempotent external quota change, persist its audit row, and commit."""
        if not reason.strip():
            raise CampusValidationError("reason is required")
        existing = self._session.scalar(
            select(CampusAllowanceAdjustment).where(CampusAllowanceAdjustment.request_id == request_id)
        )
        if existing is not None:
            if (
                existing.student_id != student_id
                or existing.delta_yuan != delta_yuan
                or existing.reason != reason.strip()
                or existing.actor_account_id != actor_account_id
            ):
                raise CampusConflictError("allowance request id was reused with different parameters")
            return self.get_summary(student_id)

        binding = self._binding(student_id)
        raw_delta = delta_yuan * self._quota_units_per_yuan
        if raw_delta != raw_delta.to_integral_value():
            raise CampusValidationError("delta_yuan is smaller than the gateway quota precision")
        usage = self._gateway.adjust_quota(binding.gateway_token_id, int(raw_delta), request_id)
        self._session.add(
            CampusAllowanceAdjustment(
                student_id=student_id,
                request_id=request_id,
                actor_account_id=actor_account_id,
                delta_yuan=delta_yuan,
                reason=reason.strip(),
            )
        )
        self._session.commit()
        return self._to_summary(usage)

    def _binding(self, student_id: str) -> CampusGatewayBinding:
        binding = self._session.scalar(
            select(CampusGatewayBinding).where(CampusGatewayBinding.student_id == student_id)
        )
        if binding is None:
            raise GatewayBindingNotFoundError(student_id)
        return binding

    def _to_summary(self, usage: GatewayUsage) -> AllowanceSummary:
        remaining = self._yuan(usage.remaining_quota)
        used = self._yuan(usage.used_quota)
        by_model = tuple(
            ModelUsageSummary(model=item.model, used_yuan=self._yuan(item.quota), requests=item.requests)
            for item in usage.by_model
        )
        return AllowanceSummary(
            remaining_yuan=remaining,
            used_yuan=used,
            total_yuan=remaining + used,
            model_calls_enabled=usage.remaining_quota > 0,
            by_model=by_model,
        )

    def _yuan(self, quota: int) -> Decimal:
        return (Decimal(quota) / Decimal(self._quota_units_per_yuan)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
