import assert from "node:assert/strict";
import test from "node:test";
import {
  archiveSha256,
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
