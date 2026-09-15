import { createHmac } from "node:crypto";
import { expect, test, type Page, type Locator } from "@playwright/test";
import { clickAuthenticatedAction } from "./auth-helper";

// The real smoke suite uses one disposable instance across viewports.
const credentials = { username: "m0-t03-admin", password: "m0-t03-password" };
function totp(secret: string) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...secret].map((character) => alphabet.indexOf(character).toString(2).padStart(5, "0")).join("");
  const bytes = Buffer.from(bits.match(/.{8}/g)!.map((value) => Number.parseInt(value, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000)));
  const digest = createHmac("sha1", bytes).update(counter).digest();
  return String((digest.readUInt32BE(digest.at(-1)! & 15) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel(/^Username(?:\s*\*)?$/).fill(credentials.username);
  await page.getByLabel(/^Password(?:\s*\*)?$/).fill(credentials.password);
  const creating = await page.getByRole("heading", { name: "Create the administrator" }).isVisible();
  if (creating) await page.getByLabel(/^Confirm password(?:\s*\*)?$/).fill(credentials.password);
  const result = await clickAuthenticatedAction(page, creating ? "/api/v1/setup/administrator" : "/api/v1/auth/login",
    () => page.getByRole("button", { name: creating ? "Create administrator" : "Sign in", exact: true }).click());
  expect(result.ok()).toBe(true);
}
async function signout(page: Page) {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
}
async function recoveryLogin(page: Page, code: string) {
  await login(page);
  await page.getByRole("button", { name: "Use a recovery code", exact: true }).click();
  await page.getByLabel(/^Recovery code(?:\s*\*)?$/).fill(code);
  const result = await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/verify",
    async () => {
      await page.getByLabel(/^Recovery code(?:\s*\*)?$/).fill(code);
      await page.getByRole("button", { name: "Verify", exact: true }).click();
    });
  expect(result.status()).toBe(200);
  await expect(page).not.toHaveURL(/\/login$/);
}
async function proof(region: Locator, code: string) {
  await region.getByLabel(/^Current password(?:\s*\*)?$/).fill(credentials.password);
  const method = region.getByRole("textbox", { name: "Verification method", exact: true });
  if (await method.inputValue() !== "Recovery code") {
    await method.click();
    await region.page().getByRole("option", { name: "Recovery code", exact: true }).click();
  }
  await region.getByLabel(/^Recovery code(?:\s*\*)?$/).fill(code);
}

test("real two-factor enrollment, challenge, recovery rotation, password change and disable", async ({ page, baseURL }, info) => {
  test.setTimeout(480_000);
  page.setDefaultTimeout(10_000);
  const errors: string[] = [];
  const external: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (!request.url().startsWith(new URL(baseURL!).origin) && !request.url().startsWith("data:")) external.push(request.url()); });
  await login(page);
  await expect(page).not.toHaveURL(/\/login$/);
  if (info.project.name === "desktop") {
    // Revisit the optional wizard step and exercise its full enrollment flow.
    await page.goto("/setup");
    for (let i = 0; i < 6; i++) {
      if (await page.getByRole("heading", { name: "Two-factor authentication", exact: true }).isVisible()) break;
      if (await page.getByRole("heading", { name: "Review setup", exact: true }).isVisible()) await page.getByRole("button", { name: "Back", exact: true }).click();
      else if (await page.getByRole("button", { name: "Skip for now", exact: true }).isVisible()) await page.getByRole("button", { name: "Skip for now", exact: true }).click();
      else await page.getByRole("button", { name: "Save", exact: true }).click();
      await page.waitForTimeout(100);
    }
  } else await page.goto("/settings");
  await page.getByRole("button", { name: "Enable two-factor authentication", exact: true }).click();
  expect((await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/enroll", async () => {
    await page.locator("#two-factor-enroll-password").fill(credentials.password);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  })).status()).toBe(200);
  await expect(page.getByRole("img", { name: "Authenticator setup QR code" })).toHaveAttribute("src", /^data:image\/png;base64,/);
  const secret = (await page.locator("code").textContent())!;
  expect(secret).toMatch(/^[A-Z2-7]{32}$/);
  await page.locator("#two-factor-confirm-password").fill(credentials.password);
  await page.locator("#two-factor-confirm-code").fill(totp(secret));
  const enabled = await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/confirm",
    async () => {
      await page.locator("#two-factor-confirm-password").fill(credentials.password);
      await page.locator("#two-factor-confirm-code").fill(totp(secret));
      await page.getByRole("button", { name: "Enable two-factor authentication", exact: true }).click();
    });
  expect(enabled.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Recovery codes", exact: true })).toBeVisible();
  let recovery = (await page.locator("pre").textContent())!.split("\n");
  expect(recovery).toHaveLength(10);
  expect(await page.locator("code").count()).toBe(0);
  const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage, location.href]));
  for (const value of [secret, ...recovery]) expect(storage).not.toContain(value);
  expect(external).toEqual([]);
  await page.getByRole("button", { name: "I saved these codes", exact: true }).click();
  await expect(page.locator("pre")).toHaveCount(0);
  await expect(page.getByText("10 recovery codes remaining", { exact: true })).toBeVisible();
  await signout(page);
  await login(page);
  await expect(page.getByRole("heading", { name: "Verify your sign-in", exact: true })).toBeVisible();
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  const codeInput = page.getByLabel(/^Verification code(?:\s*\*)?$/);
  await expect(codeInput).toHaveAttribute("type", "text");
  await expect(codeInput).toHaveAttribute("inputmode", "numeric");
  await expect(codeInput).toHaveAttribute("autocomplete", "one-time-code");
  await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/verify", async () => {
    await codeInput.fill("000000");
    await page.getByRole("button", { name: "Verify", exact: true }).click();
  });
  await expect(page.getByText("That verification code is invalid or has already been used.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.locator("#credentials-password")).toHaveValue("");
  await login(page);
  await expect(page.getByRole("heading", { name: "Verify your sign-in", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await login(page);
  // The enrollment code is already consumed. Wait for the next real time step.
  await page.waitForTimeout(30_050 - Date.now() % 30_000);
  await page.locator("#login-two-factor-code").fill(totp(secret));
  const verified = await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/verify", async () => {
    await page.locator("#login-two-factor-code").fill(totp(secret));
    await page.getByRole("button", { name: "Verify", exact: true }).click();
  });
  expect(verified.status()).toBe(200);
  await expect(page).not.toHaveURL(/\/login$/);
  await signout(page);
  await recoveryLogin(page, recovery[0]);
  await page.goto("/settings");
  await expect(page.getByText("9 recovery codes remaining", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Generate new recovery codes", exact: true }).click();
  let region = page.getByRole("region", { name: "Generate new recovery codes", exact: true });
  await proof(region, recovery[1]);
  expect((await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/recovery-codes", async () => { await proof(region, recovery[1]); await region.getByRole("button", { name: "Confirm", exact: true }).click(); })).status()).toBe(200);
  await expect(page.locator("pre")).toBeVisible();
  const oldCode = recovery[2];
  recovery = (await page.locator("pre").textContent())!.split("\n");
  expect(recovery).toHaveLength(10);
  expect(recovery).not.toContain(oldCode);
  await page.getByRole("button", { name: "I saved these codes", exact: true }).click();
  await page.reload();
  await expect(page.locator("pre")).toHaveCount(0);
  region = page.getByRole("region", { name: "Change password", exact: true });
  await proof(region, oldCode);
  await region.getByLabel(/^New password(?:\s*\*)?$/).fill(credentials.password);
  await region.getByLabel(/^Confirm password(?:\s*\*)?$/).fill(credentials.password);
  await clickAuthenticatedAction(page, "/api/v1/auth/password", () => region.getByRole("button", { name: "Update password", exact: true }).click());
  await expect(region.getByText("That verification code is invalid or has already been used.", { exact: true })).toBeVisible();
  await region.getByLabel(/^Recovery code(?:\s*\*)?$/).fill(recovery[0]);
  expect((await clickAuthenticatedAction(page, "/api/v1/auth/password", () => region.getByRole("button", { name: "Update password", exact: true }).click())).status()).toBe(204);
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await recoveryLogin(page, recovery[1]);
  await page.goto("/settings");
  await expect(page.getByText("8 recovery codes remaining", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Disable two-factor authentication", exact: true }).click();
  region = page.getByRole("region", { name: "Disable two-factor authentication", exact: true });
  await proof(region, recovery[2]);
  expect((await clickAuthenticatedAction(page, "/api/v1/auth/two-factor/disable", async () => { await proof(region, recovery[2]); await region.getByRole("button", { name: "Confirm", exact: true }).click(); })).status()).toBe(204);
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await login(page);
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto("/settings");
  await expect(page.getByText("Two-factor authentication is disabled.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  await signout(page);
});
