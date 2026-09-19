# Placement AI Workspace

Placement AI is a FastAPI/PostgreSQL backend with a standalone Next.js frontend.

## Local development

Development Compose intentionally includes local PostgreSQL, published localhost
ports, source bind mounts, and local Playwright/Chromium support.

```bash
cd workspace
make dev
```

The backend is available at `http://localhost:8000` and the frontend at
`http://localhost:3000`. Copy `.env.example` to `.env` for local credentials.

## AWS production architecture

Production runs on one AWS EC2 Ubuntu 24.04 host with Docker Compose:

```text
Internet -> Cloudflare -> host cloudflared systemd service
         -> 127.0.0.1:8080 -> Caddy
         -> app.placement-ai.com -> frontend:3000
         -> api.placement-ai.com -> backend:8000
```

- AWS RDS PostgreSQL is canonical and external to Compose.
- AWS S3 stores uploaded files. boto3 uses the EC2 IAM Instance Role through
  its standard credential provider chain; do not create static AWS access keys.
- Redis is an internal, non-canonical cache/pub-sub service.
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

Production Compose publishes exactly `127.0.0.1:8080:80`. Backend, frontend,
Redis, Qdrant, and RDS are never published by Compose.

## Production deployment

Copy `.env.production.example` to an untracked `.env` on EC2 and replace every
required placeholder. `DATABASE_URL` should be an RDS URL such as:

```text
postgresql://USER:PASSWORD@RDS_HOST:5432/openagents_workspace?sslmode=require
```

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
```

The one-shot `migrate` service runs `alembic upgrade head`. Backend and worker
start only after it succeeds. A failed migration therefore stops deployment.

Timers use a conditional database update committed before event delivery. This
makes claims safe across multiple Uvicorn processes and gives timer delivery
at-most-once semantics: a process failure after claim may lose a timer event,
but another process cannot deliver a duplicate.

`NEXT_PUBLIC_*` variables are compiled into the frontend image. Rebuild the
frontend when they change, and never put backend secrets into those variables.

## Useful commands

```bash
cd workspace
make test
make migrate
make migration msg="add_new_table"
make reset-db
```
