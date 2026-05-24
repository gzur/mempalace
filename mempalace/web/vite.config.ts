import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

const here = path.dirname(fileURLToPath(import.meta.url));

// Vite config for the MemPalace SPA.
//
// - ``base: "/app/"`` because the daemon serves the bundle under that
//   path (Starlette mount in ``mempalace/http_server.py``).
// - ``build.outDir: "../web_dist"`` because the Python wheel ships
//   ``mempalace/web_dist`` and we keep the JS source out of the wheel.
// - The dev server proxies every server-side path to the live daemon,
//   so ``npm run dev`` works against the real palace on localhost.
export default defineConfig({
  base: "/app/",
  plugins: [
    tanstackRouter({
      target: "react",
      routesDirectory: "src/routes",
      generatedRouteTree: "src/routeTree.gen.ts",
      autoCodeSplitting: true,
    }),
    react(),
    tailwind(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(here, "./src"),
    },
  },
  build: {
    outDir: "../web_dist",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:9876",
      "/mcp": "http://127.0.0.1:9876",
      "/healthz": "http://127.0.0.1:9876",
      "/ui": "http://127.0.0.1:9876",
    },
  },
});
