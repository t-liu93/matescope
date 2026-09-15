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
SCHEMA_VERSION = "0003_two_factor"
MAX_DATABASE = 128 * 1024 * 1024
MAX_MANIFEST = 4096
MAX_ARCHIVE = MAX_DATABASE + 16384
TIMEOUT = 30
SCHEMA_COLUMNS = {
    "0002_settings": {
    "alembic_version": {"version_num"},
    "administrator": {"id", "username", "password_hash"},
    "instance": {"id", "key_check"},
    "login_session": {"token_hash", "administrator_id", "expires_at"},
    "application_settings": {"id", "configuration", "encrypted_passwords"},
    },
    "0003_two_factor": {
        "alembic_version": {"version_num"},
        "administrator": {
            "id", "username", "password_hash", "auth_version", "totp_seed",
            "totp_enabled_at", "totp_last_step", "factor_failures", "totp_used_codes",
        },
        "instance": {"id", "key_check"},
        "login_session": {"token_hash", "administrator_id", "expires_at", "auth_version"},
        "application_settings": {"id", "configuration", "encrypted_passwords"},
        "login_challenge": {
            "token_hash", "administrator_id", "auth_version", "expires_at", "failures",
        },
        "factor_enrollment": {
            "administrator_id", "session_hash", "auth_version", "seed", "expires_at",
        },
        "recovery_code": {"code_hash", "administrator_id"},
    },
}
SUPPORTED_SCHEMAS = frozenset(SCHEMA_COLUMNS)
MAX_OBJECTS = 32
MAX_SESSIONS = 20
MAX_CHALLENGES = 20
MAX_RECOVERY_CODES = 10
MAX_USED_TOTP_CODES = 6
MAX_FACTOR_FAILURES = 20
TOTP_PERIOD = 30
SEED_PREFIX = b"matescope-totp-v1:"
AUTO_INDEXES = {
    "0002_settings": {
        "sqlite_autoindex_alembic_version_1": ("alembic_version", "version_num"),
        "sqlite_autoindex_login_session_1": ("login_session", "token_hash"),
    },
    "0003_two_factor": {
        "sqlite_autoindex_alembic_version_1": ("alembic_version", "version_num"),
        "sqlite_autoindex_login_session_1": ("login_session", "token_hash"),
        "sqlite_autoindex_login_challenge_1": ("login_challenge", "token_hash"),
        "sqlite_autoindex_recovery_code_1": ("recovery_code", "code_hash"),
    },
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


def schema_version(connection: sqlite3.Connection) -> str:
    versions = connection.execute("SELECT version_num FROM alembic_version LIMIT 2").fetchall()
    if len(versions) != 1 or not isinstance(versions[0][0], str):
        raise RecoveryError("Unsupported schema version; use the matching MateScope version")
    version = versions[0][0]
    if version not in SUPPORTED_SCHEMAS:
        raise RecoveryError("Unsupported schema version; use the matching MateScope version")
    return version


def valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def valid_integer(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def decrypt_totp_seed(cipher: Fernet, token: object) -> str:
    if not isinstance(token, str):
        raise ValueError
    plaintext = cipher.decrypt(token.encode())
    if not plaintext.startswith(SEED_PREFIX):
        raise ValueError
    seed = plaintext.removeprefix(SEED_PREFIX).decode("ascii")
    if len(seed) != 32 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in seed
    ):
        raise ValueError
    return seed


def validate_sessions(
    connection: sqlite3.Connection, auth_version: int | None
) -> set[str]:
    columns = "token_hash, administrator_id, expires_at"
    if auth_version is not None:
        columns += ", auth_version"
    sessions = connection.execute(
        f"SELECT {columns} FROM login_session LIMIT ?", (MAX_SESSIONS + 1,)
    ).fetchall()
    if len(sessions) > MAX_SESSIONS:
        raise RecoveryError("Invalid administrator or session records")
    tokens = set()
    for row in sessions:
        token, administrator_id, expires_at = row[:3]
        if (
            not valid_digest(token)
            or administrator_id != 1
            or not valid_integer(expires_at, minimum=1)
            or (auth_version is not None and row[3] != auth_version)
        ):
            raise RecoveryError("Invalid administrator or session records")
        tokens.add(token)
    return tokens


def validate_two_factor_state(connection: sqlite3.Connection, cipher: Fernet) -> None:
    administrators = connection.execute(
        "SELECT id, auth_version, totp_seed, totp_enabled_at, totp_last_step, "
        "factor_failures, totp_used_codes FROM administrator LIMIT 2"
    ).fetchall()
    if administrators not in ([],) and (len(administrators) != 1 or administrators[0][0] != 1):
        raise RecoveryError("Invalid administrator or session records")
    if not administrators:
        for table in ("login_session", "login_challenge", "factor_enrollment", "recovery_code"):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise RecoveryError("Invalid administrator or session records")
        return
    _, auth_version, seed, enabled_at, last_step, failures, used_codes = administrators[0]
    if not valid_integer(auth_version) or not valid_integer(last_step, minimum=-1):
        raise RecoveryError("Invalid two-factor authentication state")
    if (seed is None) != (enabled_at is None) or (
        enabled_at is not None and not valid_integer(enabled_at, minimum=1)
    ):
        raise RecoveryError("Invalid two-factor authentication state")
    try:
        if seed is not None:
            decrypt_totp_seed(cipher, seed)
        failure_values = json.loads(failures)
        used_values = json.loads(used_codes)
        if (
            not isinstance(failure_values, list)
            or len(failure_values) > MAX_FACTOR_FAILURES
            or not all(valid_integer(value, minimum=0) for value in failure_values)
            or not isinstance(used_values, list)
            or len(used_values) > MAX_USED_TOTP_CODES
            or any(
                not isinstance(value, list)
                or len(value) != 2
                or not valid_digest(value[0])
                or not valid_integer(value[1], minimum=0)
                for value in used_values
            )
            or len({value[0] for value in used_values}) != len(used_values)
        ):
            raise ValueError
    except (InvalidToken, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise RecoveryError("Invalid two-factor authentication state") from None

    sessions = validate_sessions(connection, auth_version)
    challenges = connection.execute(
        "SELECT token_hash, administrator_id, auth_version, expires_at, failures "
        "FROM login_challenge LIMIT ?",
        (MAX_CHALLENGES + 1,),
    ).fetchall()
    enrollments = connection.execute(
        "SELECT administrator_id, session_hash, auth_version, seed, expires_at "
        "FROM factor_enrollment LIMIT 2"
    ).fetchall()
    recovery_codes = connection.execute(
        "SELECT code_hash, administrator_id FROM recovery_code LIMIT ?", (MAX_RECOVERY_CODES + 1,)
    ).fetchall()
    if (
        len(challenges) > MAX_CHALLENGES
        or len(enrollments) > 1
        or len(recovery_codes) > MAX_RECOVERY_CODES
        or any(
            not valid_digest(token)
            or administrator_id != 1
            or version != auth_version
            or not valid_integer(expires_at, minimum=1)
            or not valid_integer(attempts)
            or attempts > 5
            for token, administrator_id, version, expires_at, attempts in challenges
        )
        or any(
            administrator_id != 1
            or not valid_digest(session_hash)
            or version != auth_version
            or not valid_integer(expires_at, minimum=1)
            or session_hash not in sessions
            for administrator_id, session_hash, version, _seed, expires_at in enrollments
        )
        or any(
            not valid_digest(code_hash) or administrator_id != 1
            for code_hash, administrator_id in recovery_codes
        )
    ):
        raise RecoveryError("Invalid two-factor authentication state")
    if seed is None and (last_step != -1 or used_values or recovery_codes or challenges):
        raise RecoveryError("Invalid two-factor authentication state")
    if seed is not None and (last_step < 0 or enrollments):
        raise RecoveryError("Invalid two-factor authentication state")
    try:
        for _, _, _, enrollment_seed, _ in enrollments:
            decrypt_totp_seed(cipher, enrollment_seed)
    except (InvalidToken, UnicodeDecodeError, ValueError, TypeError):
        raise RecoveryError("Invalid two-factor authentication state") from None


def validate_database(connection: sqlite3.Connection, key: bytes) -> str:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RecoveryError("SQLite integrity validation failed")
    objects = connection.execute(
        f"SELECT type, name FROM sqlite_schema LIMIT {MAX_OBJECTS + 1}"
    ).fetchall()
    tables = {name for kind, name in objects if kind == "table"}
    version = schema_version(connection)
    columns = SCHEMA_COLUMNS[version]
    if (
        len(objects) > MAX_OBJECTS
        or tables != columns.keys()
        or any(
            kind != "table" and kind != "index"
            for kind, name in objects
        )
    ):
        raise RecoveryError("Unsupported database schema; use the matching MateScope version")
    indexes = {
        name: (table, sql)
        for kind, name, table, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE type='index'"
        )
    }
    if set(indexes) != AUTO_INDEXES[version].keys():
        raise RecoveryError("Unsupported database schema; use the matching MateScope version")
    for name, (table, column) in AUTO_INDEXES[version].items():
        index_table, sql = indexes[name]
        index_columns = connection.execute(f'PRAGMA index_info("{name}")').fetchall()
        if (
            index_table != table
            or sql is not None
            or [item[2] for item in index_columns] != [column]
        ):
            raise RecoveryError("Unsupported database schema; use the matching MateScope version")
    for table, expected_columns in columns.items():
        # table_info omits generated columns.  table_xinfo includes every column and
        # identifies generated/hidden columns in its final field, so it lets this
        # offline recovery boundary enforce the schema whitelist completely.
        table_columns = connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
        if (
            {row[1] for row in table_columns} != expected_columns
            or any(row[6] != 0 for row in table_columns)
        ):
            raise RecoveryError("Unsupported database schema; use the matching MateScope version")
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
    if version == "0003_two_factor":
        validate_two_factor_state(connection, cipher)
    else:
        validate_sessions(connection, None)
    return version


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
                    version = validate_database(snapshot, key)
            finally:
                os.close(staged)
            private_write(stage / KEY, key)
            manifest = {
                "format_version": FORMAT_VERSION,
                "schema_version": version,
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


def unpack(stream: BinaryIO, stage: Path) -> str:
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
        or not isinstance(manifest["schema_version"], str)
        or manifest["schema_version"] not in SUPPORTED_SCHEMAS
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
    schema = manifest["schema_version"]
    assert isinstance(schema, str)
    return schema


def clear_restored_authentication(connection: sqlite3.Connection, version: str) -> None:
    connection.execute("DELETE FROM login_session")
    if version == "0003_two_factor":
        connection.execute("DELETE FROM login_challenge")
        connection.execute("DELETE FROM factor_enrollment")
        connection.execute("DELETE FROM recovery_code")
        connection.execute(
            "UPDATE administrator SET auth_version=auth_version + 1, factor_failures='[]', "
            "totp_used_codes='[]', totp_last_step=CASE WHEN totp_seed IS NULL THEN -1 "
            "ELSE MAX(totp_last_step, ?) END WHERE id=1",
            (int(time.time() // TOTP_PERIOD) + 1,),
        )


def restore(data_dir: Path, archive: Path) -> str:
    with directory(data_dir) as destination, directory(archive.parent) as archive_directory:
        if os.listdir(destination):
            raise RecoveryError(
                "Restore requires an existing empty data directory and stopped service"
            )
        with regular_file(archive_directory, archive.name, maximum=MAX_ARCHIVE) as stream:
            # Stage outside the destination; invalid archives leave even its permissions unchanged.
            with tempfile.TemporaryDirectory(prefix="matescope-restore-") as temporary:
                stage = Path(temporary)
                manifest_version = unpack(stream, stage)
                with directory(stage) as staged:
                    key = read_key(staged)
                    with closing(database_connection(staged, writable=True)) as connection:
                        version = validate_database(connection, key)
                        if version != manifest_version:
                            raise RecoveryError("Backup manifest does not match database schema")
                        with connection:
                            clear_restored_authentication(connection, version)
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
                return version


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
                version = validate_database(connection, key)
                if connection.execute("SELECT id FROM administrator LIMIT 2").fetchall() != [(1,)]:
                    raise RecoveryError("No existing administrator; complete web setup first")
                encoded = password_hasher.hash(validated.password.get_secret_value())
                connection.execute(
                    "UPDATE administrator SET password_hash=? WHERE id=1", (encoded,)
                )
                connection.execute("DELETE FROM login_session")
                if version == "0003_two_factor":
                    connection.execute("DELETE FROM login_challenge")
                    connection.execute("DELETE FROM factor_enrollment")
                    connection.execute(
                        "UPDATE administrator SET auth_version=auth_version + 1 WHERE id=1"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def reset_two_factor(data_dir: Path) -> bool:
    """Remove a configured second factor without changing the administrator password."""
    with directory(data_dir) as source:
        key = read_key(source)
        with closing(database_connection(source, writable=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = validate_database(connection, key)
                if connection.execute("SELECT id FROM administrator LIMIT 2").fetchall() != [(1,)]:
                    raise RecoveryError("No existing administrator; complete web setup first")
                if version == "0002_settings":
                    connection.rollback()
                    return False
                connection.execute("DELETE FROM login_session")
                connection.execute("DELETE FROM login_challenge")
                connection.execute("DELETE FROM factor_enrollment")
                connection.execute("DELETE FROM recovery_code")
                connection.execute(
                    "UPDATE administrator SET auth_version=auth_version + 1, totp_seed=NULL, "
                    "totp_enabled_at=NULL, totp_last_step=-1, factor_failures='[]', "
                    "totp_used_codes='[]' WHERE id=1"
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matescope", description="Local MateScope recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore", "reset-password", "reset-2fa"):
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
        elif name == "reset-password":
            command.add_argument(
                "--password-stdin",
                action="store_true",
                help="read one password line from a secure pipe (no confirmation)",
            )
        else:
            command.add_argument(
                "--service-stopped",
                action="store_true",
                required=True,
                help="acknowledge all services using the target are stopped",
            )
            command.add_argument(
                "--confirm-reset-2fa",
                action="store_true",
                required=True,
                help="explicitly confirm removal of the configured second factor",
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            backup(args.data_dir, args.output)
        elif args.command == "restore":
            restored_version = restore(args.data_dir, args.input)
        elif args.command == "reset-password":
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
        else:
            changed = reset_two_factor(args.data_dir)
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
    if args.command == "restore" and restored_version == "0003_two_factor":
        print(
            "restore completed; the archive-time password and any active TOTP seed were "
            "restored, while sessions, recovery codes, and pending two-factor state were "
            "cleared. Wait up to 60 seconds (or until server time catches up with the restored "
            "watermark), then sign in with the authenticator and generate new recovery codes."
        )
    elif args.command == "restore":
        print(
            "restore completed; the archive-time password was restored. This 0002 archive has "
            "two-factor authentication disabled and will be migrated on application startup."
        )
    elif args.command == "reset-2fa" and not changed:
        print("reset-2fa completed; this 0002 database has no two-factor state to remove")
    else:
        print(f"{args.command} completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
