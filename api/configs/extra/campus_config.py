from decimal import Decimal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


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
    CAMPUS_DEFAULT_ALLOWANCE_YUAN: Decimal = Field(default=Decimal(20), ge=Decimal(0))
    CAMPUS_RESERVATION_CAPACITY: int = Field(default=500, ge=1)
    CAMPUS_BOOKING_DAYS: int = Field(default=7, ge=1, le=31)
    CAMPUS_PORTAL_SESSION_TTL_HOURS: int = Field(default=12, ge=1, le=168)
    CAMPUS_PORTAL_COOKIE_NAME: str = Field(default="campus_portal_session", min_length=1, max_length=64)
    CAMPUS_PORTAL_COOKIE_SECURE: bool = Field(default=True)

    CAMPUS_NEWAPI_BASE_URL: str | None = Field(default=None)
    CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN: SecretStr | None = Field(default=None)
    CAMPUS_NEWAPI_ADMIN_USER_ID: int = Field(default=1, ge=1)
    CAMPUS_NEWAPI_GROUP: str = Field(default="campus", min_length=1, max_length=64)
    CAMPUS_NEWAPI_MODEL_LIMITS: str = Field(default="")
    CAMPUS_NEWAPI_QUOTA_UNITS_PER_YUAN: int = Field(default=500_000, ge=1)

    CAMPUS_MODEL_PROVIDER: str = Field(default="langgenius/openai/openai")
    CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER: str = Field(default="")
    CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME: str = Field(default="Campus managed", min_length=1, max_length=30)
    CAMPUS_MODEL_PROVIDER_API_KEY_FIELD: str = Field(default="openai_api_key", min_length=1)
    CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD: str = Field(default="openai_api_base", min_length=1)
    CAMPUS_MODEL_PROVIDER_BASE_URL: str = Field(default="http://model-gateway:3000/v1", min_length=1)
