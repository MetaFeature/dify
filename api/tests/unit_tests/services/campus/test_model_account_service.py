from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import CampusGatewayBinding, CampusStudent, StudentStatus
from services.campus.domain import ManagedGatewayToken
from services.campus.errors import CampusProvisioningError
from services.campus.model_account_service import StudentModelAccountService


class FakeGateway:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}
        self.calls: list[tuple[str, str, str, int]] = []
        self.deleted: list[str] = []

    def create_managed_token(
        self,
        external_ref: str,
        student_number: str,
        student_name: str,
        allowance_quota: int,
    ) -> ManagedGatewayToken:
        self.calls.append((external_ref, student_number, student_name, allowance_quota))
        existing = self.tokens.get(external_ref)
        if existing is not None:
            return ManagedGatewayToken(token_id=existing, secret="existing-secret", created=False)
        token_id = str(len(self.tokens) + 1)
        self.tokens[external_ref] = token_id
        return ManagedGatewayToken(token_id=token_id, secret="new-secret", created=True)

    def update_managed_identity(self, token_id: str, student_number: str, student_name: str) -> None:
        raise AssertionError("create-or-get owns identity synchronization")

    def delete_managed_token(self, token_id: str) -> None:
        self.deleted.append(token_id)


@pytest.fixture
def model_account_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusGatewayBinding.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                CampusStudent(
                    student_number="20260001",
                    display_name="Student One",
                    status=StudentStatus.ACTIVE,
                    initial_allowance_usd=Decimal(20),
                ),
                CampusStudent(
                    student_number="20260002",
                    display_name="Student Two",
                    status=StudentStatus.SUSPENDED,
                    initial_allowance_usd=Decimal(12.5),
                ),
            ]
        )
        session.commit()
        yield session


def test_reconcile_preprovisions_every_roster_student_idempotently(model_account_session: Session) -> None:
    gateway = FakeGateway()
    service = StudentModelAccountService(
        session=model_account_session,
        gateway=gateway,
        quota_units_per_usd=100,
    )

    first = service.reconcile()
    second = service.reconcile()

    assert first.students == 2
    assert first.bindings_created == 2
    assert first.existing_bindings == 0
    assert second.students == 2
    assert second.bindings_created == 0
    assert second.existing_bindings == 2
    assert model_account_session.query(CampusGatewayBinding).count() == 2
    assert [(number, name, allowance) for _, number, name, allowance in gateway.calls[:2]] == [
        ("20260001", "Student One", 2_000),
        ("20260002", "Student Two", 1_250),
    ]


def test_reconcile_rejects_a_binding_to_a_different_gateway_token(model_account_session: Session) -> None:
    gateway = FakeGateway()
    first_student = model_account_session.query(CampusStudent).order_by(CampusStudent.student_number).first()
    assert first_student is not None
    model_account_session.add(CampusGatewayBinding(student_id=first_student.id, gateway_token_id="99"))
    model_account_session.commit()
    service = StudentModelAccountService(
        session=model_account_session,
        gateway=gateway,
        quota_units_per_usd=100,
    )

    with pytest.raises(CampusProvisioningError, match="different managed token"):
        service.reconcile(student_numbers=(first_student.student_number,))

    assert gateway.deleted == ["1"]
