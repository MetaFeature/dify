"""allow one pending and one effective reservation claim per student

Replaces the one-active-claim-per-student partial unique index with a
per-slot uniqueness guard. The effective/pending bounds are time-dependent
and enforced by the reservation service under the student row lock.

Revision ID: b3e5a7c90d12
Revises: c8d9f1a2b3e4
Create Date: 2026-08-28 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b3e5a7c90d12"
down_revision = "c8d9f1a2b3e4"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("campus_reservations_one_active_per_student_pg_idx", table_name="campus_reservations")
        op.create_index(
            "campus_reservations_one_active_per_slot_pg_idx",
            "campus_reservations",
            ["student_id", "slot_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.drop_index("campus_reservations_one_active_per_student_sqlite_idx", table_name="campus_reservations")
        op.create_index(
            "campus_reservations_one_active_per_slot_sqlite_idx",
            "campus_reservations",
            ["student_id", "slot_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("campus_reservations_one_active_per_slot_pg_idx", table_name="campus_reservations")
        op.create_index(
            "campus_reservations_one_active_per_student_pg_idx",
            "campus_reservations",
            ["student_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.drop_index("campus_reservations_one_active_per_slot_sqlite_idx", table_name="campus_reservations")
        op.create_index(
            "campus_reservations_one_active_per_student_sqlite_idx",
            "campus_reservations",
            ["student_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('confirmed', 'waitlisted')"),
        )
