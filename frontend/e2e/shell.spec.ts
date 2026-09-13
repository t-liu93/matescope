import { expect, test } from "@playwright/test";

const credentials = { username: "m0-t03-admin", password: "m0-t03-password" };

test("onboarding saves, resumes, skips and supports later editing", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/login");
  await expect(page.getByLabel(/^Username(?:\s*\*)?$/)).toBeVisible();
  const creating = await page.getByRole("heading", { name: "Create the administrator" }).isVisible();
  await page.getByLabel(/^Username(?:\s*\*)?$/).fill(credentials.username);
  await page.getByLabel(/^Password(?:\s*\*)?$/).fill(credentials.password);
  if (creating) await page.getByLabel(/^Confirm password(?:\s*\*)?$/).fill(credentials.password);
  await page.getByRole("button", { name: creating ? "Create administrator" : "Sign in", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login$/);

  // Revisit the same saved wizard on the second viewport without resetting application data.
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Setup", exact: true })).toBeVisible();
  for (let index = 0; index < 4; index += 1) {
    if (await page.getByLabel(/^Display timezone(?:\s*\*)?$/).isVisible()) break;
    const previous = await page.getByRole("region").getAttribute("aria-label");
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await expect(page.getByRole("region")).not.toHaveAttribute("aria-label", previous!);
  }
  await page.getByLabel(/^Display timezone(?:\s*\*)?$/).fill("Europe/Amsterdam");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { name: "PostgreSQL", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "PostgreSQL", exact: true })).toBeVisible();
  await page.getByLabel("Host", { exact: true }).fill("synthetic-postgres");
  await page.getByLabel(/^Username(?:\s*\*)?$/).fill("synthetic_readonly");
  await page.getByLabel("Enabled", { exact: true }).check();
  await page.getByRole("textbox", { name: "Password handling", exact: true }).click();
  await page.getByRole("option", { name: "Replace password", exact: true }).click();
  await page.getByLabel(/^Password \(optional\)/).fill("synthetic-connection-password");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { name: "MQTT", exact: true })).toBeVisible();
  await expect(page.getByLabel("Host", { exact: true })).toHaveValue("");
  if (info.project.name === "desktop") {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await page.getByLabel(/^Username(?:\s*\*)?$/).fill(credentials.username);
    await page.getByLabel(/^Password(?:\s*\*)?$/).fill(credentials.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "MQTT", exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "Skip for now", exact: true }).click();
  await expect(page.getByRole("heading", { name: "SMTP", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Review setup", exact: true })).toBeVisible();
  await expect(page.getByText("PostgreSQL: Saved, not verified", { exact: true })).toBeVisible();
  await expect(page.getByText("MQTT: Skipped", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Finish setup", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Setup complete", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Edit settings", exact: true }).click();
  const pg = page.getByRole("region", { name: "PostgreSQL", exact: true });
  await expect(pg.getByLabel("Host", { exact: true })).toHaveValue("synthetic-postgres");
  await expect(pg.getByText("Password saved", { exact: true })).toBeVisible();
  await expect(page.getByLabel(/^Display timezone(?:\s*\*)?$/)).toHaveValue("Europe/Amsterdam");
  await pg.getByLabel("Host", { exact: true }).fill("synthetic-edited");
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/settings/postgresql") && response.request().method() === "PUT"),
    pg.getByRole("button", { name: "Save", exact: true }).click(),
  ]);
  await page.reload();
  await expect(pg.getByLabel("Host", { exact: true })).toHaveValue("synthetic-edited");
  await expect(pg.getByText("Password saved", { exact: true })).toBeVisible();
  await pg.getByRole("textbox", { name: "Password handling", exact: true }).click();
  await page.getByRole("option", { name: "Clear password", exact: true }).click();
  await pg.getByRole("button", { name: "Save", exact: true }).click();
  await expect(pg.getByText("No password saved", { exact: true }).first()).toBeVisible();

  await page.getByRole("region", { name: "Preferences", exact: true })
    .getByRole("textbox", { name: "Language", exact: true }).click();
  await page.getByRole("option", { name: "中文", exact: true }).click();
  await page.getByRole("region", { name: "Preferences", exact: true })
    .getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh");
  await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  await page.getByRole("region", { name: "偏好设置", exact: true })
    .getByRole("textbox", { name: "语言", exact: true }).click();
  await page.getByRole("option", { name: "English", exact: true }).click();
  await page.getByRole("region", { name: "偏好设置", exact: true })
    .getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  if (info.project.name === "mobile") {
    // Reusing the synthetic password still exercises the change flow and revokes the session.
    await page.getByLabel("Current password", { exact: true }).fill(credentials.password);
    await page.getByLabel("New password", { exact: true }).fill(credentials.password);
    await page.getByLabel(/^Confirm password(?:\s*\*)?$/).fill(credentials.password);
    await page.getByRole("button", { name: "Update password", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
  }
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  expect(errors).toEqual([]);
});
