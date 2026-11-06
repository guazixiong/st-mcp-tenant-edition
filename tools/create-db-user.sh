#!/bin/sh
# 在神通库里建一个租户账号（复用已部署主实例容器里的 STPython 驱动）。
# create-tenant.sh 会自动调用它；也可以单独跑。
#
# 用法：
#   sh tools/create-db-user.sh -t tenant_alice [-u 库用户名] [-E 口令文件]
#                              [-D 包目录] [-c 主实例容器名]
#                              [-H 库IP] [-N 库端口] [-d 库名] [--password-stdin]
#
# 口令来源：--password-stdin 从标准输入读一行；-E 指定的 env 文件（含 ST_PASSWORD=...）；
# 否则读 $PKG/secrets/<租户>.env。库管理员口令从主实例容器的 ST_PASSWORD 读。
# 口令只经 stdin 进容器，不出现在命令行和进程参数里。
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

die() { printf '{"success": false, "error": "%s"}\n' "$1"; exit 1; }

TENANT=
DB_USER=
ENVF_OVERRIDE=
PKG=
CONTAINER=
DB_HOST=
DB_PORT=
DB_NAME=
PASSWORD_STDIN=no

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--tenant)   [ $# -ge 2 ] || die "参数 $1 缺少取值"; TENANT=$2; shift 2 ;;
        -u|--db-user)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_USER=$2; shift 2 ;;
        -E|--env-file) [ $# -ge 2 ] || die "参数 $1 缺少取值"; ENVF_OVERRIDE=$2; shift 2 ;;
        -D|--pkg)      [ $# -ge 2 ] || die "参数 $1 缺少取值"; PKG=$2; shift 2 ;;
        -c|--container) [ $# -ge 2 ] || die "参数 $1 缺少取值"; CONTAINER=$2; shift 2 ;;
        -H|--db-host)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_HOST=$2; shift 2 ;;
        -N|--db-port)  [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_PORT=$2; shift 2 ;;
        -d|--db)       [ $# -ge 2 ] || die "参数 $1 缺少取值"; DB_NAME=$2; shift 2 ;;
        --password-stdin) PASSWORD_STDIN=yes; shift ;;
        -h|--help)     sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[ -n "$TENANT" ] || die "缺少租户名（-t tenant_alice）"
[ -n "$PKG" ] || PKG=$(resolve_pkg)

conf_get() { if [ -f "$1" ]; then sed -n "s/^$2=//p" "$1" | tail -1; fi; return 0; }

CONF="$PKG/instances/$TENANT.conf"
if [ -n "$ENVF_OVERRIDE" ]; then
    ENVF="$ENVF_OVERRIDE"
else
    ENVF="$PKG/secrets/$TENANT.env"
fi
if [ "$PASSWORD_STDIN" = yes ]; then
    IFS= read -r PASSWORD || true
    [ -n "$PASSWORD" ] || die "标准输入里没有读到口令"
else
    [ -f "$ENVF" ] || die "找不到口令文件 $ENVF"
    PASSWORD=$(sed -n 's/^ST_PASSWORD=//p' "$ENVF" | tail -1)
    [ -n "$PASSWORD" ] || die "$ENVF 里没有 ST_PASSWORD"
fi
case "$PASSWORD" in *[!A-Za-z0-9@#%_.+~=-]*) die "口令含脚本不支持的字符" ;; esac

[ -n "$DB_HOST" ] || DB_HOST=$(conf_get "$CONF" ST_HOST)
[ -n "$DB_HOST" ] || DB_HOST=$(conf_get "$PKG/deploy.conf" ST_HOST)
[ -n "$DB_HOST" ] || DB_HOST=127.0.0.1
[ -n "$DB_PORT" ] || DB_PORT=$(conf_get "$CONF" ST_PORT)
[ -n "$DB_PORT" ] || DB_PORT=$(conf_get "$PKG/deploy.conf" ST_PORT)
[ -n "$DB_PORT" ] || DB_PORT=2003
[ -n "$DB_NAME" ] || DB_NAME=$(conf_get "$CONF" ST_DB)
[ -n "$DB_NAME" ] || DB_NAME=$(conf_get "$PKG/deploy.conf" ST_DB)
[ -n "$DB_NAME" ] || DB_NAME=OSRDB
GATEWAY=$(conf_get "$CONF" ST_HOST_GATEWAY)
[ -n "$GATEWAY" ] || GATEWAY=172.17.0.1

[ -n "$CONTAINER" ] || CONTAINER=$(conf_get "$PKG/deploy.conf" CONTAINER_NAME)
[ -n "$CONTAINER" ] || CONTAINER=st-mcp

if [ -z "$DB_USER" ]; then
    DB_USER=$(printf '%s' "$TENANT" | LC_ALL=C tr 'a-z-' 'A-Z_')
fi

# 建账号的语句要拼进容器内 Python，先钉死字符集，避免注入
case "$DB_USER" in *[!A-Z0-9_]*) die "库用户名异常：$DB_USER" ;; esac
case "$DB_HOST" in *[!A-Za-z0-9._:-]*) die "数据库地址异常：$DB_HOST" ;; esac
case "$DB_PORT" in ''|*[!0-9]*) die "数据库端口异常：$DB_PORT" ;; esac
case "$DB_NAME" in *[!A-Za-z0-9_]*) die "数据库名异常：$DB_NAME" ;; esac

command -v docker >/dev/null 2>&1 || die "本机没有 docker，无法复用主实例驱动建账号"
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" \
    || die "主实例容器 $CONTAINER 未运行：先 sh install-all.sh，或用 -c 指定容器名"

# 127.0.0.1 在容器里指向容器自己，改写成宿主网桥地址（与 entrypoint.sh 同规则）
case "$DB_HOST" in
    127.0.0.1|localhost|::1) DB_HOST_TARGET=$GATEWAY ;;
    *) DB_HOST_TARGET=$DB_HOST ;;
esac

# 口令走 stdin 的 JSON；脚本本身不含任何机密，可以放 argv。
SCRIPT='
import json
import os
import sys

payload = json.load(sys.stdin)
user = payload["user"]
password = payload["password"]
dsn = payload["dsn"]
admin_user = os.environ.get("ST_USER", "SYSDBA")
admin_pw = os.environ.get("ST_PASSWORD", "")

result = {"success": False, "user": user, "db": dsn, "created": None, "verified": False}
try:
    import STPython
except Exception as exc:
    result["error"] = "容器内没有 STPython 驱动：%s" % exc
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)

def open_conn(u, p):
    try:
        return STPython.connect(user=u, password=p, dsn=dsn)
    except TypeError:
        return STPython.connect(u, p, dsn)

try:
    conn = open_conn(admin_user, admin_pw)
    cur = conn.cursor()

    try:
        cur.execute("SELECT USERNAME FROM ALL_USERS WHERE USERNAME = \x27" + user + "\x27")
        result["created"] = cur.fetchone() is None
    except Exception:
        result["created"] = None

    ddl_error = ""
    try:
        cur.execute("CREATE USER \"" + user + "\" WITH PASSWORD \x27" + password + "\x27")
    except Exception as exc:
        ddl_error = str(exc)[:300]

    # 账号已存在时把口令对齐（新建过就等价于再设一次）
    try:
        cur.execute("ALTER USER \"" + user + "\" WITH PASSWORD \x27" + password + "\x27")
    except Exception as exc:
        if not ddl_error:
            ddl_error = str(exc)[:300]
    try:
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass

    c2 = open_conn(user, password)
    cur2 = c2.cursor()
    cur2.execute("SELECT USER FROM DUAL")
    row = cur2.fetchone()
    result["verified"] = bool(row)
    result["success"] = bool(row) and str(row[0]).upper() == user
    try:
        c2.close()
    except Exception:
        pass
    if not result["success"] and ddl_error:
        result["error"] = ddl_error
except Exception as exc:
    result["error"] = str(exc)[:400]

print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result.get("success") else 1)
'

PAYLOAD=$(printf '{"user":"%s","password":"%s","dsn":"%s:%s/%s"}' \
    "$DB_USER" "$PASSWORD" "$DB_HOST_TARGET" "$DB_PORT" "$DB_NAME")

set +e
OUT=$(printf '%s' "$PAYLOAD" | docker exec -i "$CONTAINER" python -c "$SCRIPT" 2>&1)
RC=$?
set -e

printf '%s\n' "$OUT"
[ $RC -eq 0 ] || exit $RC
