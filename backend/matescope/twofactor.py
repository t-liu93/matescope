"""Atomic factor operations; callers hold Storage.transaction's SQLite writer lock."""

import hashlib
import hmac
import json
import re
import secrets
from typing import Literal

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Administrator, FactorEnrollment, LoginChallenge, LoginSession, RecoveryCode

SEED_PREFIX = b"matescope-totp-v1:"


class FactorProof(BaseModel):
    method: Literal["totp", "recovery_code"]
    code: SecretStr = Field(min_length=1, max_length=64)


def decrypt_seed(cipher: Fernet, token: str) -> str:
    try:
        raw = cipher.decrypt(token.encode())
        if not raw.startswith(SEED_PREFIX):
            raise ValueError
        seed = raw[len(SEED_PREFIX) :].decode("ascii")
        if re.fullmatch(r"[A-Z2-7]{32}", seed) is None:
            raise ValueError
        return seed
    except (InvalidToken, UnicodeError, ValueError):
        raise ValueError("Invalid two-factor configuration") from None


def encrypt_seed(cipher: Fernet, seed: str) -> str:
    return cipher.encrypt(SEED_PREFIX + seed.encode()).decode()


def state(
    admin: Administrator, session: Session, cipher: Fernet
) -> tuple[list[int], list[list[str | int]]]:
    try:
        if (admin.totp_seed is None) != (admin.totp_enabled_at is None):
            raise ValueError
        if admin.auth_version < 0 or admin.totp_last_step < -1:
            raise ValueError
        if admin.totp_seed is not None:
            if (
                admin.totp_enabled_at is None
                or admin.totp_enabled_at <= 0
                or admin.totp_last_step < 0
            ):
                raise ValueError
            decrypt_seed(cipher, admin.totp_seed)
        elif admin.totp_last_step != -1 or session.scalar(select(RecoveryCode.code_hash).limit(1)):
            raise ValueError
        failures = json.loads(admin.factor_failures)
        used = json.loads(admin.totp_used_codes)
        if (
            not isinstance(failures, list)
            or len(failures) > 20
            or any(type(t) is not int or t < 0 for t in failures)
        ):
            raise ValueError
        if not isinstance(used, list) or len(used) > 6:
            raise ValueError
        for record in used:
            if (
                not isinstance(record, list)
                or len(record) != 2
                or not isinstance(record[0], str)
                or re.fullmatch(r"[0-9a-f]{64}", record[0]) is None
                or type(record[1]) is not int
                or record[1] < 0
            ):
                raise ValueError
        if (admin.totp_seed is None and used) or len({record[0] for record in used}) != len(used):
            raise ValueError
        codes = session.scalars(select(RecoveryCode).limit(11)).all()
        if len(codes) > 10 or any(
            code.administrator_id != 1 or re.fullmatch(r"[0-9a-f]{64}", code.code_hash) is None
            for code in codes
        ):
            raise ValueError
        return failures, used
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            503, "Two-factor configuration is invalid; use local recovery"
        ) from None


def revoke_auth(session: Session, admin: Administrator) -> None:
    admin.auth_version += 1
    for model in (LoginSession, LoginChallenge, FactorEnrollment):
        session.execute(delete(model))


def recovery_hash(code: str) -> str | None:
    # Exactly 32 hex digits or eight groups of four; no permissive punctuation stripping.
    if re.fullmatch(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{4}(?:-[0-9a-fA-F]{4}){7})", code) is None:
        return None
    return hashlib.sha256(
        b"matescope-recovery-v1:" + code.replace("-", "").lower().encode()
    ).hexdigest()


def new_recovery_codes(session: Session) -> list[str]:
    session.execute(delete(RecoveryCode))
    codes = []
    for _ in range(10):
        raw = secrets.token_hex(16)
        formatted = "-".join(raw[i : i + 4] for i in range(0, 32, 4))
        codes.append(formatted)
        session.add(RecoveryCode(code_hash=recovery_hash(formatted), administrator_id=1))
    return codes


def verify_factor(
    session: Session,
    admin: Administrator,
    cipher: Fernet,
    proof: FactorProof | None,
    now: int,
    *,
    enrollment_seed: str | None = None,
) -> HTTPException | None:
    """Return errors, never raise after recording a failure: caller must commit first."""
    failures, used = state(admin, session, cipher)
    failures = [t for t in failures if t > now - 900]
    recent = [t for t in failures if t > now - 60]
    waits = []
    if len(recent) >= 5:
        waits.append(min(recent) + 60 - now)
    if len(failures) >= 20:
        waits.append(min(failures) + 900 - now)
    if waits:
        return HTTPException(
            429,
            "Too many verification attempts; retry later",
            headers={"Retry-After": str(max(waits))},
        )
    valid = False
    if proof is not None:
        code = proof.code.get_secret_value()
        if (
            proof.method == "recovery_code"
            and enrollment_seed is None
            and admin.totp_seed is not None
        ):
            hashed = recovery_hash(code)
            record = session.get(RecoveryCode, hashed) if hashed else None
            if record is not None and record.administrator_id == admin.id:
                session.delete(record)
                valid = True
        elif proof.method == "totp" and re.fullmatch(r"[0-9]{6}", code):
            seed = enrollment_seed or (
                decrypt_seed(cipher, admin.totp_seed) if admin.totp_seed else None
            )
            if seed:
                totp = pyotp.TOTP(seed)
                step = now // 30
                matches = [
                    s
                    for s in range(max(0, step - 1), step + 2)
                    if hmac.compare_digest(totp.at(s * 30), code)
                ]
                hashed_code = hmac.new(
                    seed.encode(), b"matescope-otp-v1:" + code.encode(), hashlib.sha256
                ).hexdigest()
                active_used = [r for r in used if int(r[1]) > now]
                if (
                    matches
                    and max(matches) > admin.totp_last_step
                    and all(r[0] != hashed_code for r in active_used)
                ):
                    admin.totp_last_step = max(matches)
                    admin.totp_used_codes = json.dumps(
                        [*active_used, [hashed_code, (step + 3) * 30]]
                    )
                    valid = True
    if not valid:
        admin.factor_failures = json.dumps([*failures, now])
        return HTTPException(401, "Invalid or already used verification code")
    admin.factor_failures = json.dumps(failures)
    return None
