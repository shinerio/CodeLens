import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const apiHost = process.env.CODELENS_API_HOST ?? "127.0.0.1";
const apiPort = process.env.CODELENS_API_PORT ?? "8800";
const frontendPort = parseInt(process.env.CODELENS_FRONTEND_PORT ?? "5173", 10);
// 允许访问 dev server 的主机名列表，逗号分隔。设为 "true" 或 "all" 允许全部。
const allowedHostsEnv = process.env.CODELENS_FRONTEND_ALLOWED_HOSTS ?? "";
const allowedHosts: string[] | true =
  allowedHostsEnv === "true" || allowedHostsEnv === "all"
    ? true
    : allowedHostsEnv
        .split(",")
        .map((h) => h.trim())
        .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      {
        find: /^monaco-editor$/,
        replacement: fileURLToPath(
          new URL("./node_modules/monaco-editor/esm/vs/editor/editor.main.js", import.meta.url),
        ),
      },
    ],
  },
  server: {
    port: frontendPort,
    allowedHosts,
    proxy: {
      "/api": {
        target: `http://${apiHost}:${apiPort}`,
        changeOrigin: true,
        headers: { origin: `http://${apiHost}:${apiPort}` },
      },
    },
  },
  test: {
    environment: "jsdom",
    maxWorkers: 4,
    setupFiles: "./src/testSetup.ts",
    exclude: ["e2e/**", "playwright.config.ts", "**/node_modules/**"],
  },
});
