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


class LoginSession(Base):
    __tablename__ = "login_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[int] = mapped_column(ForeignKey("administrator.id"))
    expires_at: Mapped[int]


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
