"""SSRF-protected client for the private Campus model-gateway management API.

Only this adapter sees gateway administrator credentials. Callers receive typed
domain summaries, never raw response dictionaries or gateway secrets except
during the one-time managed-token provisioning handoff.
"""

from collections.abc import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from core.helper import ssrf_proxy
from core.tools.errors import ToolSSRFError
from services.campus.domain import GatewayUsage, ManagedGatewayToken, ModelUsage
from services.campus.errors import ModelGatewayError

type Requester = Callable[..., httpx.Response]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Envelope(_StrictModel):
    success: bool
    message: str = ""
    data: object | None = None


class _ManagedTokenData(_StrictModel):
    token_id: int
    user_id: int
    key: str
    created: bool


class _ModelUsageData(_StrictModel):
    model: str
    quota: int = Field(ge=0)
    requests: int = Field(ge=0)


class _UsageData(_StrictModel):
    token_id: int
    remaining_quota: int = Field(ge=0)
    used_quota: int = Field(ge=0)
    by_model: list[_ModelUsageData] = Field(default_factory=list)


class NewApiClient:
    """Typed client for the isolated NewAPI Campus management contract."""

    _base_url: str
    _headers: dict[str, str]
    _group: str
    _model_limits: tuple[str, ...]
    _requester: Requester
    _timeout_seconds: float

    def __init__(
        self,
        *,
        base_url: str,
        admin_access_token: str,
        admin_user_id: int,
        group: str,
        model_limits: tuple[str, ...],
        requester: Requester | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if not admin_access_token:
            raise ValueError("admin_access_token is required")
        if admin_user_id <= 0:
            raise ValueError("admin_user_id must be positive")
        if not group.strip():
            raise ValueError("group is required")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": admin_access_token,
            "New-Api-User": str(admin_user_id),
        }
        self._group = group
        self._model_limits = model_limits
        self._requester = requester or ssrf_proxy.make_request
        self._timeout_seconds = timeout_seconds

    def create_managed_token(
        self,
        external_ref: str,
        student_number: str,
        student_name: str,
        allowance_quota: int,
    ) -> ManagedGatewayToken:
        data = self._request(
            "POST",
            "/api/campus/tokens",
            json={
                "external_ref": external_ref,
                "student_number": student_number,
                "student_name": student_name,
                "allowance_quota": allowance_quota,
                "group": self._group,
                "model_limits": list(self._model_limits),
            },
        )
        managed = _ManagedTokenData.model_validate(data)
        return ManagedGatewayToken(token_id=str(managed.token_id), secret=managed.key, created=managed.created)

    def update_managed_identity(self, token_id: str, student_number: str, student_name: str) -> None:
        self._request(
            "PATCH",
            f"/api/campus/tokens/{int(token_id)}/identity",
            json={"student_number": student_number, "student_name": student_name},
        )

    def delete_managed_token(self, token_id: str) -> None:
        try:
            response = self._requester(
                "DELETE",
                f"{self._base_url}/api/campus/tokens/{int(token_id)}",
                headers=self._headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except (httpx.HTTPError, ToolSSRFError, ValueError) as error:
            raise ModelGatewayError("model gateway transport failed") from error

    def get_usage(self, token_id: str) -> GatewayUsage:
        data = self._request("GET", f"/api/campus/tokens/{int(token_id)}/usage")
        return self._to_gateway_usage(_UsageData.model_validate(data))

    def adjust_quota(self, token_id: str, delta_quota: int, request_id: str) -> GatewayUsage:
        data = self._request(
            "POST",
            f"/api/campus/tokens/{int(token_id)}/quota-adjust",
            json={"delta_quota": delta_quota, "request_id": request_id},
        )
        return self._to_gateway_usage(_UsageData.model_validate(data))

    def _request(self, method: str, path: str, *, json: dict[str, object] | None = None) -> object | None:
        try:
            response = self._requester(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                json=json,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            envelope = _Envelope.model_validate(response.json())
        except (httpx.HTTPError, ToolSSRFError, ValueError) as error:
            raise ModelGatewayError("model gateway transport failed") from error
        if not envelope.success:
            raise ModelGatewayError(f"model gateway request failed: {envelope.message or 'unknown error'}")
        return envelope.data

    @staticmethod
    def _to_gateway_usage(usage: _UsageData) -> GatewayUsage:
        return GatewayUsage(
            remaining_quota=usage.remaining_quota,
            used_quota=usage.used_quota,
            by_model=tuple(
                ModelUsage(model=item.model, quota=item.quota, requests=item.requests) for item in usage.by_model
            ),
        )
