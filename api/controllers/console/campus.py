from datetime import UTC, datetime

from flask import Response, make_response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound, TooManyRequests, Unauthorized

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    launch_service,
    newapi_client,
    portal_sessions,
    portal_student,
    portal_token,
    require_campus_enabled,
    reservation_service,
)
from controllers.console.campus_schemas import (
    AccessDecisionResponse,
    AllowanceResponse,
    PortalLoginResponse,
    ReservationCreatePayload,
    ReservationListResponse,
    ReservationResponse,
    ResultResponse,
    SlotListQuery,
    SlotListResponse,
    VirtualLoginPayload,
)
from controllers.console.wraps import setup_required
from extensions.ext_database import db
from libs.helper import dump_response, extract_remote_ip
from libs.login import current_account_with_tenant_optional
from libs.token import set_access_token_to_cookie, set_csrf_token_to_cookie, set_refresh_token_to_cookie
from services.campus.allowance_service import AllowanceService
from services.campus.domain import AllowanceSummary, ReservationResult
from services.campus.errors import (
    AccessSlotRequiredError,
    ActiveReservationExistsError,
    CampusAdministratorRequiredError,
    CurrentSlotLoadUnavailableError,
    GatewayBindingNotFoundError,
    PortalSessionError,
    ReservationCancellationError,
    ReservationNotFoundError,
    ReservationWindowError,
    StudentNotFoundError,
    StudentSuspendedError,
)


def _reservation_response(reservation: ReservationResult) -> dict[str, object]:
    return dump_response(ReservationResponse, reservation)


def _allowance_response(summary: AllowanceSummary) -> dict[str, object]:
    return dump_response(AllowanceResponse, summary)


@console_ns.route("/campus/auth/virtual")
class CampusVirtualLoginApi(Resource):
    @console_ns.expect(console_ns.models[VirtualLoginPayload.__name__])
    @console_ns.response(200, "Portal session issued", console_ns.models[PortalLoginResponse.__name__])
    @setup_required
    def post(self) -> ResponseReturnValue:
        require_campus_enabled()
        if not dify_config.CAMPUS_VIRTUAL_IDENTITY_ENABLED:
            raise NotFound()
        payload = VirtualLoginPayload.model_validate(console_ns.payload or {})
        try:
            issued = portal_sessions().authenticate(
                payload.subject,
                payload.credential,
                now=datetime.now(UTC),
            )
        except (PortalSessionError, StudentNotFoundError, StudentSuspendedError) as error:
            raise Unauthorized("Student identity could not be verified") from error
        response = make_response(dump_response(PortalLoginResponse, issued))
        response.set_cookie(
            dify_config.CAMPUS_PORTAL_COOKIE_NAME,
            issued.token,
            httponly=True,
            secure=dify_config.CAMPUS_PORTAL_COOKIE_SECURE,
            samesite="Lax",
            max_age=dify_config.CAMPUS_PORTAL_SESSION_TTL_HOURS * 3600,
            path="/",
        )
        return response


@console_ns.route("/campus/slots")
class CampusSlotListApi(Resource):
    @console_ns.doc(params=query_params_from_model(SlotListQuery))
    @console_ns.response(200, "Access slots", console_ns.models[SlotListResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        portal_student()
        query = SlotListQuery.model_validate(request.args.to_dict(flat=True))
        try:
            slots = reservation_service().list_slots(query.day, now=datetime.now(UTC))
        except ReservationWindowError as error:
            raise BadRequest(str(error)) from error
        return dump_response(SlotListResponse, {"data": slots})


@console_ns.route("/campus/reservations")
class CampusReservationListApi(Resource):
    @console_ns.response(200, "Student reservations", console_ns.models[ReservationListResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student()
        reservations = reservation_service().list_student_reservations(student.id, now=datetime.now(UTC))
        return dump_response(ReservationListResponse, {"data": reservations})

    @console_ns.expect(console_ns.models[ReservationCreatePayload.__name__])
    @console_ns.response(201, "Reservation created", console_ns.models[ReservationResponse.__name__])
    @setup_required
    def post(self) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student()
        payload = ReservationCreatePayload.model_validate(console_ns.payload or {})
        try:
            reservation = reservation_service().reserve(student.id, payload.starts_at, now=datetime.now(UTC))
        except ActiveReservationExistsError as error:
            raise Conflict("Student already has an unfinished reservation") from error
        except CurrentSlotLoadUnavailableError as error:
            raise TooManyRequests("Current slot admission is temporarily unavailable") from error
        except ReservationWindowError as error:
            raise BadRequest(str(error)) from error
        return _reservation_response(reservation), 201


@console_ns.route("/campus/reservations/<string:reservation_id>")
class CampusReservationApi(Resource):
    @console_ns.response(204, "Reservation cancelled")
    @setup_required
    def delete(self, reservation_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student()
        try:
            reservation_service().cancel(student.id, reservation_id, now=datetime.now(UTC))
        except ReservationNotFoundError as error:
            raise NotFound("Reservation not found") from error
        except ReservationCancellationError as error:
            raise Conflict(str(error)) from error
        return Response(status=204)


@console_ns.route("/campus/access")
class CampusAccessApi(Resource):
    @console_ns.response(200, "Current access decision", console_ns.models[AccessDecisionResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student()
        decision = reservation_service().access_decision(student.id, now=datetime.now(UTC))
        return dump_response(AccessDecisionResponse, decision)


@console_ns.route("/campus/session/access-check")
class CampusSessionAccessCheckApi(Resource):
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        account, _ = current_account_with_tenant_optional()
        if account is not None:
            try:
                admin_service().require_admin(account.id, display_name=account.name)
            except CampusAdministratorRequiredError:
                pass
            else:
                return Response(status=204)
        try:
            decision = launch_service().access_check(portal_token(), now=datetime.now(UTC))
        except (PortalSessionError, StudentNotFoundError, StudentSuspendedError):
            return Response(status=401)
        return Response(status=204 if decision.allowed else 403)


@console_ns.route("/campus/session/launch")
class CampusSessionLaunchApi(Resource):
    @console_ns.response(200, "Dify session launched", console_ns.models[ResultResponse.__name__])
    @setup_required
    def post(self) -> ResponseReturnValue:
        require_campus_enabled()
        try:
            token_pair = launch_service().launch(
                portal_token(),
                now=datetime.now(UTC),
                ip_address=extract_remote_ip(request),
            )
        except AccessSlotRequiredError as error:
            raise Forbidden("An active confirmed reservation is required") from error
        except (PortalSessionError, StudentNotFoundError, StudentSuspendedError) as error:
            raise Unauthorized("Campus portal session is invalid") from error
        response = make_response(ResultResponse(result="success").model_dump(mode="json"))
        set_access_token_to_cookie(request, response, token_pair.access_token)
        set_refresh_token_to_cookie(request, response, token_pair.refresh_token)
        set_csrf_token_to_cookie(request, response, token_pair.csrf_token)
        return response


@console_ns.route("/campus/allowance")
class CampusAllowanceApi(Resource):
    @console_ns.response(200, "Student model allowance", console_ns.models[AllowanceResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student()
        try:
            summary = AllowanceService(
                session=db.session(),
                gateway=newapi_client(),
                quota_units_per_yuan=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_YUAN,
            ).get_summary(student.id)
        except GatewayBindingNotFoundError as error:
            raise Conflict("Student model allowance is not provisioned") from error
        return _allowance_response(summary)
