import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    host: true,
    // Forward /api/* to a local backend proxy (optional, for connection testing)
    proxy: {
      "/api": {
        target: `http://${process.env.PROXY_HOST || "127.0.0.1"}:${process.env.PROXY_PORT || 5175}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
  },
});
