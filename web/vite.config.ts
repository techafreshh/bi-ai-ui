/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Pinned to 0.0.0.0 inside Docker so the SPA is reachable from the host;
// strictPort makes the :5173 contract deterministic for probing.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Issue #12: the beUI copies import via "@/...", so both the dev server and
  // vitest resolve the alias the same way.
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
  },
  // Issue #11: the A2UI contract tests run in the same pipeline as the
  // build — jsdom so @testing-library/react can mount the renderer.
  test: {
    environment: "jsdom",
    // Issue #12: jsdom ships no matchMedia/IntersectionObserver/ResizeObserver,
    // which motion's useInView, useReducedMotion and Recharts'
    // ResponsiveContainer all hard-require — polyfills in setup.ts.
    setupFiles: ["./src/test/setup.ts"],
  },
});
