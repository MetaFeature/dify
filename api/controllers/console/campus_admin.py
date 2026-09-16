"""Named-administrator resources for roster, lifecycle, slot, and allowance operations."""

from datetime import UTC, datetime
from urllib.parse import quote

from flask import Response, make_response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, NotFound, ServiceUnavailable

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    allowance_service,
    credential_service,
    default_allowance,
    knowledge_limits,
    lab_manuals,
    model_account_service,
    newapi_client,
    portal_login_pages,
    portal_presentation,
    require_admin,
    require_campus_enabled,
    reservation_service,
    slot_capacity_service,
    student_retention,
    student_service,
    usage_reports,
    virtual_student_numbers,
)
from controllers.console.campus_schemas import (
    AdministratorCreatePayload,
    AdministratorListResponse,
    AdministratorPayload,
    AdminSlotListResponse,
    AllowanceAdjustmentPayload,
    AllowanceResponse,
    DefaultAllowanceChangeResponse,
    DefaultAllowancePayload,
    DefaultAllowanceResponse,
    KnowledgeLimitSettingPayload,
    KnowledgeLimitSettingResponse,
    LabManualChapterDetailResponse,
    LabManualChapterListResponse,
    LabManualChapterPositionPayload,
    LabManualChapterResponse,
    LabManualChapterSavedResponse,
    LabManualChapterStatusPayload,
    PortalLoginStateResponse,
    PortalPresentationPayload,
    PortalPresentationResponse,
    ResultResponse,
    RetentionPurgeResponse,
    RosterParsedResponse,
    RosterSyncResponse,
    SlotCapacityPayload,
    SlotCapacityResponse,
    SlotCapacitySettingChangeResponse,
    SlotCapacitySettingPayload,
    SlotCapacitySettingResponse,
    SlotListQuery,
    StudentCreatePayload,
    StudentDetailResponse,
    StudentIdentityPayload,
    StudentInitialPasswordResponse,
    StudentListQuery,
    StudentListResponse,
    StudentRenamePayload,
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
from services.campus.portal_login_page import MAX_PORTAL_LOGIN_BYTES
from services.campus.portal_presentation_service import PortalPresentation, TrackPresentation
from services.campus.roster_import import MAX_ROSTER_BYTES, parse_roster_xlsx
from services.campus.student_service import derive_initial_password
from services.campus.usage_report import render_usage_report_html, render_usage_report_xlsx
from services.campus.usage_report_service import parse_granularity
from tasks.campus_provision_student_task import provision_student_workspace_task


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
        students = student_service().list_students(
            limit=query.limit,
            offset=query.offset,
            keyword=query.keyword,
            include_deleted=query.include_deleted,
        )
        credentialed = credential_service().credentialed_student_ids([student.id for student in students])
        # One gateway lookup per row; rows the gateway did not answer for come
        # back as None and the portal renders them as unknown, not as zero.
        allowances = allowance_service().summaries_for([student.id for student in students])
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
                        "deleted_at": student.deleted_at,
                        "allowance": allowances.get(student.id),
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
        # Warm up the workspace in the background: building it here would add
        # several seconds to this request, and the student has not signed in yet.
        provision_student_workspace_task.delay(payload.student_number)
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


@console_ns.route("/campus/admin/slot-capacity")
class CampusAdminSlotCapacityApi(Resource):
    @console_ns.response(200, "Slot capacity setting", console_ns.models[SlotCapacitySettingResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        state = slot_capacity_service().state()
        return dump_response(SlotCapacitySettingResponse, _slot_capacity_setting_payload(state))

    @console_ns.expect(console_ns.models[SlotCapacitySettingPayload.__name__])
    @console_ns.response(
        200, "Slot capacity setting changed", console_ns.models[SlotCapacitySettingChangeResponse.__name__]
    )
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = SlotCapacitySettingPayload.model_validate(console_ns.payload or {})
        try:
            change = slot_capacity_service().set_default_capacity(
                payload.capacity,
                actor_account_id=current_user.id,
                now=datetime.now(UTC),
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(SlotCapacitySettingChangeResponse, _slot_capacity_change_payload(change))

    @console_ns.response(
        200, "Slot capacity setting default restored", console_ns.models[SlotCapacitySettingChangeResponse.__name__]
    )
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        change = slot_capacity_service().restore_default(
            actor_account_id=current_user.id,
            now=datetime.now(UTC),
        )
        return dump_response(SlotCapacitySettingChangeResponse, _slot_capacity_change_payload(change))


def _slot_capacity_setting_payload(state) -> dict[str, object]:
    return {
        "capacity": state.capacity,
        "platform_default": state.platform_default,
        "configured_capacity": state.configured_capacity,
        "is_default": state.is_default,
    }


def _slot_capacity_change_payload(change) -> dict[str, object]:
    return {
        **_slot_capacity_setting_payload(change.state),
        "previous_configured_capacity": change.previous_configured_capacity,
        "scanned_slots": change.application.scanned,
        "changed_slots": change.application.changed,
        "promoted_waiters": change.application.promoted,
    }


@console_ns.route("/campus/admin/knowledge-limits")
class CampusAdminKnowledgeLimitApi(Resource):
    @console_ns.response(200, "Knowledge limit setting", console_ns.models[KnowledgeLimitSettingResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return dump_response(KnowledgeLimitSettingResponse, _knowledge_limit_payload(knowledge_limits().state()))

    @console_ns.expect(console_ns.models[KnowledgeLimitSettingPayload.__name__])
    @console_ns.response(200, "Knowledge limits changed", console_ns.models[KnowledgeLimitSettingResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = KnowledgeLimitSettingPayload.model_validate(console_ns.payload or {})
        try:
            state = knowledge_limits().set_limits(
                max_datasets_per_workspace=payload.max_datasets_per_workspace,
                max_documents_per_dataset=payload.max_documents_per_dataset,
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(KnowledgeLimitSettingResponse, _knowledge_limit_payload(state))

    @console_ns.response(
        200, "Knowledge limits restored to the platform defaults",
        console_ns.models[KnowledgeLimitSettingResponse.__name__],
    )
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return dump_response(
            KnowledgeLimitSettingResponse,
            _knowledge_limit_payload(knowledge_limits().restore_default(actor_account_id=current_user.id)),
        )


def _knowledge_limit_payload(state) -> dict[str, object]:
    return {
        "max_datasets_per_workspace": state.max_datasets_per_workspace,
        "max_documents_per_dataset": state.max_documents_per_dataset,
        "platform_max_datasets_per_workspace": state.platform_max_datasets_per_workspace,
        "platform_max_documents_per_dataset": state.platform_max_documents_per_dataset,
        "configured_max_datasets_per_workspace": state.configured_max_datasets_per_workspace,
        "configured_max_documents_per_dataset": state.configured_max_documents_per_dataset,
        "is_default": state.is_default,
    }


@console_ns.route("/campus/admin/default-allowance")
class CampusAdminDefaultAllowanceApi(Resource):
    @console_ns.response(200, "Default allowance setting", console_ns.models[DefaultAllowanceResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return dump_response(DefaultAllowanceResponse, _default_allowance_payload(default_allowance().state()))

    @console_ns.expect(console_ns.models[DefaultAllowancePayload.__name__])
    @console_ns.response(200, "Default allowance changed", console_ns.models[DefaultAllowanceChangeResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = DefaultAllowancePayload.model_validate(console_ns.payload or {})
        try:
            change = default_allowance().set_default(
                payload.default_allowance_usd,
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(DefaultAllowanceChangeResponse, _default_allowance_change_payload(change))

    @console_ns.response(
        200, "Default allowance restored to the platform default",
        console_ns.models[DefaultAllowanceChangeResponse.__name__],
    )
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        change = default_allowance().restore_default(actor_account_id=current_user.id)
        return dump_response(DefaultAllowanceChangeResponse, _default_allowance_change_payload(change))


def _default_allowance_payload(state) -> dict[str, object]:
    return {
        "default_allowance_usd": state.default_allowance_usd,
        "platform_default_usd": state.platform_default_usd,
        "configured_default_allowance_usd": state.configured_default_allowance_usd,
        "is_default": state.is_default,
    }


def _default_allowance_change_payload(change) -> dict[str, object]:
    return {
        **_default_allowance_payload(change.state),
        "previous_configured_default_allowance_usd": change.previous_configured_default_allowance_usd,
        "scanned_students": change.application.scanned,
        "changed_students": change.application.changed,
    }


@console_ns.route("/campus/admin/portal-login")
class CampusAdminPortalLoginApi(Resource):
    @console_ns.response(200, "Portal login page state", console_ns.models[PortalLoginStateResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        return dump_response(PortalLoginStateResponse, _portal_login_state_payload(portal_login_pages().state()))

    @console_ns.response(201, "Portal login page uploaded", console_ns.models[PortalLoginStateResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise BadRequest("Login page HTML file is required")
        if not upload.filename.lower().endswith((".html", ".htm")):
            raise BadRequest("Login page must use the .html format")
        try:
            service = portal_login_pages()
            service.upload(
                filename=upload.filename,
                content=upload.stream.read(MAX_PORTAL_LOGIN_BYTES + 1),
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(PortalLoginStateResponse, _portal_login_state_payload(service.state())), 201

    @console_ns.response(200, "Portal login page deactivated", console_ns.models[PortalLoginStateResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        service = portal_login_pages()
        service.deactivate(actor_account_id=current_user.id)
        return dump_response(PortalLoginStateResponse, _portal_login_state_payload(service.state()))


@console_ns.route("/campus/admin/portal-login/pages/<string:page_id>/activate")
class CampusAdminPortalLoginActivateApi(Resource):
    @console_ns.response(200, "Portal login page activated", console_ns.models[PortalLoginStateResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account, page_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        service = portal_login_pages()
        try:
            service.activate(page_id, actor_account_id=current_user.id)
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(PortalLoginStateResponse, _portal_login_state_payload(service.state()))


@console_ns.route("/campus/admin/portal-login/pages/<string:page_id>")
class CampusAdminPortalLoginPageApi(Resource):
    @console_ns.response(200, "Portal login page deleted", console_ns.models[PortalLoginStateResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account, page_id: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        service = portal_login_pages()
        try:
            service.delete(page_id, actor_account_id=current_user.id)
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(PortalLoginStateResponse, _portal_login_state_payload(service.state()))


def _portal_login_page_payload(service, page) -> dict[str, object]:
    report = service.validation_report(page)
    return {
        "id": page.id,
        "filename": page.filename,
        "size_bytes": page.size_bytes,
        "digest": page.digest,
        "is_active": page.is_active,
        "created_at": page.created_at,
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", []),
        "checks": report.get("checks", []),
    }


def _portal_login_state_payload(state) -> dict[str, object]:
    service = portal_login_pages()
    return {
        "active_id": state.active.id if state.active else None,
        "active_filename": state.active.filename if state.active else None,
        "pages": [_portal_login_page_payload(service, page) for page in state.pages],
    }


# The uploaded login page lives under the portal origin, so media serves it


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
        # One warm-up task per student so the roster's workspaces are built in
        # parallel and the import returns immediately; sign-in repeats this work
        # for anyone the warm up has not reached. See the task's docstring.
        for student in payload.students:
            provision_student_workspace_task.delay(student.student_number)
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
                    }
                    for row in rows
                ]
            },
        )


@console_ns.route("/campus/admin/students/<string:student_number>/password")
class CampusAdminStudentPasswordApi(Resource):
    @console_ns.response(200, "Password reset", console_ns.models[StudentInitialPasswordResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        """Reset one password to the initial password the roster would hand out.

        The administrator types nothing: the initial password is derived from the
        student's own name and number (see `derive_initial_password`), and the
        response carries it so the reset can be passed on. The student can change
        it afterwards from the reservation centre.
        """
        require_campus_enabled()
        require_admin(current_user)
        try:
            student = student_service().get_student(student_number)
            initial_password = derive_initial_password(student.display_name, student.student_number)
            credential_service().set_password(
                student.student_number,
                initial_password,
                now=datetime.now(UTC),
                actor_account_id=current_user.id,
            )
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(
            StudentInitialPasswordResponse,
            {"student_number": student.student_number, "password": initial_password},
        )


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


@console_ns.route("/campus/admin/students/<string:student_number>")
class CampusAdminStudentDeleteApi(Resource):
    @console_ns.response(200, "Student soft-deleted", console_ns.models[StudentResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def delete(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            student = student_service().soft_delete(student_number, actor_account_id=current_user.id)
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentResponse, student)


@console_ns.route("/campus/admin/students/<string:student_number>/restore")
class CampusAdminStudentRestoreApi(Resource):
    @console_ns.response(200, "Student restored", console_ns.models[StudentResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        try:
            student = student_service().restore(student_number, actor_account_id=current_user.id)
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentResponse, student)


@console_ns.route("/campus/admin/students/<string:student_number>/name")
class CampusAdminStudentRenameApi(Resource):
    @console_ns.expect(console_ns.models[StudentRenamePayload.__name__])
    @console_ns.response(200, "Student renamed", console_ns.models[StudentResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def put(self, current_user: Account, student_number: str) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        payload = StudentRenamePayload.model_validate(console_ns.payload or {})
        try:
            student = student_service().rename(
                student_number,
                display_name=payload.display_name,
                actor_account_id=current_user.id,
            )
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentResponse, student)


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _usage_report(current_user: Account):
    require_campus_enabled()
    require_admin(current_user)
    granularity = parse_granularity(request.args.get("granularity"))
    now = datetime.now(UTC)
    return usage_reports().report(granularity, now=now), granularity, now


@console_ns.route("/campus/admin/usage-report")
class CampusAdminUsageReportApi(Resource):
    """A printable html report of every token's usage and billing."""

    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        report, _, now = _usage_report(current_user)
        response = make_response(render_usage_report_html(report, now=now))
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        # Usage figures are per-request and never worth caching.
        response.headers["Cache-Control"] = "private, no-store"
        return response


@console_ns.route("/campus/admin/usage-report.xlsx")
class CampusAdminUsageReportExportApi(Resource):
    """The same three tables as a workbook the administrator can keep."""

    @setup_required
    @login_required
    @with_current_user
    def get(self, current_user: Account) -> ResponseReturnValue:
        report, granularity, _ = _usage_report(current_user)
        response = make_response(render_usage_report_xlsx(report))
        response.headers["Content-Type"] = XLSX_MEDIA_TYPE
        response.headers["Content-Disposition"] = f'attachment; filename="token-usage-{granularity}.xlsx"'
        response.headers["Cache-Control"] = "private, no-store"
        return response


@console_ns.route("/campus/admin/students/purge")
class CampusAdminStudentPurgeApi(Resource):
    """Irreversibly purge students soft-deleted longer ago than the retention window."""

    @console_ns.response(200, "Retention sweep finished", console_ns.models[RetentionPurgeResponse.__name__])
    @setup_required
    @login_required
    @with_current_user
    def post(self, current_user: Account) -> ResponseReturnValue:
        require_campus_enabled()
        require_admin(current_user)
        result = student_retention().purge(now=datetime.now(UTC), actor_account_id=current_user.id)
        return dump_response(
            RetentionPurgeResponse,
            {"purged": list(result.purged), "failed": [list(item) for item in result.failed]},
        )


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
        service = allowance_service()
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
        "content_url": _manual_view_url(chapter.id),
        "position": chapter.position,
        "status": chapter.status,
    }


def _manual_view_url(chapter_id: str) -> str:
    hostname = request.host.split(":", 1)[0]
    return (
        f"{request.scheme}://{hostname}:{dify_config.CAMPUS_MANUAL_PUBLIC_PORT}"
        f"/console/api/campus/lab-manuals/documents/{quote(chapter_id)}/view"
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
