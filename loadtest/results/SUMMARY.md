# Load test — 2026-10-24 (real run, local box)

Setup: gunicorn `-w 1 --threads 16`, PostgreSQL 17 on the same VM (8 vCPUs, shared with Locust), `CELERY_EAGER=1`
(webhook deposits processed inline), throttles raised, 0.5% transfer fee on. Locust 2.47:
`-u 50 -r 10 -t 60s --headless`.

| Endpoint | Requests | Failures | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| POST /api/transfers/ | 3,913 | 0 | 320 | 530 | 670 |
| GET /api/wallets/{id}/ | 2,064 | 0 | 120 | 170 | 210 |
| GET /api/wallets/{id}/transactions/ | 1,389 | 0 | 130 | 180 | 200 |
| POST /api/wallets/{id}/holds/ | 664 | 0 | 160 | 280 | 480 |
| GET /api/holds/{id}/ | 339 | 0 | 130 | 170 | 200 |
| setup (register, token, wallet, provider charge→webhook) | 200 | 0 | 85–290 | 190–490 | — |
| **Aggregate** | **8,569** | **0 (0.00%)** | **160** | **480** | **590** |

Throughput: **143 req/s aggregate (~65 transfers/s)**.

Post-run integrity (`python manage.py reconcile`): **status `clean`** — 50/50 provider charges matched to
deposits, 0 accounts whose cached balance differs from SUM(entries), NGN entries sum to exactly 0
(11,896 entries, 3,934 transfers posted — the server finished a few in-flight requests after Locust's stats
window closed). /metrics agreed: `ledger_transactions_posted_total{type="transfer"} 3934`.

Raw Locust CSV: `run_stats.csv`.
