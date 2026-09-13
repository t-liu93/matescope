"""Bounded, read-only TeslaMate SQL access. Never changes the external schema or roles."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import NullConnectionPool, PoolTimeout
from sqlalchemy.orm import Session

from .auth import storage
from .models import ApplicationSettings
from .settings import PostgreSQLResponse, read_settings

COLUMNS = {
    "cars": ("id", "name", "model"),
    "drives": ("id", "car_id", "start_date", "end_date", "distance", "duration_min", "speed_max"),
    "charging_processes": (
        "id",
        "car_id",
        "start_date",
        "end_date",
        "charge_energy_added",
        "duration_min",
    ),
    "positions": ("id", "drive_id", "date", "latitude", "longitude"),
}


class SourceFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def snapshot(request: Request) -> tuple[PostgreSQLResponse, str]:
    with Session(storage(request).engine) as session:
        config = read_settings(session).postgresql
        record = session.get(ApplicationSettings, 1)
        encrypted = json.loads(record.encrypted_passwords).get("postgresql") if record else None
        password = storage(request).cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
        return config, password


def classify(error: Exception) -> str:
    if isinstance(error, SourceFailure):
        return error.code
    if isinstance(error, (psycopg.errors.QueryCanceled, PoolTimeout)):
        return "timeout"
    if isinstance(error, psycopg.errors.InsufficientPrivilege):
        return "insufficient_permissions"
    if isinstance(error, (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable)):
        return "incompatible_schema"
    if isinstance(error, psycopg.Error):
        # libpq connection failures sometimes omit SQLSTATE. Classify internally;
        # never return the original error, which can contain connection metadata.
        if (error.sqlstate or "").startswith("28") or "password authentication failed" in str(
            error
        ):
            return "invalid_credentials"
        if "timeout" in str(error).lower():
            return "timeout"
    return "unavailable"


def validate_connection(connection: psycopg.Connection[dict[str, Any]]) -> None:
    role = connection.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname = current_user"
    ).fetchone()
    if not role or any(role.values()):
        raise SourceFailure("unsafe_permissions")
    # Effective privileges include inherited roles and column-level grants. Check
    # every public table, including credential tables, without selecting its data.
    tables = connection.execute(
        "SELECT c.oid, c.relname, c.relkind, n.nspname, "
        "has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS writes, "
        "has_any_column_privilege(c.oid, 'INSERT,UPDATE,REFERENCES') AS column_writes, "
        "has_table_privilege(c.oid, 'SELECT') OR "
        "has_any_column_privilege(c.oid, 'SELECT') AS reads "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('public','private') AND c.relkind IN ('r','p','v','m','f')"
    ).fetchall()
    if any(row["writes"] or row["column_writes"] for row in tables):
        raise SourceFailure("unsafe_permissions")
    if any(row["reads"] and row["relname"] in {"tokens", "users"} for row in tables):
        raise SourceFailure("unsafe_permissions")
    relations = {row["relname"]: row for row in tables if row["nspname"] == "public"}
    for table, required in COLUMNS.items():
        relation = relations.get(table)
        if relation is None or relation["relkind"] not in {"r", "p"}:
            raise SourceFailure("incompatible_schema")
        columns = connection.execute(
            "SELECT a.attname, t.typname, has_column_privilege(a.attrelid,a.attname,'SELECT') "
            "AS readable FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
            "WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped",
            (relation["oid"],),
        ).fetchall()
        by_name = {row["attname"]: row for row in columns}
        for name in required:
            column = by_name.get(name)
            if column is None:
                raise SourceFailure("incompatible_schema")
            expected = (
                {"timestamp", "timestamptz"}
                if name in {"date", "start_date", "end_date"}
                else {"text", "varchar"}
                if name in {"name", "model"}
                else {"int2", "int4", "int8"}
                if name in {"id", "car_id", "drive_id"}
                else {"int2", "int4", "int8", "numeric", "float4", "float8"}
            )
            if column["typname"] not in expected:
                raise SourceFailure("incompatible_schema")
            if not column["readable"]:
                raise SourceFailure("insufficient_permissions")
    schema = connection.execute("SELECT has_schema_privilege('public','USAGE') AS ok").fetchone()
    if schema is None or not schema["ok"]:
        raise SourceFailure("insufficient_permissions")


class DataSource:
    """One process-wide pool; serialize replacement and use to bound all versions."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.pool: NullConnectionPool[psycopg.Connection[dict[str, Any]]] | None = None
        self.version: int | None = None

    @contextmanager
    def bounded_lock(self) -> Iterator[None]:
        if not self.lock.acquire(timeout=5):
            raise SourceFailure("timeout")
        try:
            yield
        finally:
            self.lock.release()

    @contextmanager
    def connection(
        self,
        config: PostgreSQLResponse,
        password: str,
    ) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        if config.skipped:
            raise SourceFailure("skipped")
        if not config.enabled:
            raise SourceFailure("disabled")
        if not config.host or not config.username:
            raise SourceFailure("unconfigured")
        with self.bounded_lock():
            if self.pool is None or self.version != config.version:
                self.close()
                self.pool = NullConnectionPool(
                    kwargs={
                        "host": config.host,
                        "port": config.port,
                        "dbname": config.database,
                        "user": config.username,
                        "password": password,
                        "sslmode": config.sslmode,
                        "connect_timeout": 5,
                        "row_factory": dict_row,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=5000 "
                        "-c timezone=UTC -c search_path=pg_catalog,public",
                    },
                    max_size=3,
                    timeout=5,
                    open=True,
                )
                self.version = config.version
            with self.pool.connection() as connection:
                validate_connection(connection)
                yield connection

    def close(self) -> None:
        with self.lock:
            if self.pool:
                self.pool.close()
                self.pool = None
                self.version = None
