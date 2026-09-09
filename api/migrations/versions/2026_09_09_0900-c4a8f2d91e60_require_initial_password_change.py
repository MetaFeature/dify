"""require derived Campus initial passwords to be changed

Revision ID: c4a8f2d91e60
Revises: a61d7c9e4b20
Create Date: 2026-09-09 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c4a8f2d91e60"
down_revision = "a61d7c9e4b20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("campus_student_credentials") as batch_op:
        batch_op.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("campus_student_credentials") as batch_op:
        batch_op.drop_column("must_change_password")
