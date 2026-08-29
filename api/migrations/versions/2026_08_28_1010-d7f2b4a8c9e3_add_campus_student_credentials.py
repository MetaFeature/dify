"""add campus student credentials table

Platform-held student passwords (pbkdf2 + salt, base64-encoded like Dify
accounts). A row's presence makes it the authoritative portal credential
for that student; the virtual identity source remains a fallback for
students without a row.

Revision ID: d7f2b4a8c9e3
Revises: b3e5a7c90d12
Create Date: 2026-08-28 10:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

import models

revision = "d7f2b4a8c9e3"
down_revision = "b3e5a7c90d12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campus_student_credentials",
        sa.Column("student_id", models.types.StringUUID(), nullable=False),
        sa.Column("password_hashed", sa.String(length=255), nullable=False),
        sa.Column("password_salt", sa.String(length=64), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="campus_student_credentials_pkey"),
        sa.UniqueConstraint("student_id", name="campus_student_credentials_student_id_key"),
    )


def downgrade():
    op.drop_table("campus_student_credentials")
