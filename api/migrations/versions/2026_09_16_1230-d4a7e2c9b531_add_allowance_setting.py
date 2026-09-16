"""add administrator-managed default allowance setting

Revision ID: d4a7e2c9b531
Revises: c9d2f5b8a3e1
Create Date: 2026-09-16 12:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "d4a7e2c9b531"
down_revision = "c9d2f5b8a3e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_allowance_settings",
        sa.Column("setting_key", sa.String(length=64), nullable=False),
        sa.Column("default_allowance_usd", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("updated_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_allowance_settings_pkey"),
        sa.UniqueConstraint("setting_key", name="campus_allowance_settings_setting_key_key"),
    )


def downgrade():
    op.drop_table("campus_allowance_settings")
