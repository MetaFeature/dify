import pytest
from sqlalchemy.orm import Session

from models.account import Account
from models.campus import CampusAdministrator, CampusAuditEvent
from services.campus import administrator_service
from services.campus.administrator_service import AdministratorService
from services.campus.errors import CampusAdministratorRequiredError, CampusConflictError


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [Account.__table__, CampusAdministrator.__table__, CampusAuditEvent.__table__]
    CampusAdministrator.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Account(name="First Admin", email="first@example.invalid"),
                Account(name="Second Admin", email="second@example.invalid"),
            ]
        )
        session.commit()
        yield session


def test_bootstrap_list_can_seed_multiple_platform_administrators(campus_session: Session):
    first_account, second_account = campus_session.query(Account).order_by(Account.name).all()
    service = AdministratorService(
        session=campus_session,
        bootstrap_account_ids=(first_account.id, second_account.id),
    )

    first = service.require_admin(first_account.id, display_name=first_account.name)
    second = service.require_admin(second_account.id, display_name=second_account.name)

    assert first.active is True
    assert second.active is True
    assert campus_session.query(CampusAdministrator).count() == 2


def test_admin_can_add_and_revoke_another_admin_with_audit(campus_session: Session):
    first_account, second_account = campus_session.query(Account).order_by(Account.name).all()
    service = AdministratorService(session=campus_session, bootstrap_account_ids=(first_account.id,))
    service.require_admin(first_account.id, display_name=first_account.name)

    service.add_admin(second_account.id, actor_account_id=first_account.id)
    service.revoke_admin(second_account.id, actor_account_id=first_account.id)

    with pytest.raises(CampusAdministratorRequiredError):
        service.require_admin(second_account.id, display_name=second_account.name)
    actions = [event.action for event in campus_session.query(CampusAuditEvent).all()]
    assert actions == ["administrator.bootstrapped", "administrator.added", "administrator.revoked"]


def test_non_admin_fails_closed(campus_session: Session):
    service = AdministratorService(session=campus_session, bootstrap_account_ids=())

    with pytest.raises(CampusAdministratorRequiredError):
        service.require_admin("not-an-admin", display_name="Unknown")


def test_admin_cannot_revoke_own_access(campus_session: Session):
    first_account = campus_session.query(Account).order_by(Account.name).first()
    assert first_account is not None
    service = AdministratorService(session=campus_session, bootstrap_account_ids=(first_account.id,))
    service.require_admin(first_account.id, display_name=first_account.name)

    with pytest.raises(CampusConflictError, match="own access"):
        service.revoke_admin(first_account.id, actor_account_id=first_account.id)


def test_admin_can_create_and_authorize_a_named_account(
    campus_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    creator = campus_session.query(Account).order_by(Account.name).first()
    assert creator is not None
    tenant_calls: list[str] = []

    def create_account(**kwargs) -> Account:
        account = Account(name=kwargs["name"], email=kwargs["email"])
        campus_session.add(account)
        campus_session.commit()
        return account

    monkeypatch.setattr(administrator_service.AccountService, "create_account", create_account)
    monkeypatch.setattr(
        administrator_service.TenantService,
        "create_owner_tenant_if_not_exist",
        lambda account, **_: tenant_calls.append(account.id),
    )
    service = AdministratorService(session=campus_session, bootstrap_account_ids=(creator.id,))
    service.require_admin(creator.id, display_name=creator.name)

    created = service.create_admin_account(
        email="NEW@example.invalid",
        name="New Admin",
        password="Temporary123",
        actor_account_id=creator.id,
    )

    account = campus_session.get(Account, created.account_id)
    assert account is not None
    assert account.email == "new@example.invalid"
    assert tenant_calls == [account.id]
    assert [event.action for event in campus_session.query(CampusAuditEvent).all()][-2:] == [
        "administrator.added",
        "administrator.account_created",
    ]
