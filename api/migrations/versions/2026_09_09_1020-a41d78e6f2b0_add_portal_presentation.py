"""add administrator-managed portal presentation

Revision ID: a41d78e6f2b0
Revises: d3f56c728a91
Create Date: 2026-09-09 10:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "a41d78e6f2b0"
down_revision = "c4a8f2d91e60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_portal_presentations",
        sa.Column("content_key", sa.String(length=64), nullable=False),
        sa.Column("draft_json", sa.Text(), nullable=False),
        sa.Column("published_json", sa.Text(), nullable=True),
        sa.Column("updated_by_account_id", models.types.StringUUID(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_portal_presentations_pkey"),
        sa.UniqueConstraint("content_key", name="campus_portal_presentations_content_key_key"),
    )


def downgrade():
    op.drop_table("campus_portal_presentations")
