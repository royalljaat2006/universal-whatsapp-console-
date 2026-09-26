import { defineConfig, devices } from "@playwright/test";

// Targets the docker-compose stack behind nginx (http://localhost:8080),
// not a separately-started dev server: that's the real request path this
// app depends on for same-origin cookies, CSRF and BASE_PATH handling.
// Run `docker compose up -d` first, then `npm run test:e2e`.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
