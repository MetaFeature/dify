"""denominate the model allowance in US dollars

The gateway's quota unit is anchored to the US dollar (500,000 quota = $1),
so the allowance these columns hold was never renminbi despite the name.
Renaming the columns rather than converting the values: the stored numbers
were already dollars, only the label was wrong.

Revision ID: e4a1c6b25f78
Revises: d7f2b4a8c9e3
Create Date: 2026-08-28 16:00:00.000000

"""

from alembic import op

revision = "e4a1c6b25f78"
down_revision = "d7f2b4a8c9e3"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("campus_students", "initial_allowance_yuan", new_column_name="initial_allowance_usd")
    op.alter_column("campus_allowance_adjustments", "delta_yuan", new_column_name="delta_usd")


def downgrade():
    op.alter_column("campus_allowance_adjustments", "delta_usd", new_column_name="delta_yuan")
    op.alter_column("campus_students", "initial_allowance_usd", new_column_name="initial_allowance_yuan")
