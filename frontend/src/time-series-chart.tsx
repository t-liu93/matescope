import { useId, useMemo } from "react";
import { Alert } from "@mantine/core";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTranslation } from "react-i18next";

import type { components } from "./api/schema";
import { chartPoints, dualChartPoints, formatSeriesTime, seriesValue } from "./time-series-chart-data";

type TimeSeries = components["schemas"]["TimeSeries"];

function formatValue(value: number | null | undefined, unit: string) {
  return value === null || value === undefined
    ? "—"
    : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: unit === "%" ? 0 : 2 }).format(value)} ${unit}`;
}

function SeriesDescription({ series, timezone }: { series: TimeSeries; timezone: string }) {
  const { t } = useTranslation();
  const aggregationLabel = t(`seriesAggregation_${series.aggregation}`);
  const rangeLabel = series.aggregation === "mean_min_max" ? t("seriesRangeDescription") : "";
  return <>{t("seriesDescription", {
    unit: series.unit,
    aggregation: aggregationLabel,
    samples: series.sample_count,
    buckets: series.bucket_count,
    timezone,
  })} {rangeLabel}</>;
}

export function TimeSeriesChart({ series, timezone, title }: { series: TimeSeries; timezone: string; title: string }) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const points = useMemo(() => chartPoints(series), [series]);
  const hasValues = points.some((point) => point.value !== null);

  if (!series.capability.available) {
    return <p role="status">{t("seriesUnavailable")}</p>;
  }

  return (
    <figure className="time-series-chart" aria-labelledby={titleId}>
      <figcaption id={titleId}>{title}</figcaption>
      <p id={descriptionId} className="time-series-description">
        <SeriesDescription series={series} timezone={timezone} />
      </p>
      {hasValues ? (
        <div className="time-series-plot" role="group" aria-describedby={descriptionId}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={points} accessibilityLayer tabIndex={0} margin={{ top: 8, right: 20, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                domain={["dataMin", "dataMax"]}
                minTickGap={44}
                tickFormatter={(value) => formatSeriesTime(value, timezone)}
                type="number"
              />
              <YAxis unit={` ${series.unit}`} width={58} />
              <Tooltip
                formatter={(value) => formatValue(typeof value === "number" ? value : null, series.unit)}
                labelFormatter={(value) => formatSeriesTime(typeof value === "string" || typeof value === "number" ? value : "", timezone)}
              />
              <Line
                connectNulls={false}
                dataKey="value"
                dot={false}
                isAnimationActive="auto"
                name={`${title} (${series.unit})`}
                stroke="var(--mantine-color-blue-filled)"
                strokeWidth={2}
                type="linear"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : <p role="status">{t("seriesEmpty")}</p>}
      <details className="time-series-table">
        <summary>{t("seriesDataTable")}</summary>
        <table>
          <thead><tr><th scope="col">{t("seriesTime")}</th><th scope="col">{title} ({series.unit})</th></tr></thead>
          <tbody>
            {series.points.map((point, index) => (
              <tr key={`${point.time}-${index}`}>
                <td>{formatSeriesTime(point.time, timezone)}</td>
                <td>{point.discontinuity
                  ? `${t("seriesGap")}: ${formatValue(series.aggregation === "last" ? point.value : point.mean, series.unit)}`
                  : formatValue(series.aggregation === "last" ? point.value : point.mean, series.unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/**
 * A paired chart deliberately supplies each line its own discontinuity-aware
 * data. This lets a gap in speed break only speed, while a usable power line
 * remains continuous (and vice versa).
 */
export function DualTimeSeriesChart({
  first,
  second,
  timezone,
  title,
  firstTitle,
  secondTitle,
}: {
  first: TimeSeries;
  second: TimeSeries;
  timezone: string;
  title: string;
  firstTitle: string;
  secondTitle: string;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const firstPoints = useMemo(() => chartPoints(first), [first]);
  const secondPoints = useMemo(() => chartPoints(second), [second]);
  const points = useMemo(() => dualChartPoints(first, second), [first, second]);
  const firstHasValues = first.capability.available && firstPoints.some((point) => point.value !== null);
  const secondHasValues = second.capability.available && secondPoints.some((point) => point.value !== null);
  const times = [...firstPoints, ...secondPoints].map((point) => point.time);
  const domain: [number, number] | undefined = times.length ? [Math.min(...times), Math.max(...times)] : undefined;

  return (
    <figure className="time-series-chart" aria-labelledby={titleId}>
      <figcaption id={titleId}>{title}</figcaption>
      <p id={descriptionId} className="time-series-description">
        {firstTitle} ({first.unit}): <SeriesDescription series={first} timezone={timezone} /> {secondTitle} ({second.unit}): <SeriesDescription series={second} timezone={timezone} />
      </p>
      {!first.capability.available && <Alert color="yellow" role="status">{firstTitle}: {t("seriesUnavailable")}</Alert>}
      {!second.capability.available && <Alert color="yellow" role="status">{secondTitle}: {t("seriesUnavailable")}</Alert>}
      {(firstHasValues || secondHasValues) ? (
        <div className="time-series-plot" role="group" aria-describedby={descriptionId}>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={points} accessibilityLayer tabIndex={0} margin={{ top: 8, right: 12, bottom: 8, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="time" domain={domain} minTickGap={44} tickFormatter={(value) => formatSeriesTime(value, timezone)} type="number" />
              {firstHasValues && <YAxis yAxisId="first" unit={` ${first.unit}`} width={64} />}
              {secondHasValues && <YAxis yAxisId="second" orientation="right" unit={` ${second.unit}`} width={64} />}
              <Tooltip
                formatter={(value, name) => {
                  const unit = name === firstTitle ? first.unit : second.unit;
                  return formatValue(typeof value === "number" ? value : null, unit);
                }}
                labelFormatter={(value) => formatSeriesTime(typeof value === "string" || typeof value === "number" ? value : "", timezone)}
              />
              <Legend />
              {firstHasValues && <Line connectNulls={false} dataKey="first" dot={false} isAnimationActive="auto" name={firstTitle} stroke="var(--mantine-color-blue-filled)" strokeWidth={2} type="linear" yAxisId="first" />}
              {secondHasValues && <Line connectNulls={false} dataKey="second" dot={false} isAnimationActive="auto" name={secondTitle} stroke="var(--mantine-color-orange-filled)" strokeWidth={2} type="linear" yAxisId="second" />}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : <p role="status">{t("seriesEmpty")}</p>}
      <details className="time-series-table">
        <summary>{t("seriesDataTable")}</summary>
        <table>
          <thead><tr><th scope="col">{t("seriesTime")}</th><th scope="col">{firstTitle} ({first.unit})</th><th scope="col">{secondTitle} ({second.unit})</th></tr></thead>
          <tbody>
            {Array.from(new Set([...first.points, ...second.points].map((point) => point.time))).sort().map((time) => {
              const firstPoint = first.points.find((point) => point.time === time);
              const secondPoint = second.points.find((point) => point.time === time);
              const cell = (point: TimeSeries["points"][number] | undefined, series: TimeSeries) => !point ? "—" : point.discontinuity
                ? `${t("seriesGap")}: ${formatValue(seriesValue(series, point), series.unit)}`
                : formatValue(seriesValue(series, point), series.unit);
              return <tr key={time}><td>{formatSeriesTime(time, timezone)}</td><td>{cell(firstPoint, first)}</td><td>{cell(secondPoint, second)}</td></tr>;
            })}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
