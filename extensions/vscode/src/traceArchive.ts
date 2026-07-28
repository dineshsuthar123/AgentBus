import { createHash } from "node:crypto";

export const MAX_CONTROL_TRACE_ARCHIVE_BYTES = 650_000;
export const TRACE_ARCHIVE_EXTENSION = ".agentbus-trace";

export function decodeTraceArchive(
  archiveBase64: string,
  expectedSha256?: string
): Uint8Array {
  if (
    !archiveBase64 ||
    archiveBase64.length > 900_000 ||
    archiveBase64.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]*={0,2}$/.test(archiveBase64)
  ) {
    throw new Error("Trace archive payload is not canonical base64.");
  }
  const bytes = Buffer.from(archiveBase64, "base64");
  if (
    bytes.length === 0 ||
    bytes.length > MAX_CONTROL_TRACE_ARCHIVE_BYTES ||
    bytes.toString("base64") !== archiveBase64
  ) {
    throw new Error("Trace archive payload is empty, oversized, or malformed.");
  }
  if (
    expectedSha256 &&
    archiveSha256(bytes) !== expectedSha256.toLowerCase()
  ) {
    throw new Error("Trace archive SHA-256 does not match the control response.");
  }
  return bytes;
}

export function encodeTraceArchive(bytes: Uint8Array): string {
  if (
    bytes.byteLength === 0 ||
    bytes.byteLength > MAX_CONTROL_TRACE_ARCHIVE_BYTES
  ) {
    throw new Error("Trace archive is empty or exceeds the 650 KiB limit.");
  }
  return Buffer.from(bytes).toString("base64");
}

export function archiveSha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

export function validateTraceArchiveFileName(path: string): void {
  if (!path.toLowerCase().endsWith(TRACE_ARCHIVE_EXTENSION)) {
    throw new Error(
      `Trace archive files must use the ${TRACE_ARCHIVE_EXTENSION} extension.`
    );
  }
}
