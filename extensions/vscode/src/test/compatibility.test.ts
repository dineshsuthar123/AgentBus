import assert from "node:assert/strict";
import test from "node:test";
import {
  assessDaemonCompatibility,
  assessStateSchema,
  EXPECTED_STATE_SCHEMA
} from "../compatibility";

test("daemon compatibility accepts the 0.6 beta line", () => {
  for (const version of ["0.6.0b1", "0.6.2", "0.6.9-rc.1"]) {
    assert.equal(assessDaemonCompatibility(version, "1.0").compatible, true);
  }
});

test("daemon compatibility distinguishes protocol old and new failures", () => {
  assert.equal(
    assessDaemonCompatibility("0.6.0b1", "0.9").code,
    "protocol_mismatch"
  );
  assert.equal(
    assessDaemonCompatibility("0.5.9", "1.0").code,
    "daemon_too_old"
  );
  assert.equal(
    assessDaemonCompatibility("0.7.0", "1.0").code,
    "daemon_too_new"
  );
});

test("state schema compatibility gives actionable migration direction", () => {
  assert.equal(assessStateSchema(EXPECTED_STATE_SCHEMA).compatible, true);
  assert.equal(assessStateSchema(EXPECTED_STATE_SCHEMA - 1).code, "schema_too_old");
  assert.equal(assessStateSchema(EXPECTED_STATE_SCHEMA + 1).code, "schema_too_new");
});
