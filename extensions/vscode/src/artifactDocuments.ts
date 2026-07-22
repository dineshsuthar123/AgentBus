import * as vscode from "vscode";
import type { AgentBusClient } from "./apiClient";
import {
  validateToolArtifact,
  verifyToolArtifactContent
} from "./artifactPresentation";
import { isSafeControlId } from "./toolPresentation";

type ClientProvider = () => Promise<AgentBusClient>;

interface ArtifactDocumentIdentity {
  runId: string;
  invocationId: string;
  artifactId: string;
}

export function toolArtifactUri(
  runId: string,
  invocationId: string,
  artifactId: string
): vscode.Uri {
  if (
    !isSafeControlId(runId) ||
    !isSafeControlId(invocationId) ||
    !isSafeControlId(artifactId)
  ) {
    throw new Error("Unsafe AgentBus tool artifact identity.");
  }
  return vscode.Uri.from({
    scheme: "agentbus-artifact",
    path: `/${encodeURIComponent(runId)}`,
    query: new URLSearchParams({
      invocation: invocationId,
      artifact: artifactId
    }).toString()
  });
}

export class ToolArtifactDocumentProvider
  implements vscode.TextDocumentContentProvider
{
  public constructor(private readonly client: ClientProvider) {}

  public async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const identity = parseArtifactUri(uri);
    const client = await this.client();
    const invocation = await client.toolInvocation(
      identity.runId,
      identity.invocationId
    );
    const artifact = invocation.result?.artifacts?.find(
      (candidate) => candidate.artifact_id === identity.artifactId
    );
    if (!artifact) {
      throw new Error("Tool artifact is no longer available.");
    }
    const openable = validateToolArtifact(artifact);
    const response = await client.file(identity.runId, openable.path, "after");
    return verifyToolArtifactContent(openable, response);
  }
}

function parseArtifactUri(uri: vscode.Uri): ArtifactDocumentIdentity {
  const runId = decodeURIComponent(uri.path.replace(/^\//, ""));
  const query = new URLSearchParams(uri.query);
  const invocationId = query.get("invocation") ?? "";
  const artifactId = query.get("artifact") ?? "";
  if (
    uri.scheme !== "agentbus-artifact" ||
    !isSafeControlId(runId) ||
    !isSafeControlId(invocationId) ||
    !isSafeControlId(artifactId)
  ) {
    throw new Error("Unsafe AgentBus tool artifact document identity.");
  }
  return { runId, invocationId, artifactId };
}
