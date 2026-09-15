"""Synthetic security-boundary and concurrency checks for optional two-factor auth."""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from matescope import auth, twofactor_routes
from matescope.auth import CHALLENGE_COOKIE, SESSION_COOKIE, LoginLimiter, digest
from matescope.main import create_app
from matescope.models import (
    Administrator,
    FactorEnrollment,
    LoginChallenge,
    LoginSession,
    RecoveryCode,
)
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_auth import NEW_PASSWORD, PASSWORD, configuration, create_admin, csrf_headers


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    clock = [1_800_000_000]
    monkeypatch.setattr(auth, "timestamp", lambda: clock[0])
    monkeypatch.setattr(twofactor_routes, "timestamp", lambda: clock[0])
    return clock


@pytest.fixture
def client(tmp_path: Path, now: list[int]) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        create_admin(instance)
        yield instance


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    # Isolate the persistent account limiter; source resource limiter has separate coverage.
    client.app.state.login_limiter = LoginLimiter()
    return client.post("/api/v1/auth/" + path, json=body, headers=csrf_headers(client))


def enable(client: TestClient, now: list[int]) -> tuple[str, list[str]]:
    enrollment = post(client, "two-factor/enroll", {"current_password": PASSWORD})
    assert enrollment.status_code == 200, enrollment.text
    seed = enrollment.json()["secret"]
    response = post(
        client,
        "two-factor/confirm",
        {
            "current_password": PASSWORD,
            "code": pyotp.TOTP(seed).at(now[0]),
        },
    )
    assert response.status_code == 200, response.text
    return seed, response.json()["recovery_codes"]


def challenge(client: TestClient, password: str = PASSWORD) -> Any:
    response = post(client, "login", {"username": "admin", "password": password})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "two_factor_required"
    assert SESSION_COOKIE not in client.cookies
    return response


def proof(code: str, method: str = "totp") -> dict[str, str]:
    return {"method": method, "code": code}


def test_enrollment_encryption_rotation_and_metadata(client: TestClient, now: list[int]) -> None:
    old_cookie = client.cookies[SESSION_COOKIE]
    seed, codes = enable(client, now)
    assert len(set(codes)) == 10
    assert client.cookies[SESSION_COOKIE] != old_cookie
    with Session(client.app.state.storage.engine) as session:
        admin = session.get_one(Administrator, 1)
        assert admin.totp_seed and seed not in admin.totp_seed
        assert admin.auth_version == 1
        assert admin.totp_last_step == now[0] // 30
        assert pyotp.TOTP(seed).at(now[0]) not in admin.totp_used_codes
        assert session.get(LoginSession, digest(old_cookie)) is None
        assert session.get(FactorEnrollment, 1) is None
        hashes = session.scalars(select(RecoveryCode.code_hash)).all()
        assert len(hashes) == 10 and not set(hashes).intersection(codes)
    statements: list[str] = []

    def capture(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        statements.append(statement)

    event.listen(client.app.state.storage.engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/v1/auth/two-factor")
    finally:
        event.remove(client.app.state.storage.engine, "before_cursor_execute", capture)
    assert response.json() == {"enabled": True, "recovery_codes_remaining": 10}
    assert response.headers["cache-control"] == "no-store"
    assert not any("administrator.totp_seed," in sql for sql in statements)
    assert not any("recovery_code.code_hash" in sql for sql in statements)
    assert post(client, "two-factor/enroll", {"current_password": PASSWORD}).status_code == 409


@pytest.mark.parametrize(
    "path",
    [
        "/auth/me",
        "/auth/two-factor",
        "/settings",
        "/vehicles",
        "/diagnostics",
    ],
)
def test_password_only_challenge_has_no_permission(
    client: TestClient, now: list[int], path: str
) -> None:
    enable(client, now)
    response = challenge(client)
    assert response.json()["expires_in"] == 300
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=lax" in response.headers["set-cookie"]
    )
    response = client.get("/api/v1" + path)
    assert response.status_code == 401
    for endpoint in (
        "password",
        "two-factor/enroll",
        "two-factor/disable",
        "two-factor/recovery-codes",
    ):
        body: dict[str, Any] = {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
            "proof": proof("000000"),
        }
        assert post(client, endpoint, body).status_code == 401


def test_totp_replay_cross_endpoint_and_recovery_single_use(
    client: TestClient, now: list[int]
) -> None:
    seed, codes = enable(client, now)
    challenge(client)
    reused = post(client, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0])))
    assert reused.status_code == 401
    now[0] += 30
    assert post(client, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0]))).status_code == 200
    assert CHALLENGE_COOKIE not in client.cookies
    replay = post(
        client,
        "two-factor/recovery-codes",
        {"current_password": PASSWORD, "proof": proof(pyotp.TOTP(seed).at(now[0]))},
    )
    assert replay.status_code == 401
    challenge(client)
    assert post(client, "two-factor/verify", proof(codes[0], "recovery_code")).status_code == 200
    assert client.get("/api/v1/auth/two-factor").json() == {
        "enabled": True,
        "recovery_codes_remaining": 9,
    }
    challenge(client)
    assert post(client, "two-factor/verify", proof(codes[0], "recovery_code")).status_code == 401


@pytest.mark.parametrize("kind", ["totp", "recovery_code", "challenge"])
def test_concurrent_only_one_success(client: TestClient, now: list[int], kind: str) -> None:
    seed, codes = enable(client, now)
    now[0] += 30
    challenge(client)
    if kind == "challenge":
        bodies = [proof(code, "recovery_code") for code in codes[:2]]
        cookies = [dict(client.cookies)] * 2
    else:
        first = dict(client.cookies)
        # Independent challenge: avoid replacing the first browser's cookie.
        other = TestClient(client.app)
        challenge(other)
        cookies = [first, dict(other.cookies)]
        bodies = [
            proof(pyotp.TOTP(seed).at(now[0])) if kind == "totp" else proof(codes[0], kind)
        ] * 2
    client.app.state.login_limiter = LoginLimiter()

    def attempt(index: int) -> int:
        other = TestClient(client.app)
        other.cookies.update(cookies[index])
        return other.post(
            "/api/v1/auth/two-factor/verify", headers=csrf_headers(other), json=bodies[index]
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, range(2)))
    assert sorted(statuses) == [200, 401]


def test_failures_persist_restart_and_cross_management(tmp_path: Path, now: list[int]) -> None:
    settings = configuration(tmp_path)
    with TestClient(create_app(settings)) as first:
        create_admin(first)
        seed, _ = enable(first, now)
        session_cookie = dict(first.cookies)
        for _ in range(3):
            assert (
                post(
                    first,
                    "two-factor/recovery-codes",
                    {"current_password": PASSWORD, "proof": proof("invalid")},
                ).status_code
                == 401
            )
        challenger = TestClient(first.app)
        challenge(challenger)
        for _ in range(2):
            assert post(challenger, "two-factor/verify", proof("invalid")).status_code == 401
        saved = dict(challenger.cookies)
        with Session(first.app.state.storage.engine) as session:
            assert len(json.loads(session.get_one(Administrator, 1).factor_failures)) == 5
    with TestClient(create_app(settings)) as second:
        second.cookies.update(saved)
        result = post(second, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0] + 30)))
        assert result.status_code == 429 and result.headers["retry-after"] == "60"
        challenge(second)
        assert post(second, "two-factor/verify", proof("invalid")).status_code == 429
        # Existing full browser also shares the persistent account budget.
        second.cookies.update(session_cookie)
        assert (
            post(
                second,
                "two-factor/disable",
                {"current_password": PASSWORD, "proof": proof("invalid")},
            ).status_code
            == 429
        )
        now[0] += 61
        assert (
            post(
                second,
                "two-factor/disable",
                {"current_password": PASSWORD, "proof": proof(pyotp.TOTP(seed).at(now[0]))},
            ).status_code
            == 204
        )


def test_long_failure_budget_and_challenge_five_failures(
    client: TestClient, now: list[int]
) -> None:
    enable(client, now)
    for batch in range(4):
        challenge(client)
        for _ in range(5):
            assert post(client, "two-factor/verify", proof("invalid")).status_code == 401
        assert (
            "challenge"
            in post(client, "two-factor/verify", proof("invalid")).json()["detail"].lower()
        )
        if batch < 3:
            now[0] += 61
    now[0] += 61
    challenge(client)
    blocked = post(client, "two-factor/verify", proof("invalid"))
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) == 656
    now[0] += 657
    challenge(client)
    assert post(client, "two-factor/verify", proof("invalid")).status_code == 401


@pytest.mark.parametrize("operation", ["disable", "recovery-codes", "password"])
def test_sensitive_changes_revoke_all_old_state(
    client: TestClient, now: list[int], operation: str
) -> None:
    _, codes = enable(client, now)
    old_full = dict(client.cookies)
    other = TestClient(client.app)
    challenge(other)
    old_challenge = dict(other.cookies)
    if operation == "password":
        result = post(
            client,
            "password",
            {
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "password_confirmation": NEW_PASSWORD,
                "proof": proof(codes[0], "recovery_code"),
            },
        )
    else:
        result = post(
            client,
            "two-factor/" + operation,
            {"current_password": PASSWORD, "proof": proof(codes[0], "recovery_code")},
        )
    assert result.status_code == (200 if operation == "recovery-codes" else 204), result.text
    with Session(client.app.state.storage.engine) as session:
        admin = session.get_one(Administrator, 1)
        assert admin.auth_version == 2
        assert not session.scalars(select(LoginChallenge)).all()
        assert not session.scalars(select(FactorEnrollment)).all()
        assert len(session.scalars(select(LoginSession)).all()) == (
            1 if operation == "recovery-codes" else 0
        )
    stale = TestClient(client.app)
    stale.cookies.update(old_full)
    assert stale.get("/api/v1/auth/me").status_code == 401
    stale.cookies.clear()
    stale.cookies.update(old_challenge)
    assert post(stale, "two-factor/verify", proof(codes[1], "recovery_code")).status_code == 401
    if operation == "disable":
        response = post(client, "login", {"username": "admin", "password": PASSWORD})
        assert response.json()["status"] == "authenticated"
    else:
        challenge(client, NEW_PASSWORD if operation == "password" else PASSWORD)
        expected = 401 if operation == "recovery-codes" else 200
        assert (
            post(client, "two-factor/verify", proof(codes[1], "recovery_code")).status_code
            == expected
        )


def test_enrollment_session_binding_expiry_and_password(client: TestClient, now: list[int]) -> None:
    assert post(client, "two-factor/enroll", {"current_password": "wrong"}).status_code == 401
    data = post(client, "two-factor/enroll", {"current_password": PASSWORD}).json()
    other = TestClient(client.app)
    assert post(other, "login", {"username": "admin", "password": PASSWORD}).status_code == 200
    body = {"current_password": PASSWORD, "code": pyotp.TOTP(data["secret"]).at(now[0])}
    assert post(other, "two-factor/confirm", body).status_code == 409
    assert (
        post(client, "two-factor/confirm", {**body, "current_password": "wrong"}).status_code == 401
    )
    now[0] += 600
    assert post(client, "two-factor/confirm", body).status_code == 409
    assert client.get("/api/v1/auth/two-factor").json()["enabled"] is False


@pytest.mark.parametrize(
    "path", ["verify", "cancel", "enroll", "confirm", "disable", "recovery-codes"]
)
def test_csrf_origin_redaction(client: TestClient, now: list[int], path: str) -> None:
    seed, codes = enable(client, now)
    body = {
        "current_password": PASSWORD,
        "code": "secret-invalid-value",
        "method": "totp",
        "proof": proof(codes[0], "recovery_code"),
    }
    for headers in ({}, {**csrf_headers(client), "Origin": "https://evil.example"}):
        result = client.post("/api/v1/auth/two-factor/" + path, json=body, headers=headers)
        assert result.status_code == 403
        assert result.headers["cache-control"] == "no-store"
        assert all(secret not in result.text for secret in (seed, codes[0], PASSWORD, body["code"]))
    malformed = post(client, "two-factor/verify", {"method": "totp", "code": "SECRET" * 100})
    assert malformed.status_code == 422 and "SECRET" not in malformed.text


def test_challenge_expiry_cancel_and_storage_cap(client: TestClient, now: list[int]) -> None:
    _, codes = enable(client, now)
    for _ in range(25):
        other = TestClient(client.app)
        challenge(other)
    with Session(client.app.state.storage.engine) as session:
        assert len(session.scalars(select(LoginChallenge)).all()) == 20
    challenge(client)
    assert post(client, "two-factor/cancel").status_code == 204
    assert CHALLENGE_COOKIE not in client.cookies
    assert post(client, "two-factor/verify", proof(codes[0], "recovery_code")).status_code == 401
    challenge(client)
    now[0] += 300
    assert post(client, "two-factor/verify", proof(codes[0], "recovery_code")).status_code == 401


@pytest.mark.parametrize(
    "corruption", ["missing_seed", "bad_ciphertext", "bad_failures", "bad_replay"]
)
def test_corrupt_factor_state_fails_closed(
    client: TestClient, now: list[int], corruption: str
) -> None:
    enable(client, now)
    with client.app.state.storage.transaction() as session:
        admin = session.get_one(Administrator, 1)
        if corruption == "missing_seed":
            admin.totp_seed = None
        elif corruption == "bad_ciphertext":
            admin.totp_seed = "corrupt"
        elif corruption == "bad_failures":
            admin.factor_failures = "{}"
        else:
            admin.totp_used_codes = '[["bad", 1]]'
    response = post(client, "login", {"username": "admin", "password": PASSWORD})
    assert response.status_code == 503
    assert client.get("/api/v1/auth/me").status_code == 503


def test_repeated_value_in_adjacent_steps_is_rejected(
    client: TestClient, now: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, _ = enable(client, now)
    used_code = pyotp.TOTP(seed).at(now[0])
    monkeypatch.setattr(pyotp.TOTP, "at", lambda self, for_time, counter_offset=0: used_code)
    now[0] += 30
    challenge(client)
    assert post(client, "two-factor/verify", proof(used_code)).status_code == 401


def test_login_and_password_change_race_cannot_leave_old_session(
    client: TestClient, now: list[int]
) -> None:
    _, codes = enable(client, now)
    full = dict(client.cookies)
    other = TestClient(client.app)
    challenge(other)
    pending = dict(other.cookies)
    client.app.state.login_limiter = LoginLimiter()

    def run(index: int) -> int:
        browser = TestClient(client.app)
        browser.cookies.update(full if index == 0 else pending)
        path = "/api/v1/auth/password" if index == 0 else "/api/v1/auth/two-factor/verify"
        body = (
            {
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "password_confirmation": NEW_PASSWORD,
                "proof": proof(codes[0], "recovery_code"),
            }
            if index == 0
            else proof(codes[1], "recovery_code")
        )
        return browser.post(path, headers=csrf_headers(browser), json=body).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == 204 and results[1] in (200, 401)
    with Session(client.app.state.storage.engine) as session:
        assert not session.scalars(select(LoginSession)).all()
        assert not session.scalars(select(LoginChallenge)).all()


def test_enrollment_replacement_and_failed_confirm_share_budget(
    client: TestClient, now: list[int]
) -> None:
    for _ in range(5):
        assert post(client, "two-factor/enroll", {"current_password": PASSWORD}).status_code == 200
        assert (
            post(
                client, "two-factor/confirm", {"current_password": PASSWORD, "code": "abcdef"}
            ).status_code
            == 401
        )
    data = post(client, "two-factor/enroll", {"current_password": PASSWORD}).json()
    assert (
        post(
            client,
            "two-factor/confirm",
            {"current_password": PASSWORD, "code": pyotp.TOTP(data["secret"]).at(now[0])},
        ).status_code
        == 429
    )
    now[0] += 61
    assert (
        post(
            client,
            "two-factor/confirm",
            {"current_password": PASSWORD, "code": pyotp.TOTP(data["secret"]).at(now[0])},
        ).status_code
        == 200
    )


def test_totp_window_and_persisted_watermark(tmp_path: Path, now: list[int]) -> None:
    settings = configuration(tmp_path)
    with TestClient(create_app(settings)) as browser:
        create_admin(browser)
        seed, _ = enable(browser, now)
        now[0] += 90
        challenge(browser)
        assert (
            post(browser, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0] + 60))).status_code
            == 401
        )
        assert (
            post(browser, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0] - 30))).status_code
            == 200
        )
    with TestClient(create_app(settings)) as browser:
        challenge(browser)
        assert (
            post(browser, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0] - 30))).status_code
            == 401
        )
        assert (
            post(browser, "two-factor/verify", proof(pyotp.TOTP(seed).at(now[0] + 30))).status_code
            == 200
        )


def test_challenge_cannot_use_settings_writes_or_connection_tests(
    client: TestClient, now: list[int]
) -> None:
    enable(client, now)
    challenge(client)
    for path in ("postgresql/test", "mqtt/test", "smtp/test"):
        response = client.post("/api/v1/settings/" + path, headers=csrf_headers(client))
        assert response.status_code == 401
    response = client.put(
        "/api/v1/settings/preferences",
        headers=csrf_headers(client),
        json={
            "language": "en",
            "timezone": "UTC",
            "distance_unit": "km",
            "temperature_unit": "celsius",
        },
    )
    assert response.status_code == 401
