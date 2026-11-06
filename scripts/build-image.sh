#!/bin/sh
# 在任意有 Docker 的 arm64 机器上离线构建 ST-MCP 镜像，并导出为可 U 盘拷贝的 docker-archive。
# 用法：sh scripts/build-image.sh [--save]
#   默认只构建；带 --save 时额外 docker save 到 images/02-st-mcp-<tag>-arm64.tar.gz
set -eu

PKG=$(cd "$(dirname "$0")/.." && pwd)
. "$PKG/scripts/common.sh"
load_conf "$PKG/deploy.conf"

need_docker
[ "$(uname -m)" = "aarch64" ] || die "构建机必须是 aarch64（轮子是 arm64 专用的）"

STPY=$(ls "$PKG"/wheels/STPython-*.whl 2>/dev/null | head -1 || true)
if [ -n "$STPY" ]; then
    log "将打入驱动：$(basename "$STPY")"
    case "$(basename "$STPY")" in
        *cp311*) : ;;
        *) warn "STPython 轮子不是 cp311，而基础镜像固定 python:3.11-slim —— 装上也无法导入" ;;
    esac
else
    warn "wheels/ 内没有 STPython 轮子，构建出的镜像不含驱动（仅可用于流程验证）"
fi

if [ -f "$PKG/aci/libaci.so" ]; then
    log "将打入 ACI：aci/libaci.so（$(wc -c < "$PKG/aci/libaci.so") 字节）"
else
    warn "aci/libaci.so 缺失：镜像里 STPython 会因找不到 libaci.so 而无法导入"
fi

# 基础镜像必须是 3.11：神通 STPython 只发布到 cp311。
BASE_IMAGE="python:3.11-slim"
if ! have_image "$BASE_IMAGE"; then
    log "载入基础镜像"
    load_image_archive "$PKG/images/01-python-3.11-slim.tar.gz"
fi

IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
log "离线构建 $IMAGE_REF"
docker build --network=none -f "$PKG/docker/Dockerfile.offline" -t "$IMAGE_REF" "$PKG"

docker run --rm --entrypoint python "$IMAGE_REF" -c "import st_mcp,mcp,pydantic;print('IMPORT_OK')" | grep -q IMPORT_OK \
    || die "镜像内依赖导入失败"
if [ -n "$STPY" ]; then
    # 只 import 就足以暴露 libaci.so 缺失或 ACI/STPython 版本不匹配。
    docker run --rm --entrypoint python "$IMAGE_REF" -c "import STPython;print('ACI_OK')" | grep -q ACI_OK \
        || die "镜像内 STPython 导入失败（检查 aci/libaci.so 是否到位、版本是否与数据库一致）"
    log "驱动自检通过：STPython + libaci 可导入"
fi
log "镜像自检通过：$IMAGE_REF"

if [ "${1:-}" = "--save" ]; then
    OUT="$PKG/images/02-${IMAGE_NAME}-${IMAGE_TAG}-arm64.tar.gz"
    log "导出镜像 -> $OUT"
    docker save "$IMAGE_REF" | gzip -1 > "$OUT"
    ls -l "$OUT"
fi
