import { expect, test, type BrowserContext, type Page } from "@playwright/test";

async function waitForWorkerControl(page: Page) {
  await page.goto("/login");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL ?? ""))
    .toContain("/sw.js");
}

async function cacheEntries(page: Page) {
  return page.evaluate(async () => {
    const keys = await caches.keys();
    const cache = await caches.open("matescope-offline-v2");
    return {
      keys: keys.sort(),
      entries: (await cache.keys()).map((request) => new URL(request.url).pathname + new URL(request.url).search).sort(),
    };
  });
}

async function mockLoginSetup(context: BrowserContext) {
  await context.route("**/api/v1/setup/status", async (route) => {
    await route.fulfill({ json: { administrator_exists: true, csrf_token: "pwa-test-csrf" } });
  });
}

async function mockVehicleHistory(context: BrowserContext, delayTrip = false) {
  let releaseTrip: () => void = () => {};
  const tripReady = new Promise<void>((resolve) => { releaseTrip = resolve; });
  let completeLateTripResponse: () => void = () => {};
  const lateTripResponseCompleted = new Promise<void>((resolve) => { completeLateTripResponse = resolve; });
  let tripDetailRequests = 0;
  await context.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: "admin" } });
    if (path.endsWith("/settings")) return route.fulfill({ json: {
      preferences: { language: "en", timezone: "UTC", tile_url: "https://tiles.example/{z}/{x}/{y}.png", saved: true },
      onboarding: { step: "review", completed: true },
    } });
    if (/\/vehicles\/\d+\/history-window$/.test(path)) return route.fulfill({ json: {
      preset: "last_30_days", timezone: "UTC", start: "2026-08-18T00:00:00Z", end: "2026-09-17T00:00:00Z", is_empty: false,
    } });
    if (path.endsWith("/vehicles")) return route.fulfill({ json: {
      items: [{ id: 1, name: "SYNTHETIC Atlas", model: "Model 3" }],
    } });
    if (path.endsWith("/trips")) return route.fulfill({ json: {
      items: [{ id: 1, vehicle_id: 1, start: "2026-09-17T10:00:00Z", end: "2026-09-17T11:00:00Z", duration_min: 60, distance_km: 12.5, speed_max_kmh: 72 }],
      next_cursor: null,
    } });
    if (path.endsWith("/charges")) return route.fulfill({ json: {
      items: [{ id: 1, vehicle_id: 1, start: "2026-09-17T10:00:00Z", end: "2026-09-17T11:00:00Z", duration_min: 60, energy_added_kwh: 22.5 }],
      next_cursor: null,
    } });
    if (/\/trips\/1$/.test(path)) {
      tripDetailRequests += 1;
      if (delayTrip) await tripReady;
      try {
        await route.fulfill({ json: {
          id: 1, vehicle_id: 1, start: "2026-09-17T10:00:00Z", end: "2026-09-17T11:00:00Z", duration_min: 60,
          distance_km: tripDetailRequests === 1 ? 12.5 : 25, speed_max_kmh: 72,
        } });
      } finally {
        if (delayTrip) completeLateTripResponse();
      }
      return;
    }
    if (path.endsWith("/trips/1/series")) return route.fulfill({ json: {
      trip_id: 1, capability: { available: true, reason: null }, series: [
        { name: "speed", unit: "km/h", start: "2026-09-17T10:00:00Z", end: "2026-09-17T11:00:00Z", sample_count: 1, bucket_count: 1, aggregation: "raw", capability: { available: true, reason: null }, points: [{ time: "2026-09-17T10:00:00Z", value: 42, discontinuity: false }] },
        { name: "power", unit: "kW", start: "2026-09-17T10:00:00Z", end: "2026-09-17T11:00:00Z", sample_count: 1, bucket_count: 1, aggregation: "raw", capability: { available: true, reason: null }, points: [{ time: "2026-09-17T10:00:00Z", value: -5, discontinuity: false }] },
      ],
    } });
    if (path.endsWith("/trajectory")) return route.fulfill({ json: {
      trip_id: 1, points: [{ id: 1, time: "2026-09-17T10:00:00Z", latitude: 52.1, longitude: 4.3, segment_id: 0 }], simplified: false, total_points: 1,
    } });
    return route.fulfill({ status: 404, json: { detail: "unexpected request" } });
  });
  return {
    releaseTrip,
    lateTripResponseCompleted,
    tripDetailRequests: () => tripDetailRequests,
  };
}

test("controlled production worker only caches the offline allowlist and never rewrites API navigation", async ({ page, context }) => {
  await waitForWorkerControl(page);
  await page.evaluate(async () => {
    await fetch("/icons/matescope-192.png?cache-bust=1");
    await fetch("/api/v1/pwa-cache-check");
    await fetch("/tiles/0/0/0.png");
    await fetch("https://tiles.example/0/0/0.png").catch(() => undefined);
    await fetch("/icons/matescope-192.png", { method: "POST", body: "ignored" }).catch(() => undefined);
  });
  expect(await cacheEntries(page)).toEqual({
    keys: ["matescope-offline-v2"],
    entries: [
      "/icons/matescope-192.png",
      "/icons/matescope-512.png",
      "/icons/matescope-maskable-512.png",
      "/offline.html",
    ],
  });

  await context.setOffline(true);
  const apiNavigation = await page.goto("/api").then(() => "resolved").catch(() => "rejected");
  expect(apiNavigation).toBe("rejected");
  await context.setOffline(false);
  await waitForWorkerControl(page);
  await context.setOffline(true);
  await page.goto("/trips?offline-check=1");
  await expect(page.getByText("You are offline", { exact: true })).toBeVisible();
  await expect(page.getByText(/Vehicle data and maps require a connection/)).toBeVisible();
  await context.setOffline(false);
  await page.goto("/manifest.webmanifest");
  expect(await page.textContent("body")).toContain("MateScope");
});

test("login status survives offline and worker updates wait for the user before scoped cleanup", async ({ page, context }) => {
  await mockLoginSetup(context);
  await waitForWorkerControl(page);
  await page.getByLabel(/^Password(?:\s*\*)?$/).fill("typing-must-remain");
  await context.setOffline(true);
  await expect(page.getByText("You are offline", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(page.getByText("当前处于离线状态", { exact: true })).toBeVisible();
  await context.setOffline(false);
  await expect(page.getByText("当前处于离线状态", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel(/^密码(?:\s*\*)?$/)).toHaveValue("typing-must-remain");

  await page.evaluate(async () => {
    await caches.open("matescope-offline-old");
    await caches.open("unrelated-cache");
  });
  await page.evaluate(async () => { await navigator.serviceWorker.register("/test-update-sw.js", { scope: "/" }); });
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration())?.waiting))).toBe(true);
  await expect(page.getByLabel(/^密码(?:\s*\*)?$/)).toHaveValue("typing-must-remain");

  await page.close();
  const verification = await activateUpdatedWorker(context);
  await expect.poll(() => verification.evaluate(async () => (await caches.keys()).sort()))
    .toEqual(["matescope-offline-v3", "unrelated-cache"]);
  await verification.close();
});

test("offline hides every vehicle-data route and reconnecting fetches a new detail value", async ({ page, context }) => {
  const history = await mockVehicleHistory(context);
  await waitForWorkerControl(page);
  await page.goto("/trips/1");
  await expect(page.getByText(/12\.5 km/)).toBeVisible();
  await expect(page.getByText("Route", { exact: true })).toBeVisible();

  await context.setOffline(true);
  await expect(page.getByText("Vehicle data is unavailable while offline. Reconnect to load it again.")).toBeVisible();
  await expect(page.getByText(/12\.5 km/)).toHaveCount(0);
  await expect(page.getByText("Route", { exact: true })).toHaveCount(0);
  for (const route of ["/vehicles", "/trips", "/charges"]) {
    await page.evaluate((nextRoute) => {
      window.history.pushState({}, "", nextRoute);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, route);
    await expect(page).toHaveURL(new RegExp(`${route}$`));
    await expect(page.getByText("Vehicle data is unavailable while offline. Reconnect to load it again.")).toBeVisible();
    await expect(page.getByText(/SYNTHETIC Atlas|12\.5 km|22\.5 kWh/)).toHaveCount(0);
  }

  await context.setOffline(false);
  await page.goto("/trips/1");
  await expect(page.getByText(/25 km/)).toBeVisible();
  await expect(page.getByText(/12\.5 km/)).toHaveCount(0);
  expect(history.tripDetailRequests()).toBe(2);
});

test("a trip response that arrives after offline cannot restore vehicle data", async ({ page, context }) => {
  const history = await mockVehicleHistory(context, true);
  await waitForWorkerControl(page);
  await page.goto("/trips/1");
  await expect(page.getByText("Loading…", { exact: true })).toBeVisible();

  await context.setOffline(true);
  await expect(page.getByText("Vehicle data is unavailable while offline. Reconnect to load it again.")).toBeVisible();
  history.releaseTrip();
  await history.lateTripResponseCompleted;
  await expect(page.getByText(/12\.5 km/)).toHaveCount(0);

  await context.setOffline(false);
  await expect(page.getByText(/25 km/)).toBeVisible();
  await expect(page.getByText(/12\.5 km/)).toHaveCount(0);
});

async function activateUpdatedWorker(context: BrowserContext) {
  const verification = await context.newPage();
  await verification.goto("/manifest.webmanifest");
  await verification.waitForFunction(() => navigator.serviceWorker.controller !== null);
  await expect.poll(() => verification.evaluate(async () => (await navigator.serviceWorker.getRegistration())?.active?.scriptURL ?? ""))
    .toContain("/test-update-sw.js");
  return verification;
}
