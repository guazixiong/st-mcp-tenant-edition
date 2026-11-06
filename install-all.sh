#!/bin/sh
# ST-MCP 离线一键部署（aarch64 / 麒麟 V10 / 已有 Docker）。
# 全程不联网：镜像来自 images/，依赖轮子来自 wheels/。
# 用法：sh install-all.sh [实例名]    （幂等，可重复执行）
#   不带实例名 = 主实例（deploy.conf + secrets/st-mcp.env）
#   带实例名   = 该实例（instances/<名>.conf + secrets/<名>.env），用于一台机器连多个神通库
set -eu

PKG=$(cd "$(dirname "$0")" && pwd)
. "$PKG/scripts/common.sh"

log "交付包根目录：$PKG"
INSTANCE=${1:-}
instance_paths "$INSTANCE"
if [ -n "$INSTANCE" ]; then
    log "实例 $INSTANCE：配置 $CONF_FILE，口令 $SECRET_FILE"
fi
load_conf "$CONF_FILE"

# ---- 1. 前置检查 ----
need_docker
ARCH=$(uname -m)
[ "$ARCH" = "aarch64" ] || warn "当前架构 $ARCH 非 aarch64，包内轮子与镜像均为 arm64，可能无法运行"

STPY=$(ls "$PKG"/wheels/STPython-*.whl 2>/dev/null | head -1 || true)
[ -n "$STPY" ] || warn "wheels/ 中没有 STPython（神通 Python 驱动）轮子 —— MCP 将无法真正连库"

# ---- 2. 口令文件 ----
SECRET=$SECRET_FILE
[ -f "$SECRET" ] || die "缺少口令文件 $SECRET，请先按 README 生成（docker 以 --env-file 原样读取，特殊字符不会被二次解析）"
load_secret_file "$SECRET" || die "读取口令文件失败：$SECRET"
if [ "$(get_env ST_PASSWORD)" = "" ]; then
    die "口令文件里必须包含 ST_PASSWORD=<密码>"
fi
if [ -n "$INSTANCE" ]; then
    log "实例 $INSTANCE 连接目标：$ST_USER@$ST_HOST:$ST_PORT/$ST_DB（密码已从 $SECRET 读取，不回显）"
else
    log "主实例连接目标：$ST_USER@$ST_HOST:$ST_PORT/$ST_DB（密码已从 $SECRET 读取，不回显）"
fi

# ---- 2.5 租户注册表（单端口多租户）----
# 所有租户共用一个进程/端口；注册表里每条记录是 token → 一个神通库账号。
# 容器只读挂载这个目录，服务端按文件 mtime 热加载，加租户不用重启。
TENANT_DIR=$PKG/tenants
REG_FILE=$TENANT_DIR/tenants.json
mkdir -p "$TENANT_DIR"
chmod 755 "$TENANT_DIR" 2>/dev/null || true
REGTOOL=$PKG/scripts/tenant-registry.py
if [ -n "$INSTANCE" ]; then
    log "多实例容器不启用租户注册表：实例 $INSTANCE 保持独立单库账号模式（可用 sse）"
elif command -v python3 >/dev/null 2>&1 && [ -f "$REGTOOL" ]; then
    MAIN_TOKEN=""
    if [ -f "$REG_FILE" ]; then
        MAIN_TOKEN=$(python3 "$REGTOOL" get --file "$REG_FILE" --name default --field token 2>/dev/null || true)
    fi
    [ -n "$MAIN_TOKEN" ] || MAIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    if printf '%s\n' "$(get_env ST_PASSWORD)" | python3 "$REGTOOL" add --file "$REG_FILE" \
        --name default --token "$MAIN_TOKEN" --user "$ST_USER" --password-stdin \
        --host "$ST_HOST" --port "$ST_PORT" --database "$ST_DB" --access-mode "$ACCESS_MODE" \
        --note "主账号：deploy.conf 里的 ST_USER，install-all.sh 维护" >/dev/null 2>&1; then
        log "租户注册表就位：$REG_FILE（主账号 default → $ST_USER）"
        log "主账号 MCP 地址：http://<本机IP>:$HOST_PORT$(mcp_endpoint)/$MAIN_TOKEN"
    else
        warn "租户注册表写入失败：$REG_FILE（服务会以 401 拒绝所有请求，请检查目录权限）"
    fi
else
    warn "本机没有 python3 或缺少 $REGTOOL，跳过租户注册表初始化（单端口多租户无法启用）"
fi

# ---- 3. 校验整包 ----
# deploy.conf / secrets/ 下的文件是按现场改过的，摘要必然与打包时不同；
# 把它们和"真的坏文件"区分开，否则运维改完参数就再也装不上。
if [ -f "$PKG/SHA256SUMS.txt" ] && [ "${SKIP_CHECKSUM:-0}" != "1" ]; then
    log "校验交付包完整性"
    SUMRC=0
    SUMOUT=$( cd "$PKG" && LC_ALL=C sha256sum -c SHA256SUMS.txt 2>&1 ) || SUMRC=$?
    if [ "$SUMRC" != 0 ]; then
        # "FAILED open or read" 是文件缺失或读不动（U 盘拷贝不完整最常见的一种），
        # 必须和摘要不一致同等对待，否则 697 MB 的安装介质少拷一半也会被放行。
        BAD=$( printf '%s\n' "$SUMOUT" | sed -n '/: FAILED/{s/^[* ]\{0,1\}\([^:]*\): FAILED.*$/\1/p}' )
        REAL=""
        for f in $BAD; do
            case "$f" in
                deploy.conf|instances/*|secrets/*) log "  现场修改过：$f（属预期）" ;;
                *) REAL="$REAL $f" ;;
            esac
        done
        if [ -n "$( printf '%s' "$REAL" )" ]; then
            printf '%s\n' "$SUMOUT" | grep ': FAILED' >&2
            die "以下文件与打包时不一致，U 盘拷贝可能不完整：$REAL"
        fi
        warn "整包校验仅存在现场配置类差异，继续部署"
    else
        log "  全部文件摘要一致"
    fi
else
    warn "跳过 SHA256 校验"
fi

# ---- 4. 神通数据库（默认 yes：vendor/ 有介质就装库建库；库已存在则只补齐初始化与驱动） ----
MEDIA=$(ls "$PKG"/vendor/*.rar "$PKG"/vendor/*.tar.gz "$PKG"/vendor/*.tgz "$PKG"/vendor/*.zip 2>/dev/null || true)
SHENTONG_SCRIPT=$PKG/shentong/install-shentong.sh
PREFIX=${ST_INSTALL_PREFIX:-/opt/ShenTong}
# 目标机上已经有 bin/oscar 时不需要介质：install-shentong.sh 会跳过 setup，
# 只补建库/初始化/服务与驱动收割（现场最常见的"装了但库没起来"就是靠这个修好）。
has_shentong() { [ -x "$PREFIX/bin/oscar" ]; }
run_shentong() {
    [ -f "$SHENTONG_SCRIPT" ] || die "缺少 shentong/install-shentong.sh"
    sh "$SHENTONG_SCRIPT" "$PKG"
}
case "${INSTALL_SHENTONG:-auto}" in
    no|NO|0)
        log "按配置跳过神通数据库安装（假定库已在目标机运行）"
        ;;
    yes|YES|1)
        if has_shentong; then
            log "检测到已安装的神通（$PREFIX/bin/oscar），无需介质，继续补齐初始化与驱动"
            run_shentong
        else
            [ -n "$MEDIA" ] || die "INSTALL_SHENTONG=yes 但 vendor/ 内没有神通安装介质（且 $PREFIX 下没有已装好的 oscar）"
            log "检测到 vendor/ 下的神通安装介质，开始安装"
            run_shentong
        fi
        ;;
    *)
        if has_shentong; then
            log "检测到已安装的神通（$PREFIX/bin/oscar），补齐初始化与驱动"
            run_shentong
        elif [ -n "$MEDIA" ] && [ -f "$SHENTONG_SCRIPT" ]; then
            log "检测到 vendor/ 下的神通安装介质，开始安装"
            run_shentong
        else
            warn "vendor/ 内没有神通数据库安装介质，跳过装库（库需已在目标机运行，否则连不上）"
        fi
        ;;
esac

# ---- 5. 准备镜像 ----
IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
MODE=${IMAGE_MODE:-build}

if [ "$MODE" = auto ]; then
    if [ -n "$STPY" ]; then
        MODE=build
    else
        MODE=load
    fi
fi

if [ "$MODE" = build ]; then
    sh "$PKG/scripts/build-image.sh"
else
    PREBUILT=$(ls "$PKG"/images/02-st-mcp-*.tar.gz 2>/dev/null | head -1 || true)
    [ -n "$PREBUILT" ] || die "IMAGE_MODE=load 但 images/ 下没有预构建的 st-mcp 镜像"
    if ! have_image "$IMAGE_REF"; then
        log "载入预构建镜像 $(basename "$PREBUILT")"
        load_image_archive "$PREBUILT"
    fi
    if [ -n "$STPY" ]; then
        warn "wheels/ 里存在 STPython 轮子，但当前为 load 模式，镜像内未包含驱动；请改用 IMAGE_MODE=build 重新构建"
    fi
fi

# ---- 6. 运行容器 ----
DOCKER_ARGS=""
RUN_ENV=/tmp/st-mcp-run.$$.env
rm -f "$RUN_ENV"
cat > "$RUN_ENV" <<EOF
ST_HOST=$ST_HOST
ST_PORT=$ST_PORT
ST_USER=$ST_USER
ST_DB=$ST_DB
ST_HOST_GATEWAY=$ST_HOST_GATEWAY
ST_PASSWORD=$(get_env ST_PASSWORD)
EOF
if [ -n "${ST_PASSWORD_URL_ENCODED:-}" ]; then
    echo "ST_URI_URL_ENCODED=$ST_PASSWORD_URL_ENCODED" >> "$RUN_ENV"
fi
chmod 600 "$RUN_ENV"

if [ "$TRANSPORT" = stdio ]; then
    DOCKER_ARGS="-i --rm"
    log "stdio 模式：由 MCP 客户端通过 docker run -i 直接拉起，本脚本不启动常驻容器"
    log "客户端配置示例见 README"
    rm -f "$RUN_ENV"
    exit 0
fi

# 端点路径由传输方式决定，验收与提示都用它，别让运维去猜。
case "$TRANSPORT" in
    sse|streamable-http) : ;;
    *) die "TRANSPORT 只支持 sse / streamable-http / stdio（$CONF_FILE 里写的是：$TRANSPORT）" ;;
esac
ENDPOINT=$(mcp_endpoint)

# 多实例并存时最常见的错是把两个实例配到同一个宿主机端口；这里提前拦住，
# 免得 docker run 抛一句 "address already in use" 让人猜是谁占的。
OWNER=$(port_owner "$HOST_PORT")
if [ -n "$OWNER" ] && [ "$OWNER" != "$CONTAINER_NAME" ]; then
    die "宿主机端口 $HOST_PORT 已被容器 $OWNER 占用；请把本实例的 HOST_PORT（或 $CONF_FILE 里的）改成空闲端口，再重跑"
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    log "移除已存在的容器 $CONTAINER_NAME"
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

log "启动容器 $CONTAINER_NAME（宿主机端口 $HOST_PORT -> 容器 $SSE_PORT，传输 $TRANSPORT，端点 $ENDPOINT）"
# 只有主实例挂租户注册表；多实例容器保持各自的单库账号模式（见 02-运维.md 第 4 节）。
# 路径按现场习惯不含空格，这里沿用上面 $DOCKER_ARGS 的逐词展开方式。
if [ -z "$INSTANCE" ]; then
    DOCKER_ARGS="$DOCKER_ARGS -e ST_TENANT_REGISTRY=/data/tenants.json -v $TENANT_DIR:/data:ro"
fi
# shellcheck disable=SC2086
docker run -d --name "$CONTAINER_NAME" \
    --restart=always \
    --env-file "$RUN_ENV" \
    -e TRANSPORT="$TRANSPORT" \
    -e MCP_PATH="${MCP_PATH:-/mcp}" \
    -e SSE_HOST=0.0.0.0 \
    -e SSE_PORT="$SSE_PORT" \
    -e ACCESS_MODE="$ACCESS_MODE" \
    -p "${HOST_PORT}:${SSE_PORT}" \
    $DOCKER_ARGS \
    "$IMAGE_REF" >/dev/null

rm -f "$RUN_ENV"

# ---- 7. 验收 ----
if sh "$PKG/scripts/verify.sh" "$INSTANCE"; then
    log "部署完成：实例 ${INSTANCE:-主} 的 MCP 端点 http://127.0.0.1:$HOST_PORT$ENDPOINT"
    if [ -n "${MAIN_TOKEN:-}" ]; then
        log "主账号（default）客户端地址：http://<本机IP>:$HOST_PORT$ENDPOINT/$MAIN_TOKEN"
    fi
    log "连的库：$ST_USER@$ST_HOST:$ST_PORT/$ST_DB"
    log "加租户：sh tools/create-tenant.sh -t <租户名>（在交付夹里执行，一条命令建账号+发 token）"
    if [ -z "$STPY" ]; then
        warn "包内没有 STPython 轮子，镜像里就没有驱动：MCP 服务与 10 个工具正常，执行 SQL 会报驱动缺失"
        warn "拿到轮子后：cp STPython-*.whl wheels/ && sh install-all.sh（会自动重建镜像）"
    fi
else
    die "部署完成但验收未通过，请查看上方 FAIL 项与 docker logs $CONTAINER_NAME"
fi
