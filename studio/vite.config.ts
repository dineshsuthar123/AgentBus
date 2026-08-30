import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const controlTarget = validateControlTarget(
  process.env.AGENTBUS_STUDIO_TARGET ?? "http://127.0.0.1:8765"
);

export function validateControlTarget(value: string): string {
  const target = new URL(value);
  const host = target.hostname.replace(/^\[|\]$/g, "");
  const rootOnly = target.pathname === "/" && !target.search && !target.hash;
  if (
    target.protocol !== "http:" ||
    !["127.0.0.1", "::1"].includes(host) ||
    target.username ||
    target.password ||
    !rootOnly
  ) {
    throw new Error(
      "AGENTBUS_STUDIO_TARGET must be a credential-free numeric loopback HTTP origin."
    );
  }
  return target.origin;
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@agentbus/protocol": path.resolve(
        repositoryRoot,
        "extensions/vscode/src/generated/protocol.ts"
      )
    }
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    fs: { allow: [repositoryRoot] },
    proxy: {
      "/health": { target: controlTarget, changeOrigin: true },
      "/api": { target: controlTarget, changeOrigin: true }
    }
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy: {
      "/health": { target: controlTarget, changeOrigin: true },
      "/api": { target: controlTarget, changeOrigin: true }
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    restoreMocks: true
  }
});
