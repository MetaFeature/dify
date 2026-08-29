"""add campus lab manual tables

The deep-learning and agent experiment tracks are completed on the student's own
machine, so the only thing the platform publishes for them is a chapter-ordered
lab manual (ADR-0018). Chapter bodies are stored already sanitized, cleaned once
at upload, so serving a chapter never re-parses untrusted markup.

Position is indexed but not unique: reordering chapters would otherwise need a
temporary value to step around the constraint, and ties are broken by id.

Revision ID: f2c8d4b71a95
Revises: e4a1c6b25f78
Create Date: 2026-08-29 17:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

import models

revision = "f2c8d4b71a95"
down_revision = "e4a1c6b25f78"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_lab_manual_chapters",
        sa.Column("track", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_lab_manual_chapters_pkey"),
    )
    op.create_index(
        "campus_lab_manual_chapters_track_position_idx",
        "campus_lab_manual_chapters",
        ["track", "position"],
    )
    op.create_table(
        "campus_lab_manual_images",
        sa.Column("track", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_lab_manual_images_pkey"),
    )
    op.create_index("campus_lab_manual_images_track_idx", "campus_lab_manual_images", ["track"])


def downgrade():
    op.drop_index("campus_lab_manual_images_track_idx", table_name="campus_lab_manual_images")
    op.drop_table("campus_lab_manual_images")
    op.drop_index("campus_lab_manual_chapters_track_position_idx", table_name="campus_lab_manual_chapters")
    op.drop_table("campus_lab_manual_chapters")
