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
});
