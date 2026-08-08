import { createHash } from "node:crypto";
import * as vscode from "vscode";
import { redactText } from "./redaction";

const DOCUMENT_SCHEME = "agentbus-intelligence";
const MAX_DOCUMENTS = 64;
const MAX_DOCUMENT_CHARACTERS = 250_000;

export type IntelligenceDocumentKind =
  | "architecture"
  | "context"
  | "dependencies"
  | "dependents"
  | "impact"
  | "ownership"
  | "search"
  | "symbol"
  | "tests";

export class IntelligenceDocumentProvider
  implements vscode.TextDocumentContentProvider, vscode.Disposable
{
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  private readonly documents = new Map<string, string>();
  public readonly onDidChange = this.changed.event;

  public publish(
    kind: IntelligenceDocumentKind,
    title: string,
    content: string
  ): vscode.Uri {
    const safeTitle = title
      .replace(/[^A-Za-z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 64) || "report";
    const bounded = redactText(content, MAX_DOCUMENT_CHARACTERS);
    const digest = createHash("sha256")
      .update(kind)
      .update("\0")
      .update(bounded)
      .digest("hex")
      .slice(0, 20);
    const uri = vscode.Uri.from({
      scheme: DOCUMENT_SCHEME,
      path: `/${kind}/${safeTitle}-${digest}.md`
    });
    this.documents.delete(uri.toString());
    this.documents.set(uri.toString(), bounded);
    while (this.documents.size > MAX_DOCUMENTS) {
      const oldest = this.documents.keys().next().value as string | undefined;
      if (!oldest) break;
      this.documents.delete(oldest);
    }
    this.changed.fire(uri);
    return uri;
  }

  public provideTextDocumentContent(uri: vscode.Uri): string {
    if (uri.scheme !== DOCUMENT_SCHEME) {
      throw new Error("Unsafe AgentBus intelligence document scheme.");
    }
    const content = this.documents.get(uri.toString());
    if (content === undefined) {
      throw new Error("AgentBus intelligence document is no longer available.");
    }
    return content;
  }

  public dispose(): void {
    this.documents.clear();
    this.changed.dispose();
  }
}

export async function showIntelligenceDocument(
  provider: IntelligenceDocumentProvider,
  kind: IntelligenceDocumentKind,
  title: string,
  content: string
): Promise<void> {
  const document = await vscode.workspace.openTextDocument(
    provider.publish(kind, title, content)
  );
  await vscode.window.showTextDocument(document, {
    preview: true,
    preserveFocus: false
  });
}
