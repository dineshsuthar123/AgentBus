import * as vscode from "vscode";
import type { DaemonManager } from "./daemonManager";
import {
  ONBOARDING_VERSION,
  assessInstallationOutput,
  detectIndexState,
  formatOnboardingSummary,
  safeConfigurationDocument,
  shouldShowOnboarding,
  type InstallationAssessment,
  type OnboardingState
} from "./onboardingPresentation";
import {
  buildProductCommandSpec,
  runProductCommand,
  type ProductCommandResult
} from "./productCommand";
import { redactText, safeError } from "./redaction";
import { ensureWorkspaceTrust } from "./workspace";

const ONBOARDING_STATE_KEY = "agentbus.onboarding.lastShownVersion";
const DOCUMENTATION_URL =
  "https://github.com/dineshsuthar123/AgentBus/blob/main/docs/getting-started.md";

export class OnboardingController implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];

  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly daemon: DaemonManager,
    private readonly output: vscode.OutputChannel
  ) {}

  public register(): void {
    this.command("agentbus.getStarted", () => this.getStarted());
    this.command("agentbus.runSetup", () => this.runSetup());
    this.command("agentbus.runQuickstart", () => this.runQuickstart());
    this.command("agentbus.checkInstallation", () => this.checkInstallation());
    this.command("agentbus.openDocumentation", () => this.openDocumentation());
    this.command("agentbus.openResolvedConfiguration", () =>
      this.openResolvedConfiguration()
    );
    void this.showFirstRun().catch((error: unknown) => {
      this.output.appendLine(`Onboarding detection failed: ${safeError(error)}`);
    });
  }

  public dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
  }

  private command(name: string, callback: () => Promise<void>): void {
    this.disposables.push(
      vscode.commands.registerCommand(name, async () => {
        try {
          await callback();
        } catch (error) {
          const detail = safeError(error);
          this.output.appendLine(`Onboarding command failed: ${detail}`);
          void vscode.window.showErrorMessage(`AgentBus: ${detail}`);
        }
      })
    );
  }

  private async showFirstRun(): Promise<void> {
    const enabled = vscode.workspace
      .getConfiguration("agentbus")
      .get<boolean>("showWelcomeOnStartup", true);
    const lastShown = this.context.globalState.get<string>(ONBOARDING_STATE_KEY);
    if (!shouldShowOnboarding(enabled, lastShown)) return;
    await this.context.globalState.update(ONBOARDING_STATE_KEY, ONBOARDING_VERSION);
    const state = await this.detectState();
    const summary = formatOnboardingSummary(state);
    this.output.appendLine(summary);
    const action = await vscode.window.showInformationMessage(
      summary,
      "Get Started",
      "Check Installation"
    );
    if (action === "Get Started") await this.getStarted();
    if (action === "Check Installation") await this.checkInstallation();
  }

  private async detectState(): Promise<OnboardingState> {
    const trusted = vscode.workspace.isTrusted;
    const workspace = this.workspacePath();
    if (!trusted) {
      return {
        installation: {
          state: "not_checked",
          message: "Installation checks wait until the workspace is trusted."
        },
        daemon: "not_checked",
        trusted,
        index: workspace ? "unknown" : "no_workspace"
      };
    }
    const installation = await this.detectInstallation();
    let daemon: OnboardingState["daemon"] = "not_detected";
    try {
      daemon = await this.daemon.discover() ? "compatible" : "not_detected";
    } catch (error) {
      this.output.appendLine(`Daemon compatibility check failed: ${safeError(error)}`);
    }
    const index = installation.state === "compatible" && workspace
      ? await this.detectIndex(workspace)
      : workspace ? "unknown" : "no_workspace";
    return {
      installation,
      daemon,
      trusted,
      index
    };
  }

  private async detectInstallation(): Promise<InstallationAssessment> {
    try {
      const result = await this.run(["version", "--json"]);
      if (result.exitCode !== 0) {
        return {
          state: "missing",
          message: "AgentBus is not available from the configured executable."
        };
      }
      return assessInstallationOutput(result.stdout);
    } catch {
      return {
        state: "missing",
        message: "AgentBus is not available from the configured executable."
      };
    }
  }

  private async detectIndex(workspace: string): Promise<OnboardingState["index"]> {
    try {
      const result = await this.run(this.doctorArgs(workspace));
      return detectIndexState(result.stdout);
    } catch {
      return "unknown";
    }
  }

  private async getStarted(): Promise<void> {
    await vscode.commands.executeCommand(
      "workbench.action.openWalkthrough",
      "agentbus.agentbus-vscode#agentbus.gettingStarted",
      false
    );
  }

  private async runSetup(): Promise<void> {
    if (!(await ensureWorkspaceTrust("first-run setup"))) return;
    const confirmed = await vscode.window.showWarningMessage(
      "Run local AgentBus setup with the deterministic, network-free provider?",
      {
        modal: true,
        detail:
          "Setup creates or updates AgentBus user configuration and runtime directories. " +
          "It preserves existing configuration and does not contact Azure or Ollama."
      },
      "Run Setup"
    );
    if (confirmed !== "Run Setup") return;
    const workspace = this.workspacePath();
    const args = [
      "setup",
      "--non-interactive",
      "--provider",
      "deterministic",
      "--scope",
      "user",
      ...(workspace ? ["--workspace", workspace] : []),
      "--json"
    ];
    const result = await this.progress("Running offline AgentBus setup", args);
    this.requireSuccess(result, "setup");
    this.reportResult("Setup", result);
    void vscode.window.showInformationMessage("AgentBus setup completed offline.");
  }

  private async runQuickstart(): Promise<void> {
    if (!(await ensureWorkspaceTrust("offline quickstart"))) return;
    const result = await this.progress(
      "Running deterministic AgentBus quickstart",
      ["quickstart", "--json"]
    );
    this.requireSuccess(result, "quickstart");
    this.reportResult("Quickstart", result);
    void vscode.window.showInformationMessage(
      "AgentBus quickstart completed without a live provider."
    );
  }

  private async checkInstallation(): Promise<void> {
    if (!(await ensureWorkspaceTrust("installation diagnostics"))) return;
    const installationResult = await this.progress(
      "Checking AgentBus installation",
      ["version", "--json"]
    );
    this.requireSuccess(installationResult, "version check");
    const installation = assessInstallationOutput(installationResult.stdout);
    const workspace = this.workspacePath();
    const doctor = await this.progress(
      "Running offline AgentBus diagnostics",
      this.doctorArgs(workspace)
    );
    this.output.appendLine(`Installation: ${installation.message}`);
    this.reportResult("Doctor", doctor);
    this.output.show();
    const message = installation.state === "compatible"
      ? "AgentBus installation is compatible. Review Doctor for any local warnings."
      : installation.message;
    if (installation.state === "compatible") {
      void vscode.window.showInformationMessage(message);
    } else {
      void vscode.window.showWarningMessage(message);
    }
  }

  private async openResolvedConfiguration(): Promise<void> {
    if (!(await ensureWorkspaceTrust("configuration inspection"))) return;
    const settings = this.daemon.launchSettings();
    const workspace = this.workspacePath();
    const args = [
      "config",
      "show",
      ...(settings.configPath ? ["--config", settings.configPath] : []),
      ...(workspace ? ["--workspace", workspace] : []),
      "--json"
    ];
    const result = await this.progress("Resolving safe AgentBus configuration", args);
    this.requireSuccess(result, "configuration inspection");
    const document = await vscode.workspace.openTextDocument({
      content: safeConfigurationDocument(result.stdout),
      language: "json"
    });
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async openDocumentation(): Promise<void> {
    const opened = await vscode.env.openExternal(vscode.Uri.parse(DOCUMENTATION_URL));
    if (!opened) throw new Error("VS Code could not open the AgentBus documentation.");
  }

  private doctorArgs(workspace?: string): string[] {
    const settings = this.daemon.launchSettings();
    return [
      "doctor",
      ...(settings.configPath ? ["--config", settings.configPath] : []),
      ...(workspace ? ["--workspace", workspace] : []),
      "--json"
    ];
  }

  private async progress(title: string, args: string[]): Promise<ProductCommandResult> {
    return vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title },
      () => this.run(args)
    );
  }

  private run(args: string[]): Promise<ProductCommandResult> {
    const spec = buildProductCommandSpec(this.daemon.launchSettings(), args);
    return runProductCommand(spec, { cwd: this.workspacePath() });
  }

  private requireSuccess(result: ProductCommandResult, operation: string): void {
    if (result.exitCode === 0) return;
    const detail = redactText(result.stderr || result.stdout, 4_000).trim();
    throw new Error(
      `AgentBus ${operation} failed${detail ? `: ${detail}` : "."}`
    );
  }

  private reportResult(label: string, result: ProductCommandResult): void {
    const output = redactText(
      [result.stdout, result.stderr].filter(Boolean).join("\n"),
      64_000
    ).trim();
    this.output.appendLine(`${label} (exit ${result.exitCode}):`);
    this.output.appendLine(output || "No diagnostic output.");
  }

  private workspacePath(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  }
}
