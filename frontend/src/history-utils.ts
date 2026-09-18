import type { components } from "./api/schema";
import type { HistoryWindow } from "./api/client";

type Point = components["schemas"]["Point"];

export function defaultWindow(now = new Date()): HistoryWindow {
  return {
    start: new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    end: now.toISOString(),
  };
}

export type CalendarRange = [string | null, string | null];

function calendarParts(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  return Object.fromEntries(parts.filter(({ type }) => type !== "literal").map(({ type, value }) => [type, value])) as Record<string, string>;
}

export function localDateIso(date: Date, timezone: string): string {
  const parts = calendarParts(date, timezone);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function addCalendarDays(value: string, days: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const result = new Date(Date.UTC(year, month - 1, day + days));
  return result.toISOString().slice(0, 10);
}

export function windowToCalendarRange(window: HistoryWindow, timezone: string): CalendarRange {
  return [localDateIso(new Date(window.start), timezone), localDateIso(new Date(new Date(window.end).getTime() - 1), timezone)];
}

export function validateWindow(window: HistoryWindow): string | null {
  const start = parseUtcIso(window.start);
  const end = parseUtcIso(window.end);
  if (!start || !end) return "historyInvalidUtc";
  if (end <= start) return "historyInvalidWindow";
  return null;
}

export function parseUtcIso(value: string): Date | null {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,3})?Z$/.exec(value);
  if (!match)
    return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const milliseconds = match[2] ? match[2].slice(1).padEnd(3, "0") : "000";
  return date.toISOString() === `${match[1]}.${milliseconds}Z` ? date : null;
}

export function groupTrajectory(points: Point[]): Point[][] {
  const groups: Point[][] = [];
  for (const point of points) {
    const current = groups.at(-1);
    if (!current || current[0].segment_id !== point.segment_id) groups.push([point]);
    else current.push(point);
  }
  return groups;
}
