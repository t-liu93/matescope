"""Web-managed instance preferences and connection configuration (no external I/O)."""

import json
import re
from datetime import UTC, datetime
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


def validate_mailbox(value: str) -> str:
    # A deliberately narrow single ASCII mailbox; no display names, lists or headers.
    if (
        len(value) > 254
        or not re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*",
            value,
        )
        or len(value.split("@")[0]) > 64
    ):
        raise ValueError("Use one ASCII email address")
    return value


class SMTPFields(OptionalFields):
    port: int = Field(default=587, ge=1, le=65535)
    tls_mode: Literal["implicit", "starttls", "plain"] = "starttls"
    verify_tls: bool = True
    sender: str = Field(default="", max_length=320)


class SMTPTestInput(InputModel):
    recipient: str = Field(min_length=3, max_length=254)

    @field_validator("recipient")
    @classmethod
    def valid_recipient(cls, value: str) -> str:
        return validate_mailbox(value)


class PostgreSQLInput(PostgreSQLFields):
    password: PasswordChange = Field(default_factory=PasswordChange)


class MQTTInput(MQTTFields):
    password: PasswordChange = Field(default_factory=PasswordChange)


class SMTPInput(SMTPFields):
    @field_validator("sender")
    @classmethod
    def valid_sender(cls, value: str) -> str:
        return validate_mailbox(value) if value else value

    password: PasswordChange = Field(default_factory=PasswordChange)


class ConnectionState(BaseModel):
    password_set: bool = False
    version: int = 0
    test_available: bool = False


class PostgreSQLTestResult(BaseModel):
    version: int
    status: Literal["success", "failure"]
    code: Literal[
        "ok",
        "empty_data",
        "unconfigured",
        "disabled",
        "skipped",
        "invalid_credentials",
        "unavailable",
        "timeout",
        "incompatible_schema",
        "unsafe_permissions",
        "insufficient_permissions",
        "configuration_changed",
    ]
    tested_at: datetime
    persisted: bool = False


class PostgreSQLResponse(PostgreSQLFields, ConnectionState):
    status: Literal["unconfigured", "unverified", "disabled", "skipped", "success", "failure"] = (
        "disabled"
    )
    test_available: bool = True
    test_result: PostgreSQLTestResult | None = None

    @field_validator("test_available", mode="before")
    @classmethod
    def supports_test(cls, value: object) -> bool:
        return True


class OptionalTestResult(BaseModel):
    version: int
    status: Literal["success", "failure"]
    code: Literal[
        "subscription_accepted",
        "message_received",
        "delivery_accepted",
        "disabled",
        "skipped",
        "unconfigured",
        "invalid_credentials",
        "unavailable",
        "timeout",
        "tls_error",
        "configuration_changed",
        "busy",
        "subscription_rejected",
        "sender_rejected",
        "recipient_rejected",
        "message_rejected",
        "tls_unavailable",
        "protocol_error",
    ]
    tested_at: datetime
    persisted: bool = False


class MQTTTestResult(OptionalTestResult):
    message_received: bool = False


class SMTPTestResult(OptionalTestResult):
    delivery_accepted: bool = False


class OptionalResponse(ConnectionState):
    status: Literal["unconfigured", "unverified", "disabled", "skipped", "success", "failure"] = (
        "disabled"
    )
    test_available: bool = True

    @field_validator("test_available", mode="before")
    @classmethod
    def supports_test(cls, value: object) -> bool:
        return True


class MQTTResponse(MQTTFields, OptionalResponse):
    test_result: MQTTTestResult | None = None


class SMTPResponse(SMTPFields, OptionalResponse):
    test_result: SMTPTestResult | None = None


class Onboarding(InputModel):
    step: Literal["preferences", "postgresql", "mqtt", "smtp", "two_factor", "review"] = (
        "preferences"
    )
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
            test_available=True,
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


@router.post(
    "/postgresql/test", response_model=PostgreSQLTestResult, dependencies=[WriteProtection]
)
def test_postgresql(request: Request) -> PostgreSQLTestResult:
    from .postgresql import classify, snapshot

    config, password = snapshot(request)
    code = "ok"
    try:
        with request.app.state.postgresql.connection(config, password) as connection:
            if connection.execute("SELECT id FROM public.cars LIMIT 1").fetchone() is None:
                code = "empty_data"
    except Exception as error:
        code = classify(error)
    result = PostgreSQLTestResult(
        version=config.version,
        status="success" if code in {"ok", "empty_data"} else "failure",
        code=code,  # type: ignore[arg-type]
        tested_at=datetime.now(UTC),
    )
    with storage(request).transaction() as session:
        record = record_for_write(request, session)
        current = SettingsResponse.model_validate_json(record.configuration)
        if current.postgresql.version != config.version:
            result.status = "failure"
            result.code = "configuration_changed"
            return result
        result.persisted = True
        current.postgresql.status = result.status
        current.postgresql.test_result = result
        record.configuration = current.model_dump_json()
    return result


def test_optional(
    name: Literal["mqtt", "smtp"], request: Request, recipient: str = ""
) -> MQTTTestResult | SMTPTestResult:
    from .connection_tests import run_test

    with Session(storage(request).engine) as session:
        config = getattr(read_settings(session), name)
        record = session.get(ApplicationSettings, 1)
        encrypted = json.loads(record.encrypted_passwords).get(name) if record else None
        password = storage(request).cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
    code, observed = run_test(name, config, password, recipient)
    fields = dict(
        version=config.version,
        status="success"
        if code in {"subscription_accepted", "message_received", "delivery_accepted"}
        else "failure",
        code=code,
        tested_at=datetime.now(UTC),
    )
    result: MQTTTestResult | SMTPTestResult = (
        MQTTTestResult.model_validate({**fields, "message_received": observed})
        if name == "mqtt"
        else SMTPTestResult.model_validate({**fields, "delivery_accepted": observed})
    )
    with storage(request).transaction() as session:
        record = record_for_write(request, session)
        current = SettingsResponse.model_validate_json(record.configuration)
        target = getattr(current, name)
        if target.version != config.version:
            result.status = "failure"
            result.code = "configuration_changed"
            return result
        result.persisted = True
        target.status = result.status
        target.test_result = result
        record.configuration = current.model_dump_json()
    return result


@router.post("/mqtt/test", response_model=MQTTTestResult, dependencies=[WriteProtection])
def test_mqtt(request: Request) -> MQTTTestResult:
    result = test_optional("mqtt", request)
    assert isinstance(result, MQTTTestResult)
    return result


@router.post("/smtp/test", response_model=SMTPTestResult, dependencies=[WriteProtection])
def test_smtp(body: SMTPTestInput, request: Request) -> SMTPTestResult:
    result = test_optional("smtp", request, body.recipient)
    assert isinstance(result, SMTPTestResult)
    return result
