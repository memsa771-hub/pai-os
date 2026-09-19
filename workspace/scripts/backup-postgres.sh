#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKSPACE_DIR=$(dirname -- "$SCRIPT_DIR")
ENV_FILE=${ENV_FILE:-"$WORKSPACE_DIR/.env"}
BACKUP_DIR=${BACKUP_DIR:-"$WORKSPACE_DIR/backups"}
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT="$BACKUP_DIR/openagents_workspace_$TIMESTAMP.dump"
TEMP_OUTPUT="$OUTPUT.tmp"

if [ ! -f "$ENV_FILE" ]; then
    echo "Environment file not found: $ENV_FILE" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
trap 'rm -f "$TEMP_OUTPUT"' 0 1 2 15

docker compose \
    --env-file "$ENV_FILE" \
    -f "$WORKSPACE_DIR/docker-compose.prod.yml" \
    exec -T postgres \
    sh -c 'exec pg_dump --format=custom --no-owner --no-acl --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    > "$TEMP_OUTPUT"

chmod 600 "$TEMP_OUTPUT"
mv "$TEMP_OUTPUT" "$OUTPUT"
trap - 0 1 2 15
echo "PostgreSQL backup written to $OUTPUT"
