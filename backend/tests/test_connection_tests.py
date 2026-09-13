"""Optional service probes: side effects require explicit, authorized POSTs."""

import json
import smtplib
import ssl
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from matescope import connection_tests as probes
from matescope.main import create_app
from matescope.settings import MQTTResponse, SMTPResponse
from test_auth import configuration, create_admin, csrf_headers
from test_settings import save


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        yield instance


@pytest.mark.parametrize("service", ["mqtt", "smtp"])
def test_auth_state_and_no_implicit_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Startup, GET, save or inactive test attempted external I/O")

    monkeypatch.setattr(probes.subprocess, "run", forbidden)
    with TestClient(create_app(configuration(tmp_path))) as client:
        path = f"/api/v1/settings/{service}/test"
        body = {"recipient": "chosen@example.test"} if service == "smtp" else None
        assert client.post(path, headers=csrf_headers(client), json=body).status_code == 401
        create_admin(client)
        headers = csrf_headers(client)
        for invalid in (
            {},
            {**headers, "Origin": "https://evil.example"},
            {**headers, "X-CSRF-Token": "wrong"},
        ):
            assert client.post(path, headers=invalid, json=body).status_code == 403
        for config, expected in (
            ({}, "disabled"),
            ({"skipped": True}, "skipped"),
            ({"enabled": True}, "unconfigured"),
        ):
            save(client, service, config)
            result = client.post(path, headers=headers, json=body)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
            assert result.json()["code"] == expected
            assert result.json()["persisted"]
            assert client.get("/api/v1/diagnostics").json()[service]["test_result"] == result.json()
        save(client, service, {"host": "synthetic.example", "enabled": True})
        assert client.get("/api/v1/settings").json()[service]["test_result"] is None
        cookies = dict(client.cookies)
    with TestClient(create_app(configuration(tmp_path))) as client:
        client.cookies.update(cookies)
        assert client.get("/api/v1/settings").json()[service]["status"] == "unverified"
        assert client.get("/api/v1/diagnostics").status_code == 200


@pytest.mark.parametrize(
    "recipient",
    [
        "",
        "a@example.test,b@example.test",
        "A <a@example.test>",
        "a@example.test\r\nBcc:b@example.test",
        "a@x\x00",
        " a@example.test",
        "é@example.test",
        "a..b@example.test",
        "a@-example.test",
        "a" * 65 + "@example.test",
    ],
)
def test_recipient_validation(client: TestClient, recipient: str) -> None:
    create_admin(client)
    response = client.post(
        "/api/v1/settings/smtp/test", headers=csrf_headers(client), json={"recipient": recipient}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request fields"}


@pytest.mark.parametrize(
    "service,code,observed",
    [
        ("mqtt", "subscription_accepted", False),
        ("mqtt", "message_received", True),
        ("mqtt", "subscription_rejected", False),
        ("smtp", "delivery_accepted", True),
        ("smtp", "invalid_credentials", False),
    ],
)
def test_persist_and_clear(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, service: str, code: str, observed: bool
) -> None:
    create_admin(client)
    monkeypatch.setattr(probes, "run_test", lambda *args: (code, observed))
    body = {"enabled": True, "host": "synthetic.example"}
    save(client, service, body)
    response = client.post(
        f"/api/v1/settings/{service}/test",
        headers=csrf_headers(client),
        json={"recipient": "chosen@example.test"} if service == "smtp" else None,
    ).json()
    assert response["version"] == 1 and response["persisted"]
    assert response["status"] == (
        "success"
        if code in {"subscription_accepted", "message_received", "delivery_accepted"}
        else "failure"
    )
    assert client.get("/api/v1/settings").json()[service]["test_result"] == response
    assert save(client, service, body)[service]["test_result"] is None


@pytest.mark.parametrize("service", ["mqtt", "smtp"])
def test_concurrent_save(client: TestClient, monkeypatch: pytest.MonkeyPatch, service: str) -> None:
    create_admin(client)
    save(client, service, {"enabled": True, "host": "synthetic.example"})
    entered, release = Event(), Event()

    def delayed(*args: Any) -> tuple[str, bool]:
        entered.set()
        assert release.wait(10)
        return ("delivery_accepted" if service == "smtp" else "message_received"), True

    monkeypatch.setattr(probes, "run_test", delayed)
    headers = csrf_headers(client)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.post,
            f"/api/v1/settings/{service}/test",
            headers=headers,
            json={"recipient": "chosen@example.test"} if service == "smtp" else None,
        )
        assert entered.wait(10)
        try:
            save(client, service, {"enabled": False})
        finally:
            release.set()
        result = future.result(timeout=10).json()
    assert result["code"] == "configuration_changed" and not result["persisted"]
    assert result["status"] == "failure"
    assert result["delivery_accepted" if service == "smtp" else "message_received"]
    assert client.get("/api/v1/settings").json()[service]["test_result"] is None


@pytest.mark.parametrize("mode", ["plain", "starttls", "implicit"])
@pytest.mark.parametrize("verify", [True, False])
def test_smtp_modes_and_single_fixed_delivery(
    monkeypatch: pytest.MonkeyPatch, mode: str, verify: bool
) -> None:
    events: list[Any] = []

    class FakeSMTP:
        def __init__(self, **kwargs: Any) -> None:
            events.append(("init", kwargs))

        def connect(self, *args: Any) -> None:
            events.append(("connect", args))

        def ehlo_or_helo_if_needed(self) -> None:
            pass

        def ehlo(self) -> None:
            pass

        def starttls(self, **kwargs: Any) -> None:
            events.append(("starttls", kwargs))

        def login(self, *args: Any) -> None:
            events.append(("login", args))

        def send_message(self, message: Any, **kwargs: Any) -> dict[str, Any]:
            events.append(("send", kwargs, message))
            return {}

        def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(probes.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(probes.smtplib, "SMTP_SSL", FakeSMTP)
    config = SMTPResponse(
        host="mail",
        username="synthetic",
        sender="sender@example.test",
        tls_mode=mode,
        verify_tls=verify,
    )
    assert probes.smtp_probe(config, "secret", "chosen@example.test") == ("delivery_accepted", True)
    sends = [event for event in events if event[0] == "send"]
    assert len(sends) == 1
    assert sends[0][1] == {"from_addr": "sender@example.test", "to_addrs": ["chosen@example.test"]}
    message = sends[0][2]
    assert message["To"] == "chosen@example.test" and message["Bcc"] is None
    assert message["Subject"] == "MateScope SMTP test"
    assert "No vehicle data" in message.get_content() and len(message.as_bytes()) < 1000
    assert ("login", ("synthetic", "secret")) in events
    assert events[-1] == ("close",)
    contexts = [
        event[1]["context"]
        for event in events
        if event[0] in {"init", "starttls"} and "context" in event[1]
    ]
    assert len(contexts) == (0 if mode == "plain" else 1)
    for context in contexts:
        assert context.check_hostname is verify
        assert context.verify_mode == (ssl.CERT_REQUIRED if verify else ssl.CERT_NONE)


@pytest.mark.parametrize(
    "error,expected",
    [
        (ssl.SSLCertVerificationError("secret"), "tls_error"),
        (TimeoutError("secret"), "timeout"),
        (smtplib.SMTPAuthenticationError(535, b"secret"), "invalid_credentials"),
        (smtplib.SMTPRecipientsRefused({"secret": (550, b"secret")}), "recipient_rejected"),
        (smtplib.SMTPSenderRefused(550, b"secret", "secret"), "sender_rejected"),
        (smtplib.SMTPDataError(550, b"secret"), "message_rejected"),
        (smtplib.SMTPNotSupportedError("secret"), "protocol_error"),
        (ConnectionRefusedError("secret"), "unavailable"),
    ],
)
def test_safe_classification(error: Exception, expected: str) -> None:
    assert probes.classify(error) == expected


def test_subprocess_deadline_and_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def timeout(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired("child", kwargs["timeout"])

    monkeypatch.setattr(probes.subprocess, "run", timeout)
    config = MQTTResponse(enabled=True, host="synthetic")
    for _ in range(3):
        assert probes.run_test("mqtt", config, "secret") == ("timeout", False)
    assert len(calls) == 3  # semaphore released on every deadline
    assert calls[0][1]["timeout"] == 12
    assert "secret" not in str(calls[0][0])
    assert json.loads(calls[0][1]["input"])["password"] == "secret"
    probes._slots.acquire()
    probes._slots.acquire()
    try:
        assert probes.run_test("mqtt", config, "secret") == ("busy", False)
    finally:
        probes._slots.release()
        probes._slots.release()


@pytest.mark.parametrize("ack", ["accepted", "rejected", "wrong_mid", "extra_reason"])
def test_mqtt_waits_for_suback_and_disconnects(monkeypatch: pytest.MonkeyPatch, ack: str) -> None:
    events: list[Any] = []

    class FakeMQTT:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.count = 0

        def max_queued_messages_set(self, value: int) -> None:
            pass

        def username_pw_set(self, *args: Any) -> None:
            events.append(("auth", args))

        def tls_set_context(self, context: ssl.SSLContext) -> None:
            assert context.check_hostname

        def connect(self, *args: Any, **kwargs: Any) -> None:
            self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)

        def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
            events.append(("subscribe", topic))
            return 0, 1

        def loop(self, **kwargs: Any) -> int:
            self.count += 1
            if self.count == 1:
                self.on_subscribe(
                    self,
                    None,
                    2 if ack == "wrong_mid" else 1,
                    [SimpleNamespace(is_failure=ack == "rejected")]
                    * (2 if ack == "extra_reason" else 1),
                    None,
                )
            return 0

        def disconnect(self) -> None:
            events.append(("disconnect",))

    monkeypatch.setattr(probes.mqtt, "Client", FakeMQTT)
    monkeypatch.setattr(probes, "MESSAGE_WAIT", 0)
    result = probes.mqtt_probe(
        MQTTResponse(host="synthetic", username="reader", tls=True), "secret"
    )
    assert result == (
        "subscription_accepted" if ack == "accepted" else "subscription_rejected",
        False,
    )
    assert ("subscribe", "teslamate/#") in events
    assert events[-1] == ("disconnect",)


def test_legacy_sender_remains_editable(client: TestClient) -> None:
    from matescope.models import ApplicationSettings
    from sqlalchemy.orm import Session

    create_admin(client)
    save(client, "smtp", {"host": "mail", "enabled": True})
    with Session(client.app.state.storage.engine) as session:
        record = session.get(ApplicationSettings, 1)
        data = json.loads(record.configuration)
        data["smtp"]["sender"] = "previously,allowed@example.test"
        for service in ("postgresql", "mqtt", "smtp"):
            data[service]["test_available"] = False
        record.configuration = json.dumps(data)
        session.commit()
    settings = client.get("/api/v1/settings")
    assert settings.status_code == 200
    for service in ("postgresql", "mqtt", "smtp"):
        assert settings.json()[service]["test_available"] is True
    response = client.post(
        "/api/v1/settings/smtp/test",
        headers=csrf_headers(client),
        json={"recipient": "chosen@example.test"},
    )
    assert response.json()["code"] == "unconfigured"
    assert save(client, "smtp", {"sender": "correct@example.test"})["smtp"]["sender"] == (
        "correct@example.test"
    )
