import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAllowanceAdjustment, CampusGatewayBinding
from services.campus.domain import AllowanceSummary, GatewayUsage, ModelGateway, ModelUsageSummary
from services.campus.errors import CampusConflictError, CampusValidationError, GatewayBindingNotFoundError

logger = logging.getLogger(__name__)

# A page is at most 500 rows; the gateway is on the same Docker network, so a
# handful of concurrent lookups keeps a page responsive without hammering it.
_USAGE_FETCH_WORKERS = 8


class AllowanceService:
    """Translate opaque gateway quota units into redacted US-dollar allowance views."""

    _session: Session
    _gateway: ModelGateway
    _quota_units_per_usd: int

    def __init__(self, *, session: Session, gateway: ModelGateway, quota_units_per_usd: int) -> None:
        if quota_units_per_usd < 1:
            raise ValueError("quota_units_per_usd must be positive")
        self._session = session
        self._gateway = gateway
        self._quota_units_per_usd = quota_units_per_usd

    def get_summary(self, student_id: str) -> AllowanceSummary:
        """Fetch gateway usage and return only the redacted dollar-denominated view."""
        binding = self._binding(student_id)
        return self._to_summary(self._gateway.get_usage(binding.gateway_token_id))

    def allows_model_calls(self, student_id: str) -> bool:
        """Return whether the student may still start a session on their allowance.

        A student who has never been provisioned has no gateway binding yet, so
        there is nothing to exhaust and the gate stays open — provisioning runs
        later in the same login. Once a binding exists, only a positive remaining
        quota opens the gate.
        """
        try:
            return self.get_summary(student_id).model_calls_enabled
        except GatewayBindingNotFoundError:
            return True

    def summaries_for(self, student_ids: Sequence[str]) -> dict[str, AllowanceSummary]:
        """Allowance per student, for one page of the administration list.

        The gateway exposes usage one token at a time, so a page costs one call
        per bound student. They run on a small pool and a row that fails is left
        out of the result: a gateway hiccup must not blank the whole list, and
        the caller renders a missing entry as "unknown" rather than as zero.
        """
        if not student_ids:
            return {}
        bindings = {
            binding.student_id: binding.gateway_token_id
            for binding in self._session.scalars(
                select(CampusGatewayBinding).where(CampusGatewayBinding.student_id.in_(list(student_ids)))
            )
        }
        if not bindings:
            return {}
        summaries: dict[str, AllowanceSummary] = {}
        with ThreadPoolExecutor(max_workers=min(_USAGE_FETCH_WORKERS, len(bindings))) as pool:
            pending = {
                pool.submit(self._gateway.get_usage, token_id): student_id
                for student_id, token_id in bindings.items()
            }
            for future in as_completed(pending):
                student_id = pending[future]
                try:
                    summaries[student_id] = self._to_summary(future.result())
                except Exception:
                    logger.warning("campus allowance lookup failed for student %s", student_id, exc_info=True)
        return summaries

    def adjust(
        self,
        student_id: str,
        *,
        delta_usd: Decimal,
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
                or existing.delta_usd != delta_usd
                or existing.reason != reason.strip()
                or existing.actor_account_id != actor_account_id
            ):
                raise CampusConflictError("allowance request id was reused with different parameters")
            return self.get_summary(student_id)

        binding = self._binding(student_id)
        raw_delta = delta_usd * self._quota_units_per_usd
        if raw_delta != raw_delta.to_integral_value():
            raise CampusValidationError("delta_usd is smaller than the gateway quota precision")
        usage = self._gateway.adjust_quota(binding.gateway_token_id, int(raw_delta), request_id)
        self._session.add(
            CampusAllowanceAdjustment(
                student_id=student_id,
                request_id=request_id,
                actor_account_id=actor_account_id,
                delta_usd=delta_usd,
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
        remaining = self._usd(usage.remaining_quota)
        used = self._usd(usage.used_quota)
        by_model = tuple(
            ModelUsageSummary(model=item.model, used_usd=self._usd(item.quota), requests=item.requests)
            for item in usage.by_model
        )
        return AllowanceSummary(
            remaining_usd=remaining,
            used_usd=used,
            total_usd=remaining + used,
            model_calls_enabled=usage.remaining_quota > 0,
            by_model=by_model,
        )

    def _usd(self, quota: int) -> Decimal:
        return (Decimal(quota) / Decimal(self._quota_units_per_usd)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
