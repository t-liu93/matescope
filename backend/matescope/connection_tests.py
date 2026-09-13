"""One-shot optional transport probes. No startup I/O or persistent subscriptions.

A short-lived child gives DNS, TLS and blocking SMTP a hard deadline. It does not open or use
application storage or sessions and receives credentials only via stdin. The
single web process limits probes to two; children always exit or are killed/reaped.
"""

import json
import os
import resource
import smtplib
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Literal

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .settings import MQTTResponse, SMTPResponse, validate_mailbox

TEST_DEADLINE = 12
SOCKET_TIMEOUT = 3
MQTT_WAIT = 6
MESSAGE_WAIT = 1
_slots = BoundedSemaphore(2)


def tls_context(verify: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def run_test(
    name: Literal["mqtt", "smtp"],
    config: MQTTResponse | SMTPResponse,
    password: str,
    recipient: str = "",
) -> tuple[str, bool]:
    if config.skipped:
        return "skipped", False
    if not config.enabled:
        return "disabled", False
    if not config.host or (isinstance(config, SMTPResponse) and not config.sender):
        return "unconfigured", False
    if isinstance(config, SMTPResponse):
        try:
            validate_mailbox(config.sender)
        except ValueError:
            return "unconfigured", False
    if not _slots.acquire(blocking=False):
        return "busy", False
    try:
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        result = subprocess.run(
            [sys.executable, "-m", "matescope.connection_tests"],
            input=json.dumps(
                {
                    "name": name,
                    "config": config.model_dump(mode="json"),
                    "password": password,
                    "recipient": recipient,
                }
            ),
            text=True,
            capture_output=True,
            timeout=TEST_DEADLINE,
            env=environment,
        )
        if result.returncode != 0:
            return "unavailable", False
        code, observed = json.loads(result.stdout)
        return str(code), bool(observed)
    except subprocess.TimeoutExpired:
        # A server may have accepted SMTP DATA before a response/timeout; never retry.
        return "timeout", False
    except (OSError, ValueError):
        return "unavailable", False
    finally:
        _slots.release()


def mqtt_probe(config: MQTTResponse, password: str) -> tuple[str, bool]:
    client = mqtt.Client(
        CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv311,
        clean_session=True,
        reconnect_on_failure=False,
    )
    client.connect_timeout = SOCKET_TIMEOUT
    client.max_queued_messages_set(1)
    outcome = "timeout"
    subscribed_at: float | None = None
    message_received = False
    finished = False
    subscription_mid: int | None = None

    def on_connect(
        instance: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason: Any,
        properties: Any,
    ) -> None:
        nonlocal outcome, finished, subscription_mid
        if reason.is_failure:
            outcome = "invalid_credentials" if reason.value in {134, 135} else "unavailable"
            finished = True
        else:
            result, subscription_mid = instance.subscribe(
                config.topic_prefix.rstrip("/") + "/#", qos=0
            )
            if result != mqtt.MQTT_ERR_SUCCESS:
                outcome, finished = "subscription_rejected", True

    def on_subscribe(
        instance: mqtt.Client,
        userdata: Any,
        mid: int,
        reasons: Any,
        properties: Any,
    ) -> None:
        nonlocal outcome, subscribed_at, finished
        if mid != subscription_mid or len(reasons) != 1 or reasons[0].is_failure:
            outcome, finished = "subscription_rejected", True
        else:
            outcome = "subscription_accepted"
            subscribed_at = time.monotonic()

    def on_message(instance: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        nonlocal message_received
        # Deliberately do not inspect, copy, log, return or persist topic/payload.
        message_received = True

    client.on_connect, client.on_subscribe, client.on_message = (
        on_connect,
        on_subscribe,
        on_message,
    )
    try:
        if config.username:
            client.username_pw_set(config.username, password or None)
        if config.tls:
            client.tls_set_context(tls_context(config.verify_tls))
        client.connect(config.host, config.port, keepalive=10)
        deadline = time.monotonic() + MQTT_WAIT
        while not finished and time.monotonic() < deadline:
            result = client.loop(timeout=0.1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                return (outcome if finished else "unavailable"), False
            if subscribed_at is not None:
                if message_received:
                    return "message_received", True
                if time.monotonic() - subscribed_at >= MESSAGE_WAIT:
                    return "subscription_accepted", False
        return outcome, False
    finally:
        # Manual loop creates no networking thread. disconnect closes the socket;
        # process exit also reclaims sockets on every error/hard-deadline path.
        client.disconnect()


def smtp_probe(config: SMTPResponse, password: str, recipient: str) -> tuple[str, bool]:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = recipient
    message["Subject"] = "MateScope SMTP test"
    message.set_content(
        "This is the test email you explicitly requested from MateScope.\n"
        "No vehicle data is included.\n"
    )
    context = tls_context(config.verify_tls)
    client: smtplib.SMTP | None = None
    try:
        if config.tls_mode == "implicit":
            client = smtplib.SMTP_SSL(
                host=config.host, port=config.port, timeout=SOCKET_TIMEOUT, context=context
            )
        else:
            client = smtplib.SMTP(host=config.host, port=config.port, timeout=SOCKET_TIMEOUT)
        client.ehlo_or_helo_if_needed()
        if config.tls_mode == "starttls":
            try:
                client.starttls(context=context)
            except smtplib.SMTPNotSupportedError:
                return "tls_unavailable", False
            client.ehlo()
        if config.username:
            client.login(config.username, password)
        refused = client.send_message(message, from_addr=config.sender, to_addrs=[recipient])
        return ("recipient_rejected", False) if refused else ("delivery_accepted", True)
    finally:
        # close instead of QUIT: a post-delivery QUIT failure must not imply send failure.
        if client is not None:
            client.close()


def classify(error: Exception) -> str:
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or (
        isinstance(error, smtplib.SMTPServerDisconnected)
        and isinstance(error.__context__, TimeoutError)
    ):
        return "timeout"
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "invalid_credentials"
    if isinstance(error, smtplib.SMTPSenderRefused):
        return "sender_rejected"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "recipient_rejected"
    if isinstance(error, smtplib.SMTPDataError):
        return "message_rejected"
    if isinstance(error, smtplib.SMTPException):
        return "protocol_error"
    return "unavailable"


def main() -> None:
    # Cap a broker's potential packet allocation; children have no application state.
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    try:
        data = json.loads(sys.stdin.read(65536))
        if data["name"] == "mqtt":
            result = mqtt_probe(MQTTResponse.model_validate(data["config"]), data["password"])
        else:
            result = smtp_probe(
                SMTPResponse.model_validate(data["config"]), data["password"], data["recipient"]
            )
    except Exception as error:
        result = classify(error), False
    print(json.dumps(result))


if __name__ == "__main__":
    main()
