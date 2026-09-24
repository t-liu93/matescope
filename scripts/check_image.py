"""Run critical operations against an already built image using disposable synthetic services."""

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path
from uuid import uuid4

from check_recovery import Client, command, wait_ready


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", required=True, choices=("linux/amd64", "linux/arm64"))
    args = parser.parse_args()
    if os.environ.get("MATESCOPE_TEST_IMAGE") != "matescope-synthetic-m0-t08":
        parser.error("Explicit synthetic image-test opt-in is required")
    inspected = json.loads(command("docker", "image", "inspect", args.image))[0]
    if f"{inspected['Os']}/{inspected['Architecture']}" != args.platform:
        parser.error("Loaded image does not match the requested platform")
    image_id = inspected["Id"]
    project = f"matescope-image-{args.platform.split('/')[1]}-{uuid4().hex[:12]}"
    port = free_port()
    restore_port = free_port()
    while restore_port == port:
        restore_port = free_port()
    base = f"http://127.0.0.1:{port}"
    os.environ.update(
        MATESCOPE_IMAGE=args.image,
        MATESCOPE_PORT=str(port),
        MATESCOPE_PUBLIC_URL=base,
    )
    compose = (
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "compose.yaml",
        "-f",
        "compose.dev.yaml",
    )
    # This random project is exclusively owned by this invocation. Never target an existing stack.
    if command(*compose, "ps", "-aq"):
        raise RuntimeError("Refusing an existing Compose project")
    try:
        # No build: every assertion and recovery operation must use the loaded artifact.
        subprocess.run(
            (*compose, "up", "--no-build", "-d", "--wait", "--wait-timeout", "90"),
            check=True,
            timeout=300,
        )
        app = command(*compose, "ps", "-q", "app")
        actual = json.loads(command("docker", "inspect", app))[0]
        assert actual["Image"] == image_id, "Compose started a different image artifact"
        assert command("docker", "exec", app, "id", "-u") != "0"
        wait_ready(base)
        anonymous = Client(base)
        try:
            anonymous.request("/api/v1/vehicles")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Vehicle data is not protected")
        client = Client(base)
        csrf = client.request("/api/v1/auth/csrf")
        assert isinstance(csrf, dict)
        client.csrf = csrf["csrf_token"]
        created = client.request(
            "/api/v1/setup/administrator",
            {
                "username": "m0-t03-admin",
                "password": "m0-t03-password",
                "password_confirmation": "m0-t03-password",
            },
            "POST",
        )
        assert isinstance(created, dict)
        client.csrf = created["csrf_token"]
        client.request(
            "/api/v1/settings/preferences",
            {
                "language": "zh",
                "timezone": "Europe/Amsterdam",
                "tile_url": "https://tiles.example.test/{z}/{x}/{y}.png",
            },
            "PUT",
        )
        client.request(
            "/api/v1/settings/postgresql",
            {
                "host": "postgres",
                "port": 5432,
                "database": "teslamate_synthetic",
                "username": "matescope_readonly",
                "sslmode": "disable",
                "enabled": True,
                "skipped": False,
                "password": {"action": "replace", "value": "synthetic-reader-only"},
            },
            "PUT",
        )
        # Simulate an existing pre-M1 JSON record.  The current image must start it
        # without a SQLite migration and resolve the added display defaults.
        command(
            "docker",
            "exec",
            app,
            "/app/.venv/bin/python",
            "-c",
            "import json,sqlite3; p='/app/data/matescope.sqlite3'; c=sqlite3.connect(p); "
            "r=c.execute('SELECT configuration FROM application_settings WHERE id=1').fetchone(); "
            "v=json.loads(r[0]); v['preferences'].pop('range_basis',None); "
            "v['preferences'].pop('display_currency',None); "
            "c.execute('UPDATE application_settings SET configuration=? WHERE id=1', "
            "(json.dumps(v),)); "
            "c.commit()",
        )
        command(*compose, "up", "-d", "--no-build", "--no-deps", "--force-recreate", "app")
        app = command(*compose, "ps", "-q", "app")
        assert json.loads(command("docker", "inspect", app))[0]["Image"] == image_id
        wait_ready(base)
        assert client.request("/api/v1/auth/me") == {"username": "m0-t03-admin"}
        refreshed_csrf = client.request("/api/v1/auth/csrf")
        assert isinstance(refreshed_csrf, dict)
        client.csrf = refreshed_csrf["csrf_token"]
        legacy_preferences = client.request("/api/v1/settings")
        assert isinstance(legacy_preferences, dict)
        assert legacy_preferences["preferences"].get("range_basis") == "rated"
        assert legacy_preferences["preferences"].get("display_currency") is None
        tested = client.request("/api/v1/settings/postgresql/test", method="POST")
        assert isinstance(tested, dict) and tested["status"] == "success"
        vehicles = client.request("/api/v1/vehicles")
        assert isinstance(vehicles, dict) and len(vehicles["items"]) == 2
        trip = client.request("/api/v1/trips/1")
        assert isinstance(trip, dict) and trip["distance_km"] == 12.5
        trajectory = client.request("/api/v1/trips/1/trajectory")
        assert isinstance(trajectory, dict) and 0 < len(trajectory["points"]) <= 2000
        charge = client.request("/api/v1/charges/1")
        assert isinstance(charge, dict) and charge["energy_added_kwh"] == 22.5
        before_upgrade = client.request("/api/v1/history/capabilities")
        assert isinstance(before_upgrade, dict)
        before_capabilities = before_upgrade["capabilities"]
        assert all(
            before_capabilities[name] == {
                "available": False,
                "reason": "insufficient_permissions",
            }
            for name in (
                "trip_details", "charge_details", "latest_values", "trip_series", "charge_series"
            )
        )
        postgres = command(*compose, "ps", "-q", "postgres")
        guard = command(
            "docker",
            "exec",
            postgres,
            "psql",
            "-U",
            "teslamate_admin",
            "-d",
            "teslamate_synthetic",
            "-Atc",
            "SELECT identity FROM public.matescope_synthetic_guard",
        )
        assert guard == "matescope-synthetic-m0-t04", "Refusing a non-synthetic database"
        upgrade = Path(__file__).with_name("postgresql") / "upgrade-readonly.sql"
        command("docker", "cp", str(upgrade), f"{postgres}:/tmp/upgrade-readonly.sql")
        command(
            "docker",
            "exec",
            postgres,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "teslamate_admin",
            "-d",
            "teslamate_synthetic",
            "-v",
            "matescope_role=matescope_readonly",
            "-f",
            "/tmp/upgrade-readonly.sql",
        )
        after_upgrade = client.request("/api/v1/history/capabilities")
        assert isinstance(after_upgrade, dict)
        after_capabilities = after_upgrade["capabilities"]
        assert all(
            status == {"available": True, "reason": None}
            for status in after_capabilities.values()
        )
        trip_series = client.request("/api/v1/trips/1/series")
        charge_series = client.request("/api/v1/charges/1/series")
        assert isinstance(trip_series, dict) and trip_series["capability"]["available"] is True
        assert isinstance(charge_series, dict) and charge_series["capability"]["available"] is True
        assert client.request("/api/v1/auth/me") == {"username": "m0-t03-admin"}
        assert client.request("/api/v1/vehicles") == vehicles
        print(
            f"{args.platform}: legacy config/reader startup and explicit upgrade grants passed",
            flush=True,
        )
        subprocess.run(
            (
                sys.executable,
                str(Path(__file__).with_name("check_recovery.py")),
                "--project",
                project,
                "--port",
                str(port),
                "--restore-port",
                str(restore_port),
            ),
            env={**os.environ, "MATESCOPE_TEST_RECOVERY": "matescope-synthetic-m0-t07"},
            check=True,
            timeout=600,
        )
        recreated = command(*compose, "ps", "-q", "app")
        assert json.loads(command("docker", "inspect", recreated))[0]["Image"] == image_id
        print(f"{args.platform}: tested artifact {image_id}; no rebuild performed", flush=True)
    finally:
        # Sanitize naturally: app logs never include credentials, and fixtures contain no real data.
        artifacts = Path(os.environ.get("MATESCOPE_ARTIFACT_DIR", "ci-artifacts"))
        artifacts.mkdir(parents=True, exist_ok=True)
        try:
            logs = command(*compose, "logs", "--no-color")
            (artifacts / f"image-{args.platform.split('/')[1]}.log").write_text(logs)
        finally:
            subprocess.run((*compose, "down", "--volumes"), check=True, timeout=90)


if __name__ == "__main__":
    main()
