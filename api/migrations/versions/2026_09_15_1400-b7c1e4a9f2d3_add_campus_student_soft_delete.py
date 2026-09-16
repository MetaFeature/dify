"""add soft delete to campus students

Deletion is two stages. `campus_students.deleted_at` marks a student as
soft-deleted: the row, its credential, its gateway token and its Dify
workspace all survive, so an administrator can restore the account with a
single column change. A separate retention sweep is what finally destroys the
data, and only once the deletion is older than the retention window.

The partial index keeps the two hot queries — "the roster" (deleted_at IS
NULL) and "what is old enough to purge" (deleted_at <= cutoff) — off a full
table scan.

Revision ID: b7c1e4a9f2d3
Revises: 6b6e6ab6861b
Create Date: 2026-09-15 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b7c1e4a9f2d3"
down_revision = "6b6e6ab6861b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campus_students", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("campus_students_deleted_at_idx", "campus_students", ["deleted_at"])


def downgrade():
    op.drop_index("campus_students_deleted_at_idx", table_name="campus_students")
    op.drop_column("campus_students", "deleted_at")
