import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const playwrightCli = require.resolve("@playwright/test/cli");
const configuredRoot = process.env.CODELENS_INTEGRATION_DATA_DIR
  ?? path.resolve(process.cwd(), ".tmp");
const dataRoot = path.resolve(configuredRoot);
mkdirSync(dataRoot, { recursive: true });
const runDataDir = mkdtempSync(path.join(dataRoot, "codelens-integration-"));

try {
  const result = spawnSync(process.execPath, [playwrightCli, "test"], {
    cwd: process.cwd(),
    encoding: "utf8",
    env: {
      ...process.env,
      CODELENS_INTEGRATION_DATA_DIR: runDataDir,
    },
    maxBuffer: 16 * 1024 * 1024,
    shell: false,
    timeout: 5 * 60 * 1000,
  });
  process.stdout.write(result.stdout ?? "");
  process.stderr.write(result.stderr ?? "");
  if (result.error !== undefined) {
    throw result.error;
  }
  process.exitCode = result.status === 0 ? 0 : 1;
} finally {
  rmSync(runDataDir, { force: true, recursive: true });
}
