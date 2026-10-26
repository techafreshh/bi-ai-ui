/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Pinned to 0.0.0.0 inside Docker so the SPA is reachable from the host;
// strictPort makes the :5173 contract deterministic for probing.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
  },
  // Issue #11: the A2UI contract tests run in the same pipeline as the
  // build — jsdom so @testing-library/react can mount the renderer.
  test: {
    environment: "jsdom",
  },
});
