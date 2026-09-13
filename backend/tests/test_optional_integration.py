"""Only fixed local synthetic services; no production settings or outbound email."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import paho.mqtt.publish as publish
import pytest
from fastapi.testclient import TestClient
from matescope.connection_tests import run_test
from matescope.main import create_app
from matescope.settings import MQTTResponse, SMTPResponse
from test_auth import configuration, create_admin, csrf_headers
from test_settings import save

IDENTITY = "matescope-synthetic-m0-t05"


@pytest.fixture
def services() -> tuple[MQTTResponse, SMTPResponse, str]:
    if os.environ.get("MATESCOPE_TEST_OPTIONAL_SERVICES") != IDENTITY:
        pytest.skip("Explicit dedicated synthetic MQTT/Mailpit opt-in required")
    mqtt = MQTTResponse(
        enabled=True,
        host="127.0.0.1",
        port=int(os.environ.get("MATESCOPE_TEST_MQTT_PORT", "11883")),
        username="matescope_synthetic",
        topic_prefix=IDENTITY,
    )
    smtp = SMTPResponse(
        enabled=True,
        host="127.0.0.1",
        port=int(os.environ.get("MATESCOPE_TEST_SMTP_PORT", "11025")),
        username="matescope_synthetic",
        sender="sender@example.test",
        tls_mode="plain",
    )
    api_port = int(os.environ.get("MATESCOPE_TEST_MAIL_API_PORT", "18025"))
    assert 1 <= api_port <= 65535
    api = f"http://127.0.0.1:{api_port}"
    info = httpx.get(api + "/api/v1/info").json()
    assert info["Version"] == "v1.27.8" and info["LatestVersion"] == "disabled"
    webui = httpx.get(api + "/api/v1/webui").json()
    assert webui["Label"] == IDENTITY and webui["MessageRelay"]["Enabled"] is False
    return mqtt, smtp, api


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        create_admin(instance)
        yield instance


def test_mqtt_real_subscription_and_private_payload(
    services: tuple[MQTTResponse, SMTPResponse, str],
    client: TestClient,
) -> None:
    config, _, _ = services
    # Unique allowed prefix ensures the first subscription has no retained message.
    prefix = IDENTITY + "/" + uuid4().hex
    config.topic_prefix = prefix
    assert run_test("mqtt", config, "synthetic-mqtt-only") == ("subscription_accepted", False)
    topic, payload = prefix + "/private", "synthetic-payload-must-not-appear"
    auth = {"username": "matescope_synthetic", "password": "synthetic-mqtt-only"}
    publish.single(topic, payload, hostname="127.0.0.1", port=config.port, auth=auth, retain=True)
    try:
        save(
            client,
            "mqtt",
            {
                **config.model_dump(
                    include={"host", "port", "enabled", "username", "topic_prefix"}
                ),
                "password": {"action": "replace", "value": "synthetic-mqtt-only"},
            },
        )
        result = client.post("/api/v1/settings/mqtt/test", headers=csrf_headers(client))
        assert result.json()["code"] == "message_received"
        assert result.json()["message_received"] and result.json()["persisted"]
        assert payload not in result.text and topic not in result.text
        assert payload not in client.get("/api/v1/settings").text
        assert (
            payload.encode()
            not in (Path(client.app.state.storage.directory) / "matescope.sqlite3").read_bytes()
        )
    finally:
        publish.single(topic, b"", hostname="127.0.0.1", port=config.port, auth=auth, retain=True)
    assert run_test("mqtt", config, "wrong-synthetic-password") == ("invalid_credentials", False)
    # Mosquitto built-in ACL accepts SUBACK but suppresses forbidden topic deliveries.
    config.topic_prefix = "denied-synthetic-prefix"
    assert run_test("mqtt", config, "synthetic-mqtt-only") == ("subscription_accepted", False)


def test_mailpit_single_explicit_delivery_and_failures(
    services: tuple[MQTTResponse, SMTPResponse, str],
    client: TestClient,
) -> None:
    _, config, api = services
    before = httpx.get(api + "/api/v1/messages").json()["total"]
    fields = config.model_dump(
        include={"host", "port", "enabled", "username", "sender", "tls_mode"}
    )
    save(
        client,
        "smtp",
        {**fields, "password": {"action": "replace", "value": "synthetic-smtp-only"}},
    )
    client.get("/api/v1/settings")
    client.get("/api/v1/diagnostics")
    assert httpx.get(api + "/api/v1/messages").json()["total"] == before
    recipient = f"chosen-{uuid4().hex}@example.test"
    result = client.post(
        "/api/v1/settings/smtp/test", headers=csrf_headers(client), json={"recipient": recipient}
    )
    assert result.json()["code"] == "delivery_accepted" and result.json()["delivery_accepted"]
    messages = httpx.get(api + "/api/v1/messages").json()
    assert messages["total"] == before + 1
    matching = [item for item in messages["messages"] if item["To"][0]["Address"] == recipient]
    assert len(matching) == 1
    item = matching[0]
    message = httpx.get(api + "/api/v1/message/" + item["ID"]).json()
    assert [entry["Address"] for entry in message["To"]] == [recipient]
    assert message["From"]["Address"] == "sender@example.test"
    assert message["Subject"] == "MateScope SMTP test"
    assert message["Text"].replace("\r\n", "\n") == (
        "This is the test email you explicitly requested from MateScope.\n"
        "No vehicle data is included.\n"
    )
    assert len(message["Text"]) < 200 and not message["HTML"]
    assert recipient not in client.get("/api/v1/settings").text
    assert run_test("smtp", config, "wrong-synthetic-password", recipient) == (
        "invalid_credentials",
        False,
    )
    config.tls_mode = "starttls"
    assert run_test("smtp", config, "synthetic-smtp-only", recipient) == ("tls_unavailable", False)
    assert httpx.get(api + "/api/v1/messages").json()["total"] == before + 1
