import assert from "node:assert/strict";
import * as vscode from "vscode";
import { AgentBusApiError } from "../../apiClient";
import { formatApprovalConfirmation } from "../../approvalPresentation";
import { toolArtifactUri } from "../../artifactDocuments";
import type { AgentBusExtensionApi } from "../../extension";
import type {
  ApprovalSummary,
  CancelResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  ToolInvocationSummary
} from "../../generated/protocol";

type DeterministicProfile = NonNullable<
  NonNullable<RunCreateRequest["deterministic"]>["profile"]
>;

export async function run(): Promise<void> {
  const pythonPath = requiredEnvironment("AGENTBUS_E2E_PYTHON");
  const configPath = requiredEnvironment("AGENTBUS_E2E_CONFIG");
  const mcpPrivateMarker = requiredEnvironment("AGENTBUS_E2E_MCP_MARKER");
  const registryPath = requiredEnvironment("AGENTBUS_E2E_REGISTRY");
  const workspace = requiredEnvironment("AGENTBUS_E2E_WORKSPACE");
  const configuration = vscode.workspace.getConfiguration("agentbus");
  await configuration.update(
    "pythonPath",
    pythonPath,
    vscode.ConfigurationTarget.Global
  );
  await configuration.update(
    "registryPath",
    registryPath,
    vscode.ConfigurationTarget.Global
  );
  await configuration.update(
    "configPath",
    configPath,
    vscode.ConfigurationTarget.Global
  );
  await configuration.update(
    "defaultProvider",
    "deterministic",
    vscode.ConfigurationTarget.Global
  );
  await configuration.update(
    "logLevel",
    "error",
    vscode.ConfigurationTarget.Global
  );

  const extension = vscode.extensions.getExtension<AgentBusExtensionApi>(
    "agentbus.agentbus-vscode"
  );
  assert.ok(extension, "AgentBus extension was not discovered");
  const api = await extension.activate();
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes("agentbus.startTask"));
  assert.ok(commands.includes("agentbus.submitRun"));
  assert.ok(commands.includes("agentbus.requestCancellation"));
  assert.ok(commands.includes("agentbus.openChange"));
  assert.ok(commands.includes("agentbus.showToolInvocation"));
  assert.ok(commands.includes("agentbus.cancelToolInvocation"));
  assert.ok(commands.includes("agentbus.openToolArtifact"));
  assert.ok(commands.includes("agentbus.checkMcpServer"));
  assert.ok(commands.includes("agentbus.runDoctor"));

  const client = await api.client();
  const initialDaemon = api.daemonId();
  assert.ok(initialDaemon, "AgentBus daemon did not start");
  await waitFor(() => (api.eventStreamConnected() ? true : undefined));
  const toolRun = await submitAfterWorkspaceRelease(
    runRequest(
      workspace,
      "tool-control-acceptance",
      0,
      [],
      "Read, write, and verify through the managed tool runtime.",
      true
    )
  );
  assert.ok(toolRun?.run_id);
  const completed = await waitForRun(client, toolRun.run_id, "succeeded");
  assert.equal(completed.reviewer_status, "approved");
  await waitForEvent(api, toolRun.run_id, "commit_created");
  await waitForEvent(api, toolRun.run_id, "tool_succeeded");
  await waitForEvent(api, toolRun.run_id, "durable_run_succeeded");
  await vscode.commands.executeCommand("agentbus.refresh");
  assert.equal(
    api.runs().find((run) => run.run_id === toolRun.run_id)?.status,
    "succeeded"
  );
  const tasks = await client.tasks(toolRun.run_id);
  assert.equal(tasks.tasks[0]?.status, "succeeded");
  assert.ok(
    api
      .events()
      .some(
        (event) =>
          event.run_id === toolRun.run_id &&
          event.event_type === "durable_task_started"
      ),
    "SSE did not deliver durable task-tree transitions"
  );
  await vscode.commands.executeCommand("agentbus.tools.focus");
  const toolInvocations = await waitFor(async () => {
    const response = await client.toolInvocations(toolRun.run_id);
    return response.invocations.some(
      (invocation) => invocation.tool_name === "filesystem.write"
    )
      ? response.invocations
      : undefined;
  });
  const writeInvocation = requiredInvocation(
    toolInvocations,
    "filesystem.write",
    "coder"
  );
  const pytestInvocation = requiredInvocation(
    toolInvocations,
    "test.execute",
    "coder"
  );
  assert.ok(toolInvocations.every((invocation) => invocation.policy_decision));
  await vscode.commands.executeCommand("agentbus.showToolInvocation", {
    value: pytestInvocation
  });
  const toolDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-tool"
    )
  );
  assert.match(toolDocument.getText(), /safe_diagnostic_metadata/);
  assert.match(toolDocument.getText(), /stdout_truncated/);
  assert.match(toolDocument.getText(), /Raw tool arguments, stdout, stderr/);

  const writeDetail = await client.toolInvocation(
    toolRun.run_id,
    writeInvocation.invocation_id
  );
  const artifact = writeDetail.result?.artifacts?.find(
    (candidate) => candidate.relative_path === "acceptance_tool.py"
  );
  assert.ok(artifact, "Managed write did not expose its safe source artifact");
  const artifactDocument = await vscode.workspace.openTextDocument(
    toolArtifactUri(toolRun.run_id, writeInvocation.invocation_id, artifact.artifact_id)
  );
  await vscode.window.showTextDocument(artifactDocument, { preview: true });
  assert.match(artifactDocument.getText(), /return left \+ right/);

  await vscode.commands.executeCommand(
    "agentbus.openChange",
    toolRun.run_id,
    "acceptance_tool.py"
  );
  const afterDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-after"
    )
  );
  const beforeDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-before"
    )
  );
  assert.match(afterDocument.getText(), /return left \+ right/);
  assert.equal(beforeDocument.getText(), "");
  await vscode.commands.executeCommand("agentbus.openRunReport");
  const successReport = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-report" &&
        document.getText().includes("**Status:** succeeded")
    )
  );
  assert.match(successReport.getText(), /commit_identifier/);
  assert.match(successReport.getText(), /## Tool Runtime/);
  assert.match(successReport.getText(), /acceptance_tool\.py/);

  const approvalRun = await submitAfterWorkspaceRelease(
    runRequest(
      workspace,
      "tool-delete-approval",
      0,
      [],
      "Delete the deterministic target only after exact approval.",
      false
    )
  );
  assert.ok(approvalRun?.run_id);
  const approval = await waitForToolApproval(client, approvalRun.run_id);
  assert.equal(approval.tool_name, "filesystem.delete");
  assert.deepEqual(approval.affected_paths, ["delete_me.txt"]);
  assert.deepEqual(
    approval.capabilities?.map((capability) => capability.name),
    ["filesystem.delete"]
  );
  const approvalConfirmation = formatApprovalConfirmation(approval);
  assert.match(approvalConfirmation, /filesystem\.delete/);
  assert.match(approvalConfirmation, /delete_me\.txt/);
  await waitForEvent(api, approvalRun.run_id, "tool_approval_required");
  const approved = await client.decideApproval(
    approvalRun.run_id,
    approval.approval_id,
    "approve",
    { revision: approval.revision ?? 1, reason: "VS Code Electron exact approval" }
  );
  assert.equal(approved.approval.state, "approved");
  const resumed = await resumeAfterOwnerRelease(client, approvalRun.run_id);
  assert.equal(resumed.resumed, true);
  await waitForRun(client, approvalRun.run_id, "succeeded");
  await waitForEvent(api, approvalRun.run_id, "tool_approval_approved");
  const deletion = requiredInvocation(
    (await client.toolInvocations(approvalRun.run_id)).invocations,
    "filesystem.delete",
    "coder"
  );
  assert.equal(deletion.status, "succeeded");
  assert.equal(deletion.approval_id, approval.approval_id);

  const processRun = await submitAfterWorkspaceRelease(
    runRequest(
      workspace,
      "tool-process-cancel",
      0,
      [],
      "Cancel a running managed sandbox process.",
      false
    )
  );
  assert.ok(processRun?.run_id);
  const runningProcess = await waitForToolInvocation(
    client,
    processRun.run_id,
    (invocation) =>
      invocation.tool_name === "process.execute" &&
      invocation.status === "running"
  );
  const toolCancellation = await client.cancelToolInvocation(
    processRun.run_id,
    runningProcess.invocation_id,
    "VS Code Electron sandbox cancellation"
  );
  assert.equal(toolCancellation.run_cancellation_requested, true);
  assert.equal(toolCancellation.cancellation?.requested, true);
  await waitForRun(client, processRun.run_id, "cancelled");
  await waitForEvent(api, processRun.run_id, "tool_cancel_acknowledged");
  await waitForEvent(api, processRun.run_id, "tool_cleanup_completed");
  const cancelledProcess = await waitFor(async () => {
    const detail = await client.toolInvocation(
      processRun.run_id,
      runningProcess.invocation_id
    );
    return detail.status === "cancelled" ? detail : undefined;
  });
  assert.equal(cancelledProcess.result?.cancellation?.acknowledged, true);
  assert.equal(cancelledProcess.result?.cancellation?.process_terminated, true);
  assert.equal(cancelledProcess.result?.cancellation?.cleanup_completed, true);
  const processReport = await client.report(processRun.run_id);
  assert.equal(processReport.status, "cancelled");
  assert.equal(
    recordValue(processReport.report.tool_runtime)?.cancellation_count,
    1
  );
  await vscode.commands.executeCommand("agentbus.openRunReport");
  const processReportDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-report" &&
        document.getText().includes(`AgentBus Run ${processRun.run_id}`) &&
        document.getText().includes("**Status:** cancelled")
    )
  );
  assert.match(processReportDocument.getText(), /## Tool Runtime/);

  const cancelling = await submitAfterWorkspaceRelease(
    runRequest(
      workspace,
      "cancellation-two-task",
      30,
      ["coder"],
      "Exercise deterministic provider cancellation.",
      false
    )
  );
  assert.ok(cancelling?.run_id);
  await waitForProviderOperation(client, cancelling.run_id);
  await waitForEvent(api, cancelling.run_id, "worker_started");
  const firstCancel = await vscode.commands.executeCommand<CancelResponse>(
    "agentbus.requestCancellation",
    cancelling.run_id,
    "VS Code Electron acceptance"
  );
  assert.equal(firstCancel?.cancellation_requested, true);
  assert.equal(
    firstCancel?.cancellation?.provider_cancellation_signalled,
    true
  );
  const repeatedCancel = await vscode.commands.executeCommand<CancelResponse>(
    "agentbus.requestCancellation",
    cancelling.run_id,
    "Duplicate VS Code cancellation"
  );
  assert.equal(repeatedCancel?.cancellation_requested, true);
  const cancelled = await waitForRun(client, cancelling.run_id, "cancelled");
  assert.equal(cancelled.cancellation?.provider_cancellation_acknowledged, true);
  await waitFor(() =>
    api
      .events()
      .some(
        (event) =>
          event.run_id === cancelling.run_id &&
          event.event_type === "cancellation_cleanup_completed"
      )
  );
  assert.ok(
    api
      .events()
      .some(
        (event) =>
          event.run_id === cancelling.run_id &&
          event.event_type === "provider_cancellation_acknowledged"
      )
  );
  await vscode.commands.executeCommand("agentbus.openRunReport");
  const cancellationReport = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-report" &&
        document.getText().includes("Provider cancellation acknowledged")
    )
  );
  assert.match(cancellationReport.getText(), /Resume unavailable/);

  const mcpServers = await client.mcpServers();
  assert.equal(mcpServers.total, 1);
  const mcpServer = mcpServers.servers[0];
  assert.ok(mcpServer, "Configured MCP fixture was not returned");
  assert.equal(mcpServer.server_id, "fixture");
  await vscode.commands.executeCommand("agentbus.showMcpServer", {
    value: mcpServer
  });
  const mcpDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-mcp"
    )
  );
  assert.match(mcpDocument.getText(), /mcp\\\.fixture\\\.echo/);
  assert.doesNotMatch(mcpDocument.getText(), new RegExp(mcpPrivateMarker));
  assert.doesNotMatch(mcpDocument.getText(), /fake_server\.py/);
  await vscode.commands.executeCommand("agentbus.checkMcpServer", {
    value: mcpServer
  });
  const mcpCheckDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find((document) => {
      const text = document.getText();
      return text.includes("# MCP Check fixture") && text.includes("Ready | yes");
    })
  );
  assert.match(mcpCheckDocument.getText(), /Cleanup completed \| yes/);
  assert.doesNotMatch(mcpCheckDocument.getText(), new RegExp(mcpPrivateMarker));

  await vscode.commands.executeCommand("agentbus.restartDaemon");
  await waitFor(() => {
    const current = api.daemonId();
    return current && current !== initialDaemon ? current : undefined;
  });
  await waitFor(() => (api.eventStreamConnected() ? true : undefined));
  const recovered = await (await api.client()).listRuns();
  assert.equal(
    recovered.runs.find((run) => run.run_id === toolRun.run_id)?.status,
    "succeeded"
  );
  assert.equal(
    recovered.runs.find((run) => run.run_id === approvalRun.run_id)?.status,
    "succeeded"
  );
  assert.equal(
    recovered.runs.find((run) => run.run_id === processRun.run_id)?.status,
    "cancelled"
  );
  assert.equal(
    recovered.runs.find((run) => run.run_id === cancelling.run_id)?.status,
    "cancelled"
  );
  const recoveredClient = await api.client();
  assert.equal(
    requiredInvocation(
      (await recoveredClient.toolInvocations(toolRun.run_id)).invocations,
      "filesystem.write",
      "coder"
    ).status,
    "succeeded"
  );
  assert.equal(
    requiredInvocation(
      (await recoveredClient.toolInvocations(processRun.run_id)).invocations,
      "process.execute",
      "coder"
    ).status,
    "cancelled"
  );
  assert.equal((await recoveredClient.mcpServers()).servers[0]?.server_id, "fixture");
  await vscode.commands.executeCommand("agentbus.stopDaemon");
}

function runRequest(
  workspace: string,
  profile: DeterministicProfile,
  latencySeconds: number,
  latencyRoles: Array<"planner" | "coder" | "reviewer" | "summarizer">,
  task = "Create and verify the deterministic calculator.",
  commitChanges = true
): RunCreateRequest {
  return {
    task,
    workspace,
    provider: "deterministic",
    workflow: "multi",
    durable: true,
    parallel: profile === "cancellation-two-task",
    max_workers: 1,
    commit_changes: commitChanges,
    keep_worktrees: true,
    deterministic: {
      profile,
      latency_seconds: latencySeconds,
      latency_roles: latencyRoles
    }
  };
}

async function waitForEvent(
  api: AgentBusExtensionApi,
  runId: string,
  eventType: string
): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (
      api
        .events()
        .some(
          (event) =>
            event.run_id === runId && event.event_type === eventType
        )
    ) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const observed = api
    .events()
    .filter((event) => event.run_id === runId)
    .map((event) => event.event_type)
    .join(", ");
  throw new Error(
    `SSE did not deliver ${eventType}; connected=${api.eventStreamConnected()}; observed=${observed || "none"}`
  );
}

async function waitForToolApproval(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
): Promise<ApprovalSummary> {
  return waitFor(async () => {
    try {
      const response = await client.approvals(runId);
      return response.approvals.find(
        (approval) =>
          approval.approval_kind === "tool" && approval.state === "pending"
      );
    } catch {
      return undefined;
    }
  });
}

async function waitForToolInvocation(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string,
  predicate: (invocation: ToolInvocationSummary) => boolean
): Promise<ToolInvocationSummary> {
  return waitFor(async () => {
    try {
      return (await client.toolInvocations(runId)).invocations.find(predicate);
    } catch {
      return undefined;
    }
  });
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
      if (
        error instanceof AgentBusApiError &&
        error.status === 409 &&
        /^Workspace already has an active AgentBus run: [0-9a-f]+\.$/.test(
          error.message
        )
      ) {
        return undefined;
      }
      throw error;
    }
  });
}

async function resumeAfterOwnerRelease(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
) {
  return waitFor(async () => {
    try {
      return await client.resume(runId);
    } catch (error) {
      if (
        error instanceof AgentBusApiError &&
        error.status === 409 &&
        error.message === "The run already has an active owner."
      ) {
        return undefined;
      }
      throw error;
    }
  });
}

function requiredInvocation(
  invocations: ToolInvocationSummary[],
  toolName: string,
  callerRole: string
): ToolInvocationSummary {
  const invocation = invocations.find(
    (candidate) =>
      candidate.tool_name === toolName && candidate.caller_role === callerRole
  );
  assert.ok(
    invocation,
    `Missing ${callerRole} ${toolName} invocation: ${invocations
      .map((candidate) => `${candidate.caller_role}:${candidate.tool_name}`)
      .join(", ")}`
  );
  return invocation;
}

async function waitForRun(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string,
  expected: string
): Promise<RunSummary> {
  return waitFor(async () => {
    try {
      const run = await client.run(runId);
      return run.status === expected ? run : undefined;
    } catch {
      return undefined;
    }
  }, 90_000);
}

async function waitForProviderOperation(
  client: Awaited<ReturnType<AgentBusExtensionApi["client"]>>,
  runId: string
): Promise<void> {
  await waitFor(async () => {
    try {
      const response = await client.report(runId);
      const cancellation = recordValue(response.report.cancellation);
      const operations = Array.isArray(cancellation?.active_operations)
        ? cancellation.active_operations
        : [];
      return operations.some((value) => {
        const operation = recordValue(value);
        return (
          operation?.provider === "deterministic" &&
          operation?.name === "deterministic.coder.generate"
        );
      })
        ? true
        : undefined;
    } catch {
      return undefined;
    }
  }, 30_000);
}

async function waitFor<T>(
  check: () => T | undefined | Promise<T | undefined>,
  timeoutMilliseconds = 30_000
): Promise<T> {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const value = await check();
    if (value !== undefined && value !== false) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("Timed out waiting for AgentBus Electron state.");
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name} for AgentBus Electron test.`);
  return value;
}
