from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, DefaultFieldsMixin
from .types import EnumText, StringUUID


class StudentStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ReservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"


ACTIVE_RESERVATION_STATUSES = (ReservationStatus.CONFIRMED, ReservationStatus.WAITLISTED)


class CampusStudent(DefaultFieldsMixin, Base):
    __tablename__ = "campus_students"
    __table_args__ = (
        sa.UniqueConstraint("student_number", name="campus_students_student_number_key"),
        sa.Index("campus_students_status_idx", "status"),
    )

    student_number: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    cohort: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    status: Mapped[StudentStatus] = mapped_column(
        EnumText(StudentStatus, length=16), nullable=False, default=StudentStatus.ACTIVE
    )
    initial_allowance_usd: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False, default=Decimal(0))


class CampusStudentCredential(DefaultFieldsMixin, Base):
    """Platform-held login credential for one Campus student.

    When a row exists for a student number, it is the authoritative portal
    credential; the virtual identity source is consulted only for students
    without a row.
    """

    __tablename__ = "campus_student_credentials"
    __table_args__ = (sa.UniqueConstraint("student_id", name="campus_student_credentials_student_id_key"),)

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    password_hashed: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    password_salt: Mapped[str] = mapped_column(sa.String(64), nullable=False)


class CampusWorkspaceBinding(DefaultFieldsMixin, Base):
    __tablename__ = "campus_workspace_bindings"
    __table_args__ = (
        sa.UniqueConstraint("student_id", name="campus_workspace_bindings_student_id_key"),
        sa.UniqueConstraint("dify_tenant_id", name="campus_workspace_bindings_dify_tenant_id_key"),
    )

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    dify_account_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    dify_tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)


class CampusAdministrator(DefaultFieldsMixin, Base):
    __tablename__ = "campus_administrators"
    __table_args__ = (sa.UniqueConstraint("account_id", name="campus_administrators_account_id_key"),)

    account_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_by_account_id: Mapped[str] = mapped_column(StringUUID, nullable=False)


class CampusAuditEvent(DefaultFieldsMixin, Base):
    __tablename__ = "campus_audit_events"
    __table_args__ = (
        sa.Index("campus_audit_events_actor_created_idx", "actor_account_id", "created_at"),
        sa.Index("campus_audit_events_target_idx", "target_type", "target_id"),
    )

    actor_account_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    action: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    details_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="{}")


class CampusPortalSession(DefaultFieldsMixin, Base):
    __tablename__ = "campus_portal_sessions"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="campus_portal_sessions_token_hash_key"),
        sa.Index("campus_portal_sessions_student_expires_idx", "student_id", "expires_at"),
    )

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)


class CampusAccessSlot(DefaultFieldsMixin, Base):
    __tablename__ = "campus_access_slots"
    __table_args__ = (
        sa.UniqueConstraint("starts_at", name="campus_access_slots_starts_at_key"),
        sa.Index("campus_access_slots_starts_at_idx", "starts_at"),
    )

    starts_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class CampusReservation(DefaultFieldsMixin, Base):
    __tablename__ = "campus_reservations"
    __table_args__ = (
        sa.Index("campus_reservations_slot_status_idx", "slot_id", "status"),
        sa.Index("campus_reservations_student_status_idx", "student_id", "status"),
        sa.UniqueConstraint("slot_id", "queue_sequence", name="campus_reservations_slot_queue_sequence_key"),
        sa.Index(
            "campus_reservations_one_active_per_slot_pg_idx",
            "student_id",
            "slot_id",
            unique=True,
            postgresql_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        ).ddl_if(dialect="postgresql"),
        sa.Index(
            "campus_reservations_one_active_per_slot_sqlite_idx",
            "student_id",
            "slot_id",
            unique=True,
            sqlite_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        ).ddl_if(dialect="sqlite"),
    )

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    slot_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(EnumText(ReservationStatus, length=16), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    queue_sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)


class CampusGatewayBinding(DefaultFieldsMixin, Base):
    __tablename__ = "campus_gateway_bindings"
    __table_args__ = (
        sa.UniqueConstraint("student_id", name="campus_gateway_bindings_student_id_key"),
        sa.UniqueConstraint("gateway_token_id", name="campus_gateway_bindings_gateway_token_id_key"),
    )

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    gateway_token_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)


class CampusAllowanceAdjustment(DefaultFieldsMixin, Base):
    __tablename__ = "campus_allowance_adjustments"
    __table_args__ = (
        sa.UniqueConstraint("request_id", name="campus_allowance_adjustments_request_id_key"),
        sa.Index("campus_allowance_adjustments_student_created_idx", "student_id", "created_at"),
    )

    student_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    request_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    actor_account_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    delta_usd: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(500), nullable=False)
