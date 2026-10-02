import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The dev server binds 127.0.0.1:5173 / localhost:5173, which is already in the
// backend's CORS allow-list, so the browser can call the API cross-origin in
// development without the backend being reconfigured.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // The backend's CORS allow-list names 5173 explicitly. Silently falling
    // back to another port would leave the app loading but failing every API
    // call with an opaque CORS error, so fail fast with a clear message
    // instead.
    strictPort: true,
  },
  build: {
    // Recharts is only used by the risk panel, but it is the single largest
    // dependency. Splitting it out keeps the initial bundle for the landing
    // and investigate routes small instead of shipping it with first paint.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
        },
      },
    },
  },
});
