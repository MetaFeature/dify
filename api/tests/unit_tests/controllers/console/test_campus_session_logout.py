from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from configs import dify_config
from controllers.console.campus import CampusSessionLogoutApi


class RecordingPortalSessions:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def revoke(self, raw_token: str, **_kwargs: object) -> bool:
        self.calls.append(raw_token)
        return True


@pytest.fixture
def app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    return flask_app


def test_student_logout_revokes_portal_session_and_clears_all_managed_cookies(app: Flask) -> None:
    portal_sessions = RecordingPortalSessions()
    account = SimpleNamespace(id="account-1")
    cookie_name = dify_config.CAMPUS_PORTAL_COOKIE_NAME

    with (
        app.test_request_context(
            "/console/api/campus/session/logout",
            method="POST",
            headers={"Cookie": f"{cookie_name}=raw-portal-token"},
        ),
        patch("controllers.console.campus.require_campus_enabled"),
        patch("controllers.console.campus.portal_sessions", return_value=portal_sessions),
        patch("controllers.console.campus.current_account_with_tenant_optional", return_value=(account, "tenant-1")),
        patch("controllers.console.campus.AccountService.logout") as logout,
        patch("controllers.console.campus.flask_login.logout_user") as logout_user,
    ):
        response = CampusSessionLogoutApi.post.__wrapped__(CampusSessionLogoutApi())

    assert response.status_code == 200
    assert portal_sessions.calls == ["raw-portal-token"]
    logout.assert_called_once_with(account=account)
    logout_user.assert_called_once_with()

    cleared = response.headers.getlist("Set-Cookie")
    assert any(header.startswith(f"{cookie_name}=;") for header in cleared)
    assert sum("Max-Age=0" in header or "Expires=Thu, 01 Jan 1970" in header for header in cleared) >= 4
