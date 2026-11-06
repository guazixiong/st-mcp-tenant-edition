#!/bin/sh
# 开通一个租户：在神通库里建/对齐库账号，然后往注册表写一条 token→账号 的记录。
# 单端口多租户：所有租户共用同一个 MCP 地址，token 决定用哪个库账号，
# 所以这里不建容器、不占端口，也不需要重启服务（服务端按文件 mtime 热加载）。
#
# 用法：
#   sh tools/create-tenant.sh -t tenant_alice [-u 库用户名] [-p '库口令']
#                             [-H 库IP] [-N 库端口] [-d 库名] [-I 对外IP]
#                             [-P MCP端口] [-D 交付包目录]
#                             [--token 指定token] [--restricted]
#                             [--no-db-user] [--simulate]
#
# 输出最后一行是 JSON（管理界面按它渲染）。
set -eu

SELF=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF/.." && pwd)
REGTOOL="$SELF/tenant-registry.py"
[ -f "$REGTOOL" ] || REGTOOL="$PKG/scripts/tenant-registry.py"

resolve_pkg() {
    if [ -n "${ST_MCP_PKG:-}" ]; then printf '%s' "$ST_MCP_PKG"; return; fi
    for d in "$ROOT"/packages/st-mcp-offline-*/ ; do
        if [ -f "$d/deploy.conf" ]; then printf '%s' "${d%/}"; return; fi
    done
    printf '%s' "$ROOT/packages/st-mcp-offline-arm64-20261015"
}

die() { printf '{"success": false, "error": "%s"}\n' "$1"; exit 1; }
warn() { printf 'WARN: %s\n' "$1" >&2; }

PKG=$(resolve_pkg)
DATA=${ST_ADMIN_DATA:-$ROOT/tenants}
DB_HOST=${ST_DB_HOST:-}
DB_PORT=${ST_DB_PORT:-}
DB_NAME=${ST_DB_NAME:-}
SERVER_IP=${ST_ADMIN_SERVER_IP:-}
TENANT=
DB_USER=
PASSWORD=
TOKEN=
MCP_PORT=
ACCESS_MODE=unrestricted
INSTALL=yes
SIMULATE=
DBUSER=auto
PASSWORD_STDIN=no

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--tenant)   [ $# -ge 2 ] || die "参数 $1 缺少取值"; TENANT=$2; shift 2 ;;
        -u|--db-user)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_USER=$2; shift 2 ;;
        -p|--password) [ $# -ge 2 ] || die "参数 $1 缺少取值"; PASSWORD=$2; shift 2 ;;
        -P|--port)     [ $# -ge 2 ] || die "参数 $1 缺少取值"; MCP_PORT=$2; shift 2 ;;
        -H|--db-host)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_HOST=$2; shift 2 ;;
        -N|--db-port)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_PORT=$2; shift 2 ;;
        -d|--db)       [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_NAME=$2; shift 2 ;;
        -I|--server-ip) [ $# -ge 2 ] || die "参数 $1 缺少取值"; SERVER_IP=$2; shift 2 ;;
        -D|--pkg)      [ $# -ge 2 ] || die "参数 $1 缺少取值"; PKG=$2; shift 2 ;;
        --token)       [ $# -ge 2 ] || die "参数 $1 缺少取值"; TOKEN=$2; shift 2 ;;
        --password-stdin) PASSWORD_STDIN=yes; shift ;;
        --restricted)  ACCESS_MODE=restricted; shift ;;
        --no-install)  INSTALL=no; shift ;;
        --no-db-user)  DBUSER=no; shift ;;
        --db-user)     DBUSER=yes; shift ;;
        -s|--simulate) SIMULATE=yes; shift ;;
        -h|--help)     sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[ -n "$TENANT" ] || die "缺少租户名（-t tenant_alice）"
command -v python3 >/dev/null 2>&1 || die "本机没有 python3（注册表与 token 生成需要它）"

# ---- 校验 ----
case "$TENANT" in *[!A-Za-z0-9_-]*) die "租户名只能用字母、数字、下划线、连字符" ;; esac
case "$TENANT" in [A-Za-z]*) : ;; *) die "租户名必须以字母开头" ;; esac
[ ${#TENANT} -le 28 ] || die "租户名最长 28 个字符"
case "$TENANT" in default|main) die "租户名 $TENANT 是主账号保留名，换一个" ;; esac

conf_get() { if [ -f "$1" ]; then sed -n "s/^$2=//p" "$1" | tail -1; fi; return 0; }
DEPLOY_CONF="$PKG/deploy.conf"
[ -n "$DB_HOST" ] || DB_HOST=$(conf_get "$DEPLOY_CONF" ST_HOST)
[ -n "$DB_HOST" ] || DB_HOST=127.0.0.1
[ -n "$DB_PORT" ] || DB_PORT=$(conf_get "$DEPLOY_CONF" ST_PORT)
[ -n "$DB_PORT" ] || DB_PORT=2003
[ -n "$DB_NAME" ] || DB_NAME=$(conf_get "$DEPLOY_CONF" ST_DB)
[ -n "$DB_NAME" ] || DB_NAME=OSRDB
[ -n "$MCP_PORT" ] || MCP_PORT=$(conf_get "$DEPLOY_CONF" HOST_PORT)
[ -n "$MCP_PORT" ] || MCP_PORT=8100
CONTAINER=$(conf_get "$DEPLOY_CONF" CONTAINER_NAME)
[ -n "$CONTAINER" ] || CONTAINER=st-mcp

case "$DB_PORT" in ''|*[!0-9]*) die "库端口必须是数字" ;; esac
case "$MCP_PORT" in ''|*[!0-9]*) die "MCP 端口必须是数字" ;; esac

if [ -z "$SERVER_IP" ]; then
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -n "${SERVER_IP:-}" ] || SERVER_IP=127.0.0.1
fi

# 口令与 token：不传就生成；两者都只写进 600 注册表，不进命令行
gen_secret() { python3 -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"; }
if [ "$PASSWORD_STDIN" = yes ]; then
    IFS= read -r PASSWORD || true
fi
if [ -z "$PASSWORD" ]; then PASSWORD=$(gen_secret 18); fi
case "$PASSWORD" in *[!A-Za-z0-9@#%_.+~=-]*) die "口令只能用字母数字和 @ # % _ . + ~ = - 这些符号" ;; esac
[ ${#PASSWORD} -ge 8 ] || die "口令至少 8 位"

REG=${ST_TENANT_REGISTRY_FILE:-$PKG/tenants/tenants.json}
mkdir -p "$(dirname "$REG")" "$DATA/sql"
chmod 755 "$(dirname "$REG")" 2>/dev/null || true

# 同名租户已存在时保留原 token（除非显式 --token），避免用户手里的 URL 突然失效
EXISTS=no
if python3 "$REGTOOL" get --file "$REG" --name "$TENANT" >/dev/null 2>&1; then
    EXISTS=yes
    warn "租户 $TENANT 已存在，将更新库口令与访问模式"
fi
if [ -z "$TOKEN" ]; then
    if [ "$EXISTS" = yes ]; then
        TOKEN=$(python3 "$REGTOOL" get --file "$REG" --name "$TENANT" --field token)
    else
        TOKEN=$(gen_secret 24)
    fi
fi
[ ${#TOKEN} -ge 16 ] || die "token 至少 16 个字符"

# 库用户名：显式 -u 优先；更新已有租户时沿用注册表里的账号，不悄悄换库账号
if [ -z "$DB_USER" ] && [ "$EXISTS" = yes ]; then
    DB_USER=$(python3 "$REGTOOL" get --file "$REG" --name "$TENANT" --field user 2>/dev/null || true)
fi
if [ -z "$DB_USER" ]; then
    DB_USER=$(printf '%s' "$TENANT" | LC_ALL=C tr 'a-z-' 'A-Z_')
fi
case "$DB_USER" in *[!A-Z0-9_]*) die "库用户名只能大写字母、数字、下划线：$DB_USER" ;; esac
[ ${#DB_USER} -le 30 ] || die "库用户名最长 30 个字符（神通标识符限制）：$DB_USER"

# ---- 建库 SQL（自动建账号失败时留给人手工执行）----
SQLF="$DATA/sql/$TENANT.sql"
cat > "$SQLF" <<EOF
-- 租户 $TENANT 的库账号，在神通实例 $DB_NAME 上执行一次（dbstudio / bin/isql 均可）
-- 语法已在 OSCAR V7.0.8 实测：CREATE USER ... WITH PASSWORD 可用；
-- 建完就能登录，不要写 GRANT CONNECT（神通不认这个 Oracle 语法，会报 syntax error at "CONNECT"）。
CREATE USER "$DB_USER" WITH PASSWORD '$PASSWORD';
-- 神通建用户时自动建同名 schema；若查询报"schema 不存在"，再执行下面一行
-- CREATE SCHEMA "$DB_USER" AUTHORIZATION "$DB_USER";
-- 只允许该账号访问自己的 schema（不额外授权，MCP 的 ALL_* 视图就看不到别人的表）
EOF
chmod 600 "$SQLF" 2>/dev/null || true

# ---- 建/对齐库账号 ----
DBUSER_RESULT=skipped
HAS_DOCKER=no
command -v docker >/dev/null 2>&1 && HAS_DOCKER=yes
CONTAINER_UP=no
if [ "$HAS_DOCKER" = yes ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
    CONTAINER_UP=yes
fi

if [ -n "$SIMULATE" ]; then
    DBUSER_RESULT=preview
elif [ "$DBUSER" = no ]; then
    :
elif [ "$CONTAINER_UP" = yes ]; then
    TMPENV=$(mktemp "${TMPDIR:-/tmp}/st-tenant.XXXXXX")
    chmod 600 "$TMPENV"
    printf 'ST_PASSWORD=%s\n' "$PASSWORD" > "$TMPENV"
    set +e
    DBU_OUT=$(sh "$SELF/create-db-user.sh" -t "$TENANT" -u "$DB_USER" -E "$TMPENV" -D "$PKG" \
        -c "$CONTAINER" -H "$DB_HOST" -N "$DB_PORT" -d "$DB_NAME" 2>&1)
    DBU_RC=$?
    set -e
    rm -f "$TMPENV"
    if [ $DBU_RC -ne 0 ]; then
        MSG=$(printf '%s' "$DBU_OUT" | tr '\n' ' ' | tr -d '"' | cut -c1-220)
        die "库账号创建失败：$MSG（SQL 已留在 $SQLF，可手工执行后加 --no-db-user 重跑）"
    fi
    DBUSER_RESULT=created
else
    if [ "$DBUSER" = yes ]; then
        die "本机没有运行中的主实例容器 $CONTAINER，无法复用驱动建库账号（SQL 已留在 $SQLF）"
    fi
    DBUSER_RESULT=manual
    warn "没有运行中的主实例容器，库账号未创建；请手工执行 $SQLF 后重跑本命令（或加 --no-db-user 只写注册表）"
fi

# ---- 写注册表 ----
printf '%s' "$PASSWORD" | python3 "$REGTOOL" add --file "$REG" \
    --name "$TENANT" --token "$TOKEN" --user "$DB_USER" --password-stdin \
    --host "$DB_HOST" --port "$DB_PORT" --database "$DB_NAME" \
    --access-mode "$ACCESS_MODE" >/tmp/st-tenant-add.$$ 2>&1 \
    || { MSG=$(tr '\n' ' ' < /tmp/st-tenant-add.$$ | cut -c1-220); rm -f /tmp/st-tenant-add.$$; die "写注册表失败：$MSG"; }
rm -f /tmp/st-tenant-add.$$
chmod 600 "$REG" 2>/dev/null || true

# ---- 提醒：服务端必须是新版（认注册表），否则这条记录不会生效 ----
SERVICE_MODE=new
if [ "$CONTAINER_UP" = yes ]; then
    if ! docker exec "$CONTAINER" printenv ST_TENANT_REGISTRY >/dev/null 2>&1; then
        SERVICE_MODE=old-image
        warn "主实例容器仍是旧镜像：注册表已写入，但要在包目录执行 sh install-all.sh 重建/重装后才会生效"
    fi
fi

URL="http://$SERVER_IP:$MCP_PORT/mcp/$TOKEN"
CLIENT=$(printf '{"mcpServers": {"st-mcp-%s": {"url": "%s"}}}' "$TENANT" "$URL")

printf '\n租户 %s 已写入注册表\n' "$TENANT"
printf '  库账号：%s@%s:%s/%s（%s）\n' "$DB_USER" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DBUSER_RESULT"
printf '  访问模式：%s\n' "$ACCESS_MODE"
printf '  MCP 地址：%s\n' "$URL"
printf '  客户端配置：%s\n' "$CLIENT"
printf '\n'

printf '{"success": true, "message": "租户已开通", "data": {"tenant": "%s", "db_user": "%s", "db_account": "%s", "token": "%s", "access_mode": "%s", "mcp_url": "%s", "client_config": "%s", "service_mode": "%s", "sql_file": "%s", "registry": "%s"}}\n' \
    "$TENANT" "$DB_USER" "$DBUSER_RESULT" "$TOKEN" "$ACCESS_MODE" "$URL" \
    "$(printf '%s' "$CLIENT" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')" \
    "$SERVICE_MODE" "$SQLF" "$REG"
