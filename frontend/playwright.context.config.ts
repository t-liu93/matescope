import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "history.spec.ts",
  workers: 1,
  webServer: {
    command: "pnpm vite --host 127.0.0.1 --port 49233",
    url: "http://127.0.0.1:49233",
    reuseExistingServer: false,
  },
  use: { baseURL: "http://127.0.0.1:49233" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
});
