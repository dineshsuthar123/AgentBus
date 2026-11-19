import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const { launch, run_id: runId, phase, output } = JSON.parse(input);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.name));
  await page.goto(launch);
  await expect(page.getByRole("heading", { name: "Delete the deterministic target only after exact approval.", exact: true })).toBeVisible();
  const observed = await page.evaluate(async (id) => {
    const response = await globalThis.fetch(`/api/v1/runs/${id}`);
    if (!response.ok) throw new Error("Authenticated Studio could not read the CLI run");
    const run = await response.json();
    return { run_id: run.run_id, status: run.status, reviewer_status: run.reviewer_status };
  }, runId);
  expect(observed.run_id).toBe(runId);
  expect(observed.status).toBe(phase === "pending" ? "waiting_for_approval" : "succeeded");
  if (phase === "pending") await expect(page.getByRole("region", { name: "Capability envelope", exact: true }).getByText("paths: delete_me.txt", { exact: true })).toBeVisible();
  else expect(observed.reviewer_status).toBe("approved");
  expect(errors).toEqual([]);
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: path.join(output, `${phase}.png`), fullPage: true });
  await writeFile(path.join(output, `${phase}.json`), JSON.stringify({ ...observed, browser_errors: errors.length, manual_token_entry: false }, null, 2));
} finally {
  await browser.close();
}
