import assert from "node:assert/strict";
import { readFile, realpath, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import * as vscode from "vscode";
import type { AgentBusExtensionApi } from "../../extension";
import type {
  ApprovalSummary,
  CancelResponse,
  ReplayAcceptedResponse,
  ReplaySessionResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  WorkspaceIndexMutationResponse
} from "../../generated/protocol";
import { canonicalWorkspacePath, selectWorkspace } from "../../workspace";

interface Handoff {
  runId: string;
  replayId: string;
  workspaceId: string;
  daemonId: string;
  cancelledRunId?: string;
  approvalRunId?: string;
  stressReplayId?: string;
  secondaryRunId?: string;
  recoveredDaemonId?: string;
}

export async function run(): Promise<void> {
  try {
    await runStage();
  } catch (error) {
    const diagnostic = safeDiagnostic(error);
    process.stderr.write(`Fresh-profile assertion: ${diagnostic}\n`);
    const handoffPath = process.env.AGENTBUS_FRESH_HANDOFF;
    if (handoffPath) {
      await writeFile(`${handoffPath}.failure`, diagnostic, "utf8");
    }
    throw error;
  }
}

async function runStage(): Promise<void> {
  const stage = requiredEnvironment("AGENTBUS_FRESH_STAGE");
  const pythonPath = requiredEnvironment("AGENTBUS_FRESH_PYTHON");
  const configPath = requiredEnvironment("AGENTBUS_FRESH_CONFIG");
  const mcpConfigPath = requiredEnvironment("AGENTBUS_FRESH_MCP_CONFIG");
  const registryPath = requiredEnvironment("AGENTBUS_FRESH_REGISTRY");
  const workspace = requiredEnvironment("AGENTBUS_FRESH_WORKSPACE");
  const secondaryWorkspace = requiredEnvironment(
    "AGENTBUS_FRESH_SECONDARY_WORKSPACE"
  );
  const incompatibleDaemonId = requiredEnvironment(
    "AGENTBUS_FRESH_INCOMPATIBLE_DAEMON"
  );
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
    stage !== "initial",
    vscode.ConfigurationTarget.Global
  );

  const extension = vscode.extensions.getExtension<AgentBusExtensionApi>(
    "agentbus.agentbus-vscode"
  );
  assert.ok(extension, "Fresh-profile VSIX was not discovered");
  const api = await bounded(extension.activate(), 30_000, "extension activation");
  for (let activation = 0; activation < 4; activation += 1) {
    assert.equal(
      await bounded(extension.activate(), 5_000, "repeated extension activation"),
      api,
      "Repeated activation must reuse the existing extension instance"
    );
  }
  if (stage === "initial") {
    await initialFlow(api, configuration, workspace, handoffPath);
    return;
  }
  if (stage === "recovery") {
    await recoveryFlow(api, workspace, handoffPath);
    return;
  }
  if (stage === "stress") {
    await stressFlow(
      api,
      configuration,
      workspace,
      secondaryWorkspace,
      configPath,
      mcpConfigPath,
      registryPath,
      incompatibleDaemonId,
      handoffPath
    );
    return;
  }
  if (stage === "untrusted") {
    await untrustedFlow(api);
    return;
  }
  if (stage === "trusted-recovery") {
    await trustedRecoveryFlow(api, workspace, handoffPath);
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

async function stressFlow(
  api: AgentBusExtensionApi,
  configuration: vscode.WorkspaceConfiguration,
  workspace: string,
  secondaryWorkspace: string,
  configPath: string,
  mcpConfigPath: string,
  registryPath: string,
  incompatibleDaemonId: string,
  handoffPath: string
): Promise<void> {
  assert.equal(vscode.workspace.isTrusted, true);
  const folders = vscode.workspace.workspaceFolders ?? [];
  assert.equal(folders.length, 2, "Stress profile must open both workspace roots");
  const openedRoots = await Promise.all(
    folders.map((folder) => canonicalPath(folder.uri.fsPath))
  );
  assert.deepEqual(new Set(openedRoots), new Set(await Promise.all([
    canonicalPath(workspace),
    canonicalPath(secondaryWorkspace)
  ])));
  const selectedSecondary = await selectWorkspace(false, async (choices) => {
    assert.equal(choices.length, 2);
    const expected = await canonicalPath(secondaryWorkspace);
    for (const choice of choices) {
      if (await canonicalPath(choice.folder.uri.fsPath) === expected) return choice;
    }
    return undefined;
  });
  assert.ok(selectedSecondary, "Secondary workspace was not explicitly selected");
  const selectedSecondaryPath = await canonicalWorkspacePath(selectedSecondary);
  assert.equal(
    await canonicalPath(selectedSecondaryPath),
    await canonicalPath(secondaryWorkspace)
  );
  assert.equal(
    await selectWorkspace(false, async () => undefined),
    undefined,
    "Cancelling multi-root selection must not fall back to the first root"
  );

  const handoff = JSON.parse(await readFile(handoffPath, "utf8")) as Handoff;
  let client = await waitFor(async () => {
    try {
      return await bounded(api.client(), 10_000, "stress daemon connection");
    } catch {
      return undefined;
    }
  });
  const initialDaemonId = api.daemonId();
  assert.ok(initialDaemonId, "Stress profile daemon did not start");
  assert.notEqual(initialDaemonId, incompatibleDaemonId);
  await assertRegistryRejectedIncompatibleDaemon(
    registryPath,
    incompatibleDaemonId
  );
  await waitFor(() => api.eventStreamConnected() ? true : undefined);

  const secondaryValidation = await bounded(
    client.validateWorkspace({
      workspace: secondaryWorkspace,
      require_git: true
    }),
    10_000,
    "secondary workspace validation"
  );
  assert.equal(secondaryValidation.valid, true);
  assert.equal(
    await canonicalPath(secondaryValidation.git_top_level ?? ""),
    await canonicalPath(secondaryWorkspace)
  );

  for (let refresh = 0; refresh < 4; refresh += 1) {
    await bounded(
      vscode.commands.executeCommand("agentbus.refresh"),
      15_000,
      "run tree refresh"
    );
    assertUniqueRuns(api.runs());
  }

  await bounded(
    vscode.commands.executeCommand("agentbus.restartDaemon"),
    30_000,
    "daemon restart"
  );
  await waitFor(() => {
    const value = api.daemonId();
    return value && value !== initialDaemonId ? value : undefined;
  });
  client = await bounded(api.client(), 10_000, "restarted daemon connection");
  const reattached = await bounded(
    client.attachWorkspaceIndex({ workspace }),
    15_000,
    "index reconnect"
  );
  assert.equal(reattached.workspace_id, handoff.workspaceId);
  assert.ok(["current", "stale"].includes(reattached.status.state));

  const cancelledRunId = await exerciseCancellation(api, client, workspace);
  const approvalRunId = await exerciseApproval(api, client, workspace);
  const secondaryRunId = await exerciseSecondaryWorkspaceRun(
    client,
    selectedSecondaryPath
  );
  const stressReplayId = await exerciseReplay(client, handoff.runId);
  client = await exerciseSyntheticMcpFailure(
    api,
    configuration,
    configPath,
    mcpConfigPath
  );

  const preStaleDaemonId = api.daemonId();
  assert.ok(preStaleDaemonId);
  const validConfiguration = await readFile(configPath, "utf8");
  await writeFile(configPath, "{ stale configuration", "utf8");
  try {
    await assert.rejects(
      bounded(
        vscode.commands.executeCommand("agentbus.restartDaemon"),
        30_000,
        "stale configuration rejection"
      ),
      /handshake|config|exited|startup/iu
    );
  } finally {
    await writeFile(configPath, validConfiguration, "utf8");
  }
  await waitFor(() => api.daemonId() === undefined ? true : undefined);
  await bounded(
    vscode.commands.executeCommand("agentbus.restartDaemon"),
    30_000,
    "stale configuration recovery"
  );
  const recoveredDaemonId = await waitFor(() => {
    const value = api.daemonId();
    return value && value !== preStaleDaemonId ? value : undefined;
  });
  client = await bounded(api.client(), 10_000, "recovered daemon connection");
  assert.equal((await client.run(handoff.runId)).status, "succeeded");
  assert.equal((await client.run(cancelledRunId)).status, "cancelled");
  assert.equal((await client.run(approvalRunId)).status, "succeeded");
  assert.equal((await client.run(secondaryRunId)).status, "succeeded");
  assert.equal((await client.replay(stressReplayId)).status, "succeeded");
  assert.equal(
    (await client.attachWorkspaceIndex({ workspace })).workspace_id,
    handoff.workspaceId
  );
  for (let refresh = 0; refresh < 3; refresh += 1) {
    await bounded(
      vscode.commands.executeCommand("agentbus.refresh"),
      15_000,
      "recovered run tree refresh"
    );
  }
  assertUniqueRuns(api.runs());

  await writeFile(
    handoffPath,
    JSON.stringify({
      ...handoff,
      cancelledRunId,
      approvalRunId,
      secondaryRunId,
      stressReplayId,
      recoveredDaemonId
    } satisfies Handoff),
    "utf8"
  );
  await bounded(
    vscode.commands.executeCommand("agentbus.stopDaemon"),
    20_000,
    "stress daemon stop"
  );
  await waitFor(() => api.daemonId() === undefined ? true : undefined);
}

async function exerciseCancellation(
  api: AgentBusExtensionApi,
  client: FreshClient,
  workspace: string
): Promise<string> {
  const accepted = await bounded(
    vscode.commands.executeCommand<RunAcceptedResponse>(
      "agentbus.submitRun",
      {
        task: "Exercise bounded cooperative cancellation in the IDE.",
        workspace,
        provider: "deterministic",
        workflow: "multi",
        durable: true,
        parallel: false,
        max_workers: 1,
        commit_changes: false,
        keep_worktrees: false,
        deterministic: {
          profile: "cancellation-two-task",
          latency_seconds: 30,
          latency_roles: ["coder"]
        }
      } satisfies RunCreateRequest
    ),
    15_000,
    "cancellation run submission"
  );
  assert.ok(accepted?.run_id, "Cancellation run was not accepted");
  await waitForRunStatus(client, accepted.run_id, ["running"], 30_000);
  const cancellation = await bounded(
    vscode.commands.executeCommand<CancelResponse>(
      "agentbus.requestCancellation",
      accepted.run_id,
      "Fresh-profile cancellation stress"
    ),
    20_000,
    "run cancellation"
  );
  assert.equal(cancellation?.cancellation_requested, true);
  assert.equal(cancellation?.cancellation?.requested, true);
  await waitForCancellationCleanup(client, accepted.run_id);
  assertUniqueRuns(api.runs());
  return accepted.run_id;
}

async function exerciseApproval(
  api: AgentBusExtensionApi,
  client: FreshClient,
  workspace: string
): Promise<string> {
  const accepted = await submitAfterWorkspaceRelease({
    task: "Delete only the deterministic target after exact approval.",
    workspace,
    provider: "deterministic",
    workflow: "multi",
    durable: true,
    parallel: false,
    max_workers: 1,
    commit_changes: false,
    keep_worktrees: false,
    deterministic: { profile: "tool-delete-approval" }
  });
  assert.ok(accepted?.run_id, "Approval run was not accepted");
  const approval = await waitForPendingApproval(client, accepted.run_id);
  assert.deepEqual(approval.affected_paths, ["delete_me.txt"]);
  assert.deepEqual(
    approval.capabilities?.map((capability) => capability.name),
    ["filesystem.delete"]
  );
  const { formatApprovalConfirmation } = await import(
    "../../approvalPresentation"
  );
  const confirmation = formatApprovalConfirmation(approval);
  assert.match(confirmation, /delete_me\.txt/u);
  assert.doesNotMatch(confirmation, /workspace-secondary/iu);

  const { ApprovalsProvider, Selection } = await import("../../views");
  const selection = new Selection();
  selection.set(accepted.run_id);
  const approvals = new ApprovalsProvider(async () => client, selection);
  const firstItems = await bounded(
    approvals.getChildren(),
    10_000,
    "approval tree load"
  );
  const secondItems = await bounded(
    approvals.getChildren(),
    10_000,
    "approval tree reload"
  );
  assert.equal(firstItems.length, 1);
  assert.equal(secondItems.length, 1);
  assert.deepEqual(firstItems[0]?.value, approval);
  assert.deepEqual(secondItems[0]?.value, approval);

  const decided = await bounded(
    client.decideApproval(
      accepted.run_id,
      approval.approval_id,
      "approve",
      {
        revision: approval.revision ?? 1,
        reason: "Fresh-profile exact deletion approval"
      }
    ),
    10_000,
    "approval decision"
  );
  assert.equal(decided.approval.state, "approved");
  const repeated = await bounded(
    client.decideApproval(
      accepted.run_id,
      approval.approval_id,
      "approve",
      {
        revision: approval.revision ?? 1,
        reason: "Fresh-profile idempotency check"
      }
    ),
    10_000,
    "repeated approval decision"
  );
  assert.equal(repeated.idempotent, true);
  await bounded(client.resume(accepted.run_id), 15_000, "approved run resume");
  await waitForRunStatus(client, accepted.run_id, ["succeeded"], 60_000);
  assertUniqueRuns(api.runs());
  return accepted.run_id;
}

async function submitAfterWorkspaceRelease(
  request: RunCreateRequest
): Promise<RunAcceptedResponse> {
  return waitFor(async () => {
    try {
      return await vscode.commands.executeCommand<RunAcceptedResponse>(
        "agentbus.submitRun",
        request
      );
    } catch (error) {
      if (/Workspace already has an active AgentBus run/iu.test(String(error))) {
        return undefined;
      }
      throw error;
    }
  }, 15_000);
}

async function exerciseReplay(
  client: FreshClient,
  runId: string
): Promise<string> {
  const replay = await bounded(
    vscode.commands.executeCommand<ReplayAcceptedResponse>(
      "agentbus.replayRunOffline",
      runId
    ),
    15_000,
    "stress replay submission"
  );
  assert.ok(replay?.replay_id, "Stress replay was not accepted");
  const completed = await waitForReplay(client, replay.replay_id);
  assert.equal(completed.provider_calls, 0);
  assert.equal(completed.network_calls, 0);
  return replay.replay_id;
}

async function exerciseSecondaryWorkspaceRun(
  client: FreshClient,
  workspace: string
): Promise<string> {
  const accepted = await submitAfterWorkspaceRelease({
    task: "Implement only inside the explicitly selected secondary repository.",
    workspace,
    provider: "deterministic",
    workflow: "multi",
    durable: true,
    parallel: false,
    max_workers: 1,
    commit_changes: false,
    keep_worktrees: false,
    deterministic: { profile: "python-calculator" }
  });
  assert.ok(accepted?.run_id, "Secondary workspace run was not accepted");
  assert.equal(
    await canonicalPath(accepted.workspace),
    await canonicalPath(workspace)
  );
  const run = await waitForRun(client, accepted.run_id);
  assert.equal(await canonicalPath(run.workspace), await canonicalPath(workspace));
  const changes = await client.changes(run.run_id);
  assert.equal(
    await canonicalPath(changes.workspace),
    await canonicalPath(workspace)
  );
  assert.deepEqual(
    changes.changes.map((change) => change.path).sort(),
    ["agentbus_result.py", "test_agentbus_result.py"]
  );
  const diff = await client.diff(run.run_id);
  assert.match(diff.diff, /agentbus_result\.py/u);
  assert.doesNotMatch(diff.diff, /delete_me\.txt/u);
  const report = await client.report(run.run_id);
  assert.equal(
    await canonicalPath(String(report.report.workspace)),
    await canonicalPath(workspace)
  );
  return run.run_id;
}

async function exerciseSyntheticMcpFailure(
  api: AgentBusExtensionApi,
  configuration: vscode.WorkspaceConfiguration,
  configPath: string,
  mcpConfigPath: string
): Promise<FreshClient> {
  await configuration.update(
    "configPath",
    mcpConfigPath,
    vscode.ConfigurationTarget.Global
  );
  try {
    await bounded(
      vscode.commands.executeCommand("agentbus.restartDaemon"),
      30_000,
      "MCP diagnostic daemon restart"
    );
    const client = await bounded(
      api.client(),
      10_000,
      "MCP diagnostic daemon connection"
    );
    const servers = await bounded(client.mcpServers(), 10_000, "MCP server list");
    assert.deepEqual(
      servers.servers.map((server) => server.server_id),
      ["synthetic-failure"]
    );
    const check = await bounded(
      client.checkMcpServer("synthetic-failure"),
      15_000,
      "synthetic MCP failure"
    );
    assert.equal(check.ready, false);
    assert.equal(check.cleanup_completed, true);
    assert.ok((check.message?.length ?? 0) <= 1_000);
  } finally {
    await configuration.update(
      "configPath",
      configPath,
      vscode.ConfigurationTarget.Global
    );
  }
  await bounded(
    vscode.commands.executeCommand("agentbus.restartDaemon"),
    30_000,
    "post-MCP daemon recovery"
  );
  return bounded(api.client(), 10_000, "post-MCP daemon connection");
}

async function untrustedFlow(api: AgentBusExtensionApi): Promise<void> {
  assert.equal(
    vscode.workspace.isTrusted,
    false,
    "Fresh trust profile unexpectedly started trusted"
  );
  const onboarding = await api.onboardingState();
  assert.ok(onboarding);
  assert.equal(onboarding.trusted, false);
  assert.equal(onboarding.daemon, "not_checked");
  await assert.rejects(
    bounded(api.client(), 10_000, "untrusted daemon rejection"),
    /disabled until the workspace is trusted/iu
  );
  assert.equal(api.daemonId(), undefined);
}

async function trustedRecoveryFlow(
  api: AgentBusExtensionApi,
  workspace: string,
  handoffPath: string
): Promise<void> {
  assert.equal(vscode.workspace.isTrusted, true);
  const handoff = JSON.parse(await readFile(handoffPath, "utf8")) as Handoff;
  assert.ok(handoff.cancelledRunId);
  assert.ok(handoff.approvalRunId);
  assert.ok(handoff.secondaryRunId);
  assert.ok(handoff.stressReplayId);
  const client = await waitFor(async () => {
    try {
      return await bounded(api.client(), 10_000, "trusted recovery connection");
    } catch {
      return undefined;
    }
  });
  assert.ok(api.daemonId(), "Trusted transition did not start a daemon");
  assert.equal((await client.run(handoff.runId)).status, "succeeded");
  assert.equal((await client.run(handoff.cancelledRunId)).status, "cancelled");
  assert.equal((await client.run(handoff.approvalRunId)).status, "succeeded");
  assert.equal((await client.run(handoff.secondaryRunId)).status, "succeeded");
  assert.equal((await client.replay(handoff.stressReplayId)).status, "succeeded");
  assert.equal(
    (await client.attachWorkspaceIndex({ workspace })).workspace_id,
    handoff.workspaceId
  );
  await bounded(
    vscode.commands.executeCommand("agentbus.refresh"),
    15_000,
    "trusted recovery refresh"
  );
  assertUniqueRuns(api.runs());
  await bounded(
    vscode.commands.executeCommand("agentbus.stopDaemon"),
    20_000,
    "trusted recovery daemon stop"
  );
  await waitFor(() => api.daemonId() === undefined ? true : undefined);
}

type FreshClient = Awaited<ReturnType<AgentBusExtensionApi["client"]>>;

async function waitForRunStatus(
  client: FreshClient,
  runId: string,
  expectedStatuses: string[],
  timeoutMs: number
): Promise<RunSummary> {
  return waitFor(async () => {
    let run: RunSummary;
    try {
      run = await client.run(runId);
    } catch {
      return undefined;
    }
    if (expectedStatuses.includes(run.status)) return run;
    if (["succeeded", "failed", "cancelled"].includes(run.status)) {
      throw new Error(
        `Run ${runId} reached ${run.status}; expected ${expectedStatuses.join(", ")}.`
      );
    }
    return undefined;
  }, timeoutMs);
}

async function waitForCancellationCleanup(
  client: FreshClient,
  runId: string
): Promise<RunSummary> {
  return waitFor(async () => {
    let run: RunSummary;
    try {
      run = await client.run(runId);
    } catch {
      return undefined;
    }
    if (
      run.status === "cancelled" &&
      run.cancellation?.cleanup_completed === true
    ) {
      return run;
    }
    if (["succeeded", "failed"].includes(run.status)) {
      throw new Error(
        `Cancellation run ${runId} reached unexpected status ${run.status}.`
      );
    }
    return undefined;
  }, 45_000);
}

async function waitForPendingApproval(
  client: FreshClient,
  runId: string
): Promise<ApprovalSummary> {
  return waitFor(async () => {
    const run = await client.run(runId);
    if (["succeeded", "failed", "cancelled"].includes(run.status)) {
      throw new Error(`Approval run terminated as ${run.status}.`);
    }
    const pending = (await client.approvals(runId)).approvals.filter(
      (approval) => approval.state === "pending"
    );
    if (run.status !== "waiting_for_approval" || pending.length === 0) {
      return undefined;
    }
    assert.equal(pending.length, 1, "Approval API returned duplicate pending items");
    return pending[0];
  }, 45_000);
}

async function assertRegistryRejectedIncompatibleDaemon(
  registryPath: string,
  incompatibleDaemonId: string
): Promise<void> {
  const registry = JSON.parse(await readFile(registryPath, "utf8")) as {
    daemons?: Array<{ daemon_id?: unknown }>;
  };
  assert.ok(Array.isArray(registry.daemons));
  assert.equal(
    registry.daemons.some(
      (entry) => entry.daemon_id === incompatibleDaemonId
    ),
    false,
    "Incompatible daemon registry entry was not discarded"
  );
}

function assertUniqueRuns(runs: RunSummary[]): void {
  const runIds = runs.map((run) => run.run_id);
  assert.equal(
    new Set(runIds).size,
    runIds.length,
    "Run tree contains duplicate restored entries"
  );
}

async function canonicalPath(value: string): Promise<string> {
  const normalized = await realpath(resolve(value));
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function safeDiagnostic(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return text
    .replace(
      /(Bearer\s+|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*|token\s*[:=]\s*)[^\s,;]+/giu,
      "$1[REDACTED]"
    )
    .slice(0, 2_000);
}

async function bounded<T>(
  operation: PromiseLike<T>,
  timeoutMs: number,
  label: string
): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error(`Timed out waiting for ${label}.`)),
      timeoutMs
    );
  });
  try {
    return await Promise.race([operation, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function waitForRun(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
): Promise<RunSummary> {
  let lastRun: RunSummary | undefined;
  try {
    return await waitFor(async () => {
      try {
        lastRun = await client.run(runId);
      } catch {
        return undefined;
      }
      if (lastRun.status === "succeeded") return lastRun;
      if (["failed", "cancelled"].includes(lastRun.status)) {
        throw new Error(
          `Fresh-profile run reached ${lastRun.status}: ${
            lastRun.failure_reason ?? "unknown"
          }`
        );
      }
      return undefined;
    }, 90_000);
  } catch (error) {
    if (!/Timed out waiting/iu.test(String(error))) throw error;
    let taskState = "unavailable";
    try {
      const tasks = await client.tasks(runId);
      taskState = tasks.tasks.map((task) =>
        [
          task.task_id,
          task.status,
          task.failure_category ?? "none",
          task.failure_message ?? "none"
        ].join(":")
      ).join(" | ");
    } catch {
      // The last run status still makes a lost daemon distinguishable.
    }
    throw new Error(
      `Fresh-profile run timed out at ${lastRun?.status ?? "unavailable"}; tasks=${safeDiagnostic(taskState)}`
    );
  }
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
