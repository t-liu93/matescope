import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL;
if (baseURL !== "http://127.0.0.1:49233") {
  throw new Error("M1-T33 browser scale test requires PLAYWRIGHT_BASE_URL=http://127.0.0.1:49233");
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "m1-t33-scale.spec.ts",
  workers: 1,
  use: { baseURL },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
});
