# Ledger wallet playground

Local portfolio frontend built with Next.js App Router, React, and TypeScript.
The Django backend is kept in the parent project. The playground and failure lab
run without an API or credentials. The separate `/live` workspace authenticates
through a server-side proxy and reads and posts actual PostgreSQL transactions.

## Run

Use Node.js 22.18 or newer (Node 25 was used locally).

```bash
# from the repository root
cd frontend
npm ci
npm run dev
```

Open the local URL printed by Next.js (normally `http://127.0.0.1:3000`).
For production preview, run `npm run build` then `npm start`.

## Demo script

1. Select **Take the 60-second tour**. Post the guided NGN 10,000 transfer.
2. Inspect the sender debit of NGN 10,050, recipient credit of NGN 10,000,
   and fee credit of NGN 50 in Ledger Explorer. NGN entries sum to zero.
3. Replay the request. The transaction count and balances stay unchanged.
4. Return to Overview. Select another currency card to filter wallet activity.
5. For FX, choose NGN as source and your USD wallet as destination. Generate a
   new idempotency key before submitting a different request. Send NGN 150,000;
   inspect five entries that balance separately in NGN and USD.
6. Try an amount above available funds: the error leaves the ledger unchanged.
7. Use **Reset demo → Restore sample data**, or refresh, to restore the fixtures.

Search supports references, IDs, currencies and idempotency keys. Type filters
compose with search. Open a posting’s request details to see integer minor units.

## Structure and boundaries

- `app/page.tsx`: responsive dashboard, guided tour, explorer, architecture view.
- `app/globals.css`: local font stack, CSS illustrations and responsive layouts.
- `lib/demo.ts`: pure, immutable-update demo adapter shared by every view.
- `tests/demo.test.ts`: accounting invariants, parsing, overdraft and replay tests.
- `app/lab/page.tsx` and `lib/scenarios.ts`: executable, isolated failure experiments.
- `app/live/page.tsx`: authenticated wallets, transfers and ledger entry details.
- `app/api/`: HTTP-only cookie sessions, refresh, route allowlist and Origin checks.

Playground data is in React memory for the current page instance. It makes no
network API call. The sample hold is a fixed authorisation, not a posted entry.
The simulator mirrors signed minor units, the default 50-basis-point fee, available
funds checks, idempotent requests, and FX account flows. Quotes are fixed demo
values (USD = NGN 1,500; GBP = NGN 1,900); conversions round down to minor units.
It does **not** implement backend KYC caps, JWT permissions, real concurrency,
webhooks, provider settlement, reconciliation, withdrawals, or durable storage.

Do not present the frontend’s simulation results as backend runtime acceptance.
The full backend suite has now been run against isolated PostgreSQL, including
the real migrations and triggers. Connected mode writes only to the configured
API database; the playground and failure lab remain local simulations.

## Validation

```bash
npm run typecheck
npm test
npm run build
```

Validated on 29 October 2026:

| Phase | Result | Evidence |
| --- | --- | --- |
| Foundation | Complete | Formatting, generated route types + TypeScript check, and production build pass; root returns HTTP 200. Direct dependencies are pinned and the lockfile is included. |
| Demo and dashboard | Complete | Browser-verified guided transfer, wallet balance/hold display, USD currency switching, idempotency conflict, insufficient funds, reset, and engineering notes. |
| Ledger Explorer | Complete | Browser-verified signed entries, NGN/USD FX zero sums, replay, search empty state, type filter, and expandable request JSON. |
| Demo engine and failure lab | Complete | All 10 tests pass, including all six FX directions and executable failure cases. |
| Browser layout | Complete for sampled sizes | Desktop overview and explorer, 390 px mobile overview and explorer. No document/nav horizontal overflow at the sampled mobile size. No browser console warnings or errors observed. |
| Dependencies | Checked | `npm audit --omit=dev --audit-level=high` reports 0 vulnerabilities. |
| CI | Added | Separate frontend workflow runs format, typecheck, tests, and build. Remote CI has not been run in this session. |
| Backend/live integration | Implemented | 151 PostgreSQL tests pass. HTTP acceptance checks cover login, posting, replay/conflict, cross-origin rejection, refresh, logout and balanced FX entries. |

`npm run typecheck` generates Next.js route declarations before checking TypeScript,
so it also works before the first build in a fresh checkout.

Preview captures:

- [Desktop dashboard](docs/screenshots/overview-desktop.jpg)
- [Desktop explorer](docs/screenshots/explorer-desktop.jpg)
- [Mobile explorer](docs/screenshots/explorer-mobile.jpg)

Both the standalone Ledger checkout and `My Porfolio` started clean. Final
read-only checks confirm `My Porfolio` is still clean at
`24f9af13a228cf842519e4b49123cf9d498c9699`. All changes are inside this Ledger project.

## Connected mode

Set server-only `API_BASE_URL` to the Django origin and `PUBLIC_APP_URL` to the
frontend origin in `.env.local`. Run the backend and seed synthetic accounts as
described in the [runbook](../docs/RUNBOOK.md). Sign in at `/live`. JWTs remain in
HTTP-only cookies and never appear in browser storage or login JSON.

The new `/api/transactions/` and detail endpoints restrict visibility to a
participant or staff. Full balanced entries are shown with counterparty identities
redacted. The proxy cannot access arbitrary backend or admin paths.

For deployment use HTTPS and secure cookies. Only local HTTP preview uses
`SESSION_COOKIE_SECURE=0`. Native full-stack verification is recorded in
[verification evidence](../docs/VERIFICATION.md); Docker and hosted checks are
reported separately.
