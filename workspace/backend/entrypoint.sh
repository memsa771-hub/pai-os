#!/bin/sh
set -e

# Migrations run once in the production Compose `migrate` service.
# PORT remains a generic optional container override. Back-pressure is handled
# by the bounded SQLAlchemy pool and AnyIO thread limiter.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}"
