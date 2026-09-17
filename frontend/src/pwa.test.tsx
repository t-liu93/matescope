import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { clearVehicleData } from "./pwa";

describe("offline vehicle-data guard", () => {
  it("removes vehicle history and trajectory queries without touching account or two-factor state", () => {
    const client = new QueryClient();
    client.setQueryData(["vehicles"], { items: [{ name: "SYNTHETIC Atlas" }] });
    client.setQueryData(["trips", { start: "2026-09-01T00:00:00Z" }], { items: [{ id: 1 }] });
    client.setQueryData(["charges", { start: "2026-09-01T00:00:00Z" }], { items: [{ id: 1 }] });
    client.setQueryData(["trajectory", 1], { points: [{ latitude: 52.1 }] });
    client.setQueryData(["settings"], { preferences: { saved: true } });
    client.setQueryData(["me"], { username: "admin" });
    client.setQueryData(["two-factor"], { enabled: true });

    clearVehicleData(client);

    expect(client.getQueryData(["vehicles"])).toBeUndefined();
    expect(client.getQueryData(["trips", { start: "2026-09-01T00:00:00Z" }])).toBeUndefined();
    expect(client.getQueryData(["charges", { start: "2026-09-01T00:00:00Z" }])).toBeUndefined();
    expect(client.getQueryData(["trajectory", 1])).toBeUndefined();
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
