import assert from "node:assert/strict";
import test from "node:test";
import type { ReplaySessionResponse } from "../generated/protocol";
import {
  formatReplayDocument,
  REPLAY_GROUPS,
  replayGroup
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
