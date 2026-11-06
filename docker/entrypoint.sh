#!/bin/sh
# ST-MCP 容器入口（POSIX sh，不用 bashism）。
# 密码只通过环境变量传入，不落 argv，避免在 /proc/<pid>/cmdline 与 docker inspect 里泄露。
set -eu

ST_HOST="${ST_HOST:-127.0.0.1}"
ST_PORT="${ST_PORT:-2003}"
ST_USER="${ST_USER:-SYSDBA}"
ST_DB="${ST_DB:-OSRDB}"
# MCP_TRANSPORT 是对外文档用的名字，TRANSPORT 是 deploy.conf 里的键名（load_conf 直接导出成环境变量）。
TRANSPORT="${MCP_TRANSPORT:-${TRANSPORT:-streamable-http}}"
MCP_PATH="${MCP_PATH:-/mcp}"
SSE_HOST="${SSE_HOST:-0.0.0.0}"
SSE_PORT="${SSE_PORT:-8000}"
ACCESS_MODE="${ACCESS_MODE:-unrestricted}"
ST_HOST_GATEWAY="${ST_HOST_GATEWAY:-172.17.0.1}"

# 容器里的 127.0.0.1 是容器自己；连宿主机上的神通时自动改用网关地址。
case "$ST_HOST" in
    localhost|127.0.0.1|::1)
        echo "ST-MCP: ST_HOST=$ST_HOST 重映射为宿主机网关 $ST_HOST_GATEWAY" >&2
        ST_HOST="$ST_HOST_GATEWAY"
        ;;
esac

export ST_HOST ST_PORT ST_USER ST_DB

if [ "${ST_PASSWORD:-}" = "" ]; then
    echo "ST-MCP: 未设置 ST_PASSWORD（请通过 docker run -e ST_PASSWORD=... 或 --env-file 注入）" >&2
fi

# 显式参数优先于环境变量，因此只在没有给参数时才补默认值。
case "$*" in
    *--transport*) : ;;
    *) set -- "$@" "--transport" "$TRANSPORT" ;;
esac
case "$*" in
    *--sse-host*) : ;;
    *) set -- "$@" "--sse-host" "$SSE_HOST" ;;
esac
case "$*" in
    *--sse-port*) : ;;
    *) set -- "$@" "--sse-port" "$SSE_PORT" ;;
esac
case "$*" in
    *--access-mode*) : ;;
    *) set -- "$@" "--access-mode" "$ACCESS_MODE" ;;
esac
# st-mcp 只在 streamable-http 下接受 --mcp-path，其它传输传了这个参数会直接报错。
if [ "$TRANSPORT" = "streamable-http" ]; then
    case "$*" in
        *--mcp-path*) : ;;
        *) set -- "$@" "--mcp-path" "$MCP_PATH" ;;
    esac
fi

echo "ST-MCP: 启动 st-mcp $*（连接目标 $ST_USER@$ST_HOST:$ST_PORT/$ST_DB，密码已隐藏）" >&2
exec st-mcp "$@"
