import { expect, test, type Page } from "@playwright/test";

const baseUrl = "http://127.0.0.1:49234";
const tilePng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

type FixtureOptions = { language?: "en" | "zh" };

const points = [
  { time: "2026-09-12T20:00:00Z", value: 20, mean: 20, min: 19, max: 21, discontinuity: false },
  { time: "2026-09-12T20:30:00Z", value: 60, mean: 60, min: 59, max: 61, discontinuity: false },
];

function series(name: string, unit: string, aggregation: "last" | "mean_min_max" = "mean_min_max") {
  return {
    name,
    unit,
    start: points[0].time,
    end: points[1].time,
    sample_count: 2,
    bucket_count: 2,
    aggregation,
    capability: { available: true, reason: null },
    points,
  };
}

async function installHistoryFixture(page: Page, { language = "en" }: FixtureOptions = {}) {
  const settings = {
    preferences: { language, timezone: "Europe/Amsterdam", tile_url: "https://tiles.example/{z}/{x}/{y}.png", range_basis: "rated", display_currency: null, saved: true },
    postgresql: { password_set: false, version: 1, status: "success", test_available: true, test_result: null, host: "postgres", username: "reader", enabled: true, skipped: false, port: 5432, database: "teslamate_synthetic", sslmode: "disable" },
    mqtt: { password_set: false, version: 0, status: "disabled", test_available: false, test_result: null, host: "", username: "", enabled: false, skipped: true, port: 1883, tls: false, verify_tls: true, topic_prefix: "" },
    smtp: { password_set: false, version: 0, status: "disabled", test_available: false, test_result: null, host: "", username: "", enabled: false, skipped: true, port: 587, tls_mode: "starttls", verify_tls: true, sender: "" },
    onboarding: { step: "review", completed: true },
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = new URL(route.request().url());
    const path = request.pathname;
    if (path.endsWith("/setup/status")) return route.fulfill({ json: { administrator_exists: true } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: "m1-t34" } });
    if (path.endsWith("/auth/csrf")) return route.fulfill({ json: { csrf_token: "m1-t34-csrf" } });
    if (path.endsWith("/settings/preferences")) return route.fulfill({ json: settings });
    if (path.endsWith("/settings")) return route.fulfill({ json: settings });
    if (path.endsWith("/vehicles")) return route.fulfill({ json: { items: [{ id: 1, name: "SYNTHETIC Atlas", model: "Model 3" }] } });
    if (/\/vehicles\/\d+\/history-window$/.test(path)) return route.fulfill({ json: { preset: request.searchParams.get("preset") ?? "last_30_days", timezone: "Europe/Amsterdam", start: "2026-08-14T00:00:00Z", end: "2026-09-13T00:00:00Z", is_empty: false } });
    if (/\/vehicles\/\d+\/snapshot$/.test(path)) return route.fulfill({ json: { vehicle_id: 1, battery_level: 72, battery_level_at: "2026-09-14T09:00:00Z", range_km: 310.5, range_at: "2026-09-14T08:00:00Z", odometer_km: 12345.6, odometer_at: "2026-09-13T20:00:00Z", capability: { available: true, reason: null } } });
    if (/\/vehicles\/\d+\/trip-summary$/.test(path)) return route.fulfill({ json: { vehicle_id: 1, start: request.searchParams.get("start"), end: request.searchParams.get("end"), total_count: 2, ended_count: 1, not_ended_count: 1, distance_km: 12.5, duration_min: 60, estimated_energy_kwh: 2.4, estimated_average_consumption_wh_per_km: 192, distance_coverage: { applicable_count: 1, valid_count: 1 }, duration_coverage: { applicable_count: 1, valid_count: 1 }, estimated_energy_coverage: { applicable_count: 1, valid_count: 1 }, estimated_average_consumption_coverage: { applicable_count: 1, valid_count: 1 }, estimate_capability: { available: true, reason: null } } });
    if (/\/vehicles\/\d+\/charge-summary$/.test(path)) return route.fulfill({ json: { vehicle_id: 1, start: request.searchParams.get("start"), end: request.searchParams.get("end"), total_count: 1, ended_count: 1, not_ended_count: 0, energy_added_kwh: 22.5, duration_min: 45, cost: null, currency: null, energy_added_coverage: { applicable_count: 1, valid_count: 1 }, duration_coverage: { applicable_count: 1, valid_count: 1 }, cost_coverage: { applicable_count: 1, valid_count: 0 }, cost_capability: { available: true, reason: null } } });
    if (path.endsWith("/trips")) return route.fulfill({ json: { items: [{ id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T21:00:00Z", duration_min: 60, distance_km: 12.5, speed_max_kmh: 72, start_place: "SYNTHETIC extraordinarily long trip origin that must never displace the distance", end_place: "SYNTHETIC extraordinarily long trip destination that must never displace the duration", start_battery_level: 82, end_battery_level: 68, estimated_energy_kwh: 2.4 }] , next_cursor: null, start: request.searchParams.get("start"), end: request.searchParams.get("end") } });
    if (path.endsWith("/charges")) return route.fulfill({ json: { items: [{ id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5, place: "SYNTHETIC extraordinarily long charging location that must never displace the energy added", start_battery_level: 20, end_battery_level: 60, cost: null }], next_cursor: null, start: request.searchParams.get("start"), end: request.searchParams.get("end") } });
    if (/\/trips\/1$/.test(path)) return route.fulfill({ json: { id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T21:00:00Z", duration_min: 60, distance_km: 12.5, speed_max_kmh: 72, start_place: "SYNTHETIC extraordinarily long trip origin for the core detail", end_place: "SYNTHETIC extraordinarily long trip destination for the core detail", start_battery_level: 82, end_battery_level: 68, estimated_energy_kwh: 2.4, estimated_average_consumption_wh_per_km: 192 } });
    if (/\/charges\/1$/.test(path)) return route.fulfill({ json: { id: 1, vehicle_id: 1, start: "2026-09-12T20:00:00Z", end: "2026-09-12T20:45:00Z", duration_min: 45, energy_added_kwh: 22.5, place: "SYNTHETIC extraordinarily long charging location for the core detail", start_battery_level: 20, end_battery_level: 60, recorded_energy_used_kwh: 18.1, cost: null } });
    if (path.endsWith("/trips/1/series")) return route.fulfill({ json: { trip_id: 1, capability: { available: true, reason: null }, series: [series("speed", "km/h"), series("power", "kW"), series("battery", "%", "last"), series("inside_temperature", "°C"), series("outside_temperature", "°C"), series("elevation", "m")] } });
    if (path.endsWith("/charges/1/series")) return route.fulfill({ json: { charge_id: 1, capability: { available: true, reason: null }, series: [series("power", "kW"), series("battery", "%", "last"), series("outside_temperature", "°C")] } });
    if (path.endsWith("/trajectory")) return route.fulfill({ json: { trip_id: 1, points: [], simplified: false, total_points: 0 } });
    return route.fulfill({ status: 404, json: { detail: "Unexpected M1-T34 fixture request" } });
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === "https://tiles.example") return route.fulfill({ status: 200, contentType: "image/png", body: tilePng });
    if (url.origin === baseUrl) return route.fallback();
    return route.abort();
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function expectFigures(page: Page, titles: string[]) {
  for (const title of titles) {
    const chart = page.getByRole("figure", { name: title, exact: true });
    await expect(chart).toBeVisible();
    await expect(chart.locator(".time-series-description")).toBeVisible();
  }
}

async function expectTripDetail(page: Page, language: "en" | "zh") {
  const labels = language === "zh"
    ? { more: "更多数据", charts: ["速度和功率", "电量", "车内外温度", "海拔"], distance: /距离:\s*12\.5 km/, energy: /估算能耗:\s*2\.4 kWh/ }
    : { more: "More data", charts: ["Speed and power", "Battery level", "Inside and outside temperature", "Elevation"], distance: /Distance:\s*12\.5 km/, energy: /Estimated energy:\s*2\.4 kWh/ };
  await expect(page.getByText(/SYNTHETIC extraordinarily long trip origin for the core detail/)).toBeVisible();
  await expect(page.getByText(labels.distance)).toBeVisible();
  await expect(page.getByText(labels.energy)).toBeVisible();
  await expectFigures(page, labels.charts.slice(0, 2));
  await page.getByText(labels.more, { exact: true }).click();
  await expectFigures(page, labels.charts);
  await expectNoHorizontalOverflow(page);
}

async function expectChargeDetail(page: Page, language: "en" | "zh") {
  const labels = language === "zh"
    ? { more: "更多数据", charts: ["充电功率和电量", "车外温度"], energy: /增加电量\s*22\.5 kWh/, used: /记录的已用电量\s*18\.1 kWh/ }
    : { more: "More data", charts: ["Charging power and battery level", "Outdoor temperature"], energy: /Energy added\s*22\.5 kWh/, used: /Recorded energy used\s*18\.1 kWh/ };
  await expect(page.getByText(/SYNTHETIC extraordinarily long charging location for the core detail/)).toBeVisible();
  await expect(page.getByText(labels.energy)).toBeVisible();
  await expect(page.getByText(labels.used)).toBeVisible();
  await expectFigures(page, labels.charts.slice(0, 1));
  await page.getByText(labels.more, { exact: true }).click();
  await expectFigures(page, labels.charts);
  await expectNoHorizontalOverflow(page);
}

test.describe("M1-T34 responsive and accessibility integration", () => {
  test("Chromium covers all required widths, languages, themes, main and detail pages, charts, and long locations", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "chromium", "Chromium is the complete responsive flow.");
    for (const [width, language, scheme] of [[360, "en", "light"], [390, "zh", "dark"], [768, "en", "dark"], [1280, "zh", "light"], [1440, "en", "light"]] as const) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await installHistoryFixture(page, { language });
      await page.goto("/overview?vehicle=1&start=2026-09-01T00%3A00%3A00Z&end=2026-10-01T00%3A00%3A00Z");
      await expect(page.locator("html")).toHaveAttribute("lang", language === "zh" ? "zh" : "en");
      await expect(page.locator("html")).toHaveAttribute("data-mantine-color-scheme", scheme);
      await expect(page.getByRole("main")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      for (const path of ["/trips?vehicle=1", "/charges?vehicle=1"]) {
        await page.goto(path);
        await expect(page.getByRole("main")).toBeVisible();
        await expectNoHorizontalOverflow(page);
      }
      await page.goto("/trips/1?vehicle=1");
      await expectTripDetail(page, language);
      await page.goto("/charges/1?vehicle=1");
      await expectChargeDetail(page, language);
      if (width < 768) {
        const navigation = page.locator(".shell-bottom-bar");
        await expect(navigation).toBeVisible();
        for (const item of await navigation.getByRole("link").all()) expect((await item.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      }
      await page.unroute("**/api/v1/**");
      await page.unroute("**/*");
    }
  });

  test("Chromium mobile simulation uses touch input and keeps the safe-area layout clear", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "chromium-touch", "This flow needs the explicit touch context.");
    await installHistoryFixture(page);
    await page.goto("/trips?vehicle=1");
    expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(true);
    await page.getByLabel("Date range", { exact: true }).tap();
    await expect(page.getByRole("button", { name: "All history", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    const bottomBar = page.locator(".shell-bottom-bar");
    await expect(bottomBar).toBeVisible();
    await expect.poll(() => page.evaluate(() => {
      const css = Array.from(document.styleSheets).flatMap((sheet) => Array.from(sheet.cssRules)).map((rule) => rule.cssText).join(" ");
      const bar = document.querySelector(".shell-bottom-bar");
      const main = document.querySelector(".mantine-AppShell-main");
      return css.includes(".shell-bottom-bar") && css.includes("padding-bottom: env(safe-area-inset-bottom)")
        && css.includes("padding-bottom: calc(64px + env(safe-area-inset-bottom))")
        && bar instanceof HTMLElement && main instanceof HTMLElement
        && bar.getBoundingClientRect().bottom <= window.innerHeight
        && Number.parseFloat(getComputedStyle(main).paddingBottom) >= 64;
    })).toBe(true);
    await bottomBar.getByRole("link", { name: "Charges", exact: true }).tap();
    await expect(page).toHaveURL(/\/charges/);
    await page.getByRole("link", { name: "View details", exact: true }).tap();
    await expect(page).toHaveURL(/\/charges\/1/);
    await expectChargeDetail(page, "en");
  });

  test("Chromium supports keyboard picker, navigation, records, and chart descriptions", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "chromium", "Chromium is the complete keyboard flow.");
    await page.setViewportSize({ width: 390, height: 844 });
    await installHistoryFixture(page);
    await page.goto("/trips?vehicle=1");
    const picker = page.getByLabel("Date range", { exact: true });
    await picker.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "All history", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    const record = page.locator('[data-history-record-id="1"]');
    await record.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/trips\/1/);
    const chart = page.getByRole("figure", { name: "Speed and power", exact: true });
    await expect(chart).toBeVisible();
    await expect(chart.getByText(/Values use km\/h/)).toBeVisible();
    await chart.getByText("Chart data table", { exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(chart.locator("table")).toBeVisible();
    await page.getByRole("link", { name: "Charges", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/charges/);
    await expectNoHorizontalOverflow(page);
  });

  test("WebKit supplements picker, navigation, and core detail", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "webkit", "This flow is executed in native Playwright WebKit.");
    await page.setViewportSize({ width: 390, height: 844 });
    await installHistoryFixture(page, { language: "zh" });
    await page.goto("/trips?vehicle=1");
    await page.getByLabel("日期范围", { exact: true }).click();
    await expect(page.getByRole("button", { name: "全部历史", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("link", { name: "查看详情", exact: true }).click();
    await expect(page).toHaveURL(/\/trips\/1/);
    await expect(page.getByRole("figure", { name: "速度和功率", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "充电", exact: true }).click();
    await expect(page).toHaveURL(/\/charges/);
    await page.getByRole("link", { name: "查看详情", exact: true }).click();
    await expect(page.getByRole("heading", { name: "摘要", exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
