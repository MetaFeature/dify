"""add administrator-managed slot capacity setting

Revision ID: 91b21bfd1652
Revises: b72e8c1f4a30
Create Date: 2026-09-12 13:36:56.352407
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "91b21bfd1652"
down_revision = "b72e8c1f4a30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_slot_capacity_settings",
        sa.Column("setting_key", sa.String(length=64), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("updated_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_slot_capacity_settings_pkey"),
        sa.UniqueConstraint("setting_key", name="campus_slot_capacity_settings_setting_key_key"),
    )


def downgrade():
    op.drop_table("campus_slot_capacity_settings")
