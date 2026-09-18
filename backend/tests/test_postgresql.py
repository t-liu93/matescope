"""Real SQL tests opt in only to the identified, isolated synthetic development DB."""

import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from matescope.main import create_app
from matescope.postgresql import DataSource, SourceFailure, classify
from matescope.settings import PostgreSQLResponse
from psycopg.conninfo import conninfo_to_dict
from test_auth import configuration, create_admin, csrf_headers
from test_settings import save


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    dsn = os.environ.get("MATESCOPE_TEST_PG_DSN")
    if not dsn:
        pytest.skip("MATESCOPE_TEST_PG_DSN not set; dedicated synthetic PostgreSQL required")
    params = conninfo_to_dict(dsn)
    # Never accept a general production DSN. Both transport/name and a DB marker
    # must match before these tests can perform any fixture mutations.
    if params.get("host") not in {"127.0.0.1", "localhost", "postgres"} or (
        params.get("dbname") != "teslamate_synthetic" or params.get("user") != "teslamate_admin"
    ):
        pytest.fail("Integration tests require the dedicated synthetic development database")
    with psycopg.connect(dsn, autocommit=True) as connection:
        row = connection.execute("SELECT identity FROM public.matescope_synthetic_guard").fetchall()
        if row != [("matescope-synthetic-m0-t04",)]:
            pytest.fail("Synthetic database identity guard failed; no mutations performed")
        yield connection


@pytest.fixture
def pgconfig(admin: psycopg.Connection[Any]) -> PostgreSQLResponse:
    return PostgreSQLResponse(
        enabled=True,
        host=admin.info.host,
        port=admin.info.port,
        database=admin.info.dbname,
        username="matescope_readonly",
        sslmode="disable",
        version=1,
    )


@pytest.fixture
def client(tmp_path: Path, pgconfig: PostgreSQLResponse) -> Iterator[TestClient]:
    with TestClient(create_app(configuration(tmp_path))) as instance:
        create_admin(instance)
        save(
            instance,
            "postgresql",
            {
                **pgconfig.model_dump(
                    include={
                        "host",
                        "port",
                        "database",
                        "username",
                        "sslmode",
                        "enabled",
                    }
                ),
                "password": {"action": "replace", "value": "synthetic-reader-only"},
            },
        )
        yield instance


def check(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/settings/postgresql/test", headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def mutation(admin: psycopg.Connection[Any], change: str, undo: str) -> Iterator[None]:
    admin.execute(change)
    try:
        yield
    finally:
        admin.execute(undo)


def test_synthetic_sql_and_readonly(client: TestClient, pgconfig: PostgreSQLResponse) -> None:
    assert check(client)["code"] == "ok"
    current = client.get("/api/v1/settings").json()["postgresql"]
    assert current["status"] == "success" and current["test_result"]["persisted"]
    assert len(client.get("/api/v1/vehicles").json()["items"]) == 2
    source = DataSource()
    try:
        with source.connection(pgconfig, "synthetic-reader-only") as connection:
            assert (
                connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"]
                == "on"
            )
            assert (
                connection.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5s"
            )
            assert source.pool is not None and source.pool.max_size == 3
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute("UPDATE public.drives SET distance=1 WHERE id=1")
    finally:
        source.close()


def test_m1_synthetic_history_shape_and_legacy_minimum(
    admin: psycopg.Connection[Any],
) -> None:
    """The M1 fixture has optional joins while the M0 role remains minimal."""
    rows = admin.execute(
        "SELECT d.id, d.car_id, a.name, a.road, g.name, d.start_rated_range_km, "
        "d.end_rated_range_km, c.efficiency "
        "FROM public.drives AS d "
        "LEFT JOIN public.addresses AS a ON a.id = d.start_address_id "
        "LEFT JOIN public.geofences AS g ON g.id = d.start_geofence_id "
        "JOIN public.cars AS c ON c.id = d.car_id "
        "WHERE d.id IN (1, 6, 7) ORDER BY d.id"
    ).fetchall()
    assert rows == [
        (
            1,
            1,
            "Synthetic home",
            "Example Road",
            "Synthetic home",
            300,
            288,
            Decimal("0.1800"),
        ),
        (6, 2, None, None, None, None, None, None),
        (7, 1, None, None, None, None, None, Decimal("0.1800")),
    ]
    charge = admin.execute(
        "SELECT cp.id, cp.car_id, cp.end_date, cp.charge_energy_used, cp.cost, "
        "ch.battery_level, ch.ideal_battery_range_km "
        "FROM public.charging_processes AS cp "
        "LEFT JOIN public.charges AS ch ON ch.charging_process_id = cp.id "
        "WHERE cp.id IN (1, 2, 3, 4) ORDER BY cp.id, ch.id"
    ).fetchall()
    assert charge[0][0:2] == (1, 1) and charge[0][3:5] == (None, None)
    assert charge[0][5:] == (40, 150)
    assert charge[1][5:] == (80, None)
    assert charge[2] == (2, 2, None, None, None, None, None)
    assert (
        charge[3][0:5] == (3, 2, charge[3][2], Decimal("13.50"), Decimal("0.00"))
        and charge[3][5:] == (50, 180)
    )
    assert charge[4][0:3] == (4, 1, None) and charge[4][3:] == (None, None, None, None)
    assert (
        admin.execute(
            "SELECT count(*) FROM public.drives "
            "WHERE start_date < TIMESTAMP '2022-01-01'"
        ).fetchone()[0]
        == 1
    )
    assert not admin.execute(
        "SELECT has_column_privilege('matescope_readonly', 'public.cars', 'efficiency', 'SELECT')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_column_privilege('matescope_readonly', 'public.charges', 'id', 'SELECT')"
    ).fetchone()[0]


def test_window_pagination_timezone_missing_and_old(client: TestClient) -> None:
    first = client.get("/api/v1/trips", params={"limit": 1}).json()
    assert first["items"][0]["end"] is None
    assert first["items"][0]["distance_km"] is None
    ids = [first["items"][0]["id"]]
    cursor = first["next_cursor"]
    while cursor:
        response = client.get("/api/v1/trips", params={"limit": 1, "cursor": cursor})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["start"] == first["start"] and result["end"] == first["end"]
        ids.extend(item["id"] for item in result["items"])
        cursor = result["next_cursor"]
    assert ids == [2, 5, 1, 3]
    now = datetime.now(UTC)
    older = client.get(
        "/api/v1/trips",
        params={
            "start": (now - timedelta(days=130)).isoformat(),
            "end": (now - timedelta(days=110)).isoformat(),
        },
    ).json()
    assert [row["id"] for row in older["items"]] == [4]
    params = {"start": first["start"], "end": first["end"]}
    utc = client.get("/api/v1/trips", params=params).json()
    from datetime import timezone

    offset = timezone(timedelta(hours=5, minutes=30))
    shifted = {
        key: datetime.fromisoformat(value).astimezone(offset).isoformat()
        for key, value in params.items()
    }
    assert client.get("/api/v1/trips", params=shifted).json() == utc
    assert all(row["start"].endswith("Z") for row in utc["items"])
    assert client.get("/api/v1/trips", params={"vehicle_id": 999}).json()["items"] == []
    charge = client.get("/api/v1/charges/1").json()
    assert charge["energy_added_kwh"] == 22.5
    assert client.get("/api/v1/charges/2").json()["end"] is None
    for path in ("trips", "charges"):
        assert client.get(f"/api/v1/{path}/999999").status_code == 404
    assert client.get("/api/v1/trips/999999/trajectory").status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-01-01"},
        {"start": "2026-01-01T00:00:00"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-05-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        {"limit": 101},
        {"limit": 0},
        {"cursor": "garbage"},
        {"cursor": "e30="},
    ],
)
def test_invalid_windows(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/api/v1/trips", params=params).status_code == 422


@pytest.mark.parametrize("identifier", ["1e999", "Infinity", "-Infinity", "NaN"])
def test_nonfinite_cursor_id(client: TestClient, identifier: str) -> None:
    payload = (
        '{"start":"2026-01-01T00:00:00Z","end":"2026-01-02T00:00:00Z",'
        '"after":"2026-01-01T12:00:00Z","vehicle_id":null,"kind":"trips","id":' + identifier + "}"
    )
    cursor = base64.urlsafe_b64encode(payload.encode()).decode()
    response = client.get("/api/v1/trips", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid time window or cursor"}


def test_trajectory_budget_endpoints_and_gaps(client: TestClient) -> None:
    response = client.get("/api/v1/trips/1/trajectory")
    assert response.status_code == 200, response.text
    result = response.json()
    points = result["points"]
    assert len(points) <= 2000 and result["simplified"] and result["total_points"] == 4950
    assert points[0]["id"] == 10000 and points[-1]["id"] == 15000
    assert [p["id"] for p in points] == sorted(p["id"] for p in points)
    assert len({p["segment_id"] for p in points}) == 2
    assert all(p["latitude"] is not None for p in points)
    assert len(response.content) < 400000
    assert client.get("/api/v1/trips/2/trajectory").json()["points"] == []


@pytest.mark.parametrize(
    "change,undo,code",
    [
        (
            "GRANT UPDATE ON public.drives TO matescope_readonly",
            "REVOKE UPDATE ON public.drives FROM matescope_readonly",
            "unsafe_permissions",
        ),
        (
            "GRANT UPDATE(distance) ON public.drives TO matescope_readonly",
            "REVOKE UPDATE(distance) ON public.drives FROM matescope_readonly",
            "unsafe_permissions",
        ),
        (
            "GRANT SELECT(access) ON public.tokens TO matescope_readonly",
            "REVOKE SELECT(access) ON public.tokens FROM matescope_readonly",
            "unsafe_permissions",
        ),
        (
            "GRANT SELECT ON private.tokens TO matescope_readonly",
            "REVOKE SELECT ON private.tokens FROM matescope_readonly",
            "unsafe_permissions",
        ),
        (
            "REVOKE SELECT(model) ON public.cars FROM matescope_readonly",
            "GRANT SELECT(model) ON public.cars TO matescope_readonly",
            "insufficient_permissions",
        ),
        (
            "ALTER TABLE public.drives RENAME COLUMN distance TO missing_distance",
            "ALTER TABLE public.drives RENAME COLUMN missing_distance TO distance",
            "incompatible_schema",
        ),
    ],
)
def test_permission_schema_failures(
    client: TestClient,
    admin: psycopg.Connection[Any],
    change: str,
    undo: str,
    code: str,
) -> None:
    with mutation(admin, change, undo):
        assert check(client)["code"] == code
        response = client.get("/api/v1/trips")
        assert response.status_code == 503 and response.json()["detail"]["code"] == code
    assert check(client)["code"] == "ok"


def test_inherited_permissions(client: TestClient, admin: psycopg.Connection[Any]) -> None:
    admin.execute("CREATE ROLE matescope_test_writer NOLOGIN")
    try:
        admin.execute("GRANT UPDATE(distance) ON public.drives TO matescope_test_writer")
        admin.execute("GRANT matescope_test_writer TO matescope_readonly")
        assert check(client)["code"] == "unsafe_permissions"
    finally:
        admin.execute("REVOKE matescope_test_writer FROM matescope_readonly")
        admin.execute("DROP OWNED BY matescope_test_writer")
        admin.execute("DROP ROLE matescope_test_writer")


def test_invalid_credentials_and_superuser(
    client: TestClient, pgconfig: PostgreSQLResponse
) -> None:
    body = pgconfig.model_dump(
        include={"host", "port", "database", "username", "sslmode", "enabled"}
    )
    save(client, "postgresql", {**body, "password": {"action": "replace", "value": "WRONG-secret"}})
    result = check(client)
    assert result["code"] == "invalid_credentials"
    assert "WRONG-secret" not in str(result) and "127.0.0.1" not in str(result)
    save(
        client,
        "postgresql",
        {
            **body,
            "username": "teslamate_admin",
            "password": {"action": "replace", "value": "synthetic-only"},
        },
    )
    assert check(client)["code"] == "unsafe_permissions"


def test_timeout_is_sanitized(client: TestClient, admin: psycopg.Connection[Any]) -> None:
    with admin.transaction():
        admin.execute("LOCK public.cars IN ACCESS EXCLUSIVE MODE")
        result = check(client)
        assert result["code"] == "timeout" and result["status"] == "failure"
    assert check(client)["code"] == "ok"


def test_empty_database(client: TestClient, admin: psycopg.Connection[Any]) -> None:
    # RLS provides an empty view for the reader, leaving fixture rows intact.
    with mutation(
        admin,
        "ALTER TABLE public.cars ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE public.cars DISABLE ROW LEVEL SECURITY",
    ):
        assert check(client)["code"] == "empty_data"
        assert client.get("/api/v1/vehicles").json()["items"] == []


def test_unavailable_does_not_block_settings(
    client: TestClient, pgconfig: PostgreSQLResponse
) -> None:
    save(
        client,
        "postgresql",
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 1,
            "username": "synthetic",
        },
    )
    assert check(client)["code"] == "unavailable"
    assert client.get("/api/v1/settings").status_code == 200
    assert client.get("/api/v1/readiness").status_code == 200


def test_classification_does_not_echo_error() -> None:
    assert classify(SourceFailure("unsafe_permissions")) == "unsafe_permissions"
    assert classify(RuntimeError("synthetic secret DSN")) == "unavailable"


def test_timestamptz_capability(client: TestClient, admin: psycopg.Connection[Any]) -> None:
    expected = client.get("/api/v1/trips").json()["items"]
    with mutation(
        admin,
        "ALTER TABLE public.drives ALTER COLUMN start_date TYPE timestamptz "
        "USING start_date AT TIME ZONE 'UTC', ALTER COLUMN end_date TYPE timestamptz "
        "USING end_date AT TIME ZONE 'UTC'",
        "ALTER TABLE public.drives ALTER COLUMN start_date TYPE timestamp "
        "USING start_date AT TIME ZONE 'UTC', ALTER COLUMN end_date TYPE timestamp "
        "USING end_date AT TIME ZONE 'UTC'",
    ):
        assert check(client)["code"] == "ok"
        assert client.get("/api/v1/trips").json()["items"] == expected
        page = client.get("/api/v1/trips", params={"limit": 1}).json()
        assert (
            client.get(
                "/api/v1/trips",
                params={
                    "cursor": page["next_cursor"],
                    "limit": 1,
                },
            ).json()["items"][0]["id"]
            == 5
        )
    # A successful result is invalidated even when re-saving identical fields.
    before = client.get("/api/v1/settings").json()["postgresql"]
    after = save(
        client,
        "postgresql",
        {
            key: before[key]
            for key in ("host", "port", "database", "username", "sslmode", "enabled")
        },
    )["postgresql"]
    assert after["version"] == before["version"] + 1
    assert after["status"] == "unverified" and after["test_result"] is None
