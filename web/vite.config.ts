/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = "http://localhost:8765";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/mcp": { target: API, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // e2e/ holds the Playwright suite (npm run e2e), not vitest tests.
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
