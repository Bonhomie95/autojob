import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Dev-time proxy: the browser only ever talks to :5173 (this server), which
// forwards /api/* to the Flask backend on :9000. That keeps everything
// same-origin from the browser's point of view — no CORS config needed, and
// cookies (the session, CSRF) just work — matching how the production build
// is served (Flask serves the built SPA + /api/* from one origin).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:9000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
