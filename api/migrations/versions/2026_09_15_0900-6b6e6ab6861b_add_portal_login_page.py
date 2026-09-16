"""add administrator-uploaded portal login pages

Revision ID: 6b6e6ab6861b
Revises: 64a2932a78c8
Create Date: 2026-09-15 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "6b6e6ab6861b"
down_revision = "64a2932a78c8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_portal_login_pages",
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("uploaded_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_portal_login_pages_pkey"),
    )
    op.create_index(
        "campus_portal_login_pages_active_idx",
        "campus_portal_login_pages",
        ["is_active"],
    )


def downgrade():
    op.drop_index("campus_portal_login_pages_active_idx", table_name="campus_portal_login_pages")
    op.drop_table("campus_portal_login_pages")
