"""Create the single administrator and server-side session storage."""

import sqlalchemy as sa
from alembic import op

revision = "0001_auth"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "administrator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.CheckConstraint("id = 1", name="single_administrator"),
    )
    op.create_table(
        "login_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "administrator_id", sa.Integer(), sa.ForeignKey("administrator.id"), nullable=False
        ),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "instance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_check", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="single_instance"),
    )


def downgrade() -> None:
    op.drop_table("login_session")
    op.drop_table("administrator")
    op.drop_table("instance")
