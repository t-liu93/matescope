import type { components } from "./api/schema";

type TimeSeries = components["schemas"]["TimeSeries"];

export type ChartPoint = {
  time: number;
  value: number | null;
  key: string;
};

/**
 * Turn the API's explicit discontinuities into null chart points. Recharts is
 * told not to connect nulls, so neither absent buckets nor a >5 minute/internal
 * discontinuity can become an invented line segment.
 */
export function chartPoints(series: TimeSeries): ChartPoint[] {
  return series.points.flatMap((point, index) => {
    const value = series.aggregation === "last" ? point.value : point.mean;
    const time = new Date(point.time).getTime();
    if (!Number.isFinite(time)) return [];
    const key = `${time}-${index}`;
    return point.discontinuity
      ? [{ time, value: null, key: `${key}-break` }, { time, value: value ?? null, key }]
      : [{ time, value: value ?? null, key }];
  });
}

export function formatSeriesTime(value: string | number, timezone: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}
