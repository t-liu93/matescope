"""Actual local TLS handshakes, including SMTP host/SNI and certificate failure."""

import ipaddress
import socket
import ssl
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from matescope import connection_tests as probes
from matescope.settings import MQTTResponse, SMTPResponse


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            False,
        )
        .sign(key, hashes.SHA256())
    )
    certpath, keypath = tmp_path / "cert.pem", tmp_path / "key.pem"
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keypath.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certpath, keypath


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        sock.settimeout(5)
        yield sock


@pytest.mark.parametrize("mode", ["implicit", "starttls"])
@pytest.mark.parametrize("trust", ["untrusted", "insecure", "trusted"])
def test_smtp_actual_tls(
    certificate: tuple[Path, Path],
    listener: socket.socket,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    trust: str,
) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    server_names: list[str | None] = []
    context.set_servername_callback(lambda sock, name, ctx: server_names.append(name))
    if trust == "trusted":
        trusted = ssl.create_default_context(cafile=str(certificate[0]))
        monkeypatch.setattr(probes, "tls_context", lambda verify: trusted)
    delivered: list[bytes] = []

    def serve() -> None:
        conn, _ = listener.accept()
        conn.settimeout(4)
        stream: Any = None
        try:
            if mode == "implicit":
                conn = context.wrap_socket(conn, server_side=True)
            conn.sendall(b"220 localhost synthetic SMTP\r\n")
            stream = conn.makefile("rb")
            while line := stream.readline(4096):
                if line.startswith(b"ehlo"):
                    conn.sendall(b"250-localhost\r\n250 STARTTLS\r\n")
                elif line.startswith(b"STARTTLS"):
                    conn.sendall(b"220 Ready\r\n")
                    stream.close()
                    conn = context.wrap_socket(conn, server_side=True)
                    stream = conn.makefile("rb")
                elif line.startswith(b"data"):
                    conn.sendall(b"354 Continue\r\n")
                    data = b""
                    while (part := stream.readline(4096)) != b".\r\n":
                        assert part
                        data += part
                    delivered.append(data)
                    conn.sendall(b"250 Accepted\r\n")
                else:
                    conn.sendall(b"250 OK\r\n")
        except ssl.SSLError:
            assert trust == "untrusted"
        finally:
            if stream is not None:
                stream.close()
            conn.close()

    config = SMTPResponse(
        host="localhost",
        port=listener.getsockname()[1],
        sender="sender@example.test",
        tls_mode=mode,
        verify_tls=trust != "insecure",
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(serve)
        if trust == "untrusted":
            with pytest.raises(ssl.SSLCertVerificationError):
                probes.smtp_probe(config, "", "chosen@example.test")
            assert not delivered
        else:
            assert probes.smtp_probe(config, "", "chosen@example.test") == (
                "delivery_accepted",
                True,
            )
            assert len(delivered) == 1
        future.result(timeout=5)
    assert server_names == ["localhost"]


@pytest.mark.parametrize("verify", [True, False])
def test_mqtt_actual_tls(
    certificate: tuple[Path, Path], listener: socket.socket, verify: bool
) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)

    def serve() -> None:
        conn, _ = listener.accept()
        conn.settimeout(4)
        try:
            conn = context.wrap_socket(conn, server_side=True)
            assert conn.recv(4096).startswith(b"\x10")
            conn.sendall(b"\x20\x02\x00\x00")  # CONNACK
            packet = conn.recv(4096)
            assert packet.startswith(b"\x82")
            # Small packet's one-byte remaining length followed by MID.
            conn.sendall(b"\x90\x03" + packet[2:4] + b"\x80")  # Rejected SUBACK
            conn.recv(4096)  # explicit DISCONNECT
        except ssl.SSLError:
            assert verify
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(serve)
        config = MQTTResponse(
            host="localhost", port=listener.getsockname()[1], tls=True, verify_tls=verify
        )
        if verify:
            with pytest.raises(ssl.SSLCertVerificationError):
                probes.mqtt_probe(config, "")
        else:
            assert probes.mqtt_probe(config, "") == ("subscription_rejected", False)
        future.result(timeout=5)


@pytest.mark.parametrize("service", ["mqtt", "smtp"])
def test_actual_connection_failure_and_timeout(listener: socket.socket, service: str) -> None:
    from threading import Event

    done = Event()
    port = listener.getsockname()[1]
    config = (
        MQTTResponse(enabled=True, host="127.0.0.1", port=port)
        if service == "mqtt"
        else SMTPResponse(
            enabled=True,
            host="127.0.0.1",
            port=port,
            sender="sender@example.test",
            tls_mode="plain",
        )
    )

    def silent_peer() -> None:
        connection, _ = listener.accept()
        with connection:
            done.wait(10)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(silent_peer)
        try:
            assert probes.run_test(service, config, "", "chosen@example.test") == ("timeout", False)
        finally:
            done.set()
        future.result(timeout=5)
    listener.close()
    assert probes.run_test(service, config, "", "chosen@example.test") == ("unavailable", False)
