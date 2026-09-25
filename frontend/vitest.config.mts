import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
    // Child processes are the most robust pool on Windows, and capping workers
    // stops a cold start (fresh install, antivirus scanning node_modules) from
    // loading jsdom in many workers at once and hitting the worker start
    // timeout. The repository path containing spaces is fine.
    pool: "forks",
    maxWorkers: 2,
  },
});
