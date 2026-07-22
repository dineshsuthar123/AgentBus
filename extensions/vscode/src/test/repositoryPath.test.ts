import assert from "node:assert/strict";
import test from "node:test";
import {
  isPublicRepositoryPath,
  isSafeRepositoryPath
} from "../repositoryPath";

test("repository paths reject traversal streams devices and aliases", () => {
  assert.equal(isSafeRepositoryPath("src/module.ts"), true);
  for (const path of [
    "../outside.txt",
    "nested/./file.txt",
    "nested/file.txt:stream",
    "nested/file.txt.",
    "nested/AUX.txt",
    "C:/outside.txt",
    "\\\\server\\share"
  ]) {
    assert.equal(isSafeRepositoryPath(path), false, path);
  }
});

test("public paths reject control and credential files", () => {
  assert.equal(isPublicRepositoryPath("src/module.ts"), true);
  for (const path of [
    ".env",
    ".aws/credentials",
    ".azure/accessTokens.json",
    ".agentbus/state.db",
    ".git/config",
    "nested/private.pem",
    "nested/application_default_credentials.json"
  ]) {
    assert.equal(isPublicRepositoryPath(path), false, path);
  }
});
