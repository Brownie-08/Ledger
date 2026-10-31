# Recruiter demo upgrade

Working directory: `C:\Users\User\Projects\ledger-wallet-api`.
The extracted repository was clean at the start. The unrelated `My Porfolio`
checkout was also clean at `24f9af13a228cf842519e4b49123cf9d498c9699`.
All implementation commands target this standalone Ledger project.

## Delivery phases

1. Frontend foundation: lean Next.js App Router + TypeScript project, lockfile,
   reproducible scripts, type check and production build.
2. Demo experience and wallet dashboard: explicit simulated-data banner, seeded
   multi-currency accounts, transfer form, balance/hold breakdown, activity,
   guided walkthrough and reset. No credentials or database needed.
3. Visual Ledger Explorer: select/search/filter postings, inspect signed entries,
   per-currency balance proof, request details and idempotent replay. Shared demo
   engine keeps the dashboard and explorer consistent.
4. Live integration: authenticated cookie-based API proxy and participant-scoped
   transaction list/detail endpoints.
5. Failure and security: executable failure lab, concurrent retry protection,
   strict minor-unit parsing, production settings and durable task dispatch.
6. Verification and operations: full PostgreSQL suite, real Redis/Celery worker,
   full-stack HTTP checks, CI, OpenAPI and deployment/runbook documentation.
7. Render publication: push the finished source, validate remote CI, review the
   paid resource total with the owner, deploy and check the live workload.

Validate each phase before proceeding. The frontend simulator is educational;
its tests do not establish PostgreSQL concurrency, durability, webhook delivery
or backend integration acceptance. Preserve the Django source and existing data.

## Acceptance evidence

Record commands, results and browser verification in `frontend/README.md` once
each phase is complete. Do not repeat inherited backend coverage/load claims as
newly measured evidence.

## Initial frontend checkpoint — 29 October 2026

Phases 1–3 are implemented and validated locally. Formatting, type checking,
eight demo-engine tests, and the production build pass. Browser checks cover the
guided transfer, replay/conflict, insufficient funds, FX accounting, filtering,
reset, and desktop/mobile layouts. Screenshots and the detailed verification
matrix are in `frontend/README.md`. A separate frontend CI workflow is added;
its remote run remains unverified. No publication was performed.

The separate portfolio repository is still clean at the recorded commit. Django
source and database state were not changed. The next phase is authenticated live
API integration with safe entry visibility and real PostgreSQL validation.

## Full upgrade checkpoint — 29 October 2026

Phases 4–6 are now implemented. Local checks pass: 151 PostgreSQL-backed tests,
95% rounded measured coverage, 10 frontend tests, TypeScript and production build,
OpenAPI and production security checks, real Redis/separate Celery delivery,
webhook retry and deduplication, payout success/reversal and clean reconciliation.
Connected HTTP tests pass for login, transfers, replay, refresh, Origin protection
and logout. See `docs/VERIFICATION.md` for acceptance boundaries.

`render.yaml` passes current schema validation. Publication is the remaining
phase and requires the owner's approval for the recurring worker/database cost.
No existing portfolio hosting resources are repurposed.
