#!/bin/sh
# Start the zero-dependency tenant admin UI.
# Local requests need no login; remote access requires ADMIN_USER/ADMIN_PASSWORD
# in secrets/admin.env and --allow-remote.
set -eu

BASE=$(cd "$(dirname "$0")" && pwd)
PY="$BASE/python/bin/python3"
[ -x "$PY" ] || { echo "run install.sh first: $BASE/install.sh" >&2; exit 1; }

export ST_MCP_PKG="${ST_MCP_PKG:-$BASE}"
export ST_TENANT_REGISTRY_FILE="${ST_TENANT_REGISTRY_FILE:-${ST_TENANT_REGISTRY:-$BASE/tenants/tenants.json}}"
# 开通/删除租户调用的 tools/*.sh 用 python3 命令，优先用包内解释器。
export PATH="$BASE/python/bin:$PATH"

REMOTE_ARG=""
if [ "${ADMIN_ALLOW_REMOTE:-}" = "1" ]; then REMOTE_ARG=--allow-remote; fi

exec "$PY" "$BASE/tools/st-mcp-admin.py" \
    --host "${ADMIN_HOST:-127.0.0.1}" \
    --port "${ADMIN_PORT:-8001}" \
    --auth-file "${ADMIN_AUTH_FILE:-$BASE/secrets/admin.env}" \
    $REMOTE_ARG
