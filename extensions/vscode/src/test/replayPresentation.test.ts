import assert from "node:assert/strict";
import test from "node:test";
import type { ReplaySessionResponse } from "../generated/protocol";
import {
  formatReplayabilityDocument,
  formatReplayDocument,
  FORK_INPUT_NAMES,
  isTerminalReplayStatus,
  offlineReplayBlockReason,
  parseForkInput,
  REPLAY_GROUPS,
  replayableCheckpoints,
  replayGroup,
  validateForkInputs
} from "../replayPresentation";

const session: ReplaySessionResponse = {
  replay_id: "replay-1",
  source_trace_id: "trace-1",
  source_run_id: "run-1",
  mode: "offline",
  status: "succeeded",
  created_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:00:01Z",
  isolated: true,
  changed_input_names: ["budget"],
  substitutions: ["provider.response"],
  missing_inputs: [],
  policy_drift: ["policy-version"],
  provider_calls: 0,
  network_calls: 0,
  span_results: [
    {
      span_id: "span-1",
      action: "substitute",
      succeeded: true,
      summary: "token=private-value",
      output_sha256: "a".repeat(64)
    }
  ]
};

test("replay lifecycle maps to every native view category", () => {
  assert.deepEqual(
    REPLAY_GROUPS.map((group) => group.key),
    [
      "active",
      "succeeded",
      "failed",
      "cancelled",
      "incompatible",
      "awaiting_input"
    ]
  );
  assert.equal(replayGroup("pending"), "active");
  assert.equal(replayGroup("running"), "active");
  assert.equal(replayGroup("succeeded"), "succeeded");
  assert.equal(replayGroup("failed"), "failed");
  assert.equal(replayGroup("cancelled"), "cancelled");
  assert.equal(replayGroup("incompatible"), "incompatible");
  assert.equal(replayGroup("awaiting_input"), "awaiting_input");
});

test("replay document exposes safe state and hashes without raw summaries", () => {
  const rendered = formatReplayDocument(session);

  assert.match(rendered, /offline/);
  assert.match(rendered, /Provider calls \| 0/);
  assert.match(rendered, new RegExp("a{64}"));
  assert.match(rendered, /policy\\-version/);
  assert.doesNotMatch(rendered, /private-value/);
});

test("offline replay plan explains substitutions isolation and blockers", () => {
  const replayability = {
    trace_id: "trace-1",
    run_id: "run-1",
    level: "partially_replayable",
    replayable_offline: false,
    reasons: ["Captured model response is missing."],
    missing_input_hashes: ["b".repeat(64)],
    live_provider_consent_required: true,
    spans: [
      {
        span_id: "span-1",
        span_type: "tool_invocation",
        level: "partially_replayable",
        reasons: ["Mutation requires isolation."],
        required_input_count: 1,
        substitution_kinds: ["captured_tool_result"],
        requires_isolated_workspace: true
      },
      {
        span_id: "span-2",
        span_type: "provider_response",
        level: "non_replayable",
        reasons: ["Input unavailable."],
        required_input_count: 1,
        missing_input_hashes: ["b".repeat(64)],
        live_provider_consent_required: true
      }
    ]
  };

  const rendered = formatReplayabilityDocument(
    replayability,
    "offline"
  );

  assert.match(rendered, /Mode \| offline/);
  assert.match(rendered, /captured\\_tool\\_result/);
  assert.match(rendered, /daemon\\_managed\\_temporary\\_workspace/);
  assert.match(rendered, /Live\\-provider consent required \| true/);
  assert.match(rendered, new RegExp("b{64}"));
  assert.match(
    offlineReplayBlockReason(replayability) ?? "",
    /not replayable in offline mode/
  );
  assert.equal(isTerminalReplayStatus("succeeded"), true);
  assert.equal(isTerminalReplayStatus("running"), false);
});

test("checkpoint selection is replayable and sequence ordered", () => {
  const checkpoints = replayableCheckpoints([
    {
      checkpoint_id: "checkpoint-3",
      span_id: "span-3",
      sequence: 3,
      label: "task completed",
      replayable: true,
      created_at: "2026-01-01T00:00:03Z"
    },
    {
      checkpoint_id: "checkpoint-1",
      span_id: "span-1",
      sequence: 1,
      label: "planning completed",
      replayable: true,
      created_at: "2026-01-01T00:00:01Z"
    },
    {
      checkpoint_id: "checkpoint-2",
      span_id: "span-2",
      sequence: 2,
      label: "unsafe",
      replayable: false,
      created_at: "2026-01-01T00:00:02Z"
    }
  ]);

  assert.deepEqual(
    checkpoints.map((checkpoint) => checkpoint.checkpoint_id),
    ["checkpoint-1", "checkpoint-3"]
  );
});

test("fork input validation enforces allowlist bounds and live-route safety", () => {
  assert.equal(FORK_INPUT_NAMES.includes("resource_budgets"), true);
  assert.deepEqual(
    parseForkInput("resource_budgets", '{"tokens":100}').changedInputNames,
    ["resource_budgets"]
  );
  assert.equal(
    validateForkInputs({
      model_route: { provider: "azure", model: "private-deployment" }
    }).liveProviderRequested,
    true
  );
  assert.throws(() => validateForkInputs({}), /at least one/);
  assert.throws(
    () => validateForkInputs({ unsupported: true }),
    /Unsupported/
  );
  assert.throws(
    () => parseForkInput("task_text", "not-json"),
    /valid JSON/
  );
  assert.throws(
    () =>
      validateForkInputs({
        task_text: "x".repeat(70_000)
      }),
    /64 KiB/
  );
});
