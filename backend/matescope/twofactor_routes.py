"""Optional two-factor authentication and administrator recovery-code management."""

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import (
    CHALLENGE_COOKIE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthResponse,
    WriteProtection,
    clear_cookie,
    create_session,
    digest,
    find_session,
    rate_limit,
    storage,
    timestamp,
    verify_password,
)
from .models import Administrator, FactorEnrollment, LoginChallenge, LoginSession, RecoveryCode
from .twofactor import (
    FactorProof,
    decrypt_seed,
    encrypt_seed,
    new_recovery_codes,
    revoke_auth,
    state,
    verify_factor,
)

router = APIRouter(prefix="/api/v1/auth/two-factor", tags=["authentication"])


class FactorStatus(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class PasswordInput(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)


class EnrollmentResponse(BaseModel):
    secret: str
    provisioning_uri: str
    expires_in: int = 600


class ConfirmInput(PasswordInput):
    code: SecretStr = Field(min_length=6, max_length=6)


class ManagementInput(PasswordInput):
    proof: FactorProof


class RecoveryCodesResponse(AuthResponse):
    recovery_codes: list[str]


def reauthenticate(request: Request, session: Session, password: SecretStr) -> Administrator:
    account_session = find_session(request, session)
    admin = session.get_one(Administrator, account_session.administrator_id)
    if not verify_password(admin.password_hash, password):
        raise HTTPException(401, "Invalid current password")
    return admin


@router.get("", response_model=FactorStatus)
def status(request: Request) -> FactorStatus:
    with Session(storage(request).engine) as session:
        raw = request.cookies.get(SESSION_COOKIE, "")
        account_session = (
            session.get(LoginSession, digest(raw)) if raw and len(raw) <= 128 else None
        )
        if account_session is None or account_session.expires_at <= timestamp():
            raise HTTPException(401, "Authentication required")
        # This endpoint deliberately projects metadata only, never loading the secret ciphertext.
        record = session.execute(
            select(
                Administrator.auth_version,
                Administrator.totp_enabled_at,
                Administrator.totp_seed.is_not(None),
            ).where(Administrator.id == account_session.administrator_id)
        ).one_or_none()
        if record is None or record[0] != account_session.auth_version:
            raise HTTPException(401, "Authentication required")
        if (record[1] is not None) != record[2]:
            raise HTTPException(503, "Two-factor configuration is invalid; use local recovery")
        count = session.scalar(select(func.count()).select_from(RecoveryCode)) or 0
        return FactorStatus(enabled=record[1] is not None, recovery_codes_remaining=count)


@router.post(
    "/verify", response_model=AuthResponse, dependencies=[WriteProtection, Depends(rate_limit)]
)
def verify(body: FactorProof, request: Request, response: Response) -> AuthResponse:
    with storage(request).transaction() as session:
        raw = request.cookies.get(CHALLENGE_COOKIE, "")
        challenge = session.get(LoginChallenge, digest(raw)) if raw and len(raw) <= 128 else None
        admin = session.get(Administrator, 1)
        if (
            challenge is None
            or admin is None
            or challenge.expires_at <= timestamp()
            or challenge.auth_version != admin.auth_version
            or challenge.failures >= 5
        ):
            raise HTTPException(401, "Two-factor challenge expired or invalid")
        state(admin, session, storage(request).cipher)
        if admin.totp_seed is None:
            raise HTTPException(401, "Two-factor challenge expired or invalid")
        error = verify_factor(session, admin, storage(request).cipher, body, timestamp())
        if error is not None:
            if error.status_code == 401:
                challenge.failures += 1
        else:
            session.delete(challenge)
            token = create_session(request, response, session)
            result = AuthResponse(username=admin.username, csrf_token=token)
    if error is not None:
        raise error
    clear_cookie(request, response, CHALLENGE_COOKIE)
    return result


@router.post("/cancel", status_code=204, dependencies=[WriteProtection])
def cancel(request: Request, response: Response) -> None:
    with storage(request).transaction() as session:
        session.execute(
            delete(LoginChallenge).where(
                LoginChallenge.token_hash == digest(request.cookies.get(CHALLENGE_COOKIE, ""))
            )
        )
    clear_cookie(request, response, CHALLENGE_COOKIE)


@router.post(
    "/enroll",
    response_model=EnrollmentResponse,
    dependencies=[WriteProtection, Depends(rate_limit)],
)
def enroll(body: PasswordInput, request: Request) -> EnrollmentResponse:
    with storage(request).transaction() as session:
        admin = reauthenticate(request, session, body.current_password)
        if admin.totp_seed is not None:
            raise HTTPException(409, "Two-factor authentication is already enabled")
        seed = pyotp.random_base32()
        session.execute(delete(FactorEnrollment))
        session.add(
            FactorEnrollment(
                administrator_id=1,
                session_hash=digest(request.cookies[SESSION_COOKIE]),
                auth_version=admin.auth_version,
                seed=encrypt_seed(storage(request).cipher, seed),
                expires_at=timestamp() + 600,
            )
        )
        uri = pyotp.TOTP(seed).provisioning_uri(admin.username, issuer_name="MateScope")
    return EnrollmentResponse(secret=seed, provisioning_uri=uri)


@router.post(
    "/confirm",
    response_model=RecoveryCodesResponse,
    dependencies=[WriteProtection, Depends(rate_limit)],
)
def confirm(body: ConfirmInput, request: Request, response: Response) -> RecoveryCodesResponse:
    with storage(request).transaction() as session:
        admin = reauthenticate(request, session, body.current_password)
        enrollment = session.get(FactorEnrollment, 1)
        if (
            admin.totp_seed is not None
            or enrollment is None
            or enrollment.expires_at <= timestamp()
            or enrollment.auth_version != admin.auth_version
            or enrollment.session_hash != digest(request.cookies.get(SESSION_COOKIE, ""))
        ):
            raise HTTPException(409, "Two-factor enrollment expired or invalid")
        try:
            seed = decrypt_seed(storage(request).cipher, enrollment.seed)
        except ValueError:
            raise HTTPException(
                503, "Two-factor configuration is invalid; use local recovery"
            ) from None
        error = verify_factor(
            session,
            admin,
            storage(request).cipher,
            FactorProof(method="totp", code=body.code),
            timestamp(),
            enrollment_seed=seed,
        )
        if error is None:
            admin.totp_seed = enrollment.seed
            admin.totp_enabled_at = timestamp()
            codes = new_recovery_codes(session)
            revoke_auth(session, admin)
            result = RecoveryCodesResponse(
                username=admin.username,
                csrf_token=create_session(request, response, session),
                recovery_codes=codes,
            )
    if error is not None:
        raise error
    clear_cookie(request, response, CHALLENGE_COOKIE)
    return result


@router.post("/disable", status_code=204, dependencies=[WriteProtection, Depends(rate_limit)])
def disable(body: ManagementInput, request: Request, response: Response) -> None:
    with storage(request).transaction() as session:
        admin = reauthenticate(request, session, body.current_password)
        if admin.totp_seed is None:
            raise HTTPException(409, "Two-factor authentication is not enabled")
        error = verify_factor(session, admin, storage(request).cipher, body.proof, timestamp())
        if error is None:
            admin.totp_seed = None
            admin.totp_enabled_at = None
            admin.totp_last_step = -1
            admin.totp_used_codes = "[]"
            session.execute(delete(RecoveryCode))
            revoke_auth(session, admin)
    if error is not None:
        raise error
    for name in (SESSION_COOKIE, CSRF_COOKIE, CHALLENGE_COOKIE):
        clear_cookie(request, response, name)


@router.post(
    "/recovery-codes",
    response_model=RecoveryCodesResponse,
    dependencies=[WriteProtection, Depends(rate_limit)],
)
def regenerate(
    body: ManagementInput, request: Request, response: Response
) -> RecoveryCodesResponse:
    with storage(request).transaction() as session:
        admin = reauthenticate(request, session, body.current_password)
        if admin.totp_seed is None:
            raise HTTPException(409, "Two-factor authentication is not enabled")
        error = verify_factor(session, admin, storage(request).cipher, body.proof, timestamp())
        if error is None:
            codes = new_recovery_codes(session)
            revoke_auth(session, admin)
            result = RecoveryCodesResponse(
                username=admin.username,
                csrf_token=create_session(request, response, session),
                recovery_codes=codes,
            )
    if error is not None:
        raise error
    clear_cookie(request, response, CHALLENGE_COOKIE)
    return result
