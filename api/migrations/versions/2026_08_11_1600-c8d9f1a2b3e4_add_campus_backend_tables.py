"""add Campus teaching-platform backend tables

Revision ID: c8d9f1a2b3e4
Revises: 7a1c2d9e4b60
Create Date: 2026-08-11 16:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

import models

revision = "c8d9f1a2b3e4"
down_revision = "7a1c2d9e4b60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_students",
        sa.Column("student_number", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("cohort", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("initial_allowance_yuan", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_students_pkey"),
        sa.UniqueConstraint("student_number", name="campus_students_student_number_key"),
    )
    op.create_index("campus_students_status_idx", "campus_students", ["status"])

    op.create_table(
        "campus_workspace_bindings",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("dify_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("dify_tenant_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_workspace_bindings_pkey"),
        sa.UniqueConstraint("student_id", name="campus_workspace_bindings_student_id_key"),
        sa.UniqueConstraint("dify_tenant_id", name="campus_workspace_bindings_dify_tenant_id_key"),
    )
    op.create_table(
        "campus_administrators",
        sa.Column("account_id", models.types.StringUUID(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_administrators_pkey"),
        sa.UniqueConstraint("account_id", name="campus_administrators_account_id_key"),
    )
    op.create_table(
        "campus_audit_events",
        sa.Column("actor_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_audit_events_pkey"),
    )
    op.create_index("campus_audit_events_actor_created_idx", "campus_audit_events", ["actor_account_id", "created_at"])
    op.create_index("campus_audit_events_target_idx", "campus_audit_events", ["target_type", "target_id"])
    op.create_table(
        "campus_portal_sessions",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_portal_sessions_pkey"),
        sa.UniqueConstraint("token_hash", name="campus_portal_sessions_token_hash_key"),
    )
    op.create_index(
        "campus_portal_sessions_student_expires_idx", "campus_portal_sessions", ["student_id", "expires_at"]
    )
    op.create_table(
        "campus_access_slots",
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_access_slots_pkey"),
        sa.UniqueConstraint("starts_at", name="campus_access_slots_starts_at_key"),
    )
    op.create_index("campus_access_slots_starts_at_idx", "campus_access_slots", ["starts_at"])
    op.create_table(
        "campus_reservations",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("slot_id", models.types.StringUUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("queue_sequence", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_reservations_pkey"),
        sa.UniqueConstraint(
            "slot_id", "queue_sequence", name="campus_reservations_slot_queue_sequence_key"
        ),
    )
    op.create_index("campus_reservations_slot_status_idx", "campus_reservations", ["slot_id", "status"])
    op.create_index("campus_reservations_student_status_idx", "campus_reservations", ["student_id", "status"])
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "campus_reservations_one_active_per_student_pg_idx",
            "campus_reservations",
            ["student_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.create_index(
            "campus_reservations_one_active_per_student_sqlite_idx",
            "campus_reservations",
            ["student_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )
    op.create_table(
        "campus_gateway_bindings",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("gateway_token_id", sa.String(length=64), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_gateway_bindings_pkey"),
        sa.UniqueConstraint("student_id", name="campus_gateway_bindings_student_id_key"),
        sa.UniqueConstraint("gateway_token_id", name="campus_gateway_bindings_gateway_token_id_key"),
    )
    op.create_table(
        "campus_allowance_adjustments",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("actor_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("delta_yuan", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_allowance_adjustments_pkey"),
        sa.UniqueConstraint("request_id", name="campus_allowance_adjustments_request_id_key"),
    )
    op.create_index(
        "campus_allowance_adjustments_student_created_idx",
        "campus_allowance_adjustments",
        ["student_id", "created_at"],
    )


def downgrade():
    op.drop_index("campus_allowance_adjustments_student_created_idx", table_name="campus_allowance_adjustments")
    op.drop_table("campus_allowance_adjustments")
    op.drop_table("campus_gateway_bindings")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("campus_reservations_one_active_per_student_pg_idx", table_name="campus_reservations")
    elif op.get_bind().dialect.name == "sqlite":
        op.drop_index("campus_reservations_one_active_per_student_sqlite_idx", table_name="campus_reservations")
    op.drop_index("campus_reservations_student_status_idx", table_name="campus_reservations")
    op.drop_index("campus_reservations_slot_status_idx", table_name="campus_reservations")
    op.drop_table("campus_reservations")
    op.drop_index("campus_access_slots_starts_at_idx", table_name="campus_access_slots")
    op.drop_table("campus_access_slots")
    op.drop_index("campus_portal_sessions_student_expires_idx", table_name="campus_portal_sessions")
    op.drop_table("campus_portal_sessions")
    op.drop_index("campus_audit_events_target_idx", table_name="campus_audit_events")
    op.drop_index("campus_audit_events_actor_created_idx", table_name="campus_audit_events")
    op.drop_table("campus_audit_events")
    op.drop_table("campus_administrators")
    op.drop_table("campus_workspace_bindings")
    op.drop_index("campus_students_status_idx", table_name="campus_students")
    op.drop_table("campus_students")
