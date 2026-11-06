#!/bin/sh
# 删除一个租户：从注册表移除记录（token 立即失效）。
# 默认不动数据库账号（DROP USER 不可逆，交给人确认后手工执行）。
# 用法：sh tools/delete-tenant.sh -t tenant_alice [--simulate]
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
DATA=${ST_ADMIN_DATA:-$ROOT/tenants}
REG=${ST_TENANT_REGISTRY_FILE:-$PKG/tenants/tenants.json}
REGTOOL="$SELF/tenant-registry.py"
[ -f "$REGTOOL" ] || REGTOOL="$PKG/scripts/tenant-registry.py"
TENANT=
SIMULATE=no

die() { printf '{"success": false, "error": "%s"}\n' "$1"; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--tenant) [ $# -ge 2 ] || die "参数 $1 缺少取值"; TENANT=$2; shift 2 ;;
        -s|--simulate) SIMULATE=yes; shift ;;
        --keep-container) shift ;;  # 旧参数，单端口模式没有租户容器
        -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[ -n "$TENANT" ] || die "缺少租户名（-t tenant_alice）"
case "$TENANT" in *[!A-Za-z0-9_-]*) die "租户名只能用字母、数字、下划线、连字符" ;; esac
case "$TENANT" in default) die "default 是主账号，不允许删除（要停用请改 deploy.conf 的 ST_USER）" ;; esac
command -v python3 >/dev/null 2>&1 || die "本机没有 python3"
[ -f "$REGTOOL" ] || die "缺少 tenant-registry.py"

DB_USER=$(python3 "$REGTOOL" get --file "$REG" --name "$TENANT" --field user 2>/dev/null || true)
[ -n "$DB_USER" ] || die "注册表里没有租户 $TENANT"

if [ "$SIMULATE" = yes ]; then
    printf '{"success": true, "message": "预览模式：未删除租户 %s", "tenant": "%s"}\n' "$TENANT" "$TENANT"
    exit 0
fi

python3 "$REGTOOL" remove --file "$REG" --name "$TENANT" >/dev/null
rm -f "$DATA/sql/$TENANT.sql"
# 旧版本按租户部署时留下的文件，顺手清掉
rm -f "$DATA/secrets/$TENANT.env" "$DATA/instances/$TENANT.conf"
rm -f "$PKG/secrets/$TENANT.env" "$PKG/instances/$TENANT.conf" 2>/dev/null || true

printf '{"success": true, "message": "租户 %s 已从注册表移除（token 立即失效）", "tenant": "%s", "db_user": "%s", "note": "数据库账号未删除，如需回收请手工执行 DROP USER \\"%s\\" CASCADE"}\n' \
    "$TENANT" "$TENANT" "$DB_USER" "$DB_USER"
