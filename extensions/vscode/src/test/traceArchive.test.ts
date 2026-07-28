import assert from "node:assert/strict";
import test from "node:test";
import {
  archiveSha256,
  decodeRegressionFixtureArchive,
  decodeTraceArchive,
  encodeTraceArchive,
  MAX_CONTROL_TRACE_ARCHIVE_BYTES,
  validateTraceArchiveFileName
} from "../traceArchive";

test("trace archive transport verifies canonical base64 and SHA-256", () => {
  const bytes = Buffer.from("portable trace archive", "utf8");
  const encoded = encodeTraceArchive(bytes);
  const hash = archiveSha256(bytes);

  assert.deepEqual(Buffer.from(decodeTraceArchive(encoded, hash)), bytes);
  assert.throws(
    () => decodeTraceArchive(encoded, "a".repeat(64)),
    /SHA-256/
  );
  assert.throws(() => decodeTraceArchive(`${encoded}\n`), /canonical/);
  assert.throws(() => decodeTraceArchive("not-base64"), /canonical/);
});

test("trace archive transport rejects empty oversized and wrong file types", () => {
  assert.throws(() => encodeTraceArchive(new Uint8Array()), /empty/);
  assert.throws(
    () =>
      encodeTraceArchive(
        new Uint8Array(MAX_CONTROL_TRACE_ARCHIVE_BYTES + 1)
      ),
    /650 KiB/
  );
  assert.doesNotThrow(() =>
    validateTraceArchiveFileName("portable.agentbus-trace")
  );
  assert.throws(
    () => validateTraceArchiveFileName("portable.zip"),
    /\.agentbus-trace/
  );
});

test("regression fixture transport enforces identity consent and no replay", () => {
  const bytes = Buffer.from("portable regression fixture", "utf8");
  const response = {
    trace_id: "trace-fixture",
    run_id: "run-fixture",
    provenance_root: "a".repeat(64),
    archive_sha256: archiveSha256(bytes),
    archive_base64: encodeTraceArchive(bytes),
    source_content_included: true,
    source_warning: "Sanitized source-like content is included.",
    license_warning: "Original licenses still apply.",
    replay_command: "agentbus replay fixture.agentbus-trace --mode offline",
    assertions_validated: true as const,
    replay_started: false as const
  };

  assert.deepEqual(
    Buffer.from(
      decodeRegressionFixtureArchive(
        response,
        "run-fixture",
        "trace-fixture",
        true
      )
    ),
    bytes
  );
  assert.throws(
    () =>
      decodeRegressionFixtureArchive(
        response,
        "different-run",
        "trace-fixture",
        true
      ),
    /identity/
  );
  assert.throws(
    () =>
      decodeRegressionFixtureArchive(
        response,
        "run-fixture",
        "trace-fixture",
        false
      ),
    /unexpected source/
  );
  assert.throws(
    () =>
      decodeRegressionFixtureArchive(
        { ...response, replay_started: undefined },
        "run-fixture",
        "trace-fixture",
        true
      ),
    /no-replay/
  );
});
