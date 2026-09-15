from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Administrator(Base):
    __tablename__ = "administrator"
    __table_args__ = (CheckConstraint("id = 1", name="single_administrator"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    auth_version: Mapped[int] = mapped_column(default=0)
    totp_seed: Mapped[str | None]
    totp_enabled_at: Mapped[int | None]
    totp_last_step: Mapped[int] = mapped_column(default=-1)
    factor_failures: Mapped[str] = mapped_column(default="[]")
    totp_used_codes: Mapped[str] = mapped_column(default="[]")


class LoginSession(Base):
    __tablename__ = "login_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[int] = mapped_column(ForeignKey("administrator.id"))
    expires_at: Mapped[int]
    auth_version: Mapped[int] = mapped_column(default=0)


class Instance(Base):
    __tablename__ = "instance"
    __table_args__ = (CheckConstraint("id = 1", name="single_instance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key_check: Mapped[str]


class ApplicationSettings(Base):
    __tablename__ = "application_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_settings"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[str]
    encrypted_passwords: Mapped[str]


class LoginChallenge(Base):
    __tablename__ = "login_challenge"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[int] = mapped_column(ForeignKey("administrator.id"))
    auth_version: Mapped[int]
    expires_at: Mapped[int]
    failures: Mapped[int] = mapped_column(default=0)


class FactorEnrollment(Base):
    __tablename__ = "factor_enrollment"

    administrator_id: Mapped[int] = mapped_column(ForeignKey("administrator.id"), primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    auth_version: Mapped[int]
    seed: Mapped[str]
    expires_at: Mapped[int]


class RecoveryCode(Base):
    __tablename__ = "recovery_code"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[int] = mapped_column(ForeignKey("administrator.id"))
