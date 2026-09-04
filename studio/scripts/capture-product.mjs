import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const studioUrl = environment("SYNDRA_STUDIO_URL", "AGENTBUS_STUDIO_URL") ?? "http://127.0.0.1:5173";
const controlUrl = environment("SYNDRA_CONTROL_URL", "AGENTBUS_CONTROL_URL") ?? "http://127.0.0.1:8765";
const token = required("SYNDRA_STUDIO_TOKEN", "AGENTBUS_STUDIO_TOKEN");
const workspace = required("SYNDRA_DEMO_WORKSPACE", "AGENTBUS_DEMO_WORKSPACE");
const approve = environment("SYNDRA_SCREENSHOTS_APPROVE", "AGENTBUS_SCREENSHOTS_APPROVE") === "true";
const outputDirectory = path.resolve(
  process.cwd(),
  environment("SYNDRA_SCREENSHOT_OUT", "AGENTBUS_SCREENSHOT_OUT") ?? "../docs/product/screenshots",
);
const forbiddenPresentationValues = [
  token,
  process.env.USERPROFILE,
  path.dirname(workspace),
].filter((value) => value && value.length > 3);

await mkdir(outputDirectory, { recursive: true });

const browser = await chromium.launch({
  channel: environment("SYNDRA_SCREENSHOT_BROWSER_CHANNEL", "AGENTBUS_SCREENSHOT_BROWSER_CHANNEL") ?? "chrome",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
  reducedMotion: "reduce",
});
const page = await context.newPage();

try {
  await page.goto(studioUrl, { waitUntil: "networkidle" });
  await stabilize(page);

  const rejectedSession = await context.newPage();
  await rejectedSession.goto(studioUrl, { waitUntil: "networkidle" });
  await rejectedSession.getByLabel("Session token").fill("x".repeat(40));
  await rejectedSession.getByRole("button", { name: "Open Studio" }).click();
  await rejectedSession.getByRole("alert").waitFor({ state: "visible" });
  await rejectedSession.close();

  await connect(page);
  await page.getByRole("heading", { name: "Execution, attention, integrity." }).waitFor();
  await page.getByText("Stream healthy", { exact: true }).waitFor({ timeout: 30_000 });
  await capture(page, "01-dashboard.png");

  await go(page, "#/demo", "Payment confirmation, made retry-safe");
  await capture(page, "03-payment-safety-demo.png");

  await go(page, "#/new?demo=payment", "Launch the idempotency repair");
  const workspaceInput = page.getByLabel("Absolute workspace");
  await workspaceInput.fill(workspace);
  await page.getByRole("button", { name: "Validate" }).click();
  await page.getByText("Repository boundary confirmed", { exact: true }).waitFor();
  await capture(page, "02-new-run.png");

  let runId = environment("SYNDRA_RUN_ID", "AGENTBUS_RUN_ID");
  const launchedNewRun = !runId;
  if (launchedNewRun) {
    await page.getByRole("button", { name: "Launch execution" }).click();
    await page.waitForURL((url) => url.hash.startsWith("#/runs/"), { timeout: 30_000 });
    const hashPath = new globalThis.URL(page.url()).hash.slice(2).split("?", 1)[0];
    runId = decodeURIComponent(hashPath.slice("runs/".length));
  } else {
    await go(page, `#/runs/${encodeURIComponent(runId)}`);
  }
  globalThis.console.log(`RUN_ID=${runId}`);

  await page.locator(".run-observatory").waitFor({ timeout: 30_000 });
  await page.getByRole("region", { name: "Execution mesh" }).waitFor();
  await selectLastMeshNode(page, ".mesh-node.kind-coder");
  await capture(page, "04-active-execution.png");
  let completed = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
  if (!terminal(completed.status)) {
    await page.locator(".approval-gate").waitFor({ timeout: 180_000 });
    await selectLastMeshNode(page, ".mesh-node.kind-approval");
    await frameApprovalTop(page);
    await capture(page, "05-approval-gate.png");
    await frameApproval(page);

    await page.getByRole("button", { name: "Source", exact: true }).click();
    await page.getByRole("region", { name: "Source lens" }).waitFor();
    await waitForSource(page);
    await page.getByRole("button", { name: "Close source lens" }).click();

    if (!approve) {
      throw new Error(
        "Approval gate captured. Set SYNDRA_SCREENSHOTS_APPROVE=true to explicitly approve and continue the real run.",
      );
    }
    completed = await completeThroughStudio(page, runId, 180_000);
  }
  if (completed.status !== "succeeded") {
    throw new Error(`Payment run ended with ${completed.status}, not succeeded.`);
  }

  await go(page, "#/", "Execution, attention, integrity.");
  await go(page, `#/runs/${encodeURIComponent(runId)}`, completed.original_task);
  await page.locator(".run-observatory").waitFor({ timeout: 30_000 });
  await page.getByRole("region", { name: "Execution mesh" }).waitFor();
  await selectLastMeshNode(page, ".mesh-node.kind-verifier");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByText("Mandatory final review", { exact: true }).waitFor();
  await capture(page, "07-verification-review.png");

  await page.getByRole("button", { name: "Source", exact: true }).click();
  await page.getByRole("region", { name: "Source lens" }).waitFor();
  await waitForSource(page);
  await capture(page, "06-source-lens.png");

  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await page.getByRole("button", { name: "Verify sealed trace" }).click();
  await page.getByText("Integrity verified", { exact: true }).waitFor({ timeout: 30_000 });
  await capture(page, "08-integrity-spine.png");
  const replayBefore = await latestReplay(runId);
  await page.getByRole("button", { name: "Run offline replay" }).click();
  const replay = await waitForReplay(runId, replayBefore?.replay_id, 60_000);
  await page.getByText("PROCESS NOT DISPATCHED", { exact: true }).waitFor({ timeout: 30_000 });
  await capture(page, "09-offline-replay.png");

  await page.getByRole("button", { name: "Disconnect Studio" }).click();
  await page.getByRole("heading", { name: "Connect to Syndra" }).waitFor();
  await capture(page, "10-runtime-disconnected.png");

  globalThis.console.log(`FINAL_STATUS=${completed.status}`);
  globalThis.console.log(`VERIFIER=${completed.verifier_status ?? "not-reported"}`);
  globalThis.console.log(`REVIEWER=${completed.reviewer_status ?? "not-reported"}`);
  globalThis.console.log(`REPLAY_ID=${replay?.replay_id ?? "not-found"}`);
  globalThis.console.log(`PROVIDER_CALLS=${replay?.provider_calls ?? "not-reported"}`);
  globalThis.console.log(`NETWORK_CALLS=${replay?.network_calls ?? "not-reported"}`);
  globalThis.console.log(`PROCESS_DISPATCHES=${replay?.process_dispatches ?? "not-reported"}`);
} finally {
  await browser.close();
}

async function connect(targetPage) {
  const tokenInput = targetPage.getByLabel("Session token");
  if (await tokenInput.isVisible().catch(() => false)) {
    await tokenInput.fill(token);
    await targetPage.getByRole("button", { name: "Open Studio" }).click();
  }
}

async function go(targetPage, hash, heading) {
  await targetPage.evaluate((nextHash) => {
    globalThis.location.hash = nextHash;
  }, hash);
  if (heading) await targetPage.getByRole("heading", { name: heading }).waitFor();
  await targetPage.waitForTimeout(250);
  await targetPage.evaluate(() => globalThis.scrollTo(0, 0));
  await targetPage.waitForTimeout(100);
}

async function frameApproval(targetPage) {
  await targetPage.getByRole("button", { name: /Approve & continue/i }).scrollIntoViewIfNeeded();
  await targetPage.waitForTimeout(100);
}

async function frameApprovalTop(targetPage) {
  await targetPage.locator(".inspector-scroll").evaluate((element) => {
    element.scrollTop = 0;
  });
  await targetPage.waitForTimeout(100);
}

async function stabilize(targetPage) {
  await targetPage.addStyleTag({
    content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}",
  });
}

async function capture(targetPage, name, options = {}) {
  await targetPage.waitForTimeout(200);
  await assertPublicSafe(targetPage, name);
  await targetPage.mouse.move(1435, 5);
  await targetPage.screenshot({
    path: path.join(outputDirectory, name),
    animations: "disabled",
    fullPage: false,
    ...options,
  });
}

async function assertPublicSafe(targetPage, name) {
  const visible = await targetPage.locator("body").innerText();
  const inputValues = await targetPage.locator("input:visible, textarea:visible").evaluateAll(
    (elements) => elements.map((element) => element.value).join("\n"),
  );
  const presentation = `${visible}\n${inputValues}`;
  for (const forbidden of forbiddenPresentationValues) {
    if (presentation.includes(forbidden)) {
      throw new Error(`${name} contains a presentation-sensitive local value.`);
    }
  }
  for (const forbidden of [/AgentBus/i, /Razorpay/i, /hackathon/i, /submission/i, /internship/i]) {
    if (forbidden.test(presentation)) {
      throw new Error(`${name} contains stale public product identity.`);
    }
  }
}

async function waitForSource(targetPage) {
  const started = Date.now();
  while (Date.now() - started < 30_000) {
    const error = targetPage.locator(".source-diff-error");
    if (await error.isVisible().catch(() => false)) {
      throw new Error(`Source lens failed: ${await error.innerText()}`);
    }
    if (await targetPage.locator(".virtual-diff, .quiet-copy").first().isVisible().catch(() => false)) return;
    await targetPage.waitForTimeout(100);
  }
  throw new Error("Source lens did not render a bounded diff within 30000ms.");
}

async function selectLastMeshNode(targetPage, selector) {
  const nodes = targetPage.locator(selector);
  await nodes.last().waitFor({ state: "visible", timeout: 30_000 });
  const count = await nodes.count();
  await nodes.nth(count - 1).click();
}

async function resumeThroughStudio(targetPage, runId) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const resume = targetPage.getByRole("button", { name: "Resume" });
    if (await resume.isVisible().catch(() => false)) {
      await resume.click();
      return;
    }
    const run = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
    if (run.status === "running" || run.status === "succeeded") return;
    const refresh = targetPage.getByRole("button", { name: "Refresh" });
    if (await refresh.isVisible().catch(() => false)) await refresh.click();
    await targetPage.waitForTimeout(500);
  }
  throw new Error("Studio did not expose a resumable continuation after approval.");
}

async function completeThroughStudio(targetPage, runId, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const run = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
    if (terminal(run.status)) return run;
    if (run.status === "waiting_for_approval") {
      const approvals = await api(`/api/v1/runs/${encodeURIComponent(runId)}/approvals`);
      const pending = approvals.approvals?.find((approval) => approval.state === "pending");
      if (pending) {
        await targetPage.locator(".approval-gate").waitFor({ timeout: 30_000 });
        await targetPage.getByLabel(/Decision note/i).fill(
          "Exact offline managed invocation reviewed for the Payment Safety Demo.",
        );
        await targetPage.getByRole("button", { name: /Approve & continue/i }).click();
        await waitForApprovalDecision(runId, pending.approval_id, 30_000);
      }
      await resumeThroughStudio(targetPage, runId);
      continue;
    }
    await targetPage.waitForTimeout(500);
  }
  throw new Error(`Run ${runId} did not complete through Studio within ${timeoutMs}ms.`);
}

async function waitForApprovalDecision(runId, approvalId, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const response = await api(`/api/v1/runs/${encodeURIComponent(runId)}/approvals`);
    const approval = response.approvals?.find((item) => item.approval_id === approvalId);
    if (approval && approval.state !== "pending") return approval;
    await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
  }
  throw new Error(`Approval ${approvalId} remained pending for ${timeoutMs}ms.`);
}

async function latestReplay(runId) {
  const response = await api(`/api/v1/replays?run_id=${encodeURIComponent(runId)}&limit=10`);
  return response.replays?.[0];
}

async function waitForReplay(runId, previousReplayId, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const replay = await latestReplay(runId);
    if (replay && replay.replay_id !== previousReplayId) return replay;
    await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
  }
  throw new Error(`Run ${runId} did not persist a new replay within ${timeoutMs}ms.`);
}

async function api(apiPath) {
  const response = await globalThis.fetch(`${controlUrl}${apiPath}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Control API ${apiPath} returned ${response.status}.`);
  return response.json();
}

function terminal(status) {
  return ["succeeded", "failed", "rejected", "cancelled", "completed"].includes(
    String(status).toLowerCase(),
  );
}

function environment(canonicalName, legacyName) {
  return process.env[canonicalName]?.trim() || process.env[legacyName]?.trim();
}

function required(canonicalName, legacyName) {
  const value = environment(canonicalName, legacyName);
  if (!value) throw new Error(`${canonicalName} is required.`);
  return value;
}
