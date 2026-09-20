# Placement AI Workspace

Placement AI is a FastAPI/PostgreSQL backend with a standalone Next.js frontend.

The `v4-prod-c` branch contains the production deployment source and the
latest application changes. Use `docker-compose.prod.yml` on the server;
`docker-compose.local.yml` is for local development only.

## AWS production architecture

Production runs on one AWS EC2 Ubuntu 24.04 host with Docker Compose:

```text
Internet -> Cloudflare -> host cloudflared systemd service
         -> 127.0.0.1:8080 -> Caddy
         -> app.placement-ai.com -> frontend:3000
         -> api.placement-ai.com -> backend:8000
```

- PostgreSQL 16 runs in Docker on the same EC2 host. Its `pgdata` volume is
  canonical and persistent; PostgreSQL has no published host port.
- AWS S3 stores uploaded files. boto3 uses the EC2 IAM Instance Role through
  its standard credential provider chain; do not create static AWS access keys.
- Redis is an internal, non-canonical cache/PubSub service. It stores only
  bounded-TTL/reconstructible data and Pub/Sub messages, runs without AOF/RDB
  persistence, and uses `allkeys-lru` under a configurable 256 MB ceiling.
- Qdrant is an internal, persisted but rebuildable derived memory index;
  PostgreSQL remains the canonical memory store.
- The durable job worker is a separate process using the backend image.
- Cloudflare Tunnel runs on the host as an existing systemd service, not in
  Compose. Caddy serves internal HTTP only; Cloudflare terminates public TLS.
- Administration uses AWS Systems Manager Session Manager. The EC2 security
  group has zero inbound rules: do not open 22, 80, 443, or application ports.
- No Elastic IP or DNS A record pointing at EC2 is required.

Cloudflare published application routes must be configured as:

```text
app.placement-ai.com -> http://127.0.0.1:8080
api.placement-ai.com -> http://127.0.0.1:8080
```

Production Compose publishes exactly `127.0.0.1:8080:80`. PostgreSQL, backend,
frontend, Redis, and Qdrant are reachable only on the private Docker network.

## Production deployment

Copy `.env.production.example` to an untracked `.env` on EC2 and replace every
required placeholder. Set `DB_PASSWORD` to a strong URL-safe random value (for
example, `openssl rand -hex 32`). Compose builds the internal `DATABASE_URL`
from `DB_USER`, `DB_PASSWORD`, and the private `postgres` service unless an
explicit `DATABASE_URL` override is set.

The S3 bucket must exist in `S3_REGION`, and the EC2 IAM role needs the required
object permissions. Production expects Browser Fabric when
`BROWSERFABRIC_API_KEY` is configured. The backend image retains local Chromium
as a safe fallback; making Chromium an optional image layer can be considered
later without risking a browserless deployment.

Validate and deploy:

```bash
docker compose --env-file .env -f docker-compose.prod.yml config
docker compose --env-file .env -f docker-compose.prod.yml build
docker compose --env-file .env -f docker-compose.prod.yml up -d
docker compose --env-file .env -f docker-compose.prod.yml ps
```

The one-shot `migrate` service runs `alembic upgrade head`. Backend and worker
start only after PostgreSQL is healthy and the migration succeeds. A failed
migration therefore stops deployment.

Create a local custom-format PostgreSQL backup with:

```bash
./scripts/backup-postgres.sh
```

The script writes to the ignored `backups/` directory by default. These files
can later be copied to S3. Backups are not automatic: schedule the script and
test restores before relying on it for disaster recovery.

Timers use a conditional database update committed before event delivery. This
makes claims safe across multiple Uvicorn processes and gives timer delivery
at-most-once semantics: a process failure after claim may lose a timer event,
but another process cannot deliver a duplicate.

`NEXT_PUBLIC_*` variables are compiled into the frontend image. Rebuild the
frontend when they change, and never put backend secrets into those variables.

## Production security operations

FastAPI documentation and OpenAPI metadata are disabled when `APP_ENV` is
`production`. Uvicorn's raw access log is also disabled because it includes the
query string; the application emits method, route template, status, duration,
and request ID for mutations and failed requests without query parameters.
Keep cloudflared at normal informational logging:
debug-level cloudflared or Cloudflare request logs may still record full URLs,
including short-lived stream tickets, and cannot be redacted by this app.

Use Cloudflare rate-limiting rules as the distributed abuse-control layer. At
minimum, apply separate rules to `/v1/auth/sign-in-username`,
`/v1/auth/username-available`, `/v1/account/stream-ticket`, `/v1/files`,
`/v1/files/upload`, `/v1/fetch`, `/v1/search/images`, and `/v1/browser/*`.
Start with monitored/challenge rules, account for campuses sharing one public
IP, then tighten thresholds from observed legitimate traffic. The backend's
existing sign-in and fetch counters are process-local safeguards, not a
replacement for Cloudflare's distributed limits.

The browser currently persists its Supabase session in `localStorage`. That is
standard bearer-token behavior but makes prevention of same-origin XSS
critical. This release sets clickjacking, referrer, MIME-sniffing, and browser
capability headers. A full script/style CSP and an HttpOnly-cookie session
design need a separately tested authentication change because a blind CSP or
cookie migration could break Next.js, analytics, OAuth, and desktop sign-in.
