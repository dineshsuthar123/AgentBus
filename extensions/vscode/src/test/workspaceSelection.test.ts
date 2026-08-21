import assert from "node:assert/strict";
import test from "node:test";
import {
  chooseWorkspace,
  type WorkspaceChoice
} from "../workspaceSelection";

interface Folder {
  readonly name: string;
  readonly path: string;
}

function choices(): WorkspaceChoice<Folder>[] {
  return [
    {
      label: "repository",
      description: "/workspaces/first",
      folder: { name: "repository", path: "/workspaces/first" }
    },
    {
      label: "repository",
      description: "/workspaces/second",
      folder: { name: "repository", path: "/workspaces/second" }
    }
  ];
}

test("multi-root selection returns only the explicitly picked root", async () => {
  const available = choices();

  const selected = await chooseWorkspace(available, async (presented) => {
    assert.equal(presented, available);
    return presented[1];
  });

  assert.equal(selected, available[1]?.folder);
});

test("multi-root cancellation never falls back to the first root", async () => {
  const available = choices();

  const selected = await chooseWorkspace(available, async () => undefined);

  assert.equal(selected, undefined);
});

test("multi-root selection rejects a choice outside the presented set", async () => {
  const available = choices();
  const forged = structuredClone(available[1]);

  const selected = await chooseWorkspace(available, async () => forged);

  assert.equal(selected, undefined);
});
