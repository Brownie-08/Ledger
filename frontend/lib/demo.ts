// Educational, in-memory adapter. It never calls or writes to the Django API.
export type Currency = "NGN" | "USD" | "GBP";
export type Account = {
  id: number;
  name: string;
  currency: Currency;
  kind: "wallet" | "cash" | "fees" | "fx";
  owned: boolean;
  held: number;
};
export type Entry = { account: number; amount: number; currency: Currency };
export type Transfer = {
  source: number;
  destination: number;
  amount: number;
  reference: string;
};
export type Posting = {
  id: string;
  type: "deposit" | "transfer" | "fx";
  reference: string;
  created: string;
  key: string;
  request?: Transfer;
  entries: Entry[];
};
export type DemoState = { accounts: Account[]; postings: Posting[] };
export class DemoError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
export const currencies: Currency[] = ["NGN", "USD", "GBP"];
export function money(minor: number, currency: Currency): string {
  return new Intl.NumberFormat("en-NG", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(minor / 100);
}
export function parseAmount(text: string): number {
  if (!/^\d{1,10}(\.\d{1,2})?$/.test(text))
    throw new DemoError(
      "invalid_amount",
      "Enter a positive amount with at most two decimal places.",
    );
  const [whole, fraction = ""] = text.split(".");
  const minor = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(minor) || minor <= 0)
    throw new DemoError("invalid_amount", "Amount must be greater than zero.");
  return minor;
}
export const feeFor = (amount: number) =>
  Number((BigInt(amount) * 50n) / 10000n);
// Fixed demo quotes, matching the backend's two-currency FX-account flow.
const ngnValue: Record<Currency, bigint> = { NGN: 1n, USD: 1500n, GBP: 1900n };
export const convert = (amount: number, from: Currency, to: Currency) =>
  Number((BigInt(amount) * ngnValue[from]) / ngnValue[to]);
export function balance(state: DemoState, id: number): number {
  return state.postings.reduce(
    (sum, p) =>
      sum +
      p.entries
        .filter((e) => e.account === id)
        .reduce((n, e) => n + e.amount, 0),
    0,
  );
}
export function currencyTotals(
  entries: Entry[],
): Partial<Record<Currency, number>> {
  return entries.reduce<Partial<Record<Currency, number>>>(
    (totals, e) => ({
      ...totals,
      [e.currency]: (totals[e.currency] ?? 0) + e.amount,
    }),
    {},
  );
}
export function createDemo(): DemoState {
  const accounts: Account[] = [
    {
      id: 1,
      name: "Alex Morgan",
      currency: "NGN",
      kind: "wallet",
      owned: true,
      held: 450000,
    },
    {
      id: 2,
      name: "Alex Morgan",
      currency: "USD",
      kind: "wallet",
      owned: true,
      held: 0,
    },
    {
      id: 3,
      name: "Alex Morgan",
      currency: "GBP",
      kind: "wallet",
      owned: true,
      held: 0,
    },
    {
      id: 4,
      name: "Ada Okafor",
      currency: "NGN",
      kind: "wallet",
      owned: false,
      held: 0,
    },
    {
      id: 5,
      name: "Northstar Studio",
      currency: "USD",
      kind: "wallet",
      owned: false,
      held: 0,
    },
    {
      id: 6,
      name: "Sam Taylor",
      currency: "GBP",
      kind: "wallet",
      owned: false,
      held: 0,
    },
    ...currencies.flatMap((currency, i) => [
      {
        id: 10 + i,
        name: "Provider cash",
        currency,
        kind: "cash" as const,
        owned: false,
        held: 0,
      },
      {
        id: 20 + i,
        name: "Transfer fees",
        currency,
        kind: "fees" as const,
        owned: false,
        held: 0,
      },
      {
        id: 30 + i,
        name: "FX position",
        currency,
        kind: "fx" as const,
        owned: false,
        held: 0,
      },
    ]),
  ];
  const deposits = [250000000, 425000, 182000].map((amount, i): Posting => ({
    id: `TX-${1041 + i}`,
    type: "deposit",
    reference: [
      "Opening NGN balance",
      "Client payment · Northstar",
      "Opening GBP balance",
    ][i],
    created: `2026-10-28T09:${10 + i}:00Z`,
    key: `seed-deposit-${i}`,
    entries: [
      { account: 10 + i, amount: -amount, currency: currencies[i] },
      { account: 1 + i, amount, currency: currencies[i] },
    ],
  }));
  let state: DemoState = { accounts, postings: deposits };
  state = postTransfer(
    state,
    {
      source: 1,
      destination: 4,
      amount: 2500000,
      reference: "Design collaboration",
    },
    "demo-design-01",
    "2026-10-28T11:30:00Z",
  ).state;
  state = postTransfer(
    state,
    {
      source: 2,
      destination: 5,
      amount: 18000,
      reference: "Software subscription",
    },
    "demo-software-01",
    "2026-10-28T13:15:00Z",
  ).state;
  return state;
}
export function postTransfer(
  state: DemoState,
  request: Transfer,
  rawKey: string,
  created = new Date().toISOString(),
): { state: DemoState; posting: Posting; replayed: boolean } {
  const key = rawKey.trim();
  if (!key || key.length > 100)
    throw new DemoError(
      "invalid_key",
      "Use an idempotency key between 1 and 100 characters.",
    );
  const previous = state.postings.find((p) => p.key === key);
  if (previous) {
    const original = previous.request;
    if (
      !original ||
      original.source !== request.source ||
      original.destination !== request.destination ||
      original.amount !== request.amount ||
      original.reference !== request.reference
    )
      throw new DemoError(
        "idempotency_conflict",
        "This key already belongs to a different request. Use a new key.",
      );
    return { state, posting: previous, replayed: true };
  }
  const source = state.accounts.find(
    (a) => a.id === request.source && a.owned && a.kind === "wallet",
  );
  const destination = state.accounts.find(
    (a) => a.id === request.destination && a.kind === "wallet",
  );
  if (!source || !destination || source.id === destination.id)
    throw new DemoError("invalid_wallet", "Choose two different demo wallets.");
  if (
    !Number.isSafeInteger(request.amount) ||
    request.amount <= 0 ||
    request.amount > 999999999999
  )
    throw new DemoError(
      "invalid_amount",
      "Amount must be a positive integer in minor units within the demo limit.",
    );
  if (request.reference.length > 120)
    throw new DemoError(
      "invalid_reference",
      "Reference must be 120 characters or fewer.",
    );
  const fee = feeFor(request.amount);
  if (balance(state, source.id) - source.held < request.amount + fee)
    throw new DemoError(
      "insufficient_funds",
      "Insufficient available balance, including the 0.5% fee. No entries were posted.",
    );
  const entries: Entry[] = [
    {
      account: source.id,
      amount: -(request.amount + fee),
      currency: source.currency,
    },
  ];
  const system = (kind: Account["kind"], currency: Currency) =>
    state.accounts.find((a) => a.kind === kind && a.currency === currency)!.id;
  if (fee > 0)
    entries.push({
      account: system("fees", source.currency),
      amount: fee,
      currency: source.currency,
    });
  const converted = convert(
    request.amount,
    source.currency,
    destination.currency,
  );
  if (converted <= 0)
    throw new DemoError(
      "amount_too_small",
      "The amount rounds to zero in the receiving currency.",
    );
  if (source.currency !== destination.currency)
    entries.push(
      {
        account: system("fx", source.currency),
        amount: request.amount,
        currency: source.currency,
      },
      {
        account: system("fx", destination.currency),
        amount: -converted,
        currency: destination.currency,
      },
    );
  entries.push({
    account: destination.id,
    amount: converted,
    currency: destination.currency,
  });
  if (Object.values(currencyTotals(entries)).some((total) => total !== 0))
    throw new DemoError("unbalanced", "The posting does not balance.");
  const posting: Posting = {
    id: `TX-${1041 + state.postings.length}`,
    type: source.currency === destination.currency ? "transfer" : "fx",
    reference: request.reference || "Wallet transfer",
    created,
    key,
    request: { ...request },
    entries,
  };
  return {
    state: { ...state, postings: [...state.postings, posting] },
    posting,
    replayed: false,
  };
}
