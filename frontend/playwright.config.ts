import { defineConfig } from "@playwright/test";
import path from "node:path";

const dataDir = process.env.CODELENS_E2E_DATA_DIR ?? path.resolve(process.cwd(), ".tmp", "codelens-e2e");
const backendPort = Number(process.env.CODELENS_E2E_BACKEND_PORT ?? "8810");
const frontendPort = Number(process.env.CODELENS_E2E_FRONTEND_PORT ?? "5183");

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `uv run --project backend python backend/scripts/run_fake_server.py --data-dir ${dataDir} --port ${backendPort}`,
      cwd: "..",
      port: backendPort,
      reuseExistingServer: false,
    },
    {
      command: `pnpm --dir frontend dev --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: "..",
      env: { CODELENS_API_PORT: String(backendPort) },
      port: frontendPort,
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
});
