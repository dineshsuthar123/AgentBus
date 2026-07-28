import assert from "node:assert/strict";
import { readFile, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import * as vscode from "vscode";
import { AgentBusApiError, type AgentBusClient } from "../../apiClient";
import { formatApprovalConfirmation } from "../../approvalPresentation";
import { toolArtifactUri } from "../../artifactDocuments";
import type { AgentBusExtensionApi } from "../../extension";
import type {
  ApprovalSummary,
  CancelResponse,
  ComparisonResponse,
  RegressionFixtureCaptureResponse,
  ReplayAcceptedResponse,
  ReplaySessionResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary,
  TraceArchiveExportResponse,
  TraceArchiveImportResponse,
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
  const artifactRoot = requiredEnvironment("AGENTBUS_E2E_ARTIFACT_ROOT");
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
  assert.ok(commands.includes("agentbus.showExecutionTimeline"));
  assert.ok(commands.includes("agentbus.showSpan"));
  assert.ok(commands.includes("agentbus.showReplaySession"));
  assert.ok(commands.includes("agentbus.replayRunOffline"));
  assert.ok(commands.includes("agentbus.replayFromCheckpoint"));
  assert.ok(commands.includes("agentbus.forkRun"));
  assert.ok(commands.includes("agentbus.showComparison"));
  assert.ok(commands.includes("agentbus.openStructuredReplayDifferences"));
  assert.ok(commands.includes("agentbus.exportTrace"));
  assert.ok(commands.includes("agentbus.importTrace"));
  assert.ok(commands.includes("agentbus.captureRegressionFixture"));
  assert.ok(commands.includes("agentbus.openProvenanceManifest"));

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

  const replayLifecycle = await exerciseReplayLifecycle({
    client,
    runId: toolRun.run_id,
    workspace,
    artifactRoot,
    mcpPrivateMarker
  });

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
  const recoveredReplays = await recoveredClient.listReplays(
    replayLifecycle.traceId,
    undefined,
    500
  );
  const recoveredReplayIds = new Set(
    recoveredReplays.replays.map((replay) => replay.replay_id)
  );
  assert.ok(recoveredReplayIds.has(replayLifecycle.fullReplayId));
  assert.ok(recoveredReplayIds.has(replayLifecycle.checkpointReplayId));
  assert.ok(recoveredReplayIds.has(replayLifecycle.forkReplayId));
  assert.ok(
    recoveredReplays.replays.every(
      (replay) =>
        !recoveredReplayIds.has(replay.replay_id) ||
        replay.status === "succeeded"
    )
  );
  const recoveredComparison = await recoveredClient.comparison(
    replayLifecycle.comparisonId,
    0,
    500
  );
  assert.equal(
    recoveredComparison.right_trace_id,
    replayLifecycle.forkTraceId
  );
  assert.ok(recoveredComparison.categories?.includes("expected"));
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

interface ReplayLifecycleInput {
  client: AgentBusClient;
  runId: string;
  workspace: string;
  artifactRoot: string;
  mcpPrivateMarker: string;
}

interface ReplayLifecycleState {
  traceId: string;
  fullReplayId: string;
  checkpointReplayId: string;
  forkReplayId: string;
  forkTraceId: string;
  comparisonId: string;
}

async function exerciseReplayLifecycle(
  input: ReplayLifecycleInput
): Promise<ReplayLifecycleState> {
  const {
    client,
    runId,
    workspace,
    artifactRoot,
    mcpPrivateMarker
  } = input;
  const reportBefore = await client.report(runId);
  const diffBefore = await client.diff(runId);
  const sourceBefore = await readFile(
    join(workspace, "acceptance_tool.py"),
    "utf8"
  );
  const trace = await client.trace(runId);
  assert.equal(trace.status, "succeeded");
  assert.ok(trace.completed_at);
  assert.ok(trace.checkpoint_count > 0);
  assert.equal(trace.checkpoints_truncated, false);

  const spanPage = await client.traceSpans(runId, 0, 500);
  assert.equal(spanPage.truncated, false);
  const spanTypes = new Set(spanPage.spans.map((span) => span.span_type));
  for (const required of [
    "provider_request",
    "provider_response",
    "model_parse",
    "tool_policy",
    "tool_invocation",
    "verifier",
    "reviewer"
  ]) {
    assert.ok(spanTypes.has(required), `Missing ${required} timeline span`);
  }
  await vscode.commands.executeCommand("agentbus.showExecutionTimeline");
  const providerSpan = spanPage.spans.find(
    (span) => span.span_type === "provider_response"
  );
  assert.ok(providerSpan);
  await vscode.commands.executeCommand("agentbus.showSpan", {
    value: providerSpan
  });
  const spanDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-span" &&
        document.uri.path.includes(providerSpan.span_id)
    )
  );
  assert.match(spanDocument.getText(), /provider\\_response/);
  assert.doesNotMatch(spanDocument.getText(), new RegExp(mcpPrivateMarker));

  const provenance = await client.provenance(runId);
  assert.equal(provenance.trace_id, trace.trace_id);
  assert.match(provenance.integrity_root, /^[0-9a-f]{64}$/);
  await vscode.commands.executeCommand(
    "agentbus.openProvenanceManifest",
    runId
  );
  const provenanceDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-provenance" &&
        document.uri.path.includes(runId)
    )
  );
  assert.match(provenanceDocument.getText(), /Provenance root/);
  assert.match(provenanceDocument.getText(), /Replayable offline/);
  assert.doesNotMatch(
    provenanceDocument.getText(),
    new RegExp(mcpPrivateMarker)
  );

  const fullAccepted = await vscode.commands.executeCommand<
    ReplayAcceptedResponse
  >("agentbus.replayRunOffline", runId);
  assert.ok(fullAccepted?.replay_id);
  const fullReplay = await waitForReplay(client, fullAccepted.replay_id);
  assertProviderlessReplay(fullReplay, false);
  assert.ok(
    fullReplay.span_results?.some(
      (result) =>
        result.action === "replayed" &&
        result.span_id ===
          spanPage.spans.find((span) => span.span_type === "model_parse")
            ?.span_id
    )
  );
  await vscode.commands.executeCommand("agentbus.showReplaySession", {
    value: fullReplay
  });
  const replayDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-replay" &&
        document.uri.path.includes(fullReplay.replay_id)
    )
  );
  assert.match(replayDocument.getText(), /Provider calls \| 0/);
  assert.match(replayDocument.getText(), /Network calls \| 0/);
  assert.match(replayDocument.getText(), /Captured provider payloads are not rendered/);

  const checkpoint = trace.checkpoints?.find(
    (candidate) =>
      candidate.replayable &&
      candidate.label === "task-graph-persisted"
  ) ?? trace.checkpoints?.find((candidate) => candidate.replayable);
  assert.ok(checkpoint, "The completed run has no replayable checkpoint");
  const checkpointAccepted = await vscode.commands.executeCommand<
    ReplayAcceptedResponse
  >("agentbus.replayFromCheckpoint", {
    runId,
    checkpointId: checkpoint.checkpoint_id
  });
  assert.ok(checkpointAccepted?.replay_id);
  const checkpointReplay = await waitForReplay(
    client,
    checkpointAccepted.replay_id
  );
  assertProviderlessReplay(checkpointReplay, true);
  assert.equal(
    checkpointReplay.from_checkpoint_id,
    checkpoint.checkpoint_id
  );

  const forkAccepted = await vscode.commands.executeCommand<
    ReplayAcceptedResponse
  >("agentbus.forkRun", {
    runId,
    changedInputs: {
      resource_budgets: { invocations_per_run: 64 }
    }
  });
  assert.ok(forkAccepted?.replay_id);
  const forkReplay = await waitForReplay(client, forkAccepted.replay_id);
  assertProviderlessReplay(forkReplay, false);
  assert.equal(forkReplay.fork, true);
  assert.deepEqual(forkReplay.changed_input_names, ["resource_budgets"]);
  assert.ok(forkReplay.result_trace_id);
  assert.ok(forkReplay.comparison_id);
  const comparison = await client.comparison(
    forkReplay.comparison_id,
    0,
    500
  );
  assertExpectedComparison(
    comparison,
    trace.trace_id,
    forkReplay.result_trace_id,
    provenance.integrity_root
  );
  await vscode.commands.executeCommand("agentbus.showComparison", {
    value: { comparison }
  });
  const comparisonDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-comparison" &&
        document.uri.path.includes(comparison.comparison_id)
    )
  );
  assert.match(comparisonDocument.getText(), /Expected Differences/);
  assert.match(comparisonDocument.getText(), /Provenance root changed \| true/);
  await vscode.commands.executeCommand(
    "agentbus.openStructuredReplayDifferences",
    { value: { comparison } }
  );
  const comparisonSides = await waitFor(() => {
    const documents = vscode.workspace.textDocuments.filter(
      (document) =>
        document.uri.scheme === "agentbus-comparison-side" &&
        document.uri.path.includes(comparison.comparison_id)
    );
    return documents.length === 2 ? documents : undefined;
  });
  assert.ok(
    comparisonSides.every((document) =>
      document.getText().includes('"categories"')
    )
  );
  assert.ok(
    comparisonSides.every((document) =>
      document.getText().includes('"sha256"')
    )
  );

  const tracePath = join(
    artifactRoot,
    `${trace.trace_id}.agentbus-trace`
  );
  const corruptPath = join(
    artifactRoot,
    `${trace.trace_id}.corrupt.agentbus-trace`
  );
  const fixturePath = join(
    artifactRoot,
    `${trace.trace_id}.regression.agentbus-trace`
  );
  assert.equal(tracePath.startsWith(workspace), false);
  const exported = await vscode.commands.executeCommand<
    TraceArchiveExportResponse
  >("agentbus.exportTrace", {
    runId,
    destination: tracePath,
    includeSourceContent: true
  });
  assert.ok(exported);
  assert.equal(exported.trace_id, trace.trace_id);
  assert.equal(exported.source_content_included, true);
  const archiveBytes = await readFile(tracePath);
  assert.ok(archiveBytes.length > 0 && archiveBytes.length <= 650_000);
  assert.equal((await stat(tracePath)).size, archiveBytes.length);

  const imported = await vscode.commands.executeCommand<
    TraceArchiveImportResponse
  >("agentbus.importTrace", {
    source: tracePath,
    allowSourceContent: true
  });
  assert.ok(imported);
  assert.equal(imported.trace_id, trace.trace_id);
  assert.equal(imported.replay_started, false);

  const replayCountBeforeCorruption = (
    await client.listReplays(trace.trace_id, undefined, 500)
  ).total;
  const corruptArchive = Buffer.from(archiveBytes);
  corruptArchive[0] = (corruptArchive[0] ?? 0) ^ 0xff;
  await writeFile(corruptPath, corruptArchive);
  let corruptionRejected = false;
  try {
    await vscode.commands.executeCommand("agentbus.importTrace", {
      source: corruptPath,
      allowSourceContent: true
    });
  } catch (error) {
    assert.ok(error instanceof AgentBusApiError);
    assert.equal(error.status, 409);
    corruptionRejected = true;
  }
  assert.equal(corruptionRejected, true);
  assert.equal(
    (await client.listReplays(trace.trace_id, undefined, 500)).total,
    replayCountBeforeCorruption
  );

  const fixture = await vscode.commands.executeCommand<
    RegressionFixtureCaptureResponse
  >("agentbus.captureRegressionFixture", {
    runId,
    destination: fixturePath,
    includeSourceContent: true
  });
  assert.ok(fixture);
  assert.equal(fixture.trace_id, trace.trace_id);
  assert.equal(fixture.assertions_validated, true);
  assert.equal(fixture.replay_started, false);
  assert.ok((await stat(fixturePath)).size > 0);

  assert.deepEqual(await client.report(runId), reportBefore);
  assert.deepEqual(await client.diff(runId), diffBefore);
  assert.equal(
    await readFile(join(workspace, "acceptance_tool.py"), "utf8"),
    sourceBefore
  );
  return {
    traceId: trace.trace_id,
    fullReplayId: fullReplay.replay_id,
    checkpointReplayId: checkpointReplay.replay_id,
    forkReplayId: forkReplay.replay_id,
    forkTraceId: forkReplay.result_trace_id,
    comparisonId: forkReplay.comparison_id
  };
}

async function waitForReplay(
  client: AgentBusClient,
  replayId: string
): Promise<ReplaySessionResponse> {
  const terminal = new Set([
    "succeeded",
    "failed",
    "cancelled",
    "incompatible",
    "awaiting_input"
  ]);
  return waitFor(async () => {
    try {
      const replay = await client.replay(replayId);
      return terminal.has(replay.status) ? replay : undefined;
    } catch {
      return undefined;
    }
  }, 60_000);
}

function assertProviderlessReplay(
  replay: ReplaySessionResponse,
  isolated: boolean
): void {
  assert.equal(replay.status, "succeeded", replay.failure_message ?? undefined);
  assert.equal(replay.provider_calls, 0);
  assert.equal(replay.network_calls, 0);
  assert.deepEqual(replay.missing_inputs, []);
  assert.equal(replay.isolated, isolated);
  assert.equal(
    replay.isolation_scope,
    isolated ? "daemon_managed_temporary_workspace" : null
  );
}

function assertExpectedComparison(
  comparison: ComparisonResponse,
  sourceTraceId: string,
  forkTraceId: string,
  sourceProvenanceRoot: string
): void {
  assert.equal(comparison.left_trace_id, sourceTraceId);
  assert.equal(comparison.right_trace_id, forkTraceId);
  assert.equal(comparison.left_provenance_root, sourceProvenanceRoot);
  assert.notEqual(comparison.right_provenance_root, sourceProvenanceRoot);
  assert.equal(comparison.summary.provenance_root_changed, true);
  assert.ok(comparison.categories?.includes("expected"));
  assert.ok(
    comparison.summary.changed_spans +
      comparison.summary.added_spans +
      comparison.summary.removed_spans >
      0
  );
  assert.equal(comparison.truncated, false);
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
