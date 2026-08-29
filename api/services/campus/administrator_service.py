import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.account import Account
from models.campus import CampusAdministrator, CampusAuditEvent
from services.campus.errors import CampusAccountNotFoundError, CampusAdministratorRequiredError, CampusConflictError


class AdministratorService:
    """Manages named Campus administrators and attributable lifecycle audit."""

    _session: Session
    _bootstrap_account_ids: frozenset[str]

    def __init__(self, *, session: Session, bootstrap_account_ids: tuple[str, ...]) -> None:
        self._session = session
        self._bootstrap_account_ids = frozenset(bootstrap_account_ids)

    def require_admin(self, account_id: str, *, display_name: str) -> CampusAdministrator:
        """Authorize an active administrator, bootstrapping configured IDs with audit once."""
        administrator = self._session.scalar(
            select(CampusAdministrator).where(CampusAdministrator.account_id == account_id)
        )
        if administrator is not None and administrator.active:
            return administrator
        if administrator is None and account_id in self._bootstrap_account_ids:
            administrator = CampusAdministrator(
                account_id=account_id,
                display_name=display_name,
                active=True,
                created_by_account_id=account_id,
            )
            self._session.add(administrator)
            self._session.add(
                CampusAuditEvent(
                    actor_account_id=account_id,
                    action="administrator.bootstrapped",
                    target_type="administrator",
                    target_id=account_id,
                    details_json="{}",
                )
            )
            self._session.commit()
            return administrator
        raise CampusAdministratorRequiredError(account_id)

    def add_admin(self, account_id: str, *, actor_account_id: str) -> CampusAdministrator:
        """Activate a real Dify account as administrator, append audit, and commit."""
        account = self._session.get(Account, account_id)
        if account is None:
            raise CampusAccountNotFoundError(account_id)
        administrator = self._session.scalar(
            select(CampusAdministrator).where(CampusAdministrator.account_id == account_id).with_for_update()
        )
        if administrator is None:
            administrator = CampusAdministrator(
                account_id=account_id,
                display_name=account.name,
                active=True,
                created_by_account_id=actor_account_id,
            )
            self._session.add(administrator)
        else:
            administrator.display_name = account.name
            administrator.active = True
        self._audit("administrator.added", account_id, actor_account_id, {"display_name": account.name})
        self._session.commit()
        return administrator

    def revoke_admin(self, account_id: str, *, actor_account_id: str) -> None:
        """Revoke another administrator, append audit, and commit the transition."""
        if account_id == actor_account_id:
            raise CampusConflictError("an administrator cannot revoke their own access")
        administrator = self._session.scalar(
            select(CampusAdministrator).where(CampusAdministrator.account_id == account_id).with_for_update()
        )
        if administrator is None:
            raise CampusAdministratorRequiredError(account_id)
        administrator.active = False
        self._audit("administrator.revoked", account_id, actor_account_id, {})
        self._session.commit()

    def list_active(self) -> list[CampusAdministrator]:
        """Return every active named administrator, ordered by display name."""
        return list(
            self._session.scalars(
                select(CampusAdministrator).where(CampusAdministrator.active).order_by(CampusAdministrator.display_name)
            ).all()
        )

    def _audit(self, action: str, target_id: str, actor_account_id: str, details: dict[str, str]) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="administrator",
                target_id=target_id,
                details_json=json.dumps(details, separators=(",", ":")),
            )
        )
