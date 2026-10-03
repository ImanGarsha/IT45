import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// build straight into the Python package so `python -m it45` serves it
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../it45/web", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8045" } },
});
