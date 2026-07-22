import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import type { ToolArtifact } from "../generated/protocol";
import {
  MAX_OPEN_ARTIFACT_BYTES,
  ToolArtifactValidationError,
  validateToolArtifact,
  verifyToolArtifactContent
} from "../artifactPresentation";

function artifact(overrides: Partial<ToolArtifact> = {}): ToolArtifact {
  return {
    artifact_id: "artifact-1",
    kind: "file",
    relative_path: "src/module.py",
    media_type: "text/plain; charset=utf-8",
    size_bytes: 12,
    sha256: "a".repeat(64),
    safe_metadata: { encoding: "utf-8" },
    ...overrides
  };
}

test("text artifact validation requires public UTF-8 repository files", () => {
  const value = validateToolArtifact(artifact());

  assert.equal(value.path, "src/module.py");
  assert.equal(value.encoding, "utf-8");
});

test("artifact validation rejects unsafe secret binary and oversized files", () => {
  const rejected = [
    artifact({ relative_path: "../outside.txt" }),
    artifact({ relative_path: ".env" }),
    artifact({ relative_path: "nested/file.txt:stream" }),
    artifact({ media_type: "application/octet-stream" }),
    artifact({ media_type: "text/plain", safe_metadata: {} }),
    artifact({ truncated: true }),
    artifact({ size_bytes: MAX_OPEN_ARTIFACT_BYTES + 1 }),
    artifact({ kind: "process_output" })
  ];

  for (const candidate of rejected) {
    assert.throws(
      () => validateToolArtifact(candidate),
      ToolArtifactValidationError
    );
  }
});

test("artifact content verification rejects stale or substituted files", () => {
  const content = "value = 1\n";
  const expected = artifact({
    size_bytes: Buffer.byteLength(content, "utf8"),
    sha256: createHash("sha256").update(content).digest("hex")
  });
  const openable = validateToolArtifact(expected);

  assert.equal(
    verifyToolArtifactContent(openable, {
      run_id: "run-1",
      path: "src/module.py",
      content,
      revision: "after"
    }),
    content
  );
  assert.throws(
    () =>
      verifyToolArtifactContent(openable, {
        run_id: "run-1",
        path: "src/module.py",
        content: "substituted\n",
        revision: "after"
      }),
    ToolArtifactValidationError
  );
});
