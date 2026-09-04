import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.orm import Session

from controllers.console.campus import CampusPasswordChangeApi, InvalidNewPasswordHTTPError
from models.campus import CampusPortalSession, CampusStudent, CampusStudentCredential, StudentStatus
from services.campus.credential_service import StudentCredentialService
from services.campus.identity_source import VirtualIdentitySource


@pytest.fixture
def app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [CampusStudent.__table__, CampusStudentCredential.__table__, CampusPortalSession.__table__]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        session.add(
            CampusStudent(
                student_number="20260001",
                display_name="Student One",
                status=StudentStatus.ACTIVE,
                initial_allowance_usd=Decimal(0),
            )
        )
        session.commit()
        yield session


def legacy_identity_source() -> VirtualIdentitySource:
    return VirtualIdentitySource(
        json.dumps(
            [
                {
                    "student_number": "20260001",
                    "display_name": "Student One",
                    "login_code": "LegacyPass1234",
                }
            ]
        )
    )


def student(session: Session) -> CampusStudent:
    stored = session.scalar(select(CampusStudent))
    assert stored is not None
    return stored


def test_legacy_student_can_change_password_through_the_public_controller(
    app: Flask,
    campus_session: Session,
) -> None:
    stored_student = student(campus_session)
    credentials = StudentCredentialService(
        session=campus_session,
        fallback_identity_source=legacy_identity_source(),
    )
    with (
        app.test_request_context(
            "/console/api/campus/password",
            method="POST",
            json={"current_password": "LegacyPass1234", "new_password": "NewPass1234"},
        ),
        patch("controllers.console.campus.require_campus_enabled"),
        patch("controllers.console.campus.portal_student", return_value=stored_student),
        patch("controllers.console.campus.credential_service", return_value=credentials),
    ):
        response = CampusPasswordChangeApi.post.__wrapped__(CampusPasswordChangeApi())

    assert response == {"result": "success"}
    assert credentials.authenticate("20260001", "NewPass1234").student_number == "20260001"


def test_password_strength_failure_has_a_dedicated_public_error_code(
    app: Flask,
    campus_session: Session,
) -> None:
    stored_student = student(campus_session)
    credentials = StudentCredentialService(session=campus_session)
    credentials.set_password("20260001", "OldPass1234", now=datetime(2026, 9, 4, tzinfo=UTC))
    with (
        app.test_request_context(
            "/console/api/campus/password",
            method="POST",
            json={"current_password": "OldPass1234", "new_password": "short"},
        ),
        patch("controllers.console.campus.require_campus_enabled"),
        patch("controllers.console.campus.portal_student", return_value=stored_student),
        patch("controllers.console.campus.credential_service", return_value=credentials),
        pytest.raises(InvalidNewPasswordHTTPError) as caught,
    ):
        CampusPasswordChangeApi.post.__wrapped__(CampusPasswordChangeApi())

    assert caught.value.data == {
        "code": "invalid_new_password",
        "message": "New password does not meet the strength requirements",
        "status": 400,
    }
