from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from controllers.console.campus_schemas import (
    AllowanceAdjustmentPayload,
    StudentIdentityPayload,
    VirtualLoginPayload,
)


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
