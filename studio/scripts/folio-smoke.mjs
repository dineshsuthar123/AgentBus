import { chromium } from "@playwright/test";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { URL } from "node:url";
import path from "node:path";
import process from "node:process";
import console from "node:console";
import { setTimeout, clearTimeout } from "node:timers";

const root = path.resolve(import.meta.dirname, "../..");
const python = process.env.SYNDRA_ACCEPTANCE_PYTHON ?? path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const installed = Boolean(process.env.SYNDRA_ACCEPTANCE_PYTHON);
if (installed) {
  const origin = spawnSync(python, ["-I", "-c", "import syndra; from pathlib import Path; import sys; p=Path(syndra.__file__).resolve(); assert p.is_relative_to(Path(sys.prefix).resolve()), 'Syndra is not installed in the acceptance environment'; print(p)"], { cwd: tmpdir(), shell: false, encoding: "utf8" });
  if (origin.status !== 0) throw new Error("Installed acceptance requires a fresh interpreter with Syndra under its own prefix, not a source checkout");
}
const workspace = await mkdtemp(path.join(tmpdir(), "syndra-folio-"));
const output = path.join(root, ".tmp", process.env.SYNDRA_ACCEPTANCE_EVIDENCE ?? "folio-evidence");
await mkdir(output, { recursive: true });
await writeFile(path.join(output, "result.json"), JSON.stringify({ acceptance_status: "running", installed_package: installed, live_provider_calls: 0 }, null, 2));
await writeFile(path.join(output, "browser-errors.json"), "[]\n");
for (const args of [["init"], ["config", "user.name", "Syndra Offline Acceptance"], ["config", "user.email", "offline@example.invalid"]]) {
  const result = spawnSync("git", args, { cwd: workspace, shell: false });
  if (result.status !== 0) throw new Error("Disposable repository initialization failed");
}
await writeFile(path.join(workspace, ".gitignore"), ".syndra/\n__pycache__/\n.pytest_cache/\n");
spawnSync("git", ["add", ".gitignore"], { cwd: workspace, shell: false });
spawnSync("git", ["commit", "-m", "Initialize offline fixture"], { cwd: workspace, shell: false });
const serverCode = "from agentbus.config import AgentBusConfig; from agentbus.control.server import serve; import sys; c=AgentBusConfig(workspace_dir=sys.argv[1],provider_name='deterministic',enable_provider_fallback=False,state_db=sys.argv[1]+'/.syndra/state.db'); serve(config=c,json_ready=True,port=0,registry_path=sys.argv[1]+'/.syndra/daemons.json',idle_timeout=300,log_level='error')";
const runtime = path.join(workspace, ".syndra");
await mkdir(runtime, { recursive: true });
const configPath = path.join(runtime, "acceptance.json");
await writeFile(configPath, JSON.stringify({ provider_name: "deterministic", enable_provider_fallback: false, state_db: path.join(runtime, "state.db"), runs_dir: path.join(runtime, "runs"), state_dir: runtime }));
const childEnvironment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^(SYNDRA_|AGENTBUS_|AZURE_|OPENAI_)/i.test(name)));
const daemon = spawn(python, installed ? ["-m", "syndra.cli", "studio", "--config", configPath, "--workspace", workspace, "--port", "0", "--no-open", "--registry-path", path.join(runtime, "daemons.json")] : ["-c", serverCode, workspace], { cwd: workspace, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: { ...childEnvironment, PYTHONPATH: "" } });
daemon.stderr.on("data", () => { /* Drain diagnostics without recording launch material or private configuration. */ });
let browser;
let page;
const accessibility = [];
const browserErrors = [];
const rendering = {};
const externalRequests = new Set();
function diagnose(context) {
  context.on("page", (client) => client.on("pageerror", (error) => {
    browserErrors.push({ name: error.name, react_code: /Minified React error #(\d+)/.exec(error.message)?.[1], frames: [...(error.stack ?? "").matchAll(/http:\/\/127\.0\.0\.1:\d+\/studio\/assets\/[a-zA-Z0-9_-]+\.js:\d+:\d+/g)].map((frame) => frame[0]).slice(0, 24) });
  }));
}
try {
  const ready = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("Daemon readiness timeout")), 30_000);
    daemon.on("error", reject);
    daemon.on("exit", () => { clearTimeout(timer); reject(new Error("Daemon exited before readiness")); });
    daemon.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      if (buffer.length > 32768) { clearTimeout(timer); reject(new Error("Daemon startup output exceeded its bound")); return; }
      if (installed) {
        const studio = /Syndra Studio: (http:\/\/127\.0\.0\.1:\d+)\/studio\//.exec(buffer);
        const bootstrap = /Open within 30 seconds: (http:\/\/127\.0\.0\.1:\d+\/)/.exec(buffer);
        if (studio && bootstrap) { clearTimeout(timer); resolve({ origin: studio[1], launch_url: bootstrap[1] }); }
      } else if (buffer.includes("\n")) {
        clearTimeout(timer);
        try { resolve(JSON.parse(buffer.split("\n")[0])); } catch { reject(new Error("Invalid handshake")); }
      }
    });
  });
  const origin = ready.origin ?? `http://127.0.0.1:${ready.port}`;
  async function api(url, body) {
    if (installed) return page.evaluate(async ({ url, body }) => {
      const response = await globalThis.fetch(url, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined, signal: globalThis.AbortSignal.timeout(15_000) });
      if (!response.ok) throw new Error(`Control request failed: ${response.status}`);
      return response.json();
    }, { url, body });
    const response = await globalThis.fetch(origin + url, {
      method: body ? "POST" : "GET",
      headers: { Authorization: `Bearer ${ready.bearer_token}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: globalThis.AbortSignal.timeout(15_000)
    });
    if (!response.ok) throw new Error(`Control request failed: ${response.status}`);
    return response.json();
  }
  browser = await chromium.launch({ ...(process.env.SYNDRA_BROWSER_CHANNEL ? { channel: process.env.SYNDRA_BROWSER_CHANNEL } : {}), headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "light", reducedMotion: "reduce" });
  const allowedOrigins = new Set([origin]);
  context.on("request", (request) => { const url = new URL(request.url()); if (["http:", "https:"].includes(url.protocol) && !allowedOrigins.has(url.origin)) externalRequests.add(url.origin); });
  diagnose(context);
  page = await context.newPage();
  const launch = installed ? ready : await api("/api/v1/studio/launch", {});
  allowedOrigins.add(new URL(launch.launch_url).origin);
  await page.goto(launch.launch_url);
  await page.getByRole("heading", { name: "All runs", exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "empty-light.png"), fullPage: true });
  async function checkAccessibility(state, target = page) {
    await target.evaluate(await readFile(path.join(root, "studio/node_modules/axe-core/axe.min.js"), "utf8"));
    const result = await target.evaluate(async () => globalThis.axe.run(globalThis.document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] } }));
    accessibility.push({ state, violations: result.violations.map((violation) => ({ id: violation.id, impact: violation.impact, nodes: violation.nodes.map((node) => ({ target: node.target, failure: node.failureSummary })) })) });
    await writeFile(path.join(output, "accessibility.json"), JSON.stringify(accessibility, null, 2));
    if (await target.evaluate(() => globalThis.document.documentElement.scrollWidth > globalThis.innerWidth + 1 || [...globalThis.document.querySelectorAll(".folio-paper")].some((element) => element.scrollWidth > element.clientWidth + 1))) throw new Error(`Page-wide horizontal overflow in ${state}`);
    if (await target.evaluate(() => {
      const annotation = globalThis.document.querySelector(".sculpture-annotation");
      if (!annotation || !annotation.getClientRects().length) return false;
      const label = annotation.getBoundingClientRect();
      return [...globalThis.document.querySelectorAll(".workshop-sculpture .engine-controls button")].some((button) => {
        const rect = button.getBoundingClientRect();
        return label.left < rect.right && label.right > rect.left && label.top < rect.bottom && label.bottom > rect.top;
      });
    })) throw new Error(`Workshop annotation obscured its controls in ${state}`);
  }
  async function appearance(theme) {
    await page.getByRole("link", { name: "Workspace", exact: true }).click();
    await page.getByRole("heading", { name: "Workspace", exact: true }).waitFor();
    await page.getByLabel("Appearance").selectOption(theme);
    await page.getByRole("button", { name: "Return to run", exact: true }).click();
  }
  await checkAccessibility("empty-light");
  const storyWrites = [];
  const recordStoryWrite = (request) => { if (new URL(request.url()).pathname.startsWith("/api/") && !["GET", "HEAD"].includes(request.method())) storyWrites.push(request.method()); };
  page.on("request", recordStoryWrite);
  await page.getByRole("link", { name: "Explore Syndra", exact: true }).click();
  await page.getByRole("heading", { name: "Your intent. Made real.", exact: true }).waitFor();
  await page.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.ready === "true");
  rendering.workshop_engine = await page.locator(".workshop-sculpture canvas").getAttribute("data-engine");
  await page.screenshot({ path: path.join(output, "workshop-arrival-light.png"), fullPage: true });
  await checkAccessibility("workshop-arrival-light");
  const ribbonGrab = page.getByRole("button", { name: "Rotate the ribbon", exact: true });
  await ribbonGrab.focus();
  await ribbonGrab.press("ArrowRight");
  if (rendering.workshop_engine === "webgl") await page.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.orbit === "0.150,0.000");
  await page.getByRole("button", { name: "Reset ribbon view", exact: true }).click();
  const grabBounds = await ribbonGrab.boundingBox();
  if (!grabBounds) throw new Error("Ribbon has no direct manipulation surface");
  await page.mouse.move(grabBounds.x + grabBounds.width * .4, grabBounds.y + grabBounds.height * .4);
  await page.mouse.down();
  await page.mouse.move(grabBounds.x + grabBounds.width * .4 + 90, grabBounds.y + grabBounds.height * .4 + 30, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture .ribbon-core")?.dataset.grabbing === "false");
  if (rendering.workshop_engine === "webgl") await page.waitForFunction(() => Number(globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.orbit?.split(",")[0]) > .4);
  rendering.ribbon_drag_and_keyboard = true;
  const fold = page.getByRole("slider", { name: "Ribbon unfold", exact: true });
  await fold.focus(); await fold.press("Home"); await fold.press("End");
  if (rendering.workshop_engine === "webgl") await page.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.unfold === "1.000");
  await page.screenshot({ path: path.join(output, "workshop-unfold-hands-on-light.png"), fullPage: true });
  await checkAccessibility("workshop-unfold-hands-on-light");
  await fold.press("Home");
  if (rendering.workshop_engine === "webgl") await page.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.unfold === "0.000");
  rendering.ribbon_scrub = true;
  await page.getByRole("button", { name: "Reset ribbon view", exact: true }).click();
  await page.getByRole("button", { name: "Explore how it works", exact: true }).click();
  await page.getByRole("button", { name: "Refactor", exact: true }).click();
  await page.getByText("Untangle the parser without changing its public API.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Inspect illustrative artifact", exact: true }).click();
  await page.waitForFunction(() => globalThis.document.querySelector(".sculpture-annotation span")?.textContent === "THE ARTIFACT");
  if (rendering.workshop_engine === "webgl") {
    const linked = await page.locator(".workshop-sculpture canvas").evaluate((canvas) => { const gl = canvas.getContext("webgl"); const program = gl.getParameter(gl.CURRENT_PROGRAM); return gl.getUniform(program, gl.getUniformLocation(program, "selection")) === 4 && gl.getUniform(program, gl.getUniformLocation(program, "inspection")) === 1; });
    if (!linked) throw new Error("Illustrative piece did not select its ribbon inspection region");
  }
  rendering.example_piece_linkage = true;
  await page.screenshot({ path: path.join(output, "workshop-pieces-light.png"), fullPage: true });
  await checkAccessibility("workshop-pieces-light");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.getByRole("button", { name: "Play illustrative walkthrough", exact: true }).click();
  await page.waitForFunction(() => { const workbench = globalThis.document.querySelector(".example-workbench"); return workbench?.dataset.piece === "2" && workbench?.dataset.playing === "false"; });
  rendering.finite_walkthrough = true;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: /Boundary/, exact: false }).click();
  await page.getByText("Power meets a boundary.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Animate the ribbon", exact: true }).waitFor();
  if (await page.getByRole("button", { name: "Animate the ribbon", exact: true }).isEnabled()) throw new Error("Scope illustration did not settle its scene");
  await page.screenshot({ path: path.join(output, "workshop-boundary-light.png"), fullPage: true });
  await checkAccessibility("workshop-boundary-light");
  await page.getByRole("button", { name: "Try the protected boundary", exact: true }).click();
  await page.locator(".scope-playground").getByRole("status").filter({ hasText: "it cannot authorize a write" }).waitFor();
  await page.screenshot({ path: path.join(output, "workshop-protected-boundary-light.png"), fullPage: true });
  await checkAccessibility("workshop-protected-boundary-light");
  await page.getByRole("button", { name: "Return to example write area", exact: true }).click();
  rendering.scope_boundary = true;
  await page.getByRole("button", { name: /^03.*Evidence$/ }).click();
  const reveal = page.getByRole("slider", { name: "Reveal proposed change", exact: true });
  await reveal.focus(); await reveal.press("Home");
  if (await reveal.getAttribute("aria-valuetext") !== "0% of illustrative proposed line revealed") throw new Error("Source reveal did not expose the before endpoint");
  await reveal.press("End");
  if (await page.locator(".change-reveal").evaluate((element) => element.style.getPropertyValue("--change-reveal")) !== "100%") throw new Error("Source reveal did not expose the proposed endpoint");
  rendering.change_reveal = true;
  await page.screenshot({ path: path.join(output, "workshop-evidence-light.png"), fullPage: true });
  await checkAccessibility("workshop-evidence-light");
  await page.getByRole("button", { name: "Bring your own intent", exact: true }).click();
  await page.getByRole("link", { name: "Open a fresh task", exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "workshop-entry-light.png"), fullPage: true });
  await checkAccessibility("workshop-entry-light");
  await page.getByRole("button", { name: "Back to the beginning", exact: true }).click();
  if (await page.locator(".workshop-journey").getAttribute("data-act") !== "0") throw new Error("Workshop journey was not reversible");
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByRole("button", { name: /01.*Arrival/ }).click();
  await page.screenshot({ path: path.join(output, "workshop-mobile-light.png"), fullPage: true });
  await checkAccessibility("workshop-mobile-light");
  await page.getByRole("button", { name: "Explore how it works", exact: true }).click();
  await page.getByRole("button", { name: /^01.*Structure$/ }).click();
  await page.getByRole("button", { name: "Next illustrative piece", exact: true }).click();
  if (await page.getByRole("button", { name: "Inspect illustrative task", exact: true }).getAttribute("aria-pressed") !== "true") throw new Error("Mobile workbench transport did not change the active piece");
  await page.screenshot({ path: path.join(output, "workshop-example-mobile-light.png"), fullPage: true });
  await checkAccessibility("workshop-example-mobile-light");
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.off("request", recordStoryWrite);
  if (storyWrites.length) throw new Error("Illustrative workshop issued a control-plane mutation");
  rendering.workshop_local_only = true;
  const entryContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", colorScheme: "light" });
  diagnose(entryContext);
  entryContext.on("request", (request) => { const url = new URL(request.url()); if (["http:", "https:"].includes(url.protocol) && !allowedOrigins.has(url.origin)) externalRequests.add(url.origin); });
  const entryPage = await entryContext.newPage();
  await entryPage.goto(`${origin}/studio/#/runs/retained-run?view=source`);
  await entryPage.getByRole("heading", { name: "Your intent. Made real.", exact: true }).waitFor();
  await entryPage.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.ready === "true");
  await entryPage.screenshot({ path: path.join(output, "workshop-public-arrival-light.png"), fullPage: false });
  await checkAccessibility("workshop-public-arrival-light", entryPage);
  await entryPage.getByRole("button", { name: "Unfold the ribbon", exact: true }).click();
  if (await entryPage.getByRole("button", { name: "Unfold the ribbon", exact: true }).getAttribute("aria-pressed") !== "true") throw new Error("Ribbon control did not expose its unfolded state");
  await entryPage.getByRole("button", { name: "Use flat ribbon rendering", exact: true }).click();
  if (await entryPage.locator(".workshop-sculpture canvas").count()) throw new Error("Flat mode retained a GPU canvas");
  await entryPage.getByRole("button", { name: "Rotate the ribbon", exact: true }).focus();
  await entryPage.getByRole("button", { name: "Rotate the ribbon", exact: true }).press("ArrowLeft");
  if (await entryPage.locator(".ribbon-core").evaluate((element) => element.style.getPropertyValue("--ribbon-turn")) !== "-3.9deg") throw new Error("Flat ribbon did not retain keyboard orbit");
  rendering.flat_keyboard_orbit = true;
  await entryPage.getByRole("button", { name: "Rotate the ribbon", exact: true }).press("Home");
  await entryPage.screenshot({ path: path.join(output, "workshop-public-flat-light.png"), fullPage: false });
  await checkAccessibility("workshop-public-flat-light", entryPage);
  rendering.workshop_flat_unmounts = true;
  await entryPage.getByRole("button", { name: "Use flat ribbon rendering", exact: true }).click();
  await entryPage.waitForFunction(() => globalThis.document.querySelector(".workshop-sculpture canvas")?.dataset.ready === "true");
  await entryPage.getByRole("button", { name: "Unfold the ribbon", exact: true }).click();
  await entryPage.getByRole("button", { name: "Open Studio", exact: true }).click();
  if (new URL(entryPage.url()).hash !== "#/runs/retained-run?view=source") throw new Error("Direct session entry lost the durable deep link");
  await entryPage.screenshot({ path: path.join(output, "workshop-public-connect-light.png"), fullPage: false });
  await checkAccessibility("workshop-public-connect-light", entryPage);
  await entryPage.setViewportSize({ width: 320, height: 740 });
  await entryPage.evaluate(() => globalThis.scrollTo(0, 0));
  await entryPage.screenshot({ path: path.join(output, "workshop-public-mobile-arrival-light.png"), fullPage: false });
  await checkAccessibility("workshop-public-mobile-arrival-light", entryPage);
  await entryPage.getByRole("button", { name: /01.*Arrival/ }).click();
  await entryPage.screenshot({ path: path.join(output, "workshop-public-mobile-light.png"), fullPage: false });
  await checkAccessibility("workshop-public-mobile-light", entryPage);
  await entryContext.close();
  await page.getByRole("link", { name: "New task", exact: true }).click();
  await page.getByRole("button", { name: "Create calculator guided workspace", exact: true }).click();
  await page.getByText("New marker-owned guided repository created.", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "new-task-light.png"), fullPage: true });
  await checkAccessibility("new-task-light");
  const acceptedResponse = page.waitForResponse((response) => response.url() === `${origin}/api/v1/runs` && response.request().method() === "POST");
  await page.getByRole("button", { name: "Launch execution", exact: true }).click();
  const accepted = await (await acceptedResponse).json();
  await page.goto(`${origin}/studio/#/runs/${accepted.run_id}`);
  await page.getByRole("heading", { name: "Fix add() so the bundled unittest passes without changing the test.", exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "run-light.png"), fullPage: true });
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await page.screenshot({ path: path.join(output, "plan-light.png"), fullPage: true });
  await page.getByRole("link", { name: "Workspace", exact: true }).click();
  await page.getByRole("heading", { name: "Workspace", exact: true }).waitFor();
  await page.getByLabel("Appearance").selectOption("dark");
  await page.screenshot({ path: path.join(output, "connections-dark.png"), fullPage: true });
  await checkAccessibility("connections-dark");
  await page.getByRole("button", { name: "Return to run", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, "run-mobile-dark.png"), fullPage: true });
  const deadline = Date.now() + 120_000;
  let status;
  while (Date.now() < deadline) {
    status = await api(`/api/v1/runs/${accepted.run_id}`);
    if (["succeeded", "failed", "cancelled"].includes(status.status)) break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (status.status !== "succeeded") throw new Error(`Offline fixture did not succeed: ${status.status}`);
  if (status.verifier_status !== "passed" || status.reviewer_status !== "approved") throw new Error("Durable completion lacks verified/reviewed results");
  await page.getByRole("button", { name: "Refresh run", exact: true }).click();
  await page.locator(".run-command-title .status-signal").filter({ hasText: /succeeded/i }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Task", exact: true }).click();
  await page.getByRole("region", { name: "Persisted task register" }).waitFor();
  await appearance("light");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const object = page.locator(".execution-overview canvas");
  await object.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => ["webgl", "fallback"].includes(globalThis.document.querySelector(".execution-overview canvas")?.dataset.engine));
  await page.evaluate(() => globalThis.document.fonts.ready);
  rendering.font_loaded = await page.evaluate(() => globalThis.document.fonts.check('550 28px "Bricolage Grotesque"'));
  if (!rendering.font_loaded) throw new Error("Bundled display font did not load");
  rendering.engine = await object.getAttribute("data-engine");
  const twoFrames = () => page.evaluate(() => new Promise((resolve) => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve))));
  await twoFrames();
  if (rendering.engine === "webgl") {
    rendering.gl_error = await object.evaluate((canvas) => canvas.getContext("webgl").getError());
    rendering.density_bounded = await object.evaluate((canvas) => canvas.width <= Math.round(canvas.clientWidth * 1.6) && canvas.height <= Math.round(canvas.clientHeight * 1.6));
    if (rendering.gl_error !== 0 || !rendering.density_bounded) throw new Error("Inspection rendering exceeded its GPU bounds");
    const initiallyPaused = Number(await object.getAttribute("data-frames"));
    await twoFrames();
    if (Number(await object.getAttribute("data-frames")) !== initiallyPaused) throw new Error("Run object did not start paused");
    rendering.initially_paused = true;
    await page.getByRole("button", { name: "Animate inspection object", exact: true }).click();
    await page.waitForFunction((baseline) => Number(globalThis.document.querySelector(".execution-overview canvas")?.dataset.frames) > baseline + 2, initiallyPaused);
    await page.getByRole("button", { name: "Pause inspection object", exact: true }).click();
    const pausedFrames = Number(await object.getAttribute("data-frames"));
    await twoFrames();
    if (Number(await object.getAttribute("data-frames")) !== pausedFrames) throw new Error("Inspection object kept animating after pause");
    rendering.animation_and_pause = true;
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByRole("button", { name: "Animate inspection object", exact: true }).click();
    await twoFrames();
    const reducedFrames = Number(await object.getAttribute("data-frames"));
    await twoFrames();
    if (Number(await object.getAttribute("data-frames")) !== reducedFrames) throw new Error("Reduced motion still animated the inspection object");
    rendering.reduced_motion = true;
    await page.getByRole("button", { name: "Pause inspection object", exact: true }).click();
  }
  await page.getByRole("button", { name: "Separate inspection layers", exact: true }).click();
  if (await page.getByRole("button", { name: "Separate inspection layers", exact: true }).getAttribute("aria-pressed") !== "true") throw new Error("Layer control did not reflect its state");
  await page.screenshot({ path: path.join(output, "execution-object-light.png"), fullPage: true });
  await checkAccessibility("execution-object-light");
  await page.getByRole("button", { name: "Hide object", exact: true }).click();
  if (await object.count()) throw new Error("Hidden object retained a renderer");
  rendering.hide_unmounts = true;
  await page.getByRole("button", { name: "Show object", exact: true }).click();
  await page.getByRole("button", { name: "Animate inspection object", exact: true }).waitFor();
  await object.scrollIntoViewIfNeeded();
  if (rendering.engine === "webgl") {
    rendering.context_loss_supported = await object.evaluate((canvas) => { const extension = canvas.getContext("webgl")?.getExtension("WEBGL_lose_context"); if (!extension) return false; extension.loseContext(); return true; });
    if (rendering.context_loss_supported) { await page.waitForFunction(() => globalThis.document.querySelector(".execution-overview canvas")?.dataset.engine === "fallback"); rendering.context_loss_fallback = true; }
  }
  await page.screenshot({ path: path.join(output, "execution-fallback-light.png"), fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  await page.screenshot({ path: path.join(output, "execution-object-mobile-light.png"), fullPage: true });
  await checkAccessibility("execution-object-mobile-light");
  await page.getByRole("button", { name: "Checks", exact: true }).click();
  if (await object.count()) throw new Error("Artifact inspection retained a decorative renderer");
  rendering.artifact_unmounts = true;
  await appearance("dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".artifact-spread .context-inspector").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "checks-mobile-dark.png"), fullPage: true });
  await checkAccessibility("checks-mobile-dark");
  const storage = await page.evaluate(() => ({ local: { ...globalThis.localStorage }, session: { ...globalThis.sessionStorage } }));
  await writeFile(path.join(output, "layout.json"), JSON.stringify(await page.evaluate(() => ({ viewport: globalThis.innerHeight, height: globalThis.document.documentElement.scrollHeight,
    overflow: [...globalThis.document.querySelectorAll("body *")].filter((element) => element.getBoundingClientRect().bottom > globalThis.innerHeight + 300).slice(0, 15).map((element) => ({ tag: element.tagName, class: element.className, bottom: element.getBoundingClientRect().bottom })) })), null, 2));
  if (ready.bearer_token && JSON.stringify(storage).includes(ready.bearer_token)) throw new Error("Bearer credential leaked into browser storage");
  if (Object.keys(storage.local).some((key) => /token|credential|session/i.test(key)) || Object.keys(storage.session).length) throw new Error("Unexpected credential/session persistence in browser storage");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByText("Mandatory final review", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "review-dark.png"), fullPage: true });
  await checkAccessibility("review-dark");
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await page.getByRole("region", { name: "Source lens" }).waitFor();
  await page.locator(".virtual-diff-line.diff-add code").filter({ hasText: "return left + right" }).waitFor();
  await page.screenshot({ path: path.join(output, "changes-dark.png"), fullPage: true });
  await checkAccessibility("changes-dark");
  await appearance("light");
  await page.screenshot({ path: path.join(output, "changes-light.png"), fullPage: true });
  await checkAccessibility("changes-light");
  await appearance("dark");
  const zoomContext = await browser.newContext({ viewport: { width: 720, height: 500 }, deviceScaleFactor: 2, storageState: await context.storageState(), reducedMotion: "reduce" });
  diagnose(zoomContext);
  const zoomPage = await zoomContext.newPage();
  await zoomPage.goto(`${origin}/studio/#/runs/${accepted.run_id}?view=source`);
  await zoomPage.getByRole("region", { name: "Source lens" }).waitFor();
  if (await zoomPage.evaluate(() => globalThis.document.documentElement.scrollWidth > globalThis.innerWidth + 1)) throw new Error("200-percent-equivalent reflow creates page-wide horizontal scrolling");
  await zoomPage.screenshot({ path: path.join(output, "changes-200-percent-reflow-dark.png"), fullPage: true });
  await checkAccessibility("changes-200-percent-reflow-dark", zoomPage);
  await zoomContext.close();
  await page.setViewportSize({ width: 1440, height: 700 });
  const readingPosition = await page.locator(".folio-paper").evaluate((element) => {
    element.scrollTop = 280;
    element.dispatchEvent(new globalThis.Event("scroll"));
    return element.scrollTop;
  });
  if (readingPosition <= 0) throw new Error("Reading-position acceptance requires a real overflowing artifact");
  await page.getByRole("link", { name: "New task", exact: true }).click();
  await page.getByLabel("What should Syndra change?").fill("Unsubmitted acceptance draft: preserve while inspecting work.");
  await page.getByRole("link", { name: "Explore Syndra", exact: true }).click();
  await page.getByRole("heading", { name: "Your intent. Made real.", exact: true }).waitFor();
  await appearance("dark");
  await page.screenshot({ path: path.join(output, "workshop-arrival-dark.png"), fullPage: true });
  await checkAccessibility("workshop-arrival-dark");
  await page.getByRole("button", { name: "Explore how it works", exact: true }).click();
  await page.getByRole("button", { name: /^03.*Evidence$/ }).click();
  await page.screenshot({ path: path.join(output, "workshop-evidence-dark.png"), fullPage: true });
  await checkAccessibility("workshop-evidence-dark");
  await page.getByRole("link", { name: "New task", exact: true }).click();
  // The preserved draft is mounted while hidden, so its value alone cannot prove navigation finished.
  await page.getByLabel("What should Syndra change?").waitFor({ state: "visible" });
  if (await page.getByLabel("What should Syndra change?").inputValue() !== "Unsubmitted acceptance draft: preserve while inspecting work.") throw new Error("Workshop navigation discarded the task draft");
  await page.locator(".workshop-sculpture canvas").waitFor({ state: "detached" });
  await page.getByLabel("Absolute workspace").fill(accepted.workspace);
  await page.getByRole("button", { name: /Single loop/ }).click();
  await page.screenshot({ path: path.join(output, "new-task-dark.png"), fullPage: true });
  await checkAccessibility("new-task-dark");
  await page.locator(".open-run-shelf .run-object a").filter({ hasText: accepted.run_id.slice(0, 8) }).click();
  await page.getByRole("region", { name: "Source lens" }).waitFor();
  await page.waitForFunction((top) => Math.abs(globalThis.document.querySelector(".folio-paper").scrollTop - top) <= 1, readingPosition);
  if (!page.url().includes("view=source")) throw new Error("Shelf selection lost the run's inspected section");
  await page.getByRole("link", { name: "New task", exact: true }).click();
  await page.getByLabel("What should Syndra change?").waitFor({ state: "visible" });
  if (await page.getByLabel("What should Syndra change?").inputValue() !== "Unsubmitted acceptance draft: preserve while inspecting work.") throw new Error("Run navigation discarded the task draft");
  if (await page.getByLabel("Absolute workspace").inputValue() !== accepted.workspace || !await page.getByRole("button", { name: /Single loop/ }).evaluate((element) => element.classList.contains("is-active"))) throw new Error("Run navigation discarded draft execution settings");
  await page.locator(".open-run-shelf .run-object a").filter({ hasText: accepted.run_id.slice(0, 8) }).click();
  await page.getByRole("region", { name: "Source lens" }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await page.getByRole("heading", { name: "Evidence management", exact: true }).waitFor();
  const archiveDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export trace archive", exact: true }).click();
  const exportedPath = path.join(output, "offline-trace.zip");
  await (await archiveDownload).saveAs(exportedPath);
  await page.getByLabel("Import trace archive (10 MiB maximum)").setInputFiles(exportedPath);
  await page.getByRole("button", { name: "Validate and import archive", exact: true }).click();
  await page.getByText(/Archive accepted:/).waitFor();
  await page.getByRole("button", { name: "Compare evidence", exact: true }).click();
  await page.getByText(/unchanged, 0 changed, 0 added and 0 removed spans/).waitFor();
  await page.screenshot({ path: path.join(output, "evidence-dark.png"), fullPage: true });
  await checkAccessibility("evidence-dark");
  await page.getByRole("link", { name: "Workspace", exact: true }).click();
  await page.getByRole("button", { name: "Repository", exact: true }).click();
  await page.getByText("I trust this repository for static local indexing. No repository code, model or shell is executed.", { exact: true }).click();
  await page.getByRole("button", { name: "Build index", exact: true }).click();
  await page.locator(".workspace-facility [role=status] strong").filter({ hasText: /^current$/ }).waitFor();
  await page.getByLabel("Search indexed files and symbols").fill("add");
  await page.getByRole("button", { name: "Search repository", exact: true }).click();
  await page.locator(".repository-results li").first().waitFor();
  await page.screenshot({ path: path.join(output, "repository-dark.png"), fullPage: true });
  await checkAccessibility("repository-dark");
  await page.getByRole("button", { name: "Tools", exact: true }).click();
  await page.getByText(/managed tools\. Availability is not authorization/).waitFor();
  await page.screenshot({ path: path.join(output, "tools-dark.png"), fullPage: true });
  await checkAccessibility("tools-dark");
  await page.getByRole("button", { name: "Return to run", exact: true }).click();
  const failedRun = await api("/api/v1/runs", { task: "Acceptance-only injected planner failure; no live model", workspace,
    provider: "deterministic", workflow: "multi", durable: true, parallel: false, max_workers: 1,
    retry_limit: 0, fallback_enabled: false, deterministic: { profile: "python-calculator", failure_calls: [1, 2, 3], failure_roles: ["planner"] } });
  const failureDeadline = Date.now() + 30_000;
  let failureStatus;
  while (Date.now() < failureDeadline) {
    failureStatus = await api(`/api/v1/runs/${failedRun.run_id}`);
    if (["failed", "cancelled", "succeeded"].includes(failureStatus.status)) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (failureStatus?.status !== "failed") throw new Error("Controlled planner failure did not remain a failed run");
  await page.goto(`${origin}/studio/#/runs/${failedRun.run_id}?view=verifier`);
  await page.getByRole("heading", { name: "Acceptance-only injected planner failure; no live model", exact: true }).waitFor();
  await page.locator(".run-command-title .status-signal").filter({ hasText: /failed/i }).waitFor();
  await page.screenshot({ path: path.join(output, "failed-checks-dark.png"), fullPage: true });
  await checkAccessibility("failed-checks-dark");
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.screenshot({ path: path.join(output, "failed-checks-tablet-dark.png"), fullPage: true });
  await checkAccessibility("failed-checks-tablet-dark");
  await page.setViewportSize({ width: 320, height: 740 });
  await page.screenshot({ path: path.join(output, "failed-checks-mobile-dark.png"), fullPage: true });
  await checkAccessibility("failed-checks-mobile-dark");
  const historySeed = "from agentbus.execution.state_store import StateStore\nfrom agentbus.execution.models import RunRecord, RunStatus\nfrom datetime import datetime, timedelta, timezone\nimport sys\ns=StateStore(sys.argv[1])\nfor i in range(121):\n t=datetime(2025,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)\n s.create_run(RunRecord(run_id=f'acceptance-history-{i:03}',original_task=f'Acceptance history fixture {i:03}: persisted only, not executed',model='deterministic-history-fixture',workspace=sys.argv[2],status=RunStatus.FAILED,failure_reason='History-only acceptance fixture; no execution or completion claimed',created_at=t,updated_at=t,planner_output={'goal':'History fixture','steps':[]},graph_data={'version':1,'tasks':[]}))";
  const seeded = spawnSync(python, ["-c", historySeed, path.join(runtime, "state.db"), workspace], { cwd: workspace, shell: false, encoding: "utf8", env: { ...childEnvironment, PYTHONPATH: "" } });
  if (seeded.status !== 0) throw new Error("Bounded persisted-history fixture initialization failed");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("link", { name: "All runs", exact: true }).click();
  await page.getByRole("heading", { name: "All runs", exact: true }).waitFor();
  await page.getByRole("button", { name: "Refresh history", exact: true }).click();
  await page.waitForFunction(() => globalThis.document.querySelectorAll(".history-page .table-row").length === 50);
  await page.getByRole("button", { name: "Load more runs", exact: true }).click();
  await page.waitForFunction(() => globalThis.document.querySelectorAll(".history-page .table-row").length === 100);
  await page.getByRole("button", { name: "Load more runs", exact: true }).click();
  await page.getByText("End of history", { exact: true }).waitFor();
  const historyIds = await page.locator(".history-page .table-row").evaluateAll((rows) => rows.map((row) => row.getAttribute("href")));
  if (historyIds.length !== 123 || new Set(historyIds).size !== 123) throw new Error("Cursor history lost or duplicated persisted runs");
  const ledgerPosition = await page.locator(".folio-paper").evaluate((element) => { element.scrollTop = element.scrollHeight - element.clientHeight; element.dispatchEvent(new globalThis.Event("scroll")); return element.scrollTop; });
  await page.getByRole("link", { name: /Acceptance history fixture 000:/ }).click();
  await page.getByRole("heading", { name: /Acceptance history fixture 000:/ }).waitFor();
  await page.goBack();
  await page.getByText("End of history", { exact: true }).waitFor();
  if (await page.locator(".history-page .table-row").count() !== 123) throw new Error("Inspecting a run discarded loaded ledger pages");
  await writeFile(path.join(output, "ledger-reading.json"), JSON.stringify({ expected: ledgerPosition, ...await page.locator(".folio-paper").evaluate((element) => ({ actual: element.scrollTop, height: element.scrollHeight, viewport: element.clientHeight })) }, null, 2));
  await page.waitForFunction((top) => Math.abs(globalThis.document.querySelector(".folio-paper").scrollTop - top) <= 1, ledgerPosition);
  await page.getByLabel("Search history").fill("Acceptance history fixture 000");
  await page.getByLabel("Status", { exact: true }).selectOption("failed");
  await page.getByRole("link", { name: /Acceptance history fixture 000:/ }).waitFor();
  await page.getByRole("link", { name: "Workspace", exact: true }).click();
  if (!page.url().includes("search=Acceptance+history+fixture+000")) throw new Error("Workspace drawer discarded the live ledger filter");
  await page.getByRole("button", { name: "Return to run", exact: true }).click();
  await page.getByRole("link", { name: /Acceptance history fixture 000:/ }).click();
  await page.getByRole("heading", { name: /Acceptance history fixture 000:/ }).waitFor();
  await page.goBack();
  await page.getByRole("heading", { name: "All runs", exact: true }).waitFor();
  if (await page.getByLabel("Search history").inputValue() !== "Acceptance history fixture 000" || await page.getByLabel("Status", { exact: true }).inputValue() !== "failed") throw new Error("Back navigation lost ledger filters");
  await page.reload();
  await page.getByRole("link", { name: /Acceptance history fixture 000:/ }).waitFor();
  await page.screenshot({ path: path.join(output, "ledger-dark.png"), fullPage: true });
  await checkAccessibility("ledger-dark");
  await page.goto(`${origin}/studio/#/runs/${accepted.run_id}?view=evidence`);
  await page.getByRole("heading", { name: "Evidence management", exact: true }).waitFor();
  await page.getByRole("link", { name: "Syndra Studio", exact: true }).click();
  // The old heading can survive until hashchange and async run validation finish.
  await page.waitForURL((url) => url.hash === `#/runs/${accepted.run_id}?view=evidence`);
  await page.getByRole("heading", { name: "Evidence management", exact: true }).waitFor();
  if (!page.url().includes(`/runs/${accepted.run_id}?view=evidence`)) throw new Error("Root navigation did not resume the last validated folio");
  const selectedUrl = page.url();
  await page.keyboard.press("Tab");
  await page.getByRole("link", { name: "Skip to work", exact: true }).focus();
  await page.keyboard.press("Enter");
  if (page.url() !== selectedUrl || !await page.locator(".folio-paper").evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Keyboard skip changed the selected folio instead of focusing it");
  await page.getByRole("link", { name: "Workspace", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Workspace", exact: true }).waitFor();
  if (!await page.getByRole("heading", { name: "Workspace", exact: true }).evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Workspace drawer did not receive keyboard focus");
  await page.keyboard.press("Escape");
  await page.getByRole("heading", { name: "Workspace", exact: true }).waitFor({ state: "hidden" });
  if (!await page.getByRole("link", { name: "Workspace", exact: true }).evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Workspace drawer did not restore focus to its edge");
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Search Studio commands", exact: true }).waitFor();
  await page.keyboard.press("Shift+Tab");
  if (!await page.getByRole("option", { name: "Runtime diagnostics", exact: true }).evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Modal command menu allowed focus to leave its controls");
  await page.keyboard.press("Tab");
  if (!await page.getByRole("combobox", { name: "Search Studio commands", exact: true }).evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Modal command menu did not wrap focus");
  await page.screenshot({ path: path.join(output, "commands-dark.png"), fullPage: true });
  await checkAccessibility("commands-dark");
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog", { name: "Studio command palette", exact: true }).waitFor({ state: "hidden" });
  if (!await page.getByRole("link", { name: "Workspace", exact: true }).evaluate((element) => globalThis.document.activeElement === element)) throw new Error("Keyboard command toggle lost its original focus");
  const deletionTarget = path.join(accepted.workspace, "delete_me.txt");
  await writeFile(deletionTarget, "deterministic deletion target\n");
  async function requestDeletion() {
    const run = await api("/api/v1/runs", { task: "Acceptance-only exact approval to delete the owned disposable fixture", workspace: accepted.workspace,
      provider: "deterministic", workflow: "multi", durable: true, parallel: false, max_workers: 1,
      retry_limit: 0, fallback_enabled: false, deterministic: { profile: "tool-delete-approval" } });
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const response = await api(`/api/v1/runs/${run.run_id}/approvals`);
      const request = response.approvals.find((approval) => ["pending", "requested", "waiting_for_approval"].includes(approval.state));
      if (request) return { run, request };
      const status = await api(`/api/v1/runs/${run.run_id}`);
      if (["failed", "succeeded", "cancelled"].includes(status.status)) throw new Error(`Approval fixture reached ${status.status} without a permission request`);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("Exact approval fixture did not reach its policy gate");
  }
  const rejected = await requestDeletion();
  await page.goto(`${origin}/studio/#/runs/${rejected.run.run_id}?view=source`);
  await page.getByRole("button", { name: "Approve & continue", exact: true }).waitFor();
  await page.getByRole("heading", { name: rejected.request.requested_action, exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "approval-request-dark.png"), fullPage: true });
  await checkAccessibility("approval-request-dark");
  await appearance("light");
  await page.getByRole("heading", { name: rejected.request.requested_action, exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "approval-light.png"), fullPage: true });
  await checkAccessibility("approval-light");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.getByRole("button", { name: "Approve & continue", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "approval-mobile-light.png"), fullPage: true });
  await checkAccessibility("approval-mobile-light");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await appearance("dark");
  await page.getByText("Request scope and resource limits", { exact: true }).click();
  await page.getByText("Full persisted capability scopes", { exact: true }).click();
  if (!await page.locator(".approval-gate").evaluate((gate) => gate.getBoundingClientRect().bottom <= gate.closest(".context-inspector").getBoundingClientRect().bottom + 1)) throw new Error("Expanded approval escapes its artifact and overlaps later content");
  await page.getByRole("button", { name: "Reject", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "approval-dark.png"), fullPage: true });
  await checkAccessibility("approval-dark");
  const otherClient = await page.context().newPage();
  await otherClient.goto(`${origin}/studio/#/runs/${rejected.run.run_id}`);
  try {
    await otherClient.getByRole("button", { name: "Approve & continue", exact: true }).waitFor();
  } catch (error) {
    await otherClient.screenshot({ path: path.join(output, "failure-second-client.png"), fullPage: true });
    console.error(await otherClient.getByRole("alert").allTextContents());
    throw error;
  }
  const stale = await page.evaluate(async ({ id, request, revision }) => {
    const response = await globalThis.fetch(`/api/v1/runs/${id}/approvals/${request}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision }) });
    return response.status;
  }, { id: rejected.run.run_id, request: rejected.request.approval_id, revision: rejected.request.revision + 1 });
  if (stale !== 409) throw new Error("Stale approval revision was not rejected by the real daemon");
  await page.getByLabel("Decision note", { exact: false }).fill("Reject the first owned-fixture request; preserve the file.");
  const rejection = page.waitForResponse((response) => response.url().endsWith(`/approvals/${rejected.request.approval_id}/reject`) && response.request().method() === "POST");
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  if (!(await rejection).ok()) throw new Error("Exact rejection was not accepted");
  await otherClient.getByRole("button", { name: "Refresh run", exact: true }).click();
  await otherClient.getByRole("button", { name: "Approve & continue", exact: true }).waitFor({ state: "hidden" });
  if (await readFile(deletionTarget, "utf8") !== "deterministic deletion target\n") throw new Error("Rejected or stale permission changed the owned fixture");
  await otherClient.close();
  const authorized = await requestDeletion();
  await page.goto(`${origin}/studio/#/runs/${authorized.run.run_id}`);
  await page.getByRole("button", { name: "Approve & continue", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: "Approval needed: inspect this exact request", exact: true }).click();
  await page.getByRole("button", { name: "Approve & continue", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "approval-mobile-dark.png"), fullPage: true });
  await checkAccessibility("approval-mobile-dark");
  const approvalResponse = page.waitForResponse((response) => response.url().endsWith(`/approvals/${authorized.request.approval_id}/approve`) && response.request().method() === "POST");
  await page.getByRole("button", { name: "Approve & continue", exact: true }).click();
  if (!(await approvalResponse).ok()) throw new Error("Exact approval was not accepted");
  const approvalDeadline = Date.now() + 30_000;
  let authorizedStatus;
  while (Date.now() < approvalDeadline) {
    authorizedStatus = await api(`/api/v1/runs/${authorized.run.run_id}`);
    if (["succeeded", "failed", "cancelled"].includes(authorizedStatus.status)) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (authorizedStatus?.status !== "succeeded") throw new Error(`Approved owned-fixture workflow did not succeed: ${authorizedStatus?.status}`);
  let deleted = false;
  try { await readFile(deletionTarget); } catch (error) { if (error.code === "ENOENT") deleted = true; else throw error; }
  if (!deleted) throw new Error("Approved tool did not delete its exact owned fixture");
  const dirty = spawnSync("git", ["status", "--porcelain"], { cwd: workspace, shell: false, encoding: "utf8" });
  if (dirty.stdout.includes("runs/") || dirty.stdout.includes(".syndra/")) throw new Error("Managed state leaked into repository changes");
  const changed = spawnSync("git", ["status", "--porcelain"], { cwd: accepted.workspace, shell: false, encoding: "utf8" });
  if (changed.status !== 0 || changed.stdout.trim() !== "M calculator.py") throw new Error("Guided repair changed unexpected files or leaked managed artifacts");
  await writeFile(path.join(output, "accessibility.json"), JSON.stringify(accessibility, null, 2));
  await writeFile(path.join(output, "result.json"), JSON.stringify({ acceptance_status: accessibility.some((state) => state.violations.length) ? "failed" : "passed", rendering, external_request_count: externalRequests.size, run_id: accepted.run_id, status: status.status, failed_run_id: failedRun.run_id, failed_run_status: failureStatus.status, persisted_history_rows: historyIds.length,
    navigation: { section_retained: true, reading_position_retained: true, draft_retained: true, root_selection_validated: true, ledger_pages_and_position_retained: true, ledger_filters_back_and_reload: true, zoom_equivalent_reflow: true },
    approvals: { rejected_run_id: rejected.run.run_id, approved_run_id: authorized.run.run_id, stale_revision_rejected: true, cross_client_resolution: true, rejected_file_preserved: true, approved_owned_file_deleted: true, approved_run_status: authorizedStatus.status },
    fixture_workspace: workspace, run_workspace: accepted.workspace, live_provider_calls: 0, installed_package: installed, manual_token_entry: false, accessibility_violations: accessibility.reduce((count, state) => count + state.violations.length, 0) }, null, 2));
  if (accessibility.some((state) => state.violations.length)) throw new Error("Browser accessibility checks failed; see accessibility.json");
  if (browserErrors.length) throw new Error("Browser JavaScript errors occurred; see browser-errors.json");
  if (externalRequests.size) throw new Error("Studio made an unexpected external request");
  console.log(`Real-daemon packaged Studio acceptance passed; run ${accepted.run_id}: ${status.status}. Installed wheel: ${installed}.`);
} catch (error) {
  await writeFile(path.join(output, "browser-errors.json"), JSON.stringify(browserErrors, null, 2));
  const result = JSON.parse(await readFile(path.join(output, "result.json"), "utf8"));
  await writeFile(path.join(output, "result.json"), JSON.stringify({ ...result, acceptance_status: "failed", failure_category: error.name }, null, 2));
  await page?.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
  let clientIndex = 0;
  for (const context of browser?.contexts() ?? []) {
    for (const client of context.pages()) {
      await client.screenshot({ path: path.join(output, `failure-client-${clientIndex++}.png`), fullPage: true });
      console.error(await client.getByRole("alert").allTextContents());
    }
  }
  console.error(await page?.getByRole("alert").allTextContents());
  throw error;
} finally {
  await browser?.close();
  if (daemon.exitCode === null && daemon.signalCode === null) await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Owned acceptance daemon did not stop")), 10_000);
    daemon.once("close", () => { clearTimeout(timer); resolve(); });
    daemon.kill();
  });
}
