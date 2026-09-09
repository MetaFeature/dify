from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from models.campus import ReservationStatus, StudentStatus
from services.campus.errors import CampusValidationError


@dataclass(frozen=True)
class StudentIdentity:
    student_number: str
    display_name: str
    cohort: str | None = None

    def __post_init__(self) -> None:
        if not self.student_number.strip():
            raise CampusValidationError("student_number is required")
        if not self.display_name.strip():
            raise CampusValidationError("display_name is required")


@dataclass(frozen=True)
class SyncResult:
    created: int
    updated: int
    password_resets: int = 0
    default_passwords: int = 0


@dataclass(frozen=True)
class ReservationResult:
    id: str
    status: ReservationStatus
    starts_at: datetime
    ends_at: datetime
    waitlist_position: int | None = None


@dataclass(frozen=True)
class SlotAvailability:
    starts_at: datetime
    ends_at: datetime
    capacity: int
    confirmed: int
    waitlisted: int
    reservable: bool


@dataclass(frozen=True)
class SlotCapacityChange:
    starts_at: datetime
    ends_at: datetime
    capacity: int
    previous_capacity: int
    confirmed: int
    waitlisted: int


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reservation_id: str | None = None
    ends_at: datetime | None = None
    server_now: datetime | None = None


@dataclass(frozen=True)
class ModelUsage:
    model: str
    quota: int
    requests: int


@dataclass(frozen=True)
class GatewayUsage:
    remaining_quota: int
    used_quota: int
    by_model: tuple[ModelUsage, ...]


class ModelGateway(Protocol):
    def get_usage(self, token_id: str) -> GatewayUsage: ...

    def adjust_quota(self, token_id: str, delta_quota: int, request_id: str) -> GatewayUsage: ...


@dataclass(frozen=True)
class ModelUsageSummary:
    model: str
    used_usd: Decimal
    requests: int


@dataclass(frozen=True)
class AllowanceSummary:
    remaining_usd: Decimal
    used_usd: Decimal
    total_usd: Decimal
    model_calls_enabled: bool
    by_model: tuple[ModelUsageSummary, ...]


@dataclass(frozen=True)
class StudentAdministrationDetail:
    id: str
    student_number: str
    display_name: str
    cohort: str | None
    status: StudentStatus
    workspace_id: str | None
    allowance: AllowanceSummary | None


@dataclass(frozen=True)
class ProvisionedWorkspace:
    dify_account_id: str
    dify_tenant_id: str


@dataclass(frozen=True)
class ManagedGatewayToken:
    token_id: str
    secret: str
    created: bool


@dataclass(frozen=True)
class GatewayModel:
    name: str
    model_type: str
    endpoints: tuple[str, ...] = ()
    billing_mode: str = "ratio"


@dataclass(frozen=True)
class ProvisionedPlatform:
    workspace: ProvisionedWorkspace
    gateway_token_id: str


@dataclass(frozen=True)
class ModelAccountReconciliation:
    students: int
    bindings_created: int
    existing_bindings: int


class WorkspaceProvisioner(Protocol):
    def provision(self, student_number: str, display_name: str) -> ProvisionedWorkspace: ...


class GatewayProvisioner(Protocol):
    def create_managed_token(
        self,
        external_ref: str,
        student_number: str,
        student_name: str,
        allowance_quota: int,
    ) -> ManagedGatewayToken: ...

    def update_managed_identity(self, token_id: str, student_number: str, student_name: str) -> None: ...

    def delete_managed_token(self, token_id: str) -> None: ...


class ModelConfigurator(Protocol):
    def configure(self, dify_tenant_id: str, gateway_secret: str) -> None: ...

    def needs_configuration(self, dify_tenant_id: str) -> bool: ...


class PlatformProvisioner(Protocol):
    def ensure_ready(self, student_id: str) -> ProvisionedPlatform | None: ...


class IdentitySource(Protocol):
    def authenticate(self, subject: str, credential: str) -> StudentIdentity: ...


class CurrentSlotLoadAdmission(Protocol):
    """Decide whether current server load permits one supplemental reservation."""

    def allows_current_slot_reservation(self) -> bool:
        """Return false when the load threshold is exceeded or the signal is unavailable."""
        ...


class StudentRosterSource(Protocol):
    def load_students(self) -> Iterable[StudentIdentity]: ...


@dataclass(frozen=True)
class IssuedPortalSession:
    token: str
    student_id: str
    expires_at: datetime
