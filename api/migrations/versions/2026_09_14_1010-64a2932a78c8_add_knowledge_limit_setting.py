"""add administrator-managed knowledge limit setting

Revision ID: 64a2932a78c8
Revises: 91b21bfd1652
Create Date: 2026-09-14 10:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "64a2932a78c8"
down_revision = "91b21bfd1652"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_knowledge_limit_settings",
        sa.Column("setting_key", sa.String(length=64), nullable=False),
        sa.Column("max_datasets_per_workspace", sa.Integer(), nullable=False),
        sa.Column("max_documents_per_dataset", sa.Integer(), nullable=False),
        sa.Column("updated_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_knowledge_limit_settings_pkey"),
        sa.UniqueConstraint("setting_key", name="campus_knowledge_limit_settings_setting_key_key"),
    )


def downgrade():
    op.drop_table("campus_knowledge_limit_settings")
