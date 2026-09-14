import hashlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from matescope import cli
from matescope.auth import SESSION_COOKIE, password_hasher
from matescope.config import Settings
from matescope.main import create_app
from matescope.models import Administrator, ApplicationSettings
from matescope.settings import SettingsResponse
from matescope.storage import Storage
from sqlalchemy.orm import Session

PASSWORD = "synthetic-original-password"
NEW_PASSWORD = "synthetic-replacement-password"
ORIGIN = "http://testserver"


def app_settings(path: Path) -> Settings:
    return Settings(data_dir=str(path), public_url=ORIGIN, cookie_secure=False)


def csrf(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/v1/auth/csrf").json()["csrf_token"]}


def login(client: TestClient, password: str = PASSWORD) -> int:
    return client.post(
        "/api/v1/auth/login",
        headers=csrf(client),
        json={
            "username": "synthetic-admin",
            "password": password,
        },
    ).status_code


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "original"
    storage = Storage(str(path))
    storage.start()
    with storage.transaction() as session:
        session.add(
            Administrator(
                id=1, username="synthetic-admin", password_hash=password_hasher.hash(PASSWORD)
            )
        )
        settings = SettingsResponse()
        settings.preferences.language = "zh"
        settings.preferences.timezone = "Europe/Amsterdam"
        settings.onboarding.completed = True
        session.add(
            ApplicationSettings(
                id=1,
                configuration=settings.model_dump_json(),
                encrypted_passwords=json.dumps(
                    {
                        "postgresql": storage.cipher.encrypt(
                            b"synthetic-postgresql-secret"
                        ).decode(),
                        "mqtt": storage.cipher.encrypt(b"synthetic-mqtt-secret").decode(),
                        "smtp": storage.cipher.encrypt(b"synthetic-smtp-secret").decode(),
                    }
                ),
            )
        )
    storage.stop()
    yield path


def contents(path: Path) -> dict[str, bytes]:
    return {file.name: file.read_bytes() for file in path.iterdir() if file.is_file()}


def make_archive(source: Path, tmp_path: Path) -> Path:
    archive = tmp_path / "snapshot.matescope.zip"
    cli.backup(source, archive)
    return archive


def rewrite_archive(
    archive: Path, entries: list[tuple[str, bytes, int]], compression: int = 0
) -> None:
    with zipfile.ZipFile(archive, "w", compression=compression) as bundle:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            info.compress_type = compression
            bundle.writestr(info, data)


def entries(archive: Path) -> list[tuple[str, bytes, int]]:
    with zipfile.ZipFile(archive) as bundle:
        return [
            (info.filename, bundle.read(info), info.external_attr >> 16)
            for info in bundle.infolist()
        ]


def test_schema_constant_tracks_migration_head() -> None:
    config = Config()
    config.set_main_option("script_location", "backend/matescope/migrations")
    assert ScriptDirectory.from_config(config).get_current_head() == cli.SCHEMA_VERSION


def test_backup_restore_preserves_settings_credentials_and_revokes_sessions(
    source: Path, tmp_path: Path
) -> None:
    with TestClient(create_app(app_settings(source))) as running:
        assert login(running) == 200
        old_cookie = running.cookies[SESSION_COOKIE]
        original = contents(source)
        archive = make_archive(source, tmp_path)
        assert contents(source) == original
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        assert running.get("/api/v1/auth/me").status_code == 200
    restored = tmp_path / "restored"
    restored.mkdir(mode=0o755)
    cli.restore(restored, archive)
    assert stat.S_IMODE(restored.stat().st_mode) == 0o700
    assert set(contents(restored)) == {cli.DATABASE, cli.KEY}
    for file in restored.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    with TestClient(create_app(app_settings(restored))) as client:
        client.cookies.set(SESSION_COOKIE, old_cookie)
        assert client.get("/api/v1/auth/me").status_code == 401
        client.cookies.clear()
        assert login(client) == 200
        response = client.get("/api/v1/settings").json()
        assert response["preferences"]["language"] == "zh"
        assert response["preferences"]["timezone"] == "Europe/Amsterdam"
        assert response["onboarding"]["completed"] is True
        with Session(client.app.state.storage.engine) as session:
            saved = session.get(ApplicationSettings, 1)
            assert saved is not None
            for name, token in json.loads(saved.encrypted_passwords).items():
                assert (
                    client.app.state.storage.cipher.decrypt(token.encode())
                    == f"synthetic-{name}-secret".encode()
                )
    assert (restored / cli.KEY).read_bytes() == (source / cli.KEY).read_bytes()


def test_live_wal_writes_backup_is_transactionally_consistent(source: Path, tmp_path: Path) -> None:
    stop = threading.Event()
    started = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            with sqlite3.connect(source / cli.DATABASE) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                for index in range(10000):
                    if stop.is_set():
                        break
                    with connection:
                        connection.execute(
                            "UPDATE administrator SET username=?", (f"synthetic-{index}",)
                        )
                        config = SettingsResponse()
                        config.preferences.timezone = "UTC"
                        config.postgresql.database = f"synthetic-{index}"
                        connection.execute(
                            "UPDATE application_settings SET configuration=?",
                            (config.model_dump_json(),),
                        )
                    started.set()
        except BaseException as error:
            failures.append(error)
            started.set()

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(5)
    try:
        archive = make_archive(source, tmp_path)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not failures
    restored = tmp_path / "restored"
    restored.mkdir()
    cli.restore(restored, archive)
    with sqlite3.connect(restored / cli.DATABASE) as connection:
        username = connection.execute("SELECT username FROM administrator").fetchone()[0]
        config = json.loads(
            connection.execute("SELECT configuration FROM application_settings").fetchone()[0]
        )
        assert config["postgresql"]["database"] == username
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize(
    "damage",
    [
        "traversal",
        "absolute",
        "duplicate",
        "symlink",
        "directory",
        "missing-key",
        "version",
        "schema",
        "key-mismatch",
        "checksum",
        "compressed",
        "extra",
        "oversize",
        "truncated",
        "manifest-duplicate",
        "invalid-database",
    ],
)
def test_invalid_archive_never_changes_source_or_destination(
    source: Path, tmp_path: Path, damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_archive(source, tmp_path)
    original = contents(source)
    items = entries(archive)
    name, data, mode = items[2]
    if damage == "traversal":
        items[2] = ("../escaped", data, mode)
    elif damage == "absolute":
        items[2] = ("/tmp/escaped", data, mode)
    elif damage == "duplicate":
        items[2] = (items[0][0], data, mode)
    elif damage == "symlink":
        items[2] = (name, b"/tmp/escaped", stat.S_IFLNK | 0o777)
    elif damage == "directory":
        items[2] = (name, data, stat.S_IFDIR | 0o700)
    elif damage == "missing-key":
        items.pop()
    elif damage in {"version", "schema"}:
        manifest = json.loads(items[0][1])
        manifest["format_version" if damage == "version" else "schema_version"] = "future"
        items[0] = (cli.MANIFEST, json.dumps(manifest).encode(), mode)
    elif damage in {"key-mismatch", "invalid-database"}:
        index = 2 if damage == "key-mismatch" else 1
        changed = Fernet.generate_key() if damage == "key-mismatch" else b"not a sqlite database"
        items[index] = (items[index][0], changed, mode)
        manifest = json.loads(items[0][1])
        manifest["files"][items[index][0]] = {
            "size": len(changed),
            "sha256": hashlib.sha256(changed).hexdigest(),
        }
        items[0] = (cli.MANIFEST, json.dumps(manifest).encode(), mode)
    elif damage == "checksum":
        items[2] = (name, Fernet.generate_key(), mode)
    elif damage == "extra":
        items.append(("extra", b"extra", mode))
    elif damage == "manifest-duplicate":
        items[0] = (cli.MANIFEST, b'{"format_version":1,"format_version":1}', mode)
    rewrite_archive(
        archive, items, zipfile.ZIP_DEFLATED if damage == "compressed" else zipfile.ZIP_STORED
    )
    if damage == "oversize":
        monkeypatch.setattr(cli, "MAX_DATABASE", 1)
    if damage == "truncated":
        archive.write_bytes(archive.read_bytes()[:20])
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    assert (
        cli.main(
            ["restore", "--data-dir", str(target), "--input", str(archive), "--service-stopped"]
        )
        == 1
    )
    assert contents(source) == original
    assert not list(target.iterdir())
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert not (tmp_path / "escaped").exists()


def test_restore_nonempty_symlinks_and_missing_target(source: Path, tmp_path: Path) -> None:
    archive = make_archive(source, tmp_path)
    original = contents(source)
    with pytest.raises(cli.RecoveryError, match="empty"):
        cli.restore(source, archive)
    assert contents(source) == original
    target = tmp_path / "target"
    with pytest.raises(FileNotFoundError):
        cli.restore(target, archive)
    assert not target.exists()
    target.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        cli.restore(target, archive)
    target.unlink()
    target.mkdir()
    linked = tmp_path / "linked.zip"
    linked.symlink_to(archive)
    with pytest.raises(OSError):
        cli.restore(target, linked)
    assert not list(target.iterdir())


@pytest.mark.parametrize(
    "damage",
    [
        "missing-key",
        "key-mismatch",
        "key-symlink",
        "database-symlink",
        "sidecar-symlink",
        "schema",
        "corrupt-settings",
        "trigger",
    ],
)
def test_invalid_source_backup_and_reset_are_readonly(
    source: Path, tmp_path: Path, damage: str
) -> None:
    if damage == "missing-key":
        (source / cli.KEY).unlink()
    elif damage == "key-mismatch":
        (source / cli.KEY).write_bytes(Fernet.generate_key())
    elif damage in {"key-symlink", "database-symlink", "sidecar-symlink"}:
        name = (
            cli.KEY
            if damage == "key-symlink"
            else cli.DATABASE
            if damage == "database-symlink"
            else cli.DATABASE + "-journal"
        )
        path = source / name
        other = tmp_path / "other"
        if path.exists():
            path.rename(other)
        else:
            other.write_bytes(b"untouched")
        path.symlink_to(other)
    else:
        with sqlite3.connect(source / cli.DATABASE) as connection:
            if damage == "schema":
                connection.execute("UPDATE alembic_version SET version_num='future'")
            elif damage == "corrupt-settings":
                connection.execute("UPDATE application_settings SET encrypted_passwords='invalid'")
            else:
                connection.execute(
                    "CREATE TRIGGER evil AFTER DELETE ON login_session "
                    "BEGIN DELETE FROM administrator; END"
                )
    original = contents(source)
    archive = tmp_path / "backup.zip"
    for operation in (
        lambda: cli.backup(source, archive),
        lambda: cli.reset_password(source, NEW_PASSWORD, NEW_PASSWORD),
    ):
        with pytest.raises((cli.RecoveryError, OSError, sqlite3.Error)):
            operation()
        assert contents(source) == original
        assert not archive.exists()
    assert not list(tmp_path.glob(".matescope-backup-*"))


def test_backup_refuses_overwrite_and_symlink_parent(source: Path, tmp_path: Path) -> None:
    archive = make_archive(source, tmp_path)
    original = archive.read_bytes()
    with pytest.raises(cli.RecoveryError, match="already exists"):
        cli.backup(source, archive)
    assert archive.read_bytes() == original
    symlink = tmp_path / "link.zip"
    symlink.symlink_to(tmp_path / "missing")
    with pytest.raises(cli.RecoveryError):
        cli.backup(source, symlink)
    parent = tmp_path / "link-parent"
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        cli.backup(source, parent / "no.zip")


def test_restore_write_failure_rolls_back(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_archive(source, tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    original_open = os.open

    def fail_key(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == cli.KEY and flags & os.O_CREAT:
            raise OSError("synthetic write failure")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_key)
    with pytest.raises(OSError):
        cli.restore(target, archive)
    assert not list(target.iterdir())
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_reset_revokes_live_sessions_and_uses_normal_password_policy(source: Path) -> None:
    with TestClient(create_app(app_settings(source))) as client:
        assert login(client) == 200
        token = client.cookies[SESSION_COOKIE]
        original = contents(source)
        for password, confirmation in [
            ("short", "short"),
            ("x" * 129, "x" * 129),
            (NEW_PASSWORD, PASSWORD),
        ]:
            with pytest.raises(cli.RecoveryError):
                cli.reset_password(source, password, confirmation)
            assert contents(source) == original
        cli.reset_password(source, NEW_PASSWORD, NEW_PASSWORD)
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/v1/auth/me").status_code == 401
        client.cookies.clear()
        assert login(client) == 401
        assert login(client, NEW_PASSWORD) == 200
        with Session(client.app.state.storage.engine) as session:
            admin = session.get(Administrator, 1)
            assert admin is not None and admin.password_hash.startswith("$argon2id$")


def test_reset_does_not_create_administrator(source: Path) -> None:
    with sqlite3.connect(source / cli.DATABASE) as connection:
        connection.execute("DELETE FROM administrator")
    original = contents(source)
    with pytest.raises(cli.RecoveryError, match="No existing administrator"):
        cli.reset_password(source, NEW_PASSWORD, NEW_PASSWORD)
    assert contents(source) == original


def test_cli_input_no_initialization_and_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))
    assert cli.main(["reset-password", "--data-dir", str(missing), "--password-stdin"]) == 1
    assert not missing.exists()
    assert NEW_PASSWORD not in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        cli.main(["restore", "--data-dir", str(missing), "--input", "missing.zip"])
    assert error.value.code == 2 and not missing.exists()
    assert cli.main(["reset-password", "--data-dir", str(missing)]) == 1
    assert not missing.exists()


@pytest.mark.parametrize(
    "password", ["short\n", "x" * 129 + "\n", NEW_PASSWORD + "\nextra", NEW_PASSWORD + "\r\n"]
)
def test_cli_rejects_invalid_stdin(
    source: Path, monkeypatch: pytest.MonkeyPatch, password: str
) -> None:
    original = contents(source)
    monkeypatch.setattr(sys, "stdin", io.StringIO(password))
    assert cli.main(["reset-password", "--data-dir", str(source), "--password-stdin"]) == 1
    assert contents(source) == original


def test_cli_secure_stdin_and_interactive_confirmation(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))
    assert cli.main(["reset-password", "--data-dir", str(source), "--password-stdin"]) == 0
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    supplied = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(supplied))
    assert cli.main(["reset-password", "--data-dir", str(source)]) == 0


def test_local_module_entrypoint(source: Path, tmp_path: Path) -> None:
    archive = tmp_path / "module.matescope.zip"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "matescope.cli",
            "backup",
            "--data-dir",
            str(source),
            "--output",
            str(archive),
        ],
        env={**os.environ, "PYTHONPATH": "backend"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert archive.is_file()


def test_backup_deadline_and_size_limit_cleanup(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = contents(source)
    archive = tmp_path / "limited.zip"
    monkeypatch.setattr(cli, "TIMEOUT", -1)
    with pytest.raises(cli.RecoveryError, match="time or size"):
        cli.backup(source, archive)
    assert not archive.exists()
    assert contents(source) == original
    assert not list(tmp_path.glob(".matescope-backup-*"))
    monkeypatch.setattr(cli, "TIMEOUT", 30)
    monkeypatch.setattr(cli, "MAX_DATABASE", 1)
    with pytest.raises(cli.RecoveryError, match="size limit"):
        cli.backup(source, archive)
    assert not archive.exists() and contents(source) == original


def test_archive_size_is_checked_before_zip_parsing(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_archive(source, tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(cli, "MAX_ARCHIVE", 1)
    with pytest.raises(cli.RecoveryError, match="size limit"):
        cli.restore(target, archive)
    assert not list(target.iterdir())


def test_missing_backup_source_is_not_initialized(tmp_path: Path) -> None:
    source = tmp_path / "missing"
    output = tmp_path / "output.zip"
    assert cli.main(["backup", "--data-dir", str(source), "--output", str(output)]) == 1
    assert not source.exists() and not output.exists()


def test_missing_key_cli_reports_recovery_without_initialization(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (source / cli.KEY).unlink()
    original = contents(source)
    output = tmp_path / "missing-key.matescope.zip"
    commands = [
        ["backup", "--data-dir", str(source), "--output", str(output)],
        ["reset-password", "--data-dir", str(source), "--password-stdin"],
    ]
    for command in commands:
        monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))
        assert cli.main(command) == 1
        error = capsys.readouterr().err
        assert "Encryption key is missing; restore the original key from backup" in error
        assert NEW_PASSWORD not in error
        assert contents(source) == original
        assert not (source / cli.KEY).exists()
        assert not output.exists()


def test_schema_catalog_limit_rejects_hidden_unsupported_objects(
    source: Path, tmp_path: Path
) -> None:
    collations = ("BINARY", "NOCASE", "RTRIM")
    constraints = [
        f"UNIQUE(id COLLATE {first}, username COLLATE {second}, password_hash COLLATE {third})"
        for first in collations
        for second in collations
        for third in collations
    ]
    with sqlite3.connect(source / cli.DATABASE) as connection:
        administrator = connection.execute("SELECT * FROM administrator").fetchone()
        connection.execute("DROP TABLE administrator")
        connection.execute(
            "CREATE TABLE administrator (id INTEGER PRIMARY KEY, "
            "username VARCHAR(64) NOT NULL, password_hash VARCHAR(256) NOT NULL, "
            + ", ".join(constraints)
            + ")"
        )
        connection.execute("INSERT INTO administrator VALUES (?, ?, ?)", administrator)
        connection.execute("CREATE TABLE unsupported_after_limit (id INTEGER)")
        objects = connection.execute("SELECT type, name FROM sqlite_schema").fetchall()
        assert len(objects) > 32
        assert "unsupported_after_limit" not in {name for _, name in objects[:32]}
        assert {name for kind, name in objects[:32] if kind == "table"} == cli.COLUMNS.keys()
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    original = contents(source)
    output = tmp_path / "unsupported.matescope.zip"
    with pytest.raises(cli.RecoveryError, match="Unsupported database schema"):
        cli.backup(source, output)
    with pytest.raises(cli.RecoveryError, match="Unsupported database schema"):
        cli.reset_password(source, NEW_PASSWORD, NEW_PASSWORD)
    assert contents(source) == original and not output.exists()
