"""Read model for platform-administrator student inspection.

The list endpoint stays inexpensive and identity-only. This detail query joins
the student's opaque workspace identifier with the redacted gateway allowance
summary without ever returning a gateway user, token, channel, or price rule.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusGatewayBinding, CampusWorkspaceBinding
from services.campus.allowance_service import AllowanceService
from services.campus.domain import ModelGateway, StudentAdministrationDetail
from services.campus.student_service import StudentAdministrationService


class CampusAdministrationQueryService:
    _session: Session
    _students: StudentAdministrationService
    _allowances: AllowanceService

    def __init__(
        self,
        *,
        session: Session,
        students: StudentAdministrationService,
        gateway: ModelGateway,
        quota_units_per_usd: int,
    ) -> None:
        self._session = session
        self._students = students
        self._allowances = AllowanceService(
            session=session,
            gateway=gateway,
            quota_units_per_usd=quota_units_per_usd,
        )

    def get_student_detail(self, student_number: str) -> StudentAdministrationDetail:
        """Return identity, workspace ID, and redacted allowance when provisioned."""
        student = self._students.get_student(student_number)
        workspace_id = self._session.scalar(
            select(CampusWorkspaceBinding.dify_tenant_id).where(CampusWorkspaceBinding.student_id == student.id)
        )
        gateway_token_id = self._session.scalar(
            select(CampusGatewayBinding.gateway_token_id).where(CampusGatewayBinding.student_id == student.id)
        )
        allowance = self._allowances.get_summary(student.id) if gateway_token_id is not None else None
        return StudentAdministrationDetail(
            id=student.id,
            student_number=student.student_number,
            display_name=student.display_name,
            cohort=student.cohort,
            status=student.status,
            created_at=student.created_at,
            deleted_at=student.deleted_at,
            workspace_id=workspace_id,
            allowance=allowance,
        )
