import { defineConfig, devices } from "@playwright/test";

const baseURL = "http://127.0.0.1:49234";
const webkitRunner = process.env.MATESCOPE_M1_T34_WEBKIT_RUNNER;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "m1-t34.spec.ts",
  workers: 1,
  webServer: {
    command: "pnpm exec vite --host 127.0.0.1 --port 49234",
    url: baseURL,
    reuseExistingServer: false,
  },
  use: { baseURL },
  projects: [
    { name: "chromium", grep: /Chromium/, use: { ...devices["Desktop Chrome"] } },
    { name: "chromium-touch", grep: /Chromium mobile simulation/, use: { ...devices["iPhone 13"], browserName: "chromium" } },
    {
      name: "webkit",
      grep: /WebKit/,
      use: {
        ...devices["Desktop Safari"],
        launchOptions: webkitRunner ? { executablePath: webkitRunner } : {},
      },
    },
  ],
});
