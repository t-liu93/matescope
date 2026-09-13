import stat
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from matescope.auth import CSRF_COOKIE, SESSION_COOKIE, LoginLimiter, digest
from matescope.config import Settings
from matescope.main import create_app
from matescope.models import Administrator, LoginSession
from matescope.storage import Storage
from sqlalchemy import select, text
from sqlalchemy.orm import Session

ORIGIN = "http://testserver"
PASSWORD = "synthetic-password-123"
NEW_PASSWORD = "different-synthetic-password"


def configuration(directory: Path) -> Settings:
    return Settings(data_dir=str(directory), public_url=ORIGIN, cookie_secure=False)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        yield instance


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def create_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/setup/administrator",
        headers=csrf_headers(client),
        json={"username": "admin", "password": PASSWORD, "password_confirmation": PASSWORD},
    )
    assert response.status_code == 201, response.text


def login(client: TestClient, password: str = PASSWORD) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers=csrf_headers(client),
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200, response.text


def test_setup_and_password_storage(client: TestClient) -> None:
    assert client.get("/api/v1/setup/status").json()["administrator_exists"] is False
    create_admin(client)
    assert client.get("/api/v1/setup/status").json()["administrator_exists"] is True
    me = client.get("/api/v1/auth/me")
    assert me.json() == {"username": "admin"}
    assert me.headers["cache-control"] == "no-store"
    with Session(client.app.state.storage.engine) as session:
        admin = session.get(Administrator, 1)
        assert admin is not None and admin.password_hash.startswith("$argon2id$")
        assert PASSWORD not in admin.password_hash
        saved = session.scalars(select(LoginSession)).one()
        assert saved.token_hash == digest(client.cookies[SESSION_COOKIE])
        assert saved.token_hash != client.cookies[SESSION_COOKIE]
    closed = client.post(
        "/api/v1/setup/administrator",
        headers=csrf_headers(client),
        json={"username": "attacker", "password": PASSWORD, "password_confirmation": PASSWORD},
    )
    assert closed.status_code == 409


def test_concurrent_setup_single_winner(client: TestClient) -> None:
    headers = csrf_headers(client)
    cookies = dict(client.cookies)

    def attempt(index: int) -> int:
        # Independent clients share the same running application and preauth cookie.
        other = TestClient(client.app)
        other.cookies.update(cookies)
        return other.post(
            "/api/v1/setup/administrator",
            headers=headers,
            json={
                "username": f"admin{index}",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
    with Session(client.app.state.storage.engine) as session:
        assert len(session.scalars(select(Administrator)).all()) == 1


@pytest.mark.parametrize(
    "path", ["/auth/login", "/setup/administrator", "/auth/logout", "/auth/password"]
)
def test_csrf_blocks_writes(client: TestClient, path: str) -> None:
    headers = csrf_headers(client)
    body = {
        "username": "admin",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        "current_password": PASSWORD,
        "new_password": PASSWORD,
    }
    for invalid in [
        {},
        {**headers, "Origin": "https://evil.example"},
        {**headers, "X-CSRF-Token": "invalid"},
    ]:
        response = client.post(f"/api/v1{path}", headers=invalid, json=body)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"


def test_unauthorized_and_validation_redaction(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    headers = csrf_headers(client)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 401
    body = {
        "current_password": PASSWORD,
        "new_password": NEW_PASSWORD,
        "password_confirmation": NEW_PASSWORD,
    }
    assert client.post("/api/v1/auth/password", headers=headers, json=body).status_code == 401
    for invalid in ["shortsecret", "s" * 129]:
        response = client.post(
            "/api/v1/setup/administrator",
            headers=headers,
            json={"username": "admin", "password": invalid, "password_confirmation": PASSWORD},
        )
        assert response.status_code == 422
        assert invalid not in response.text and PASSWORD not in response.text


def test_login_rate_limit_and_spoofed_proxy(client: TestClient) -> None:
    headers = csrf_headers(client)
    for index in range(5):
        response = client.post(
            "/api/v1/auth/login",
            headers={**headers, "X-Forwarded-For": f"192.0.2.{index}"},
            json={"username": "admin", "password": "incorrect"},
        )
        assert response.status_code == 401
    response = client.post(
        "/api/v1/auth/login", headers=headers, json={"username": "admin", "password": "incorrect"}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_rate_limiter_global_bound_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    now = [100.0]
    monkeypatch.setattr("matescope.auth.time.monotonic", lambda: now[0])
    limiter = LoginLimiter()
    for index in range(30):
        limiter.check(f"192.0.2.{index}")
    with pytest.raises(HTTPException) as error:
        limiter.check("198.51.100.1")
    assert error.value.status_code == 429
    assert len(limiter.clients) == 30
    now[0] += 61
    limiter.check("198.51.100.1")
    assert len(limiter.clients) == 1


def test_cookie_logout_expiry_and_password_revocation(client: TestClient) -> None:
    create_admin(client)
    old_session = client.cookies[SESSION_COOKIE]
    response = client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set(SESSION_COOKIE, old_session)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    login(client)
    first = client.cookies[SESSION_COOKIE]
    login(client)
    second = client.cookies[SESSION_COOKIE]
    assert first != second
    wrong = client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(client),
        json={
            "current_password": "wrong",
            "new_password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
    )
    assert wrong.status_code == 401
    # Clear limiter to isolate session correctness from independently covered rate limits.
    client.app.state.login_limiter = LoginLimiter()
    response = client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(client),
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
    )
    assert response.status_code == 204
    for token in (first, second):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    denied = client.post(
        "/api/v1/auth/login",
        headers=csrf_headers(client),
        json={"username": "admin", "password": PASSWORD},
    )
    assert denied.status_code == 401
    login(client, NEW_PASSWORD)
    with client.app.state.storage.transaction() as session:
        stored = session.get(LoginSession, digest(client.cookies[SESSION_COOKIE]))
        assert stored is not None
        stored.expires_at = 1
    assert client.get("/api/v1/auth/me").status_code == 401


def test_secure_cookie_and_canonical_origin(tmp_path: Path) -> None:
    config = Settings(data_dir=str(tmp_path), public_url="https://matescope.example")
    with TestClient(create_app(config), base_url="https://matescope.example") as client:
        csrf_response = client.get("/api/v1/auth/csrf")
        cookie = csrf_response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        headers = {"Origin": config.public_url, "X-CSRF-Token": csrf_response.json()["csrf_token"]}
        response = client.post(
            "/api/v1/setup/administrator",
            headers=headers,
            json={
                "username": "admin",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
        )
        assert response.status_code == 201
        session_cookie = response.headers.get_list("set-cookie")[0]
        assert "Max-Age=2592000" in session_cookie and "Secure" in session_cookie
        headers["Origin"] = "https://evil.example"
        headers["X-Forwarded-Host"] = "evil.example"
        headers["X-CSRF-Token"] = response.json()["csrf_token"]
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 403


def test_restart_migration_key_permissions_and_missing_key(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    with TestClient(create_app(config)) as client:
        create_admin(client)
        cookies = dict(client.cookies)
        ciphertext = client.app.state.storage.cipher.encrypt(b"synthetic-secret")
        with client.app.state.storage.engine.connect() as connection:
            assert (
                connection.scalar(text("select version_num from alembic_version"))
                == "0002_settings"
            )
    key = (tmp_path / "encryption.key").read_bytes()
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    for file in ["encryption.key", "matescope.sqlite3"]:
        assert stat.S_IMODE((tmp_path / file).stat().st_mode) == 0o600
    with TestClient(create_app(config)) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get("/api/v1/auth/me").status_code == 200
        assert restarted.app.state.storage.cipher.decrypt(ciphertext) == b"synthetic-secret"
    assert (tmp_path / "encryption.key").read_bytes() == key
    (tmp_path / "encryption.key").unlink()
    with pytest.raises(RuntimeError, match="Encryption key missing"):
        Storage(str(tmp_path)).start()
    assert not (tmp_path / "encryption.key").exists()
    (tmp_path / "encryption.key").write_bytes(Fernet.generate_key())
    with pytest.raises(RuntimeError, match="does not match"):
        Storage(str(tmp_path)).start()


def test_storage_rejects_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "encryption.key").symlink_to(outside)
    with pytest.raises(OSError):
        Storage(str(directory)).start()
    assert outside.read_text() == "untouched"


def test_local_cookie_and_csrf_rotation(client: TestClient) -> None:
    response = client.get("/api/v1/auth/csrf")
    original = response.json()["csrf_token"]
    cookie = response.headers["set-cookie"]
    assert "Secure" not in cookie
    create_admin(client)
    assert client.cookies[CSRF_COOKIE] != original
    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": original,
        },
    )
    assert response.status_code == 403


def test_malformed_csrf_and_expiry(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from matescope.auth import valid_csrf
    from starlette.requests import Request

    request = Request({"type": "http", "app": client.app})
    for token in ["nonce.².signature", "nonce.99999999999.é", "x" * 300]:
        assert valid_csrf(request, token) is False
    headers = csrf_headers(client)
    monkeypatch.setattr("matescope.auth.timestamp", lambda: 99999999999)
    response = client.post(
        "/api/v1/auth/login", headers=headers, json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:invalid",
        "https://example.com:99999",
        "https://example.com/path",
        "https://user:pass@example.com",
    ],
)
def test_invalid_public_url_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        Settings(public_url=url)


def test_default_https_port_normalized() -> None:
    assert Settings(public_url="https://Example.com:443/").public_url == "https://example.com"
    with pytest.raises(ValueError, match="secure cookies"):
        Settings(public_url="https://example.com", cookie_secure=False)
    with pytest.raises(ValueError, match="trusted_proxies"):
        Settings(trusted_proxies="*")
