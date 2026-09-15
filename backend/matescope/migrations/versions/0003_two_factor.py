"""Optional administrator TOTP and bounded authentication state."""

import sqlalchemy as sa
from alembic import op

revision = "0003_two_factor"
down_revision = "0002_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("totp_seed", sa.String(), nullable=True),
        sa.Column("totp_enabled_at", sa.Integer(), nullable=True),
        sa.Column("totp_last_step", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("factor_failures", sa.String(), nullable=False, server_default="[]"),
        sa.Column("totp_used_codes", sa.String(), nullable=False, server_default="[]"),
    ):
        op.add_column("administrator", column)
    op.add_column(
        "login_session", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_table(
        "login_challenge",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "administrator_id", sa.Integer(), sa.ForeignKey("administrator.id"), nullable=False
        ),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
    )
    op.create_table(
        "factor_enrollment",
        sa.Column(
            "administrator_id", sa.Integer(), sa.ForeignKey("administrator.id"), primary_key=True
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("seed", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "recovery_code",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column(
            "administrator_id", sa.Integer(), sa.ForeignKey("administrator.id"), nullable=False
        ),
    )


def downgrade() -> None:
    for table in ("recovery_code", "factor_enrollment", "login_challenge"):
        op.drop_table(table)
    op.drop_column("login_session", "auth_version")
    for column in (
        "auth_version",
        "totp_seed",
        "totp_enabled_at",
        "totp_last_step",
        "factor_failures",
        "totp_used_codes",
    ):
        op.drop_column("administrator", column)
