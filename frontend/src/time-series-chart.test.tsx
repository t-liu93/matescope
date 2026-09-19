import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, describe, expect, it } from "vitest";

import type { components } from "./api/schema";
import i18n from "./i18n";
import { chartPoints, formatSeriesTime } from "./time-series-chart-data";
import { TimeSeriesChart } from "./time-series-chart";

type TimeSeries = components["schemas"]["TimeSeries"];

const meanSeries: TimeSeries = {
  name: "power",
  unit: "kW",
  start: "2026-03-29T00:00:00Z",
  end: "2026-03-29T00:12:00Z",
  sample_count: 4,
  bucket_count: 4,
  aggregation: "mean_min_max",
  capability: { available: true, reason: null },
  points: [
    { time: "2026-03-29T00:00:00Z", mean: -4.2, min: -5, max: -3, value: -4.2, discontinuity: false },
    { time: "2026-03-29T00:01:00Z", mean: -2, min: -3, max: -1, value: -2, discontinuity: false },
    { time: "2026-03-29T00:08:00Z", mean: 3.5, min: 2, max: 4, value: 3.5, discontinuity: true },
    { time: "2026-03-29T00:12:00Z", mean: null, min: null, max: null, value: null, discontinuity: true },
  ],
};

afterEach(() => {
  cleanup();
  void i18n.changeLanguage("en");
});

describe("TimeSeriesChart", () => {
  it("keeps discontinuities and null buckets as null chart points without losing negative values", () => {
    expect(chartPoints(meanSeries)).toEqual([
      expect.objectContaining({ value: -4.2 }),
      expect.objectContaining({ value: -2 }),
      expect.objectContaining({ value: null, key: expect.stringContaining("-break") }),
      expect.objectContaining({ value: 3.5 }),
      expect.objectContaining({ value: null, key: expect.stringContaining("-break") }),
      expect.objectContaining({ value: null }),
    ]);
  });

  it("explains aggregation/ranges, formats in the saved timezone, and exposes a keyboard-readable data table", () => {
    render(<MantineProvider><TimeSeriesChart series={meanSeries} timezone="Europe/Amsterdam" title="Power" /></MantineProvider>);

    expect(screen.getByText(/Mean values from 4 samples in 4 time buckets/)).toBeVisible();
    expect(screen.getByText(/minimum and maximum/)).toBeVisible();
    const summary = screen.getByText("Chart data table");
    fireEvent.click(summary);
    expect(screen.getByText("-4.2 kW")).toBeVisible();
    expect(screen.getByText("Data gap: 3.5 kW")).toBeVisible();
    expect(screen.getByText("Data gap: —")).toBeVisible();
    expect(screen.getByText(formatSeriesTime(meanSeries.points[0].time, "Europe/Amsterdam"), { exact: true })).toBeVisible();
    expect(screen.getByRole("table")).toBeVisible();
  });

  it("uses last values for SOC and localizes the text alternative", async () => {
    const series: TimeSeries = {
      ...meanSeries,
      name: "battery",
      unit: "%",
      aggregation: "last",
      points: [{ time: "2026-03-29T00:00:00Z", value: 68, discontinuity: false }],
    };
    await i18n.changeLanguage("zh");
    render(<MantineProvider><TimeSeriesChart series={series} timezone="UTC" title="电量" /></MantineProvider>);

    expect(screen.getByText(/最后一个值来自 4 个样本和 4 个时间桶/)).toBeVisible();
    fireEvent.click(screen.getByText("图表数据表"));
    expect(screen.getByText("68 %")).toBeVisible();
  });
});
