"""Named-administrator resources for roster, lifecycle, slot, and allowance operations."""

from datetime import UTC, datetime
from urllib.parse import quote

from flask import Response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, NotFound, ServiceUnavailable

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    credential_service,
    lab_manuals,
    model_account_service,
    newapi_client,
    portal_presentation,
    require_admin,
    require_campus_enabled,
    reservation_service,
    student_service,
    virtual_student_numbers,
)
from controllers.console.campus_schemas import (
    AdministratorCreatePayload,
    AdministratorListResponse,
    AdministratorPayload,
    AdminSlotListResponse,
    AllowanceAdjustmentPayload,
    AllowanceResponse,
    LabManualChapterDetailResponse,
    LabManualChapterListResponse,
    LabManualChapterPositionPayload,
    LabManualChapterResponse,
    LabManualChapterSavedResponse,
    LabManualChapterStatusPayload,
    PortalPresentationPayload,
    PortalPresentationResponse,
    ResultResponse,
    RosterParsedResponse,
    RosterSyncResponse,
    SlotCapacityPayload,
    SlotCapacityResponse,
    SlotListQuery,
    StudentCreatePayload,
    StudentDetailResponse,
    StudentIdentityPayload,
    StudentListQuery,
    StudentListResponse,
    StudentPasswordResetPayload,
    StudentResponse,
    StudentRosterSyncPayload,
    StudentStatusPayload,
)
from controllers.console.wraps import setup_required, with_current_user
from extensions.ext_database import db
from libs.helper import dump_response
from libs.login import login_required
from models import Account
from models.campus import ExperimentTrack
from services.campus.administration_query_service import CampusAdministrationQueryService
from services.campus.allowance_service import AllowanceService
from services.campus.domain import AllowanceSummary, StudentIdentity
from services.campus.errors import (
    CampusAccountNotFoundError,
    CampusAdministratorRequiredError,
    CampusConflictError,
    CampusProvisioningError,
    CampusValidationError,
    GatewayBindingNotFoundError,
    ModelGatewayError,
    ReservationWindowError,
    StudentNotFoundError,
)
from services.campus.lab_manual_service import MAX_DOCUMENT_BYTES
from services.campus.portal_presentation_service import PortalPresentation, TrackPresentation
from services.campus.roster_import import MAX_ROSTER_BYTES, parse_roster_xlsx


def _allowance_response(summary: AllowanceSummary) -> dict[str, object]:
    return dump_response(AllowanceResponse, summary)


def _portal_presentation_response(presentation: PortalPresentation) -> dict[str, object]:
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


def _identity_from(student: StudentIdentityPayload) -> StudentIdentity:
    return StudentIdentity(
        student_number=student.student_number,
        display_name=student.display_name,
        cohort=student.cohort,
    )


def _passwords_from(students: list[StudentIdentityPayload]) -> dict[str, str]:
    return {student.student_number.strip(): student.password for student in students if student.password}


@console_ns.route("/campus/admin/students")
class CampusAdminStudentListApi(Resource):
    @console_ns.doc(params=query_params_from_model(StudentListQuery))
    @console_ns.response(200, "Campus students", console_ns.models[StudentListResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        query = StudentListQuery.model_validate(request.args.to_dict(flat=True))
        students = student_service().list_students(limit=query.limit, offset=query.offset)
        credentialed = credential_service().credentialed_student_ids([student.id for student in students])
        virtual_numbers = virtual_student_numbers()
        return dump_response(
            StudentListResponse,
            {
                "data": [
                    {
                        "id": student.id,
                        "student_number": student.student_number,
                        "display_name": student.display_name,
                        "cohort": student.cohort,
                        "status": student.status,
                        "has_credential": student.id in credentialed,
                        "virtual_identity": student.student_number in virtual_numbers,
                    }
                    for student in students
                ]
            },
        )

    @console_ns.expect(console_ns.models[StudentCreatePayload.__name__])
    @console_ns.response(201, "Student created", console_ns.models[StudentResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentCreatePayload.model_validate(console_ns.payload or {})
        try:
            student_service().get_student(payload.student_number)
        except StudentNotFoundError:
            pass
        else:
            raise Conflict("Student already exists")
        try:
            student_service().sync_students(
                [
                    StudentIdentity(
                        student_number=payload.student_number,
                        display_name=payload.display_name,
                        cohort=payload.cohort,
                    )
                ],
                actor_account_id=current_user.id,
                passwords={payload.student_number: payload.password},
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        try:
            model_account_service().reconcile(student_numbers=(payload.student_number,))
        except (CampusProvisioningError, ModelGatewayError) as error:
            raise ServiceUnavailable("Student model account could not be provisioned") from error
        student = student_service().get_student(payload.student_number)
        return dump_response(StudentResponse, student), 201


@console_ns.route("/campus/admin/students/<string:student_number>")
class CampusAdminStudentDetailApi(Resource):
    @console_ns.response(200, "Campus student detail", console_ns.models[StudentDetailResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            detail = CampusAdministrationQueryService(
                session=db.session(),
                students=student_service(),
                gateway=newapi_client(),
                quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
            ).get_student_detail(student_number)
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentDetailResponse, detail)


@console_ns.route("/campus/admin/slots")
class CampusAdminSlotApi(Resource):
    @console_ns.doc(params=query_params_from_model(SlotListQuery))
    @console_ns.response(200, "Access slots for one day", console_ns.models[AdminSlotListResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        query = SlotListQuery.model_validate(request.args.to_dict(flat=True))
        now = datetime.now(UTC)
        slots = reservation_service().admin_list_slots(query.day, now=now)
        return dump_response(AdminSlotListResponse, {"data": slots, "server_now": now})

    @console_ns.expect(console_ns.models[SlotCapacityPayload.__name__])
    @console_ns.response(200, "Slot capacity changed", console_ns.models[SlotCapacityResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = SlotCapacityPayload.model_validate(console_ns.payload or {})
        try:
            change = reservation_service().set_slot_capacity(
                payload.starts_at,
                payload.capacity,
                actor_account_id=current_user.id,
                now=datetime.now(UTC),
            )
        except (ReservationWindowError, CampusValidationError) as error:
            raise BadRequest(str(error)) from error
        return dump_response(SlotCapacityResponse, change)


@console_ns.route("/campus/admin/students/sync")
class CampusAdminStudentSyncApi(Resource):
    @console_ns.expect(console_ns.models[StudentRosterSyncPayload.__name__])
    @console_ns.response(200, "Roster synchronized", console_ns.models[RosterSyncResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentRosterSyncPayload.model_validate(console_ns.payload or {})
        try:
            result = student_service().sync_students(
                [_identity_from(student) for student in payload.students],
                actor_account_id=current_user.id,
                passwords=_passwords_from(payload.students),
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        try:
            model_account_service().reconcile(
                student_numbers=tuple(student.student_number for student in payload.students)
            )
        except (CampusProvisioningError, ModelGatewayError) as error:
            raise ServiceUnavailable("Student model accounts could not be reconciled") from error
        return dump_response(RosterSyncResponse, result)


@console_ns.route("/campus/admin/students/sync/preview")
class CampusAdminStudentSyncPreviewApi(Resource):
    @console_ns.expect(console_ns.models[StudentRosterSyncPayload.__name__])
    @console_ns.response(200, "Roster import preview", console_ns.models[RosterSyncResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentRosterSyncPayload.model_validate(console_ns.payload or {})
        try:
            preview = student_service().preview_sync(
                [_identity_from(student) for student in payload.students],
                passwords=_passwords_from(payload.students),
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(RosterSyncResponse, preview)


@console_ns.route("/campus/admin/students/import/parse")
class CampusAdminStudentImportParseApi(Resource):
    @console_ns.response(200, "Parsed roster workbook", console_ns.models[RosterParsedResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise BadRequest("Roster XLSX file is required")
        if not upload.filename.lower().endswith(".xlsx"):
            raise BadRequest("Roster file must use the .xlsx format")
        try:
            rows = parse_roster_xlsx(upload.stream.read(MAX_ROSTER_BYTES + 1))
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(
            RosterParsedResponse,
            {
                "data": [
                    {
                        "student_number": row.student_number,
                        "display_name": row.display_name,
                        "cohort": row.cohort,
                        "password": row.password,
                    }
                    for row in rows
                ]
            },
        )


@console_ns.route("/campus/admin/students/<string:student_number>/password")
class CampusAdminStudentPasswordApi(Resource):
    @console_ns.expect(console_ns.models[StudentPasswordResetPayload.__name__])
    @console_ns.response(200, "Password reset", console_ns.models[ResultResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentPasswordResetPayload.model_validate(console_ns.payload or {})
        try:
            credential_service().set_password(student_number, payload.password, now=datetime.now(UTC))
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return ResultResponse(result="success").model_dump(mode="json")


@console_ns.route("/campus/admin/students/<string:student_number>/status")
class CampusAdminStudentStatusApi(Resource):
    @console_ns.expect(console_ns.models[StudentStatusPayload.__name__])
    @console_ns.response(200, "Student status changed", console_ns.models[StudentResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def patch(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentStatusPayload.model_validate(console_ns.payload or {})
        try:
            student = student_service().set_status(
                student_number,
                payload.status,
                actor_account_id=current_user.id,
            )
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentResponse, student)


@console_ns.route("/campus/admin/students/<string:student_number>/allowance-adjustments")
class CampusAdminAllowanceAdjustmentApi(Resource):
    @console_ns.expect(console_ns.models[AllowanceAdjustmentPayload.__name__])
    @console_ns.response(200, "Allowance adjusted", console_ns.models[AllowanceResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = AllowanceAdjustmentPayload.model_validate(console_ns.payload or {})
        try:
            student = student_service().get_student(student_number)
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        service = AllowanceService(
            session=db.session(),
            gateway=newapi_client(),
            quota_units_per_usd=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD,
        )
        try:
            summary = service.adjust(
                student.id,
                delta_usd=payload.delta_usd,
                reason=payload.reason,
                actor_account_id=current_user.id,
                request_id=payload.request_id,
            )
        except GatewayBindingNotFoundError as error:
            raise Conflict("Student model allowance is not provisioned") from error
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        except CampusConflictError as error:
            raise Conflict(str(error)) from error
        return _allowance_response(summary)


@console_ns.route("/campus/admin/administrators")
class CampusAdministratorApi(Resource):
    @console_ns.response(200, "Active administrators", console_ns.models[AdministratorListResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return dump_response(AdministratorListResponse, {"data": admin_service().list_active()})

    @console_ns.expect(console_ns.models[AdministratorPayload.__name__])
    @console_ns.response(201, "Administrator added", console_ns.models[ResultResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = AdministratorPayload.model_validate(console_ns.payload or {})
        try:
            admin_service().add_admin(payload.account_id, actor_account_id=current_user.id)
        except CampusAccountNotFoundError as error:
            raise NotFound("Dify account not found") from error
        return ResultResponse(result="success").model_dump(mode="json"), 201


@console_ns.route("/campus/admin/administrators/create")
class CampusAdministratorCreateApi(Resource):
    @console_ns.expect(console_ns.models[AdministratorCreatePayload.__name__])
    @console_ns.response(201, "Administrator account created", console_ns.models[ResultResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = AdministratorCreatePayload.model_validate(console_ns.payload or {})
        try:
            admin_service().create_admin_account(
                email=payload.email,
                name=payload.name,
                password=payload.password,
                actor_account_id=current_user.id,
            )
        except CampusConflictError as error:
            raise Conflict(str(error)) from error
        except ValueError as error:
            raise BadRequest(str(error)) from error
        return ResultResponse(result="success").model_dump(mode="json"), 201


@console_ns.route("/campus/admin/administrators/<string:account_id>")
class CampusAdministratorDeleteApi(Resource):
    @console_ns.response(204, "Administrator revoked")
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account, account_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            admin_service().revoke_admin(account_id, actor_account_id=current_user.id)
        except CampusAdministratorRequiredError as error:
            raise NotFound("Campus administrator not found") from error
        except CampusConflictError as error:
            raise Conflict(str(error)) from error
        return Response(status=204)


def _track_or_404(track: str) -> ExperimentTrack:
    try:
        return ExperimentTrack(track)
    except ValueError as error:
        raise NotFound("Unknown experiment track") from error


@console_ns.route("/campus/admin/presentation")
class CampusAdminPortalPresentationApi(Resource):
    @console_ns.response(200, "Portal presentation draft", console_ns.models[PortalPresentationResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return _portal_presentation_response(portal_presentation().draft())

    @console_ns.expect(console_ns.models[PortalPresentationPayload.__name__])
    @console_ns.response(200, "Portal presentation draft saved", console_ns.models[PortalPresentationResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = PortalPresentationPayload.model_validate(console_ns.payload or {})
        try:
            saved = portal_presentation().save_draft(
                login_html=payload.login_html,
                tracks=[
                    TrackPresentation(
                        track=item.track,
                        title=item.title,
                        description=item.description,
                        position=item.position,
                    )
                    for item in payload.tracks
                ],
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return _portal_presentation_response(saved)

    @console_ns.response(
        200, "Portal presentation default restored", console_ns.models[PortalPresentationResponse.__name__]
    )
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return _portal_presentation_response(portal_presentation().restore_default(actor_account_id=current_user.id))


@console_ns.route("/campus/admin/presentation/publish")
class CampusAdminPortalPresentationPublishApi(Resource):
    @console_ns.response(200, "Portal presentation published", console_ns.models[PortalPresentationResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return _portal_presentation_response(portal_presentation().publish(actor_account_id=current_user.id))


def _chapter_payload(chapter) -> dict[str, object]:
    filename = getattr(chapter, "original_filename", None) or f"{chapter.title}.html"
    size_bytes = getattr(chapter, "document_size_bytes", None)
    if size_bytes is None:
        size_bytes = getattr(chapter, "size_bytes", None)
    if size_bytes is None:
        size_bytes = len(chapter.body_html.encode("utf-8"))
    return {
        "id": chapter.id,
        "track": chapter.track,
        "title": chapter.title,
        "original_filename": filename,
        "size_bytes": size_bytes,
        "content_url": _manual_content_url(chapter.id),
        "position": chapter.position,
        "status": chapter.status,
    }


def _manual_content_url(chapter_id: str) -> str:
    hostname = request.host.split(":", 1)[0]
    return (
        f"{request.scheme}://{hostname}:{dify_config.CAMPUS_MANUAL_PUBLIC_PORT}"
        f"/console/api/campus/lab-manuals/documents/{quote(chapter_id)}/content"
    )


@console_ns.route("/campus/admin/lab-manuals/<string:track>/chapters")
class CampusAdminLabManualChapterListApi(Resource):
    @console_ns.response(200, "Lab manual chapters", console_ns.models[LabManualChapterListResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account, track: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        chapters = lab_manuals().all_chapters(_track_or_404(track))
        return dump_response(
            LabManualChapterListResponse,
            {"data": [_chapter_payload(chapter) for chapter in chapters]},
        )

    @console_ns.response(201, "Chapter created", console_ns.models[LabManualChapterSavedResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account, track: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise BadRequest("An HTML file is required")
        try:
            outcome = lab_manuals().create_chapter(
                _track_or_404(track),
                filename=upload.filename,
                data=upload.read(MAX_DOCUMENT_BYTES + 1),
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(LabManualChapterSavedResponse, _saved_chapter(outcome)), 201


@console_ns.route("/campus/admin/lab-manuals/chapters/<string:chapter_id>")
class CampusAdminLabManualChapterApi(Resource):
    @console_ns.response(200, "Chapter detail", console_ns.models[LabManualChapterDetailResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            chapter = lab_manuals().chapter(chapter_id)
        except CampusValidationError as error:
            raise _chapter_error(error)
        return dump_response(
            LabManualChapterDetailResponse,
            _chapter_payload(chapter),
        )

    @console_ns.response(200, "Chapter updated", console_ns.models[LabManualChapterSavedResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise BadRequest("An HTML file is required")
        try:
            outcome = lab_manuals().update_chapter(
                chapter_id,
                filename=upload.filename,
                data=upload.read(MAX_DOCUMENT_BYTES + 1),
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise _chapter_error(error)
        return dump_response(LabManualChapterSavedResponse, _saved_chapter(outcome))

    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            lab_manuals().delete_chapter(chapter_id, actor_account_id=current_user.id)
        except CampusValidationError as error:
            raise _chapter_error(error)
        return Response(status=204)


@console_ns.route("/campus/admin/lab-manuals/chapters/<string:chapter_id>/status")
class CampusAdminLabManualChapterStatusApi(Resource):
    @console_ns.expect(console_ns.models[LabManualChapterStatusPayload.__name__])
    @console_ns.response(200, "Chapter status changed", console_ns.models[LabManualChapterResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def patch(self, current_user: Account, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = LabManualChapterStatusPayload.model_validate(console_ns.payload or {})
        try:
            outcome = lab_manuals().set_chapter_status(chapter_id, payload.status, actor_account_id=current_user.id)
        except CampusValidationError as error:
            raise _chapter_error(error)
        return dump_response(LabManualChapterResponse, _chapter_payload(outcome))


@console_ns.route("/campus/admin/lab-manuals/chapters/<string:chapter_id>/position")
class CampusAdminLabManualChapterPositionApi(Resource):
    @console_ns.expect(console_ns.models[LabManualChapterPositionPayload.__name__])
    @console_ns.response(200, "Chapter moved", console_ns.models[LabManualChapterListResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account, chapter_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = LabManualChapterPositionPayload.model_validate(console_ns.payload or {})
        manuals = lab_manuals()
        try:
            manuals.move_chapter(chapter_id, position=payload.position, actor_account_id=current_user.id)
            chapter = manuals.chapter(chapter_id)
        except CampusValidationError as error:
            raise _chapter_error(error)
        return dump_response(
            LabManualChapterListResponse,
            {"data": [_chapter_payload(sibling) for sibling in manuals.all_chapters(chapter.track)]},
        )


def _saved_chapter(outcome) -> dict[str, object]:
    return _chapter_payload(outcome)


def _chapter_error(error: CampusValidationError) -> Exception:
    """A missing chapter is a 404; anything else the service rejected is a 400."""
    if "was not found" in str(error):
        return NotFound(str(error))
    return BadRequest(str(error))


@console_ns.route("/campus/admin/lab-manuals/<string:track>/images")
class CampusAdminLabManualImageApi(Resource):
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account, track: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        upload = request.files.get("file")
        if upload is None:
            raise BadRequest("An image file is required")
        try:
            uploaded = lab_manuals().add_image(
                _track_or_404(track),
                data=upload.read(),
                mime_type=upload.mimetype or "",
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return {
            "id": uploaded.id,
            "url": uploaded.url,
            "mime_type": uploaded.mime_type,
            "size_bytes": uploaded.size_bytes,
        }, 201
