import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` on :5173, proxying the engine. Prod: `npm run build` → dist/, served by the engine.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", ws: true } },
  },
});
