/// <reference types="vitest/config" />
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Content-Security-Policy for the built app. Scripts are locked to our own files.
// Styles allow 'unsafe-inline' only because the UI library injects <style> tags at runtime.
// frame-ancestors cannot be set from a <meta> tag: the web server must send it (see deploy/).
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "manifest-src 'self'",
  "worker-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

function cspMeta(): Plugin {
  return {
    name: "ledgerline-csp",
    apply: "build", // dev server needs inline scripts for hot reload
    transformIndexHtml: () => [
      {
        tag: "meta",
        attrs: { "http-equiv": "Content-Security-Policy", content: CSP },
        injectTo: "head-prepend",
      },
    ],
  };
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

// Fills the build id and the list of files to precache into public/sw.js.
// Only the app shell is cached. The service worker itself ignores /api and non-GET requests.
function serviceWorker(): Plugin {
  let outDir = "dist";
  return {
    name: "ledgerline-sw",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    closeBundle() {
      const files = walk(outDir)
        .map((f) => "/" + relative(outDir, f).split("\\").join("/"))
        .filter((p) => p !== "/sw.js" && !p.endsWith(".map"))
        // Fonts: precache only Latin and Latin-extended; other scripts load on demand.
        .filter((p) => !p.endsWith(".woff2") || /latin(-ext)?-wght/.test(p))
        .sort();
      const hash = createHash("sha256");
      for (const p of files) hash.update(p).update(readFileSync(join(outDir, p)));
      const build = hash.digest("hex").slice(0, 16);
      const swPath = join(outDir, "sw.js");
      const sw = readFileSync(swPath, "utf8")
        .replace("__BUILD_ID__", build)
        .replace("__PRECACHE__", JSON.stringify(["/", ...files]));
      writeFileSync(swPath, sw);
    },
  };
}

export default defineConfig({
  plugins: [react(), cspMeta(), serviceWorker()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // Same-origin in development too, so the browser never needs CORS.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { target: "es2022", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
