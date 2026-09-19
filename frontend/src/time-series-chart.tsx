import { useId, useMemo } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTranslation } from "react-i18next";

import type { components } from "./api/schema";
import { chartPoints, formatSeriesTime } from "./time-series-chart-data";

type TimeSeries = components["schemas"]["TimeSeries"];

function formatValue(value: number | null | undefined, unit: string) {
  return value === null || value === undefined
    ? "—"
    : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)} ${unit}`;
}

export function TimeSeriesChart({ series, timezone, title }: { series: TimeSeries; timezone: string; title: string }) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const points = useMemo(() => chartPoints(series), [series]);
  const hasValues = points.some((point) => point.value !== null);
  const aggregationLabel = t(`seriesAggregation_${series.aggregation}`);
  const rangeLabel = series.aggregation === "mean_min_max" ? t("seriesRangeDescription") : "";

  if (!series.capability.available) {
    return <p role="status">{t("seriesUnavailable")}</p>;
  }

  return (
    <figure className="time-series-chart" aria-labelledby={titleId}>
      <figcaption id={titleId}>{title}</figcaption>
      <p id={descriptionId} className="time-series-description">
        {t("seriesDescription", {
          unit: series.unit,
          aggregation: aggregationLabel,
          samples: series.sample_count,
          buckets: series.bucket_count,
          timezone,
        })} {rangeLabel}
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
