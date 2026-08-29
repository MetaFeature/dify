"""Named-administrator resources for roster, lifecycle, slot, and allowance operations."""

from datetime import UTC, datetime

from flask import Response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    credential_service,
    newapi_client,
    require_admin,
    require_campus_enabled,
    reservation_service,
    student_service,
    virtual_student_numbers,
)
from controllers.console.campus_schemas import (
    AdministratorListResponse,
    AdministratorPayload,
    AdminSlotListResponse,
    AllowanceAdjustmentPayload,
    AllowanceResponse,
    ResultResponse,
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
from services.campus.administration_query_service import CampusAdministrationQueryService
from services.campus.allowance_service import AllowanceService
from services.campus.domain import AllowanceSummary, StudentIdentity
from services.campus.errors import (
    CampusAccountNotFoundError,
    CampusAdministratorRequiredError,
    CampusConflictError,
    CampusValidationError,
    GatewayBindingNotFoundError,
    ReservationWindowError,
    StudentNotFoundError,
)


def _allowance_response(summary: AllowanceSummary) -> dict[str, object]:
    return dump_response(AllowanceResponse, summary)


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
