"""Contract for the platform default allowance endpoint.

The service tests cover what a change does to the rows; what these check is the
wiring no unit test sees: that the route exists once, that only a named
administrator can move it, and that new students take the configured default
rather than the raw environment value.
"""

import inspect

import pytest
from pydantic import ValidationError

from controllers.console import campus_admin, campus_dependencies, console_ns
from controllers.console.campus_schemas import DefaultAllowancePayload


def test_the_default_allowance_endpoint_is_registered_once() -> None:
    routes = [rule for route in console_ns.resources for rule in route.urls if "default-allowance" in rule]

    assert set(routes) == {"/campus/admin/default-allowance"}


def test_the_endpoint_reads_writes_and_restores() -> None:
    for handler in ("get", "put", "delete"):
        assert hasattr(campus_admin.CampusAdminDefaultAllowanceApi, handler), f"{handler} must be implemented"


def test_only_a_named_administrator_can_move_the_default() -> None:
    source = inspect.getsource(campus_admin.CampusAdminDefaultAllowanceApi)

    assert source.count("require_admin(current_user)") == 3
    assert source.count("@login_required") == 3


def test_new_students_take_the_configured_default_not_the_environment_value() -> None:
    source = inspect.getsource(campus_dependencies.student_service)

    assert "default_allowance().effective_default()" in source
    assert "CAMPUS_DEFAULT_ALLOWANCE_USD" not in source


def test_the_payload_takes_an_amount_from_zero_up() -> None:
    assert DefaultAllowancePayload.model_validate({"default_allowance_usd": 0}).default_allowance_usd == 0
    assert DefaultAllowancePayload.model_validate({"default_allowance_usd": "12.5"}).default_allowance_usd == 12.5

    with pytest.raises(ValidationError):
        DefaultAllowancePayload.model_validate({"default_allowance_usd": -1})
    with pytest.raises(ValidationError):
        DefaultAllowancePayload.model_validate({"default_allowance_usd": 10, "ignored": 1})
