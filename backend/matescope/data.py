"""Authenticated, bounded historical summaries; all units are explicit."""

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import sql
from pydantic import BaseModel, field_validator

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


class Charge(Summary):
    energy_added_kwh: float | None


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
            if not timedelta(0) < self.end - self.start <= timedelta(days=90):
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
    "id, car_id AS vehicle_id, start_date AS start, end_date AS end, "
    "duration_min, distance AS distance_km, speed_max AS speed_max_kmh"
)
CHARGE_FIELDS = (
    "id, car_id AS vehicle_id, start_date AS start, end_date AS end, "
    "duration_min, charge_energy_added AS energy_added_kwh"
)


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
    conditions = [sql.SQL("start_date >= %s AND start_date < %s")]
    params: list[object] = [window.start, window.end]
    if window.vehicle_id is not None:
        conditions.append(sql.SQL("car_id = %s"))
        params.append(window.vehicle_id)
    if window.after:
        conditions.append(sql.SQL("(start_date, id) < (%s, %s)"))
        params.extend(window.after)
    params.append(window.limit + 1)
    query = sql.SQL("SELECT {} FROM public.{} WHERE {} ORDER BY start_date DESC, id DESC LIMIT %s")
    with connection(request) as database:
        rows = database.execute(
            query.format(
                sql.SQL(fields),
                sql.Identifier(table),
                sql.SQL(" AND ").join(conditions),
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
        row = database.execute(
            sql.SQL("SELECT {} FROM public.{} WHERE id=%s").format(
                sql.SQL(fields),
                sql.Identifier(table),
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
