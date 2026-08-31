import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const studioUrl = process.env.AGENTBUS_STUDIO_URL ?? "http://127.0.0.1:5173";
const controlUrl = process.env.AGENTBUS_CONTROL_URL ?? "http://127.0.0.1:8765";
const token = required("AGENTBUS_STUDIO_TOKEN");
const workspace = required("AGENTBUS_DEMO_WORKSPACE");
const approve = process.env.AGENTBUS_SCREENSHOTS_APPROVE === "true";
const outputDirectory = path.resolve(
  process.cwd(),
  process.env.AGENTBUS_SCREENSHOT_OUT ?? "../docs/submission/screenshots",
);
const forbiddenPresentationValues = [
  token,
  process.env.USERPROFILE,
  path.dirname(workspace),
].filter((value) => value && value.length > 3);

await mkdir(outputDirectory, { recursive: true });

const browser = await chromium.launch({
  channel: process.env.AGENTBUS_SCREENSHOT_BROWSER_CHANNEL ?? "chrome",
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
  await capture(page, "01-first-launch.png");

  const rejectedSession = await context.newPage();
  await rejectedSession.goto(studioUrl, { waitUntil: "networkidle" });
  await rejectedSession.getByLabel("Session token").fill("x".repeat(40));
  await rejectedSession.getByRole("button", { name: "Open Studio" }).click();
  await rejectedSession.getByRole("alert").waitFor({ state: "visible" });
  await rejectedSession.close();

  await connect(page);
  await page.getByRole("heading", { name: "Command deck" }).waitFor();
  await capture(page, "02-dashboard.png");

  await go(page, "#/demo", "Payment confirmation, made retry-safe");
  await capture(page, "04-payment-demo.png");

  await go(page, "#/new?demo=payment", "Launch the idempotency repair");
  await page.getByLabel("Absolute workspace").fill(workspace);
  await page.getByRole("button", { name: "Validate" }).click();
  await page.getByText("Repository boundary confirmed", { exact: true }).waitFor();
  await capture(page, "03-new-task.png");

  let runId = process.env.AGENTBUS_RUN_ID;
  const launchedNewRun = !runId;
  if (launchedNewRun) {
    await page.getByRole("button", { name: "Launch execution" }).click();
    await page.waitForURL((url) => url.hash.startsWith("#/runs/"), { timeout: 30_000 });
    runId = decodeURIComponent(new globalThis.URL(page.url()).hash.slice("#/runs/".length));
  } else {
    await go(page, `#/runs/${encodeURIComponent(runId)}`);
  }
  globalThis.console.log(`RUN_ID=${runId}`);

  await page.locator(".run-page").waitFor({ timeout: 30_000 });
  if (launchedNewRun) await capture(page, "05-active-run.png");
  let completed = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
  if (!terminal(completed.status)) {
    await page.getByRole("region", { name: "Approval gate" }).waitFor({ timeout: 180_000 });
    await frameApproval(page);
    await capture(page, "06-approval-gate.png");

    await page.reload({ waitUntil: "networkidle" });
    await connect(page);
    await page.getByRole("region", { name: "Approval gate" }).waitFor({ timeout: 30_000 });

    await page.getByRole("button", { name: /^Source/ }).click();
    await page.getByRole("heading", { name: "Candidate diff" }).waitFor();
    await capture(page, "11-git-diff.png");
    await page.getByRole("button", { name: /^Overview/ }).click();

    if (!approve) {
      throw new Error(
        "Approval gate captured. Set AGENTBUS_SCREENSHOTS_APPROVE=true to explicitly approve and continue the real run.",
      );
    }
    await page.getByLabel(/Decision note/i).fill(
      "Exact offline Maven invocation reviewed for the submission demo.",
    );
    await page.getByRole("button", { name: /Approve & continue/i }).click();
    await resumeThroughStudio(page, runId);
    completed = await waitForRun(runId, (run) => terminal(run.status), 180_000);
  }
  if (completed.status !== "succeeded") {
    throw new Error(`Payment run ended with ${completed.status}, not succeeded.`);
  }

  await go(page, `#/runs/${encodeURIComponent(runId)}`, completed.original_task);
  await page.getByRole("button", { name: /^Review/ }).click();
  await page.getByRole("heading", { name: "Verifier" }).waitFor();
  await capture(page, "09-verification-passed.png");
  await page.locator(".reviewer-decision").scrollIntoViewIfNeeded();
  await capture(page, "10-final-review.png");

  await page.getByRole("button", { name: /^Attempts/ }).click();
  await capture(page, "07-attempt-stack.png");

  await page.getByRole("button", { name: /^Source/ }).click();
  await capture(page, "11-git-diff.png");

  await page.getByRole("button", { name: /^Evidence/ }).click();
  await page.getByRole("button", { name: "Verify sealed trace" }).click();
  await page.getByText("Integrity verified", { exact: true }).waitFor({ timeout: 30_000 });
  await capture(page, "12-evidence-trace.png");
  await page.getByRole("button", { name: "Run offline replay" }).click();
  await page.locator(".replay-result").waitFor({ timeout: 60_000 });
  await capture(page, "13-offline-replay.png");

  const retryRunId = process.env.AGENTBUS_RETRY_RUN_ID;
  if (retryRunId) {
    await go(page, `#/runs/${encodeURIComponent(retryRunId)}`);
    await page.locator(".run-page").waitFor({ timeout: 30_000 });
    await page.getByText(retryRunId.slice(0, 8), { exact: false }).first().waitFor();
    await page.getByRole("button", { name: /^Overview/ }).click();
    await page.getByRole("region", { name: "Approval gate" }).waitFor({ timeout: 30_000 });
    await frameApproval(page);
    await capture(page, "06-approval-gate.png");
    await page.getByRole("button", { name: /^Attempts/ }).click();
    await capture(page, "07-attempt-stack.png");
    await page.getByRole("button", { name: /^Tools/ }).click();
    await page.getByRole("heading", { name: "Tool invocation timeline" }).waitFor();
    await capture(page, "08-verification-failure.png");
  }

  await go(page, "#/runtime", "Runtime health");
  await capture(page, "14-runtime-health.png");
  await go(page, "#/history", "Run history");
  await capture(page, "15-run-history.png");

  await go(page, "#/", "Command deck");
  await page.setViewportSize({ width: 1280, height: 800 });
  await capture(page, "17-dashboard-1280.png");
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "Disconnect" }).click();
  await page.getByRole("heading", { name: "Connect to AgentBus" }).waitFor();
  await capture(page, "16-runtime-disconnected.png");

  const replay = await latestReplay(runId);
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

async function stabilize(targetPage) {
  await targetPage.addStyleTag({
    content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}",
  });
}

async function capture(targetPage, name) {
  await targetPage.waitForTimeout(200);
  await assertPublicSafe(targetPage, name);
  await targetPage.mouse.move(1435, 5);
  await targetPage.screenshot({
    path: path.join(outputDirectory, name),
    animations: "disabled",
    fullPage: false,
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

async function waitForRun(runId, predicate, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const run = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
    if (predicate(run)) return run;
    await new Promise((resolve) => globalThis.setTimeout(resolve, 500));
  }
  throw new Error(`Run ${runId} did not reach the expected state within ${timeoutMs}ms.`);
}

async function latestReplay(runId) {
  const response = await api(`/api/v1/replays?run_id=${encodeURIComponent(runId)}&limit=10`);
  return response.replays?.[0];
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

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required.`);
  return value;
}
