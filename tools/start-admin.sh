#!/bin/sh
# 启动租户管理界面。参数：端口（默认 8001）。
# 用法：sh tools/start-admin.sh [端口]
# 想改监听地址/允许远程：直接用 python st-mcp-admin.py --help 里的参数。
set -eu

SELF=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SELF/.." && pwd)
PORT=${1:-8001}

# 挑一个能真正执行的 Python：Git Bash 里的 python3 可能是 Microsoft Store 空壳
PY=
for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then
        PY=$c
        break
    fi
done
[ -n "$PY" ] || { echo "找不到可用的 Python（试过 python3 / python / py）"; exit 1; }

echo "使用 $PY（$("$PY" -c 'import sys;print(sys.version.split()[0])')）"

# 远程访问要登录：首次启动生成 secrets/admin.env（600）。本机访问不受影响。
AUTH="$ROOT/secrets/admin.env"
if [ ! -f "$AUTH" ]; then
    mkdir -p "$ROOT/secrets"
    umask 077
    PASS=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(9))')
    printf 'ADMIN_USER=admin\nADMIN_PASSWORD=%s\n' "$PASS" > "$AUTH"
    chmod 600 "$AUTH"
    echo "已生成管理界面登录口令：$AUTH（用户 admin，口令 $PASS）"
fi

exec "$PY" "$ROOT/st-mcp-admin.py" --port "$PORT" --auth-file "$AUTH"
