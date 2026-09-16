"""stop forcing a change of the derived Campus initial password

The initial password is a working password again: an administrator hands it out
and the student may keep it. The column that recorded "this credential still has
to be replaced" has no reader left, so it goes away with the rule.

Revision ID: e7b3c05a91d4
Revises: d4a7e2c9b531
Create Date: 2026-09-16 18:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c05a91d4"
down_revision = "d4a7e2c9b531"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("campus_student_credentials") as batch_op:
        batch_op.drop_column("must_change_password")


def downgrade():
    with op.batch_alter_table("campus_student_credentials") as batch_op:
        batch_op.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
