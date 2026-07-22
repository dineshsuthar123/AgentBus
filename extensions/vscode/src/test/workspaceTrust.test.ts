import assert from "node:assert/strict";
import test from "node:test";
import {
  requireWorkspaceTrust,
  type WorkspaceTrustHost
} from "../workspaceTrust";

function host(options: {
  trusted: boolean;
  choice?: string;
  calls: string[];
}): WorkspaceTrustHost {
  return {
    isTrusted: options.trusted,
    async showWarning(message, action) {
      options.calls.push(`warning:${message}:${action}`);
      return options.choice;
    },
    async manageTrust() {
      options.calls.push("manage");
    }
  };
}

test("trusted workspaces permit execution without prompting", async () => {
  const calls: string[] = [];

  assert.equal(
    await requireWorkspaceTrust(host({ trusted: true, calls }), "approval"),
    true
  );
  assert.deepEqual(calls, []);
});

test("untrusted workspaces block execution after offering trust management", async () => {
  const calls: string[] = [];

  assert.equal(
    await requireWorkspaceTrust(
      host({
        trusted: false,
        choice: "Manage Workspace Trust",
        calls
      }),
      "run resume"
    ),
    false
  );
  assert.equal(calls.length, 2);
  assert.match(calls[0] ?? "", /run resume requires a trusted workspace/);
  assert.equal(calls[1], "manage");
});

test("untrusted workspaces remain blocked when trust management is dismissed", async () => {
  const calls: string[] = [];

  assert.equal(
    await requireWorkspaceTrust(host({ trusted: false, calls }), "approval"),
    false
  );
  assert.equal(calls.length, 1);
  assert.match(calls[0] ?? "", /approval requires a trusted workspace/);
});
