import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from .models import Instance

KEY_CHECK = b"matescope-instance-key-v1"


def private_file(path: Path, *, create: bool = False) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("Application storage must use regular files")
    os.fchmod(descriptor, 0o600)
    return descriptor


class Storage:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        self.engine: Engine
        self.cipher: Fernet

    def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError("Application data directory must not be a symlink")
        self.directory.chmod(0o700)
        lock = private_file(self.directory / ".startup.lock", create=True)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._initialize()
        finally:
            os.close(lock)

    def _initialize(self) -> None:
        database = self.directory / "matescope.sqlite3"
        key_path = self.directory / "encryption.key"
        if not key_path.exists():
            if database.exists():
                raise RuntimeError("Encryption key missing; restore the original key from backup")
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as key_file:
                key_file.write(Fernet.generate_key())
                key_file.flush()
                os.fsync(key_file.fileno())
        with os.fdopen(private_file(key_path), "rb") as existing_key:
            try:
                self.cipher = Fernet(existing_key.read())
            except ValueError:
                raise RuntimeError("Invalid encryption key; restore the original key") from None
        os.close(private_file(database, create=True))
        self.engine = create_engine(
            f"sqlite:///{database}",
            connect_args={"check_same_thread": False, "timeout": 15, "isolation_level": None},
        )

        @event.listens_for(self.engine, "begin")
        def begin(connection: Connection) -> None:
            statement = (
                "BEGIN IMMEDIATE" if connection.get_execution_options().get("write") else "BEGIN"
            )
            connection.exec_driver_sql(statement)

        try:
            with self.engine.begin() as connection:
                config = Config()
                config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            with self.transaction() as session:
                instance = session.get(Instance, 1)
                if instance is None:
                    session.add(Instance(id=1, key_check=self.cipher.encrypt(KEY_CHECK).decode()))
                else:
                    try:
                        if self.cipher.decrypt(instance.key_check.encode()) != KEY_CHECK:
                            raise InvalidToken
                    except InvalidToken:
                        raise RuntimeError(
                            "Encryption key does not match; restore original key"
                        ) from None
        except Exception:
            self.engine.dispose()
            raise

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        # Acquire the writer reservation before reading. All account/session mutations
        # serialize, preventing setup races and login/password-change TOCTOU.
        with self.engine.connect().execution_options(write=True) as connection:
            connection.begin()
            with Session(bind=connection, join_transaction_mode="control_fully") as session:
                try:
                    yield session
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise

    def stop(self) -> None:
        self.engine.dispose()
