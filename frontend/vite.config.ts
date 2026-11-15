import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In production nginx serves the build and proxies /api to the backend.
// `npm run dev` does the same proxying so the app behaves identically.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
