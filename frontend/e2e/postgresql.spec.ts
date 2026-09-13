import { expect, test } from "@playwright/test";

const testResult = {
  version: 1,
  status: "success",
  code: "ok",
  tested_at: "2026-09-14T10:00:00Z",
  persisted: true,
};

function settings(result: typeof testResult | null = null) {
  return {
    preferences: {
      language: "en",
      timezone: "Europe/Amsterdam",
      tile_url: "https://tiles.example/{z}/{x}/{y}.png",
      saved: true,
    },
    postgresql: {
      password_set: true,
      version: 1,
      status: result ? result.status : "unverified",
      test_available: true,
      test_result: result,
      host: "postgres",
      username: "reader",
      enabled: true,
      skipped: false,
      port: 5432,
      database: "teslamate",
      sslmode: "prefer",
    },
    mqtt: {
      password_set: false,
      version: 0,
      status: "disabled",
      test_available: false,
      host: "",
      username: "",
      enabled: false,
      skipped: true,
      port: 1883,
      tls: false,
      verify_tls: true,
      topic_prefix: "teslamate",
    },
    smtp: {
      password_set: false,
      version: 0,
      status: "disabled",
      test_available: false,
      host: "",
      username: "",
      enabled: false,
      skipped: true,
      port: 587,
      tls_mode: "starttls",
      verify_tls: true,
      sender: "",
    },
    onboarding: { step: "review", completed: true },
  };
}

async function mockApi(
  page: import("@playwright/test").Page,
  result: typeof testResult | null = null,
  bumpVersionOnReload = false,
  failRefreshAfterTest = false,
  initialResult: typeof testResult | null = null,
  resultOnReload: typeof testResult | null = null,
) {
  let current = settings(initialResult);
  let testCalls = 0;
  let settingsReads = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: "admin" } });
    if (path.endsWith("/auth/csrf")) return route.fulfill({ json: { csrf_token: "csrf" } });
    if (path.endsWith("/settings") && request.method() === "GET") {
      settingsReads += 1;
      if (failRefreshAfterTest && testCalls > 0) {
        return route.fulfill({
          status: 503,
          json: { detail: { code: "database_unavailable" } },
        });
      }
      if (bumpVersionOnReload && testCalls > 0 && settingsReads > 2) {
        current = settings(null);
        current.postgresql.version = 2;
      }
      if (resultOnReload && testCalls > 0 && settingsReads > 2) {
        current = settings(resultOnReload);
      }
      return route.fulfill({ json: current });
    }
    if (path.endsWith("/settings/postgresql/test")) {
      testCalls += 1;
      if (result?.persisted) {
        current = settings(result);
      } else if (result) {
        current = settings(null);
        current.postgresql.version = 2;
      }
      return route.fulfill({ json: result });
    }
    if (path.endsWith("/settings/postgresql") && request.method() === "PUT") {
      const body = request.postDataJSON() as {
        host?: string;
        username?: string;
        port?: number;
        enabled?: boolean;
        skipped?: boolean;
        database?: string;
        sslmode?: string;
      };
      current = settings(null);
      current.postgresql.host = body.host ?? current.postgresql.host;
      current.postgresql.username = body.username ?? current.postgresql.username;
      current.postgresql.port = body.port ?? current.postgresql.port;
      current.postgresql.enabled = body.enabled ?? current.postgresql.enabled;
      current.postgresql.skipped = body.skipped ?? current.postgresql.skipped;
      current.postgresql.database = body.database ?? current.postgresql.database;
      current.postgresql.sslmode = body.sslmode ?? current.postgresql.sslmode;
      return route.fulfill({ json: current });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected mock route" } });
  });
  return Object.assign(() => testCalls, { settingsReads: () => settingsReads });
}

test("tests PostgreSQL only after clicking the explicit test button", async ({ page }) => {
  const calls = await mockApi(page, testResult);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await expect(pg.getByRole("button", { name: "Test saved connection" })).toBeVisible();
  await expect.poll(calls).toBe(0);
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect(pg.getByText("Connection test succeeded.")).toBeVisible();
  await expect(pg.getByText("Connection is working.")).toBeVisible();
  expect(calls()).toBe(1);
});

test("disables testing unsaved settings and shows a translated failure", async ({ page }) => {
  const failure = { ...testResult, status: "failure", code: "invalid_credentials" } as const;
  const calls = await mockApi(page, failure);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await pg.getByLabel("Host", { exact: true }).fill("edited-postgres");
  await expect(pg.getByRole("button", { name: "Test saved connection" })).toBeDisabled();
  await expect(pg.getByText("Save your changes before testing the connection.")).toBeVisible();
  await pg.getByRole("button", { name: "Save", exact: true }).click();
  await expect(pg.getByRole("button", { name: "Test saved connection" })).toBeEnabled();
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect(pg.getByText("Connection test failed.")).toBeVisible();
  await expect(pg.getByText("The PostgreSQL username or password is incorrect.")).toBeVisible();
  expect(calls()).toBe(1);
});

test("keeps a configuration-changed test result as a failure", async ({ page }) => {
  const changed = {
    ...testResult,
    status: "failure",
    code: "configuration_changed",
    persisted: false,
  } as const;
  const calls = await mockApi(page, changed);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect(pg.getByText("Connection test failed.")).toBeVisible();
  await expect(
    pg.getByText("The configuration changed before this test completed."),
  ).toBeVisible();
  expect(calls()).toBe(1);
});

test("shows the latest same-version failure when the settings refresh fails", async ({ page }) => {
  const failure = {
    ...testResult,
    status: "failure",
    code: "invalid_credentials",
    tested_at: "2026-09-14T10:01:00Z",
  } as const;
  const calls = await mockApi(page, failure, false, true, testResult);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect.poll(calls).toBe(1);
  await expect(pg.getByText("Connection test failed.")).toBeVisible();
  await expect(pg.getByText("The PostgreSQL username or password is incorrect.")).toBeVisible();
  await expect(pg.getByText("Connection verified")).toHaveCount(0);
  await expect(pg.getByText("Connection test succeeded.")).toHaveCount(0);
  await expect(pg.getByText("Connection is working.")).toHaveCount(0);
});

test("shows a newer same-version saved result after a refresh", async ({ page }) => {
  const failure = {
    ...testResult,
    status: "failure",
    code: "invalid_credentials",
    tested_at: "2026-09-14T10:01:00Z",
  } as const;
  const newerSuccess = { ...testResult, tested_at: "2026-09-14T10:02:00Z" } as const;
  const calls = await mockApi(page, failure, false, false, testResult, newerSuccess);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect(pg.getByText("Connection test failed.")).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
  await expect.poll(calls.settingsReads).toBeGreaterThan(2);
  await expect(pg.getByText("Connection test succeeded.")).toBeVisible();
  await expect(pg.getByText("Connection test failed.")).toHaveCount(0);
});

test("does not show an old success after a newer settings version is loaded", async ({ page }) => {
  const calls = await mockApi(page, testResult, true);
  await page.goto("/settings");
  const pg = page.getByRole("region", { name: "PostgreSQL" });
  await pg.getByRole("button", { name: "Test saved connection" }).click();
  await expect(pg.getByText("Connection test succeeded.")).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
  await expect.poll(calls.settingsReads).toBeGreaterThan(2);
  await expect(pg.getByText("Connection test succeeded.")).toHaveCount(0);
});
