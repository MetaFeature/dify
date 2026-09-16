from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusStudent, CampusWorkspaceBinding
from services.account_service import TokenPair
from services.campus.domain import AccessDecision, AllowanceGate
from services.campus.errors import AccessSlotRequiredError, CampusAllowanceExhaustedError, CampusProvisioningError


class PortalSessionResolver(Protocol):
    def resolve(self, raw_token: str, *, now: datetime) -> CampusStudent: ...


class AccessDecisionService(Protocol):
    def access_decision(self, student_id: str, *, now: datetime) -> AccessDecision: ...


class SessionIssuer(Protocol):
    def issue(self, account_id: str, tenant_id: str, *, ip_address: str | None) -> TokenPair: ...


class SessionLaunchService:
    """Authorize a booked portal identity and issue its bound stock Dify session."""

    _session: Session
    _portal_sessions: PortalSessionResolver
    _reservations: AccessDecisionService
    _allowance_gate: AllowanceGate
    _session_issuer: SessionIssuer

    def __init__(
        self,
        *,
        session: Session,
        portal_sessions: PortalSessionResolver,
        reservations: AccessDecisionService,
        allowance_gate: AllowanceGate,
        session_issuer: SessionIssuer,
    ) -> None:
        self._session = session
        self._portal_sessions = portal_sessions
        self._reservations = reservations
        self._allowance_gate = allowance_gate
        self._session_issuer = session_issuer

    def access_check(self, raw_token: str, *, now: datetime) -> AccessDecision:
        student = self._portal_sessions.resolve(raw_token, now=now)
        return self._reservations.access_decision(student.id, now=now)

    def launch(self, raw_token: str, *, now: datetime, ip_address: str | None) -> TokenPair:
        student = self._portal_sessions.resolve(raw_token, now=now)
        # An exhausted allowance also closes the door on an already-issued portal
        # session, so a student cannot re-enter Dify after spending everything.
        if not self._allowance_gate.allows_model_calls(student.id):
            raise CampusAllowanceExhaustedError(student.student_number)
        decision = self._reservations.access_decision(student.id, now=now)
        if not decision.allowed:
            raise AccessSlotRequiredError("an active confirmed reservation is required")
        binding = self._session.scalar(
            select(CampusWorkspaceBinding).where(CampusWorkspaceBinding.student_id == student.id)
        )
        if binding is None:
            raise CampusProvisioningError("Campus workspace binding is missing")
        return self._session_issuer.issue(
            binding.dify_account_id,
            binding.dify_tenant_id,
            ip_address=ip_address,
        )
