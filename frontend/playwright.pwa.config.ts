import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "pwa.spec.ts",
  workers: 1,
  webServer: { command: "node pwa-test-server.mjs", url: "http://127.0.0.1:49231", reuseExistingServer: false },
  use: { baseURL: "http://127.0.0.1:49231" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
});
