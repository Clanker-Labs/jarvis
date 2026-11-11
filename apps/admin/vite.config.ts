/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The brain's admin app (port 9000) serves the built bundle and the API from one origin.
// In dev, Vite proxies /api (incl. the event WebSocket) to it. The Host header is kept as-is
// (no changeOrigin) because the backend checks Origin == Host on the WebSocket.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:9000", ws: true } },
  },
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { include: ["src/**/*.test.ts"] },
});
