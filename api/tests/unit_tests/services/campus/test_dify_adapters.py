from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models.account import Tenant, TenantAccountJoin, TenantAccountRole
from services.campus.dify_adapters import DifyWorkspaceProvisioner
from services.campus.errors import CampusProvisioningError


@pytest.fixture
def tenant_session(sqlite_engine) -> Session:
    tables = [Tenant.__table__, TenantAccountJoin.__table__]
    Tenant.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


def test_existing_workspace_rejects_any_additional_human_member(tenant_session: Session) -> None:
    service_principal_id = str(uuid4())
    student_id = str(uuid4())
    unexpected_member_id = str(uuid4())
    tenant = Tenant(name="Student workspace")
    tenant_session.add(tenant)
    tenant_session.flush()
    tenant_session.add_all(
        [
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=service_principal_id,
                role=TenantAccountRole.OWNER,
            ),
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=student_id,
                role=TenantAccountRole.EDITOR,
            ),
            TenantAccountJoin(
                tenant_id=tenant.id,
                account_id=unexpected_member_id,
                role=TenantAccountRole.NORMAL,
            ),
        ]
    )
    tenant_session.commit()
    provisioner = DifyWorkspaceProvisioner(
        session=tenant_session,
        service_principal_email="campus-owner@example.invalid",
    )

    with pytest.raises(CampusProvisioningError, match="unexpected human members"):
        provisioner._existing_workspace(student_id, service_principal_id)
