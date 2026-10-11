import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// A separate entry graph keeps control-plane code out of the public website.
export default defineConfig({
  root: path.resolve(import.meta.dirname, "site"),
  base: "/AgentBus/",
  publicDir: path.resolve(import.meta.dirname, "public"),
  plugins: [react()],
  build: {
    outDir: path.resolve(import.meta.dirname, "showcase-dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
