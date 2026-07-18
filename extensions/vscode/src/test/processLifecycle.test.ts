import assert from "node:assert/strict";
import type { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import test from "node:test";
import { waitForChildExit } from "../processLifecycle";

class ObservableChild extends EventEmitter {
  public exitCode: number | null = null;
  public signalCode: NodeJS.Signals | null = null;

  public exit(): void {
    this.exitCode = 0;
    this.emit("exit", 0, null);
  }
}

test("child exit wait resolves only after the process exit event", async () => {
  const observable = new ObservableChild();
  const child = observable as unknown as ChildProcess;
  let resolved = false;
  const waiting = waitForChildExit(child).then(() => {
    resolved = true;
  });

  await Promise.resolve();
  assert.equal(resolved, false);

  observable.exit();
  await waiting;

  assert.equal(resolved, true);
  assert.equal(observable.listenerCount("exit"), 0);
});

test("child exit wait accepts a process that already exited", async () => {
  const observable = new ObservableChild();
  observable.exitCode = 0;

  await waitForChildExit(observable as unknown as ChildProcess);

  assert.equal(observable.listenerCount("exit"), 0);
});
