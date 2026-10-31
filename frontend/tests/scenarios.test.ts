import { test } from "node:test";
import assert from "node:assert/strict";
import { runScenario } from "../lib/scenarios.ts";
test("duplicate and conflicting requests each produce a single posting", () => {
  for (const id of ["replay", "conflict"] as const) {
    const result = runScenario(id);
    assert.equal(result.postingsAfter, result.postingsBefore + 1);
    assert.equal(result.before - result.after, 1005000);
  }
});
test("overdraft is atomic and all scenario ledgers balance", () => {
  const rejected = runScenario("overdraft");
  assert.equal(rejected.before, rejected.after);
  assert.equal(rejected.postingsBefore, rejected.postingsAfter);
  for (const id of ["replay", "conflict", "overdraft", "fx"] as const)
    assert.ok(
      Object.values(runScenario(id).totals).every((total) => total === 0),
    );
});
