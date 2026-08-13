# Alpha-OSK telemetry worker

Cloudflare Worker that receives anonymous, opt-in usage stats from
Alpha-OSK clients and exposes a public aggregate counter.

Design: `docs/architecture/TELEMETRY.md`. Privacy story: `docs/PRIVACY.md`.

## Prerequisites

**Node ≥22.** Recent Wrangler 4.x hard-errors on older Node ("Wrangler
requires at least Node.js v22.0.0"), so `npm run dev` / `npm run deploy`
need Node 22+. `npm install` and `npm run sbom` still work on Node 20.

## One-time setup

```bash
cd backend/cf-worker
npm install
cp wrangler.toml.example wrangler.toml

# Create the D1 database (prints a database_id -- paste into wrangler.toml).
npx wrangler d1 create alpha-osk-telemetry

# Apply schema to the remote DB.
npm run schema:remote
```

## Local dev

```bash
# Apply schema to the local dev DB.
npm run schema:local

# Run the worker locally on http://127.0.0.1:8787 with a local D1.
npm run dev

# In another terminal, smoke-test:
curl -X POST http://127.0.0.1:8787/v1/submit \
    -H 'Content-Type: application/json' \
    -d '{
        "anon_id": "8f2a4c1b-1234-4abc-9def-1234567890ab",
        "app_version": "1.0.16",
        "os": "windows",
        "keystrokes": 1000,
        "words": 100,
        "predictions": 50,
        "keystrokes_saved": 200,
        "minutes": 30.5,
        "sessions": 5,
        "prediction_offers": 80
    }'
# Expect: 204

curl http://127.0.0.1:8787/v1/aggregate
# Expect: {"users":1,"keystrokes":1000,...}
```

## Deploy

```bash
npm run deploy
```

That uploads the worker and binds it to the configured route. The
endpoint URL is printed at the end of `wrangler deploy`. Configure
the client's `TELEMETRY_ENDPOINT` (in `src/telemetry.py`) to match
before shipping a release that has telemetry enabled.

## Routes

- `POST /v1/submit`: upsert one user's lifetime counters. 204 on
  success, 400 with a one-line reason on validation failure. Also 204,
  but a no-op, when the same `anon_id` was submitted too recently (see
  Rate limiting below). That case is deliberately indistinguishable
  from a real success, so re-running the curl example below in quick
  succession will keep returning 204 without necessarily updating the
  row.
- `GET /v1/aggregate`: return summed counters across all users.
  Cached at the edge for 5 minutes.
- `POST /v1/forget`: delete a user's row. Always returns 204
  (doesn't reveal whether the id existed, including when throttled).

## Schema

See `schema.sql`. Two tables: `users` (one row per opted-in install,
keyed by random anon_id) and `submissions_latest` (latest submission
per user, replaced on each POST).

## Cron

Daily at 04:00 UTC, prunes users with `last_seen` older than 365 days.
The CASCADE on `submissions_latest.anon_id` cleans up the child row.

## Rate limiting

Two layers, both keyed on `anon_id` only (never IP, header, geo, or
User-Agent: this worker doesn't read those):

- **Edge**: a Cloudflare Workers rate-limiting binding (`RATE_LIMITER` in
  `wrangler.toml.example`), checked before either POST route touches D1.
  It fails open if the binding isn't configured, so treat it as a cheap
  optimization layered in front of the D1 check, not the primary guarantee.
- **D1**: `POST /v1/submit` won't replace a user's row if the existing one
  is younger than `SUBMIT_COOLDOWN_SECONDS` (1 hour, see the comment on
  that constant in `worker.ts` for why). A throttled request returns the
  same 204 as a real success, on purpose: a distinguishable response would
  let an attacker probe whether a given `anon_id` already exists.

Neither layer stops an attacker who cycles through many distinct fake
`anon_id`s to flood `/v1/submit`. That needs an IP-based control, and this
worker deliberately doesn't implement one in code: doing so would mean
reading and storing caller IPs, which breaks the no-IP privacy guarantee
this service is built around. If you need that protection, add it out of
band: a Cloudflare dashboard WAF rate-limiting rule scoped to this worker's
route (see the comment above the `[[ratelimits]]` block in
`wrangler.toml.example`). That's account configuration the maintainer sets
up by hand, not something this repo can pin in code.

## Threat model

See `docs/architecture/TELEMETRY.md` § "Threat model". Short version: an attacker
who compromises this worker can see anon_ids and lifetime counters
(no IP, no UA, no content) and could try to backfill fake submissions to
poison the aggregate. That's limited by the per-counter sanity ceilings and
the rate limiting described above.
