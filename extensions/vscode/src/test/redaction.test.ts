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
