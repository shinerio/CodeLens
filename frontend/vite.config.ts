import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const apiPort = process.env.CODELENS_API_PORT ?? "8800";

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
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
        headers: { origin: `http://127.0.0.1:${apiPort}` },
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
