import assert from "node:assert/strict";
import test from "node:test";
import { formatProvenanceReport } from "../provenancePresentation";

test("provenance report is comprehensive bounded and payload-free", () => {
  const rendered = formatProvenanceReport({
    provenance: {
      trace_id: "trace-1",
      run_id: "run-1",
      generated_at: "2026-01-01T00:00:00Z",
      schema_version: 1,
      trace_schema_version: 1,
      agentbus_version: "0.4",
      operating_system: "windows",
      python_version: "3.13",
      node_version: "22",
      vscode_version: "1.96",
      configuration_fingerprint: "a".repeat(64),
      provider_routes: [
        {
          role: "coder",
          provider: "deterministic",
          model_identifier: "fixture"
        }
      ],
      tool_descriptors: [
        {
          name: "filesystem.write",
          version: "1.0.0",
          protocol_version: "1.0",
          descriptor_sha256: "b".repeat(64)
        }
      ],
      policy_version: "v1",
      policy_sha256: "c".repeat(64),
      protocol_hashes: { control: "d".repeat(64) },
      input_object_count: 2,
      output_object_count: 3,
      generated_artifact_count: 1,
      integrity_object_count: 6,
      task_graph_sha256: "e".repeat(64),
      event_count: 10,
      replayability: "exactly_replayable",
      replayability_reasons: ["No unresolved nondeterminism."],
      integrity_algorithm: "sha256-merkle-v1",
      integrity_root: "f".repeat(64)
    },
    trace: {
      trace_id: "trace-1",
      run_id: "run-1",
      root_span_id: "span-root",
      schema_version: 1,
      status: "succeeded",
      created_at: "2026-01-01T00:00:00Z",
      span_count: 5,
      event_count: 10,
      checkpoint_count: 2
    },
    replayability: {
      trace_id: "trace-1",
      run_id: "run-1",
      level: "exactly_replayable",
      replayable_offline: true,
      spans: []
    },
    runReport: {
      run_id: "run-1",
      status: "succeeded",
      report: {
        workspace: "C:\\Users\\Private Person\\secret-project",
        verifier_status: "passed",
        reviewer_status: "approved",
        changed_files: ["calculator.py"],
        tool_runtime: {
          authorization: "private-token"
        }
      }
    }
  });

  assert.match(rendered, new RegExp("f{64}"));
  assert.match(rendered, /filesystem\\\.write/);
  assert.match(rendered, /Verifier \| passed/);
  assert.match(rendered, /\\\[PRIVATE\\_PATH\\\]/);
  assert.doesNotMatch(rendered, /Private Person|private-token|secret-project/);
  assert.doesNotMatch(rendered, /provider payload/);
});
