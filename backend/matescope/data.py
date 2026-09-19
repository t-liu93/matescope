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
from .settings import MQTTResponse, PostgreSQLResponse, SMTPResponse, read_settings

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


class HistoryCapabilities(BaseModel):
    capabilities: dict[str, Capability]


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
            ]
        )
    else:
        fields.extend(
            [
                "NULL::smallint AS start_battery_level",
                "NULL::smallint AS end_battery_level",
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
            fields, joins = trip_projection(capability_status(database))
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
            fields, joins = trip_projection(capability_status(database))
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
