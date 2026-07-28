import assert from "node:assert/strict";
import test from "node:test";
import type { ComparisonResponse } from "../generated/protocol";
import {
  COMPARISON_GROUPS,
  comparisonSpans,
  formatComparisonDocument,
  formatComparisonSide
} from "../comparisonPresentation";

const comparison: ComparisonResponse = {
  comparison_id: "comparison-1",
  left_trace_id: "trace-left",
  right_trace_id: "trace-right",
  created_at: "2026-01-01T00:00:00Z",
  summary: {
    unchanged_spans: 1,
    changed_spans: 3,
    added_spans: 0,
    removed_spans: 0,
    category_counts: {
      expected: 1,
      regression: 1,
      policy_drift: 1,
      ordering_drift: 1
    }
  },
  categories: [
    "expected",
    "regression",
    "policy_drift",
    "ordering_drift"
  ],
  left_status: "succeeded",
  right_status: "failed",
  spans: [
    {
      semantic_key: "unchanged",
      unchanged: true
    },
    {
      semantic_key: "expected",
      unchanged: false,
      categories: ["expected"]
    },
    {
      semantic_key: "regression",
      unchanged: false,
      categories: ["regression", "policy_drift"],
      differences: [
        {
          field: "status",
          left_sha256: "a".repeat(64),
          right_sha256: "b".repeat(64),
          category: "regression",
          summary: "token=private-value"
        }
      ]
    },
    {
      semantic_key: "ordering",
      unchanged: false,
      categories: ["ordering_drift"]
    }
  ]
};

test("comparison view exposes required drift groups without hiding other drift", () => {
  assert.deepEqual(
    COMPARISON_GROUPS.map((group) => group.key),
    [
      "unchanged",
      "expected",
      "regression",
      "policy_drift",
      "model_drift",
      "tool_drift",
      "environment_drift",
      "other"
    ]
  );
  assert.deepEqual(
    comparisonSpans(comparison, "policy_drift").map(
      (span) => span.semantic_key
    ),
    ["regression"]
  );
  assert.deepEqual(
    comparisonSpans(comparison, "other").map((span) => span.semantic_key),
    ["ordering"]
  );
});

test("comparison document renders hashes and never compared values", () => {
  const rendered = formatComparisonDocument(comparison);

  assert.match(rendered, new RegExp("a{64}"));
  assert.match(rendered, new RegExp("b{64}"));
  assert.match(rendered, /Policy Drift/);
  assert.doesNotMatch(rendered, /private-value/);
});

test("native comparison sides contain hashes and no compared payloads", () => {
  const left = formatComparisonSide(comparison, "left");
  const right = formatComparisonSide(comparison, "right");

  assert.equal(JSON.parse(left).trace_id, "trace-left");
  assert.equal(JSON.parse(right).trace_id, "trace-right");
  assert.match(left, new RegExp("a{64}"));
  assert.match(right, new RegExp("b{64}"));
  assert.doesNotMatch(left, /private-value/);
  assert.doesNotMatch(right, /private-value/);
});
