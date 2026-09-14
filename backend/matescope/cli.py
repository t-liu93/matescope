"""Local recovery commands. Never initializes storage or contacts configured services."""

import argparse
import getpass
import hashlib
import json
import os
import sqlite3
import stat
import struct
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import BinaryIO

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr, ValidationError

from .auth import CreateAdminInput, password_hasher
from .settings import SettingsResponse
from .storage import KEY_CHECK

DATABASE = "matescope.sqlite3"
KEY = "encryption.key"
MANIFEST = "manifest.json"
FORMAT_VERSION = 1
SCHEMA_VERSION = "0002_settings"
MAX_DATABASE = 128 * 1024 * 1024
MAX_MANIFEST = 4096
MAX_ARCHIVE = MAX_DATABASE + 16384
TIMEOUT = 30
COLUMNS = {
    "alembic_version": {"version_num"},
    "administrator": {"id", "username", "password_hash"},
    "instance": {"id", "key_check"},
    "login_session": {"token_hash", "administrator_id", "expires_at"},
    "application_settings": {"id", "configuration", "encrypted_passwords"},
}


class RecoveryError(Exception):
    """A fixed, non-sensitive diagnostic suitable for terminal output."""


@contextmanager
def directory(path: Path) -> Iterator[int]:
    # Walk every component without following symlinks, retaining the final directory FD.
    absolute = Path(os.path.abspath(path))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def regular_file(parent: int, name: str, *, maximum: int) -> BinaryIO:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
        os.close(descriptor)
        raise RecoveryError("Expected a nonempty regular file within the recovery size limit")
    return os.fdopen(descriptor, "rb")


def read_key(parent: int) -> bytes:
    try:
        with regular_file(parent, KEY, maximum=44) as stream:
            key = stream.read(45)
    except FileNotFoundError:
        raise RecoveryError(
            "Encryption key is missing; restore the original key from backup"
        ) from None
    if len(key) != 44:
        raise RecoveryError("Encryption key is missing or invalid; restore the original key")
    try:
        Fernet(key)
    except ValueError:
        raise RecoveryError("Encryption key is invalid; restore the original key") from None
    return key


def database_connection(parent: int, *, writable: bool = False) -> sqlite3.Connection:
    # URI mode prevents SQLite from creating a missing database. Parent FD pins the directory.
    with regular_file(parent, DATABASE, maximum=MAX_DATABASE):
        pass
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            info = os.stat(DATABASE + suffix, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RecoveryError("SQLite sidecars must be regular files")
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(
        f"file:/proc/self/fd/{parent}/{DATABASE}?mode={mode}", uri=True, timeout=15
    )
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
    deadline = time.monotonic() + TIMEOUT
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return connection


def validate_database(connection: sqlite3.Connection, key: bytes) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RecoveryError("SQLite integrity validation failed")
    objects = connection.execute("SELECT type, name FROM sqlite_schema LIMIT 32").fetchall()
    tables = {name for kind, name in objects if kind == "table"}
    if (
        len(objects) == 32
        or tables != COLUMNS.keys()
        or any(
            kind != "table" and not (kind == "index" and name.startswith("sqlite_autoindex_"))
            for kind, name in objects
        )
    ):
        raise RecoveryError("Unsupported database schema; use the matching MateScope version")
    for table, columns in COLUMNS.items():
        if {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')} != columns:
            raise RecoveryError("Unsupported database schema; use the matching MateScope version")
    if connection.execute("SELECT version_num FROM alembic_version LIMIT 2").fetchall() != [
        (SCHEMA_VERSION,)
    ]:
        raise RecoveryError("Unsupported schema version; use the matching MateScope version")
    cipher = Fernet(key)
    instances = connection.execute("SELECT id, key_check FROM instance LIMIT 2").fetchall()
    try:
        if (
            len(instances) != 1
            or instances[0][0] != 1
            or cipher.decrypt(instances[0][1].encode()) != KEY_CHECK
        ):
            raise InvalidToken
        records = connection.execute(
            "SELECT id, configuration, encrypted_passwords FROM application_settings LIMIT 2"
        ).fetchall()
        if len(records) > 1 or any(row[0] != 1 for row in records):
            raise RecoveryError("Invalid application settings")
        for _, configuration, encrypted in records:
            SettingsResponse.model_validate_json(configuration)
            passwords = json.loads(encrypted)
            if not isinstance(passwords, dict) or not passwords.keys() <= {
                "postgresql",
                "mqtt",
                "smtp",
            }:
                raise RecoveryError("Invalid encrypted settings")
            for token in passwords.values():
                cipher.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError, AttributeError, ValidationError):
        raise RecoveryError(
            "Database and encryption key are incompatible or settings are corrupt"
        ) from None
    administrators = connection.execute("SELECT id FROM administrator LIMIT 2").fetchall()
    if (
        administrators not in ([], [(1,)])
        or connection.execute("PRAGMA foreign_key_check").fetchone()
    ):
        raise RecoveryError("Invalid administrator or session records")


def private_write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(data_dir: Path, output: Path) -> None:
    with directory(data_dir) as source, directory(output.parent) as destination:
        if os.path.lexists(f"/proc/self/fd/{destination}/{output.name}"):
            raise RecoveryError("Backup destination already exists; choose a new filename")
        key = read_key(source)
        # Staging alongside output permits atomic, no-overwrite hard-link publication.
        with tempfile.TemporaryDirectory(
            prefix=".matescope-backup-", dir=f"/proc/self/fd/{destination}"
        ) as temporary:
            stage = Path(temporary)
            private_write(stage / DATABASE, b"")
            with closing(database_connection(source)) as original:
                with closing(sqlite3.connect(stage / DATABASE)) as snapshot:
                    deadline = time.monotonic() + TIMEOUT
                    page_size = original.execute("PRAGMA page_size").fetchone()[0]

                    def progress(status: int, remaining: int, total: int) -> None:
                        if time.monotonic() > deadline or total * page_size > MAX_DATABASE:
                            raise RecoveryError("SQLite backup exceeded its time or size limit")

                    original.backup(snapshot, pages=128, progress=progress, sleep=0.05)
            staged = os.open(
                stage.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination
            )
            try:
                with closing(database_connection(staged)) as snapshot:
                    validate_database(snapshot, key)
            finally:
                os.close(staged)
            private_write(stage / KEY, key)
            manifest = {
                "format_version": FORMAT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "files": {
                    name: {
                        "size": (stage / name).stat().st_size,
                        "sha256": file_digest(stage / name),
                    }
                    for name in (DATABASE, KEY)
                },
            }
            private_write(stage / MANIFEST, json.dumps(manifest, sort_keys=True).encode())
            archive = stage / "backup.zip"
            private_write(archive, b"")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                for name in (MANIFEST, DATABASE, KEY):
                    bundle.write(stage / name, name)
            with archive.open("rb") as stream:
                os.fsync(stream.fileno())
            os.link(archive, output.name, dst_dir_fd=destination, follow_symlinks=False)
            os.fsync(destination)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise RecoveryError("Duplicate backup manifest field")
        result[name] = value
    return result


def unpack(stream: BinaryIO, stage: Path) -> None:
    # Bound central-directory parsing before ZipFile allocates its list of entries.
    stream.seek(-22, os.SEEK_END)
    end = stream.read(22)
    signature, disk, start_disk, count, total, size, offset, comment = struct.unpack(
        "<4s4H2LH", end
    )
    if (
        signature != b"PK\x05\x06"
        or disk
        or start_disk
        or count != 3
        or total != 3
        or size > MAX_MANIFEST
        or comment
        or offset + size != stream.tell() - 22
    ):
        raise RecoveryError("Invalid backup archive directory")
    stream.seek(0)
    with zipfile.ZipFile(stream) as bundle:
        entries = bundle.infolist()
        if len(entries) != 3 or {entry.filename for entry in entries} != {MANIFEST, DATABASE, KEY}:
            raise RecoveryError("Backup must contain exactly its manifest, database, and key")
        for entry in entries:
            limit = (
                MAX_DATABASE
                if entry.filename == DATABASE
                else 44
                if entry.filename == KEY
                else MAX_MANIFEST
            )
            mode = entry.external_attr >> 16
            if (
                not 0 < entry.file_size <= limit
                or entry.compress_type != zipfile.ZIP_STORED
                or entry.compress_size != entry.file_size
                or entry.flag_bits & 1
                or not stat.S_ISREG(mode)
                or entry.extra
                or entry.comment
            ):
                raise RecoveryError("Unsupported backup entry type, encoding, or size")
            descriptor = os.open(
                stage / entry.filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as target, bundle.open(entry) as member:
                remaining = entry.file_size
                while remaining:
                    chunk = member.read(min(65536, remaining))
                    if not chunk:
                        raise RecoveryError("Truncated backup entry")
                    target.write(chunk)
                    remaining -= len(chunk)
                if member.read(1):
                    raise RecoveryError("Oversized backup entry")
                target.flush()
                os.fsync(target.fileno())
    manifest = json.loads((stage / MANIFEST).read_bytes(), object_pairs_hook=unique_object)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"format_version", "schema_version", "files"}
        or type(manifest["format_version"]) is not int
        or manifest["format_version"] != FORMAT_VERSION
        or manifest["schema_version"] != SCHEMA_VERSION
        or not isinstance(manifest["files"], dict)
        or set(manifest["files"]) != {DATABASE, KEY}
    ):
        raise RecoveryError("Unsupported backup manifest or version")
    for name in (DATABASE, KEY):
        if manifest["files"][name] != {
            "size": (stage / name).stat().st_size,
            "sha256": file_digest(stage / name),
        }:
            raise RecoveryError("Backup checksum or size validation failed")


def restore(data_dir: Path, archive: Path) -> None:
    with directory(data_dir) as destination, directory(archive.parent) as archive_directory:
        if os.listdir(destination):
            raise RecoveryError(
                "Restore requires an existing empty data directory and stopped service"
            )
        with regular_file(archive_directory, archive.name, maximum=MAX_ARCHIVE) as stream:
            # Stage outside the destination; invalid archives leave even its permissions unchanged.
            with tempfile.TemporaryDirectory(prefix="matescope-restore-") as temporary:
                stage = Path(temporary)
                unpack(stream, stage)
                with directory(stage) as staged:
                    key = read_key(staged)
                    with closing(database_connection(staged, writable=True)) as connection:
                        validate_database(connection, key)
                        with connection:
                            connection.execute("DELETE FROM login_session")
                        # Restore only the main database, even if a backup used WAL journal mode.
                        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        connection.execute("PRAGMA journal_mode=DELETE")
                if os.listdir(destination):
                    raise RecoveryError("Restore destination is no longer empty")
                original_mode = stat.S_IMODE(os.fstat(destination).st_mode)
                created: list[str] = []
                try:
                    os.fchmod(destination, 0o700)
                    for name in (DATABASE, KEY):
                        descriptor = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=destination,
                        )
                        created.append(name)
                        with (
                            os.fdopen(descriptor, "wb") as target,
                            (stage / name).open("rb") as source,
                        ):
                            while chunk := source.read(65536):
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                    os.fsync(destination)
                except BaseException:
                    for name in created:
                        os.unlink(name, dir_fd=destination)
                    os.fchmod(destination, original_mode)
                    raise


def reset_password(data_dir: Path, password: str, confirmation: str) -> None:
    try:
        validated = CreateAdminInput(
            username="admin",
            password=SecretStr(password),
            password_confirmation=SecretStr(confirmation),
        )
    except ValidationError:
        raise RecoveryError("Passwords must match and contain 12–128 characters") from None
    with directory(data_dir) as source:
        key = read_key(source)
        with closing(database_connection(source, writable=True)) as connection:
            # Serialize with web login/password writes; never create an administrator.
            connection.execute("BEGIN IMMEDIATE")
            try:
                validate_database(connection, key)
                if connection.execute("SELECT id FROM administrator LIMIT 2").fetchall() != [(1,)]:
                    raise RecoveryError("No existing administrator; complete web setup first")
                encoded = password_hasher.hash(validated.password.get_secret_value())
                connection.execute(
                    "UPDATE administrator SET password_hash=? WHERE id=1", (encoded,)
                )
                connection.execute("DELETE FROM login_session")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matescope", description="Local MateScope recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore", "reset-password"):
        command = commands.add_parser(name)
        command.add_argument(
            "--data-dir", type=Path, default=Path(os.environ.get("MATESCOPE_DATA_DIR", "/app/data"))
        )
        if name == "backup":
            command.add_argument("--output", type=Path, required=True)
        elif name == "restore":
            command.add_argument("--input", type=Path, required=True)
            command.add_argument(
                "--service-stopped",
                action="store_true",
                required=True,
                help="acknowledge all services using the target are stopped",
            )
        else:
            command.add_argument(
                "--password-stdin",
                action="store_true",
                help="read one password line from a secure pipe (no confirmation)",
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            backup(args.data_dir, args.output)
        elif args.command == "restore":
            restore(args.data_dir, args.input)
        else:
            if args.password_stdin:
                if sys.stdin.isatty():
                    raise RecoveryError("--password-stdin requires a pipe or redirected input")
                password = sys.stdin.read(130)
                if len(password) > 129:
                    raise RecoveryError("Passwords must contain 12–128 characters")
                if password.endswith("\n"):
                    password = password[:-1]
                if "\n" in password or "\r" in password:
                    raise RecoveryError("Standard input must contain exactly one password line")
                confirmation = password
            else:
                if not sys.stdin.isatty():
                    raise RecoveryError(
                        "Interactive reset requires a terminal; "
                        "use --password-stdin for a secure pipe"
                    )
                password = getpass.getpass("New administrator password: ")
                confirmation = getpass.getpass("Confirm new password: ")
            reset_password(args.data_dir, password, confirmation)
    except RecoveryError as error:
        print(f"matescope: {error}", file=sys.stderr)
        return 1
    except (
        OSError,
        sqlite3.Error,
        zipfile.BadZipFile,
        ValueError,
        KeyError,
        EOFError,
        RuntimeError,
    ):
        print(
            "matescope: Recovery failed; check paths, permissions, archive, and database integrity",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("matescope: Cancelled", file=sys.stderr)
        return 1
    print(f"{args.command} completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
