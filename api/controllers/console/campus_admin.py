"""Named-administrator resources for roster, lifecycle, and allowance operations."""

from flask import Response, request
from flask.typing import ResponseReturnValue
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from configs import dify_config
from controllers.common.schema import query_params_from_model
from controllers.console import console_ns
from controllers.console.campus_dependencies import (
    admin_service,
    newapi_client,
    require_admin,
    require_campus_enabled,
    student_service,
)
from controllers.console.campus_schemas import (
    AdministratorPayload,
    AllowanceAdjustmentPayload,
    AllowanceResponse,
    ResultResponse,
    RosterSyncResponse,
    StudentDetailResponse,
    StudentListQuery,
    StudentListResponse,
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
    StudentNotFoundError,
)


def _allowance_response(summary: AllowanceSummary) -> dict[str, object]:
    return dump_response(AllowanceResponse, summary)


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
        return dump_response(StudentListResponse, {"data": students})


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
                quota_units_per_yuan=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_YUAN,
            ).get_student_detail(student_number)
        except StudentNotFoundError as error:
            raise NotFound("Student not found") from error
        return dump_response(StudentDetailResponse, detail)


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
                [StudentIdentity(**student.model_dump()) for student in payload.students],
                actor_account_id=current_user.id,
            )
        except CampusValidationError as error:
            raise BadRequest(str(error)) from error
        return dump_response(RosterSyncResponse, result)


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
            quota_units_per_yuan=dify_config.CAMPUS_NEWAPI_QUOTA_UNITS_PER_YUAN,
        )
        try:
            summary = service.adjust(
                student.id,
                delta_yuan=payload.delta_yuan,
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
