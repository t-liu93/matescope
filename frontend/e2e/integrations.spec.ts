import { expect, test, type Page } from "@playwright/test";

type TestResult = {
  version: number;
  status: "success" | "failure";
  code: string;
  tested_at: string;
  persisted: boolean;
  message_received?: boolean;
  delivery_accepted?: boolean;
};

function settings() {
  return {
    preferences: { language: "en", timezone: "Europe/Amsterdam", tile_url: "https://tiles.example/{z}/{x}/{y}.png", saved: true },
    postgresql: { password_set: false, version: 0, status: "disabled", test_available: true, test_result: null, host: "", username: "", enabled: false, skipped: true, port: 5432, database: "teslamate", sslmode: "prefer" },
    mqtt: { password_set: true, version: 1, status: "unverified", test_available: true, test_result: null, host: "mqtt", username: "viewer", enabled: true, skipped: false, port: 1883, tls: false, verify_tls: true, topic_prefix: "teslamate" },
    smtp: { password_set: true, version: 1, status: "unverified", test_available: true, test_result: null, host: "smtp", username: "mailer", enabled: true, skipped: false, port: 587, tls_mode: "starttls", verify_tls: true, sender: "mate@example.test" },
    onboarding: { step: "review", completed: true },
  };
}

async function mockApi(
  page: Page,
  outcomes: { mqtt: TestResult; smtp: TestResult },
  pending = false,
  smtpFailure?: "abort" | "http500",
  clearSmtpResultOnSave = false,
) {
  const current = settings();
  if (smtpFailure) {
    (current.smtp as { test_result: TestResult | null }).test_result = { ...outcomes.smtp };
    current.smtp.status = "success";
  }
  const calls = { mqtt: 0, smtp: 0, smtpBody: [] as unknown[] };
  let release: (() => void) | undefined;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: "admin" } });
    if (path.endsWith("/auth/csrf")) return route.fulfill({ json: { csrf_token: "csrf" } });
    if (path.endsWith("/settings") && request.method() === "GET") return route.fulfill({ json: current });
    if (path.endsWith("/settings/mqtt") && request.method() === "PUT") return route.fulfill({ json: current });
    if (path.endsWith("/settings/smtp") && request.method() === "PUT") {
      if (clearSmtpResultOnSave) {
        current.smtp = {
          ...current.smtp,
          version: current.smtp.version + 1,
          status: "unverified",
          test_result: null,
        };
      }
      return route.fulfill({ json: current });
    }
    if (path.endsWith("/settings/mqtt/test")) {
      calls.mqtt += 1;
      if (pending) await new Promise<void>((resolve) => { release = resolve; });
      return route.fulfill({ json: outcomes.mqtt });
    }
    if (path.endsWith("/settings/smtp/test")) {
      calls.smtp += 1;
      calls.smtpBody.push(request.postDataJSON());
      if (smtpFailure === "abort") return route.abort("failed");
      if (smtpFailure === "http500") {
        return route.fulfill({ status: 500, json: { detail: "synthetic SMTP failure" } });
      }
      return route.fulfill({ json: outcomes.smtp });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected mock route" } });
  });
  return { calls, release: () => release?.() };
}

const mqttAccepted = { version: 1, status: "success", code: "subscription_accepted", tested_at: "2026-09-14T10:00:00Z", persisted: true, message_received: false } as const;
const mqttMessage = { ...mqttAccepted, code: "message_received", message_received: true } as const;
const smtpAccepted = { version: 1, status: "success", code: "delivery_accepted", tested_at: "2026-09-14T10:00:00Z", persisted: true, delivery_accepted: true } as const;

test("does not test MQTT or SMTP on load or save; each test needs an explicit click", async ({ page }) => {
  const mock = await mockApi(page, { mqtt: mqttAccepted, smtp: smtpAccepted });
  await page.goto("/settings");
  await expect.poll(() => mock.calls).toEqual({ mqtt: 0, smtp: 0, smtpBody: [] });
  await page.getByRole("region", { name: "MQTT" }).getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => mock.calls.mqtt).toBe(0);
  await page.getByRole("region", { name: "SMTP" }).getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => mock.calls.smtp).toBe(0);
  await page.getByRole("region", { name: "MQTT" }).getByRole("button", { name: "Test saved connection" }).click();
  await expect(page.getByRole("region", { name: "MQTT" }).getByText("Subscription was accepted; no new MQTT message was observed during the test.")).toBeVisible();
  expect(mock.calls.mqtt).toBe(1);
});

test("sends one SMTP test only to the chosen valid recipient", async ({ page }) => {
  const mock = await mockApi(page, { mqtt: mqttAccepted, smtp: smtpAccepted });
  await page.goto("/settings");
  const smtp = page.getByRole("region", { name: "SMTP" });
  const button = smtp.getByRole("button", { name: "Send test email" });
  await expect(button).toBeDisabled();
  await smtp.getByLabel("Test recipient").fill("not an address");
  await expect(button).toBeDisabled();
  await smtp.getByLabel("Test recipient").fill("owner@example.test");
  await button.click();
  await expect(smtp.getByText("The SMTP server accepted the test email for delivery.")).toBeVisible();
  expect(mock.calls.smtp).toBe(1);
  expect(mock.calls.smtpBody).toEqual([{ recipient: "owner@example.test" }]);
});

test("locks the MQTT form while testing and distinguishes no message from an observed message", async ({ page }) => {
  const mock = await mockApi(page, { mqtt: mqttMessage, smtp: smtpAccepted }, true);
  await page.goto("/settings");
  const mqtt = page.getByRole("region", { name: "MQTT" });
  const testButton = mqtt.getByRole("button", { name: "Test saved connection" });
  await testButton.click();
  await expect(mqtt.getByLabel("Host", { exact: true })).toBeDisabled();
  await expect(mqtt.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await expect(
    mqtt.getByRole("button", { name: "Testing connection…" }),
  ).toBeDisabled();
  expect(mock.calls.mqtt).toBe(1);
  mock.release();
  await expect(mqtt.getByText("A new MQTT message was observed during this test.")).toBeVisible();
  await expect(mqtt.getByText("Subscription was accepted and a new MQTT message was observed.")).toBeVisible();
});

test("shows a persisted configuration-changed SMTP failure without hiding delivery acceptance", async ({ page }) => {
  const changed = { ...smtpAccepted, status: "failure", code: "configuration_changed", persisted: false } as const;
  await mockApi(page, { mqtt: mqttAccepted, smtp: changed });
  await page.goto("/settings");
  const smtp = page.getByRole("region", { name: "SMTP" });
  await smtp.getByLabel("Test recipient").fill("owner@example.test");
  await smtp.getByRole("button", { name: "Send test email" }).click();
  await expect(smtp.getByText("Connection test failed.")).toBeVisible();
  await expect(smtp.getByText("The configuration changed before this test completed.")).toBeVisible();
  await expect(smtp.getByText("Configuration changed before this test completed. A test email may still have been accepted for delivery.")).toBeVisible();
});

for (const [label, failure] of [["aborted response", "abort"], ["HTTP 500 response", "http500"]] as const) {
  test(`shows delivery uncertainty after an SMTP ${label} with no stale success`, async ({ page }) => {
    const mock = await mockApi(page, { mqtt: mqttAccepted, smtp: smtpAccepted }, false, failure);
    await page.goto("/settings");
    const smtp = page.getByRole("region", { name: "SMTP" });
    await smtp.getByLabel("Test recipient").fill("owner@example.test");
    await smtp.getByRole("button", { name: "Send test email" }).click();

    await expect(smtp.getByText("The SMTP test request failed and delivery is unknown. The server may have accepted the email before the response was lost. Check the recipient inbox before retrying.")).toBeVisible();
    await expect(smtp.getByText("The SMTP server accepted the test email for delivery.")).toHaveCount(0);
    await expect(smtp.getByText("Connection verified")).toHaveCount(0);
    expect(mock.calls.smtp).toBe(1);
    expect(mock.calls.smtpBody).toEqual([{ recipient: "owner@example.test" }]);
  });
}

test("clears an SMTP test request failure after saving a new configuration", async ({ page }) => {
  await mockApi(page, { mqtt: mqttAccepted, smtp: smtpAccepted }, false, "http500", true);
  await page.goto("/settings");
  const smtp = page.getByRole("region", { name: "SMTP" });
  await smtp.getByLabel("Test recipient").fill("owner@example.test");
  await smtp.getByRole("button", { name: "Send test email" }).click();
  await expect(smtp.getByText("The SMTP test request failed and delivery is unknown. The server may have accepted the email before the response was lost. Check the recipient inbox before retrying.")).toBeVisible();

  await smtp.getByRole("button", { name: "Save", exact: true }).click();

  await expect(smtp.getByText("Saved, not verified")).toBeVisible();
  await expect(smtp.getByText("The SMTP test request failed and delivery is unknown. The server may have accepted the email before the response was lost. Check the recipient inbox before retrying.")).toHaveCount(0);
  await expect(smtp.getByText("The SMTP server accepted the test email for delivery.")).toHaveCount(0);
});
