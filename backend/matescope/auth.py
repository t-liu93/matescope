import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from typing import Annotated, cast

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Administrator, LoginSession
from .storage import Storage

router = APIRouter(prefix="/api/v1", tags=["authentication"])
SESSION_COOKIE = "matescope_session"
CSRF_COOKIE = "matescope_csrf"
password_hasher = PasswordHasher()  # Argon2id, RFC 9106 low-memory defaults.


def timestamp() -> int:
    return int(time.time())


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Single-process rolling windows, bounded even for arbitrary client addresses."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: OrderedDict[str, list[float]] = OrderedDict()
        self.global_attempts: list[float] = []

    def check(self, client: str) -> None:
        now = time.monotonic()
        with self.lock:
            for key in list(self.clients):
                recent = [t for t in self.clients[key] if t > now - 60]
                if recent:
                    self.clients[key] = recent
                else:
                    del self.clients[key]
            self.global_attempts = [t for t in self.global_attempts if t > now - 60]
            attempts = self.clients.get(client, [])
            if (
                len(attempts) >= 5
                or len(self.global_attempts) >= 30
                or (client not in self.clients and len(self.clients) >= 1024)
            ):
                raise HTTPException(
                    429, "Too many attempts; retry later", headers={"Retry-After": "60"}
                )
            self.clients[client] = [*attempts, now]
            self.global_attempts.append(now)


def storage(request: Request) -> Storage:
    return cast(Storage, request.app.state.storage)


def config(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def csrf_signature(request: Request, payload: str) -> str:
    # Domain separation keeps this distinct from connection encryption.
    key = cast(bytes, request.app.state.csrf_key)
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def valid_csrf(request: Request, token: str) -> bool:
    if len(token) > 256:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    nonce, expires, signature = parts
    if not expires.isascii() or not expires.isdigit() or len(expires) > 12:
        return False
    return int(expires) > timestamp() and hmac.compare_digest(
        signature.encode(), csrf_signature(request, f"{nonce}.{expires}").encode()
    )


def set_cookie(request: Request, response: Response, name: str, value: str, age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=age,
        httponly=True,
        secure=config(request).cookie_secure,
        samesite="lax",
        path="/",
    )


def issue_csrf(request: Request, response: Response, *, rotate: bool = False) -> str:
    token = request.cookies.get(CSRF_COOKIE, "")
    if rotate or not valid_csrf(request, token):
        payload = f"{secrets.token_urlsafe(32)}.{timestamp() + 86400}"
        token = f"{payload}.{csrf_signature(request, payload)}"
        set_cookie(request, response, CSRF_COOKIE, token, 86400)
    return token


def require_csrf(request: Request) -> None:
    # Always compare the configured origin, never Host or forwarded request headers.
    if request.headers.get("origin") != config(request).public_url:
        raise HTTPException(403, "Request origin rejected")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not valid_csrf(request, cookie) or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise HTTPException(403, "CSRF validation failed")


def rate_limit(request: Request) -> None:
    limiter = cast(LoginLimiter, request.app.state.login_limiter)
    # Uvicorn resolves forwarded clients only when their immediate peer is trusted.
    limiter.check(request.client.host if request.client else "unknown")


def find_session(request: Request, session: Session) -> LoginSession:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Authentication required")
    account_session = session.get(LoginSession, digest(token))
    if account_session is None or account_session.expires_at <= timestamp():
        raise HTTPException(401, "Authentication required")
    return account_session


def require_admin(request: Request) -> Administrator:
    with Session(storage(request).engine) as session:
        account_session = find_session(request, session)
        admin = session.get(Administrator, account_session.administrator_id)
        if admin is None:
            raise HTTPException(401, "Authentication required")
        session.expunge(admin)
        return admin


CurrentAdmin = Annotated[Administrator, Depends(require_admin)]
WriteProtection = Depends(require_csrf)


class UserResponse(BaseModel):
    username: str


class CsrfResponse(BaseModel):
    csrf_token: str


class SetupStatus(CsrfResponse):
    administrator_exists: bool


class AuthResponse(UserResponse, CsrfResponse):
    pass


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=128)


class CreateAdminInput(LoginInput):
    password: SecretStr = Field(min_length=12, max_length=128)
    password_confirmation: SecretStr = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "CreateAdminInput":
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match")
        return self


class ChangePasswordInput(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)
    password_confirmation: SecretStr = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePasswordInput":
        if self.new_password != self.password_confirmation:
            raise ValueError("Passwords do not match")
        return self


def verify_password(encoded: str, supplied: SecretStr) -> bool:
    try:
        return password_hasher.verify(encoded, supplied.get_secret_value())
    except VerificationError:
        return False


def revoke_sessions(session: Session) -> None:
    """Used by password changes; the recovery CLI can share this transaction helper."""
    session.execute(delete(LoginSession))


def create_session(request: Request, response: Response, session: Session) -> str:
    session.execute(delete(LoginSession).where(LoginSession.expires_at <= timestamp()))
    # Bound persistent session storage. A single administrator keeps the 20 newest logins.
    hashes = session.scalars(
        select(LoginSession.token_hash).order_by(LoginSession.expires_at.desc()).offset(19)
    ).all()
    if hashes:
        session.execute(delete(LoginSession).where(LoginSession.token_hash.in_(hashes)))
    token = secrets.token_urlsafe(32)
    age = config(request).session_days * 86400
    session.add(
        LoginSession(token_hash=digest(token), administrator_id=1, expires_at=timestamp() + age)
    )
    set_cookie(request, response, SESSION_COOKIE, token, age)
    return issue_csrf(request, response, rotate=True)


@router.get("/setup/status", response_model=SetupStatus)
def setup_status(request: Request, response: Response) -> SetupStatus:
    with Session(storage(request).engine) as session:
        exists = session.get(Administrator, 1) is not None
    return SetupStatus(administrator_exists=exists, csrf_token=issue_csrf(request, response))


@router.get("/auth/csrf", response_model=CsrfResponse)
def csrf(request: Request, response: Response) -> CsrfResponse:
    return CsrfResponse(csrf_token=issue_csrf(request, response))


@router.post(
    "/setup/administrator",
    response_model=AuthResponse,
    status_code=201,
    dependencies=[WriteProtection, Depends(rate_limit)],
)
def create_administrator(
    body: CreateAdminInput, request: Request, response: Response
) -> AuthResponse:
    with storage(request).transaction() as session:
        if session.get(Administrator, 1) is not None:
            raise HTTPException(409, "Administrator setup is closed")
        session.add(
            Administrator(
                id=1,
                username=body.username,
                password_hash=password_hasher.hash(body.password.get_secret_value()),
            )
        )
        session.flush()
        token = create_session(request, response, session)
    return AuthResponse(username=body.username, csrf_token=token)


@router.post(
    "/auth/login", response_model=AuthResponse, dependencies=[WriteProtection, Depends(rate_limit)]
)
def login(body: LoginInput, request: Request, response: Response) -> AuthResponse:
    with storage(request).transaction() as session:
        admin = session.get(Administrator, 1)
        encoded = admin.password_hash if admin else cast(str, request.app.state.dummy_password)
        verified = verify_password(encoded, body.password)
        if not verified or admin is None or admin.username != body.username:
            raise HTTPException(401, "Invalid username or password")
        if password_hasher.check_needs_rehash(admin.password_hash):
            admin.password_hash = password_hasher.hash(body.password.get_secret_value())
        token = create_session(request, response, session)
        username = admin.username
    return AuthResponse(username=username, csrf_token=token)


@router.get("/auth/me", response_model=UserResponse)
def me(admin: CurrentAdmin) -> UserResponse:
    return UserResponse(username=admin.username)


@router.post("/auth/logout", status_code=204, dependencies=[WriteProtection])
def logout(request: Request, response: Response) -> None:
    with storage(request).transaction() as session:
        account_session = find_session(request, session)
        session.delete(account_session)
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path="/", httponly=True, secure=config(request).cookie_secure, samesite="lax"
        )


@router.post("/auth/password", status_code=204, dependencies=[WriteProtection, Depends(rate_limit)])
def change_password(body: ChangePasswordInput, request: Request, response: Response) -> None:
    with storage(request).transaction() as session:
        account_session = find_session(request, session)
        admin = session.get(Administrator, account_session.administrator_id)
        if admin is None or not verify_password(admin.password_hash, body.current_password):
            raise HTTPException(401, "Invalid current password")
        admin.password_hash = password_hasher.hash(body.new_password.get_secret_value())
        revoke_sessions(session)
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path="/", httponly=True, secure=config(request).cookie_secure, samesite="lax"
        )
