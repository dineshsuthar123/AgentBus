import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import type {
  FileContentResponse,
  ToolArtifact
} from "./generated/protocol";
import { isPublicRepositoryPath } from "./repositoryPath";

export const MAX_OPEN_ARTIFACT_BYTES = 1_000_000;

export interface OpenableToolArtifact {
  artifact: ToolArtifact;
  path: string;
  mediaType: string;
  encoding: "utf-8";
}

export class ToolArtifactValidationError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "ToolArtifactValidationError";
  }
}

export function validateToolArtifact(
  artifact: ToolArtifact
): OpenableToolArtifact {
  if (artifact.kind !== "file") {
    throw new ToolArtifactValidationError(
      "Only repository file artifacts can be opened."
    );
  }
  if (
    !artifact.relative_path ||
    !isPublicRepositoryPath(artifact.relative_path)
  ) {
    throw new ToolArtifactValidationError(
      "Artifact path is unsafe or secret-classified."
    );
  }
  if (artifact.truncated) {
    throw new ToolArtifactValidationError(
      "Truncated artifacts cannot be opened as repository files."
    );
  }
  if (artifact.size_bytes > MAX_OPEN_ARTIFACT_BYTES) {
    throw new ToolArtifactValidationError(
      "Artifact exceeds the 1000000-byte control-plane limit."
    );
  }

  const mediaType = (artifact.media_type ?? "").trim().toLowerCase();
  if (!isTextMediaType(mediaType)) {
    throw new ToolArtifactValidationError(
      "Artifact content type is not approved for text display."
    );
  }
  const encoding = declaredEncoding(artifact, mediaType);
  if (encoding !== "utf-8" && encoding !== "utf8") {
    throw new ToolArtifactValidationError(
      "Artifact must explicitly declare UTF-8 encoding."
    );
  }
  return {
    artifact,
    path: artifact.relative_path,
    mediaType,
    encoding: "utf-8"
  };
}

export function verifyToolArtifactContent(
  openable: OpenableToolArtifact,
  response: FileContentResponse
): string {
  const bytes = Buffer.byteLength(response.content, "utf8");
  const digest = createHash("sha256")
    .update(response.content, "utf8")
    .digest("hex");
  if (
    response.revision !== "after" ||
    response.truncated ||
    response.path !== openable.path ||
    bytes > MAX_OPEN_ARTIFACT_BYTES ||
    bytes !== openable.artifact.size_bytes ||
    digest !== openable.artifact.sha256
  ) {
    throw new ToolArtifactValidationError(
      "Repository content no longer matches the persisted tool artifact."
    );
  }
  return response.content;
}

function isTextMediaType(mediaType: string): boolean {
  const base = mediaType.split(";", 1)[0]?.trim() ?? "";
  return (
    base.startsWith("text/") ||
    base.endsWith("+json") ||
    base.endsWith("+xml") ||
    [
      "application/json",
      "application/javascript",
      "application/toml",
      "application/typescript",
      "application/xml",
      "application/x-yaml",
      "application/yaml"
    ].includes(base)
  );
}

function declaredEncoding(artifact: ToolArtifact, mediaType: string): string {
  const charset = mediaType
    .split(";")
    .slice(1)
    .map((part) => part.trim())
    .find((part) => part.startsWith("charset="))
    ?.slice("charset=".length)
    .replace(/^['"]|['"]$/g, "");
  const metadataEncoding = artifact.safe_metadata?.encoding;
  return String(charset ?? metadataEncoding ?? "").toLowerCase();
}
