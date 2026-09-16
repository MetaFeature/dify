class CampusError(Exception):
    """Base error for expected Campus domain failures."""


class CampusValidationError(CampusError):
    """Raised when a Campus command violates an input or precision contract."""


class StudentPasswordStrengthError(CampusValidationError):
    """Raised when a student-chosen replacement password is too weak."""


class CampusConflictError(CampusError):
    """Raised when a valid Campus command conflicts with persisted state."""


class StudentNotFoundError(CampusError):
    pass


class StudentDeletedError(CampusError):
    """A soft-deleted student. Kept apart from suspension so the portal can say why."""

    def __init__(self, student_number: str) -> None:
        self.student_number = student_number
        super().__init__(f"student {student_number} is deleted")


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


class CampusAllowanceExhaustedError(CampusError):
    """Raised when a student has no remaining model allowance to start a session."""


class CampusAudioDurationExceededError(CampusError, ValueError):
    """Raised when an uploaded voice clip is longer than the Campus limit.

    Subclasses ``ValueError`` for the same reason as the knowledge limit: Dify's
    console error handler turns it into a 400 whose message reaches the browser.
    """


class CampusKnowledgeLimitExceededError(CampusError, ValueError):
    """Raised when a student workspace exceeds the platform knowledge limits.

    It also subclasses ``ValueError`` because the limits are enforced from core
    dataset code paths: Dify's console error handler maps ``ValueError`` to a 400
    whose message is shown to the student, so the reason reaches the browser
    without a Campus-specific handler on every dataset route.
    """


class CampusProvisioningError(CampusError):
    """Raised when a managed Dify workspace cannot be safely provisioned or reused."""


class CampusProvisioningLockError(CampusProvisioningError):
    """Raised when provisioning cannot acquire its cross-request serialization lock."""


class ModelGatewayError(CampusError):
    """Raised when the isolated model gateway rejects or cannot complete an operation."""


class CampusAccountNotFoundError(CampusError):
    """Raised when a requested Dify account does not exist."""
