"""enforce unique Campus workspace accounts

Every Campus student already has a unique binding and Dify tenant. Make the
account side of the same one-to-one contract a database invariant so concurrent
or faulty provisioning cannot bind one Dify identity to two students.

Revision ID: a61d7c9e4b20
Revises: f2c8d4b71a95
Create Date: 2026-08-29 19:40:00.000000

"""

from alembic import op

revision = "a61d7c9e4b20"
down_revision = "f2c8d4b71a95"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "campus_workspace_bindings_dify_account_id_key",
        "campus_workspace_bindings",
        ["dify_account_id"],
    )


def downgrade():
    op.drop_constraint(
        "campus_workspace_bindings_dify_account_id_key",
        "campus_workspace_bindings",
        type_="unique",
    )
