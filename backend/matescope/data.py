"""Authenticated, bounded historical summaries; all units are explicit."""

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg import sql
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .auth import require_admin, storage
from .postgresql import CapabilityReason, capability_status, classify, snapshot
from .settings import Currency, MQTTResponse, PostgreSQLResponse, SMTPResponse, read_settings

router = APIRouter(prefix="/api/v1", tags=["history"], dependencies=[Depends(require_admin)])


class Vehicle(BaseModel):
    id: int
    name: str | None
    model: str | None


class Vehicles(BaseModel):
    items: list[Vehicle]


class Summary(BaseModel):
    id: int
    vehicle_id: int
    start: datetime
    end: datetime | None
    duration_min: float | None

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class Trip(Summary):
    distance_km: float | None
    speed_max_kmh: float | None
    start_place: str | None
    end_place: str | None
    start_battery_level: int | None
    end_battery_level: int | None
    estimated_energy_kwh: float | None
    estimated_average_consumption_wh_per_km: float | None


class Charge(Summary):
    energy_added_kwh: float | None
    place: str | None
    start_battery_level: int | None
    end_battery_level: int | None
    recorded_energy_used_kwh: float | None
    cost: float | None


class TripPage(BaseModel):
    items: list[Trip]
    next_cursor: str | None
    start: datetime
    end: datetime


class ChargePage(BaseModel):
    items: list[Charge]
    next_cursor: str | None
    start: datetime
    end: datetime


class MetricCoverage(BaseModel):
    """Validity of one period aggregate; applicability is every ended drive."""

    applicable_count: int
    valid_count: int
    reason: Literal["no_ended_records", "no_valid_values", "zero_denominator", "unavailable"] | None


class Point(BaseModel):
    id: int
    time: datetime
    latitude: float
    longitude: float
    segment_id: int

    @field_validator("time")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class Trajectory(BaseModel):
    trip_id: int
    points: list[Point]
    simplified: bool
    total_points: int


class Diagnostics(BaseModel):
    postgresql: PostgreSQLResponse
    mqtt: MQTTResponse
    smtp: SMTPResponse


class Capability(BaseModel):
    available: bool
    reason: CapabilityReason | None = None


class TripPeriodSummary(BaseModel):
    vehicle_id: int
    start: datetime
    end: datetime
    total_count: int
    ended_count: int
    not_ended_count: int
    distance_km: float | None
    duration_min: float | None
    estimated_energy_kwh: float | None
    estimated_average_consumption_wh_per_km: float | None
    distance_coverage: MetricCoverage
    duration_coverage: MetricCoverage
    estimated_energy_coverage: MetricCoverage
    estimated_average_consumption_coverage: MetricCoverage
    estimate_capability: Capability

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ChargePeriodSummary(BaseModel):
    vehicle_id: int
    start: datetime
    end: datetime
    total_count: int
    ended_count: int
    not_ended_count: int
    energy_added_kwh: float | None
    duration_min: float | None
    cost: float | None
    currency: Currency | None
    energy_added_coverage: MetricCoverage
    duration_coverage: MetricCoverage
    cost_coverage: MetricCoverage
    cost_capability: Capability

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class HistoryCapabilities(BaseModel):
    capabilities: dict[str, Capability]


class VehicleSnapshot(BaseModel):
    """Latest independently-recorded values; timestamps are never combined."""

    vehicle_id: int
    battery_level: int | None
    battery_level_at: datetime | None
    range_km: float | None
    range_at: datetime | None
    odometer_km: float | None
    odometer_at: datetime | None
    capability: Capability

    @field_validator("battery_level_at", "range_at", "odometer_at")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class SeriesPoint(BaseModel):
    """One raw sample or one equal-width aggregate bucket."""

    time: datetime
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    value: float | None = None
    discontinuity: bool

    @field_validator("time")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


type SeriesName = Literal[
    "speed", "power", "battery", "inside_temperature", "outside_temperature", "elevation"
]
type SeriesAggregation = Literal["raw", "mean_min_max", "last"]


class TimeSeries(BaseModel):
    name: SeriesName
    unit: str
    start: datetime | None
    end: datetime | None
    sample_count: int
    bucket_count: int
    aggregation: SeriesAggregation
    capability: Capability
    points: list[SeriesPoint]

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class TripSeries(BaseModel):
    trip_id: int
    capability: Capability
    series: list[TimeSeries]


class ChargeSeries(BaseModel):
    charge_id: int
    capability: Capability
    series: list[TimeSeries]


HistoryWindowPreset = Literal[
    "today",
    "last_7_days",
    "last_30_days",
    "this_month",
    "this_year",
    "all_history",
    "custom",
]


class ResolvedHistoryWindow(BaseModel):
    """A local-calendar selection resolved once for reuse by later requests."""

    preset: HistoryWindowPreset
    timezone: str
    start: datetime | None
    end: datetime | None
    is_empty: bool

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


@contextmanager
def connection(request: Request) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    try:
        config, password = snapshot(request)
        with request.app.state.postgresql.connection(config, password) as database:
            yield database
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, detail={"code": classify(error)}) from None


@router.get("/diagnostics", response_model=Diagnostics, tags=["operations"])
def diagnostics(request: Request) -> Diagnostics:
    from sqlalchemy.orm import Session

    with Session(storage(request).engine) as session:
        current = read_settings(session)
        return Diagnostics(postgresql=current.postgresql, mqtt=current.mqtt, smtp=current.smtp)


@router.get("/history/capabilities", response_model=HistoryCapabilities)
def history_capabilities(request: Request) -> HistoryCapabilities:
    with connection(request) as database:
        statuses = capability_status(database)
    return HistoryCapabilities(
        capabilities={
            name: Capability(available=reason is None, reason=reason)
            for name, reason in statuses.items()
        }
    )


@router.get("/vehicles", response_model=Vehicles)
def vehicles(request: Request) -> Vehicles:
    with connection(request) as database:
        rows = database.execute(
            "SELECT id, name, model FROM public.cars ORDER BY id LIMIT 100"
        ).fetchall()
    return Vehicles(items=[Vehicle(**row) for row in rows])


def local_midnight(value: date, timezone: ZoneInfo) -> datetime:
    """Construct a calendar boundary in the configured zone, never by adding 24 hours."""
    return datetime.combine(value, time.min, tzinfo=timezone)


def resolve_calendar_window(
    preset: HistoryWindowPreset,
    timezone: ZoneInfo,
    resolved_at: datetime,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[datetime | None, datetime]:
    """Resolve presets and custom dates to UTC without making a database query."""
    now = resolved_at.astimezone(UTC)
    local_today = now.astimezone(timezone).date()
    if preset == "custom":
        if (
            start_date is None
            or end_date is None
            or start_date > end_date
            or end_date > local_today
        ):
            raise ValueError
        start = local_midnight(start_date, timezone).astimezone(UTC)
        end = min(local_midnight(end_date + timedelta(days=1), timezone).astimezone(UTC), now)
        return start, end
    if start_date is not None or end_date is not None:
        raise ValueError
    if preset == "today":
        start = local_midnight(local_today, timezone)
    elif preset == "last_7_days":
        start = local_midnight(local_today - timedelta(days=6), timezone)
    elif preset == "last_30_days":
        start = local_midnight(local_today - timedelta(days=29), timezone)
    elif preset == "this_month":
        start = local_midnight(local_today.replace(day=1), timezone)
    elif preset == "this_year":
        start = local_midnight(local_today.replace(month=1, day=1), timezone)
    elif preset == "all_history":
        return None, now
    else:
        raise ValueError
    return start.astimezone(UTC), now


def vehicle_exists(database: psycopg.Connection[dict[str, Any]], vehicle_id: int) -> bool:
    row = database.execute("SELECT 1 FROM public.cars WHERE id=%s", (vehicle_id,)).fetchone()
    return row is not None


def latest_snapshot_value(
    database: psycopg.Connection[dict[str, Any]],
    vehicle_id: int,
    column: Literal[
        "battery_level", "rated_battery_range_km", "ideal_battery_range_km", "odometer"
    ],
) -> dict[str, Any] | None:
    """Read one field from its latest valid source row.

    The column is an internal literal, never a request value.  Charge samples
    deliberately win only when their timestamp equals a position timestamp;
    IDs break ties inside each source independently.
    """
    if column == "battery_level":
        valid_position = "p.battery_level BETWEEN 0 AND 100"
        valid_charge = "ch.battery_level BETWEEN 0 AND 100"
    else:
        valid_position = (
            f"p.{column} IS NOT NULL AND p.{column}::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            f"AND p.{column} >= 0"
        )
        valid_charge = (
            f"ch.{column} IS NOT NULL AND ch.{column}::text "
            "NOT IN ('NaN', 'Infinity', '-Infinity') "
            f"AND ch.{column} >= 0"
        )
    # Odometer is only recorded in positions.  The other values merge the two
    # real source streams, preserving each selected row's actual timestamp.
    if column == "odometer":
        return database.execute(
            "SELECT p.odometer AS value,p.date AS recorded_at FROM public.positions AS p "
            f"WHERE p.car_id=%s AND {valid_position} ORDER BY p.date DESC,p.id DESC LIMIT 1",
            (vehicle_id,),
        ).fetchone()
    return database.execute(
        "WITH samples AS ("
        f"SELECT p.{column} AS value,p.date AS recorded_at,p.id AS source_id,0 AS source_priority "
        "FROM public.positions AS p "
        f"WHERE p.car_id=%s AND {valid_position} "
        "UNION ALL "
        f"SELECT ch.{column} AS value,ch.date AS recorded_at,ch.id AS source_id,"
        "1 AS source_priority "
        "FROM public.charges AS ch JOIN public.charging_processes AS cp "
        "ON cp.id=ch.charging_process_id "
        f"WHERE cp.car_id=%s AND {valid_charge}"
        ") SELECT value,recorded_at FROM samples "
        "ORDER BY recorded_at DESC,source_priority DESC,source_id DESC LIMIT 1",
        (vehicle_id, vehicle_id),
    ).fetchone()


@router.get("/vehicles/{vehicle_id}/snapshot", response_model=VehicleSnapshot)
def vehicle_snapshot(
    request: Request, vehicle_id: Annotated[int, Path(ge=1)]
) -> VehicleSnapshot:
    """Return latest recorded values, independent of any history window."""
    with Session(storage(request).engine) as session:
        range_basis = read_settings(session).preferences.range_basis
    with connection(request) as database:
        if not vehicle_exists(database, vehicle_id):
            raise HTTPException(404, "Vehicle not found")
        reason = capability_status(database)["latest_values"]
        capability = Capability(available=reason is None, reason=reason)
        if reason is not None:
            return VehicleSnapshot(
                vehicle_id=vehicle_id,
                battery_level=None,
                battery_level_at=None,
                range_km=None,
                range_at=None,
                odometer_km=None,
                odometer_at=None,
                capability=capability,
            )
        battery = latest_snapshot_value(database, vehicle_id, "battery_level")
        range_column: Literal["rated_battery_range_km", "ideal_battery_range_km"] = (
            "rated_battery_range_km"
            if range_basis == "rated"
            else "ideal_battery_range_km"
        )
        range_value = latest_snapshot_value(database, vehicle_id, range_column)
        odometer = latest_snapshot_value(database, vehicle_id, "odometer")
    return VehicleSnapshot(
        vehicle_id=vehicle_id,
        battery_level=int(battery["value"]) if battery else None,
        battery_level_at=battery["recorded_at"] if battery else None,
        range_km=float(range_value["value"]) if range_value else None,
        range_at=range_value["recorded_at"] if range_value else None,
        odometer_km=float(odometer["value"]) if odometer else None,
        odometer_at=odometer["recorded_at"] if odometer else None,
        capability=capability,
    )


@router.get("/vehicles/{vehicle_id}/history-window", response_model=ResolvedHistoryWindow)
def history_window(
    request: Request,
    vehicle_id: Annotated[int, Path(ge=1)],
    preset: HistoryWindowPreset = "last_30_days",
    start_date: date | None = None,
    end_date: date | None = None,
) -> ResolvedHistoryWindow:
    """Resolve a saved-timezone calendar selection for one existing vehicle.

    The response is deliberately a small, stable hand-off: callers retain these
    UTC bounds for list pagination instead of resolving a new moving "now".
    """
    with Session(storage(request).engine) as session:
        timezone_name = read_settings(session).preferences.timezone
    try:
        timezone = ZoneInfo(timezone_name)
        start, end = resolve_calendar_window(
            preset, timezone, datetime.now(UTC), start_date, end_date
        )
    except (ValueError, OverflowError):
        raise HTTPException(422, "Invalid history window") from None

    with connection(request) as database:
        if not vehicle_exists(database, vehicle_id):
            raise HTTPException(404, "Vehicle not found")
        if start is None:
            row = database.execute(
                "SELECT min(start_date) AS start FROM ("
                "SELECT start_date FROM public.drives WHERE car_id=%s "
                "UNION ALL "
                "SELECT start_date FROM public.charging_processes WHERE car_id=%s"
                ") AS records",
                (vehicle_id, vehicle_id),
            ).fetchone()
            if row is None or row["start"] is None:
                return ResolvedHistoryWindow(
                    preset=preset, timezone=timezone_name, start=None, end=None, is_empty=True
                )
            earliest = row["start"]
            start = (
                earliest.replace(tzinfo=UTC)
                if earliest.tzinfo is None
                else earliest.astimezone(UTC)
            )

        # At the exact beginning of a local day, a capped today/custom range can
        # have no duration. Do not issue a misleading zero-width source query.
        if start >= end:
            return ResolvedHistoryWindow(
                preset=preset, timezone=timezone_name, start=start, end=end, is_empty=True
            )
        row = database.execute(
            "SELECT EXISTS("
            "SELECT 1 FROM public.drives WHERE car_id=%s AND start_date >= %s AND start_date < %s "
            "UNION ALL "
            "SELECT 1 FROM public.charging_processes "
            "WHERE car_id=%s AND start_date >= %s AND start_date < %s"
            ") AS exists",
            (vehicle_id, start, end, vehicle_id, start, end),
        ).fetchone()
    return ResolvedHistoryWindow(
        preset=preset,
        timezone=timezone_name,
        start=start,
        end=end,
        is_empty=not bool(row and row["exists"]),
    )


class Window:
    def __init__(
        self,
        vehicle_id: Annotated[int | None, Query(ge=1)] = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> None:
        self.vehicle_id, self.limit = vehicle_id, limit
        self.after: tuple[datetime, int] | None = None
        self.kind: str | None = None
        try:
            if cursor:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                saved_start = datetime.fromisoformat(decoded["start"])
                saved_end = datetime.fromisoformat(decoded["end"])
                if (start and start != saved_start) or (end and end != saved_end):
                    raise ValueError
                if vehicle_id != decoded["vehicle_id"]:
                    raise ValueError
                start, end = saved_start, saved_end
                self.after = (datetime.fromisoformat(decoded["after"]), int(decoded["id"]))
                self.kind = decoded["kind"]
            self.end = end or datetime.now(UTC)
            self.start = start or self.end - timedelta(days=30)
            if any(value.tzinfo is None for value in (self.start, self.end)):
                raise ValueError
            self.start, self.end = self.start.astimezone(UTC), self.end.astimezone(UTC)
            if not timedelta(0) < self.end - self.start:
                raise ValueError
            if self.after and (
                self.after[0].tzinfo is None
                or not self.start <= self.after[0] < self.end
                or self.after[1] < 1
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeError, OverflowError):
            raise HTTPException(422, "Invalid time window or cursor") from None

    def encode(self, row: Summary, kind: str) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(
                {
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "vehicle_id": self.vehicle_id,
                    "after": row.start.isoformat(),
                    "id": row.id,
                    "kind": kind,
                }
            ).encode()
        ).decode()


def period_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Validate an explicit UTC interval for a non-paginated period aggregate."""
    try:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if start >= end:
            raise ValueError
        return start, end
    except (ValueError, OverflowError):
        raise HTTPException(422, "Invalid time window") from None


def metric_coverage(
    *,
    total_count: int,
    ended_count: int,
    valid_count: int,
    denominator: float | None = None,
    unavailable: bool = False,
) -> MetricCoverage:
    reason: Literal[
        "no_ended_records", "no_valid_values", "zero_denominator", "unavailable"
    ] | None
    if unavailable:
        reason = "unavailable"
    elif total_count == 0:
        reason = None
    elif ended_count == 0:
        reason = "no_ended_records"
    elif valid_count == 0:
        reason = "no_valid_values"
    elif denominator is not None and denominator == 0:
        reason = "zero_denominator"
    else:
        reason = None
    return MetricCoverage(
        applicable_count=ended_count,
        valid_count=valid_count,
        reason=reason,
    )


def aggregate_value(value: Any, coverage: MetricCoverage, total_count: int) -> float | None:
    """Empty intervals have zero totals; incomplete nonempty intervals stay unknown."""
    if total_count == 0:
        return 0.0
    if coverage.reason is not None:
        return None
    return float(value) if value is not None else None


@router.get("/vehicles/{vehicle_id}/trip-summary", response_model=TripPeriodSummary)
def trip_summary(
    request: Request,
    vehicle_id: Annotated[int, Path(ge=1)],
    start: datetime,
    end: datetime,
) -> TripPeriodSummary:
    """Aggregate a vehicle's complete explicit interval in one bounded SQL query."""
    start, end = period_window(start, end)
    with Session(storage(request).engine) as session:
        range_basis = read_settings(session).preferences.range_basis
    with connection(request) as database:
        if not vehicle_exists(database, vehicle_id):
            raise HTTPException(404, "Vehicle not found")
        capabilities = capability_status(database)
        estimate_available = capabilities["trip_summary"] is None
        if estimate_available:
            eligibility = trip_energy_conditions(range_basis)
            start_range = f"d.start_{range_basis}_range_km"
            end_range = f"d.end_{range_basis}_range_km"
            energy = (
                f"CASE WHEN {eligibility} THEN ({start_range} - {end_range}) "
                "* trip_car.efficiency END"
            )
            valid_energy = f"CASE WHEN {eligibility} THEN 1 ELSE 0 END"
            energy_distance = f"CASE WHEN {eligibility} THEN d.distance END"
            car_join = "JOIN public.cars AS trip_car ON trip_car.id=d.car_id"
        else:
            energy = "NULL::numeric"
            valid_energy = "0"
            energy_distance = "NULL::numeric"
            car_join = ""
        finite_distance = "d.distance::text NOT IN ('NaN', 'Infinity', '-Infinity')"
        finite_duration = "d.duration_min::text NOT IN ('NaN', 'Infinity', '-Infinity')"
        valid_distance = (
            "d.end_date IS NOT NULL AND d.distance IS NOT NULL "
            f"AND {finite_distance} AND d.distance >= 0"
        )
        valid_duration = (
            "d.end_date IS NOT NULL AND d.duration_min IS NOT NULL "
            f"AND {finite_duration} AND d.duration_min >= 0"
        )
        row = database.execute(
            f"SELECT count(*) AS total_count, "
            "count(*) FILTER (WHERE d.end_date IS NOT NULL) AS ended_count, "
            "count(*) FILTER (WHERE d.end_date IS NULL) AS not_ended_count, "
            f"count(*) FILTER (WHERE {valid_distance}) AS distance_valid_count, "
            f"sum(d.distance) FILTER (WHERE {valid_distance}) AS distance_km, "
            f"count(*) FILTER (WHERE {valid_duration}) AS duration_valid_count, "
            f"sum(d.duration_min) FILTER (WHERE {valid_duration}) AS duration_min, "
            f"coalesce(sum({valid_energy}), 0) AS energy_valid_count, "
            f"sum({energy}) AS estimated_energy_kwh, "
            f"sum({energy_distance}) AS energy_distance_km "
            f"FROM public.drives AS d {car_join} "
            "WHERE d.car_id=%s AND d.start_date >= %s AND d.start_date < %s",
            (vehicle_id, start, end),
        ).fetchone()
    assert row is not None
    total_count = int(row["total_count"])
    ended_count = int(row["ended_count"])
    distance_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["distance_valid_count"]),
    )
    duration_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["duration_valid_count"]),
    )
    energy_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["energy_valid_count"]),
        unavailable=not estimate_available,
    )
    consumption_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["energy_valid_count"]),
        denominator=(
            float(row["energy_distance_km"])
            if row["energy_distance_km"] is not None
            else None
        ),
        unavailable=not estimate_available,
    )
    consumption = (
        None
        if row["estimated_energy_kwh"] is None or row["energy_distance_km"] is None
        else float(row["estimated_energy_kwh"]) / float(row["energy_distance_km"]) * 1000
    )
    return TripPeriodSummary(
        vehicle_id=vehicle_id,
        start=start,
        end=end,
        total_count=total_count,
        ended_count=ended_count,
        not_ended_count=int(row["not_ended_count"]),
        distance_km=aggregate_value(row["distance_km"], distance_coverage, total_count),
        duration_min=aggregate_value(row["duration_min"], duration_coverage, total_count),
        estimated_energy_kwh=aggregate_value(
            row["estimated_energy_kwh"], energy_coverage, total_count
        ),
        estimated_average_consumption_wh_per_km=aggregate_value(
            consumption, consumption_coverage, total_count
        ),
        distance_coverage=distance_coverage,
        duration_coverage=duration_coverage,
        estimated_energy_coverage=energy_coverage,
        estimated_average_consumption_coverage=consumption_coverage,
        estimate_capability=Capability(
            available=estimate_available, reason=capabilities["trip_summary"]
        ),
    )


@router.get("/vehicles/{vehicle_id}/charge-summary", response_model=ChargePeriodSummary)
def charge_summary(
    request: Request,
    vehicle_id: Annotated[int, Path(ge=1)],
    start: datetime,
    end: datetime,
) -> ChargePeriodSummary:
    """Aggregate one vehicle's complete UTC interval without loading charge rows."""
    start, end = period_window(start, end)
    with Session(storage(request).engine) as session:
        currency = read_settings(session).preferences.display_currency
    with connection(request) as database:
        if not vehicle_exists(database, vehicle_id):
            raise HTTPException(404, "Vehicle not found")
        capabilities = capability_status(database)
        cost_available = capabilities["charge_summary"] is None
        finite_energy = "c.charge_energy_added::text NOT IN ('NaN', 'Infinity', '-Infinity')"
        finite_duration = "c.duration_min::text NOT IN ('NaN', 'Infinity', '-Infinity')"
        valid_energy = (
            "c.end_date IS NOT NULL AND c.charge_energy_added IS NOT NULL "
            f"AND {finite_energy} AND c.charge_energy_added >= 0"
        )
        valid_duration = (
            "c.end_date IS NOT NULL AND c.duration_min IS NOT NULL "
            f"AND {finite_duration} AND c.duration_min >= 0"
        )
        if cost_available:
            finite_cost = "c.cost::text NOT IN ('NaN', 'Infinity', '-Infinity')"
            valid_cost = (
                "c.end_date IS NOT NULL AND c.cost IS NOT NULL " f"AND {finite_cost}"
            )
            cost_fields = (
                f"count(*) FILTER (WHERE {valid_cost}) AS cost_valid_count, "
                f"sum(c.cost) FILTER (WHERE {valid_cost}) AS cost"
            )
        else:
            cost_fields = "0 AS cost_valid_count, NULL::numeric AS cost"
        row = database.execute(
            f"SELECT count(*) AS total_count, "
            "count(*) FILTER (WHERE c.end_date IS NOT NULL) AS ended_count, "
            "count(*) FILTER (WHERE c.end_date IS NULL) AS not_ended_count, "
            f"count(*) FILTER (WHERE {valid_energy}) AS energy_valid_count, "
            f"sum(c.charge_energy_added) FILTER (WHERE {valid_energy}) AS energy_added_kwh, "
            f"count(*) FILTER (WHERE {valid_duration}) AS duration_valid_count, "
            f"sum(c.duration_min) FILTER (WHERE {valid_duration}) AS duration_min, "
            f"{cost_fields} FROM public.charging_processes AS c "
            "WHERE c.car_id=%s AND c.start_date >= %s AND c.start_date < %s",
            (vehicle_id, start, end),
        ).fetchone()
    assert row is not None
    total_count = int(row["total_count"])
    ended_count = int(row["ended_count"])
    energy_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["energy_valid_count"]),
    )
    duration_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["duration_valid_count"]),
    )
    cost_coverage = metric_coverage(
        total_count=total_count,
        ended_count=ended_count,
        valid_count=int(row["cost_valid_count"]),
        unavailable=not cost_available,
    )
    return ChargePeriodSummary(
        vehicle_id=vehicle_id,
        start=start,
        end=end,
        total_count=total_count,
        ended_count=ended_count,
        not_ended_count=int(row["not_ended_count"]),
        energy_added_kwh=aggregate_value(row["energy_added_kwh"], energy_coverage, total_count),
        duration_min=aggregate_value(row["duration_min"], duration_coverage, total_count),
        # A configured currency is the owner's confirmation that the stored
        # costs share that currency. MateScope never guesses or converts it.
        cost=(
            aggregate_value(row["cost"], cost_coverage, total_count)
            if currency is not None
            else None
        ),
        currency=currency,
        energy_added_coverage=energy_coverage,
        duration_coverage=duration_coverage,
        cost_coverage=cost_coverage,
        cost_capability=Capability(
            available=cost_available, reason=capabilities["charge_summary"]
        ),
    )


TRIP_FIELDS = (
    "d.id, d.car_id AS vehicle_id, d.start_date AS start, d.end_date AS end, "
    "d.duration_min, d.distance AS distance_km, d.speed_max AS speed_max_kmh"
)
CHARGE_FIELDS = (
    "c.id, c.car_id AS vehicle_id, c.start_date AS start, c.end_date AS end, "
    "c.duration_min, c.charge_energy_added AS energy_added_kwh"
)


def trip_projection(
    capabilities: dict[str, CapabilityReason | None],
    range_basis: Literal["rated", "ideal"],
) -> tuple[str, str]:
    """Build a projection only after optional column grants have been checked."""
    fields = [TRIP_FIELDS]
    joins: list[str] = []
    has_details = capabilities["trip_details"] is None
    has_locations = capabilities["locations"] is None

    if has_details:
        # drive_id is part of the M0 grant. It makes an accidentally linked
        # position from another drive unavailable instead of exposing its SOC.
        joins.extend(
            [
                "LEFT JOIN public.cars AS trip_car ON trip_car.id=d.car_id",
                "LEFT JOIN public.positions AS start_position "
                "ON start_position.id=d.start_position_id AND start_position.drive_id=d.id",
                "LEFT JOIN public.positions AS end_position "
                "ON end_position.id=d.end_position_id AND end_position.drive_id=d.id",
            ]
        )
        fields.extend(
            [
                "start_position.battery_level AS start_battery_level",
                "end_position.battery_level AS end_battery_level",
                trip_energy_projection(range_basis),
                trip_consumption_projection(range_basis),
            ]
        )
    else:
        fields.extend(
            [
                "NULL::smallint AS start_battery_level",
                "NULL::smallint AS end_battery_level",
                "NULL::numeric AS estimated_energy_kwh",
                "NULL::numeric AS estimated_average_consumption_wh_per_km",
            ]
        )

    if has_details and has_locations:
        joins.extend(
            [
                "LEFT JOIN public.geofences AS start_geofence "
                "ON start_geofence.id=d.start_geofence_id",
                "LEFT JOIN public.addresses AS start_address "
                "ON start_address.id=d.start_address_id",
                "LEFT JOIN public.geofences AS end_geofence "
                "ON end_geofence.id=d.end_geofence_id",
                "LEFT JOIN public.addresses AS end_address "
                "ON end_address.id=d.end_address_id",
            ]
        )
        fields.extend(
            [
                "COALESCE(NULLIF(btrim(start_geofence.name), ''), "
                "NULLIF(btrim(start_address.name), ''), "
                "CASE WHEN NULLIF(concat_ws(' ', NULLIF(btrim(start_address.road), ''), "
                "NULLIF(btrim(start_address.house_number), '')), '') IS NOT NULL "
                "AND NULLIF(btrim(start_address.city), '') IS NOT NULL THEN concat_ws(', ', "
                "concat_ws(' ', NULLIF(btrim(start_address.road), ''), "
                "NULLIF(btrim(start_address.house_number), '')), btrim(start_address.city)) END) "
                "AS start_place",
                "COALESCE(NULLIF(btrim(end_geofence.name), ''), "
                "NULLIF(btrim(end_address.name), ''), "
                "CASE WHEN NULLIF(concat_ws(' ', NULLIF(btrim(end_address.road), ''), "
                "NULLIF(btrim(end_address.house_number), '')), '') IS NOT NULL "
                "AND NULLIF(btrim(end_address.city), '') IS NOT NULL THEN concat_ws(', ', "
                "concat_ws(' ', NULLIF(btrim(end_address.road), ''), "
                "NULLIF(btrim(end_address.house_number), '')), btrim(end_address.city)) END) "
                "AS end_place",
            ]
        )
    else:
        fields.extend(["NULL::text AS start_place", "NULL::text AS end_place"])
    return ", ".join(fields), " ".join(joins)


def trip_energy_conditions(range_basis: Literal["rated", "ideal"]) -> str:
    """Return the shared eligibility rule for each estimated trip metric.

    PostgreSQL numeric values can contain NaN or infinities, so textual checks
    are necessary in addition to null and positivity checks.  This prevents an
    invalid source value from becoming a JSON non-finite number.
    """
    start_range = f"d.start_{range_basis}_range_km"
    end_range = f"d.end_{range_basis}_range_km"
    finite = "::text NOT IN ('NaN', 'Infinity', '-Infinity')"
    return (
        "d.end_date IS NOT NULL "
        f"AND d.distance IS NOT NULL AND d.distance{finite} AND d.distance > 0 "
        "AND trip_car.efficiency IS NOT NULL AND trip_car.efficiency > 0 "
        f"AND trip_car.efficiency{finite} "
        f"AND {start_range} IS NOT NULL AND {start_range}{finite} "
        f"AND {end_range} IS NOT NULL AND {end_range}{finite} "
        f"AND {start_range} - {end_range} >= 0"
    )


def trip_energy_projection(range_basis: Literal["rated", "ideal"]) -> str:
    start_range = f"d.start_{range_basis}_range_km"
    end_range = f"d.end_{range_basis}_range_km"
    return (
        "CASE WHEN "
        f"{trip_energy_conditions(range_basis)} "
        f"THEN ({start_range} - {end_range}) * trip_car.efficiency "
        "ELSE NULL::numeric END AS estimated_energy_kwh"
    )


def trip_consumption_projection(range_basis: Literal["rated", "ideal"]) -> str:
    start_range = f"d.start_{range_basis}_range_km"
    end_range = f"d.end_{range_basis}_range_km"
    return (
        "CASE WHEN "
        f"{trip_energy_conditions(range_basis)} "
        f"THEN (({start_range} - {end_range}) * trip_car.efficiency / d.distance) * 1000 "
        "ELSE NULL::numeric END AS estimated_average_consumption_wh_per_km"
    )


def charge_projection(
    capabilities: dict[str, CapabilityReason | None],
) -> tuple[str, str]:
    """Build charge additions only after their column grants are diagnosed."""
    fields = [CHARGE_FIELDS]
    joins: list[str] = []
    has_details = capabilities["charge_details"] is None
    has_locations = capabilities["locations"] is None

    if has_details:
        fields.extend(
            [
                "c.start_battery_level",
                "c.end_battery_level",
                "c.charge_energy_used AS recorded_energy_used_kwh",
                "c.cost",
            ]
        )
    else:
        fields.extend(
            [
                "NULL::smallint AS start_battery_level",
                "NULL::smallint AS end_battery_level",
                "NULL::numeric AS recorded_energy_used_kwh",
                "NULL::numeric AS cost",
            ]
        )

    if has_details and has_locations:
        joins.extend(
            [
                "LEFT JOIN public.geofences AS charge_geofence "
                "ON charge_geofence.id=c.geofence_id",
                "LEFT JOIN public.addresses AS charge_address "
                "ON charge_address.id=c.address_id",
            ]
        )
        fields.append(
            "COALESCE(NULLIF(btrim(charge_geofence.name), ''), "
            "NULLIF(btrim(charge_address.name), ''), "
            "CASE WHEN NULLIF(concat_ws(' ', NULLIF(btrim(charge_address.road), ''), "
            "NULLIF(btrim(charge_address.house_number), '')), '') IS NOT NULL "
            "AND NULLIF(btrim(charge_address.city), '') IS NOT NULL THEN concat_ws(', ', "
            "concat_ws(' ', NULLIF(btrim(charge_address.road), ''), "
            "NULLIF(btrim(charge_address.house_number), '')), btrim(charge_address.city)) END) "
            "AS place"
        )
    else:
        fields.append("NULL::text AS place")
    return ", ".join(fields), " ".join(joins)


def page(
    request: Request,
    window: Window,
    kind: Literal["trips", "charges"],
) -> TripPage | ChargePage:
    if window.kind is not None and window.kind != kind:
        raise HTTPException(422, "Invalid time window or cursor")
    table, fields = (
        ("drives", TRIP_FIELDS)
        if kind == "trips"
        else (
            "charging_processes",
            CHARGE_FIELDS,
        )
    )
    prefix = "d." if kind == "trips" else "c."
    conditions = [sql.SQL(f"{prefix}start_date >= %s AND {prefix}start_date < %s")]
    params: list[object] = [window.start, window.end]
    if window.vehicle_id is not None:
        conditions.append(sql.SQL(f"{prefix}car_id = %s"))
        params.append(window.vehicle_id)
    if window.after:
        conditions.append(sql.SQL(f"({prefix}start_date, {prefix}id) < (%s, %s)"))
        params.extend(window.after)
    params.append(window.limit + 1)
    with connection(request) as database:
        joins = ""
        if kind == "trips":
            with Session(storage(request).engine) as session:
                range_basis = read_settings(session).preferences.range_basis
            fields, joins = trip_projection(capability_status(database), range_basis)
        else:
            fields, joins = charge_projection(capability_status(database))
        rows = database.execute(
            sql.SQL(
                "SELECT {} FROM public.{}{} WHERE {} ORDER BY {}start_date DESC, {}id DESC LIMIT %s"
            ).format(
                sql.SQL(fields),
                sql.Identifier(table),
                sql.SQL((" AS d " if kind == "trips" else " AS c ") + joins),
                sql.SQL(" AND ").join(conditions),
                sql.SQL(prefix),
                sql.SQL(prefix),
            ),
            params,
        ).fetchall()
    entries = [Trip(**row) if kind == "trips" else Charge(**row) for row in rows[: window.limit]]
    cursor = window.encode(entries[-1], kind) if len(rows) > window.limit else None
    values = {"items": entries, "next_cursor": cursor, "start": window.start, "end": window.end}
    return TripPage.model_validate(values) if kind == "trips" else ChargePage.model_validate(values)


@router.get("/trips", response_model=TripPage)
def trips(request: Request, window: Annotated[Window, Depends()]) -> TripPage | ChargePage:
    return page(request, window, "trips")


@router.get("/charges", response_model=ChargePage)
def charges(request: Request, window: Annotated[Window, Depends()]) -> TripPage | ChargePage:
    return page(request, window, "charges")


def detail(request: Request, identifier: int, kind: Literal["trips", "charges"]) -> Trip | Charge:
    table, fields = (
        ("drives", TRIP_FIELDS)
        if kind == "trips"
        else (
            "charging_processes",
            CHARGE_FIELDS,
        )
    )
    with connection(request) as database:
        joins = ""
        if kind == "trips":
            with Session(storage(request).engine) as session:
                range_basis = read_settings(session).preferences.range_basis
            fields, joins = trip_projection(capability_status(database), range_basis)
        else:
            fields, joins = charge_projection(capability_status(database))
        row = database.execute(
            sql.SQL("SELECT {} FROM public.{}{} WHERE {}id=%s").format(
                sql.SQL(fields),
                sql.Identifier(table),
                sql.SQL((" AS d " if kind == "trips" else " AS c ") + joins),
                sql.SQL("d." if kind == "trips" else "c."),
            ),
            (identifier,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Record not found")
    return Trip(**row) if kind == "trips" else Charge(**row)


@router.get("/trips/{trip_id}", response_model=Trip)
def trip(request: Request, trip_id: int) -> Trip | Charge:
    return detail(request, trip_id, "trips")


@router.get("/charges/{charge_id}", response_model=Charge)
def charge(request: Request, charge_id: int) -> Trip | Charge:
    return detail(request, charge_id, "charges")


TRIP_SERIES: tuple[tuple[SeriesName, str, str, SeriesAggregation, str], ...] = (
    ("speed", "speed", "km/h", "mean_min_max", "trip_series_speed"),
    ("power", "power", "kW", "mean_min_max", "trip_series_power"),
    ("battery", "battery_level", "%", "last", "trip_series_battery"),
    (
        "inside_temperature", "inside_temp", "°C", "mean_min_max", "trip_series_inside_temperature"
    ),
    (
        "outside_temperature", "outside_temp", "°C", "mean_min_max",
        "trip_series_outside_temperature",
    ),
    ("elevation", "elevation", "m", "mean_min_max", "trip_series_elevation"),
)
SERIES_BUCKET_LIMIT = 600


def trip_series_metadata(
    database: psycopg.Connection[dict[str, Any]], trip_id: int
) -> dict[str, Any]:
    row = database.execute(
        "SELECT count(*) AS sample_count, min(date) AS start, max(date) AS end "
        "FROM public.positions WHERE drive_id=%s",
        (trip_id,),
    ).fetchone()
    return row or {"sample_count": 0, "start": None, "end": None}


def trip_series_points(
    database: psycopg.Connection[dict[str, Any]],
    trip_id: int,
    column: str,
    sample_count: int,
) -> list[dict[str, Any]]:
    """Return bounded raw rows or SQL aggregates, retaining missing-data breaks.

    ``column`` only comes from TRIP_SERIES.  It is deliberately interpolated
    into the fixed projection rather than accepting a client-provided name.
    """
    value = (
        f"CASE WHEN p.{column}::text IN ('NaN', 'Infinity', '-Infinity') "
        f"THEN NULL ELSE p.{column} END"
    )
    if sample_count <= SERIES_BUCKET_LIMIT:
        return database.execute(
            "WITH samples AS ("
            f"SELECT p.id,p.date,{value} AS value FROM public.positions AS p WHERE p.drive_id=%s"
            "), marked AS (SELECT *,lag(date) OVER (ORDER BY date,id) AS previous_time "
            "FROM samples) "
            "SELECT date AS time,value AS mean,value AS min,value AS max,value,"
            "coalesce(value IS NULL OR date-previous_time > interval '5 minutes',false) "
            "AS discontinuity "
            "FROM marked ORDER BY date,id",
            (trip_id,),
        ).fetchall()
    # Build exactly 600 equal-width time buckets.  A zero-duration source (all
    # samples share a timestamp) is a valid stable sample set and belongs in
    # bucket zero rather than causing a divide-by-zero error.
    return database.execute(
        "WITH bounds AS (SELECT min(date) AS start,max(date) AS ending FROM public.positions "
        "WHERE drive_id=%s), samples AS ("
        f"SELECT p.id,p.date,{value} AS value,"
        "lag(p.date) OVER (ORDER BY p.date,p.id) AS previous_time,"
        "CASE WHEN bounds.ending=bounds.start THEN 0 ELSE least(599,floor("
        "extract(epoch FROM p.date-bounds.start)/"
        "(extract(epoch FROM bounds.ending-bounds.start)/600))::integer) END AS bucket "
        "FROM public.positions AS p CROSS JOIN bounds WHERE p.drive_id=%s), grouped AS ("
        "SELECT bucket,min(date) AS time,max(date) AS last_time,avg(value) AS mean,"
        "min(value) AS min,max(value) AS max,"
        "(array_agg(value ORDER BY date DESC,id DESC) FILTER "
        "(WHERE value IS NOT NULL))[1] AS value,"
        "bool_or(value IS NULL OR date-previous_time > interval '5 minutes') "
        "AS internal_discontinuity FROM samples GROUP BY bucket), marked AS "
        "(SELECT *,lag(bucket) OVER (ORDER BY bucket) AS previous_bucket,"
        "lag(last_time) OVER (ORDER BY bucket) AS previous_last_time FROM grouped) "
        "SELECT time,mean,min,max,value,coalesce(internal_discontinuity OR "
        "previous_bucket IS NOT NULL AND (bucket-previous_bucket > 1 OR "
        "time-previous_last_time > interval '5 minutes'),false) "
        "AS discontinuity "
        "FROM marked ORDER BY time,bucket",
        (trip_id, trip_id),
    ).fetchall()


@router.get("/trips/{trip_id}/series", response_model=TripSeries)
def trip_series(request: Request, trip_id: int) -> TripSeries:
    with connection(request) as database:
        exists = database.execute(
            "SELECT 1 FROM public.drives WHERE id=%s", (trip_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "Record not found")
        statuses = capability_status(database)
        reason = statuses["trip_series"]
        capability = Capability(available=reason is None, reason=reason)
        metadata = trip_series_metadata(database, trip_id)
        samples = int(metadata["sample_count"])
        values: list[TimeSeries] = []
        for name, column, unit, aggregation, capability_name in TRIP_SERIES:
            series_reason = statuses[capability_name]
            series_capability = Capability(
                available=series_reason is None, reason=series_reason
            )
            points = (
                trip_series_points(database, trip_id, column, samples)
                if series_reason is None
                else []
            )
            values.append(
                TimeSeries(
                    name=name,
                    unit=unit,
                    start=metadata["start"],
                    end=metadata["end"],
                    sample_count=samples,
                    bucket_count=len(points),
                    aggregation="raw" if samples <= SERIES_BUCKET_LIMIT else aggregation,
                    capability=series_capability,
                    points=[SeriesPoint(**point) for point in points],
                )
            )
    return TripSeries(trip_id=trip_id, capability=capability, series=values)


CHARGE_SERIES: tuple[tuple[SeriesName, str, str, SeriesAggregation, str], ...] = (
    ("power", "charger_power", "kW", "mean_min_max", "charge_series_power"),
    ("battery", "battery_level", "%", "last", "charge_series_battery"),
    (
        "outside_temperature", "outside_temp", "°C", "mean_min_max",
        "charge_series_outside_temperature",
    ),
)


def charge_series_metadata(
    database: psycopg.Connection[dict[str, Any]], charge_id: int
) -> dict[str, Any]:
    row = database.execute(
        "SELECT count(*) AS sample_count, min(date) AS start, max(date) AS end "
        "FROM public.charges WHERE charging_process_id=%s",
        (charge_id,),
    ).fetchone()
    return row or {"sample_count": 0, "start": None, "end": None}


def charge_series_points(
    database: psycopg.Connection[dict[str, Any]],
    charge_id: int,
    column: str,
    sample_count: int,
) -> list[dict[str, Any]]:
    """Return bounded charge samples without bridging null or time gaps.

    ``column`` comes only from ``CHARGE_SERIES`` and therefore remains an
    allowlisted SQL projection rather than user-provided SQL.
    """
    value = (
        f"CASE WHEN ch.{column}::text IN ('NaN', 'Infinity', '-Infinity') "
        f"THEN NULL ELSE ch.{column} END"
    )
    if sample_count <= SERIES_BUCKET_LIMIT:
        return database.execute(
            "WITH samples AS ("
            f"SELECT ch.id,ch.date,{value} AS value FROM public.charges AS ch "
            "WHERE ch.charging_process_id=%s"
            "), marked AS (SELECT *,lag(date) OVER (ORDER BY date,id) AS previous_time "
            "FROM samples) "
            "SELECT date AS time,value AS mean,value AS min,value AS max,value,"
            "coalesce(value IS NULL OR date-previous_time > interval '5 minutes',false) "
            "AS discontinuity FROM marked ORDER BY date,id",
            (charge_id,),
        ).fetchall()
    return database.execute(
        "WITH bounds AS (SELECT min(date) AS start,max(date) AS ending FROM public.charges "
        "WHERE charging_process_id=%s), samples AS ("
        f"SELECT ch.id,ch.date,{value} AS value,"
        "lag(ch.date) OVER (ORDER BY ch.date,ch.id) AS previous_time,"
        "CASE WHEN bounds.ending=bounds.start THEN 0 ELSE least(599,floor("
        "extract(epoch FROM ch.date-bounds.start)/"
        "(extract(epoch FROM bounds.ending-bounds.start)/600))::integer) END AS bucket "
        "FROM public.charges AS ch CROSS JOIN bounds WHERE ch.charging_process_id=%s), "
        "grouped AS (SELECT bucket,min(date) AS time,max(date) AS last_time,avg(value) AS mean,"
        "min(value) AS min,max(value) AS max,"
        "(array_agg(value ORDER BY date DESC,id DESC) FILTER "
        "(WHERE value IS NOT NULL))[1] AS value,"
        "bool_or(value IS NULL OR date-previous_time > interval '5 minutes') "
        "AS internal_discontinuity FROM samples GROUP BY bucket), marked AS "
        "(SELECT *,lag(bucket) OVER (ORDER BY bucket) AS previous_bucket,"
        "lag(last_time) OVER (ORDER BY bucket) AS previous_last_time FROM grouped) "
        "SELECT time,mean,min,max,value,coalesce(internal_discontinuity OR "
        "previous_bucket IS NOT NULL AND (bucket-previous_bucket > 1 OR "
        "time-previous_last_time > interval '5 minutes'),false) AS discontinuity "
        "FROM marked ORDER BY time,bucket",
        (charge_id, charge_id),
    ).fetchall()


@router.get("/charges/{charge_id}/series", response_model=ChargeSeries)
def charge_series(request: Request, charge_id: int) -> ChargeSeries:
    with connection(request) as database:
        exists = database.execute(
            "SELECT 1 FROM public.charging_processes WHERE id=%s", (charge_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "Record not found")
        statuses = capability_status(database)
        reason = statuses["charge_series"]
        capability = Capability(available=reason is None, reason=reason)
        metadata_reason = statuses["charge_series_metadata"]
        metadata = (
            charge_series_metadata(database, charge_id)
            if metadata_reason is None
            else {"sample_count": 0, "start": None, "end": None}
        )
        samples = int(metadata["sample_count"])
        values: list[TimeSeries] = []
        for name, column, unit, aggregation, capability_name in CHARGE_SERIES:
            series_reason = metadata_reason or statuses[capability_name]
            series_capability = Capability(
                available=series_reason is None, reason=series_reason
            )
            points = (
                charge_series_points(database, charge_id, column, samples)
                if series_reason is None
                else []
            )
            values.append(
                TimeSeries(
                    name=name,
                    unit=unit,
                    start=metadata["start"],
                    end=metadata["end"],
                    sample_count=samples,
                    bucket_count=len(points),
                    aggregation="raw" if samples <= SERIES_BUCKET_LIMIT else aggregation,
                    capability=series_capability,
                    points=[SeriesPoint(**point) for point in points],
                )
            )
    return ChargeSeries(charge_id=charge_id, capability=capability, series=values)


@router.get("/trips/{trip_id}/trajectory", response_model=Trajectory)
def trajectory(request: Request, trip_id: int) -> Trajectory:
    with connection(request) as database:
        if (
            database.execute("SELECT id FROM public.drives WHERE id=%s", (trip_id,)).fetchone()
            is None
        ):
            raise HTTPException(404, "Record not found")
        rows = database.execute(
            "WITH marked AS (SELECT id,date,latitude,longitude, "
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 AS valid, "
            "sum(CASE WHEN latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 "
            "THEN 0 ELSE 1 END) OVER (ORDER BY date,id) AS segment_id "
            "FROM public.positions WHERE drive_id=%s), "
            "numbered AS (SELECT id,date,latitude,longitude,segment_id, "
            "row_number() OVER (ORDER BY date,id) AS rn,count(*) OVER () AS total "
            "FROM marked WHERE valid) "
            "SELECT id,date AS time,latitude,longitude,segment_id,total FROM numbered "
            "WHERE rn=total OR mod(rn-1,greatest(1,ceil((total-1)::numeric/1999))::bigint)=0 "
            "ORDER BY time,id LIMIT 2000",
            (trip_id,),
        ).fetchall()
    total = rows[0]["total"] if rows else 0
    return Trajectory(
        trip_id=trip_id,
        points=[Point(**row) for row in rows],
        simplified=total > len(rows),
        total_points=total,
    )
