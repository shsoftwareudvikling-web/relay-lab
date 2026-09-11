import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5179,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8179" },
  },
  preview: { port: 4179, strictPort: true },
});
