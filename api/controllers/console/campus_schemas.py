"""Strict request and redacted response schemas for the Campus console API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from controllers.common.schema import register_response_schema_models, register_schema_models
from controllers.console import console_ns
from fields.base import ResponseModel
from models.campus import StudentStatus


class CampusRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampusResponseModel(ResponseModel):
    model_config = ConfigDict(extra="forbid")


class VirtualLoginPayload(CampusRequestModel):
    subject: str = Field(min_length=1, max_length=64)
    credential: str = Field(min_length=1, max_length=255)

    @field_validator("subject", "credential")
    @classmethod
    def validate_non_whitespace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class StudentIdentityPayload(CampusRequestModel):
    student_number: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    cohort: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("student_number", "display_name")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized

    @field_validator("cohort")
    @classmethod
    def normalize_cohort(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None


class StudentRosterSyncPayload(CampusRequestModel):
    students: list[StudentIdentityPayload] = Field(min_length=1, max_length=5_000)


class StudentStatusPayload(CampusRequestModel):
    status: StudentStatus


class StudentCreatePayload(CampusRequestModel):
    student_number: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    cohort: str | None = Field(default=None, max_length=128)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("student_number", "display_name", "password")
    @classmethod
    def validate_create_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class StudentPasswordResetPayload(CampusRequestModel):
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class PortalPasswordChangePayload(CampusRequestModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class ReservationCreatePayload(CampusRequestModel):
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("starts_at must include a timezone")
        return value


class SlotListQuery(CampusRequestModel):
    day: date


class SlotCapacityPayload(CampusRequestModel):
    starts_at: datetime
    capacity: int = Field(ge=0)

    @field_validator("starts_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("starts_at must include a timezone")
        return value


class StudentListQuery(CampusRequestModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class AllowanceAdjustmentPayload(CampusRequestModel):
    delta_usd: Decimal
    reason: str = Field(min_length=1, max_length=500)
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("reason", "request_id")
    @classmethod
    def validate_adjustment_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class AdministratorPayload(CampusRequestModel):
    account_id: str = Field(min_length=1, max_length=64)

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("account_id cannot be whitespace-only")
        return normalized


class PortalLoginResponse(CampusResponseModel):
    student_id: str
    expires_at: datetime


class StudentResponse(CampusResponseModel):
    id: str
    student_number: str
    display_name: str
    cohort: str | None
    status: StudentStatus
    has_credential: bool | None = None
    virtual_identity: bool | None = None


class StudentListResponse(CampusResponseModel):
    data: list[StudentResponse]


class RosterSyncResponse(CampusResponseModel):
    created: int
    updated: int
    password_resets: int = 0


class AdministratorResponse(CampusResponseModel):
    account_id: str
    display_name: str


class AdministratorListResponse(CampusResponseModel):
    data: list[AdministratorResponse]


class ReservationResponse(CampusResponseModel):
    id: str
    status: str
    starts_at: datetime
    ends_at: datetime
    waitlist_position: int | None = None


class ReservationListResponse(CampusResponseModel):
    data: list[ReservationResponse]


class SlotResponse(CampusResponseModel):
    starts_at: datetime
    ends_at: datetime
    capacity: int
    confirmed: int
    waitlisted: int
    reservable: bool


class SlotListResponse(CampusResponseModel):
    data: list[SlotResponse]


class AdminSlotListResponse(CampusResponseModel):
    data: list[SlotResponse]
    server_now: datetime


class SlotCapacityResponse(CampusResponseModel):
    starts_at: datetime
    ends_at: datetime
    capacity: int
    previous_capacity: int
    confirmed: int
    waitlisted: int


class AccessDecisionResponse(CampusResponseModel):
    allowed: bool
    reservation_id: str | None = None
    ends_at: datetime | None = None
    server_now: datetime | None = None


class ModelUsageResponse(CampusResponseModel):
    model: str
    used_usd: Decimal
    requests: int


class AllowanceResponse(CampusResponseModel):
    remaining_usd: Decimal
    used_usd: Decimal
    total_usd: Decimal
    model_calls_enabled: bool
    by_model: list[ModelUsageResponse]


class StudentDetailResponse(StudentResponse):
    workspace_id: str | None
    allowance: AllowanceResponse | None


class ResultResponse(CampusResponseModel):
    result: str


register_schema_models(
    console_ns,
    VirtualLoginPayload,
    StudentRosterSyncPayload,
    StudentStatusPayload,
    StudentCreatePayload,
    StudentPasswordResetPayload,
    PortalPasswordChangePayload,
    ReservationCreatePayload,
    SlotListQuery,
    SlotCapacityPayload,
    StudentListQuery,
    AllowanceAdjustmentPayload,
    AdministratorPayload,
)
register_response_schema_models(
    console_ns,
    PortalLoginResponse,
    StudentResponse,
    StudentListResponse,
    RosterSyncResponse,
    AdministratorResponse,
    AdministratorListResponse,
    ReservationResponse,
    ReservationListResponse,
    SlotResponse,
    SlotListResponse,
    AdminSlotListResponse,
    SlotCapacityResponse,
    AccessDecisionResponse,
    ModelUsageResponse,
    AllowanceResponse,
    StudentDetailResponse,
    ResultResponse,
)
