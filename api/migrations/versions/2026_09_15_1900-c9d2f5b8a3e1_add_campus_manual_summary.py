"""add a derived summary to campus lab manual chapters

The experiment chooser lists each chapter under its experiment with a short
teaser underneath. That teaser is derived from the uploaded HTML rather than
authored, so it is stored next to the document instead of being recomputed on
every chooser load.

Existing rows are backfilled separately; a NULL summary simply means the
chooser shows no teaser for that chapter.

Revision ID: c9d2f5b8a3e1
Revises: b7c1e4a9f2d3
Create Date: 2026-09-15 19:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c9d2f5b8a3e1"
down_revision = "b7c1e4a9f2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campus_lab_manual_chapters", sa.Column("summary", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("campus_lab_manual_chapters", "summary")
