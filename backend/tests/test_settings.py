import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from matescope.main import create_app
from matescope.models import ApplicationSettings
from sqlalchemy.orm import Session
from test_auth import configuration, create_admin, csrf_headers, login


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        yield instance


def save(client: TestClient, section: str, body: dict[str, object]) -> dict[str, object]:
    response = client.put(f"/api/v1/settings/{section}", headers=csrf_headers(client), json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.mark.parametrize("section", ["preferences", "postgresql", "mqtt", "smtp", "onboarding"])
def test_access_and_csrf(client: TestClient, section: str) -> None:  # noqa: F811
    assert client.get("/api/v1/settings").status_code == 401
    assert (
        client.put(f"/api/v1/settings/{section}", headers=csrf_headers(client), json={}).status_code
        == 401
    )
    create_admin(client)
    headers = csrf_headers(client)
    for invalid in (
        {},
        {**headers, "Origin": "https://evil.example"},
        {**headers, "X-CSRF-Token": "wrong"},
    ):
        response = client.put(f"/api/v1/settings/{section}", headers=invalid, json={})
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("section", ["postgresql", "mqtt", "smtp"])
def test_secret_lifecycle(client: TestClient, section: str) -> None:  # noqa: F811
    create_admin(client)
    secret = "synthetic-connection-secret"
    body = {
        "host": "synthetic.example",
        "enabled": True,
        "password": {"action": "replace", "value": secret},
    }
    response = save(client, section, body)
    current = response[section]
    assert isinstance(current, dict)
    assert current["password_set"] and current["version"] == 1
    assert current["status"] == "unverified" and current["test_available"] is False
    assert secret not in json.dumps(response) and "password" not in current
    with Session(client.app.state.storage.engine) as session:
        record = session.get(ApplicationSettings, 1)
        assert record is not None
        ciphertext = json.loads(record.encrypted_passwords)[section]
        assert secret not in record.encrypted_passwords + record.configuration
        assert client.app.state.storage.cipher.decrypt(ciphertext.encode()).decode() == secret
    database = Path(client.app.state.storage.directory) / "matescope.sqlite3"
    assert secret.encode() not in database.read_bytes()
    body["password"] = {"action": "retain"}
    save(client, section, body)
    with Session(client.app.state.storage.engine) as session:
        record = session.get(ApplicationSettings, 1)
        assert record is not None and json.loads(record.encrypted_passwords)[section] == ciphertext
    body["password"] = {"action": "replace", "value": "replacement-synthetic-secret"}
    save(client, section, body)
    body["password"] = {"action": "clear"}
    current = save(client, section, body)[section]
    assert isinstance(current, dict) and not current["password_set"] and current["version"] == 4
    with Session(client.app.state.storage.engine) as session:
        record = session.get(ApplicationSettings, 1)
        assert record is not None and section not in json.loads(record.encrypted_passwords)


@pytest.mark.parametrize(
    "section,body",
    [
        ("preferences", {"timezone": "not/a-zone"}),
        ("preferences", {"timezone": "/etc/passwd"}),
        ("preferences", {"language": "fr"}),
        ("preferences", {"tile_url": "javascript:alert(1)"}),
        ("preferences", {"tile_url": "https://user:secret@example.com/{z}/{x}/{y}"}),
        ("postgresql", {"port": 0}),
        ("mqtt", {"port": 65536}),
        ("postgresql", {"sslmode": "unsafe"}),
        ("smtp", {"tls_mode": "unsafe"}),
        ("smtp", {"sender": "sender@example.com\r\nBcc:other@example.com"}),
        ("mqtt", {"topic_prefix": "teslamate/#"}),
        ("mqtt", {"enabled": True, "skipped": True}),
        ("postgresql", {"password": {"action": "replace"}}),
        ("postgresql", {"password": {"action": "replace", "value": ""}}),
        ("postgresql", {"password": {"action": "retain", "value": "synthetic-secret"}}),
        ("postgresql", {"password": {"action": "clear", "value": "synthetic-secret"}}),
    ],
)
def test_invalid_fields(client: TestClient, section: str, body: dict[str, object]) -> None:  # noqa: F811
    create_admin(client)
    response = client.put(f"/api/v1/settings/{section}", headers=csrf_headers(client), json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request fields"}


def test_progress_and_settings_survive_logout_restart(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    with TestClient(create_app(config)) as client:
        create_admin(client)
        assert client.get("/api/v1/settings").json()["preferences"]["saved"] is False
        save(client, "preferences", {"language": "zh", "timezone": "Europe/Amsterdam"})
        save(client, "onboarding", {"step": "mqtt"})
        for service in ("postgresql", "mqtt", "smtp"):
            assert save(client, service, {"skipped": True})[service]["status"] == "skipped"
        cookies = dict(client.cookies)
    with TestClient(create_app(config)) as client:
        client.cookies.update(cookies)
        data = client.get("/api/v1/settings").json()
        assert data["onboarding"] == {"step": "mqtt", "completed": False}
        assert data["preferences"]["saved"] is True
        assert data["preferences"]["timezone"] == "Europe/Amsterdam"
        complete = save(client, "onboarding", {"step": "review", "completed": True})
        assert complete["onboarding"]["completed"] is True
        client.post("/api/v1/auth/logout", headers=csrf_headers(client))
        assert client.get("/api/v1/settings").status_code == 401
        login(client)
        assert client.get("/api/v1/settings").json() == complete
        # Later editing uses the same API and invalidates the saved configuration version.
        edited = save(client, "mqtt", {"enabled": True, "host": "mqtt", "tls": True})
        assert edited["mqtt"]["status"] == "unverified"
        assert edited["mqtt"]["version"] == 2
        assert edited["onboarding"]["completed"] is True
