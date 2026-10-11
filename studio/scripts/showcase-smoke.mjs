import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { URL } from "node:url";
import { readFile, readdir, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import console from "node:console";

const root = path.resolve(import.meta.dirname, "..");
const base = `/${new URL("https://github.com/dineshsuthar123/AgentBus").pathname.split("/").at(-1)}/`;
const dist = path.join(root, "showcase-dist");
const evidence = path.resolve(root, "../.tmp/public-showcase-evidence");
await mkdir(evidence, { recursive: true });
const files = await readdir(path.join(dist, "assets"));
for (const file of files.filter((name) => name.endsWith(".js"))) {
  const script = await readFile(path.join(dist, "assets", file), "utf8");
  if (/\/api\/v1\/|syndra_session|bearer_token|AZURE_OPENAI_API_KEY|launch_execution/.test(script)) throw new Error("Public bundle contains control-plane material");
}
const html = await readFile(path.join(dist, "index.html"), "utf8");
for (const license of ["LICENSE-bricolage.txt", "LICENSE-ibm-plex-sans.txt", "LICENSE-ibm-plex-mono.txt"]) {
  if (!(await readFile(path.join(dist, license), "utf8")).includes("OPEN FONT LICENSE")) throw new Error("Public font license is missing");
}
if (!html.includes("connect-src 'none'") || !html.includes(`${base}assets/`)) throw new Error("Public CSP or project-relative base is incorrect");
const types = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff": "font/woff", ".woff2": "font/woff2" };
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://localhost");
    if (!url.pathname.startsWith(base)) { response.writeHead(404); response.end(); return; }
    const relative = decodeURIComponent(url.pathname.slice(base.length)) || "index.html";
    const file = path.resolve(dist, relative);
    if (!file.startsWith(dist + path.sep)) { response.writeHead(404); response.end(); return; }
    const bytes = await readFile(file);
    response.writeHead(200, { "Content-Type": types[path.extname(file)] ?? "text/plain", "Cache-Control": "no-store" }); response.end(bytes);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
const states = [], errors = [], apiRequests = [], external = [], missing = [];
try {
  browser = await chromium.launch({ ...(process.env.SYNDRA_BROWSER_CHANNEL ? { channel: process.env.SYNDRA_BROWSER_CHANNEL } : {}), headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", hasTouch: true });
  context.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) apiRequests.push(url.pathname);
    if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) external.push(url.origin);
  });
  context.on("response", (response) => { if (response.status() >= 400) missing.push(new URL(response.url()).pathname); });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.name));
  async function check(state) {
    await page.evaluate(await readFile(path.join(root, "node_modules/axe-core/axe.min.js"), "utf8"));
    const result = await page.evaluate(async () => globalThis.axe.run(globalThis.document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } }));
    states.push({ state, violations: result.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) })) });
    if (await page.evaluate(() => globalThis.document.documentElement.scrollWidth > globalThis.innerWidth + 1)) throw new Error(`Overflow in ${state}`);
    await page.screenshot({ path: path.join(evidence, `${state}.png`), fullPage: true });
  }
  await page.goto(`${origin}${base}`);
  await page.getByRole("heading", { name: "Your intent. Made real.", exact: true }).waitFor();
  await page.waitForFunction(() => globalThis.document.querySelector("canvas")?.dataset.ready === "true");
  await page.evaluate(() => globalThis.document.fonts.ready);
  if (!await page.evaluate(() => globalThis.document.fonts.check('550 28px "Bricolage Grotesque"'))) throw new Error("Public display font did not load");
  await check("public-arrival-light");
  await page.getByRole("button", { name: "Rotate the ribbon", exact: true }).focus();
  await page.getByRole("button", { name: "Rotate the ribbon", exact: true }).press("ArrowRight");
  await page.getByRole("slider", { name: "Ribbon unfold", exact: true }).focus();
  await page.getByRole("slider", { name: "Ribbon unfold", exact: true }).press("End");
  await page.getByRole("button", { name: "Reset ribbon view", exact: true }).click();
  await page.getByRole("button", { name: "Explore how it works", exact: true }).click();
  await page.getByRole("button", { name: "Inspect illustrative artifact", exact: true }).click();
  await check("public-pieces-light");
  await page.getByRole("button", { name: /^02.*Boundary$/ }).click();
  await page.getByRole("button", { name: "Try the protected boundary", exact: true }).click();
  await page.locator(".scope-playground").getByRole("status").filter({ hasText: "cannot authorize" }).waitFor();
  await check("public-boundary-light");
  await page.getByRole("button", { name: /^03.*Evidence$/ }).click();
  await page.getByRole("slider", { name: "Reveal proposed change", exact: true }).focus();
  await page.getByRole("slider", { name: "Reveal proposed change", exact: true }).press("End");
  await check("public-evidence-light");
  await page.getByRole("button", { name: "Install locally", exact: true }).click();
  await page.getByText(/not a hosted executor/).waitFor();
  await page.getByRole("button", { name: "macOS / Linux", exact: true }).click();
  if (!(await page.getByLabel("Local installation commands").textContent()).includes(".venv/bin/syndra")) throw new Error("Public installation platform did not change");
  await check("public-install-light");
  await page.getByRole("button", { name: "Use dark appearance", exact: true }).click();
  await check("public-install-dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Back to the beginning", exact: true }).tap();
  await check("public-mobile-dark");
  await page.getByRole("button", { name: "Use light appearance", exact: true }).tap();
  await check("public-mobile-light");
  if (states.some((state) => state.violations.length) || errors.length || apiRequests.length || external.length || missing.length) throw new Error("Public showcase acceptance failed");
  await writeFile(path.join(evidence, "result.json"), JSON.stringify({ status: "passed", states, javascript_errors: errors.length, api_requests: apiRequests.length, external_requests: external.length, missing_assets: missing.length, live_provider_calls: 0 }, null, 2));
  console.log(`Public-only showcase passed ${states.length} accessibility states, no API or external requests.`);
} catch (error) {
  await writeFile(path.join(evidence, "result.json"), JSON.stringify({ status: "failed", states, errors, apiRequests, external, missing, failure_category: error.name }, null, 2));
  throw error;
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
