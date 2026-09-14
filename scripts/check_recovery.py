"""Exercise recovery only against an explicitly selected synthetic Compose project."""

import argparse
import http.cookiejar
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4


def command(*arguments: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        arguments, input=input_text, text=True, capture_output=True, timeout=150
    )
    if result.returncode:
        # Subprocess output can contain configuration. Report the failing operation only.
        raise RuntimeError(f"Recovery check command failed: {arguments[0:3]}")
    return result.stdout.strip()


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies), urllib.request.ProxyHandler({})
        )
        self.csrf = ""

    def request(self, path: str, body: object = None, method: str = "GET") -> object:
        headers = {"Origin": self.base, "X-CSRF-Token": self.csrf}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data, headers, method=method)
        with self.opener.open(request, timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else None

    def login(self, password: str) -> None:
        data = self.request("/api/v1/auth/csrf")
        assert isinstance(data, dict)
        self.csrf = data["csrf_token"]
        for attempt in range(2):
            try:
                data = self.request(
                    "/api/v1/auth/login",
                    {"username": "m0-t03-admin", "password": password},
                    "POST",
                )
                assert isinstance(data, dict)
                self.csrf = data["csrf_token"]
                return
            except urllib.error.HTTPError as error:
                if attempt or error.code != 429 or error.headers.get("Retry-After") != "60":
                    raise
                print("Waiting for the synthetic authentication window", flush=True)
                time.sleep(60)


def wait_ready(base: str) -> None:
    for _ in range(90):
        try:
            Client(base).request("/api/v1/readiness")
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(1)
    raise RuntimeError("Synthetic recovery container did not become ready")


def rejected_session(client: Client) -> None:
    try:
        client.request("/api/v1/auth/me")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("A revoked synthetic session was accepted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--restore-port", type=int, default=18086)
    args = parser.parse_args()
    if os.environ.get("MATESCOPE_TEST_RECOVERY") != "matescope-synthetic-m0-t07":
        parser.error("Explicit synthetic recovery opt-in is required")
    if (
        not args.project.startswith("matescope-")
        or not all(1024 <= port <= 65535 for port in (args.port, args.restore_port))
        or args.port == args.restore_port
    ):
        parser.error("Select a dedicated MateScope synthetic project and distinct local ports")
    compose = (
        "docker",
        "compose",
        "-p",
        args.project,
        "-f",
        "compose.yaml",
        "-f",
        "compose.dev.yaml",
    )
    source_id = command(*compose, "ps", "-q", "app")
    postgres_id = command(*compose, "ps", "-q", "postgres")
    assert source_id and postgres_id
    marker = command(
        "docker",
        "exec",
        postgres_id,
        "psql",
        "-U",
        "teslamate_admin",
        "-d",
        "teslamate_synthetic",
        "-Atc",
        "SELECT identity FROM public.matescope_synthetic_guard",
    )
    assert marker == "matescope-synthetic-m0-t04", "Refusing a non-synthetic database"
    inspected = json.loads(command("docker", "inspect", source_id))[0]
    assert inspected["Config"]["Labels"]["com.docker.compose.project"] == args.project
    environment = dict(value.split("=", 1) for value in inspected["Config"]["Env"])
    source_base = f"http://127.0.0.1:{args.port}"
    assert environment["MATESCOPE_PUBLIC_URL"] == source_base
    assert environment["MATESCOPE_PORT"] == str(args.port)
    mounts = [item for item in inspected["Mounts"] if item["Destination"] == "/app/data"]
    assert len(mounts) == 1 and mounts[0]["Type"] == "volume"
    source_volume = mounts[0]["Name"]
    image = inspected["Image"]
    suffix = uuid4().hex[:12]
    archive = f"t07-{suffix}.matescope.zip"
    clone = f"{args.project}-recovery-{suffix}"
    helper = f"{clone}-restore"
    clone_volume = f"{clone}-data"
    created_volume = False
    stopped_source = False
    backup_created = False
    source = Client(source_base)
    source.login("m0-t03-password")
    source.request(
        "/api/v1/settings/smtp",
        {
            "host": "mail",
            "port": 1025,
            "username": "synthetic_recovery",
            "sender": "backup@example.test",
            "tls_mode": "plain",
            "verify_tls": True,
            "enabled": False,
            "skipped": False,
            "password": {"action": "replace", "value": "synthetic-recovery-only"},
        },
        "PUT",
    )
    expected = source.request("/api/v1/settings")
    try:
        command(
            "docker", "exec", source_id, "matescope", "backup", "--output", f"/app/data/{archive}"
        )
        backup_created = True
        print("Online backup completed while the synthetic app was running", flush=True)
        # Recreate only this app. Explicit interpolation preserves its original port/origin.
        command(
            "env",
            f"MATESCOPE_PORT={args.port}",
            f"MATESCOPE_PUBLIC_URL={source_base}",
            *compose,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "app",
        )
        source_id = command(*compose, "ps", "-q", "app")
        wait_ready(source_base)
        source.request("/api/v1/auth/me")
        assert source.request("/api/v1/settings") == expected
        print("Configuration and sessions survived source container recreation", flush=True)
        command("docker", "stop", source_id)
        stopped_source = True
        command("docker", "volume", "create", clone_volume)
        created_volume = True
        command(
            "docker",
            "run",
            "--rm",
            "--name",
            helper,
            "--mount",
            f"type=volume,source={source_volume},target=/source,readonly",
            "--mount",
            f"type=volume,source={clone_volume},target=/app/data",
            image,
            "matescope",
            "restore",
            "--input",
            f"/source/{archive}",
            "--data-dir",
            "/app/data",
            "--service-stopped",
        )
        clone_base = f"http://127.0.0.1:{args.restore_port}"
        command(
            "docker",
            "run",
            "-d",
            "--name",
            clone,
            "--mount",
            f"type=volume,source={clone_volume},target=/app/data",
            "-p",
            f"127.0.0.1:{args.restore_port}:8000",
            "-e",
            f"MATESCOPE_PUBLIC_URL={clone_base}",
            "-e",
            "MATESCOPE_COOKIE_SECURE=false",
            image,
        )
        wait_ready(clone_base)
        old_session = Client(clone_base)
        old_session.opener = source.opener
        rejected_session(old_session)
        restored = Client(clone_base)
        restored.login("m0-t03-password")
        assert restored.request("/api/v1/settings") == expected
        command(
            "docker",
            "exec",
            clone,
            "/app/.venv/bin/python",
            "-c",
            "import json,sqlite3; from pathlib import Path; "
            "from cryptography.fernet import Fernet; "
            "p=Path('/app/data'); c=sqlite3.connect(p/'matescope.sqlite3'); "
            "row=c.execute('SELECT encrypted_passwords FROM application_settings').fetchone(); "
            "secret=json.loads(row[0])['smtp']; "
            "actual=Fernet((p/'encryption.key').read_bytes()).decrypt(secret.encode()); "
            "assert actual==b'synthetic-recovery-only'; "
            "assert (p/'matescope.sqlite3').stat().st_mode & 0o777 == 0o600; "
            "assert (p/'encryption.key').stat().st_mode & 0o777 == 0o600",
        )
        command(
            "docker",
            "exec",
            "-i",
            clone,
            "matescope",
            "reset-password",
            "--password-stdin",
            input_text="synthetic-recovery-reset-only\n",
        )
        rejected_session(restored)
        Client(clone_base).login("synthetic-recovery-reset-only")
        print("Restored settings/key, session revocation and CLI password reset passed", flush=True)
    finally:
        # These random names were selected by this invocation; never prune unrelated resources.
        try:
            subprocess.run(["docker", "rm", "-f", clone, helper], capture_output=True, timeout=30)
            if created_volume:
                command("docker", "volume", "rm", clone_volume)
        finally:
            if stopped_source:
                command("docker", "start", source_id)
                wait_ready(source_base)
            if backup_created:
                command("docker", "exec", source_id, "rm", "-f", f"/app/data/{archive}")
    assert source.request("/api/v1/settings") == expected
    print(
        "Original synthetic instance remains available; disposable recovery data removed",
        flush=True,
    )


if __name__ == "__main__":
    main()
