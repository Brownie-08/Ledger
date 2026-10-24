# Load test — 2026-10-24 (real run, local box, KYC enforced)

Setup: gunicorn `-w 1 --threads 16`, PostgreSQL 17 on the same VM (8 vCPUs, shared with Locust), `CELERY_EAGER=1`
(webhook deposits processed inline), throttles raised, 0.5% transfer fee on, **`KYC_ENFORCED=1`**. Locust 2.47:
`-u 50 -r 10 -t 60s --headless`. Fresh database (`ledger_load`).

Seeding per simulated user: register → JWT → `POST /api/kyc/` → approval to tier 1 through the service layer
(`ledger.kyc.approve`, audited, issues a virtual account) → NGN wallet → ₦500,000 provider charge → signed webhook.

| Endpoint | Requests | Failures | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| POST /api/transfers/ | 3,032 | 0 | 470 | 690 | 770 |
| GET /api/wallets/{id}/ | 1,570 | 0 | 190 | 270 | 320 |
| GET /api/wallets/{id}/transactions/ | 1,016 | 0 | 190 | 270 | 310 |
| POST /api/wallets/{id}/holds/ | 566 | 0 | 220 | 440 | 590 |
| GET /api/holds/{id}/ | 282 | 0 | 190 | 270 | 290 |
| setup (register, token, KYC submit, wallet, provider charge→webhook) | 250 | 0 | 110–380 | 240–640 | — |
| **Aggregate** | **6,716** | **0 (0.00%)** | **240** | **630** | **730** |

Throughput: **112 req/s aggregate (~51 transfers/s)**.

Post-run integrity (`python manage.py reconcile`): **status `clean`**. 50/50 provider charges matched their
deposits, and 0 unallocated inbound items. In SQL, 0 accounts have a cached balance that differs from SUM(entries),
and the NGN entries sum to exactly 0 (9,257 entries). There are 50/50 KYC profiles at tier 1 and approved, and 50 virtual accounts.
3,055 transfers were posted. The server finished a few in-flight requests after Locust's stats window closed. /metrics agreed:
`ledger_transactions_posted_total{type="transfer"} 3055`.

Compared with the previous run without KYC (143 req/s, transfers p50 320 ms), each transfer now also runs the tier
checks under the row locks, including a SUM over today's outflow entries. That costs about 20% throughput on this shared VM.
An index on `(account_id, created_at)` for debit entries, or a per-day outflow counter, would recover most of it.

Raw Locust CSV: `run_stats.csv`.
