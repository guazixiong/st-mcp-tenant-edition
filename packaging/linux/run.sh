#!/bin/sh
# Start the single-port multi-tenant MCP server from this bundle (no Docker).
set -eu

BASE=$(cd "$(dirname "$0")" && pwd)
EXE="$BASE/python/bin/st-mcp"
[ -x "$EXE" ] || { echo "run install.sh first: $BASE/install.sh" >&2; exit 1; }

REGISTRY="${ST_TENANT_REGISTRY:-$BASE/tenants/tenants.json}"
[ -f "$REGISTRY" ] || {
    echo "tenant registry not found: $REGISTRY" >&2
    echo "create tenants with admin.sh (or edit the JSON manually) before starting." >&2
    exit 1
}
export ST_TENANT_REGISTRY="$REGISTRY"

exec "$EXE" \
    --transport streamable-http \
    --sse-host "${SSE_HOST:-0.0.0.0}" \
    --sse-port "${SSE_PORT:-8100}" \
    --mcp-path "${MCP_PATH:-/mcp}" \
    --log-level "${LOG_LEVEL:-INFO}"
