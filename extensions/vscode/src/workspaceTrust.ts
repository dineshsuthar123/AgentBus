export interface WorkspaceTrustHost {
  readonly isTrusted: boolean;
  showWarning(message: string, action: string): Promise<string | undefined>;
  manageTrust(): Promise<void>;
}

const MANAGE_TRUST = "Manage Workspace Trust";

export async function requireWorkspaceTrust(
  host: WorkspaceTrustHost,
  operation: string
): Promise<boolean> {
  if (host.isTrusted) {
    return true;
  }

  const choice = await host.showWarning(
    `AgentBus ${operation} requires a trusted workspace. Read-only diagnostics remain available.`,
    MANAGE_TRUST
  );
  if (choice === MANAGE_TRUST) {
    await host.manageTrust();
  }
  return false;
}
