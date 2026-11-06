#!/bin/sh
# 列出本交付包配置的所有 ST-MCP 实例（主实例 + instances/ 下的每个实例）及其状态。
# 用法：sh scripts/list-instances.sh
set -u

PKG=$(cd "$(dirname "$0")/.." && pwd)
. "$PKG/scripts/common.sh"

# load_conf 的原则是"环境里已有的值优先"，所以每个实例都要在一个干净变量空间里读，
# 否则上一个实例的端口/库名会串到下一个。
KEYS="ST_HOST ST_PORT ST_USER ST_DB ST_HOST_GATEWAY TRANSPORT MCP_PATH SSE_HOST SSE_PORT ACCESS_MODE
IMAGE_NAME IMAGE_TAG CONTAINER_NAME HOST_PORT IMAGE_MODE INSTALL_SHENTONG ST_INSTALL_PREFIX"

show_one() {
    _label=$1
    _conf=$2
    _secret=$3
    (
        for k in $KEYS; do unset "$k"; done
        load_conf "$_conf"
        if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
            _st="运行中"
        elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
            _st="已停止"
        else
            _st="未部署"
        fi
        if [ -f "$_secret" ]; then
            if [ -n "$(sed -n 's/^ST_PASSWORD=//p' "$_secret" | tail -1)" ]; then
                _pw="口令已填"
            else
                _pw="口令未填"
            fi
        else
            _pw="缺口令文件"
        fi
        printf '%-10s %-18s %-7s %-24s %-8s %s\n' \
            "$_label" "$CONTAINER_NAME" "$_st" \
            "$ST_USER@$ST_HOST:$ST_PORT/$ST_DB" "$HOST_PORT" "$_pw"
    )
}

printf '%-10s %-18s %-7s %-24s %-8s %s\n' "实例" "容器名" "状态" "连接目标（库）" "MCP端口" "口令"
printf '%s\n' "------------------------------------------------------------------------------------------"
show_one "主实例" "$PKG/deploy.conf" "$PKG/secrets/st-mcp.env"

for f in "$PKG"/instances/*.conf; do
    [ -e "$f" ] || continue
    n=$(basename "$f" .conf)
    show_one "$n" "$f" "$PKG/secrets/$n.env"
done

printf '%s\n' "------------------------------------------------------------------------------------------"
printf '每个实例的验收：sh scripts/verify.sh <实例名>     部署：sh install-all.sh <实例名>\n'
printf '新增实例：sh scripts/new-instance.sh <实例名> <库IP> [库端口] [库名] [用户] [MCP端口]\n'
printf '停用实例：docker rm -f <容器名>（只停这一个，其它实例不受影响）\n'
