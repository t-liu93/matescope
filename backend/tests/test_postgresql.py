"""Real SQL tests opt in only to the identified, isolated synthetic development DB."""

import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from matescope.data import resolve_calendar_window
from matescope.main import create_app
from matescope.postgresql import CAPABILITY_COLUMNS, DataSource, SourceFailure, classify
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


def has_column_select_permission(
    admin: psycopg.Connection[Any], table: str, column: str
) -> bool:
    return admin.execute(
        "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
        ("matescope_readonly", f"public.{table}", column),
    ).fetchone()[0]


@contextmanager
def column_select_grant(
    admin: psycopg.Connection[Any], table: str, column: str
) -> Iterator[None]:
    """Temporarily supply one SELECT permission without changing its prior state."""
    original = has_column_select_permission(admin, table, column)
    added = False
    try:
        if not original:
            admin.execute(f"GRANT SELECT ({column}) ON public.{table} TO matescope_readonly")
            added = True
        yield
    finally:
        if added:
            admin.execute(f"REVOKE SELECT ({column}) ON public.{table} FROM matescope_readonly")
        assert has_column_select_permission(admin, table, column) == original, (
            f"column SELECT state was not restored for public.{table}.{column}"
        )


@contextmanager
def optional_grants(admin: psycopg.Connection[Any]) -> Iterator[None]:
    columns: dict[str, set[str]] = {}
    for group in CAPABILITY_COLUMNS.values():
        for table, required in group.items():
            columns.setdefault(table, set()).update(required)
    original = {
        (table, column): admin.execute(
            "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
            ("matescope_readonly", f"public.{table}", column),
        ).fetchone()[0]
        for table, required in columns.items()
        for column in required
    }
    added: dict[str, set[str]] = {}
    try:
        for table, required in columns.items():
            missing = sorted(column for column in required if not original[(table, column)])
            if missing:
                admin.execute(
                    f"GRANT SELECT ({','.join(missing)}) ON public.{table} TO matescope_readonly"
                )
                added[table] = set(missing)
        yield
    finally:
        for table, granted in added.items():
            admin.execute(
                f"REVOKE SELECT ({','.join(sorted(granted))}) ON public.{table} "
                "FROM matescope_readonly"
            )
        for (table, column), expected in original.items():
            assert (
                admin.execute(
                    "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
                    ("matescope_readonly", f"public.{table}", column),
                ).fetchone()[0]
                == expected
            ), f"optional grant state was not restored for public.{table}.{column}"


@contextmanager
def trip_summary_fixture(admin: psycopg.Connection[Any]) -> Iterator[None]:
    """Create and precisely remove the rows used by the T16 aggregate test."""
    car_id = 97
    drive_ids = (9601, 9602, 9603, 9604, 9605, 9606)
    created_car = False
    created_drives = False
    original_cars: list[Any] = []
    original_drives: list[Any] = []
    try:
        original_cars = admin.execute(
            "SELECT * FROM public.cars WHERE id=%s", (car_id,)
        ).fetchall()
        original_drives = admin.execute(
            "SELECT * FROM public.drives WHERE id = ANY(%s) ORDER BY id", (list(drive_ids),)
        ).fetchall()
        assert not original_cars and not original_drives, (
            "T16 fixture identifiers must be unused"
        )

        admin.execute(
            "INSERT INTO public.cars (id,name,model,efficiency) VALUES (97,'SUMMARY',NULL,0.18)"
        )
        created_car = True
        admin.execute(
            "INSERT INTO public.drives "
            "(id,car_id,start_date,end_date,distance,duration_min,speed_max,"
            "start_rated_range_km,end_rated_range_km,start_ideal_range_km,"
            "end_ideal_range_km) VALUES "
            "(9601,97,TIMESTAMP '2025-01-01 00:00:00',"
            "TIMESTAMP '2025-01-01 00:30:00',10,30,1,100,90,100,80),"
            "(9602,97,TIMESTAMP '2025-01-02 00:00:00',"
            "TIMESTAMP '2025-01-02 01:00:00',20,60,1,100,100,100,100),"
            "(9603,97,TIMESTAMP '2025-01-03 00:00:00',"
            "TIMESTAMP '2025-01-03 00:10:00',-2,-4,1,100,90,100,80),"
            "(9604,97,TIMESTAMP '2025-01-04 00:00:00',NULL,8,20,1,100,90,100,80),"
            "(9605,97,TIMESTAMP '2025-01-05 00:00:00',"
            "TIMESTAMP '2025-01-05 00:10:00',NULL,NULL,1,NULL,NULL,NULL,NULL),"
            "(9606,97,TIMESTAMP '2025-02-01 00:00:00',"
            "TIMESTAMP '2025-02-01 00:10:00',99,99,1,100,90,100,80)"
        )
        created_drives = True
        yield
    finally:
        if created_drives:
            deleted_drives = admin.execute(
                "DELETE FROM public.drives WHERE car_id=%s AND id = ANY(%s) RETURNING id",
                (car_id, list(drive_ids)),
            ).fetchall()
            assert {row[0] for row in deleted_drives} == set(drive_ids)
        if created_car:
            deleted_cars = admin.execute(
                "DELETE FROM public.cars WHERE id=%s RETURNING id", (car_id,)
            ).fetchall()
            assert deleted_cars == [(car_id,)]
        assert admin.execute(
            "SELECT * FROM public.cars WHERE id=%s", (car_id,)
        ).fetchall() == original_cars
        assert admin.execute(
            "SELECT * FROM public.drives WHERE id = ANY(%s) ORDER BY id", (list(drive_ids),)
        ).fetchall() == original_drives


@contextmanager
def charge_summary_fixture(admin: psycopg.Connection[Any]) -> Iterator[None]:
    """Create and precisely remove the rows used by the T17 aggregate test."""
    car_id = 98
    charge_ids = (9811, 9812, 9813, 9814, 9815, 9816)
    created_car = False
    created_charges = False
    original_cars: list[Any] = []
    original_charges: list[Any] = []
    try:
        original_cars = admin.execute(
            "SELECT * FROM public.cars WHERE id=%s", (car_id,)
        ).fetchall()
        original_charges = admin.execute(
            "SELECT * FROM public.charging_processes WHERE id = ANY(%s) ORDER BY id",
            (list(charge_ids),),
        ).fetchall()
        assert not original_cars and not original_charges, "T17 fixture identifiers must be unused"

        admin.execute("INSERT INTO public.cars (id,name,model) VALUES (98,'CHARGE SUMMARY',NULL)")
        created_car = True
        admin.execute(
            "INSERT INTO public.charging_processes "
            "(id,car_id,start_date,end_date,charge_energy_added,duration_min,cost) VALUES "
            "(9811,98,TIMESTAMP '2025-01-01 00:00:00',"
            "TIMESTAMP '2025-01-01 00:30:00',10,30,12.5),"
            "(9812,98,TIMESTAMP '2025-01-02 00:00:00',"
            "TIMESTAMP '2025-01-02 00:00:00',0,0,0),"
            "(9813,98,TIMESTAMP '2025-01-03 00:00:00',"
            "TIMESTAMP '2025-01-03 00:10:00',-3,-2,NULL),"
            "(9814,98,TIMESTAMP '2025-01-04 00:00:00',NULL,20,15,5),"
            "(9815,98,TIMESTAMP '2025-01-05 00:00:00',"
            "TIMESTAMP '2025-01-05 00:10:00',NULL,NULL,NULL),"
            "(9816,98,TIMESTAMP '2025-02-01 00:00:00',"
            "TIMESTAMP '2025-02-01 00:10:00',99,99,99)"
        )
        created_charges = True
        yield
    finally:
        if created_charges:
            deleted_charges = admin.execute(
                "DELETE FROM public.charging_processes WHERE car_id=%s "
                "AND id = ANY(%s) RETURNING id",
                (car_id, list(charge_ids)),
            ).fetchall()
            assert {row[0] for row in deleted_charges} == set(charge_ids)
        if created_car:
            deleted_cars = admin.execute(
                "DELETE FROM public.cars WHERE id=%s RETURNING id", (car_id,)
            ).fetchall()
            assert deleted_cars == [(car_id,)]
        assert admin.execute(
            "SELECT * FROM public.cars WHERE id=%s", (car_id,)
        ).fetchall() == original_cars
        assert admin.execute(
            "SELECT * FROM public.charging_processes WHERE id = ANY(%s) ORDER BY id",
            (list(charge_ids),),
        ).fetchall() == original_charges


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


def test_source_timeout_is_classified_not_returned_as_partial_data(
    pgconfig: PostgreSQLResponse,
) -> None:
    """The retained five-second source deadline must remain an explicit failure."""
    source = DataSource()
    try:
        with source.connection(pgconfig, "synthetic-reader-only") as connection:
            with pytest.raises(psycopg.errors.QueryCanceled) as error:
                connection.execute("SELECT pg_sleep(6)")
        assert classify(error.value) == "timeout"
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


def test_history_capabilities_keep_legacy_history_available(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    response = client.get("/api/v1/history/capabilities")
    assert response.status_code == 200, response.text
    capabilities = response.json()["capabilities"]
    assert capabilities["trip_details"] == {
        "available": False,
        "reason": "insufficient_permissions",
    }
    assert capabilities["locations"] == {
        "available": False,
        "reason": "insufficient_permissions",
    }
    assert client.get("/api/v1/vehicles").status_code == 200
    assert client.get("/api/v1/trips").status_code == 200


@contextmanager
def snapshot_fixture(admin: psycopg.Connection[Any]) -> Iterator[None]:
    """Create isolated latest-value rows and remove exactly those rows."""
    car_id = 96
    process_id = 9696
    position_ids = (960001, 960002, 960003, 960004)
    charge_ids = (969601, 969602, 969603, 969604)
    assert not admin.execute("SELECT 1 FROM public.cars WHERE id=%s", (car_id,)).fetchall()
    assert not admin.execute(
        "SELECT 1 FROM public.charging_processes WHERE id=%s", (process_id,)
    ).fetchall()
    assert not admin.execute(
        "SELECT 1 FROM public.positions WHERE id = ANY(%s)", (list(position_ids),)
    ).fetchall()
    assert not admin.execute(
        "SELECT 1 FROM public.charges WHERE id = ANY(%s)", (list(charge_ids),)
    ).fetchall()
    created_car = created_process = False
    try:
        admin.execute("INSERT INTO public.cars (id,name,model) VALUES (96,'SNAPSHOT',NULL)")
        created_car = True
        admin.execute(
            "INSERT INTO public.charging_processes "
            "(id,car_id,start_date,end_date,charge_energy_added,duration_min) "
            "VALUES (9696,96,TIMESTAMP '2025-04-01 00:00:00',NULL,NULL,NULL)"
        )
        created_process = True
        admin.execute(
            "INSERT INTO public.positions "
            "(id,car_id,drive_id,date,latitude,longitude,odometer,battery_level,"
            "rated_battery_range_km,ideal_battery_range_km) VALUES "
            "(960001,96,NULL,TIMESTAMP '2025-04-01 01:00:00',0,0,100,70,200,220),"
            "(960002,96,NULL,TIMESTAMP '2025-04-01 02:00:00',0,0,101,71,201,221),"
            "(960003,96,NULL,TIMESTAMP '2025-04-01 02:00:00',0,0,102,72,-1,222),"
            "(960004,96,NULL,TIMESTAMP '2025-04-01 03:00:00',0,0,-1,101,250,270)"
        )
        admin.execute(
            "INSERT INTO public.charges "
            "(id,charging_process_id,date,battery_level,rated_battery_range_km,"
            "ideal_battery_range_km) VALUES "
            "(969601,9696,TIMESTAMP '2025-04-01 02:00:00',80,180,230),"
            "(969602,9696,TIMESTAMP '2025-04-01 02:00:00',81,181,231),"
            "(969603,9696,TIMESTAMP '2025-04-01 03:00:00',NULL,190,240),"
            "(969604,9696,TIMESTAMP '2025-04-01 04:00:00',102,-2,NULL)"
        )
        yield
    finally:
        if created_process:
            deleted = admin.execute(
                "DELETE FROM public.charges WHERE charging_process_id=%s RETURNING id",
                (process_id,),
            ).fetchall()
            assert {row[0] for row in deleted} == set(charge_ids)
            assert admin.execute(
                "DELETE FROM public.charging_processes WHERE id=%s RETURNING id", (process_id,)
            ).fetchall() == [(process_id,)]
        if created_car:
            deleted = admin.execute(
                "DELETE FROM public.positions WHERE car_id=%s RETURNING id", (car_id,)
            ).fetchall()
            assert {row[0] for row in deleted} == set(position_ids)
            deleted = admin.execute(
                "DELETE FROM public.cars WHERE id=%s RETURNING id", (car_id,)
            ).fetchall()
            assert deleted == [(car_id,)]


def test_vehicle_snapshot_uses_independent_latest_valid_sources_and_basis(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with snapshot_fixture(admin), optional_grants(admin):
        rated = client.get("/api/v1/vehicles/96/snapshot")
        assert rated.status_code == 200, rated.text
        assert rated.headers["cache-control"] == "no-store"
        assert rated.json() == {
            "vehicle_id": 96,
            # Charge wins a cross-source timestamp tie; its own ID breaks its tie.
            "battery_level": 81,
            "battery_level_at": "2025-04-01T02:00:00Z",
            "range_km": 190.0,
            "range_at": "2025-04-01T03:00:00Z",
            # Odometer has no charge source and rejects the later negative sample.
            "odometer_km": 102.0,
            "odometer_at": "2025-04-01T02:00:00Z",
            "capability": {"available": True, "reason": None},
        }
        save(client, "preferences", {"range_basis": "ideal"})
        ideal = client.get("/api/v1/vehicles/96/snapshot")
        assert ideal.status_code == 200, ideal.text
        assert ideal.json()["range_km"] == 240.0
        assert ideal.json()["range_at"] == "2025-04-01T03:00:00Z"


def test_vehicle_snapshot_is_local_when_optional_columns_are_unreadable(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/vehicles/1/snapshot")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {
        "vehicle_id": 1,
        "battery_level": None,
        "battery_level_at": None,
        "range_km": None,
        "range_at": None,
        "odometer_km": None,
        "odometer_at": None,
        "capability": {"available": False, "reason": "insufficient_permissions"},
    }


def test_vehicle_snapshot_rejects_unknown_vehicle(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        assert client.get("/api/v1/vehicles/999999/snapshot").status_code == 404


def test_trip_places_and_soc_require_only_checked_optional_columns(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T13 preserves legacy rows and isolates optional joins to their drive."""
    legacy = client.get("/api/v1/trips/1")
    assert legacy.status_code == 200
    assert {
        key: legacy.json()[key]
        for key in ("start_place", "end_place", "start_battery_level", "end_battery_level")
    } == {
        "start_place": None,
        "end_place": None,
        "start_battery_level": None,
        "end_battery_level": None,
    }
    with optional_grants(admin):
        original_places = admin.execute(
            "SELECT start_address_id, end_address_id, start_geofence_id, end_geofence_id "
            "FROM public.drives WHERE id=1"
        ).fetchone()
        original_position = admin.execute(
            "SELECT start_position_id FROM public.drives WHERE id=5"
        ).fetchone()
        original_soc = admin.execute(
            "SELECT id,battery_level FROM public.positions WHERE id IN (10000,15000) ORDER BY id"
        ).fetchall()
        try:
            admin.execute(
                "INSERT INTO public.addresses (id,name,road,house_number,city) "
                "VALUES (-998,'Ignored address','Ignored Road','1','Ignored city'), "
                "(-999,NULL,'Fallback Road','7','Fallback City')"
            )
            admin.execute(
                "INSERT INTO public.geofences (id,name) "
                "VALUES (-998,'Primary geofence'), (-999,'   ')"
            )
            admin.execute(
                "UPDATE public.drives SET start_address_id=-998, end_address_id=-999, "
                "start_geofence_id=-998, end_geofence_id=-999 WHERE id=1"
            )
            admin.execute("UPDATE public.positions SET battery_level=76 WHERE id=10000")
            admin.execute("UPDATE public.positions SET battery_level=61 WHERE id=15000")
            trip = client.get("/api/v1/trips/1")
            assert trip.status_code == 200, trip.text
            assert {
                key: trip.json()[key]
                for key in ("start_place", "end_place", "start_battery_level", "end_battery_level")
            } == {
                "start_place": "Primary geofence",
                "end_place": "Fallback Road 7, Fallback City",
                "start_battery_level": 76,
                "end_battery_level": 61,
            }

            # A position belonging to vehicle 2 cannot satisfy vehicle 1's
            # drive_id join, even when a corrupt optional FK points to it.
            admin.execute("UPDATE public.drives SET start_position_id=20001 WHERE id=5")
            isolated = client.get("/api/v1/trips/5")
            assert isolated.status_code == 200, isolated.text
            assert isolated.json()["start_battery_level"] is None
            assert isolated.json()["start_place"] is None
            assert isolated.json()["end_place"] is None
        finally:
            admin.execute(
                "UPDATE public.drives SET start_position_id=%s WHERE id=5", original_position
            )
            admin.execute(
                "UPDATE public.drives SET start_address_id=%s, end_address_id=%s, "
                "start_geofence_id=%s, end_geofence_id=%s WHERE id=1",
                original_places,
            )
            for identifier, battery_level in original_soc:
                admin.execute(
                    "UPDATE public.positions SET battery_level=%s WHERE id=%s",
                    (battery_level, identifier),
                )
            admin.execute("DELETE FROM public.geofences WHERE id IN (-998,-999)")
            admin.execute("DELETE FROM public.addresses WHERE id IN (-998,-999)")


def test_trip_estimated_energy_uses_saved_basis_and_excludes_invalid_inputs(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T15 exposes only valid estimated values and never falls back between bases."""
    legacy = client.get("/api/v1/trips/1")
    assert legacy.status_code == 200
    assert legacy.json()["estimated_energy_kwh"] is None
    assert legacy.json()["estimated_average_consumption_wh_per_km"] is None

    with optional_grants(admin):
        rated = client.get("/api/v1/trips/1")
        assert rated.status_code == 200, rated.text
        assert {
            key: rated.json()[key]
            for key in ("estimated_energy_kwh", "estimated_average_consumption_wh_per_km")
        } == {
            "estimated_energy_kwh": pytest.approx(2.16),
            "estimated_average_consumption_wh_per_km": pytest.approx(172.8),
        }
        listed = client.get("/api/v1/trips", params={"vehicle_id": 1})
        row = next(item for item in listed.json()["items"] if item["id"] == 1)
        assert row["estimated_energy_kwh"] == pytest.approx(2.16)

        save(client, "preferences", {"range_basis": "ideal"})
        ideal = client.get("/api/v1/trips/1")
        assert ideal.status_code == 200, ideal.text
        assert {
            key: ideal.json()[key]
            for key in ("estimated_energy_kwh", "estimated_average_consumption_wh_per_km")
        } == {
            "estimated_energy_kwh": pytest.approx(2.52),
            "estimated_average_consumption_wh_per_km": pytest.approx(201.6),
        }

        save(client, "preferences", {"range_basis": "rated"})
        original_one = admin.execute(
            "SELECT distance,start_rated_range_km,end_rated_range_km FROM public.drives WHERE id=1"
        ).fetchone()
        original_three = admin.execute(
            "SELECT distance,start_rated_range_km,end_rated_range_km FROM public.drives WHERE id=3"
        ).fetchone()
        try:
            admin.execute("UPDATE public.drives SET distance=0 WHERE id=1")
            zero_distance = client.get("/api/v1/trips/1").json()
            assert zero_distance["estimated_energy_kwh"] is None
            assert zero_distance["estimated_average_consumption_wh_per_km"] is None

            admin.execute(
                "UPDATE public.drives SET distance=10,start_rated_range_km=50,"
                "end_rated_range_km=55 WHERE id=1"
            )
            negative_difference = client.get("/api/v1/trips/1").json()
            assert negative_difference["estimated_energy_kwh"] is None
            assert negative_difference["estimated_average_consumption_wh_per_km"] is None

            admin.execute("UPDATE public.drives SET end_rated_range_km=50 WHERE id=1")
            zero_difference = client.get("/api/v1/trips/1").json()
            assert zero_difference["estimated_energy_kwh"] == 0
            assert zero_difference["estimated_average_consumption_wh_per_km"] == 0

            admin.execute("UPDATE public.drives SET start_rated_range_km='NaN'::numeric WHERE id=1")
            nonfinite_range = client.get("/api/v1/trips/1").json()
            assert nonfinite_range["estimated_energy_kwh"] is None
            assert nonfinite_range["estimated_average_consumption_wh_per_km"] is None

            admin.execute(
                "UPDATE public.drives SET distance=10,start_rated_range_km=100,"
                "end_rated_range_km=90 WHERE id=3"
            )
            missing_efficiency = client.get("/api/v1/trips/3").json()
            assert missing_efficiency["estimated_energy_kwh"] is None
            assert missing_efficiency["estimated_average_consumption_wh_per_km"] is None
        finally:
            admin.execute(
                "UPDATE public.drives SET distance=%s,start_rated_range_km=%s,"
                "end_rated_range_km=%s WHERE id=1",
                original_one,
            )
            admin.execute(
                "UPDATE public.drives SET distance=%s,start_rated_range_km=%s,"
                "end_rated_range_km=%s WHERE id=3",
                original_three,
            )


def test_trip_period_summary_aggregates_full_window_and_reports_coverage(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T16 keeps unfinished/invalid rows out of totals and one common energy denominator."""
    params = {
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-02-01T00:00:00Z",
    }
    with trip_summary_fixture(admin):
        legacy = client.get("/api/v1/vehicles/97/trip-summary", params=params)
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["estimate_capability"] == {
            "available": False,
            "reason": "insufficient_permissions",
        }
        assert legacy.json()["distance_km"] == 30
        assert legacy.json()["estimated_energy_kwh"] is None

        with optional_grants(admin):
            response = client.get("/api/v1/vehicles/97/trip-summary", params=params)
            assert response.status_code == 200, response.text
            result = response.json()
            assert {
                key: result[key]
                for key in (
                    "total_count",
                    "ended_count",
                    "not_ended_count",
                    "distance_km",
                    "duration_min",
                    "estimated_energy_kwh",
                    "estimated_average_consumption_wh_per_km",
                )
            } == {
                "total_count": 5,
                "ended_count": 4,
                "not_ended_count": 1,
                "distance_km": 30,
                "duration_min": 90,
                "estimated_energy_kwh": pytest.approx(1.8),
                # This is 1.8 kWh / the same 30 km eligible records, not the
                # average of 180 and 0 Wh/km per-trip averages.
                "estimated_average_consumption_wh_per_km": pytest.approx(60),
            }
            assert result["estimate_capability"] == {"available": True, "reason": None}
            assert result["distance_coverage"] == {
                "applicable_count": 4,
                "valid_count": 2,
                "reason": None,
            }
            assert result["duration_coverage"] == result["distance_coverage"]
            assert result["estimated_energy_coverage"] == {
                "applicable_count": 4,
                "valid_count": 2,
                "reason": None,
            }

            save(client, "preferences", {"range_basis": "ideal"})
            ideal = client.get("/api/v1/vehicles/97/trip-summary", params=params).json()
            assert ideal["estimated_energy_kwh"] == pytest.approx(3.6)
            save(client, "preferences", {"range_basis": "rated"})

            empty = client.get(
                "/api/v1/vehicles/97/trip-summary",
                params={"start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
            ).json()
            assert empty["total_count"] == empty["ended_count"] == empty["not_ended_count"] == 0
            empty_totals = (
                "distance_km",
                "duration_min",
                "estimated_energy_kwh",
                "estimated_average_consumption_wh_per_km",
            )
            assert all(empty[key] == 0 for key in empty_totals)
            assert all(
                value["reason"] is None
                for key, value in empty.items()
                if key.endswith("_coverage")
            )

            missing = client.get(
                "/api/v1/vehicles/97/trip-summary",
                params={"start": "2025-01-05T00:00:00Z", "end": "2025-01-06T00:00:00Z"},
            ).json()
            assert missing["distance_km"] is None
            assert missing["distance_coverage"] == {
                "applicable_count": 1,
                "valid_count": 0,
                "reason": "no_valid_values",
            }

            unfinished = client.get(
                "/api/v1/vehicles/97/trip-summary",
                params={"start": "2025-01-04T00:00:00Z", "end": "2025-01-05T00:00:00Z"},
            ).json()
            assert unfinished["distance_km"] is None
            assert unfinished["distance_coverage"] == {
                "applicable_count": 0,
                "valid_count": 0,
                "reason": "no_ended_records",
            }
            other_vehicle = client.get("/api/v1/vehicles/1/trip-summary", params=params).json()
            assert other_vehicle["total_count"] == 0


def test_charge_period_summary_aggregates_full_window_and_reports_cost_coverage(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T17 keeps provisional and unknown charges out of aggregate totals."""
    params = {
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-02-01T00:00:00Z",
    }
    with charge_summary_fixture(admin):
        legacy = client.get("/api/v1/vehicles/98/charge-summary", params=params)
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["cost_capability"] == {
            "available": False,
            "reason": "insufficient_permissions",
        }
        assert legacy.json()["energy_added_kwh"] == 10
        assert legacy.json()["cost"] is None
        assert legacy.json()["cost_coverage"] == {
            "applicable_count": 4,
            "valid_count": 0,
            "reason": "unavailable",
        }

        with column_select_grant(admin, "charging_processes", "cost"):
            unset = client.get("/api/v1/vehicles/98/charge-summary", params=params)
            assert unset.status_code == 200, unset.text
            result = unset.json()
            assert {
                key: result[key]
                for key in (
                    "total_count",
                    "ended_count",
                    "not_ended_count",
                    "energy_added_kwh",
                    "duration_min",
                    "cost",
                    "currency",
                )
            } == {
                "total_count": 5,
                "ended_count": 4,
                "not_ended_count": 1,
                "energy_added_kwh": 10,
                "duration_min": 30,
                "cost": None,
                "currency": None,
            }
            assert result["energy_added_coverage"] == {
                "applicable_count": 4,
                "valid_count": 2,
                "reason": None,
            }
            assert result["duration_coverage"] == result["energy_added_coverage"]
            assert result["cost_coverage"] == {
                "applicable_count": 4,
                "valid_count": 2,
                "reason": None,
            }
            assert result["cost_capability"] == {"available": True, "reason": None}

            save(client, "preferences", {"display_currency": "EUR"})
            configured = client.get("/api/v1/vehicles/98/charge-summary", params=params).json()
            assert configured["cost"] == 12.5
            assert configured["currency"] == "EUR"

            # Start ownership and half-open bounds exclude the Feb 1 row.
            boundary = client.get(
                "/api/v1/vehicles/98/charge-summary",
                params={"start": "2025-02-01T00:00:00Z", "end": "2025-02-02T00:00:00Z"},
            ).json()
            assert boundary["total_count"] == boundary["ended_count"] == 1
            assert (
                boundary["energy_added_kwh"] == boundary["duration_min"] == boundary["cost"] == 99
            )

            empty = client.get(
                "/api/v1/vehicles/98/charge-summary",
                params={"start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
            ).json()
            assert empty["total_count"] == empty["ended_count"] == empty["not_ended_count"] == 0
            assert empty["energy_added_kwh"] == empty["duration_min"] == empty["cost"] == 0
            assert all(
                value["reason"] is None
                for key, value in empty.items()
                if key.endswith("_coverage")
            )

            missing = client.get(
                "/api/v1/vehicles/98/charge-summary",
                params={"start": "2025-01-05T00:00:00Z", "end": "2025-01-06T00:00:00Z"},
            ).json()
            assert missing["energy_added_kwh"] is None
            assert missing["cost"] is None
            assert missing["energy_added_coverage"] == missing["cost_coverage"] == {
                "applicable_count": 1,
                "valid_count": 0,
                "reason": "no_valid_values",
            }

            unfinished = client.get(
                "/api/v1/vehicles/98/charge-summary",
                params={"start": "2025-01-04T00:00:00Z", "end": "2025-01-05T00:00:00Z"},
            ).json()
            assert unfinished["energy_added_kwh"] is None
            assert unfinished["energy_added_coverage"] == {
                "applicable_count": 0,
                "valid_count": 0,
                "reason": "no_ended_records",
            }
            other_vehicle = client.get("/api/v1/vehicles/1/charge-summary", params=params).json()
            assert other_vehicle["total_count"] == 0


def test_charge_summary_timeout_is_an_error(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with admin.transaction():
        admin.execute("LOCK public.charging_processes IN ACCESS EXCLUSIVE MODE")
        response = client.get(
            "/api/v1/vehicles/1/charge-summary",
            params={"start": "2025-01-01T00:00:00Z", "end": "2025-02-01T00:00:00Z"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "timeout"}}


def test_optional_grants_preserves_existing_column_permissions(
    admin: psycopg.Connection[Any],
) -> None:
    """A pre-existing optional grant must survive the shared test helper."""
    with column_select_grant(admin, "cars", "efficiency"):
        assert has_column_select_permission(admin, "cars", "efficiency")
        with optional_grants(admin):
            pass
        assert has_column_select_permission(admin, "cars", "efficiency")


def test_trip_summary_fixture_refuses_existing_rows(admin: psycopg.Connection[Any]) -> None:
    """A setup collision fails before mutation and leaves retained data untouched."""
    with mutation(
        admin,
        "INSERT INTO public.cars (id,name,model,efficiency) VALUES (97,'RETAINED',NULL,NULL)",
        "DELETE FROM public.cars WHERE id=97",
    ):
        with pytest.raises(AssertionError, match="fixture identifiers must be unused"):
            with trip_summary_fixture(admin):
                pass
        retained = admin.execute("SELECT name FROM public.cars WHERE id=97").fetchone()
        assert retained == ("RETAINED",)
        assert (
            admin.execute("SELECT count(*) FROM public.drives WHERE car_id=97").fetchone()[0] == 0
        )


def test_trip_summary_fixture_cleans_up_after_setup_interruption(
    admin: psycopg.Connection[Any],
) -> None:
    """A failed drive insert still removes the car created earlier in setup."""
    with mutation(
        admin,
        "ALTER TABLE public.drives ADD CONSTRAINT t16_fixture_setup_interrupt CHECK (id <> 9601)",
        "ALTER TABLE public.drives DROP CONSTRAINT t16_fixture_setup_interrupt",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with trip_summary_fixture(admin):
                pass
        assert admin.execute("SELECT count(*) FROM public.cars WHERE id=97").fetchone()[0] == 0
        assert (
            admin.execute("SELECT count(*) FROM public.drives WHERE car_id=97").fetchone()[0] == 0
        )


def test_charge_summary_fixture_refuses_existing_rows(admin: psycopg.Connection[Any]) -> None:
    with mutation(
        admin,
        "INSERT INTO public.cars (id,name,model) VALUES (98,'RETAINED',NULL)",
        "DELETE FROM public.cars WHERE id=98",
    ):
        with pytest.raises(AssertionError, match="fixture identifiers must be unused"):
            with charge_summary_fixture(admin):
                pass
        retained = admin.execute("SELECT name FROM public.cars WHERE id=98").fetchone()
        assert retained == ("RETAINED",)
        assert (
            admin.execute(
                "SELECT count(*) FROM public.charging_processes WHERE car_id=98"
            ).fetchone()[0]
            == 0
        )


def test_charge_summary_fixture_cleans_up_after_setup_interruption(
    admin: psycopg.Connection[Any],
) -> None:
    with mutation(
        admin,
        "ALTER TABLE public.charging_processes ADD CONSTRAINT "
        "t17_fixture_setup_interrupt CHECK (id <> 9811)",
        "ALTER TABLE public.charging_processes DROP CONSTRAINT t17_fixture_setup_interrupt",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with charge_summary_fixture(admin):
                pass
        assert admin.execute("SELECT count(*) FROM public.cars WHERE id=98").fetchone()[0] == 0
        assert (
            admin.execute(
                "SELECT count(*) FROM public.charging_processes WHERE car_id=98"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2025-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
        {"start": "2025-01-02T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
        {"start": "2025-01-01T00:00:00", "end": "2025-01-02T00:00:00Z"},
    ],
)
def test_trip_period_summary_rejects_invalid_windows(
    client: TestClient, params: dict[str, str]
) -> None:
    assert client.get("/api/v1/vehicles/1/trip-summary", params=params).status_code == 422


def test_charge_details_distinguish_recorded_values_permissions_and_vehicles(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T14 never turns added energy into recorded use, free, or a location."""
    legacy = client.get("/api/v1/charges/1")
    assert legacy.status_code == 200
    assert legacy.json()["energy_added_kwh"] == 22.5
    assert {
        key: legacy.json()[key]
        for key in (
            "place",
            "start_battery_level",
            "end_battery_level",
            "recorded_energy_used_kwh",
            "cost",
        )
    } == {
        "place": None,
        "start_battery_level": None,
        "end_battery_level": None,
        "recorded_energy_used_kwh": None,
        "cost": None,
    }
    with optional_grants(admin):
        original = admin.execute(
            "SELECT address_id, geofence_id, start_battery_level, end_battery_level, "
            "charge_energy_used, cost FROM public.charging_processes WHERE id=1"
        ).fetchone()
        unfinished = admin.execute(
            "SELECT start_battery_level, end_battery_level, charge_energy_used, cost "
            "FROM public.charging_processes WHERE id=4"
        ).fetchone()
        try:
            # A blank geofence falls through to a complete road/house/city address.
            admin.execute(
                "INSERT INTO public.addresses (id,name,road,house_number,city) "
                "VALUES (-997,NULL,'Charge Road','9','Charge City')"
            )
            admin.execute("INSERT INTO public.geofences (id,name) VALUES (-997,'  ')")
            admin.execute(
                "UPDATE public.charging_processes SET address_id=-997, geofence_id=-997, "
                "start_battery_level=31, end_battery_level=79, "
                "charge_energy_used=NULL, cost=NULL WHERE id=1"
            )
            charge = client.get("/api/v1/charges/1")
            assert charge.status_code == 200, charge.text
            assert {
                key: charge.json()[key]
                for key in (
                    "place",
                    "start_battery_level",
                    "end_battery_level",
                    "energy_added_kwh",
                    "recorded_energy_used_kwh",
                    "cost",
                )
            } == {
                "place": "Charge Road 9, Charge City",
                "start_battery_level": 31,
                "end_battery_level": 79,
                "energy_added_kwh": 22.5,
                "recorded_energy_used_kwh": None,
                "cost": None,
            }

            # Explicit zero is a real free charge, unlike a missing cost.  The
            # record remains owned by vehicle 2 and cannot enter vehicle 1's page.
            free = client.get("/api/v1/charges/3")
            assert free.status_code == 200, free.text
            assert free.json()["vehicle_id"] == 2
            assert free.json()["recorded_energy_used_kwh"] == 13.5
            assert free.json()["cost"] == 0
            vehicle_one = client.get("/api/v1/charges", params={"vehicle_id": 1})
            assert vehicle_one.status_code == 200, vehicle_one.text
            assert 3 not in {item["id"] for item in vehicle_one.json()["items"]}

            # Available source values on an unfinished process remain raw values;
            # the API does not infer that this charge is complete.
            admin.execute(
                "UPDATE public.charging_processes SET start_battery_level=40, "
                "end_battery_level=70, charge_energy_used=12.25, cost=0 WHERE id=4"
            )
            provisional = client.get("/api/v1/charges/4")
            assert provisional.status_code == 200, provisional.text
            assert provisional.json()["end"] is None
            assert {
                key: provisional.json()[key]
                for key in (
                    "start_battery_level",
                    "end_battery_level",
                    "recorded_energy_used_kwh",
                    "cost",
                )
            } == {
                "start_battery_level": 40,
                "end_battery_level": 70,
                "recorded_energy_used_kwh": 12.25,
                "cost": 0,
            }
        finally:
            admin.execute(
                "UPDATE public.charging_processes SET address_id=%s, geofence_id=%s, "
                "start_battery_level=%s, end_battery_level=%s, charge_energy_used=%s, cost=%s "
                "WHERE id=1",
                original,
            )
            admin.execute(
                "UPDATE public.charging_processes SET start_battery_level=%s, "
                "end_battery_level=%s, charge_energy_used=%s, cost=%s WHERE id=4",
                unfinished,
            )
            admin.execute("DELETE FROM public.geofences WHERE id=-997")
            admin.execute("DELETE FROM public.addresses WHERE id=-997")


def test_charge_details_capability_reports_a_missing_optional_grant(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        with mutation(
            admin,
            "REVOKE SELECT (cost) ON public.charging_processes FROM matescope_readonly",
            "GRANT SELECT (cost) ON public.charging_processes TO matescope_readonly",
        ):
            capabilities = client.get("/api/v1/history/capabilities").json()["capabilities"]
            assert capabilities["charge_details"] == {
                "available": False,
                "reason": "insufficient_permissions",
            }
            charge = client.get("/api/v1/charges/3")
            assert charge.status_code == 200, charge.text
            assert charge.json()["energy_added_kwh"] == 15
            assert charge.json()["recorded_energy_used_kwh"] is None
            assert charge.json()["cost"] is None


def test_history_capabilities_validate_all_added_columns(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        response = client.get("/api/v1/history/capabilities")
        assert response.status_code == 200, response.text
        assert all(
            capability == {"available": True, "reason": None}
            for capability in response.json()["capabilities"].values()
        )


def test_trip_series_is_local_when_optional_columns_are_unreadable(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/trips/6/series")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["trip_id"] == 6
    assert result["capability"] == {"available": False, "reason": "insufficient_permissions"}
    assert len(result["series"]) == 6
    assert all(
        series["capability"] == {"available": False, "reason": "insufficient_permissions"}
        and series["points"] == []
        for series in result["series"]
    )


def test_trip_series_raw_samples_keep_gaps_nulls_negative_power_and_equal_times(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """T21 retains raw ordering and never joins samples across a missing interval."""
    original = admin.execute(
        "SELECT id,speed,power,battery_level,inside_temp,outside_temp,elevation "
        "FROM public.positions WHERE id=ANY(%s) ORDER BY id",
        ([20001, 20002],),
    ).fetchall()
    collision = admin.execute("SELECT id FROM public.positions WHERE id=20003").fetchall()
    assert not collision, "T21 fixture identifier must be unused"
    try:
        admin.execute(
            "UPDATE public.positions SET speed=0,power=-1.5,battery_level=75,inside_temp=19,"
            "outside_temp=4,elevation=3 WHERE id=20001"
        )
        admin.execute(
            "UPDATE public.positions SET speed=0,power=-8,battery_level=74,inside_temp=NULL,"
            "outside_temp=5,elevation=6 WHERE id=20002"
        )
        admin.execute(
            "INSERT INTO public.positions "
            "(id,car_id,drive_id,date,battery_level,speed,power,inside_temp,"
            "outside_temp,elevation) "
            "SELECT 20003,car_id,drive_id,date,73,2,-3,20,6,7 FROM public.positions WHERE id=20002"
        )
        with optional_grants(admin):
            response = client.get("/api/v1/trips/6/series")
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["capability"] == {"available": True, "reason": None}
            assert all(series["capability"] == {"available": True, "reason": None}
                       for series in result["series"])
            assert {item["name"] for item in result["series"]} == {
                "speed", "power", "battery", "inside_temperature", "outside_temperature",
                "elevation",
            }
            power = next(item for item in result["series"] if item["name"] == "power")
            assert power["unit"] == "kW"
            assert power["sample_count"] == power["bucket_count"] == 3
            assert power["aggregation"] == "raw"
            assert [point["value"] for point in power["points"]] == [-1.5, -8, -3]
            assert [point["discontinuity"] for point in power["points"]] == [False, True, False]
            inside = next(item for item in result["series"] if item["name"] == "inside_temperature")
            assert inside["points"][1]["value"] is None
            assert inside["points"][1]["discontinuity"] is True
    finally:
        admin.execute("DELETE FROM public.positions WHERE id=20003")
        for row in original:
            admin.execute(
                "UPDATE public.positions SET speed=%s,power=%s,battery_level=%s,inside_temp=%s,"
                "outside_temp=%s,elevation=%s WHERE id=%s",
                (
                    row[1], row[2], row[3], row[4], row[5], row[6], row[0],
                ),
            )
        assert not admin.execute("SELECT id FROM public.positions WHERE id=20003").fetchall()


def test_trip_series_long_records_use_at_most_600_sql_buckets(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    original = admin.execute(
        "SELECT id,power,battery_level FROM public.positions WHERE id=ANY(%s) ORDER BY id",
        ([10000, 15000],),
    ).fetchall()
    try:
        admin.execute("UPDATE public.positions SET power=-4,battery_level=80 WHERE id=10000")
        admin.execute("UPDATE public.positions SET power=-2,battery_level=70 WHERE id=15000")
        with optional_grants(admin):
            response = client.get("/api/v1/trips/1/series")
            assert response.status_code == 200, response.text
            power = next(item for item in response.json()["series"] if item["name"] == "power")
            battery = next(item for item in response.json()["series"] if item["name"] == "battery")
            assert power["sample_count"] == 5001
            assert 1 <= power["bucket_count"] <= 600
            assert power["aggregation"] == "mean_min_max"
            assert any(point["min"] is not None and point["min"] < 0 for point in power["points"])
            assert battery["aggregation"] == "last"
            assert any(point["value"] is not None for point in battery["points"])
    finally:
        for row in original:
            admin.execute(
                "UPDATE public.positions SET power=%s,battery_level=%s WHERE id=%s",
                (row[1], row[2], row[0]),
            )


def test_trip_series_long_records_make_first_single_sample_bucket_continuous(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """The first aggregate bucket has no prior row, so it must still be a bool."""
    identifiers = list(range(10000, 10010))
    original = admin.execute(
        "SELECT id,date,power FROM public.positions WHERE id=ANY(%s) ORDER BY id", (identifiers,)
    ).fetchall()
    assert len(original) == len(identifiers), "T21 long-record fixture rows must exist"
    try:
        # Keep only id 10000 in the first equal-width bucket.  The next eight
        # values share id 10009's timestamp, which remains in the next bucket.
        admin.execute(
            "UPDATE public.positions SET date=(SELECT date FROM public.positions WHERE id=10009) "
            "WHERE id=ANY(%s)",
            (list(range(10001, 10009)),),
        )
        admin.execute("UPDATE public.positions SET power=-4 WHERE id=10000")
        with optional_grants(admin):
            response = client.get("/api/v1/trips/1/series")
            assert response.status_code == 200, response.text
            power = next(item for item in response.json()["series"] if item["name"] == "power")
            assert power["sample_count"] > 600
            assert power["aggregation"] == "mean_min_max"
            assert power["points"][0]["discontinuity"] is False
            assert all(isinstance(point["discontinuity"], bool) for point in power["points"])
    finally:
        for row in original:
            admin.execute(
                "UPDATE public.positions SET date=%s,power=%s WHERE id=%s",
                (row[1], row[2], row[0]),
            )
        assert admin.execute(
            "SELECT id,date,power FROM public.positions WHERE id=ANY(%s) ORDER BY id",
            (identifiers,),
        ).fetchall() == original


def test_trip_series_missing_one_column_only_hides_its_own_series(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        with mutation(
            admin,
            "REVOKE SELECT (elevation) ON public.positions FROM matescope_readonly",
            "GRANT SELECT (elevation) ON public.positions TO matescope_readonly",
        ):
            response = client.get("/api/v1/trips/6/series")
            assert response.status_code == 200, response.text
            series = {item["name"]: item for item in response.json()["series"]}
            assert series["elevation"]["capability"] == {
                "available": False,
                "reason": "insufficient_permissions",
            }
            assert series["elevation"]["points"] == []
            assert series["power"]["capability"] == {"available": True, "reason": None}


@contextmanager
def charge_series_fixture(
    admin: psycopg.Connection[Any], charge_id: int, sample_ids: list[int]
) -> Iterator[None]:
    """Create a fully owned charge process and remove precisely those rows."""
    assert not admin.execute(
        "SELECT id FROM public.charging_processes WHERE id=%s", (charge_id,)
    ).fetchall(), "T22 charging-process identifier must be unused"
    assert not admin.execute(
        "SELECT id FROM public.charges WHERE id = ANY(%s)", (sample_ids,)
    ).fetchall(), "T22 charge-sample identifiers must be unused"
    created_process = False
    try:
        admin.execute(
            "INSERT INTO public.charging_processes "
            "(id,car_id,start_date,end_date,charge_energy_added,duration_min) "
            "VALUES (%s,1,TIMESTAMP '2025-03-01 00:00:00',"
            "TIMESTAMP '2025-03-01 12:00:00',1,1)",
            (charge_id,),
        )
        created_process = True
        yield
    finally:
        if created_process:
            deleted_samples = admin.execute(
                "DELETE FROM public.charges WHERE charging_process_id=%s RETURNING id",
                (charge_id,),
            ).fetchall()
            assert {row[0] for row in deleted_samples} == set(sample_ids)
            deleted_process = admin.execute(
                "DELETE FROM public.charging_processes WHERE id=%s RETURNING id", (charge_id,)
            ).fetchall()
            assert deleted_process == [(charge_id,)]
        assert not admin.execute(
            "SELECT id FROM public.charging_processes WHERE id=%s", (charge_id,)
        ).fetchall()
        assert not admin.execute(
            "SELECT id FROM public.charges WHERE id = ANY(%s)", (sample_ids,)
        ).fetchall()


def test_charge_series_is_local_when_optional_columns_are_unreadable(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/charges/1/series")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["charge_id"] == 1
    assert result["capability"] == {"available": False, "reason": "insufficient_permissions"}
    assert {item["name"] for item in result["series"]} == {
        "power", "battery", "outside_temperature"
    }
    assert all(
        series["capability"] == {"available": False, "reason": "insufficient_permissions"}
        and series["points"] == []
        for series in result["series"]
    )


def test_charge_series_raw_samples_keep_gaps_nulls_negative_power_and_equal_times(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    charge_id = 9822
    sample_ids = [982201, 982202, 982203]
    with charge_series_fixture(admin, charge_id, sample_ids):
        admin.execute(
            "INSERT INTO public.charges "
            "(id,charging_process_id,date,charger_power,battery_level,outside_temp) VALUES "
            "(982201,9822,TIMESTAMP '2025-03-01 00:00:00',-1.5,75,4),"
            "(982202,9822,TIMESTAMP '2025-03-01 00:00:00',-8,74,NULL),"
            "(982203,9822,TIMESTAMP '2025-03-01 00:06:00',-3,73,6)"
        )
        with optional_grants(admin):
            response = client.get(f"/api/v1/charges/{charge_id}/series")
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["capability"] == {"available": True, "reason": None}
            assert all(item["capability"] == {"available": True, "reason": None}
                       for item in result["series"])
            power = next(item for item in result["series"] if item["name"] == "power")
            assert power["unit"] == "kW"
            assert power["sample_count"] == power["bucket_count"] == 3
            assert power["aggregation"] == "raw"
            assert [point["value"] for point in power["points"]] == [-1.5, -8, -3]
            assert [point["discontinuity"] for point in power["points"]] == [False, False, True]
            temperature = next(
                item for item in result["series"] if item["name"] == "outside_temperature"
            )
            assert temperature["points"][1]["value"] is None
            assert temperature["points"][1]["discontinuity"] is True


def test_charge_series_long_records_use_at_most_600_sql_buckets(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    charge_id = 9823
    sample_ids = list(range(982300, 982901))
    with charge_series_fixture(admin, charge_id, sample_ids):
        admin.execute(
            "INSERT INTO public.charges "
            "(id,charging_process_id,date,charger_power,battery_level,outside_temp) "
            "SELECT 982300 + sample,9823,TIMESTAMP '2025-03-01 00:00:00' + "
            "sample * INTERVAL '1 minute',CASE WHEN sample=300 THEN -4 ELSE 7 END,"
            "80 - mod(sample,20),5 FROM generate_series(0,600) AS sample"
        )
        with optional_grants(admin):
            response = client.get(f"/api/v1/charges/{charge_id}/series")
            assert response.status_code == 200, response.text
            power = next(item for item in response.json()["series"] if item["name"] == "power")
            battery = next(item for item in response.json()["series"] if item["name"] == "battery")
            assert power["sample_count"] == 601
            assert 1 <= power["bucket_count"] <= 600
            assert power["aggregation"] == "mean_min_max"
            assert any(point["min"] is not None and point["min"] < 0 for point in power["points"])
            assert battery["aggregation"] == "last"
            assert any(point["value"] is not None for point in battery["points"])


def test_charge_series_missing_one_column_only_hides_its_own_series(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        with mutation(
            admin,
            "REVOKE SELECT (outside_temp) ON public.charges FROM matescope_readonly",
            "GRANT SELECT (outside_temp) ON public.charges TO matescope_readonly",
        ):
            response = client.get("/api/v1/charges/1/series")
            assert response.status_code == 200, response.text
            series = {item["name"]: item for item in response.json()["series"]}
            assert series["outside_temperature"]["capability"] == {
                "available": False,
                "reason": "insufficient_permissions",
            }
            assert series["outside_temperature"]["points"] == []
            assert series["power"]["capability"] == {"available": True, "reason": None}


def test_charge_series_rejects_unknown_charge(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with optional_grants(admin):
        response = client.get("/api/v1/charges/999999/series")
    assert response.status_code == 404


def test_history_capabilities_report_schema_and_permission_separately(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with mutation(
        admin,
        "GRANT SELECT (efficiency) ON public.cars TO matescope_readonly",
        "REVOKE SELECT (efficiency) ON public.cars FROM matescope_readonly",
    ):
        result = client.get("/api/v1/history/capabilities").json()["capabilities"]
        assert result["trip_details"] == {
            "available": False,
            "reason": "insufficient_permissions",
        }
    with mutation(
        admin,
        "ALTER TABLE public.charges ALTER COLUMN charger_power TYPE text "
        "USING charger_power::text",
        "ALTER TABLE public.charges ALTER COLUMN charger_power TYPE numeric "
        "USING charger_power::numeric",
    ):
        result = client.get("/api/v1/history/capabilities").json()["capabilities"]
        assert result["charge_series"] == {
            "available": False,
            "reason": "incompatible_schema",
        }


def test_history_capabilities_reject_unsafe_role(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    with mutation(
        admin,
        "GRANT SELECT ON public.tokens TO matescope_readonly",
        "REVOKE SELECT ON public.tokens FROM matescope_readonly",
    ):
        response = client.get("/api/v1/history/capabilities")
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "unsafe_permissions"}}


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


def test_complete_history_pagination_binds_cursor_to_kind_vehicle_and_window(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    """A resolved multi-year window remains complete across small cursor pages."""
    admin.execute("INSERT INTO public.cars (id, name, model) VALUES (99, 'PAGING', NULL)")
    admin.execute(
        "INSERT INTO public.drives "
        "(id, car_id, start_date, end_date, distance, duration_min, speed_max) VALUES "
        "(9701, 99, TIMESTAMP '2020-01-02 00:00:00', TIMESTAMP '2020-01-02 00:10:00', 1, 10, 10), "
        "(9702, 99, TIMESTAMP '2022-06-15 12:00:00', TIMESTAMP '2022-06-15 12:10:00', 2, 10, 20), "
        "(9703, 99, TIMESTAMP '2022-06-15 12:00:00', TIMESTAMP '2022-06-15 12:10:00', 3, 10, 30), "
        "(9704, 99, TIMESTAMP '2025-12-31 23:00:00', TIMESTAMP '2025-12-31 23:10:00', 4, 10, 40)"
    )
    admin.execute(
        "INSERT INTO public.charging_processes "
        "(id, car_id, start_date, end_date, charge_energy_added, duration_min) VALUES "
        "(9801, 99, TIMESTAMP '2020-01-02 00:00:00', TIMESTAMP '2020-01-02 00:10:00', 1, 10), "
        "(9802, 99, TIMESTAMP '2022-06-15 12:00:00', TIMESTAMP '2022-06-15 12:10:00', 2, 10), "
        "(9803, 99, TIMESTAMP '2022-06-15 12:00:00', TIMESTAMP '2022-06-15 12:10:00', 3, 10), "
        "(9804, 99, TIMESTAMP '2025-12-31 23:00:00', TIMESTAMP '2025-12-31 23:10:00', 4, 10)"
    )
    try:
        window = {
            "vehicle_id": 99,
            "start": "2020-01-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
            "limit": 2,
        }
        pages = (
            ("trips", [9704, 9703, 9702, 9701]),
            ("charges", [9804, 9803, 9802, 9801]),
        )
        for path, expected in pages:
            first = client.get(f"/api/v1/{path}", params=window)
            assert first.status_code == 200, first.text
            page = first.json()
            ids = [item["id"] for item in page["items"]]
            assert page["next_cursor"]

            cursor = page["next_cursor"]
            while cursor:
                response = client.get(
                    f"/api/v1/{path}",
                    params={"vehicle_id": 99, "limit": 2, "cursor": cursor},
                )
                assert response.status_code == 200, response.text
                page = response.json()
                ids.extend(item["id"] for item in page["items"])
                cursor = page["next_cursor"]
            assert ids == expected
            assert len(ids) == len(set(ids))

        trip_cursor = client.get("/api/v1/trips", params=window).json()["next_cursor"]
        assert trip_cursor
        assert client.get(
            "/api/v1/trips", params={"cursor": trip_cursor, "vehicle_id": 1}
        ).status_code == 422
        assert client.get(
            "/api/v1/trips", params={"cursor": trip_cursor, "start": "2021-01-01T00:00:00Z"}
        ).status_code == 422
        assert client.get(
            "/api/v1/charges", params={"cursor": trip_cursor, "vehicle_id": 99}
        ).status_code == 422
    finally:
        admin.execute("DELETE FROM public.drives WHERE car_id=99")
        admin.execute("DELETE FROM public.charging_processes WHERE car_id=99")
        admin.execute("DELETE FROM public.cars WHERE id=99")


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-01-01"},
        {"start": "2026-01-01T00:00:00"},
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


def utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def test_calendar_boundaries_handle_dst_leap_days_and_years() -> None:
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 4, 2, 10, 30, tzinfo=UTC)

    start, end = resolve_calendar_window("custom", zone, now, date(2024, 3, 31), date(2024, 4, 1))
    assert (utc_iso(start), utc_iso(end)) == ("2024-03-30T23:00:00Z", "2024-04-01T22:00:00Z")

    start, end = resolve_calendar_window(
        "custom", zone, now, date(2024, 10, 27), date(2024, 10, 28)
    )
    assert (utc_iso(start), utc_iso(end)) == ("2024-10-26T22:00:00Z", "2024-10-28T23:00:00Z")

    start, end = resolve_calendar_window("custom", zone, now, date(2024, 2, 29), date(2024, 2, 29))
    assert (utc_iso(start), utc_iso(end)) == ("2024-02-28T23:00:00Z", "2024-02-29T23:00:00Z")

    start, end = resolve_calendar_window("this_year", zone, now)
    assert (utc_iso(start), utc_iso(end)) == ("2025-12-31T23:00:00Z", "2026-04-02T10:30:00Z")

    start, end = resolve_calendar_window("today", zone, now)
    assert (utc_iso(start), utc_iso(end)) == ("2026-04-01T22:00:00Z", "2026-04-02T10:30:00Z")
    start, end = resolve_calendar_window("last_7_days", zone, now)
    assert (utc_iso(start), utc_iso(end)) == ("2026-03-26T23:00:00Z", "2026-04-02T10:30:00Z")
    start, end = resolve_calendar_window("last_30_days", zone, now)
    assert (utc_iso(start), utc_iso(end)) == ("2026-03-03T23:00:00Z", "2026-04-02T10:30:00Z")
    start, end = resolve_calendar_window("this_month", zone, now)
    assert (utc_iso(start), utc_iso(end)) == ("2026-03-31T22:00:00Z", "2026-04-02T10:30:00Z")
    start, end = resolve_calendar_window("all_history", zone, now)
    assert start is None and utc_iso(end) == "2026-04-02T10:30:00Z"


def test_history_window_endpoint_resolves_saved_timezone_and_empty_status(
    client: TestClient, admin: psycopg.Connection[Any]
) -> None:
    save(client, "preferences", {"timezone": "Europe/Amsterdam"})
    selected = client.get(
        "/api/v1/vehicles/1/history-window",
        params={"preset": "custom", "start_date": "2024-06-11", "end_date": "2024-06-11"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.headers["cache-control"] == "no-store"
    assert selected.json() == {
        "preset": "custom",
        "timezone": "Europe/Amsterdam",
        "start": "2024-06-10T22:00:00Z",
        "end": "2024-06-11T22:00:00Z",
        "is_empty": False,
    }

    all_history = client.get("/api/v1/vehicles/2/history-window", params={"preset": "all_history"})
    assert all_history.status_code == 200, all_history.text
    assert all_history.json()["start"] == "2021-02-03T09:00:00Z"
    assert all_history.json()["end"] is not None and not all_history.json()["is_empty"]

    admin.execute("INSERT INTO public.cars (id, name, model) VALUES (99, 'EMPTY', NULL)")
    try:
        empty = client.get("/api/v1/vehicles/99/history-window", params={"preset": "all_history"})
        assert empty.status_code == 200, empty.text
        assert empty.json() == {
            "preset": "all_history",
            "timezone": "Europe/Amsterdam",
            "start": None,
            "end": None,
            "is_empty": True,
        }
    finally:
        admin.execute("DELETE FROM public.cars WHERE id=99")


def test_history_window_rejects_invalid_dates_and_unknown_vehicles(client: TestClient) -> None:
    cases = (
        {"preset": "custom"},
        {"preset": "custom", "start_date": "2024-02-30", "end_date": "2024-03-01"},
        {"preset": "custom", "start_date": "2024-03-02", "end_date": "2024-03-01"},
        {"preset": "today", "start_date": "2024-03-01"},
        {"preset": "all_history", "end_date": "2024-03-01"},
        {
            "preset": "custom",
            "start_date": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
            "end_date": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
        },
    )
    for params in cases:
        response = client.get("/api/v1/vehicles/1/history-window", params=params)
        assert response.status_code == 422, response.text
    assert client.get("/api/v1/vehicles/999/history-window").status_code == 404
