import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { clearAuthenticatedSession, clearVehicleData } from "./pwa";

describe("offline vehicle-data guard", () => {
  it("clears navigation metadata when an authenticated session ends", () => {
    const client = new QueryClient();
    client.setQueryData(["me"], { username: "admin" });
    sessionStorage.setItem("matescope-history-navigation", JSON.stringify({
      cursors: { "/trips?vehicle=1": [null, "opaque"] },
      returns: { "/trips?vehicle=1": { recordId: 42, scrollY: 640 } },
    }));

    clearAuthenticatedSession(client);

    expect(client.getQueryData(["me"])).toBeUndefined();
    expect(sessionStorage.getItem("matescope-history-navigation")).toBeNull();
  });

  it("removes every vehicle-data query family without touching account or two-factor state", () => {
    const client = new QueryClient();
    client.setQueryData(["vehicles"], { items: [{ name: "SYNTHETIC Atlas" }] });
    client.setQueryData(["trips", { start: "2026-09-01T00:00:00Z" }], { items: [{ id: 1 }] });
    client.setQueryData(["charges", { start: "2026-09-01T00:00:00Z" }], { items: [{ id: 1 }] });
    client.setQueryData(["trajectory", 1], { points: [{ latitude: 52.1 }] });
    client.setQueryData(["trip", 1], { vehicle_id: 1 });
    client.setQueryData(["charge", 1], { vehicle_id: 1 });
    client.setQueryData(["history-capabilities"], { capabilities: { trip_series: { available: true } } });
    client.setQueryData(["history-window", 1], { start: "2026-01-01T00:00:00Z" });
    client.setQueryData(["trip-summary", 1], { distance_km: 12.5 });
    client.setQueryData(["charge-summary", 1], { energy_added_kwh: 22.5 });
    client.setQueryData(["latest-values", 1], { odometer_km: 100 });
    client.setQueryData(["trip-series", 1], { series: [] });
    client.setQueryData(["charge-series", 1], { series: [] });
    client.setQueryData(["settings"], { preferences: { saved: true } });
    client.setQueryData(["me"], { username: "admin" });
    client.setQueryData(["two-factor"], { enabled: true });

    clearVehicleData(client);

    expect(client.getQueryData(["vehicles"])).toBeUndefined();
    expect(client.getQueryData(["trips", { start: "2026-09-01T00:00:00Z" }])).toBeUndefined();
    expect(client.getQueryData(["charges", { start: "2026-09-01T00:00:00Z" }])).toBeUndefined();
    expect(client.getQueryData(["trajectory", 1])).toBeUndefined();
    expect(client.getQueryData(["trip", 1])).toBeUndefined();
    expect(client.getQueryData(["charge", 1])).toBeUndefined();
    expect(client.getQueryData(["history-capabilities"])).toBeUndefined();
    expect(client.getQueryData(["history-window", 1])).toBeUndefined();
    expect(client.getQueryData(["trip-summary", 1])).toBeUndefined();
    expect(client.getQueryData(["charge-summary", 1])).toBeUndefined();
    expect(client.getQueryData(["latest-values", 1])).toBeUndefined();
    expect(client.getQueryData(["trip-series", 1])).toBeUndefined();
    expect(client.getQueryData(["charge-series", 1])).toBeUndefined();
    expect(client.getQueryData(["settings"])).toEqual({ preferences: { saved: true } });
    expect(client.getQueryData(["me"])).toEqual({ username: "admin" });
    expect(client.getQueryData(["two-factor"])).toEqual({ enabled: true });
  });

  it("does not restore a vehicle query after its delayed request completes", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveResponse: (value: { items: { id: number }[] }) => void = () => {};
    let requestCompleted = false;
    const response = new Promise<{ items: { id: number }[] }>((resolve) => {
      resolveResponse = (value) => {
        requestCompleted = true;
        resolve(value);
      };
    });
    const request = client.fetchQuery({ queryKey: ["trips", 1], queryFn: () => response });

    clearVehicleData(client);
    resolveResponse({ items: [{ id: 1 }] });
    await response;
    await expect(request).rejects.toThrow("CancelledError");

    expect(requestCompleted).toBe(true);
    expect(client.getQueryData(["trips", 1])).toBeUndefined();
  });
});
