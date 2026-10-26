import { chromium, expect } from "@playwright/test";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import console from "node:console";
import { Buffer } from "node:buffer";
import { URL } from "node:url";
import { setTimeout, clearTimeout } from "node:timers";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const workspace = await mkdtemp(path.join(tmpdir(), "syndra-repair-"));
const output = path.join(root, ".tmp", "customer-repair-acceptance");
await mkdir(output, { recursive: true });
for (const args of [["init"], ["config", "user.name", "Syndra Offline Acceptance"], ["config", "user.email", "offline@example.invalid"]]) {
  if (spawnSync("git", args, { cwd: workspace, shell: false }).status !== 0) throw new Error("Disposable repository initialization failed.");
}
await writeFile(path.join(workspace, ".gitignore"), ".syndra/\n");
spawnSync("git", ["add", ".gitignore"], { cwd: workspace, shell: false });
spawnSync("git", ["commit", "-m", "Initialize owned customer repair fixture"], { cwd: workspace, shell: false });
const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^(SYNDRA_|AGENTBUS_|AZURE_|OPENAI_)/i.test(name)));
const code = "from agentbus.config import AgentBusConfig; from agentbus.control.server import serve; import sys; c=AgentBusConfig(workspace_dir=sys.argv[1],provider_name='deterministic',enable_provider_fallback=False,state_dir=sys.argv[1]+'/.syndra'); serve(config=c,json_ready=True,port=0,registry_path=sys.argv[1]+'/.syndra/daemons.json',idle_timeout=300,log_level='error')";
const daemon = spawn(python, ["-c", code, workspace], { cwd: workspace, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: { ...environment, PYTHONPATH: "", ALL_PROXY: "http://127.0.0.1:9", HTTP_PROXY: "http://127.0.0.1:9", HTTPS_PROXY: "http://127.0.0.1:9", NO_PROXY: "127.0.0.1,localhost,::1" } });
daemon.stderr.on("data", () => { /* Do not persist launch material. */ });
const accessibility = [];
const external = new Set();
const errors = [];
let browser;
let page;
try {
  const ready = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("Owned daemon readiness timeout.")), 30_000);
    daemon.once("error", () => { clearTimeout(timer); reject(new Error("Owned daemon did not start.")); });
    daemon.once("exit", () => { clearTimeout(timer); reject(new Error("Owned daemon exited before readiness.")); });
    daemon.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      if (buffer.length > 32768) { clearTimeout(timer); reject(new Error("Daemon handshake exceeded its limit.")); return; }
      if (buffer.includes("\n")) {
        clearTimeout(timer);
        try { resolve(JSON.parse(buffer.split("\n")[0])); } catch { reject(new Error("Daemon handshake was invalid.")); }
      }
    });
  });
  const origin = `http://127.0.0.1:${ready.port}`;
  async function api(resource, body) {
    const response = await globalThis.fetch(origin + "/api/v1" + resource, { method: body ? "POST" : "GET", headers: { Authorization: `Bearer ${ready.bearer_token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined, signal: globalThis.AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error(`Owned fixture API returned ${response.status}.`);
    return response.json();
  }
  const base = "/customer-repair";
  const launch = await api("/studio/launch", {});
  const allowedOrigins = new Set([origin, new URL(launch.launch_url).origin]);
  browser = await chromium.launch({ channel: process.env.SYNDRA_BROWSER_CHANNEL ?? "chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", colorScheme: "light", acceptDownloads: true });
  context.on("request", (request) => { const url = new URL(request.url()); if (["http:", "https:"].includes(url.protocol) && !allowedOrigins.has(url.origin)) external.add(url.origin); });
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push({ name: error.name }));
  await page.goto(launch.launch_url);
  await page.getByRole("link", { name: "Customer repair", exact: true }).click();
  await page.getByRole("heading", { name: "The code is fixed. Make the people whole." }).waitFor();
  const axe = await readFile(path.join(root, "studio/node_modules/axe-core/axe.min.js"), "utf8");
  async function inspect(state) {
    await page.evaluate(axe);
    const results = await page.evaluate(async () => globalThis.axe.run(globalThis.document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } }));
    const violations = results.violations.map((entry) => ({ id: entry.id, impact: entry.impact, nodes: entry.nodes.map((node) => ({ target: node.target, failure: node.failureSummary })) }));
    accessibility.push({ state, violations });
    await writeFile(path.join(output, "accessibility.json"), JSON.stringify(accessibility, null, 2));
    await page.screenshot({ path: path.join(output, `${state}.png`), fullPage: true });
    if (violations.length) throw new Error(`Accessibility failure in ${state}.`);
    if (await page.evaluate(() => globalThis.document.documentElement.scrollWidth > globalThis.innerWidth + 1 || [...globalThis.document.querySelectorAll(".folio-paper")].some((element) => element.scrollWidth > element.clientWidth + 1))) throw new Error(`Horizontal overflow in ${state}.`);
  }
  async function appearance(theme) {
    await page.getByRole("link", { name: "Workspace", exact: true }).click();
    await page.getByLabel("Appearance").selectOption(theme);
    await page.getByRole("button", { name: "Return to run", exact: true }).click();
  }
  await inspect("entry-light");
  await page.setViewportSize({ width: 320, height: 740 });
  await appearance("dark");
  await inspect("entry-mobile-dark");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await appearance("light");
  await page.getByRole("button", { name: "Try the working demo", exact: true }).click();
  await page.getByRole("heading", { name: "Paid, but still waiting at the door", exact: true }).waitFor();
  const caseId = /#\/repair\/([a-f0-9]{32})/.exec(page.url())[1];
  await inspect("code-fixed-history-broken");
  await page.getByRole("button", { name: "Find affected records", exact: true }).click();
  await expect(page.locator(".repair-counts .state-repairable strong")).toHaveText("65");
  const map = page.getByRole("grid");
  await map.getByRole("button").first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(map.getByRole("button").nth(1)).toBeFocused();
  await expect(map.locator('button[tabindex="0"]')).toHaveCount(1);
  await map.getByRole("button").first().focus();
  await page.getByRole("button", { name: "Read demo account", exact: true }).click();
  await page.getByText("Workspace access is still locked.", { exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Record status" }).selectOption("unresolved");
  await expect(page.locator(".repair-record-list article")).toHaveCount(10);
  await page.getByRole("searchbox", { name: "Search purchase records" }).fill("not-present");
  await page.getByText("No records match. Clear the filters to see the entire preview.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Clear record filters", exact: true }).click();
  await inspect("preview-light");
  await page.getByText("Try a change after preview", { exact: true }).click();
  await page.getByRole("button", { name: "Support fixes five accounts", exact: true }).click();
  await page.getByText("5 accounts were corrected by support.", { exact: false }).waitFor();
  let applies = 0;
  let applyFailure;
  const lostResponse = async (route) => {
    applies += 1;
    const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: `Bearer ${ready.bearer_token}` } });
    if (response.status() !== 200) {
      const error = await response.json();
      applyFailure = { status: response.status(), code: error.error?.code };
      await route.fulfill({ response });
      return;
    }
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "test_response_interrupted", message: "The response was interrupted after server completion.", retryable: false } }) });
  };
  await page.route("**/customer-repair/cases/*/apply", lostResponse);
  await page.getByRole("checkbox", { name: "I reviewed the selected corrections.", exact: false }).check();
  await page.getByRole("button", { name: "Apply 65 selected corrections", exact: true }).click();
  await page.getByText("The apply response is not confirmed.", { exact: true }).waitFor();
  if (applyFailure) throw new Error(`Owned correction returned ${applyFailure.status}: ${applyFailure.code}.`);
  await inspect("interrupted-response");
  await page.reload();
  await page.getByRole("button", { name: "Check saved result", exact: true }).click();
  await expect(page.locator(".repair-result-summary strong")).toHaveText("60 access corrections verified");
  if (applies !== 1) throw new Error("An apply request was automatically retried.");
  await page.unroute("**/customer-repair/cases/*/apply", lostResponse);
  await inspect("saved-result-light");
  await map.getByRole("button").first().focus();
  await page.getByRole("button", { name: "Read demo account", exact: true }).click();
  await page.getByText("Your workspace is unlocked.", { exact: true }).waitFor();
  await inspect("customer-account-unlocked");
  await appearance("dark");
  await page.setViewportSize({ width: 320, height: 740 });
  await inspect("saved-result-mobile-dark");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await appearance("light");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export this preview & results", exact: true }).click();
  const download = await downloadPromise;
  const packagePath = path.join(output, "repair-package.json");
  await download.saveAs(packagePath);
  const verified = spawnSync(python, ["-m", "syndra", "repair", "verify", packagePath, "--json"], { cwd: root, shell: false, encoding: "utf8", env: { ...environment, PYTHONPATH: "" } });
  if (verified.status !== 0 || !JSON.parse(verified.stdout).valid) throw new Error("Exported package consistency failed.");
  await page.getByRole("button", { name: "Preview current records", exact: true }).click();
  await expect(page.locator(".repair-counts .state-repairable strong")).toHaveText("0");
  await expect(page.locator(".repair-counts .state-already_correct strong")).toHaveText("80");
  await expect(page.locator(".repair-counts .state-unresolved strong")).toHaveText("10");
  await inspect("remaining-investigations");
  const saved = await api(`${base}/cases/${caseId}/batches`);
  if (saved.batches.length !== 1 || saved.batches[0].restored_count !== 60 || saved.batches[0].skipped_count !== 5) throw new Error("Saved result disagrees with the independent cohort expectation.");
  await page.getByRole("link", { name: "All customer cases", exact: true }).click();
  const template = (await api(`${base}/template`)).snapshot;
  const importedTitle = "Imported support case " + "W".repeat(100);
  template.title = importedTitle;
  template.purchases[0].account_id = "account-" + "x".repeat(60);
  template.coverage_complete = false;
  await page.getByLabel("Purchase snapshot JSON file").setInputFiles({ name: "owned-synthetic-records.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(template)) });
  await page.getByRole("heading", { name: importedTitle, exact: true }).waitFor();
  await page.getByRole("button", { name: "Find affected records", exact: true }).click();
  await page.getByText("Partial evidence.", { exact: true }).waitFor();
  await page.getByRole("heading", { name: "Take the proposed corrections with you.", exact: true }).waitFor();
  await expect(page.getByRole("button", { name: /^Apply \d+ selected corrections$/ })).toHaveCount(0);
  await inspect("imported-partial-evidence");
  await page.setViewportSize({ width: 320, height: 740 });
  await inspect("imported-long-title-mobile");
  await page.getByRole("link", { name: "All customer cases", exact: true }).click();
  await page.getByRole("heading", { name: "Saved cases", exact: true }).waitFor();
  await page.getByRole("link", { name: importedTitle, exact: false }).waitFor();
  await inspect("saved-case-long-title-mobile");
  await page.setViewportSize({ width: 1440, height: 1000 });
  const refundCase = (await api(`${base}/demo`, {})).case.summary.case_id;
  await page.goto(`${origin}/studio/#/repair/${refundCase}`);
  await page.getByRole("button", { name: "Find affected records", exact: true }).click();
  await page.getByText("Try a change after preview", { exact: true }).click();
  await page.getByRole("button", { name: "Refund the first purchase", exact: true }).click();
  await page.getByText("1 purchase was refunded.", { exact: false }).waitFor();
  await page.getByRole("checkbox", { name: "I reviewed the selected corrections.", exact: false }).check();
  await page.getByRole("button", { name: "Apply 65 selected corrections", exact: true }).click();
  await expect(page.locator(".repair-result-summary strong")).toHaveText("64 access corrections verified");
  const refundResult = (await api(`${base}/cases/${refundCase}/batches`)).batches[0];
  if (refundResult.results.find((row) => row.purchase_id === "purchase-001")?.outcome !== "ineligible") throw new Error("A later refund was not preserved.");
  await inspect("later-refund-preserved");
  if (external.size || errors.length) throw new Error("Unexpected external requests or browser errors.");
  const git = spawnSync("git", ["status", "--porcelain"], { cwd: workspace, shell: false, encoding: "utf8" });
  if (git.status !== 0 || git.stdout.trim()) throw new Error("Managed repair artifacts changed the repository working tree.");
  await writeFile(path.join(output, "result.json"), JSON.stringify({ status: "passed", cohort_records: 100, historical_missing_after_code_fix: 65, corrected: 60, preserved_manual_corrections: 5, unresolved_evidence: 10, preserved_refund: true, lost_response_reconciled: true, first_case_apply_requests: applies, accessibility_states: accessibility.length, accessibility_violations: 0, external_requests: external.size, browser_errors: errors.length, live_provider_calls: 0, git_working_tree_clean: true, export_consistency: true }, null, 2));
  console.log(`Customer repair acceptance passed: ${accessibility.length} accessibility states; real correction, interruption, restart, import, refund, and export checks passed.`);
} catch (error) {
  await writeFile(path.join(output, "result.json"), JSON.stringify({ status: "failed", message: String(error.message).replace(/https?:\/\/\S+/g, "[local URL omitted]").slice(0, 1000), accessibility_states: accessibility.length, browser_errors: errors }, null, 2));
  if (page) await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
  process.exitCode = 1;
  console.error("Customer repair acceptance failed. Inspect the local evidence directory.");
} finally {
  await browser?.close();
  if (daemon.exitCode === null) daemon.kill();
  await new Promise((resolve) => { if (daemon.exitCode !== null) resolve(); else { const timer = setTimeout(resolve, 3000); daemon.once("exit", () => { clearTimeout(timer); resolve(); }); } });
}
