import { chromium, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { URL } from "node:url";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const browser = await chromium.launch({ channel: process.env.SYNDRA_BROWSER_CHANNEL ?? "chrome", headless: true });
const external = [];
const errors = [];
const states = [];
const allowedOrigins = new Set([config.origin, new URL(config.launch).origin]);
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", colorScheme: "light" });
await context.route("**/*", async (route) => {
  const url = new URL(route.request().url());
  if (["http:", "https:"].includes(url.protocol) && !allowedOrigins.has(url.origin)) { external.push(url.origin); await route.abort(); }
  else await route.continue();
});
const page = await context.newPage();
page.on("pageerror", (error) => errors.push(error.name));
try {
  await page.goto(config.launch);
  await page.evaluate((caseId) => { globalThis.location.hash = `#/repair/${caseId}`; }, config.case_id);
  const region = page.getByRole("region", { name: "Connected application repair" });
  await region.getByRole("combobox", { name: "Application connection", exact: true }).selectOption("owned-reference");
  await expect(region.getByLabel("Maximum approved writes", { exact: true })).toHaveValue("1");
  await region.getByRole("button", { name: "Check target identity", exact: true }).click();
  await region.getByRole("button", { name: "Refresh authoritative evidence", exact: true }).click();
  const candidate = region.getByRole("checkbox", { name: "Select purchase-00000: eligible", exact: true });
  await candidate.check();
  await region.getByRole("checkbox", { name: /I trust this workspace/ }).check();
  await region.getByRole("button", { name: "Review one-record canary", exact: true }).click();
  await region.getByRole("heading", { name: "One account. One exact correction.", exact: true }).waitFor();
  const axe = await readFile(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
  async function inspect(name) {
    await page.evaluate(axe);
    const result = await page.evaluate(async () => globalThis.axe.run(globalThis.document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } }));
    const violations = result.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }));
    states.push({ name, violations });
    await writeFile(path.join(config.output, "accessibility.json"), JSON.stringify(states, null, 2));
    await page.screenshot({ path: path.join(config.output, `${name}.png`), fullPage: true });
    if (violations.length) throw new Error(`Accessibility failed: ${name}`);
    if (await page.evaluate(() => globalThis.document.documentElement.scrollWidth > globalThis.innerWidth + 1)) throw new Error(`Horizontal overflow: ${name}`);
  }
  await inspect("exact-plan-desktop");
  await region.getByRole("checkbox", { name: /I reviewed this exact target/ }).check();
  await region.getByRole("button", { name: "Approve this exact plan", exact: true }).click();
  let executions = 0;
  await page.route("**/customer-repair/plans/*/execute", async (route) => {
    executions += 1;
    const response = await route.fetch({ headers: { ...route.request().headers(), Origin: new URL(page.url()).origin, "Sec-Fetch-Site": "same-origin" } });
    if (!response.ok()) {
      const body = await response.json();
      await writeFile(path.join(config.output, "execute-error.json"), JSON.stringify({ status: response.status(), code: body.error?.code, message: body.error?.message }, null, 2));
      throw new Error("Owned execute request failed before its response was intentionally dropped.");
    }
    await route.abort();
  });
  await region.getByRole("button", { name: "Execute approved canary", exact: true }).click();
  await region.getByRole("alert").waitFor();
  await page.reload();
  await region.getByRole("heading", { name: "One account. One exact correction.", exact: true }).waitFor();
  await region.getByRole("button", { name: "Reconcile saved operations", exact: true }).click();
  await expect(region.locator(".repair-plan-review")).toContainText("access passed");
  if (executions !== 1) throw new Error("Reload/reconciliation duplicated the execute request.");
  await inspect("verified-after-lost-response");
  await page.setViewportSize({ width: 320, height: 740 });
  await inspect("verified-mobile");
  await page.getByRole("link", { name: "Workspace", exact: true }).click();
  await page.getByLabel("Appearance").selectOption("dark");
  await page.getByRole("button", { name: "Return to run", exact: true }).click();
  await inspect("verified-mobile-dark");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await inspect("verified-desktop-dark");
  if (external.length || errors.length) throw new Error("Unexpected external request or browser error.");
  await writeFile(path.join(config.output, "browser.json"), JSON.stringify({ ok: true, execute_requests: executions, accessibility_states: states.length, violations: 0, external_requests: external.length, browser_errors: errors.length }, null, 2));
} catch (error) {
  const message = String(error.message).replaceAll(config.launch, "[one-use-launch-url]").slice(0, 3000);
  await writeFile(path.join(config.output, "browser-failure.json"), JSON.stringify({ name: error.name, message }, null, 2));
  // Raw Playwright causes can contain launch/session material; retain only sanitized diagnostics.
  // eslint-disable-next-line preserve-caught-error
  throw new Error("Connected acceptance failed; inspect browser-failure.json.");
} finally { await browser.close(); }
