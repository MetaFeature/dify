class CampusError(Exception):
    """Base error for expected Campus domain failures."""


class CampusValidationError(CampusError):
    """Raised when a Campus command violates an input or precision contract."""


class CampusConflictError(CampusError):
    """Raised when a valid Campus command conflicts with persisted state."""


class StudentNotFoundError(CampusError):
    pass


class StudentSuspendedError(CampusError):
    pass


class PendingReservationExistsError(CampusError):
    """Raised when the student already holds a claim that has not yet granted access."""


class DuplicateSlotClaimError(CampusError):
    """Raised when the student already holds an unfinished claim on the requested slot."""


class ReservationWindowError(CampusError):
    pass


class CurrentSlotLoadUnavailableError(CampusError):
    """Raised when an under-capacity current slot fails load admission."""


class ReservationNotFoundError(CampusError):
    pass


class ReservationCancellationError(CampusError):
    pass


class GatewayBindingNotFoundError(CampusError):
    pass


class PortalSessionError(CampusError):
    pass


class IdentitySourceNotConfiguredError(PortalSessionError):
    """Raised when a declared institutional identity adapter has no concrete schema."""


class CredentialNotFoundError(PortalSessionError):
    """Raised when a student has no platform-held credential row, allowing fallback sources."""


class CampusAdministratorRequiredError(CampusError):
    pass


class AccessSlotRequiredError(CampusError):
    pass


class CampusProvisioningError(CampusError):
    """Raised when a managed Dify workspace cannot be safely provisioned or reused."""


class CampusProvisioningLockError(CampusProvisioningError):
    """Raised when provisioning cannot acquire its cross-request serialization lock."""


class ModelGatewayError(CampusError):
    """Raised when the isolated model gateway rejects or cannot complete an operation."""


class CampusAccountNotFoundError(CampusError):
    """Raised when a requested Dify account does not exist."""
