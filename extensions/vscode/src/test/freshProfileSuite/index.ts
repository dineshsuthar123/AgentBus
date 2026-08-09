import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import * as vscode from "vscode";
import type { AgentBusExtensionApi } from "../../extension";
import type {
  ReplayAcceptedResponse,
  ReplaySessionResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  WorkspaceIndexMutationResponse
} from "../../generated/protocol";

interface Handoff {
  runId: string;
  replayId: string;
  workspaceId: string;
  daemonId: string;
}

export async function run(): Promise<void> {
  const stage = requiredEnvironment("AGENTBUS_FRESH_STAGE");
  const pythonPath = requiredEnvironment("AGENTBUS_FRESH_PYTHON");
  const configPath = requiredEnvironment("AGENTBUS_FRESH_CONFIG");
  const registryPath = requiredEnvironment("AGENTBUS_FRESH_REGISTRY");
  const workspace = requiredEnvironment("AGENTBUS_FRESH_WORKSPACE");
  const handoffPath = requiredEnvironment("AGENTBUS_FRESH_HANDOFF");
  const configuration = vscode.workspace.getConfiguration("agentbus");
  await configuration.update("pythonPath", pythonPath, vscode.ConfigurationTarget.Global);
  await configuration.update("configPath", configPath, vscode.ConfigurationTarget.Global);
  await configuration.update("registryPath", registryPath, vscode.ConfigurationTarget.Global);
  await configuration.update(
    "defaultProvider",
    "deterministic",
    vscode.ConfigurationTarget.Global
  );
  await configuration.update("logLevel", "error", vscode.ConfigurationTarget.Global);
  await configuration.update(
    "autoStartDaemon",
    stage === "recovery",
    vscode.ConfigurationTarget.Global
  );

  const extension = vscode.extensions.getExtension<AgentBusExtensionApi>(
    "agentbus.agentbus-vscode"
  );
  assert.ok(extension, "Fresh-profile VSIX was not discovered");
  const api = await extension.activate();
  if (stage === "initial") {
    await initialFlow(api, configuration, workspace, handoffPath);
    return;
  }
  if (stage === "recovery") {
    await recoveryFlow(api, workspace, handoffPath);
    return;
  }
  throw new Error(`Unknown fresh-profile stage: ${stage}`);
}

async function initialFlow(
  api: AgentBusExtensionApi,
  configuration: vscode.WorkspaceConfiguration,
  workspace: string,
  handoffPath: string
): Promise<void> {
  const onboarding = await api.onboardingState();
  assert.ok(onboarding, "First activation did not produce onboarding state");
  assert.equal(onboarding.installation.state, "compatible");
  assert.equal(onboarding.trusted, true);
  assert.equal(onboarding.daemon, "not_detected");
  assert.equal(onboarding.index, "not_built");

  const commands = await vscode.commands.getCommands(true);
  for (const command of [
    "agentbus.getStarted",
    "agentbus.runSetup",
    "agentbus.runQuickstart",
    "agentbus.checkInstallation",
    "agentbus.openDocumentation",
    "agentbus.openResolvedConfiguration"
  ]) {
    assert.equal(commands.includes(command), true, command);
  }
  await vscode.commands.executeCommand("agentbus.getStarted");
  await vscode.commands.executeCommand("agentbus.checkInstallation");

  await configuration.update(
    "autoStartDaemon",
    true,
    vscode.ConfigurationTarget.Global
  );
  const client = await waitFor(async () => {
    try {
      return (await api.client());
    } catch {
      return undefined;
    }
  });
  const daemonId = api.daemonId();
  assert.ok(daemonId, "Fresh-profile daemon did not start");

  const index = await vscode.commands.executeCommand<WorkspaceIndexMutationResponse>(
    "agentbus.buildRepositoryIndex"
  );
  assert.ok(index, "Fresh-profile repository index did not build");
  assert.equal(index.result.status.state, "current");
  assert.equal(index.result.provider_calls ?? 0, 0);
  assert.equal(index.result.network_calls ?? 0, 0);

  const request: RunCreateRequest = {
    task: "Create and verify a small deterministic Python calculator.",
    workspace,
    provider: "deterministic",
    workflow: "multi",
    durable: true,
    parallel: false,
    max_workers: 1,
    commit_changes: true,
    keep_worktrees: false,
    deterministic: { profile: "python-calculator" }
  };
  const accepted = await vscode.commands.executeCommand<RunAcceptedResponse>(
    "agentbus.submitRun",
    request
  );
  assert.ok(accepted?.run_id, "Fresh-profile deterministic run was not accepted");
  const run = await waitForRun(client, accepted.run_id);
  assert.equal(run.status, "succeeded", run.failure_reason ?? undefined);
  assert.equal(run.reviewer_status, "approved");

  const report = await waitForCommittedReport(client, run.run_id);
  const changes = await client.changes(run.run_id);
  assert.deepEqual(
    changes.changes.map((change) => change.path).sort(),
    ["agentbus_result.py", "test_agentbus_result.py"]
  );
  assert.ok(changes.changes.every((change) => change.status === "committed"));
  const implementation = changes.changes.find(
    (change) => change.path === "agentbus_result.py"
  );
  assert.ok(implementation);
  const diff = await client.diff(run.run_id);
  assert.match(diff.diff, /agentbus_result\.py/u);
  assert.equal(diff.truncated ?? false, false);
  assert.equal(report.status, "succeeded");
  await vscode.commands.executeCommand("agentbus.openRunReport");
  const reportDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-report"
    )
  );
  assert.match(reportDocument.getText(), /\*\*Status:\*\* succeeded/u);
  assert.match(reportDocument.getText(), /reviewer_status \| approved/u);
  assert.match(reportDocument.getText(), /agentbus_result\.py/u);
  await vscode.commands.executeCommand(
    "agentbus.openChange",
    run.run_id,
    implementation.path
  );
  const changedDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-after"
    )
  );
  assert.match(changedDocument.getText(), /def add/u);

  const replay = await vscode.commands.executeCommand<ReplayAcceptedResponse>(
    "agentbus.replayRunOffline",
    run.run_id
  );
  assert.ok(replay?.replay_id, "Fresh-profile offline replay was not accepted");
  const completedReplay = await waitForReplay(client, replay.replay_id);
  assert.equal(completedReplay.status, "succeeded");
  assert.equal(completedReplay.provider_calls, 0);
  assert.equal(completedReplay.network_calls, 0);

  await writeFile(
    handoffPath,
    JSON.stringify({
      runId: run.run_id,
      replayId: replay.replay_id,
      workspaceId: index.workspace_id,
      daemonId
    } satisfies Handoff),
    "utf8"
  );
  await vscode.commands.executeCommand("agentbus.stopDaemon");
  await waitFor(() => api.daemonId() === undefined ? true : undefined);
}

async function recoveryFlow(
  api: AgentBusExtensionApi,
  workspace: string,
  handoffPath: string
): Promise<void> {
  assert.equal(
    await api.onboardingState(),
    undefined,
    "Onboarding should be shown only once for the 0.6 product line"
  );
  const handoff = JSON.parse(await readFile(handoffPath, "utf8")) as Handoff;
  const client = await waitFor(async () => {
    try {
      return await api.client();
    } catch {
      return undefined;
    }
  });
  const recoveredDaemon = api.daemonId();
  assert.ok(recoveredDaemon, "Recovery daemon did not start");
  assert.notEqual(recoveredDaemon, handoff.daemonId);
  const run = await client.run(handoff.runId);
  assert.equal(run.status, "succeeded");
  assert.equal((await client.report(handoff.runId)).status, "succeeded");
  assert.match((await client.diff(handoff.runId)).diff, /agentbus_result\.py/u);
  const replay = await client.replay(handoff.replayId);
  assert.equal(replay.status, "succeeded");
  assert.equal(replay.provider_calls, 0);
  assert.equal(replay.network_calls, 0);
  const index = await client.attachWorkspaceIndex({ workspace });
  assert.equal(index.workspace_id, handoff.workspaceId);
  assert.ok(["current", "stale"].includes(index.status.state));
  await vscode.commands.executeCommand("agentbus.stopDaemon");
  await waitFor(() => api.daemonId() === undefined ? true : undefined);
}

async function waitForRun(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
): Promise<RunSummary> {
  return waitFor(async () => {
    let run: RunSummary;
    try {
      run = await client.run(runId);
    } catch {
      return undefined;
    }
    if (run.status === "succeeded") return run;
    if (["failed", "cancelled"].includes(run.status)) {
      throw new Error(
        `Fresh-profile run reached ${run.status}: ${run.failure_reason ?? "unknown"}`
      );
    }
    return undefined;
  }, 90_000);
}

async function waitForCommittedReport(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
) {
  return waitFor(async () => {
    const report = await client.report(runId);
    return typeof report.report.commit_identifier === "string" &&
      report.report.commit_identifier.length > 0
      ? report
      : undefined;
  }, 30_000);
}

async function waitForReplay(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  replayId: string
): Promise<ReplaySessionResponse> {
  return waitFor(async () => {
    let replay: ReplaySessionResponse;
    try {
      replay = await client.replay(replayId);
    } catch {
      return undefined;
    }
    if (replay.status === "succeeded") return replay;
    if (["failed", "cancelled", "incompatible", "awaiting_input"].includes(
      replay.status
    )) {
      throw new Error(
        `Fresh-profile replay reached ${replay.status}: ${
          replay.failure_message ?? "unknown"
        }`
      );
    }
    return undefined;
  }, 60_000);
}

async function waitFor<T>(
  check: () => T | undefined | Promise<T | undefined>,
  timeoutMs = 30_000
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value !== undefined && value !== false) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("Timed out waiting for fresh-profile AgentBus state.");
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name} for fresh-profile acceptance.`);
  return value;
}
