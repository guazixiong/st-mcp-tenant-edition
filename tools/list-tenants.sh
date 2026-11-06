#!/bin/sh
# 列出所有租户（读单端口注册表）。被管理界面调用，也可命令行单跑。
# 用法：sh tools/list-tenants.sh [--json]
set -eu

SELF=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF/.." && pwd)

resolve_pkg() {
    if [ -n "${ST_MCP_PKG:-}" ]; then printf '%s' "$ST_MCP_PKG"; return; fi
    for d in "$ROOT"/packages/st-mcp-offline-*/ ; do
        if [ -f "$d/deploy.conf" ]; then printf '%s' "${d%/}"; return; fi
    done
    printf '%s' "$ROOT/packages/st-mcp-offline-arm64-20261015"
}
PKG=$(resolve_pkg)
REG=${ST_TENANT_REGISTRY_FILE:-$PKG/tenants/tenants.json}
REGTOOL="$SELF/tenant-registry.py"
[ -f "$REGTOOL" ] || REGTOOL="$PKG/scripts/tenant-registry.py"

conf_get() { if [ -f "$1" ]; then sed -n "s/^$2=//p" "$1" | tail -1; fi; return 0; }
IP=${ST_ADMIN_SERVER_IP:-}
[ -n "$IP" ] || IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "${IP:-}" ] || IP=127.0.0.1
PORT=$(conf_get "$PKG/deploy.conf" HOST_PORT)
[ -n "$PORT" ] || PORT=8100
PATH_EP=$(conf_get "$PKG/deploy.conf" MCP_PATH)
[ -n "$PATH_EP" ] || PATH_EP=/mcp
BASE="http://$IP:$PORT$PATH_EP"

command -v python3 >/dev/null 2>&1 || { printf '{"success": false, "error": "本机没有 python3"}\n'; exit 1; }
[ -f "$REGTOOL" ] || { printf '{"success": false, "error": "缺少 tenant-registry.py"}\n'; exit 1; }

if [ "${1:-}" = "--json" ]; then
    python3 "$REGTOOL" list --file "$REG" --json
else
    printf '注册表：%s\n' "$REG"
    python3 "$REGTOOL" list --file "$REG" --base "$BASE"
fi
