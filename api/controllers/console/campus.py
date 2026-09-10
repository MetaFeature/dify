from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

import flask_login
from flask import Response, make_response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound, TooManyRequests, Unauthorized

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    credential_service,
    lab_manuals,
    launch_service,
    newapi_client,
    portal_presentation,
    portal_sessions,
    portal_student,
    portal_token,
    require_campus_enabled,
    reservation_service,
)
from controllers.console.campus_schemas import (
    AccessDecisionResponse,
    AllowanceResponse,
    ExperimentTrackListResponse,
    LabManualResponse,
    PortalLoginResponse,
    PortalPasswordChangePayload,
    PortalPresentationResponse,
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
from libs.exception import BaseHTTPException
from libs.helper import dump_response, extract_remote_ip
from libs.login import current_account_with_tenant_optional
from libs.token import (
    clear_access_token_from_cookie,
    clear_csrf_token_from_cookie,
    clear_refresh_token_from_cookie,
    set_access_token_to_cookie,
    set_csrf_token_to_cookie,
    set_refresh_token_to_cookie,
)
from models.campus import MANUAL_TRACKS, ExperimentTrack, LabManualChapterStatus
from services.account_service import AccountService
from services.campus.allowance_service import AllowanceService
from services.campus.domain import AllowanceSummary, ReservationResult
from services.campus.errors import (
    AccessSlotRequiredError,
    CampusAdministratorRequiredError,
    CampusValidationError,
    CurrentSlotLoadUnavailableError,
    DuplicateSlotClaimError,
    GatewayBindingNotFoundError,
    PendingReservationExistsError,
    PortalSessionError,
    ReservationCancellationError,
    ReservationNotFoundError,
    ReservationWindowError,
    StudentNotFoundError,
    StudentPasswordStrengthError,
    StudentSuspendedError,
)
from services.campus.portal_presentation_service import PortalPresentation


class PendingReservationExistsHTTPError(BaseHTTPException):
    error_code = "pending_reservation_exists"
    description = "Student already has a reservation or waitlist entry that has not granted access"
    code = 409


class DuplicateSlotClaimHTTPError(BaseHTTPException):
    error_code = "duplicate_slot_claim"
    description = "Student already has an unfinished claim on this slot"
    code = 409


class InvalidNewPasswordHTTPError(BaseHTTPException):
    error_code = "invalid_new_password"
    description = "New password does not meet the strength requirements"
    code = 400


def _reservation_response(reservation: ReservationResult) -> dict[str, object]:
    return dump_response(ReservationResponse, reservation)


def _allowance_response(summary: AllowanceSummary) -> dict[str, object]:
    return dump_response(AllowanceResponse, summary)


def _presentation_response(presentation: PortalPresentation) -> dict[str, object]:
    return dump_response(
        PortalPresentationResponse,
        {
            "login_html": presentation.login_html,
            "tracks": [
                {
                    "track": item.track,
                    "title": item.title,
                    "description": item.description,
                    "position": item.position,
                }
                for item in presentation.tracks
            ],
            "is_custom": presentation.is_custom,
        },
    )


def _manual_origin_url(chapter_id: str, endpoint: str) -> str:
    hostname = request.host.split(":", 1)[0]
    return (
        f"{request.scheme}://{hostname}:{dify_config.CAMPUS_MANUAL_PUBLIC_PORT}"
        f"/console/api/campus/lab-manuals/documents/{quote(chapter_id)}/{endpoint}"
    )


def _manual_view_url(chapter_id: str) -> str:
    return _manual_origin_url(chapter_id, "view")


def _manual_content_url(chapter_id: str) -> str:
    return f"/console/api/campus/lab-manuals/documents/{quote(chapter_id)}/content"


def _manual_view_html(*, title: str, filename: str, content_url: str, portal_url: str) -> str:
    safe_title = escape(title)
    safe_filename = escape(filename)
    safe_content_url = escape(content_url, quote=True)
    safe_portal_url = escape(portal_url, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{safe_title} · AI 实践平台</title>
  <style>
    :root {{
      color: #172036;
      background: #eef3fb;
      font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 320px;
      height: 100vh;
      margin: 0;
      overflow: hidden;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      min-height: 76px;
      padding: 12px 24px;
      border-bottom: 1px solid #dce4f0;
      background: rgba(255, 255, 255, .96);
      box-shadow: 0 8px 28px rgba(45, 61, 96, .08);
    }}
    .identity {{ display: flex; align-items: center; min-width: 0; gap: 13px; }}
    .mark {{
      display: grid;
      place-items: center;
      flex: 0 0 44px;
      width: 44px;
      height: 44px;
      border-radius: 13px;
      color: #fff;
      background: #2453a6;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .08em;
    }}
    .title {{ min-width: 0; }}
    h1 {{
      margin: 0 0 3px;
      overflow: hidden;
      font-size: 18px;
      line-height: 1.25;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    p {{ margin: 0; overflow: hidden; color: #707b92; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }}
    nav {{ display: flex; flex: 0 0 auto; gap: 9px; }}
    a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 15px;
      border: 1px solid #d5ddea;
      border-radius: 11px;
      color: #36547f;
      background: #fff;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
    }}
    a:hover {{ border-color: #9fb1ce; background: #f5f8fd; }}
    a.primary {{
      border-color: #2458ae;
      color: #fff;
      background: #2458ae;
      box-shadow: 0 8px 18px rgba(36, 88, 174, .2);
    }}
    iframe {{ display: block; width: 100%; height: 100%; border: 0; background: #fff; }}
    @media (max-width: 640px) {{
      header {{ align-items: stretch; flex-direction: column; gap: 10px; padding: 12px; }}
      nav {{ width: 100%; }}
      nav a {{ flex: 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="identity">
      <span class="mark" aria-hidden="true">NJIT</span>
      <div class="title"><h1>{safe_title}</h1><p>{safe_filename}</p></div>
    </div>
    <nav aria-label="手册操作">
      <a href="{safe_content_url}" target="manual-content">重新载入手册</a>
      <a class="primary" href="{safe_portal_url}">返回实验选择</a>
    </nav>
  </header>
  <iframe name="manual-content" src="{safe_content_url}" title="{safe_title}" allowfullscreen></iframe>
</body>
</html>"""


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
        response = make_response(
            dump_response(
                PortalLoginResponse,
                {
                    "student_id": issued.student_id,
                    "expires_at": issued.expires_at,
                    "must_change_password": credential_service().must_change_password(issued.student_id),
                },
            )
        )
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
        except PendingReservationExistsError as error:
            raise PendingReservationExistsHTTPError() from error
        except DuplicateSlotClaimError as error:
            raise DuplicateSlotClaimHTTPError() from error
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


@console_ns.route("/campus/session/logout")
class CampusSessionLogoutApi(Resource):
    @console_ns.response(200, "Campus and Dify sessions ended", console_ns.models[ResultResponse.__name__])
    @setup_required
    def post(self) -> ResponseReturnValue:
        require_campus_enabled()
        raw_portal_token = request.cookies.get(dify_config.CAMPUS_PORTAL_COOKIE_NAME)
        if raw_portal_token:
            portal_sessions().revoke(raw_portal_token, now=datetime.now(UTC))

        account, _ = current_account_with_tenant_optional()
        if account is not None:
            AccountService.logout(account=account)
            flask_login.logout_user()

        response = make_response(ResultResponse(result="success").model_dump(mode="json"))
        clear_access_token_from_cookie(response)
        clear_refresh_token_from_cookie(response)
        clear_csrf_token_from_cookie(response)
        response.set_cookie(
            dify_config.CAMPUS_PORTAL_COOKIE_NAME,
            "",
            expires=0,
            max_age=0,
            httponly=True,
            secure=dify_config.CAMPUS_PORTAL_COOKIE_SECURE,
            samesite="Lax",
            path="/",
        )
        return response


@console_ns.route("/campus/password")
class CampusPasswordChangeApi(Resource):
    @console_ns.expect(console_ns.models[PortalPasswordChangePayload.__name__])
    @console_ns.response(200, "Password changed", console_ns.models[ResultResponse.__name__])
    @setup_required
    def post(self) -> ResponseReturnValue:
        require_campus_enabled()
        student = portal_student(allow_initial_password=True)
        payload = PortalPasswordChangePayload.model_validate(console_ns.payload or {})
        try:
            credential_service().change_password(
                student.id,
                payload.current_password,
                payload.new_password,
            )
        except StudentPasswordStrengthError as error:
            raise InvalidNewPasswordHTTPError() from error
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        except PortalSessionError as error:
            raise Forbidden("Current password is incorrect") from error
        return ResultResponse(result="success").model_dump(mode="json")


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
                quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
            ).get_summary(student.id)
        except GatewayBindingNotFoundError as error:
            raise Conflict("Student model allowance is not provisioned") from error
        return _allowance_response(summary)


@console_ns.route("/campus/experiment-tracks")
class CampusExperimentTrackListApi(Resource):
    @console_ns.response(200, "Experiment tracks", console_ns.models[ExperimentTrackListResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        portal_student()
        manuals = lab_manuals()
        tracks = []
        for track in ExperimentTrack:
            tracks.append(
                {
                    "track": track,
                    "kind": "dify" if track is ExperimentTrack.LARGE_MODEL else "manual",
                    "chapters": len(manuals.published_chapters(track)),
                }
            )
        return dump_response(ExperimentTrackListResponse, {"data": tracks})


@console_ns.route("/campus/presentation")
class CampusPortalPresentationApi(Resource):
    @console_ns.response(200, "Published Portal presentation", console_ns.models[PortalPresentationResponse.__name__])
    @setup_required
    def get(self) -> ResponseReturnValue:
        require_campus_enabled()
        return _presentation_response(portal_presentation().published())


@console_ns.route("/campus/lab-manuals/<string:track>")
class CampusLabManualApi(Resource):
    @console_ns.response(200, "Published lab manual", console_ns.models[LabManualResponse.__name__])
    @setup_required
    def get(self, track: str) -> ResponseReturnValue:
        require_campus_enabled()
        # Signing in is enough: a manual carries no student data, no credential,
        # and no allowance, and the two manual tracks are not gated by a
        # reservation because they run on the student's own machine (ADR-0018).
        portal_student()
        try:
            experiment_track = ExperimentTrack(track)
        except ValueError as error:
            raise NotFound("Unknown experiment track") from error
        if experiment_track not in MANUAL_TRACKS:
            raise NotFound("This experiment track has no lab manual")
        chapters = lab_manuals().published_chapters(experiment_track)
        return dump_response(
            LabManualResponse,
            {
                "track": experiment_track,
                "data": [
                    {
                        "id": chapter.id,
                        "track": chapter.track,
                        "title": chapter.title,
                        "original_filename": chapter.original_filename or f"{chapter.title}.html",
                        "size_bytes": (
                            chapter.document_size_bytes
                            if chapter.document_size_bytes is not None
                            else len(chapter.body_html.encode("utf-8"))
                        ),
                        "content_url": _manual_view_url(chapter.id),
                        "position": chapter.position,
                        "status": chapter.status,
                    }
                    for chapter in chapters
                ],
            },
        )


@console_ns.route("/campus/lab-manuals/documents/<string:chapter_id>/view")
class CampusLearningDocumentViewApi(Resource):
    @setup_required
    def get(self, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        administrator = _is_administrator_reader()
        if not administrator:
            portal_student()
        try:
            document = lab_manuals().chapter(chapter_id)
        except CampusValidationError as error:
            raise NotFound(str(error)) from error
        if not administrator and document.status is not LabManualChapterStatus.PUBLISHED:
            raise NotFound("Learning document was not found")
        hostname = request.host.split(":", 1)[0]
        page = _manual_view_html(
            title=document.title,
            filename=document.original_filename or f"{document.title}.html",
            content_url=_manual_content_url(chapter_id),
            portal_url=f"{request.scheme}://{hostname}/portal/?from=manual",
        )
        response = make_response(page)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Cache-Control"] = "private, no-store"
        return response


@console_ns.route("/campus/lab-manuals/documents/<string:chapter_id>/content")
class CampusLearningDocumentContentApi(Resource):
    @setup_required
    def get(self, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        administrator = _is_administrator_reader()
        if not administrator:
            portal_student()
        try:
            document = lab_manuals().chapter(chapter_id)
        except CampusValidationError as error:
            raise NotFound(str(error)) from error
        if not administrator and document.status is not LabManualChapterStatus.PUBLISHED:
            raise NotFound("Learning document was not found")
        original = lab_manuals().document(chapter_id)
        response = make_response(original.data)
        response.headers["Content-Type"] = "text/html"
        response.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(original.filename)}"
        response.headers["Cache-Control"] = "private, no-store"
        return response


@console_ns.route("/campus/lab-manuals/images/<string:image_id>")
class CampusLabManualImageApi(Resource):
    @setup_required
    def get(self, image_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        # A manual is readable by any signed-in student, and so are its images.
        # An administrator previewing a chapter reaches this through the same
        # route on the loopback listener, where a console session stands in.
        _require_manual_reader()
        try:
            image = lab_manuals().image(image_id)
        except CampusValidationError as error:
            raise NotFound(str(error)) from error
        response = make_response(image.data)
        response.headers["Content-Type"] = image.mime_type
        # Same-origin only, never inline-rendered as a document, and cacheable
        # because an image is addressed by an immutable id.
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response


def _require_manual_reader() -> None:
    """Accept a portal session, or a console session belonging to an administrator.

    Students read manuals through the portal session. An administrator previewing
    a chapter on the loopback listener has a console session instead and no
    portal session at all, so both are accepted here -- the same shape the access
    check above uses.
    """
    account, _ = current_account_with_tenant_optional()
    if account is not None:
        try:
            admin_service().require_admin(account.id, display_name=account.name)
        except CampusAdministratorRequiredError:
            pass
        else:
            return
    portal_student()


def _is_administrator_reader() -> bool:
    account, _ = current_account_with_tenant_optional()
    if account is None:
        return False
    try:
        admin_service().require_admin(account.id, display_name=account.name)
    except CampusAdministratorRequiredError:
        return False
    return True
