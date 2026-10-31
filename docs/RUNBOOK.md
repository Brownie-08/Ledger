# Ledger portfolio runbook

This is a synthetic payments demo. KYC, bank accounts, quotes and provider rails
are mock implementations. Do not use real financial or identity information.

## Local full stack

Requires Docker Compose. From the project root:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open `http://127.0.0.1:3000`. The API is at `http://127.0.0.1:8000/api/docs/`.
The one-shot migration service completes before the API, worker and beat start.
Postgres and Redis use named volumes; `docker compose down` preserves them.
Do not use `down -v` unless you explicitly intend to destroy the demo data.

To create synthetic users, supply a private password of at least 12 characters:

```powershell
docker compose exec -e DEMO_SEED_ENABLED=1 -e DEMO_PASSWORD=YOUR_PRIVATE_DEMO_PASSWORD api python manage.py seed_demo
```

Sign in at `/live` as `demo_alex` or `demo_ada`. The seed command prints their NGN
wallet IDs, preserves spent balances on repeated runs, and creates no staff user.
Never publish an admin password. `/` and `/lab` remain available without login.

## Native development

Create a Python 3.13 virtual environment, install `requirements.txt`, and set
`POSTGRES_*` for a dedicated PostgreSQL 16 database. Use a separate Redis URL.
Run migrations, then `python manage.py runserver 127.0.0.1:8000`.
In `frontend/`, copy `.env.example` to `.env.local`, run `npm ci`, then `npm run dev`.
Run worker and beat separately for asynchronous provider flows:

```text
celery -A config worker --concurrency=2 -l info
celery -A config beat -l info --schedule .local/celerybeat
```

The portable `scripts/demo.py` creates a uniquely named database and exercises
the complete HTTP flow in eager mode. It refuses to overwrite an existing DB.
It does not establish real-worker acceptance; use `scripts/verify_worker.py` for
that, with `CELERY_EAGER=0` and a database ending in `_verification`.

## Free deployment (live)

The owner selected zero-cost hosting. The public playground is
<https://ledger-wallet-ui-udohpeterbrown-1954s-projects.vercel.app/>. Follow
[Vercel + Neon](FREE-HOSTING.md) for configuration and limits and
[verification](VERIFICATION.md) for the hosted checks. The free hosted
maintenance sweep runs daily; the local worker/beat frequencies above remain.

## Render deployment (paid alternative, not approved)

`render.yaml` creates only resources named `ledger-wallet-*` in Oregon, using
`https://github.com/Brownie-08/Ledger` on `main`. It does not reuse the separate
portfolio database, services or environment groups.

- UI and API: Free web services; they may sleep and take time to wake up.
- Worker: smallest paid compute plan, one Celery worker with embedded beat.
- Database: dedicated PostgreSQL 16, smallest paid compute and 1 GB disk.
- Queue: Free Key Value, private network access only, `noeviction` policy.

The workspace already has its one free Postgres allocation, so a new isolated DB
requires paid compute. Review the Dashboard’s current recurring total before
applying. The intended base estimate is about **USD 13.30/month**, excluding usage
overages: worker USD 7, database USD 6, database storage USD 0.30. Pricing source:
[Render pricing](https://render.com/pricing). Never enable a charged resource
without the owner’s authorization.

The worker's release command owns migrations and opt-in seeding. The API waits
read-only for the schema to be ready. Render generates independent secrets;
the seed password is stored only on the worker. Retrieve it privately in the
Dashboard if you want to sign into the synthetic live accounts. No credentials
are written into source code or the frontend bundle.

Free Key Value is volatile. PostgreSQL dispatch intent and periodic recovery
can redeliver unfinished work. This is an at-least-once design, not exactly-once
delivery. Keep one worker/beat instance for this demo; split beat into its own
service before horizontally scaling. Paid API/UI/queue plans can remove the
free-plan limitations later.

## Security and operations

Production rejects insecure default secrets and wildcard hosts. HTTPS redirects,
HSTS, secure cookies and proxy trust are configured explicitly. A Render-generated
256-bit key is SHA-512-normalised if its base64 representation is shorter than
Django's length heuristic; this preserves its existing entropy.

The frontend stores JWTs only in HTTP-only, SameSite cookies; its proxy checks
Origin for writes and restricts accessible routes. `SESSION_COOKIE_SECURE=0` is
only for local HTTP preview. Production uses secure cookies.

Full transaction entry visibility is restricted to participants or staff;
counterparty usernames, internal metadata and idempotency keys are omitted.
Raw mock checkout endpoints default off outside development.

Use `/healthz` for database readiness, `/metrics` for Prometheus, and the staff-only
reconciliation endpoint or `python manage.py reconcile` for accounting checks.
Outbox event publication is still a demo sink; durable Celery dispatch intent is
implemented separately. Delivery retries use explicit exponential backoff with
jitter; pending dispatches are recovered after five minutes.

Back up PostgreSQL before changes. Additive migration `0009` creates dispatch
intent without editing ledger history. Rolling back application code does not
reverse posted money, migrations or provider events. Preserve database backups
and apply new correcting entries rather than editing append-only entries.
