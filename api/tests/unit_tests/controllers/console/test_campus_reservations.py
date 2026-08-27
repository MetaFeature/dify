from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.exceptions import TooManyRequests

from controllers.console.campus import CampusReservationListApi
from services.campus.errors import CurrentSlotLoadUnavailableError


class LoadRejectingReservationService:
    def reserve(self, *_args: object, **_kwargs: object) -> None:
        raise CurrentSlotLoadUnavailableError("private load detail")


@pytest.fixture
def app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    return flask_app


def test_current_slot_load_rejection_returns_retryable_public_error(app: Flask) -> None:
    with (
        app.test_request_context(
            "/console/api/campus/reservations",
            method="POST",
            json={"starts_at": "2026-08-11T02:00:00Z"},
        ),
        patch("controllers.console.campus.require_campus_enabled"),
        patch("controllers.console.campus.portal_student", return_value=SimpleNamespace(id="student-1")),
        patch(
            "controllers.console.campus.reservation_service",
            return_value=LoadRejectingReservationService(),
        ),
        pytest.raises(TooManyRequests) as caught,
    ):
        CampusReservationListApi.post.__wrapped__(CampusReservationListApi())

    assert caught.value.code == 429
    assert "private load detail" not in caught.value.description
