import {
  balance,
  createDemo,
  currencyTotals,
  DemoError,
  postTransfer,
} from "./demo.ts";
export type Scenario = "replay" | "conflict" | "overdraft" | "fx";
export type ScenarioResult = {
  title: string;
  steps: string[];
  before: number;
  after: number;
  postingsBefore: number;
  postingsAfter: number;
  code: string;
  totals: Partial<Record<string, number>>;
};
export function runScenario(id: Scenario): ScenarioResult {
  const seed = createDemo();
  const request = {
    source: 1,
    destination: 4,
    amount: 1000000,
    reference: "Failure lab",
  };
  let state = seed;
  let steps: string[] = [];
  let code = "posted";
  let title = "";
  if (id === "replay" || id === "conflict") {
    state = postTransfer(seed, request, "lab-key").state;
    steps = [
      "First request posts once: sender debit, fee credit, recipient credit.",
    ];
    if (id === "replay") {
      state = postTransfer(state, request, "lab-key").state;
      title = "The retry did not move money twice.";
      code = "Idempotent-Replayed: true";
      steps.push(
        "The same key and payload return the original transaction.",
        "Posting count increases by one across two requests.",
      );
    } else {
      try {
        postTransfer(state, { ...request, amount: 2000000 }, "lab-key");
      } catch (error) {
        code = (error as DemoError).code;
      }
      title = "The changed request was rejected.";
      steps.push(
        "A different amount reuses the first request’s key.",
        "The conflict leaves the first posting intact and creates no second posting.",
      );
    }
  } else if (id === "overdraft") {
    try {
      postTransfer(
        seed,
        { ...request, amount: balance(seed, 1) },
        "lab-overdraft",
      );
    } catch (error) {
      code = (error as DemoError).code;
    }
    title = "Available funds protected the wallet.";
    steps = [
      "The request attempts to spend the entire posted balance.",
      "Held funds and the fee make the request unaffordable.",
      "The operation is rejected before any entries or balances change.",
    ];
  } else {
    const result = postTransfer(
      seed,
      { ...request, destination: 2, amount: 15000000 },
      "lab-fx",
    );
    state = result.state;
    title = "Two currencies. Two independent proofs.";
    steps = [
      "The NGN wallet is debited by NGN 150,750, including a NGN 750 fee.",
      "NGN and USD FX position accounts bridge the conversion.",
      "The recipient gets USD 100. Both currencies independently sum to zero.",
    ];
  }
  return {
    title,
    steps,
    before: balance(seed, 1),
    after: balance(state, 1),
    postingsBefore: seed.postings.length,
    postingsAfter: state.postings.length,
    code,
    totals: currencyTotals(state.postings.flatMap((p) => p.entries)),
  };
}
