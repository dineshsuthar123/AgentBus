import assert from "node:assert/strict";
import test from "node:test";
import { redactText } from "../redaction";

test("redaction removes bearer and query credentials", () => {
  const token = "sensitive-token-value-with-many-characters";
  const output = redactText(
    `Authorization: Bearer ${token} https://example.test/path?key=${token}`
  );

  assert.equal(output.includes(token), false);
  assert.match(output, /\[REDACTED\]/);
});

test("redaction removes absolute personal home paths", () => {
  const output = redactText(
    "windows=C:\\Users\\Alice Smith\\private project\\trace.db; " +
      "posix=/home/alice/private/project/trace.db; " +
      "mac=/Users/alice/private/project/trace.db"
  );

  assert.equal((output.match(/\[PRIVATE_PATH\]/g) ?? []).length, 3);
  assert.doesNotMatch(output, /Alice Smith|\/home\/alice|\/Users\/alice/);
});
