from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

type ModelApiProtocol = Literal["responses", "chat"]
type ModelCredentialScope = Literal["provider", "model"]


class CampusConfig(BaseSettings):
    CAMPUS_ENABLED: bool = Field(default=False, description="Enable the Campus teaching-platform backend")
    CAMPUS_VIRTUAL_IDENTITY_ENABLED: bool = Field(
        default=False, description="Enable development-only virtual student authentication"
    )
    CAMPUS_VIRTUAL_IDENTITIES_JSON: SecretStr | None = Field(
        default=None, description="Development-only virtual identity records"
    )
    CAMPUS_BOOTSTRAP_ADMIN_ACCOUNT_IDS: str = Field(
        default="", description="Comma-separated Dify account IDs allowed to bootstrap Campus administration"
    )
    CAMPUS_SERVICE_PRINCIPAL_EMAIL: str | None = Field(
        default=None, description="Hidden Dify owner account for student workspaces"
    )
    CAMPUS_DEFAULT_ALLOWANCE_USD: Decimal = Field(
        default=Decimal(20), ge=Decimal(0), description="Model allowance granted to each new student, in US dollars"
    )
    CAMPUS_RESERVATION_CAPACITY: int = Field(default=500, ge=1)
    CAMPUS_BOOKING_DAYS: int = Field(default=7, ge=1, le=31)
    CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU: float = Field(default=1.0, gt=0, le=10)
    CAMPUS_PORTAL_SESSION_TTL_HOURS: int = Field(default=12, ge=1, le=168)
    CAMPUS_PORTAL_COOKIE_NAME: str = Field(default="campus_portal_session", min_length=1, max_length=64)
    CAMPUS_PORTAL_COOKIE_SECURE: bool = Field(default=True)
    CAMPUS_MANUAL_PUBLIC_PORT: int = Field(default=18083, ge=1, le=65535)

    CAMPUS_NEWAPI_BASE_URL: str | None = Field(default=None)
    CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN: SecretStr | None = Field(default=None)
    CAMPUS_NEWAPI_ADMIN_USER_ID: int = Field(default=1, ge=1)
    CAMPUS_NEWAPI_GROUP: str = Field(default="campus", min_length=1, max_length=64)
    CAMPUS_NEWAPI_MODEL_LIMITS: str = Field(default="")
    CAMPUS_NEWAPI_QUOTA_UNITS_PER_USD: int = Field(
        default=500_000,
        ge=1,
        description="Gateway quota units per US dollar; must match the gateway's own QuotaPerUnit",
    )

    CAMPUS_MODEL_PROVIDER: str = Field(default="langgenius/openai_api_compatible/openai_api_compatible")
    CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER: str = Field(default="")
    CAMPUS_MODEL_PROVIDER_PLUGIN_PACKAGE_PATH: str = Field(
        default="",
        description="Local .difypkg for the pinned provider plugin; empty falls back to the public Marketplace",
    )
    CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME: str = Field(default="Campus managed", min_length=1, max_length=30)
    CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE: ModelCredentialScope = "model"
    CAMPUS_MODEL_PROVIDER_API_KEY_FIELD: str = Field(default="api_key", min_length=1)
    CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD: str = Field(default="endpoint_url", min_length=1)
    CAMPUS_MODEL_PROVIDER_BASE_URL: str = Field(default="http://model-gateway:3000/v1", min_length=1)
    CAMPUS_MODEL_PROVIDER_MODELS: str = Field(
        default=(
            "llm:deepseek-v4-flash,llm:deepseek-v4-flash-0817,llm:glm-5.3-flash,"
            "text-embedding:bge-m3,rerank:bge-reranker-v2-m3"
        ),
        min_length=1,
        description=(
            "Comma-separated type:name gateway models exposed in every student workspace; "
            "every name must be priced in the gateway and at least one must be an llm"
        ),
    )
    CAMPUS_MODEL_PROVIDER_API_PROTOCOL: ModelApiProtocol = "chat"
