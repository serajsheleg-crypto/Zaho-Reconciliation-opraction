import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8077 (uvicorn); in production FastAPI serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5177,
    proxy: { "/api": { target: "http://127.0.0.1:8077", changeOrigin: false } },
  },
  build: { chunkSizeWarningLimit: 900 },
});
