# Free portfolio hosting

Prepared 30 October 2026. Deployment is pending account connections and live
verification. No paid Render resources were created. Do not apply `render.yaml`:
it is a paid alternative retained for reference.

## Architecture and limits

Use **Vercel Hobby** for two projects from `Brownie-08/Ledger`: a Django API at
the repository root and a Next.js frontend with root directory `frontend`.
Use a new, isolated **Neon Free** PostgreSQL project in US East.

The API uses Vercel's Celery adapter and private queue-triggered functions.
It keeps real asynchronous processing; there is no Redis service or always-on
worker to pay for. `worker.py` discovers both ledger and mock-provider tasks.
Posted money, dispatch intent and delivery state remain durable in PostgreSQL.
Celery task results are ignored; application state is the source of truth.

This relies on Vercel's beta Celery/Queues integration and must be verified on
the host before calling it accepted. Local Redis acceptance does not establish
Vercel queue acceptance. Retries use short countdowns relative to the worker's
300-second maximum invocation; deliveries can repeat and handlers deduplicate.

Normal withdrawals and provider events enqueue immediately. Hobby's daily cron
calls the secret-protected `/internal/maintenance/` endpoint, which queues
dispatch recovery, hold expiry, the demo outbox sink and reconciliation.
This replaces the local 10-second dispatch sweep and 60-second hold sweep.
Unpublished or abandoned jobs can wait until the next daily sweep; held funds
can remain reserved until then. Cron timing can be up to 59 minutes late.
Manual authenticated invocation can request an earlier sweep. Failed publishing
returns 503; partially queued maintenance can safely be invoked again.

Hobby is for personal, non-commercial projects. Current included limits include
4 CPU-hours, 360 GB-hours of function memory, one million function invocations,
and one million queue operations monthly. Exceeding free limits can pause the
demo. Neon Free includes 100 CU-hours and 1 GB storage per project, without a
credit card or time limit. Use Free/Hobby only: no Pro trial, paid upgrade,
card entry or paid marketplace integration is authorized.

Sources checked on the date above:

- [Vercel Hobby](https://vercel.com/docs/plans/hobby)
- [Django hosting](https://vercel.com/docs/frameworks/full-stack/django)
- [Celery integration and retry behavior](https://vercel.com/docs/frameworks/backend/celery)
- [Queue limits](https://vercel.com/docs/queues/pricing)
- [Free cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing)
- [Neon pricing](https://neon.com/pricing)

## Provision and release

1. Confirm both accounts are on their free plans. Create a dedicated Neon
   PostgreSQL project; preserve all existing portfolio services and databases.
2. Import the repository root as Django. The root `pyproject.toml` contains
   runtime-only pinned dependencies, the WSGI entrypoint and Celery subscriber.
   `.vercelignore` excludes local data, credentials and the separate frontend.
   Do not enable automatic database migration during preview builds.
3. Set API production environment variables privately:

   | Variable | Value |
   |---|---|
   | `DEBUG` | `0` |
   | `SECRET_KEY` | independent random secret, at least 50 characters |
   | `PROVIDER_WEBHOOK_SECRET` | independent random secret, at least 32 characters |
   | `VIRTUAL_ACCOUNT_SECRET` | independent random secret, at least 32 characters |
   | `CRON_SECRET` | independent random secret, at least 32 characters |
   | `DATABASE_URL` | Neon's pooled PostgreSQL URL with TLS and channel binding |
   | `DB_CONN_MAX_AGE` | `0` |
   | `ALLOWED_HOSTS` | exact production API hostname |
   | `TRUST_PROXY` | `1` |
   | `CELERY_BROKER_URL` | `vercel://` |
   | `CELERY_EAGER` | `0` |
   | `PROVIDER_WEBHOOK_BASE_URL` | production API HTTPS origin |
   | `MOCK_PROVIDER_ENABLED` | `0` |
   | `USE_REDIS_CACHE` | `0` |

   Throttles use per-instance memory in this free configuration; they are not a
   global abuse-control guarantee. Configure the host firewall as appropriate.
   Never expose a database URL, JWT or secret in frontend public variables.
4. From a private local environment pointing only at the new Neon database,
   run `python manage.py migrate --noinput`, then opt-in `seed_demo` with a
   private `DEMO_PASSWORD` of at least 12 characters and `DEMO_SEED_ENABLED=1`.
   This is an explicit release step, never a request-time or preview side effect.
5. Import `frontend` as a separate Next.js project. Set `API_BASE_URL` to the
   production API HTTPS origin and `PUBLIC_APP_URL` to its own production HTTPS
   origin. Leave secure cookies enabled. Redeploy after setting exact origins.
   Use production only for live writes; previews must not target this database.
6. Check `/healthz`, login/cookies, real transfer and replay/conflict, participant
   visibility and clean reconciliation. Separately exercise the queue subscriber
   with a pending mock payout, signed webhook retry/deduplication and reversal.
   Confirm maintenance denies missing/wrong credentials and queues all four tasks
   with the correct credential. Inspect host logs without publishing secrets.
7. Record actual URLs, commit, platform build results, queue results and remaining
   limits in `VERIFICATION.md`. Do not claim deployment from configuration alone.
