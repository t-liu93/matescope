"""Web-managed instance preferences and connection configuration (no external I/O)."""

import json
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy.orm import Session

from .auth import WriteProtection, find_session, require_admin, storage
from .models import ApplicationSettings

router = APIRouter(
    prefix="/api/v1/settings", tags=["settings"], dependencies=[Depends(require_admin)]
)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preferences(InputModel):
    language: Literal["en", "zh"] = "en"
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    tile_url: str = Field(default="https://tile.openstreetmap.org/{z}/{x}/{y}.png", max_length=2048)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown IANA timezone") from None
        return value

    @field_validator("tile_url")
    @classmethod
    def valid_tile_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(token not in value for token in ("{z}", "{x}", "{y}"))
        ):
            raise ValueError("Use an HTTP(S) tile URL with z, x, y placeholders and no credentials")
        _ = parsed.port
        return value


class PreferencesResponse(Preferences):
    saved: bool = False


class PasswordChange(InputModel):
    action: Literal["retain", "replace", "clear"] = "retain"
    value: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_action(self) -> "PasswordChange":
        if (self.action == "replace") != (self.value is not None):
            raise ValueError("Only replace requires a password value")
        return self


class ConnectionFields(InputModel):
    host: str = Field(default="", max_length=253, pattern=r"^[^\s/@?#]*$")
    username: str = Field(default="", max_length=256, pattern=r"^[^\r\n\x00]*$")


class OptionalFields(ConnectionFields):
    enabled: bool = False
    skipped: bool = False

    @model_validator(mode="after")
    def exclusive_state(self) -> "OptionalFields":
        if self.enabled and self.skipped:
            raise ValueError("A skipped service cannot be enabled")
        return self


class PostgreSQLFields(OptionalFields):
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="teslamate", min_length=1, max_length=128)
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"


class MQTTFields(OptionalFields):
    port: int = Field(default=1883, ge=1, le=65535)
    tls: bool = False
    verify_tls: bool = True
    topic_prefix: str = Field(
        default="teslamate", min_length=1, max_length=256, pattern=r"^[^+#\x00\r\n]+$"
    )


class SMTPFields(OptionalFields):
    port: int = Field(default=587, ge=1, le=65535)
    tls_mode: Literal["implicit", "starttls", "plain"] = "starttls"
    verify_tls: bool = True
    sender: str = Field(default="", max_length=320, pattern=r"^(?:[^\s<>@]+@[^\s<>@]+)?$")


class PostgreSQLInput(PostgreSQLFields):
    password: PasswordChange = Field(default_factory=PasswordChange)


class MQTTInput(MQTTFields):
    password: PasswordChange = Field(default_factory=PasswordChange)


class SMTPInput(SMTPFields):
    password: PasswordChange = Field(default_factory=PasswordChange)


class ConnectionState(BaseModel):
    password_set: bool = False
    version: int = 0
    status: Literal["unconfigured", "unverified", "disabled", "skipped"] = "unconfigured"
    test_available: Literal[False] = False


class PostgreSQLResponse(PostgreSQLFields, ConnectionState):
    status: Literal["unconfigured", "unverified", "disabled", "skipped"] = "disabled"


class MQTTResponse(MQTTFields, ConnectionState):
    status: Literal["unconfigured", "unverified", "disabled", "skipped"] = "disabled"


class SMTPResponse(SMTPFields, ConnectionState):
    status: Literal["unconfigured", "unverified", "disabled", "skipped"] = "disabled"


class Onboarding(InputModel):
    step: Literal["preferences", "postgresql", "mqtt", "smtp", "review"] = "preferences"
    completed: bool = False


class SettingsResponse(BaseModel):
    preferences: PreferencesResponse = Field(default_factory=PreferencesResponse)
    postgresql: PostgreSQLResponse = Field(default_factory=PostgreSQLResponse)
    mqtt: MQTTResponse = Field(default_factory=MQTTResponse)
    smtp: SMTPResponse = Field(default_factory=SMTPResponse)
    onboarding: Onboarding = Field(default_factory=Onboarding)


def read_settings(session: Session) -> SettingsResponse:
    record = session.get(ApplicationSettings, 1)
    return (
        SettingsResponse.model_validate_json(record.configuration) if record else SettingsResponse()
    )


def record_for_write(request: Request, session: Session) -> ApplicationSettings:
    # Recheck session inside the writer transaction, serializing with logout/password changes.
    find_session(request, session)
    record = session.get(ApplicationSettings, 1)
    if record is None:
        record = ApplicationSettings(
            id=1, configuration=SettingsResponse().model_dump_json(), encrypted_passwords="{}"
        )
        session.add(record)
    return record


@router.get("", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    with Session(storage(request).engine) as session:
        return read_settings(session)


@router.put("/preferences", response_model=SettingsResponse, dependencies=[WriteProtection])
def save_preferences(body: Preferences, request: Request) -> SettingsResponse:
    with storage(request).transaction() as session:
        record = record_for_write(request, session)
        result = SettingsResponse.model_validate_json(record.configuration)
        result.preferences = PreferencesResponse(**body.model_dump(), saved=True)
        record.configuration = result.model_dump_json()
        return result


@router.put("/onboarding", response_model=SettingsResponse, dependencies=[WriteProtection])
def save_onboarding(body: Onboarding, request: Request) -> SettingsResponse:
    with storage(request).transaction() as session:
        record = record_for_write(request, session)
        result = SettingsResponse.model_validate_json(record.configuration)
        result.onboarding = body
        record.configuration = result.model_dump_json()
        return result


def save_connection(
    name: Literal["postgresql", "mqtt", "smtp"],
    body: PostgreSQLInput | MQTTInput | SMTPInput,
    request: Request,
) -> SettingsResponse:
    with storage(request).transaction() as session:
        record = record_for_write(request, session)
        result = SettingsResponse.model_validate_json(record.configuration)
        passwords: dict[str, str] = json.loads(record.encrypted_passwords)
        if body.password.action == "clear":
            passwords.pop(name, None)
        elif body.password.action == "replace":
            assert body.password.value is not None
            passwords[name] = (
                storage(request)
                .cipher.encrypt(body.password.value.get_secret_value().encode())
                .decode()
            )
        fields = body.model_dump(exclude={"password"})
        status = "unverified" if body.host else "unconfigured"
        if isinstance(body, OptionalFields):
            status = "skipped" if body.skipped else status if body.enabled else "disabled"
        fields.update(
            password_set=name in passwords,
            version=getattr(result, name).version + 1,
            status=status,
            test_available=False,
        )
        response_types: dict[str, type[BaseModel]] = {
            "postgresql": PostgreSQLResponse,
            "mqtt": MQTTResponse,
            "smtp": SMTPResponse,
        }
        setattr(result, name, response_types[name].model_validate(fields))
        record.configuration = result.model_dump_json()
        record.encrypted_passwords = json.dumps(passwords)
        return result


@router.put("/postgresql", response_model=SettingsResponse, dependencies=[WriteProtection])
def save_postgresql(body: PostgreSQLInput, request: Request) -> SettingsResponse:
    return save_connection("postgresql", body, request)


@router.put("/mqtt", response_model=SettingsResponse, dependencies=[WriteProtection])
def save_mqtt(body: MQTTInput, request: Request) -> SettingsResponse:
    return save_connection("mqtt", body, request)


@router.put("/smtp", response_model=SettingsResponse, dependencies=[WriteProtection])
def save_smtp(body: SMTPInput, request: Request) -> SettingsResponse:
    return save_connection("smtp", body, request)
