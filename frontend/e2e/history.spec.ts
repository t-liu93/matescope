import { expect, test, type Page } from "@playwright/test";
import { clickAuthenticatedAction } from "./auth-helper";

const credentials = { username: "m0-t03-admin", password: "m0-t03-password" };
const baseUrl = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:8000");
const realSql = process.env.MATESCOPE_E2E_REAL_PG === "1"
  && ["127.0.0.1", "localhost", "::1"].includes(baseUrl.hostname);
const tilePng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

async function localTiles(page: Page, fail = false, attempts?: { count: number }) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === "https://tiles.example") {
      if (attempts) attempts.count += 1;
      await route.fulfill(fail
        ? { status: 503, contentType: "text/plain", body: "synthetic tile failure" }
        : { status: 200, contentType: "image/png", body: tilePng });
    } else if (![
      baseUrl.origin,
      "http://127.0.0.1:49233",
    ].includes(url.origin)) await route.abort();
    // Preserve the API fixtures registered before this network guard.
    else await route.fallback();
  });
}

async function signIn(page: Page) {
  await page.goto("/login");
  const username = page.getByLabel(/^Username(?:\s*\*)?$/);
  await expect(username).toBeVisible();
  const creating = await page.getByRole("heading", { name: "Create the administrator" }).isVisible();
  await username.fill(credentials.username);
  await page.getByLabel(/^Password(?:\s*\*)?$/).fill(credentials.password);
  if (creating) await page.getByLabel(/^Confirm password(?:\s*\*)?$/).fill(credentials.password);
  await clickAuthenticatedAction(
    page,
    creating ? "/api/v1/setup/administrator" : "/api/v1/auth/login",
    () => page.getByRole("button", { name: creating ? "Create administrator" : "Sign in", exact: true }).click(),
  );
  await expect(page).not.toHaveURL(/\/login$/);
}

async function saveSyntheticPostgres(page: Page) {
  // The real flow always saves through Settings, then runs the explicit test.
  await page.goto("/settings");
  const preferences = page.getByRole("region", { name: "Preferences", exact: true });
  await expect(preferences).toBeVisible();
  await preferences.getByLabel(/^Display timezone(?:\s*\*)?$/).fill("Europe/Amsterdam");
  await preferences.getByLabel("Map tile URL", { exact: true }).fill("https://tiles.example/{z}/{x}/{y}.png");
  await preferences.getByRole("button", { name: "Save", exact: true }).click();
  const pg = page.getByRole("region", { name: "PostgreSQL", exact: true });
  await pg.getByLabel("Host", { exact: true }).fill("postgres");
  await pg.getByLabel(/^Username(?:\s*\*)?$/).fill("matescope_readonly");
  await pg.getByLabel("Database", { exact: true }).fill("teslamate_synthetic");
  await pg.getByLabel("Enabled", { exact: true }).check();
  await pg.getByRole("textbox", { name: "Password handling", exact: true }).click();
  await page.getByRole("option", { name: "Replace password", exact: true }).click();
  await pg.getByLabel(/^Password \(optional\)/).fill("synthetic-reader-only");
  await pg.getByRole("button", { name: "Save", exact: true }).click();
  const testConnection = pg.getByRole("button", { name: "Test saved connection", exact: true });
  await expect(testConnection).toBeEnabled();
  await testConnection.click();
  await expect(pg.getByText("Connection test succeeded.", { exact: true })).toBeVisible();
}

async function finishOnboarding(page: Page) {
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Setup", exact: true })).toBeVisible();
  const steps = ["Preferences", "PostgreSQL", "MQTT", "SMTP", "Two-factor authentication", "Review setup"] as const;
  type SetupStep = (typeof steps)[number];
  const currentStep = async (previous?: SetupStep): Promise<SetupStep> => {
    let observed: SetupStep | null = null;
    await expect.poll(async () => {
      observed = null;
      for (const step of steps) {
        if (step !== previous && await page.getByRole("heading", { name: step, exact: true }).isVisible()) {
          observed = step;
          return step;
        }
      }
      return null;
    }, { timeout: 15_000 }).toMatch(/^(Preferences|PostgreSQL|MQTT|SMTP|Two-factor authentication|Review setup)$/);
    if (observed === null) throw new Error("Setup step was not rendered");
    return observed;
  };

  // Confirm each rendered step after navigation. This also handles a persisted
  // onboarding state that already starts at Review setup.
  for (;;) {
    const step = await currentStep();
    if (step === "Review setup") break;
    const region = page.getByRole("region", { name: step, exact: true });
    const action = step === "Preferences" || step === "PostgreSQL" ? "Save" : "Skip for now";
    const button = step === "Two-factor authentication"
      ? page.getByRole("button", { name: action, exact: true })
      : region.getByRole("button", { name: action, exact: true });
    await button.click();
    await currentStep(step);
  }
  const review = page.getByRole("region", { name: "Review setup", exact: true });
  await expect(review).toBeVisible();
  await review.getByRole("button", { name: "Finish setup", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Setup complete", exact: true })).toBeVisible();
}

type MockOptions = {
  empty?: boolean;
  zeroCars?: boolean;
  settingsFailure?: boolean;
  settingsFailuresRemaining?: { count: number };
  trajectoryFailure?: boolean;
  language?: "en" | "zh";
  settingsRequests?: { count: number };
  vehicleRequests?: { count: number };
  historyWindowRequests?: { requests: URL[] };
  preferenceBodies?: { bodies: Record<string, unknown>[] };
  deferredHistoryWindow?: { preset: string; vehicleId?: number; started: { count: number }; release: Promise<void> };
  tripSummaryFailure?: boolean;
  tripSummaryPermissionFailure?: boolean;
  tripSummaryRequests?: { requests: URL[] };
  tripSummaryResponse?: Record<string, unknown>;
  chargeSummaryFailure?: boolean;
  chargeSummaryPermissionFailure?: boolean;
  chargeSummaryRequests?: { requests: URL[] };
  chargeSummaryResponse?: Record<string, unknown>;
  displayCurrency?: "EUR";
  chargeItems?: Record<string, unknown>[];
};
async function mockHistoryApi(page: Page, options: MockOptions = {}) {
  const lists: URL[] = [];
  const settings = {
    preferences: { language: options.language ?? "en", timezone: "Europe/Amsterdam", tile_url: "https://tiles.example/{z}/{x}/{y}.png", range_basis: "rated", display_currency: options.displayCurrency ?? null, saved: true },
    postgresql: { password_set: false, version: 1, status: "success", test_available: true, test_result: null, host: "postgres", username: "reader", enabled: true, skipped: false, port: 5432, database: "teslamate_synthetic", sslmode: "disable" },
    mqtt: { password_set: false, version: 0, status: "disabled", test_available: false, test_result: null, host: "", username: "", enabled: false, skipped: true, port: 1883, tls: false, verify_tls: true, topic_prefix: "" },
    smtp: { password_set: false, version: 0, status: "disabled", test_available: false, test_result: null, host: "", username: "", enabled: false, skipped: true, port: 587, tls_mode: "starttls", verify_tls: true, sender: "" },
    onboarding: { step: "review", completed: true },
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = new URL(route.request().url());
    const { pathname: path } = request;
    if (path.endsWith("/setup/status")) return route.fulfill({ json: { administrator_exists: true } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: credentials.username } });
    if (path.endsWith("/auth/csrf")) return route.fulfill({ json: { csrf_token: "mock-csrf" } });
    if (path.endsWith("/settings/preferences")) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      options.preferenceBodies?.bodies.push(body);
      Object.assign(settings.preferences, body, { saved: true });
      return route.fulfill({ json: settings });
    }
    if (path.endsWith("/settings")) {
      if (options.settingsRequests) options.settingsRequests.count += 1;
      const settingsFailure = options.settingsFailure
        || (options.settingsFailuresRemaining !== undefined && options.settingsFailuresRemaining.count-- > 0);
      return route.fulfill(settingsFailure
        ? { status: 503, json: { detail: "unavailable" } } : { json: settings });
    }
    if (path.endsWith("/vehicles")) {
      if (options.vehicleRequests) options.vehicleRequests.count += 1;
      return route.fulfill({ json: { items: options.zeroCars ? [] : [{ id: 1, name: "SYNTHETIC Atlas", model: "Model 3" }, { id: 2, name: "SYNTHETIC Boreal", model: null }] } });
    }
    if (/\/vehicles\/\d+\/history-window$/.test(path)) {
      if (options.historyWindowRequests) options.historyWindowRequests.requests.push(request);
      const preset = request.searchParams.get("preset") ?? "last_30_days";
      if (options.deferredHistoryWindow?.preset === preset
        && (options.deferredHistoryWindow.vehicleId === undefined || Number(path.split("/").at(-2)) === options.deferredHistoryWindow.vehicleId)) {
        options.deferredHistoryWindow.started.count += 1;
        await options.deferredHistoryWindow.release;
      }
      const custom = preset === "custom";
      return route.fulfill({ json: { preset, timezone: "Europe/Amsterdam", start: custom ? "2026-01-01T00:00:00Z" : preset === "all_history" ? null : "2026-08-14T00:00:00Z", end: "2026-09-13T00:00:00Z", is_empty: options.empty ?? false } });
    }
    if (/\/vehicles\/\d+\/trip-summary$/.test(path)) {
      options.tripSummaryRequests?.requests.push(request);
      if (options.tripSummaryPermissionFailure) return route.fulfill({ status: 503, json: { detail: { code: "insufficient_permissions" } } });
      if (options.tripSummaryFailure) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
      return route.fulfill({ json: {
        vehicle_id: Number(path.split("/").at(-2)), start: request.searchParams.get("start"), end: request.searchParams.get("end"),
        total_count: 52, ended_count: 51, not_ended_count: 1, distance_km: 456.7, duration_min: 540, estimated_energy_kwh: 78.9,
        estimated_average_consumption_wh_per_km: 172.8,
        distance_coverage: { applicable_count: 51, valid_count: 50 }, duration_coverage: { applicable_count: 51, valid_count: 51 },
        estimated_energy_coverage: { applicable_count: 51, valid_count: 49 }, estimated_average_consumption_coverage: { applicable_count: 51, valid_count: 49 },
        estimate_capability: { available: true, reason: null },
        ...options.tripSummaryResponse,
      } });
    }
    if (/\/vehicles\/\d+\/charge-summary$/.test(path)) {
      options.chargeSummaryRequests?.requests.push(request);
      if (options.chargeSummaryPermissionFailure) return route.fulfill({ status: 503, json: { detail: { code: "insufficient_permissions" } } });
      if (options.chargeSummaryFailure) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
      return route.fulfill({ json: {
        vehicle_id: Number(path.split("/").at(-2)), start: request.searchParams.get("start"), end: request.searchParams.get("end"),
        total_count: 52, ended_count: 51, not_ended_count: 1, energy_added_kwh: 456.7, duration_min: 540, cost: options.displayCurrency ? 12.5 : null, currency: options.displayCurrency ?? null,
        energy_added_coverage: { applicable_count: 51, valid_count: 50, reason: null }, duration_coverage: { applicable_count: 51, valid_count: 51, reason: null },
        cost_coverage: { applicable_count: 51, valid_count: 49, reason: null }, cost_capability: { available: true, reason: null },
        ...options.chargeSummaryResponse,
      } });
    }
    if (path.endsWith("/trips") || path.endsWith("/charges")) {
      lists.push(request);
      const trips = path.endsWith("/trips");
      const items = options.empty ? [] : trips
        ? [{ id: 1, vehicle_id: 1, start: "2026-03-29T00:30:00Z", end: "2026-03-29T01:30:00Z", duration_min: 60, distance_km: 12.5, speed_max_kmh: 72 }, { id: 2, vehicle_id: 1, start: "2026-09-13T10:00:00Z", end: null, duration_min: null, distance_km: null, speed_max_kmh: null }]
        : options.chargeItems ?? [{ id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5, place: "SYNTHETIC Supercharger", start_battery_level: 20, end_battery_level: 60, cost: 0 }, { id: 2, vehicle_id: 1, start: "2026-09-13T20:00:00Z", end: null, duration_min: null, energy_added_kwh: null, place: null, start_battery_level: null, end_battery_level: null, cost: null }, { id: 3, vehicle_id: 1, start: "2026-09-11T20:00:00Z", end: "2026-09-11T20:45:00Z", duration_min: 45, energy_added_kwh: 10, place: "SYNTHETIC Supercharger", start_battery_level: 30, end_battery_level: 50, cost: 12.5 }];
      return route.fulfill({ json: { items, next_cursor: !options.empty && !request.searchParams.get("cursor") ? "cursor-1" : null, start: request.searchParams.get("start"), end: request.searchParams.get("end") } });
    }
    if (/\/trips\/1$/.test(path)) return route.fulfill({ json: { id: 1, vehicle_id: 1, start: "2026-03-29T00:30:00Z", end: "2026-03-29T01:30:00Z", duration_min: 60, distance_km: 12.5, speed_max_kmh: 72, start_place: "SYNTHETIC Starting Place", end_place: "SYNTHETIC Destination Place", start_battery_level: 82, end_battery_level: 68, estimated_energy_kwh: 2.4, estimated_average_consumption_wh_per_km: 192 } });
    if (/\/trips\/2$/.test(path)) return route.fulfill({ json: { id: 2, vehicle_id: 2, start: "2026-03-30T00:30:00Z", end: "2026-03-30T01:30:00Z", duration_min: 60, distance_km: 22, speed_max_kmh: 72, start_place: "SYNTHETIC Boreal Start", end_place: "SYNTHETIC Boreal End", start_battery_level: 77, end_battery_level: 60, estimated_energy_kwh: 4.1, estimated_average_consumption_wh_per_km: 186 } });
    if (/\/charges\/1$/.test(path)) return route.fulfill({ json: { id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5 } });
    if (path.endsWith("/trajectory")) {
      if (options.trajectoryFailure) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
      return route.fulfill({ json: { trip_id: 1, points: [{ id: 1, time: "2026-03-29T00:30:00Z", latitude: 52.1, longitude: 4.3, segment_id: 0 }, { id: 2, time: "2026-03-29T00:45:00Z", latitude: 52.15, longitude: 4.35, segment_id: 0 }, { id: 3, time: "2026-03-29T01:30:00Z", latitude: 52.2, longitude: 4.4, segment_id: 1 }, { id: 4, time: "2026-03-29T01:31:00Z", latitude: 52.21, longitude: 4.41, segment_id: 1 }], simplified: true, total_points: 4950 } });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected history mock route" } });
  });
  return lists;
}

test.describe("T09 calendar history filters", () => {
  test("uses presets, stable cursor pages, missing values, details, and DST display", async ({ page }) => {
    const lists = await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    await page.getByRole("textbox", { name: "Vehicle", exact: true }).click();
    await page.getByRole("option", { name: "SYNTHETIC Atlas", exact: true }).click();
    await page.getByRole("button", { name: "Last 7 days", exact: true }).click();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("1");
    await expect.poll(() => lists.at(-1)?.searchParams.get("start")).toBe("2026-08-14T00:00:00Z");
    await expect(page.getByText("Record not ended", { exact: true })).toBeVisible();
    await expect(page.getByText("—", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Mar 29, 2026, 1:30 AM", { exact: true })).toBeVisible();
    await expect(page.getByText("Mar 29, 2026, 1:30 AM", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    await expect.poll(() => lists.at(-1)?.searchParams.get("start")).toBe("2026-08-14T00:00:00Z");
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("1");
    await page.getByRole("button", { name: "Previous page", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBeNull();
    await page.getByRole("button", { name: "This year", exact: true }).click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page).toHaveURL(/preset=last_7_days/);
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("1");
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBeNull();
    await page.locator('a[href^="/trips/1"]').click();
    await expect(page.getByText("SYNTHETIC Atlas · #1", { exact: true })).toBeVisible();
    await expect(page.getByText("SYNTHETIC Starting Place", { exact: false })).toBeVisible();
    await expect(page.getByText("SYNTHETIC Destination Place", { exact: false })).toBeVisible();
    await expect(page.getByText("82 → 68", { exact: false })).toBeVisible();
    await expect(page.getByText("Estimated", { exact: true })).toBeVisible();
    await expect(page.getByText("Based on rated range", { exact: true })).toBeVisible();
    const detailLayout = page.locator(".trip-detail-layout");
    await expect(detailLayout).toBeVisible();
    const columns = await detailLayout.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length);
    expect(columns).toBe(test.info().project.name === "desktop" ? 2 : 1);
    await expect(page.getByText(/12\.5 km/)).toBeVisible();
    await expect(page.getByLabel("Trip route map")).toBeVisible();
    await expect(page.locator("path.leaflet-interactive")).toHaveCount(2);
    await expect(page.getByText("Showing a simplified route from 4950 recorded positions.")).toBeVisible();
    await page.goto("/charges");
    await page.locator('a[href^="/charges/1"]').click();
    await expect(page.getByText(/22\.5 kWh/)).toBeVisible();
  });

  test("resets pagination when a new range or vehicle is applied", async ({ page }) => {
    const lists = await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("1");
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    await page.getByRole("button", { name: "This year", exact: true }).click();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect(page).not.toHaveURL(/[?&](cursor|page)=/);
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBeNull();
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect(page).toHaveURL(/cursor=cursor-1.*page=2/);
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    await page.getByRole("textbox", { name: "Vehicle", exact: true }).click();
    await page.getByRole("option", { name: "SYNTHETIC Boreal", exact: true }).click();
    await expect(page).not.toHaveURL(/[?&](cursor|page)=/);
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("2");
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBeNull();
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect(page).toHaveURL(/cursor=cursor-1.*page=2/);
  });

  test("restores a paged list after reload and browser Back/Forward", async ({ page }) => {
    const lists = await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect(page).toHaveURL(/cursor=cursor-1.*page=2/);
    await page.reload();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    await page.evaluate(() => { document.body.style.minHeight = "2400px"; window.scrollTo(0, 640); });
    const record = page.locator('[data-history-record-id="1"]');
    await record.click();
    await expect(page).toHaveURL(/\/trips\/1\?.*cursor=cursor-1.*page=2/);
    await page.goBack();
    await expect(page).toHaveURL(/\/trips\?.*cursor=cursor-1.*page=2/);
    await expect(record).toBeFocused();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    await page.goForward();
    await expect(page).toHaveURL(/\/trips\/1\?.*cursor=cursor-1.*page=2/);
    await page.goBack();
    await expect(page).toHaveURL(/\/trips\?.*cursor=cursor-1.*page=2/);
    await expect(record).toBeFocused();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  });

  test("does not apply a delayed range after Cancel", async ({ page }) => {
    let release!: () => void;
    const started = { count: 0 };
    await mockHistoryApi(page, {
      deferredHistoryWindow: { preset: "all_history", started, release: new Promise<void>((resolve) => { release = resolve; }) },
    });
    await localTiles(page);
    await page.goto("/trips");
    await expect(page).toHaveURL(/start=.*end=/);
    const before = page.url();
    await page.getByRole("button", { name: "All history", exact: true }).click();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect.poll(() => started.count).toBe(1);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    release();
    await expect(page).toHaveURL(before);
  });

  test("does not apply a delayed range after switching vehicles", async ({ page }) => {
    let release!: () => void;
    const started = { count: 0 };
    await mockHistoryApi(page, {
      deferredHistoryWindow: { preset: "all_history", vehicleId: 2, started, release: new Promise<void>((resolve) => { release = resolve; }) },
    });
    await localTiles(page);
    await page.goto("/trips");
    await page.getByRole("textbox", { name: "Vehicle", exact: true }).click();
    await page.getByRole("option", { name: "SYNTHETIC Boreal", exact: true }).click();
    await expect(page).toHaveURL(/vehicle=2/);
    await page.getByRole("button", { name: "All history", exact: true }).click();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect.poll(() => started.count).toBe(1);
    await page.getByRole("textbox", { name: "Vehicle", exact: true }).click();
    await page.getByRole("option", { name: "SYNTHETIC Atlas", exact: true }).click();
    await expect(page).toHaveURL(/vehicle=1/);
    release();
    await expect(page).toHaveURL(/vehicle=1/);
    await expect(page).not.toHaveURL(/preset=all_history/);
  });

  test("uses custom calendar dates only after Apply and retains the summary when trajectory fails", async ({ page }) => {
    await mockHistoryApi(page, { trajectoryFailure: true });
    await localTiles(page);
    await page.goto("/trips");
    await expect(page).toHaveURL(/start=.*end=/);
    const picker = page.getByLabel("Date range", { exact: true });
    await picker.click();
    if (test.info().project.name === "mobile") await expect(page.getByRole("dialog")).toBeVisible();
    else await expect(page.locator('[data-dates-dropdown="true"]')).toBeVisible();
    const previousMonth = page.locator("button.mantine-DatePickerInput-calendarHeaderControl").first();
    await expect(previousMonth).toHaveAttribute("data-direction", "previous");
    await previousMonth.click();
    await page.getByRole("button", { name: "Change to month view" }).click();
    await page.getByRole("button", { name: "Change to year view" }).click();
    await page.getByRole("button", { name: "Previous decade" }).click();
    await page.getByRole("button", { name: "2019", exact: true }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Custom", exact: true }).click();
    await expect(page).toHaveURL(/start=.*end=/);
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect(page).toHaveURL(/start=2026-01-01T00%3A00%3A00Z/);
    await page.locator('a[href^="/trips/1"]').click();
    await expect(page.getByText(/12\.5 km/)).toBeVisible();
    await expect(page.getByText("The route map could not be loaded. The trip summary is still available.")).toBeVisible();
  });

  test("keeps the route and summary visible when map tiles fail", async ({ page }) => {
    const attempts = { count: 0 };
    await mockHistoryApi(page);
    await localTiles(page, true, attempts);
    const errors: Error[] = [];
    page.on("pageerror", (error) => errors.push(error));
    await page.goto("/trips/1");
    await expect(page.getByText(/12\.5 km/)).toBeVisible();
    await expect(page.getByLabel("Trip route map")).toBeVisible();
    await expect(page.locator("path.leaflet-interactive")).toHaveCount(2);
    await expect(page.getByText("The route map could not be loaded. The trip summary is still available.")).toBeVisible();
    await expect.poll(() => attempts.count).toBeGreaterThan(0);
    expect(errors).toEqual([]);
  });

  test("does not list-query an empty all-history window", async ({ page }) => {
    const lists = await mockHistoryApi(page, { empty: true });
    await localTiles(page);
    await page.goto("/trips");
    await expect(page.getByText("No records match this window.", { exact: true })).toBeVisible();
    const before = lists.length;
    await page.getByRole("button", { name: "All history", exact: true }).click();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect(page.getByText("No records match this window.", { exact: true })).toBeVisible();
    expect(lists).toHaveLength(before);
  });

  test("shows empty history and gates it when settings cannot load", async ({ page }) => {
    await mockHistoryApi(page, { empty: true });
    await localTiles(page);
    await page.goto("/trips");
    await expect(page.getByText("No records match this window.", { exact: true })).toBeVisible();
    const failed = await page.context().newPage();
    const lists = await mockHistoryApi(failed, { settingsFailure: true });
    await localTiles(failed);
    await failed.goto("/trips");
    await expect(failed.getByText("We could not load history. Please try again.")).toBeVisible();
    expect(lists.filter((request) => request.pathname.endsWith("/trips"))).toHaveLength(0);
    await failed.close();
  });

  test("loads saved Chinese preferences before direct vehicle navigation and reload", async ({ page }) => {
    const settingsRequests = { count: 0 };
    await mockHistoryApi(page, { language: "zh", settingsRequests });
    await localTiles(page);
    await page.goto("/vehicles");
    await expect(page.getByRole("heading", { name: "车辆", exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByText("SYNTHETIC Atlas", { exact: true })).toBeVisible();
    const navigation = page.getByRole("navigation", { name: "主导航", exact: true });
    await expect(navigation.getByRole("link", { name: "行程", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "充电", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "车辆", exact: true })).toHaveCount(0);
    await expect.poll(() => settingsRequests.count).toBe(1);
    await page.reload();
    await expect(page.getByRole("heading", { name: "车辆", exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByText("SYNTHETIC Atlas", { exact: true })).toBeVisible();
    await expect.poll(() => settingsRequests.count).toBe(2);
  });

  test("gates vehicles on settings failure and retries preferences before loading", async ({ page }) => {
    const settingsFailuresRemaining = { count: 1 };
    const vehicleRequests = { count: 0 };
    await mockHistoryApi(page, { settingsFailuresRemaining, vehicleRequests });
    await localTiles(page);
    await page.goto("/vehicles");
    await expect(page.getByText("We could not load history. Please try again.")).toBeVisible();
    expect(vehicleRequests.count).toBe(0);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("main").getByText("SYNTHETIC Atlas", { exact: true })).toBeVisible();
    expect(vehicleRequests.count).toBe(1);
  });

  test("mobile navigation exposes only implemented primary routes", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile-only navigation assertion");
    await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
    await expect(navigation.getByRole("link", { name: "Trips", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Charges", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link")).toHaveCount(2);
  });

  test("uses the phone header and safe-area bottom bar for shared selection", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile shell assertion");
    await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    await expect(page.getByRole("textbox", { name: "Select vehicle", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Settings", exact: true })).toBeVisible();
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
    await expect(navigation).toBeVisible();
    await expect(navigation).toHaveCSS("padding-bottom", "0px");
    await navigation.getByRole("link", { name: "Charges", exact: true }).click();
    await expect(page).toHaveURL(/\/charges\?vehicle=1/);
  });

  test("uses the sidebar at tablet and desktop widths", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop shell assertion");
    await mockHistoryApi(page);
    await localTiles(page);
    await page.setViewportSize({ width: 768, height: 900 });
    await page.goto("/trips");
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
    await expect(navigation).toBeVisible();
    await expect(navigation.getByRole("link")).toHaveCount(2);
    await expect(page.getByRole("textbox", { name: "Select vehicle", exact: true })).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(navigation).toBeVisible();
  });

  test("keeps a fixed scoped window across navigation and establishes the detail owner", async ({ page }) => {
    const lists = await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips?vehicle=2&start=2026-01-01T00%3A00%3A00Z&end=2026-02-01T00%3A00%3A00Z");
    await expect.poll(() => lists.at(-1)?.searchParams.get("vehicle_id")).toBe("2");
    await page.getByRole("navigation", { name: "Primary navigation", exact: true })
      .getByRole("link", { name: "Charges", exact: true }).click();
    await expect(page).toHaveURL(/vehicle=2.*start=2026-01-01T00/);
    await page.goto("/trips/1");
    await expect(page.getByText(/12\.5 km/)).toBeVisible();
    await expect(page).toHaveURL(/vehicle=1/);
  });

  test("returns to the triggering list when a direct detail resolves to another vehicle", async ({ page }) => {
    await mockHistoryApi(page);
    await localTiles(page);
    const listUrl = "/trips?vehicle=1&start=2026-01-01T00%3A00%3A00Z&end=2026-02-01T00%3A00%3A00Z";
    await page.goto(listUrl);
    await expect(page.getByText("12.5 km", { exact: true })).toBeVisible();
    await page.goto("/trips/2");
    await expect(page.getByText(/22 km/)).toBeVisible();
    await expect(page).toHaveURL(/\/trips\/2\?vehicle=2.*start=2026-08-14T00/);
    await page.goBack();
    await expect(page).toHaveURL(/\/trips\?vehicle=1.*start=2026-01-01T00/);
    await expect(page.getByText("12.5 km", { exact: true })).toBeVisible();
    await page.goForward();
    await expect(page).toHaveURL(/\/trips\/2\?vehicle=2.*start=2026-08-14T00/);
    await expect(page.getByText(/22 km/)).toBeVisible();
  });

  test("shows explicit invalid and empty vehicle states without substituting a car", async ({ page }) => {
    await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips?vehicle=99");
    await expect(page.getByText("This vehicle is not available. Choose an available vehicle to continue.")).toBeVisible();
    await mockHistoryApi(page, { zeroCars: true });
    await page.goto("/trips");
    await expect(page.getByText("No vehicles are available.", { exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByRole("link", { name: "Settings", exact: true })).toBeVisible();
  });

  test("real synthetic PostgreSQL flow covers vehicles, records, map, and logout", async ({ page }) => {
    test.skip(!realSql, "Set MATESCOPE_E2E_REAL_PG=1 with a loopback isolated synthetic instance");
    test.setTimeout(150_000);
    await localTiles(page);
    await signIn(page);
    await saveSyntheticPostgres(page);
    await finishOnboarding(page);
    await page.goto("/vehicles");
    await expect(page.getByText("SYNTHETIC Atlas", { exact: true })).toBeVisible();
    await page.goto("/trips");
    await page.locator('a[href^="/trips/1"]').click();
    await expect(page.getByText(/12\.5 km/)).toBeVisible();
    await expect(page.getByLabel("Trip route map")).toBeVisible();
    await expect(page.locator("path.leaflet-interactive")).toHaveCount(2);
    await expect(page.getByText("Showing a simplified route from 4950 recorded positions.")).toBeVisible();
    await page.goto("/charges");
    await page.locator('a[href^="/charges/1"]').click();
    await expect(page.getByText(/22\.5 kWh/)).toBeVisible();
    await page.goto("/settings");
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  });
});

test.describe("T18 compact trip list", () => {
  test("groups local dates, uses the complete-period summary, and retains 50-row cursor paging", async ({ page }) => {
    const summaryRequests = { requests: [] as URL[] };
    const lists = await mockHistoryApi(page, { tripSummaryRequests: summaryRequests });
    await page.goto("/trips");
    await expect(page.locator('[aria-label="Selected-period trip summary"]')).toBeVisible();
    await expect(page.getByText("456.7 km", { exact: false })).toBeVisible();
    await expect(page.getByText("1 record not ended is excluded from ended-record totals.", { exact: true })).toBeVisible();
    await expect.poll(() => lists.at(-1)?.searchParams.get("limit")).toBe("50");
    await expect.poll(() => summaryRequests.requests.at(-1)?.searchParams.get("start")).toBe(lists.at(-1)?.searchParams.get("start"));
    await expect(page.getByText("Sunday, September 13, 2026", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    expect(summaryRequests.requests).toHaveLength(1);
  });

  test("keeps trip rows available when only the summary fails", async ({ page }) => {
    await mockHistoryApi(page, { tripSummaryPermissionFailure: true });
    await page.goto("/trips");
    await expect(page.getByText("We could not load history. Please try again.", { exact: true })).toBeVisible();
    await expect(page.getByText("The PostgreSQL account lacks required permissions.", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "View details", exact: true }).first()).toBeVisible();
  });

  test("always shows trip metric coverage and English unavailable reasons", async ({ page }) => {
    await mockHistoryApi(page, { tripSummaryResponse: {
      distance_km: null,
      duration_min: null,
      estimated_energy_kwh: null,
      distance_coverage: { applicable_count: 0, valid_count: 0, reason: "no_ended_records" },
      duration_coverage: { applicable_count: 0, valid_count: 0, reason: "no_valid_values" },
      estimated_energy_coverage: { applicable_count: 3, valid_count: 0, reason: "unavailable" },
    } });
    await page.goto("/trips");
    const summary = page.locator('[aria-label="Selected-period trip summary"]');
    await expect(summary.getByText("0 of 0 applicable records", { exact: true })).toHaveCount(2);
    await expect(summary.getByText("0 of 3 applicable records", { exact: true })).toBeVisible();
    await expect(summary.getByText("No ended records are available for this metric.", { exact: true })).toBeVisible();
    await expect(summary.getByText("No valid values are available for this metric.", { exact: true })).toBeVisible();
    await expect(summary.getByText("This metric is unavailable.", { exact: true })).toBeVisible();
    await expect(summary.getByText("—", { exact: true })).toHaveCount(3);
  });

  test("keeps full and partial coverage visible with Chinese reasons", async ({ page }) => {
    await mockHistoryApi(page, { language: "zh", tripSummaryResponse: {
      distance_coverage: { applicable_count: 2, valid_count: 2, reason: null },
      duration_min: null,
      duration_coverage: { applicable_count: 2, valid_count: 0, reason: "no_valid_values" },
      estimated_energy_kwh: null,
      estimated_energy_coverage: { applicable_count: 2, valid_count: 0, reason: "no_valid_values" },
    } });
    await page.goto("/trips");
    const summary = page.locator('[aria-label="所选期间行程摘要"]');
    await expect(summary.getByText("2 条适用记录中的 2 条", { exact: true })).toBeVisible();
    await expect(summary.getByText("2 条适用记录中的 0 条", { exact: true })).toHaveCount(2);
    await expect(summary.getByText("此指标没有有效值可用。", { exact: true })).toHaveCount(2);
  });
});

test.describe("T19 compact charge list", () => {
  test("groups local dates, keeps the complete-period summary separate from 50-row pages, and distinguishes free from unknown cost", async ({ page }) => {
    const summaryRequests = { requests: [] as URL[] };
    const lists = await mockHistoryApi(page, { chargeSummaryRequests: summaryRequests });
    await page.goto("/charges");
    const summary = page.locator('[aria-label="Selected-period charge summary"]');
    await expect(summary).toBeVisible();
    await expect(summary.getByText("Currency not configured", { exact: true })).toBeVisible();
    await expect(summary.getByText("49 of 51 applicable records", { exact: true })).toBeVisible();
    await expect(page.getByText("Sunday, September 13, 2026", { exact: true })).toBeVisible();
    await expect(page.getByText("0.00", { exact: true })).toBeVisible();
    await expect(page.getByText("12.50", { exact: true })).toBeVisible();
    await expect(page.getByText("Unknown", { exact: true })).toBeVisible();
    await expect(page.getByText("Record not ended", { exact: true })).toBeVisible();
    await expect.poll(() => lists.at(-1)?.searchParams.get("limit")).toBe("50");
    await expect.poll(() => summaryRequests.requests.at(-1)?.searchParams.get("start")).toBe(lists.at(-1)?.searchParams.get("start"));
    await page.getByRole("button", { name: "Next page", exact: true }).click();
    await expect.poll(() => lists.at(-1)?.searchParams.get("cursor")).toBe("cursor-1");
    expect(summaryRequests.requests).toHaveLength(1);
  });

  test("formats configured currency without converting source costs and keeps rows when only the summary fails", async ({ page }) => {
    await mockHistoryApi(page, { displayCurrency: "EUR", chargeSummaryPermissionFailure: true, chargeItems: [{ id: 9, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5, place: "SYNTHETIC Long Place", start_battery_level: 20, end_battery_level: 60, cost: 12.345 }] });
    await page.goto("/charges");
    await expect(page.getByText("The PostgreSQL account lacks required permissions.", { exact: true })).toBeVisible();
    await expect(page.getByText(/€12\.35/)).toBeVisible();
    await expect(page.getByRole("link", { name: "View details", exact: true })).toBeVisible();
  });

  test("renders unavailable charge coverage and has no horizontal phone overflow for long locations", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "phone overflow assertion");
    await mockHistoryApi(page, { chargeSummaryResponse: {
      energy_added_kwh: null, duration_min: null,
      energy_added_coverage: { applicable_count: 0, valid_count: 0, reason: "no_ended_records" },
      duration_coverage: { applicable_count: 2, valid_count: 0, reason: "no_valid_values" },
      cost_coverage: { applicable_count: 2, valid_count: 0, reason: "unavailable" },
    }, chargeItems: [{ id: 4, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5, place: "SYNTHETIC extraordinarily long charging location name that must be clipped on a narrow phone screen", start_battery_level: 20, end_battery_level: 60, cost: null }] });
    await page.goto("/charges");
    const summary = page.locator('[aria-label="Selected-period charge summary"]');
    await expect(summary.getByText("No ended records are available for this metric.", { exact: true })).toBeVisible();
    await expect(summary.getByText("No valid values are available for this metric.", { exact: true })).toBeVisible();
    await expect(summary.getByText("This metric is unavailable.", { exact: true })).toBeVisible();
    await expect(page.locator(".charge-place")).toHaveCSS("text-overflow", "ellipsis");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});

test.describe("T10 visual foundations", () => {
  test("follows explicit light and dark themes with readable metric states", async ({ page }) => {
    await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    await expect(page.locator(".metric-value-empty").first()).toBeVisible();
    const contrast = (foreground: string, background: string) => {
      const parse = (value: string) => {
        const channels = value.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
        return channels.slice(0, 3).map((channel) => {
          const normalized = channel / 255;
          return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
        });
      };
      const luminance = (value: string) => {
        const [red, green, blue] = parse(value);
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      };
      const foregroundLuminance = luminance(foreground);
      const backgroundLuminance = luminance(background);
      return (Math.max(foregroundLuminance, backgroundLuminance) + 0.05)
        / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05);
    };
    for (const scheme of ["light", "dark"] as const) {
      await page.evaluate((value) => document.documentElement.setAttribute("data-mantine-color-scheme", value), scheme);
      const styles = await page.locator("body").evaluate((element) => {
        const body = getComputedStyle(element);
        const empty = getComputedStyle(document.querySelector(".metric-value-empty")!);
        return { background: body.backgroundColor, empty: empty.color };
      });
      expect(contrast(styles.empty, styles.background)).toBeGreaterThanOrEqual(4.5);
      expect(styles.background).toBe(scheme === "light" ? "rgb(248, 249, 250)" : "rgb(20, 21, 23)");
    }
  });

  test("keeps sidebar navigation touchable and keyboard focus visible", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop sidebar assertion");
    await mockHistoryApi(page);
    await localTiles(page);
    await page.goto("/trips");
    const items = page.getByRole("navigation", { name: "Primary navigation", exact: true }).getByRole("link");
    await expect(items).toHaveCount(2);
    for (let index = 0; index < await items.count(); index += 1) {
      const box = await items.nth(index).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await items.first().focus();
    const outline = await items.first().evaluate((element) => getComputedStyle(element).outlineStyle);
    expect(outline).toBe("solid");
  });
});

test.describe("T12 display preferences", () => {
  test("saves and restores display controls without changing language or timezone", async ({ page }) => {
    const preferenceBodies = { bodies: [] as Record<string, unknown>[] };
    await mockHistoryApi(page, { preferenceBodies });
    await page.goto("/settings");
    const preferences = page.getByRole("region", { name: "Preferences", exact: true });
    await expect(preferences.getByLabel(/^Display timezone(?:\s*\*)?$/)).toHaveValue("Europe/Amsterdam");

    await preferences.getByLabel("Range basis", { exact: true }).click();
    await page.getByRole("option", { name: "Ideal range", exact: true }).click();
    await preferences.getByLabel("Display currency", { exact: true }).click();
    await page.getByRole("option", { name: "EUR", exact: true }).click();
    await preferences.getByLabel("Appearance", { exact: true }).click();
    await page.getByRole("option", { name: "Dark", exact: true }).click();
    await preferences.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => preferenceBodies.bodies.length).toBe(1);
    expect(preferenceBodies.bodies[0]).toMatchObject({
      language: "en",
      timezone: "Europe/Amsterdam",
      range_basis: "ideal",
      display_currency: "EUR",
    });
    await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", "dark");

    await page.reload();
    const reloaded = page.getByRole("region", { name: "Preferences", exact: true });
    await expect(reloaded.getByLabel("Language", { exact: true })).toHaveValue("English");
    await expect(reloaded.getByLabel("Range basis", { exact: true })).toHaveValue("Ideal range");
    await expect(reloaded.getByLabel("Display currency", { exact: true })).toHaveValue("EUR");
    await expect(reloaded.getByLabel(/^Display timezone(?:\s*\*)?$/)).toHaveValue("Europe/Amsterdam");
    await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", "dark");

    await reloaded.getByLabel("Appearance", { exact: true }).click();
    await page.getByRole("option", { name: "Light", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", "light");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", "light");
    await expect(page.getByRole("region", { name: "Preferences", exact: true }).getByLabel("Appearance", { exact: true })).toHaveValue("Light");

    const lightReloaded = page.getByRole("region", { name: "Preferences", exact: true });
    await lightReloaded.getByRole("button", { name: "Clear display currency", exact: true }).click();
    await lightReloaded.getByLabel("Appearance", { exact: true }).click();
    await page.getByRole("option", { name: "System default", exact: true }).click();
    await expect(lightReloaded.getByLabel("Appearance", { exact: true })).toHaveValue("System default");
    await lightReloaded.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => preferenceBodies.bodies.length).toBe(2);
    expect(preferenceBodies.bodies[1]).toMatchObject({
      language: "en",
      timezone: "Europe/Amsterdam",
      range_basis: "ideal",
      display_currency: null,
    });
    await expect(lightReloaded.getByText("Choose a currency only when existing records use that same currency. MateScope does not convert amounts.", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", "light");
    await expect(page.getByRole("region", { name: "Preferences", exact: true }).getByLabel("Appearance", { exact: true })).toHaveValue("System default");
  });
});
