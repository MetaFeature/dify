"""Request-scoped composition root for Campus console resources."""

from datetime import UTC, datetime, timedelta

from flask import request
from werkzeug.exceptions import Forbidden, NotFound, Unauthorized

from configs import dify_config
from extensions.ext_database import db
from models import Account
from models.campus import CampusStudent
from services.campus.administrator_service import AdministratorService
from services.campus.dify_adapters import DifyModelConfigurator, DifySessionIssuer, DifyWorkspaceProvisioner
from services.campus.domain import IdentitySource
from services.campus.errors import (
    CampusAdministratorRequiredError,
    CampusProvisioningError,
    ModelGatewayError,
    PortalSessionError,
    StudentNotFoundError,
    StudentSuspendedError,
)
from services.campus.identity_source import UnconfiguredIdentitySource, VirtualIdentitySource
from services.campus.newapi_client import NewApiClient
from services.campus.portal_session_service import PortalSessionService
from services.campus.provisioning_service import PlatformProvisioningService
from services.campus.reservation_service import ReservationService
from services.campus.session_launch_service import SessionLaunchService
from services.campus.student_service import StudentAdministrationService


def require_campus_enabled() -> None:
    if not dify_config.CAMPUS_ENABLED:
        raise NotFound()


def identity_source() -> IdentitySource:
    if not dify_config.CAMPUS_VIRTUAL_IDENTITY_ENABLED:
        return UnconfiguredIdentitySource()
    configured = dify_config.CAMPUS_VIRTUAL_IDENTITIES_JSON
    if configured is None:
        return UnconfiguredIdentitySource()
    return VirtualIdentitySource(configured.get_secret_value())


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


def reservation_service() -> ReservationService:
    return ReservationService(
        session=db.session(),
        capacity=dify_config.CAMPUS_RESERVATION_CAPACITY,
        booking_days=dify_config.CAMPUS_BOOKING_DAYS,
    )


def student_service() -> StudentAdministrationService:
    return StudentAdministrationService(
        session=db.session(),
        default_allowance_yuan=dify_config.CAMPUS_DEFAULT_ALLOWANCE_YUAN,
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
    return PlatformProvisioningService(
        session=session,
        workspace_provisioner=DifyWorkspaceProvisioner(
            session=session,
            service_principal_email=principal_email,
        ),
        gateway_provisioner=newapi_client(),
        model_configurator=DifyModelConfigurator(
            session=session,
            provider=dify_config.CAMPUS_MODEL_PROVIDER,
            credential_name=dify_config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME,
            api_key_field=dify_config.CAMPUS_MODEL_PROVIDER_API_KEY_FIELD,
            base_url_field=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD,
            base_url=dify_config.CAMPUS_MODEL_PROVIDER_BASE_URL,
        ),
        quota_units_per_yuan=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_YUAN,
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
        session_issuer=DifySessionIssuer(session=session),
    )


def portal_token() -> str:
    token = request.cookies.get(dify_config.CAMPUS_PORTAL_COOKIE_NAME)
    if not token:
        raise Unauthorized("Campus portal session is required")
    return token


def portal_student() -> CampusStudent:
    try:
        return portal_sessions().resolve(portal_token(), now=datetime.now(UTC))
    except (PortalSessionError, StudentNotFoundError, StudentSuspendedError) as error:
        raise Unauthorized("Campus portal session is invalid") from error
