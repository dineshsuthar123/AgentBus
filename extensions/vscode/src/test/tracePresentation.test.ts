import assert from "node:assert/strict";
import test from "node:test";
import type {
  TraceSpanDetailResponse,
  TraceSpanSummary
} from "../generated/protocol";
import {
  formatSpanDocument,
  spanDescription,
  timelineChildren
} from "../tracePresentation";

function summary(
  spanId: string,
  sequence: number,
  parentSpanId?: string
): TraceSpanSummary {
  return {
    trace_id: "trace-1",
    span_id: spanId,
    parent_span_id: parentSpanId,
    run_id: "run-1",
    span_type: spanId.startsWith("task") ? "task" : "tool_invocation",
    name: spanId,
    sequence,
    started_at: "2026-01-01T00:00:00Z",
    status: "succeeded"
  };
}

test("timeline preserves hierarchy and deterministic sequence ordering", () => {
  const spans = [
    summary("tool-late", 4, "task-1"),
    summary("orphan", 3, "missing"),
    summary("root", 1),
    summary("task-1", 2, "root"),
    summary("tool-early", 3, "task-1")
  ];

  assert.deepEqual(
    timelineChildren(spans).map((span) => span.span_id),
    ["root", "orphan"]
  );
  assert.deepEqual(
    timelineChildren(spans, "task-1").map((span) => span.span_id),
    ["tool-early", "tool-late"]
  );
  assert.equal(
    spanDescription(spans[3] as TraceSpanSummary),
    "#2 task | succeeded"
  );
  const selfParent = summary("self", 5, "self");
  assert.deepEqual(timelineChildren([selfParent], "self"), []);
});

test("span document renders hashes but withholds arbitrary sensitive values", () => {
  const detail: TraceSpanDetailResponse = {
    ...summary("provider", 2, "root"),
    span_type: "provider_response",
    inputs: [
      {
        reference_id: "input-1",
        name: "model.request",
        sha256: "a".repeat(64),
        media_type: "application/json",
        byte_length: 123,
        redacted: true,
        required_for_replay: true
      }
    ],
    attributes: {
      provider: "deterministic",
      hidden_prompt: "do not render this",
      api_token: "private-value"
    },
    resource_usage: { input_tokens: 12 }
  };

  const rendered = formatSpanDocument(detail);

  assert.match(rendered, new RegExp("a{64}"));
  assert.match(rendered, /provider/);
  assert.match(rendered, /Sensitive attribute values withheld: 2/);
  assert.doesNotMatch(rendered, /do not render this/);
  assert.doesNotMatch(rendered, /private-value/);
});
