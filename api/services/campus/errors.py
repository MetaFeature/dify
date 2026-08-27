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


class ActiveReservationExistsError(CampusError):
    pass


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
