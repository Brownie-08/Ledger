import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createDemo,
  balance,
  currencyTotals,
  parseAmount,
  postTransfer,
  feeFor,
  DemoError,
} from "../lib/demo.ts";

const request = {
  source: 1,
  destination: 4,
  amount: 1000000,
  reference: "Demo payment",
};
test("seed balances rebuild from entries and every posting balances per currency", () => {
  const state = createDemo();
  assert.equal(balance(state, 1), 247487500);
  assert.equal(balance(state, 2), 406910);
  for (const posting of state.postings)
    assert.ok(
      Object.values(currencyTotals(posting.entries)).every(
        (total) => total === 0,
      ),
    );
});
test("transfer charges 0.5%, preserves held funds, and never mutates the input", () => {
  const state = createDemo();
  const before = structuredClone(state);
  const result = postTransfer(state, request, "payment-1");
  assert.deepEqual(state, before);
  assert.equal(balance(result.state, 1), balance(state, 1) - 1005000);
  assert.equal(balance(result.state, 4), balance(state, 4) + request.amount);
  assert.equal(result.state.accounts[0].held, 450000);
  assert.deepEqual(currencyTotals(result.posting.entries), { NGN: 0 });
  assert.equal(feeFor(199), 0);
});
test("replay returns the same transaction without changing balances; changed body conflicts", () => {
  const first = postTransfer(createDemo(), request, "payment-1");
  const replay = postTransfer(first.state, { ...request }, " payment-1 ");
  assert.equal(replay.replayed, true);
  assert.equal(replay.state, first.state);
  assert.equal(replay.posting.id, first.posting.id);
  assert.equal(
    postTransfer(
      first.state,
      {
        reference: request.reference,
        amount: request.amount,
        destination: request.destination,
        source: request.source,
      },
      "payment-1",
    ).replayed,
    true,
  );
  assert.throws(
    () => postTransfer(first.state, { ...request, amount: 100 }, "payment-1"),
    (e: unknown) => e instanceof DemoError && e.code === "idempotency_conflict",
  );
});
test("overdraft including holds or fee fails atomically", () => {
  const state = createDemo();
  const before = structuredClone(state);
  for (const amount of [
    balance(state, 1),
    balance(state, 1) - state.accounts[0].held,
  ]) {
    assert.throws(
      () => postTransfer(state, { ...request, amount }, "overdraft"),
      /Insufficient available/,
    );
  }
  assert.deepEqual(state, before);
});
test("FX balances each currency independently using fixed quotes", () => {
  const result = postTransfer(
    createDemo(),
    { ...request, destination: 2, amount: 15000000 },
    "fx-1",
  );
  assert.deepEqual(currencyTotals(result.posting.entries), { NGN: 0, USD: 0 });
  assert.equal(
    result.posting.entries.find((e) => e.account === 2)?.amount,
    10000,
  );
  assert.equal(result.posting.entries.length, 5);
});
test("amount parser preserves minor units and rejects malformed input", () => {
  assert.equal(parseAmount("123.45"), 12345);
  assert.equal(parseAmount("0.01"), 1);
  for (const text of [
    "0",
    "-1",
    "1.001",
    "1e3",
    "NaN",
    "",
    " 2",
    "10000000000",
  ])
    assert.throws(() => parseAmount(text), DemoError);
});

test("all six FX directions preserve whole-ledger zero sums and available funds", () => {
  for (const source of [1, 2, 3]) {
    for (const destination of [1, 2, 3]) {
      if (source === destination) continue;
      const result = postTransfer(
        createDemo(),
        {
          source,
          destination,
          amount: source === 1 ? 15000000 : 10000,
          reference: "FX matrix",
        },
        `fx-${source}-${destination}`,
      );
      assert.ok(
        Object.values(
          currencyTotals(result.state.postings.flatMap((p) => p.entries)),
        ).every((total) => total === 0),
      );
      for (const account of result.state.accounts.filter((a) => a.owned))
        assert.ok(balance(result.state, account.id) - account.held >= 0);
      assert.equal(result.posting.entries.length, 5);
    }
  }
});
test("invalid wallets, keys, unsafe amounts, and zero FX results create no entries", () => {
  const state = createDemo();
  for (const patch of [
    { source: 4 },
    { destination: 1 },
    { destination: 999 },
    { amount: 1.1 },
    { amount: -1 },
    { amount: Number.MAX_SAFE_INTEGER },
  ]) {
    assert.throws(
      () => postTransfer(state, { ...request, ...patch }, "invalid"),
      DemoError,
    );
  }
  assert.throws(() => postTransfer(state, request, " "), DemoError);
  assert.throws(
    () =>
      postTransfer(state, { ...request, destination: 2, amount: 1 }, "tiny-fx"),
    /rounds to zero/,
  );
});
