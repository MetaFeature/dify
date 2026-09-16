"""Strict request and redacted response schemas for the Campus console API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from controllers.common.schema import register_response_schema_models, register_schema_models
from controllers.console import console_ns
from fields.base import ResponseModel
from libs.helper import EmailStr
from models.campus import ExperimentTrack, LabManualChapterStatus, StudentStatus


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
    # Matched against the student number or the display name.
    keyword: str | None = Field(default=None, max_length=128)
    # The restore view is the only caller that wants soft-deleted rows.
    include_deleted: bool = False


class StudentInitialPasswordResponse(CampusResponseModel):
    """The initial password an administrator reset just installed."""

    student_number: str
    password: str


class StudentRenamePayload(CampusRequestModel):
    display_name: str = Field(min_length=1, max_length=255)

    @field_validator("display_name")
    @classmethod
    def validate_non_whitespace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class RetentionPurgeResponse(CampusResponseModel):
    purged: list[str]
    failed: list[list[str]]


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


class AdministratorCreatePayload(CampusRequestModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name", "password")
    @classmethod
    def validate_create_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class PortalLoginResponse(CampusResponseModel):
    student_id: str
    expires_at: datetime


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


class StudentResponse(CampusResponseModel):
    id: str
    student_number: str
    display_name: str
    cohort: str | None
    status: StudentStatus
    has_credential: bool | None = None
    virtual_identity: bool | None = None
    # Set once the student has been soft-deleted; cleared by a restore.
    deleted_at: datetime | None = None
    # Present on the list too, so an administrator can read every student's
    # allowance without opening each one. None means the lookup did not answer.
    allowance: AllowanceResponse | None = None


class StudentListResponse(CampusResponseModel):
    data: list[StudentResponse]


class RosterSyncResponse(CampusResponseModel):
    created: int
    updated: int
    password_resets: int = 0
    default_passwords: int = 0


class RosterParsedResponse(CampusResponseModel):
    data: list[StudentIdentityPayload]


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


class SlotCapacitySettingPayload(CampusRequestModel):
    capacity: int = Field(ge=1)


class SlotCapacitySettingResponse(CampusResponseModel):
    capacity: int
    platform_default: int
    configured_capacity: int | None = None
    is_default: bool


class KnowledgeLimitSettingPayload(CampusRequestModel):
    max_datasets_per_workspace: int = Field(ge=1)
    max_documents_per_dataset: int = Field(ge=1)


class KnowledgeLimitSettingResponse(CampusResponseModel):
    max_datasets_per_workspace: int
    max_documents_per_dataset: int
    platform_max_datasets_per_workspace: int
    platform_max_documents_per_dataset: int
    configured_max_datasets_per_workspace: int | None = None
    configured_max_documents_per_dataset: int | None = None
    is_default: bool


class PortalLoginPageResponse(CampusResponseModel):
    id: str
    filename: str
    size_bytes: int
    digest: str
    is_active: bool
    created_at: datetime
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []


class PortalLoginStateResponse(CampusResponseModel):
    active_id: str | None = None
    active_filename: str | None = None
    pages: list[PortalLoginPageResponse] = []


class DefaultAllowancePayload(CampusRequestModel):
    default_allowance_usd: Decimal = Field(ge=0)


class DefaultAllowanceResponse(CampusResponseModel):
    default_allowance_usd: Decimal
    platform_default_usd: Decimal
    configured_default_allowance_usd: Decimal | None = None
    is_default: bool


class DefaultAllowanceChangeResponse(CampusResponseModel):
    default_allowance_usd: Decimal
    platform_default_usd: Decimal
    configured_default_allowance_usd: Decimal | None = None
    is_default: bool
    previous_configured_default_allowance_usd: Decimal | None = None
    scanned_students: int
    changed_students: int


class StudentKnowledgeLimitResponse(CampusResponseModel):
    max_datasets_per_workspace: int
    max_documents_per_dataset: int


class SlotCapacitySettingChangeResponse(CampusResponseModel):
    capacity: int
    platform_default: int
    configured_capacity: int | None = None
    is_default: bool
    previous_configured_capacity: int | None = None
    scanned_slots: int
    changed_slots: int
    promoted_waiters: int


class AccessDecisionResponse(CampusResponseModel):
    allowed: bool
    reservation_id: str | None = None
    ends_at: datetime | None = None
    server_now: datetime | None = None


class StudentDetailResponse(StudentResponse):
    workspace_id: str | None
    created_at: datetime
    deleted_at: datetime | None = None


class ResultResponse(CampusResponseModel):
    result: str


class LabManualChapterStatusPayload(CampusRequestModel):
    status: LabManualChapterStatus


class LabManualChapterPositionPayload(CampusRequestModel):
    position: int = Field(ge=1, le=1000)


class LabManualChapterResponse(CampusResponseModel):
    id: str
    track: ExperimentTrack
    title: str
    original_filename: str
    size_bytes: int
    content_url: str
    position: int
    status: LabManualChapterStatus


class LabManualChapterDetailResponse(LabManualChapterResponse):
    pass


class LabManualChapterSavedResponse(LabManualChapterDetailResponse):
    pass


class LabManualChapterListResponse(CampusResponseModel):
    data: list[LabManualChapterResponse]


class LabManualResponse(CampusResponseModel):
    """One track's published learning-document metadata, in order."""

    track: ExperimentTrack
    data: list[LabManualChapterResponse]


class ManualChapterReferenceResponse(CampusResponseModel):
    id: str
    #: The title as a student reads it — no file extension.
    title: str
    #: A short derived teaser of the document body, shown under the title.
    summary: str | None = None
    #: Absolute URL on the manual origin; the portal links straight at it.
    view_url: str


class ExperimentTrackResponse(CampusResponseModel):
    track: ExperimentTrack
    #: Whether the platform serves this track through Dify or through a manual.
    kind: str
    chapters: int
    #: The same chapters in reading order, with the URL each one opens by.
    chapter_list: list[ManualChapterReferenceResponse]


class ExperimentTrackListResponse(CampusResponseModel):
    data: list[ExperimentTrackResponse]


class TrackPresentationPayload(CampusRequestModel):
    track: ExperimentTrack
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    position: int = Field(ge=1, le=4)

    @field_validator("title", "description")
    @classmethod
    def normalize_presentation_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be whitespace-only")
        return normalized


class PortalPresentationPayload(CampusRequestModel):
    login_html: str = Field(min_length=1, max_length=200_000)
    tracks: list[TrackPresentationPayload] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_tracks(self):
        if {item.track for item in self.tracks} != set(ExperimentTrack):
            raise ValueError("presentation must configure every experiment track exactly once")
        if {item.position for item in self.tracks} != {1, 2, 3, 4}:
            raise ValueError("presentation track positions must use positions 1, 2, and 3")
        return self


class PortalPresentationResponse(CampusResponseModel):
    login_html: str
    tracks: list[TrackPresentationPayload]
    is_custom: bool


register_schema_models(
    console_ns,
    VirtualLoginPayload,
    StudentRosterSyncPayload,
    StudentStatusPayload,
    StudentRenamePayload,
    StudentCreatePayload,
    PortalPasswordChangePayload,
    ReservationCreatePayload,
    SlotListQuery,
    SlotCapacityPayload,
    SlotCapacitySettingPayload,
    DefaultAllowancePayload,
    StudentListQuery,
    AllowanceAdjustmentPayload,
    AdministratorPayload,
    AdministratorCreatePayload,
    LabManualChapterStatusPayload,
    LabManualChapterPositionPayload,
    PortalPresentationPayload,
)
register_response_schema_models(
    console_ns,
    PortalLoginResponse,
    StudentInitialPasswordResponse,
    StudentResponse,
    StudentListResponse,
    RetentionPurgeResponse,
    RosterSyncResponse,
    RosterParsedResponse,
    AdministratorResponse,
    AdministratorListResponse,
    ReservationResponse,
    ReservationListResponse,
    SlotResponse,
    SlotListResponse,
    AdminSlotListResponse,
    SlotCapacityResponse,
    SlotCapacitySettingResponse,
    SlotCapacitySettingChangeResponse,
    DefaultAllowanceResponse,
    DefaultAllowanceChangeResponse,
    KnowledgeLimitSettingPayload,
    KnowledgeLimitSettingResponse,
    StudentKnowledgeLimitResponse,
    PortalLoginPageResponse,
    PortalLoginStateResponse,
    AccessDecisionResponse,
    ModelUsageResponse,
    AllowanceResponse,
    StudentDetailResponse,
    ResultResponse,
    LabManualChapterResponse,
    LabManualChapterDetailResponse,
    LabManualChapterSavedResponse,
    LabManualChapterListResponse,
    LabManualResponse,
    ExperimentTrackResponse,
    ManualChapterReferenceResponse,
    ExperimentTrackListResponse,
    PortalPresentationResponse,
)
