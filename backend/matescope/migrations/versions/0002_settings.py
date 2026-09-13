"""Persist web configuration separately from authentication records."""

import sqlalchemy as sa
from alembic import op

revision = "0002_settings"
down_revision = "0001_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("encrypted_passwords", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="single_settings"),
    )


def downgrade() -> None:
    op.drop_table("application_settings")
