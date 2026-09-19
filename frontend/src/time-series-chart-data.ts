import type { components } from "./api/schema";

type TimeSeries = components["schemas"]["TimeSeries"];

export type ChartPoint = {
  time: number;
  value: number | null;
  key: string;
};

export type DualChartPoint = {
  time: number;
  first: number | null;
  second: number | null;
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

/**
 * Align two discontinuity-aware series for Recharts' shared chart data model.
 * At a shared timestamp, a series with one point repeats that point beside the
 * other series' inserted null break, so a gap in one line cannot split the
 * other line.
 */
export function dualChartPoints(first: TimeSeries, second: TimeSeries): DualChartPoint[] {
  const byTime = new Map<number, { first: (number | null)[]; second: (number | null)[] }>();
  for (const [key, points] of [["first", chartPoints(first)], ["second", chartPoints(second)]] as const) {
    for (const point of points) {
      const values = byTime.get(point.time) ?? { first: [], second: [] };
      values[key].push(point.value);
      byTime.set(point.time, values);
    }
  }
  return [...byTime.entries()].sort(([left], [right]) => left - right).flatMap(([time, values]) => {
    const count = Math.max(values.first.length, values.second.length);
    const atOrLast = (series: (number | null)[], index: number) => index < series.length ? series[index] : series.at(-1) ?? null;
    return Array.from({ length: count }, (_, index) => ({
      time,
      first: atOrLast(values.first, index),
      second: atOrLast(values.second, index),
    }));
  });
}

export function formatSeriesTime(value: string | number, timezone: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}

export function seriesValue(series: TimeSeries, point: TimeSeries["points"][number]) {
  return series.aggregation === "last" ? point.value : point.mean;
}
