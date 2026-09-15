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

async function activateUpdatedWorker(context: BrowserContext) {
  const verification = await context.newPage();
  await verification.goto("/manifest.webmanifest");
  await verification.waitForFunction(() => navigator.serviceWorker.controller !== null);
  await expect.poll(() => verification.evaluate(async () => (await navigator.serviceWorker.getRegistration())?.active?.scriptURL ?? ""))
    .toContain("/test-update-sw.js");
  return verification;
}
