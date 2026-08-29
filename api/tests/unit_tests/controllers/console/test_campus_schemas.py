from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from controllers.console.campus_schemas import (
    AllowanceAdjustmentPayload,
    LabManualChapterPayload,
    LabManualChapterPositionPayload,
    LabManualChapterStatusPayload,
    StudentIdentityPayload,
    VirtualLoginPayload,
)
from models.campus import LabManualChapterStatus


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (StudentIdentityPayload, {"student_number": "   ", "display_name": "Student"}),
        (StudentIdentityPayload, {"student_number": "20260001", "display_name": "   "}),
        (VirtualLoginPayload, {"subject": "   ", "credential": "code"}),
        (
            AllowanceAdjustmentPayload,
            {"delta_usd": Decimal(1), "reason": "grant", "request_id": "   "},
        ),
    ],
)
def test_campus_request_schemas_reject_whitespace_only_identifiers(
    schema: type[BaseModel], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_lab_manual_chapter_payload_rejects_a_whitespace_title() -> None:
    with pytest.raises(ValidationError):
        LabManualChapterPayload.model_validate({"title": "   ", "body_html": "<p>x</p>"})


def test_lab_manual_chapter_payload_rejects_an_empty_body() -> None:
    with pytest.raises(ValidationError):
        LabManualChapterPayload.model_validate({"title": "装环境", "body_html": ""})


def test_lab_manual_chapter_payload_forbids_unknown_fields() -> None:
    # extra="forbid" is what stops a caller from smuggling in a status or a
    # position that only the service is allowed to decide.
    with pytest.raises(ValidationError):
        LabManualChapterPayload.model_validate({"title": "装环境", "body_html": "<p>x</p>", "status": "published"})


def test_lab_manual_chapter_position_must_be_a_real_place() -> None:
    assert LabManualChapterPositionPayload.model_validate({"position": 1}).position == 1
    for bad in ({"position": 0}, {"position": -1}, {"position": 100000}):
        with pytest.raises(ValidationError):
            LabManualChapterPositionPayload.model_validate(bad)


def test_lab_manual_chapter_status_payload_accepts_only_the_two_states() -> None:
    assert LabManualChapterStatusPayload.model_validate({"status": "published"}).status is (
        LabManualChapterStatus.PUBLISHED
    )
    with pytest.raises(ValidationError):
        LabManualChapterStatusPayload.model_validate({"status": "archived"})
