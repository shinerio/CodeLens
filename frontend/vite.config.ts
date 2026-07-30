import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const apiHost = process.env.CODELENS_API_HOST ?? "127.0.0.1";
const apiPort = process.env.CODELENS_API_PORT ?? "8800";
const frontendPort = parseInt(process.env.CODELENS_FRONTEND_PORT ?? "5173", 10);

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
