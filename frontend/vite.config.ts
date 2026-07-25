import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8800",
        changeOrigin: true,
        headers: { origin: "http://127.0.0.1:8800" },
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
