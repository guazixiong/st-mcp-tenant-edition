#!/bin/sh
# 生成一个 ST-MCP 实例的配置与口令文件（一台机器同时连多个神通库时用）。
# 用法：sh scripts/new-instance.sh <实例名> <库地址> [库端口] [库名] [用户] [MCP端口]
#         库地址：神通数据库服务器的 IP（本机就填 127.0.0.1，同网段另一台机器就填它的 IP）
#         默认值：库端口 2003、库名 OSRDB、用户 SYSDBA、MCP 端口自动挑空闲端口
# 生成后只需填口令，再 sh install-all.sh <实例名> 即可。全程不联网、不改主实例。
# 想要老客户端用的 sse（端点 /sse），前面加一个环境变量即可：
#   TRANSPORT=sse sh scripts/new-instance.sh legacy 10.20.30.41
set -eu

PKG=$(cd "$(dirname "$0")/.." && pwd)
. "$PKG/scripts/common.sh"

[ $# -ge 2 ] || die "用法：sh scripts/new-instance.sh <实例名> <库地址> [库端口] [库名] [用户] [MCP端口]
     例：sh scripts/new-instance.sh erp 10.20.30.41            # 用默认端口/库名，MCP 端口自动选
         sh scripts/new-instance.sh core 10.20.30.42 2003 CORE DBA 8102"

NAME=$1
HOST=$2
PORT=${3:-2003}
DB=${4:-OSRDB}
USER=${5:-SYSDBA}
MCP=${6:-}

# 实例用什么传输由环境变量决定（默认与主实例一致，streamable-http）：
#   TRANSPORT=sse sh scripts/new-instance.sh legacy 10.20.30.41
T=${TRANSPORT:-streamable-http}
case "$T" in
    sse|streamable-http) : ;;
    *) die "TRANSPORT 只支持 sse / streamable-http（收到：'$T'）" ;;
esac

case "$NAME" in
    *[!A-Za-z0-9_-]*) die "实例名只能用字母、数字、下划线和连字符（收到：'$NAME'）" ;;
esac
case "$NAME" in
    st-mcp|default|main) die "实例名 '$NAME' 与主实例冲突，换一个（如 erp、core）" ;;
esac
[ -f "$PKG/instances/$NAME.conf" ] && die "实例 $NAME 已存在：$PKG/instances/$NAME.conf（要改就直接编辑它，再 sh install-all.sh $NAME）"
case "$PORT" in ''|*[!0-9]*) die "库端口必须是数字（收到：'$PORT'）" ;; esac
case "$DB" in ''|*[!A-Za-z0-9_-]*) die "库名只能用字母、数字、下划线和连字符（收到：'$DB'）" ;; esac
case "$USER" in ''|*[!A-Za-z0-9_]*) die "用户名必须是字母数字下划线（收到：'$USER'）" ;; esac
need_docker

if [ -z "$MCP" ]; then
    MCP=$(pick_free_port) || die "8100-8199 没有空闲端口，请显式指定 MCP 端口"
    log "MCP 端口自动选 $MCP"
else
    case "$MCP" in ''|*[!0-9]*) die "MCP 端口必须是数字（收到：'$MCP'）" ;; esac
    OWNER=$(port_owner "$MCP")
    [ -z "$OWNER" ] || die "MCP 端口 $MCP 已被容器 $OWNER 占用，请换一个"
fi

mkdir -p "$PKG/instances"

cat > "$PKG/instances/$NAME.conf" <<EOF
# ST-MCP 实例「$NAME」的连接参数（本文件不得包含口令；口令在 secrets/$NAME.env）
# 由 new-instance.sh 生成；改完重新执行：sh install-all.sh $NAME

# ---- 连哪个神通数据库 ----
ST_HOST=$HOST
ST_PORT=$PORT
ST_USER=$USER
ST_DB=$DB
# 仅当 ST_HOST 是 127.0.0.1/localhost（库与容器在同一台机器）时才会用到：
# 容器内的 127.0.0.1 是容器自己，入口脚本会把本机地址换成这个宿主机网关。
ST_HOST_GATEWAY=172.17.0.1

# ---- MCP 服务 ----
# TRANSPORT：sse（长连接，端点 /sse）或 streamable-http（单端点，新客户端用的就是这个）
# MCP_PATH ：streamable-http 的端点路径，sse 模式下忽略
TRANSPORT=$T
MCP_PATH=/mcp
SSE_HOST=0.0.0.0
SSE_PORT=$MCP
ACCESS_MODE=unrestricted

# ---- 容器 ----
IMAGE_NAME=st-mcp
IMAGE_TAG=0.1.0
CONTAINER_NAME=st-mcp-$NAME
HOST_PORT=$MCP

# ---- 安装行为 ----
# 实例只负责连库，不在本机装数据库；要连的库必须已经运行且网络可达。
INSTALL_SHENTONG=no
# build：镜像已存在时会直接复用（各实例共用同一个镜像，不重复构建）
IMAGE_MODE=build
EOF

if [ -f "$PKG/secrets/$NAME.env" ]; then
    log "口令文件已存在，保留不动：secrets/$NAME.env"
else
    mkdir -p "$PKG/secrets"
    cat > "$PKG/secrets/$NAME.env" <<EOF
# 实例「$NAME」的数据库口令。下面这一行等号后原样写密码，支持特殊符号，
# 不要加引号、不要转义、不要留空格（docker --env-file 原样读取，本包不会二次解析）。
ST_PASSWORD=
EOF
    chmod 600 "$PKG/secrets/$NAME.env"
fi

log "实例已生成"
printf '\n'
printf '下一步（两条命令）：\n'
printf '  1) vi secrets/%s.env      # 填 ST_PASSWORD=\n' "$NAME"
printf '  2) sh install-all.sh %s   # 部署并验收（19 PASS 即连通）\n' "$NAME"
printf '\n'
if [ "$T" = streamable-http ]; then
    printf '客户端连这个实例用：http://<本机IP>:%s/mcp\n' "$MCP"
else
    printf '客户端连这个实例用：http://<本机IP>:%s/sse\n' "$MCP"
fi
printf '连的库：%s@%s:%s/%s（本机不装库，要求该地址的 %s 端口网络可达）\n' "$USER" "$HOST" "$PORT" "$DB" "$PORT"
printf '要换传输方式：编辑 instances/%s.conf 里的 TRANSPORT（sse 或 streamable-http），再 sh install-all.sh %s\n' "$NAME" "$NAME"
printf '查看所有实例：sh scripts/list-instances.sh\n'
