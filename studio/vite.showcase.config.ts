import path from "node:path";
import { URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// A separate entry graph keeps control-plane code out of the public website.
const project = new URL("https://github.com/dineshsuthar123/AgentBus").pathname.split("/").at(-1);
export default defineConfig({
  root: path.resolve(import.meta.dirname, "site"),
  base: `/${project}/`,
  publicDir: path.resolve(import.meta.dirname, "public"),
  plugins: [react()],
  build: {
    outDir: path.resolve(import.meta.dirname, "showcase-dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
