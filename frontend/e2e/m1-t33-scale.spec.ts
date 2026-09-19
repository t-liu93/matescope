import { expect, test, type Page } from "@playwright/test";
import { clickAuthenticatedAction } from "./auth-helper";

const baseUrl = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "");
const enabled = process.env.MATESCOPE_M1_T33_BROWSER === "1"
  && baseUrl.origin === "http://127.0.0.1:49233";

async function localOnly(page: Page) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === baseUrl.origin) await route.fallback();
    else await route.abort();
  });
}

test("M1-T33 displays the complete synthetic history without a paged aggregate", async ({ page }) => {
  test.skip(!enabled, "Set the explicit M1-T33 loopback origin and opt-in flag");
  test.setTimeout(150_000);
  await localOnly(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Create the administrator" })).toBeVisible();
  await page.getByLabel(/^Username(?:\s*\*)?$/).fill("m1-t33-browser");
  await page.getByLabel(/^Password(?:\s*\*)?$/).fill("m1-t33-browser-password");
  await page.getByLabel(/^Confirm password(?:\s*\*)?$/).fill("m1-t33-browser-password");
  const create = page.getByRole("button", { name: "Create administrator", exact: true });
  await clickAuthenticatedAction(page, "/api/v1/setup/administrator", () => create.click());
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto("/settings");
  const preferences = page.getByRole("region", { name: "Preferences", exact: true });
  await expect(preferences).toBeVisible();
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
  await pg.getByRole("button", { name: "Test saved connection", exact: true }).click();
  await expect(pg.getByText("Connection test succeeded.", { exact: true })).toBeVisible();
  await page.goto("/trips?vehicle=33&preset=all_history");
  await expect(page.getByRole("button", { name: "All history", exact: true })).toBeVisible();
  await expect(page.getByLabel("Selected-period trip summary")).toContainText("100,000");
  await expect(page.locator('[data-history-record-id]')).toHaveCount(50);
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
});
