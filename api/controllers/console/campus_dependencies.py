"""Request-scoped composition root for Campus console resources."""

from datetime import UTC, datetime, timedelta

from flask import request
from werkzeug.exceptions import Forbidden, NotFound, Unauthorized

from configs import dify_config
from extensions.ext_database import db
from extensions.ext_storage import storage
from models import Account
from models.campus import CampusStudent
from services.campus.administrator_service import AdministratorService
from services.campus.allowance_service import AllowanceService
from services.campus.credential_service import ManagedFirstIdentitySource, StudentCredentialService
from services.campus.default_allowance_service import DefaultAllowanceService
from services.campus.dify_adapters import (
    DifyAccountDeleter,
    DifyModelConfigurator,
    DifySessionIssuer,
    DifyWorkspaceProvisioner,
    RefreshingModelConfigurator,
    parse_campus_models,
)
from services.campus.domain import IdentitySource
from services.campus.errors import (
    CampusAdministratorRequiredError,
    CampusProvisioningError,
    ModelGatewayError,
    PortalSessionError,
    StudentDeletedError,
    StudentNotFoundError,
    StudentSuspendedError,
)
from services.campus.identity_source import UnconfiguredIdentitySource, VirtualIdentitySource
from services.campus.knowledge_limit_service import KnowledgeLimitService, knowledge_limit_service
from services.campus.lab_manual_service import LabManualService
from services.campus.load_admission import SystemLoadAdmission
from services.campus.model_account_service import StudentModelAccountService
from services.campus.newapi_client import NewApiClient
from services.campus.portal_login_service import PortalLoginPageService
from services.campus.portal_presentation_service import PortalPresentationService
from services.campus.portal_session_service import PortalSessionService
from services.campus.provisioning_service import PlatformProvisioningService
from services.campus.reservation_service import ReservationService
from services.campus.session_launch_service import SessionLaunchService
from services.campus.slot_capacity_service import SlotCapacityService
from services.campus.student_retention_service import StudentRetentionService
from services.campus.student_service import StudentAdministrationService
from services.campus.usage_report import UsageReportService


def require_campus_enabled() -> None:
    if not dify_config.CAMPUS_ENABLED:
        raise NotFound()


def virtual_student_numbers() -> frozenset[str]:
    """Student numbers still present in the virtual demo roster, for admin visibility."""
    if not dify_config.CAMPUS_VIRTUAL_IDENTITY_ENABLED:
        return frozenset()
    configured = dify_config.CAMPUS_VIRTUAL_IDENTITIES_JSON
    if configured is None:
        return frozenset()
    return VirtualIdentitySource(configured.get_secret_value()).student_numbers


def fallback_identity_source() -> IdentitySource:
    if not dify_config.CAMPUS_VIRTUAL_IDENTITY_ENABLED:
        return UnconfiguredIdentitySource()
    configured = dify_config.CAMPUS_VIRTUAL_IDENTITIES_JSON
    return UnconfiguredIdentitySource() if configured is None else VirtualIdentitySource(configured.get_secret_value())


def credential_service() -> StudentCredentialService:
    return StudentCredentialService(session=db.session(), fallback_identity_source=fallback_identity_source())


def identity_source() -> IdentitySource:
    fallback = fallback_identity_source()
    credentials = StudentCredentialService(session=db.session(), fallback_identity_source=fallback)
    return ManagedFirstIdentitySource(credentials, fallback)


def student_retention() -> StudentRetentionService:
    """The irreversible second stage of deletion; see the service docstring."""
    session = db.session()
    return StudentRetentionService(
        session=session,
        gateway=newapi_client(),
        delete_dify_account=DifyAccountDeleter(session=session),
    )


def usage_reports() -> UsageReportService:
    """Read-only token usage / billing reporting straight from the gateway."""
    return UsageReportService(gateway=newapi_client())


def lab_manuals() -> LabManualService:
    return LabManualService(session=db.session(), storage=storage)


def portal_presentation() -> PortalPresentationService:
    return PortalPresentationService(session=db.session())


def newapi_client() -> NewApiClient:
    base_url = dify_config.CAMPUS_NEWAPI_BASE_URL
    access_token = dify_config.CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN
    if not base_url or access_token is None:
        raise ModelGatewayError("Campus NewAPI management connection is not configured")
    model_limits = tuple(item.strip() for item in dify_config.CAMPUS_NEWAPI_MODEL_LIMITS.split(",") if item.strip())
    return NewApiClient(
        base_url=base_url,
        admin_access_token=access_token.get_secret_value(),
        admin_user_id=dify_config.CAMPUS_NEWAPI_ADMIN_USER_ID,
        group=dify_config.CAMPUS_NEWAPI_GROUP,
        model_limits=model_limits,
    )


def slot_capacity_service() -> SlotCapacityService:
    return SlotCapacityService(
        session=db.session(),
        platform_default=dify_config.CAMPUS_RESERVATION_CAPACITY,
        booking_days=dify_config.CAMPUS_BOOKING_DAYS,
    )


def reservation_service() -> ReservationService:
    return ReservationService(
        session=db.session(),
        capacity=slot_capacity_service().effective_capacity(),
        booking_days=dify_config.CAMPUS_BOOKING_DAYS,
        current_slot_load_admission=SystemLoadAdmission(
            max_load_per_cpu=dify_config.CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU,
        ),
    )


def knowledge_limits() -> KnowledgeLimitService:
    return knowledge_limit_service(db.session())


def default_allowance() -> DefaultAllowanceService:
    """The platform default allowance, as an administrator last set it.

    `student_service()` reads it once per request, so a roster import or a new
    student picks up the current default while students that already hold a
    model account keep their own allowance.
    """
    return DefaultAllowanceService(
        session=db.session(),
        platform_default_usd=dify_config.CAMPUS_DEFAULT_ALLOWANCE_USD,
    )


def portal_login_pages() -> PortalLoginPageService:
    return PortalLoginPageService(session=db.session())


def student_service() -> StudentAdministrationService:
    return StudentAdministrationService(
        session=db.session(),
        default_allowance_usd=default_allowance().effective_default(),
    )


def allowance_service() -> AllowanceService:
    return AllowanceService(
        session=db.session(),
        gateway=newapi_client(),
        quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
    )


def model_account_service() -> StudentModelAccountService:
    return StudentModelAccountService(
        session=db.session(),
        gateway=newapi_client(),
        quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
    )


def admin_service() -> AdministratorService:
    bootstrap_ids = tuple(
        item.strip() for item in dify_config.CAMPUS_BOOTSTRAP_ADMIN_ACCOUNT_IDS.split(",") if item.strip()
    )
    return AdministratorService(session=db.session(), bootstrap_account_ids=bootstrap_ids)


def require_admin(account: Account) -> None:
    try:
        admin_service().require_admin(account.id, display_name=account.name)
    except CampusAdministratorRequiredError as error:
        raise Forbidden("Campus administrator access is required") from error


def platform_provisioner() -> PlatformProvisioningService:
    principal_email = dify_config.CAMPUS_SERVICE_PRINCIPAL_EMAIL
    if not principal_email:
        raise CampusProvisioningError("Campus service principal is not configured")
    session = db.session()
    gateway = newapi_client()

    def current_model_configurator() -> DifyModelConfigurator:
        gateway_models = gateway.get_model_catalog()
        model_spec = ",".join(f"{model.model_type}:{model.name}" for model in gateway_models)
        return DifyModelConfigurator(
            session=session,
            provider=dify_config.CAMPUS_MODEL_PROVIDER,
            provider_plugin_unique_identifier=dify_config.CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER,
            credential_name=dify_config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME,
            credential_scope=dify_config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE,
            api_key_field=dify_config.CAMPUS_MODEL_PROVIDER_API_KEY_FIELD,
            base_url_field=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD,
            base_url=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL,
            models=parse_campus_models(model_spec),
            api_protocol=dify_config.CAMPUS_MODEL_PROVIDER_API_PROTOCOL,
            plugin_package_path=dify_config.CAMPUS_MODEL_PROVIDER_PLUGIN_PACKAGE_PATH,
            vision_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_VISION_MODELS.split(",") if item.strip()
            ),
            audio_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_AUDIO_MODELS.split(",") if item.strip()
            ),
            document_models=tuple(
                item.strip() for item in dify_config.CAMPUS_MODEL_PROVIDER_DOCUMENT_MODELS.split(",") if item.strip()
            ),
        )

    return PlatformProvisioningService(
        session=session,
        workspace_provisioner=DifyWorkspaceProvisioner(
            session=session,
            service_principal_email=principal_email,
        ),
        gateway_provisioner=gateway,
        model_configurator=RefreshingModelConfigurator(current_model_configurator),
        quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
    )


def portal_sessions() -> PortalSessionService:
    return PortalSessionService(
        session=db.session(),
        identity_source=identity_source(),
        platform_provisioner=platform_provisioner(),
        session_ttl=timedelta(hours=dify_config.CAMPUS_PORTAL_SESSION_TTL_HOURS),
    )


def launch_service() -> SessionLaunchService:
    session = db.session()
    return SessionLaunchService(
        session=session,
        portal_sessions=portal_sessions(),
        reservations=reservation_service(),
        allowance_gate=allowance_service(),
        session_issuer=DifySessionIssuer(session=session),
    )


def portal_token() -> str:
    token = request.cookies.get(dify_config.CAMPUS_PORTAL_COOKIE_NAME)
    if not token:
        raise Unauthorized("Campus portal session is required")
    return token


def portal_student(*, allow_initial_password: bool = False) -> CampusStudent:
    try:
        student = portal_sessions().resolve(portal_token(), now=datetime.now(UTC))
    except (PortalSessionError, StudentDeletedError, StudentNotFoundError, StudentSuspendedError) as error:
        raise Unauthorized("Campus portal session is invalid") from error
    if not allow_initial_password and credential_service().must_change_password(student.id):
        raise Forbidden("Initial password must be changed before using the platform")
    return student
