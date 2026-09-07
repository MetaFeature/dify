import json

import httpx
import pytest

from services.campus.errors import ModelGatewayError
from services.campus.newapi_client import NewApiClient


def test_create_managed_token_uses_internal_admin_contract_and_returns_secret():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["Authorization"]
        seen["user"] = request.headers["New-Api-User"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "success": True,
                "message": "",
                "data": {"token_id": 42, "user_id": 84, "key": "gateway-secret", "created": True},
            },
        )

    client = NewApiClient(
        base_url="http://newapi:3000",
        admin_access_token="admin-secret",
        admin_user_id=1,
        group="campus",
        model_limits=("text-model", "image-model"),
        requester=httpx.Client(transport=httpx.MockTransport(handler)).request,
    )

    managed = client.create_managed_token("student-uuid", "20260001", "Student One", 2_000)

    assert managed.token_id == "42"
    assert managed.secret == "gateway-secret"
    assert managed.created is True
    assert seen == {
        "path": "/api/campus/tokens",
        "authorization": "admin-secret",
        "user": "1",
        "payload": {
            "external_ref": "student-uuid",
            "student_number": "20260001",
            "student_name": "Student One",
            "allowance_quota": 2_000,
            "group": "campus",
            "model_limits": ["text-model", "image-model"],
        },
    }


def test_update_managed_identity_uses_token_scoped_admin_contract():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "message": "", "data": None})

    client = NewApiClient(
        base_url="http://newapi:3000",
        admin_access_token="admin-secret",
        admin_user_id=1,
        group="campus",
        model_limits=(),
        requester=httpx.Client(transport=httpx.MockTransport(handler)).request,
    )

    client.update_managed_identity("42", "20260001", "Student One")

    assert seen == {
        "method": "PATCH",
        "path": "/api/campus/tokens/42/identity",
        "payload": {"student_number": "20260001", "student_name": "Student One"},
    }


def test_usage_and_adjustment_map_only_public_metering_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/campus/tokens/42/usage"
        return httpx.Response(
            200,
            json={
                "success": True,
                "message": "",
                "data": {
                    "token_id": 42,
                    "remaining_quota": 2_000,
                    "used_quota": 3_000,
                    "by_model": [{"model": "text-model", "quota": 3_000, "requests": 4}],
                },
            },
        )

    client = NewApiClient(
        base_url="http://newapi:3000",
        admin_access_token="admin-secret",
        admin_user_id=1,
        group="campus",
        model_limits=(),
        requester=httpx.Client(transport=httpx.MockTransport(handler)).request,
    )

    usage = client.get_usage("42")

    assert usage.remaining_quota == 2_000
    assert usage.used_quota == 3_000
    assert usage.by_model[0].model == "text-model"
    assert not hasattr(usage, "key")
    assert not hasattr(usage, "channel")


def test_model_catalog_maps_only_typed_public_model_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/campus/models/catalog"
        return httpx.Response(
            200,
            json={
                "success": True,
                "message": "",
                "data": {
                    "contract_version": 1,
                    "revision": "a" * 64,
                    "models": [
                        {
                            "name": "qwen3.8-flash",
                            "model_type": "llm",
                            "endpoints": ["openai"],
                            "billing_mode": "tiered_expr",
                        },
                        {
                            "name": "bge-m3",
                            "model_type": "text-embedding",
                            "endpoints": ["embeddings"],
                            "billing_mode": "ratio",
                        },
                    ],
                    "excluded": [{"name": "unpriced-model", "reason": "unpriced"}],
                },
            },
        )

    client = NewApiClient(
        base_url="http://newapi:3000",
        admin_access_token="admin-secret",
        admin_user_id=1,
        group="campus",
        model_limits=(),
        requester=httpx.Client(transport=httpx.MockTransport(handler)).request,
    )

    catalog = client.get_model_catalog()

    assert [(model.model_type, model.name) for model in catalog] == [
        ("llm", "qwen3.8-flash"),
        ("text-embedding", "bge-m3"),
    ]
    assert not hasattr(catalog[0], "channel")
    assert catalog[0].endpoints == ("openai",)
    assert catalog[0].billing_mode == "tiered_expr"


def test_newapi_business_error_is_raised_even_when_http_status_is_200():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "quota exhausted"})

    client = NewApiClient(
        base_url="http://newapi:3000",
        admin_access_token="admin-secret",
        admin_user_id=1,
        group="campus",
        model_limits=(),
        requester=httpx.Client(transport=httpx.MockTransport(handler)).request,
    )

    with pytest.raises(ModelGatewayError, match="model gateway request failed: quota exhausted"):
        client.adjust_quota("42", -2_001, "adjustment-1")
