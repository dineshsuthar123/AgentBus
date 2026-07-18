import type { ChildProcess } from "node:child_process";

const childExitTimeoutMilliseconds = 15_000;

export function waitForChildExit(
  child: ChildProcess,
  timeoutMilliseconds = childExitTimeoutMilliseconds
): Promise<void> {
  if (hasExited(child)) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const onExit = (): void => {
      cleanup();
      resolve();
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("Timed out waiting for AgentBus daemon process to exit."));
    }, timeoutMilliseconds);
    const cleanup = (): void => {
      clearTimeout(timer);
      child.off("exit", onExit);
    };

    child.once("exit", onExit);
    if (hasExited(child)) {
      onExit();
    }
  });
}

function hasExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}
