import { expect, test, type Page } from "@playwright/test";

const connectionStatus = {
  password_set: false,
  version: 0,
  status: "disabled",
  test_available: false,
};

function settings(step: "mqtt" | "review", mqttEnabled = true) {
  return {
    preferences: {
      language: "en",
      timezone: "UTC",
      tile_url: "https://tiles.example/{z}/{x}/{y}.png",
      saved: true,
    },
    postgresql: {
      ...connectionStatus,
      host: "postgres",
      username: "reader",
      enabled: true,
      skipped: false,
      port: 5432,
      database: "teslamate",
      sslmode: "prefer",
    },
    mqtt: {
      ...connectionStatus,
      host: "mqtt",
      username: "viewer",
      enabled: mqttEnabled,
      skipped: !mqttEnabled,
      port: 1883,
      tls: false,
      verify_tls: true,
      topic_prefix: "teslamate",
    },
    smtp: {
      ...connectionStatus,
      host: "smtp",
      username: "mailer",
      enabled: false,
      skipped: true,
      port: 587,
      tls_mode: "starttls",
      verify_tls: true,
      sender: "mate@example.test",
    },
    onboarding: { step, completed: step === "review" },
  };
}

async function mockApi(
  page: Page,
  handlers: {
    step: "mqtt" | "review";
    onboarding?: "error";
    mqtt?: (body: unknown) => void;
    logout?: "error";
  },
) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url()).pathname;
    if (url.endsWith("/auth/me")) {
      await route.fulfill({ json: { username: "admin" } });
    } else if (url.endsWith("/auth/csrf")) {
      await route.fulfill({ json: { csrf_token: "mock-csrf" } });
    } else if (url.endsWith("/settings") && request.method() === "GET") {
      await route.fulfill({ json: settings(handlers.step) });
    } else if (url.endsWith("/settings/onboarding")) {
      if (handlers.onboarding === "error") {
        await route.fulfill({
          status: 500,
          json: { detail: "transition failed" },
        });
      } else {
        await route.fulfill({ json: settings("review") });
      }
    } else if (url.endsWith("/settings/mqtt")) {
      handlers.mqtt?.(request.postDataJSON());
      await route.fulfill({ json: settings("mqtt", false) });
    } else if (url.endsWith("/auth/logout")) {
      await route.fulfill(
        handlers.logout === "error"
          ? { status: 500, json: { detail: "logout failed" } }
          : { status: 204 },
      );
    } else {
      await route.fulfill({
        status: 404,
        json: { detail: "Unexpected mock route" },
      });
    }
  });
}

test("shows a visible error when completing setup cannot persist", async ({
  page,
}) => {
  await mockApi(page, { step: "review", onboarding: "error" });
  await page.goto("/setup");
  await page.getByRole("button", { name: "Finish setup" }).click();
  await expect(page.getByText("We could not save your changes.")).toBeVisible();
  await expect(page).toHaveURL(/\/setup$/);
});

test("uses confirmed skipped state for the next MQTT save", async ({
  page,
}) => {
  let lastMqttBody: unknown;
  await mockApi(page, {
    step: "mqtt",
    mqtt: (body) => {
      lastMqttBody = body;
    },
  });
  await page.goto("/settings");
  const mqtt = page.getByRole("region", { name: "MQTT" });
  await mqtt.getByRole("button", { name: "Skip for now" }).click();
  await expect(
    mqtt.getByRole("checkbox", { name: "Enabled" }),
  ).not.toBeChecked();
  await mqtt.getByRole("button", { name: "Save", exact: true }).click();
  await expect
    .poll(() => lastMqttBody)
    .toMatchObject({ enabled: false, skipped: false });
});

test("shows a logout failure without leaving settings or throwing a page error", async ({
  page,
}) => {
  const pageErrors: Error[] = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  await mockApi(page, { step: "mqtt", logout: "error" });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByText("We could not save your changes.")).toBeVisible();
  await expect(page).toHaveURL(/\/settings$/);
  expect(pageErrors).toEqual([]);
});

test("retries setup status without showing administrator creation on failure", async ({ page }) => {
  let available = false;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()).pathname;
    await route.fulfill(url.endsWith("/setup/status") && available
      ? { json: { administrator_exists: true, csrf_token: "mock-csrf" } }
      : { status: 503, json: { detail: "unavailable" } });
  });
  await page.goto("/login");
  await expect(page.getByRole("alert")).toHaveText("We could not load this page. Please try again.");
  await expect(page.getByRole("heading", { name: "Create the administrator" })).toHaveCount(0);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.getByRole("button", { name: "中文" }).click();
  await expect(page.getByRole("alert")).toHaveText("无法加载此页面，请重试。");
  available = true;
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "创建管理员" })).toHaveCount(0);
});

test("retries a failed session read without redirecting to login", async ({ page }) => {
  let available = false;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()).pathname;
    if (url.endsWith("/auth/me") && available) {
      await route.fulfill({ json: { username: "admin" } });
    } else if (url.endsWith("/settings")) {
      await route.fulfill({ json: settings("review") });
    } else {
      await route.fulfill({ status: 503, json: { detail: "unavailable" } });
    }
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toHaveText("We could not load this page. Please try again.");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  available = true;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Setup complete" })).toBeVisible();
});

test("redirects a confirmed unauthenticated session to login", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()).pathname;
    await route.fulfill(url.endsWith("/setup/status")
      ? { json: { administrator_exists: true, csrf_token: "mock-csrf" } }
      : { status: 401, json: { detail: "not authenticated" } });
  });
  await page.goto("/settings");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

for (const path of ["/", "/setup", "/settings"]) {
  test(`retries failed settings on ${path} without misclassifying onboarding`, async ({ page }) => {
    let available = false;
    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url()).pathname;
      if (url.endsWith("/auth/me")) {
        await route.fulfill({ json: { username: "admin" } });
      } else if (url.endsWith("/settings") && available) {
        await route.fulfill({ json: settings("review") });
      } else {
        await route.fulfill({ status: 503, json: { detail: "unavailable" } });
      }
    });
    await page.goto(path);
    await expect(page.getByRole("alert")).toHaveText("We could not load this page. Please try again.");
    expect(new URL(page.url()).pathname).toBe(path);
    available = true;
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("heading", { name: path === "/" ? "Setup complete" : path === "/setup" ? "Review setup" : "Preferences", exact: true })).toBeVisible();
  });
}
