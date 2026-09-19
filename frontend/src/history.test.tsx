import { cleanup, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, describe, expect, it } from "vitest";

import { DetailValues, MetricCoverage, TripMoreData } from "./history";
import type { components } from "./api/schema";
import i18n from "./i18n";

function renderCoverage(coverage: Parameters<typeof MetricCoverage>[0]["coverage"]) {
  return render(<MantineProvider><MetricCoverage coverage={coverage} t={i18n.t.bind(i18n)} /></MantineProvider>);
}

afterEach(() => {
  cleanup();
  void i18n.changeLanguage("en");
});

describe("MetricCoverage", () => {
  it("always renders valid/applicable counts and localizes every unavailable reason", async () => {
    const cases = [
      ["no_ended_records", "No ended records are available for this metric.", "此指标没有已结束的记录可用。"],
      ["no_valid_values", "No valid values are available for this metric.", "此指标没有有效值可用。"],
      ["zero_denominator", "This metric cannot be calculated because its denominator is zero.", "此指标的分母为零，无法计算。"],
      ["unavailable", "This metric is unavailable.", "此指标不可用。"],
    ] as const;

    for (const [reason, english, chinese] of cases) {
      const { unmount } = renderCoverage({ applicable_count: 0, valid_count: 0, reason });
      expect(screen.getByText("0 of 0 applicable records", { exact: true })).toBeVisible();
      expect(screen.getByText(english, { exact: true })).toBeVisible();
      unmount();

      await i18n.changeLanguage("zh");
      const chineseView = renderCoverage({ applicable_count: 0, valid_count: 0, reason });
      expect(screen.getByText("0 条适用记录中的 0 条", { exact: true })).toBeVisible();
      expect(screen.getByText(chinese, { exact: true })).toBeVisible();
      chineseView.unmount();
      await i18n.changeLanguage("en");
    }
  });

  it("keeps full and partial coverage visible without an unavailable reason", () => {
    const { rerender } = renderCoverage({ applicable_count: 2, valid_count: 2, reason: null });
    expect(screen.getByText("2 of 2 applicable records", { exact: true })).toBeVisible();
    rerender(<MantineProvider><MetricCoverage coverage={{ applicable_count: 2, valid_count: 1, reason: null }} t={i18n.t.bind(i18n)} /></MantineProvider>);
    expect(screen.getByText("1 of 2 applicable records", { exact: true })).toBeVisible();
  });
});

describe("Trip detail summary", () => {
  it("keeps full places and labels estimated energy with its range basis", () => {
    const trip: components["schemas"]["Trip"] = {
      id: 7,
      vehicle_id: 3,
      start: "2026-01-30T08:00:00Z",
      end: "2026-01-30T09:30:00Z",
      duration_min: 90,
      distance_km: 42.5,
      speed_max_kmh: 110,
      start_place: "A very long starting place",
      end_place: "A very long destination place",
      start_battery_level: 80,
      end_battery_level: 65,
      estimated_energy_kwh: 8.4,
      estimated_average_consumption_wh_per_km: 198,
    };
    render(<MantineProvider><DetailValues item={trip} kind="trips" timezone="UTC" rangeBasis="ideal" /></MantineProvider>);
    expect(screen.getByText("A very long starting place", { exact: false })).toBeVisible();
    expect(screen.getByText("A very long destination place", { exact: false })).toBeVisible();
    expect(screen.getByText("Estimated", { exact: true })).toBeVisible();
    expect(screen.getByText("Based on ideal range", { exact: true })).toBeVisible();
  });
});

describe("Trip more data", () => {
  const series = (name: components["schemas"]["SeriesName"], unit: string): components["schemas"]["TimeSeries"] => ({
    name,
    unit,
    start: "2026-01-30T08:00:00Z",
    end: "2026-01-30T08:06:00Z",
    aggregation: "mean_min_max",
    sample_count: 2,
    bucket_count: 2,
    capability: { available: true, reason: null },
    points: [
      { time: "2026-01-30T08:00:00Z", mean: 12, min: 11, max: 13, value: null, discontinuity: false },
      { time: "2026-01-30T08:06:00Z", mean: null, min: null, max: null, value: null, discontinuity: true },
    ],
  });

  it("starts collapsed and reveals the temperature and elevation charts", () => {
    render(<MantineProvider><TripMoreData series={[series("inside_temperature", "°C"), series("outside_temperature", "°C"), series("elevation", "m")]} timezone="Europe/Amsterdam" /></MantineProvider>);
    const details = screen.getByText("More data", { exact: true }).closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    details?.setAttribute("open", "");
    expect(screen.getAllByText("Inside and outside temperature", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Elevation", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText(/Values use °C/)).toBeVisible();
    expect(screen.getByText(/Values use m/)).toBeVisible();
  });

  it("keeps missing temperature and elevation local", () => {
    render(<MantineProvider><TripMoreData series={[series("outside_temperature", "°C")]} timezone="UTC" /></MantineProvider>);
    const details = screen.getByText("More data", { exact: true }).closest("details");
    expect(details).not.toBeNull();
    details?.setAttribute("open", "");
    expect(screen.getByText("Inside temperature data is unavailable.", { exact: true })).toBeVisible();
    expect(screen.getByText("Elevation data is unavailable.", { exact: true })).toBeVisible();
  });
});
