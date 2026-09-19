#!/usr/bin/env python3
# ruff: noqa: E501
"""Seed and verify the isolated M1-T33 ten-year synthetic scale database.

This tool deliberately refuses every DSN except the dedicated loopback Compose
database.  It never creates indexes or changes the TeslaMate schema: all data
is synthetic and the caller removes the owned Compose volumes after inspection.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from matescope.config import Settings
from matescope.main import create_app
from matescope.postgresql import DataSource, classify
from psycopg.conninfo import conninfo_to_dict

GUARD = "matescope-synthetic-m1-t33"
TRIPS = 100_000
CHARGES = 30_000
TRIP_SAMPLES = 1_000_000
LONG_CHARGE_ID = 2_000_001
OWNED_ORIGIN = "http://127.0.0.1:49233"


def require_dsn() -> str:
    dsn = os.environ.get("MATESCOPE_M1_T33_PG_DSN", "")
    params = conninfo_to_dict(dsn)
    if (
        params.get("host") not in {"127.0.0.1", "localhost"}
        or params.get("dbname") != "teslamate_synthetic"
        or params.get("user") != "teslamate_admin"
    ):
        raise SystemExit("M1-T33 requires its explicit loopback synthetic admin DSN")
    return dsn


def elapsed(label: str, fn: Any, results: dict[str, Any]) -> Any:
    started = time.monotonic()
    value = fn()
    results["timings_ms"][label] = round((time.monotonic() - started) * 1000, 2)
    return value


def seed(database: psycopg.Connection[Any], results: dict[str, Any]) -> None:
    guard = database.execute("SELECT identity FROM public.matescope_synthetic_guard").fetchall()
    if guard != [(GUARD,)]:
        raise RuntimeError("M1-T33 guard mismatch; no scale mutations performed")
    existing_row = database.execute("SELECT count(*) FROM public.drives WHERE car_id=33").fetchone()
    assert existing_row is not None
    existing = existing_row[0]
    if existing:
        raise RuntimeError("M1-T33 scale data already exists; use an empty owned volume")
    database.execute(
        "INSERT INTO public.cars (id,name,model,efficiency) VALUES (33,'SYNTHETIC Scale','Model S',0.16)"
    )
    elapsed(
        "seed_trips",
        lambda: database.execute(
            "INSERT INTO public.drives "
            "(id,car_id,start_date,end_date,distance,duration_min,speed_max,"
            "start_rated_range_km,end_rated_range_km,start_ideal_range_km,end_ideal_range_km) "
            "SELECT 1000000+i,33, timestamp '2016-01-01 00:00:00' + i * interval '52 minutes', "
            "timestamp '2016-01-01 00:35:00' + i * interval '52 minutes', "
            "20 + (i % 50),35,110,300,290,330,318 FROM generate_series(1,100000) AS i"
        ),
        results,
    )
    elapsed(
        "seed_trip_samples",
        lambda: database.execute(
            "INSERT INTO public.positions "
            "(id,car_id,drive_id,date,latitude,longitude,battery_level,speed,power,inside_temp,outside_temp,elevation) "
            "SELECT 2000000 + (d.i-1)*10+s.j,33,1000000+d.i,"
            "timestamp '2016-01-01 00:00:00' + d.i * interval '52 minutes' + s.j * interval '3 minutes 30 seconds',"
            "52.0,4.0,80-(s.j%10),40+(s.j%70),CASE WHEN s.j=5 THEN NULL ELSE -12+(s.j%30) END,20,10,5 "
            "FROM generate_series(1,100000) AS d(i) CROSS JOIN generate_series(0,9) AS s(j)"
        ),
        results,
    )
    elapsed(
        "seed_charging_processes",
        lambda: database.execute(
            "INSERT INTO public.charging_processes "
            "(id,car_id,start_date,end_date,charge_energy_added,duration_min,start_battery_level,end_battery_level,charge_energy_used,cost) "
            "SELECT 2000000+i,33,timestamp '2016-01-01 00:00:00' + i * interval '3 hours',"
            "timestamp '2016-01-01 00:45:00' + i * interval '3 hours',20,45,30,70,22,4.5 "
            "FROM generate_series(1,30000) AS i"
        ),
        results,
    )
    elapsed(
        "seed_long_charge_samples",
        lambda: database.execute(
            "INSERT INTO public.charges "
            "(id,charging_process_id,date,battery_level,rated_battery_range_km,ideal_battery_range_km,charger_power,outside_temp) "
            "SELECT 3000000+i,2000001,timestamp '2016-01-01 03:00:00' + i * interval '1 minute',"
            "30+(i%60),120+(i%100),150+(i%100),CASE WHEN i BETWEEN 400 AND 410 THEN NULL ELSE 7+(i%20) END,10 "
            "FROM generate_series(0,1199) AS i"
        ),
        results,
    )
    # This throwaway database models the post-upgrade reader capability so the
    # scale run exercises every M1 series.  Compose volume removal reverts it.
    database.execute("GRANT SELECT (efficiency) ON public.cars TO matescope_readonly")
    database.execute(
        "GRANT SELECT (start_position_id,end_position_id,start_address_id,end_address_id,"
        "start_geofence_id,end_geofence_id,start_rated_range_km,end_rated_range_km,"
        "start_ideal_range_km,end_ideal_range_km) ON public.drives TO matescope_readonly"
    )
    database.execute(
        "GRANT SELECT (battery_level,speed,power,inside_temp,outside_temp,elevation,"
        "car_id,odometer,rated_battery_range_km,ideal_battery_range_km) "
        "ON public.positions TO matescope_readonly"
    )
    database.execute(
        "GRANT SELECT (address_id,geofence_id,start_battery_level,end_battery_level,"
        "charge_energy_used,cost) ON public.charging_processes TO matescope_readonly"
    )
    database.execute(
        "GRANT SELECT (id,charging_process_id,date,battery_level,rated_battery_range_km,"
        "ideal_battery_range_km,charger_power,outside_temp) ON public.charges TO matescope_readonly"
    )
    database.execute("GRANT SELECT (id,name,road,house_number,city) ON public.addresses TO matescope_readonly")
    database.execute("GRANT SELECT (id,name) ON public.geofences TO matescope_readonly")


def csrf(client: TestClient) -> dict[str, str]:
    return {
        "Origin": OWNED_ORIGIN,
        "X-CSRF-Token": client.get("/api/v1/auth/csrf").json()["csrf_token"],
    }


def setup_client(dsn: str) -> tuple[TestClient, tempfile.TemporaryDirectory[str]]:
    params = conninfo_to_dict(dsn)
    host, port, database = str(params["host"]), int(str(params["port"])), str(params["dbname"])
    directory = tempfile.TemporaryDirectory(prefix="matescope-m1-t33-")
    settings = Settings(data_dir=directory.name, cookie_secure=False, public_url=OWNED_ORIGIN)
    client = TestClient(create_app(settings), base_url=OWNED_ORIGIN)
    client.__enter__()
    created = client.post(
        "/api/v1/setup/administrator",
        headers=csrf(client),
        json={
            "username": "m1-t33-admin",
            "password": "m1-t33-password",
            "password_confirmation": "m1-t33-password",
        },
    )
    assert created.is_success, created.text
    saved = client.put(
        "/api/v1/settings/postgresql",
        headers=csrf(client),
        json={
            "enabled": True,
            "host": host,
            "port": port,
            "database": database,
            "username": "matescope_readonly",
            "sslmode": "disable",
            "password": {"action": "replace", "value": "synthetic-reader-only"},
        },
    )
    assert saved.status_code == 200, saved.text
    return client, directory


def api_checks(dsn: str, results: dict[str, Any]) -> None:
    client, directory = setup_client(dsn)
    try:
        window = elapsed(
            "all_history_window",
            lambda: client.get("/api/v1/vehicles/33/history-window?preset=all_history"),
            results,
        )
        assert window.status_code == 200, window.text
        bounds = window.json()
        assert bounds["start"].startswith("2016-01-01") and not bounds["is_empty"]
        query = f"vehicle_id=33&start={bounds['start']}&end={bounds['end']}&limit=100"
        seen: set[int] = set()
        cursor: str | None = None
        pages = 0
        while True:
            suffix = f"&cursor={cursor}" if cursor else ""
            response = client.get(f"/api/v1/trips?{query}{suffix}")
            assert response.status_code == 200, response.text
            body = response.json()
            ids = [row["id"] for row in body["items"]]
            assert not (set(ids) & seen), "duplicate trip found across all-history pages"
            seen.update(ids)
            pages += 1
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == TRIPS, f"all-history paging lost trips: {len(seen)}"
        results["trip_pages"] = pages
        results["trip_page_items"] = len(seen)
        charge_seen: set[int] = set()
        charge_cursor: str | None = None
        charge_pages = 0
        charge_first_page_bytes = 0
        charge_paging_started = time.monotonic()
        while True:
            suffix = f"&cursor={charge_cursor}" if charge_cursor else ""
            response = client.get(f"/api/v1/charges?{query}{suffix}")
            assert response.status_code == 200, response.text
            body = response.json()
            if charge_pages == 0:
                charge_first_page_bytes = len(response.content)
            ids = [row["id"] for row in body["items"]]
            assert not (set(ids) & charge_seen), "duplicate charge found across all-history pages"
            charge_seen.update(ids)
            charge_pages += 1
            charge_cursor = body["next_cursor"]
            if charge_cursor is None:
                break
        expected_charge_ids = set(range(2_000_001, 2_000_001 + CHARGES))
        assert charge_seen == expected_charge_ids, (
            f"all-history paging lost or changed charges: {len(charge_seen)}"
        )
        results["charge_pages"] = charge_pages
        results["charge_page_items"] = len(charge_seen)
        results["timings_ms"]["charge_paging"] = round(
            (time.monotonic() - charge_paging_started) * 1000, 2
        )
        results["payload_bytes"]["charge_first_page"] = charge_first_page_bytes
        trip_summary = elapsed(
            "trip_summary",
            lambda: client.get(
                f"/api/v1/vehicles/33/trip-summary?start={bounds['start']}&end={bounds['end']}"
            ),
            results,
        )
        charge_summary = elapsed(
            "charge_summary",
            lambda: client.get(
                f"/api/v1/vehicles/33/charge-summary?start={bounds['start']}&end={bounds['end']}"
            ),
            results,
        )
        assert trip_summary.status_code == 200 and charge_summary.status_code == 200
        assert trip_summary.json()["total_count"] == TRIPS
        assert charge_summary.json()["total_count"] == CHARGES
        assert trip_summary.json()["total_count"] > 100
        trip_series = elapsed(
            "trip_series", lambda: client.get("/api/v1/trips/1000001/series"), results
        )
        charge_series = elapsed(
            "charge_series", lambda: client.get(f"/api/v1/charges/{LONG_CHARGE_ID}/series"), results
        )
        assert trip_series.status_code == 200 and charge_series.status_code == 200
        for series in trip_series.json()["series"] + charge_series.json()["series"]:
            assert series["bucket_count"] <= 600
        assert charge_series.json()["series"][0]["sample_count"] == 1200
        assert charge_series.json()["series"][0]["bucket_count"] == 600
        assert any(point["discontinuity"] for point in charge_series.json()["series"][0]["points"])
        for name, response in {
            "trip_summary": trip_summary,
            "charge_summary": charge_summary,
            "trip_series": trip_series,
            "charge_series": charge_series,
        }.items():
            results["payload_bytes"][name] = len(response.content)
    finally:
        client.__exit__(None, None, None)
        directory.cleanup()


def main() -> None:
    dsn = require_dsn()
    results: dict[str, Any] = {
        "guard": GUARD,
        "started_at": datetime.now(UTC).isoformat(),
        "timings_ms": {},
        "payload_bytes": {},
    }
    with psycopg.connect(dsn, autocommit=True) as database:
        version = database.execute("SHOW server_version").fetchone()
        assert version is not None
        results["postgres_version"] = version[0]
        seed(database, results)
        counts = database.execute(
            "SELECT (SELECT count(*) FROM drives WHERE car_id=33), (SELECT count(*) FROM charging_processes WHERE car_id=33), (SELECT count(*) FROM positions WHERE car_id=33), (SELECT count(*) FROM charges WHERE charging_process_id=%s)",
            (LONG_CHARGE_ID,),
        ).fetchone()
        assert counts == (TRIPS, CHARGES, TRIP_SAMPLES, 1200), counts
        results["counts"] = {
            "trips": counts[0],
            "charging_processes": counts[1],
            "trip_samples": counts[2],
            "long_charge_samples": counts[3],
        }
    api_checks(dsn, results)
    source = DataSource()
    try:
        from matescope.settings import PostgreSQLResponse

        params = conninfo_to_dict(dsn)
        config = PostgreSQLResponse(
            enabled=True,
            host=str(params["host"]),
            port=int(str(params["port"])),
            database=str(params["dbname"]),
            username="matescope_readonly",
            sslmode="disable",
            version=33,
        )
        started = time.monotonic()
        try:
            with source.connection(config, "synthetic-reader-only") as connection:
                connection.execute("SELECT pg_sleep(6)")
            raise AssertionError("timeout query unexpectedly succeeded")
        except Exception as error:
            assert classify(error) == "timeout", repr(error)
        results["timings_ms"]["explicit_timeout"] = round((time.monotonic() - started) * 1000, 2)
    finally:
        source.close()
    results["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
