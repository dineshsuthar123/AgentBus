import assert from "node:assert/strict";
import * as vscode from "vscode";
import type { AgentBusExtensionApi } from "../../extension";
import type {
  CancelResponse,
  RunAcceptedResponse,
  RunCreateRequest,
  RunSummary
} from "../../generated/protocol";

export async function run(): Promise<void> {
  const pythonPath = requiredEnvironment("AGENTBUS_E2E_PYTHON");
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
  assert.ok(commands.includes("agentbus.runDoctor"));

  const client = await api.client();
  const initialDaemon = api.daemonId();
  assert.ok(initialDaemon, "AgentBus daemon did not start");
  const successful = await vscode.commands.executeCommand<RunAcceptedResponse>(
    "agentbus.submitRun",
    runRequest(workspace, "python-calculator", 0, [])
  );
  assert.ok(successful?.run_id);
  const completed = await waitForRun(client, successful.run_id, "succeeded");
  assert.equal(completed.reviewer_status, "approved");
  await waitFor(() =>
    api
      .events()
      .some(
        (event) =>
          event.run_id === successful.run_id &&
          event.event_type === "integration_commit_published"
      )
  );
  assert.ok(
    api
      .events()
      .some(
        (event) =>
          event.run_id === successful.run_id &&
          event.event_type === "durable_run_succeeded"
      ),
    `SSE did not deliver the terminal run transition: ${api
      .events()
      .filter((event) => event.run_id === successful.run_id)
      .map((event) => event.event_type)
      .join(", ")}`
  );
  await vscode.commands.executeCommand("agentbus.refresh");
  assert.equal(
    api.runs().find((run) => run.run_id === successful.run_id)?.status,
    "succeeded"
  );
  const tasks = await client.tasks(successful.run_id);
  assert.equal(tasks.tasks[0]?.status, "succeeded");
  assert.ok(
    api
      .events()
      .some(
        (event) =>
          event.run_id === successful.run_id &&
          event.event_type === "worker_started"
      ),
    "SSE did not deliver worker task-tree transitions"
  );

  await vscode.commands.executeCommand(
    "agentbus.openChange",
    successful.run_id,
    "agentbus_result.py"
  );
  const afterDocument = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) => document.uri.scheme === "agentbus-after"
    )
  );
  assert.match(afterDocument.getText(), /return left \+ right/);
  await vscode.commands.executeCommand("agentbus.openRunReport");
  const successReport = await waitFor(() =>
    vscode.workspace.textDocuments.find(
      (document) =>
        document.uri.scheme === "agentbus-report" &&
        document.getText().includes("**Status:** succeeded")
    )
  );
  assert.match(successReport.getText(), /commit_identifier/);

  const cancelling = await vscode.commands.executeCommand<RunAcceptedResponse>(
    "agentbus.submitRun",
    runRequest(
      workspace,
      "cancellation-two-task",
      30,
      ["coder"],
      "Exercise deterministic provider cancellation."
    )
  );
  assert.ok(cancelling?.run_id);
  await waitForProviderOperation(client, cancelling.run_id);
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

  await vscode.commands.executeCommand("agentbus.restartDaemon");
  await waitFor(() => {
    const current = api.daemonId();
    return current && current !== initialDaemon ? current : undefined;
  });
  const recovered = await (await api.client()).listRuns();
  assert.equal(
    recovered.runs.find((run) => run.run_id === successful.run_id)?.status,
    "succeeded"
  );
  assert.equal(
    recovered.runs.find((run) => run.run_id === cancelling.run_id)?.status,
    "cancelled"
  );
  await vscode.commands.executeCommand("agentbus.stopDaemon");
}

function runRequest(
  workspace: string,
  profile: "python-calculator" | "cancellation-two-task",
  latencySeconds: number,
  latencyRoles: Array<"planner" | "coder" | "reviewer" | "summarizer">,
  task = "Create and verify the deterministic calculator."
): RunCreateRequest {
  return {
    task,
    workspace,
    provider: "deterministic",
    workflow: "multi",
    durable: true,
    parallel: true,
    max_workers: 1,
    commit_changes: true,
    keep_worktrees: true,
    deterministic: {
      profile,
      latency_seconds: latencySeconds,
      latency_roles: latencyRoles
    }
  };
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
