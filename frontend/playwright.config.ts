import { defineConfig } from "@playwright/test";
import path from "node:path";

const dataDir = process.env.CODELENS_E2E_DATA_DIR ?? path.resolve(process.cwd(), ".tmp", "codelens-e2e");

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `uv run --project backend python backend/scripts/run_fake_server.py --data-dir ${dataDir}`,
      cwd: "..",
      port: 8765,
      reuseExistingServer: false,
    },
    {
      command: "pnpm --dir frontend dev --host 127.0.0.1",
      cwd: "..",
      port: 5173,
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
});
