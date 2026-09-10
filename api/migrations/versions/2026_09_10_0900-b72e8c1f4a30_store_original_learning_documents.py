"""store original learning-document bytes

Legacy chapter bodies were sanitized before storage, so their original scripts
and event handlers cannot be reconstructed. Keep the migrated copy as an admin
draft for reference, but require replacement with the original file before it
can be published through the unrestricted document origin.

Revision ID: b72e8c1f4a30
Revises: a41d78e6f2b0
Create Date: 2026-09-10 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b72e8c1f4a30"
down_revision = "a41d78e6f2b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campus_lab_manual_chapters", sa.Column("document_data", sa.LargeBinary(), nullable=True))
    op.add_column("campus_lab_manual_chapters", sa.Column("original_filename", sa.String(length=255), nullable=True))
    op.add_column("campus_lab_manual_chapters", sa.Column("document_size_bytes", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE campus_lab_manual_chapters
            SET document_data = convert_to(body_html, 'UTF8'),
                original_filename = title || '.html',
                document_size_bytes = octet_length(convert_to(body_html, 'UTF8')),
                status = 'draft'
            WHERE document_data IS NULL
            """
        )
    )


def downgrade():
    op.drop_column("campus_lab_manual_chapters", "document_size_bytes")
    op.drop_column("campus_lab_manual_chapters", "original_filename")
    op.drop_column("campus_lab_manual_chapters", "document_data")
