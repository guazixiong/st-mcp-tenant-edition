#!/bin/sh
# ST-MCP 离线部署验收（只读检查，不改动任何状态）。
# 用法：sh scripts/verify.sh [实例名]    退出码 0=全部通过（允许 WARN），1=存在 FAIL
#   不带实例名 = 验主实例（deploy.conf）；带实例名 = 验 instances/<名>.conf 那个实例
set -u

PKG=$(cd "$(dirname "$0")/.." && pwd)
. "$PKG/scripts/common.sh"
INSTANCE=${1:-}
instance_paths "$INSTANCE"
load_conf "$CONF_FILE"
SECRET=$SECRET_FILE
[ -f "$SECRET" ] && load_secret_file "$SECRET"
[ -n "$INSTANCE" ] && log "验收实例：$INSTANCE（$CONF_FILE）"

PASS=0
FAIL=0
SKIP=0
ok()   { printf '  [PASS] %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }
skip() { printf '  [SKIP] %s\n' "$*"; SKIP=$((SKIP+1)); }

log "===== 1/5 交付包结构 ====="
for f in deploy.conf docker/Dockerfile.offline docker/entrypoint.sh wheels/st_mcp-0.1.0-py3-none-any.whl; do
    if [ -f "$PKG/$f" ]; then ok "$f"; else bad "缺少 $f"; fi
done
if ls "$PKG"/wheels/mcp-*.whl >/dev/null 2>&1; then
    ok "mcp 轮子就位：$(ls "$PKG"/wheels/ | sed -n 's/^mcp-\(.*\)-py3-none-any.whl$/\1/p' | head -1)"
else
    bad "缺少 mcp 轮子"
fi
# streamable-http 由 mcp 内部的 FastMCP.run_streamable_http_async() 起 uvicorn，
# 少了这只轮子该传输会在容器启动时才报 ModuleNotFoundError。
if ls "$PKG"/wheels/uvicorn-*.whl >/dev/null 2>&1; then
    ok "uvicorn 轮子就位（streamable-http 需要）：$(ls "$PKG"/wheels/ | grep '^uvicorn' | head -1)"
else
    bad "缺少 uvicorn 轮子 —— TRANSPORT=streamable-http 无法启动"
fi
if ls "$PKG"/wheels/pydantic_core-*aarch64*.whl >/dev/null 2>&1; then
    ok "pydantic_core aarch64 轮子就位"
else
    bad "缺少 pydantic_core aarch64 轮子（cp311）"
fi
if ls "$PKG"/wheels/STPython-*.whl >/dev/null 2>&1; then
    ok "STPython 驱动轮子就位：$(ls "$PKG"/wheels/ | grep '^STPython' | head -1)"
else
    bad "缺少 STPython 轮子（wheels/STPython-*-cp311-cp311-linux_aarch64.whl）"
fi
if [ -f "$PKG/aci/libaci.so" ]; then
    ok "ACI 客户端库就位：aci/libaci.so（$(wc -c < "$PKG/aci/libaci.so") 字节）"
else
    bad "缺少 aci/libaci.so —— STPython 导入时会找不到共享库"
fi

log "===== 2/5 运行环境 ====="
if command -v docker >/dev/null 2>&1; then ok "docker 命令存在"; else bad "未安装 docker"; fi
if docker info >/dev/null 2>&1; then ok "docker 守护进程可用"; else bad "docker 守护进程不可用"; fi
case $(uname -m) in
    aarch64|arm64) ok "CPU 架构 $(uname -m)" ;;
    *) skip "架构为 $(uname -m)，非 arm64，包内轮子可能不匹配" ;;
esac

log "===== 3/5 镜像与驱动 ====="
IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    ok "镜像 $IMAGE_REF 已就绪（$(docker image inspect -f '{{.Size}}' "$IMAGE_REF") 字节）"
    if docker run --rm --entrypoint python "$IMAGE_REF" -c "import STPython" >/dev/null 2>&1; then
        ok "镜像内 STPython 可用"
        HAS_DRIVER=1
    else
        skip "镜像内无 STPython 驱动 —— 需要厂商提供 aarch64 轮子放入 wheels/ 后以 IMAGE_MODE=build 重建"
        HAS_DRIVER=0
    fi
    docker run --rm --entrypoint python "$IMAGE_REF" -c "import st_mcp,mcp,pydantic;print('IMPORT_OK')" 2>/dev/null \
        | grep -q IMPORT_OK \
        && ok "镜像内 st_mcp/mcp/pydantic 均可导入" \
        || bad "镜像内 Python 依赖导入失败"
else
    bad "本机没有镜像 $IMAGE_REF"
fi

log "===== 4/5 MCP 服务 ====="
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    ok "容器 $CONTAINER_NAME 正在运行"
    # 单端口多租户：容器必须挂着注册表，否则请求会全 401 或退回旧行为。
    REGFILE="$PKG/tenants/tenants.json"
    if docker exec "$CONTAINER_NAME" printenv ST_TENANT_REGISTRY >/dev/null 2>&1; then
        ok "容器已启用租户注册表（$(docker exec "$CONTAINER_NAME" printenv ST_TENANT_REGISTRY)）"
        TENANT_MODE=1
    else
        if [ -n "$INSTANCE" ]; then
            skip "实例 $INSTANCE 不启用租户注册表（多实例模式按单库账号运行）"
        else
            skip "容器未设置 ST_TENANT_REGISTRY（旧镜像或旧部署）"
        fi
        TENANT_MODE=0
    fi
    TOKEN=""
    if [ -f "$REGFILE" ] && command -v python3 >/dev/null 2>&1; then
        TOKEN=$(python3 - "$REGFILE" <<'PY' 2>/dev/null || true
import json, sys
doc = json.load(open(sys.argv[1]))
tenants = doc.get("tenants") or []
chosen = next((t for t in tenants if t.get("name") == "default"), tenants[0] if tenants else None)
print(chosen.get("token", "") if chosen else "")
PY
)
    fi
    TMPF=/tmp/stmcp-ep.$$
    URL="http://127.0.0.1:${HOST_PORT}$(mcp_endpoint)"
    [ "$TENANT_MODE" = 1 ] && [ -n "$TOKEN" ] && URL="$URL/$TOKEN"
    if [ "$TRANSPORT" = "streamable-http" ]; then
        # 发一次 initialize 握手而不是空 GET：streamable-http 只认 POST，GET 会回 405/404。
        # 成功响应本身是 SSE 流，curl 必然因超时退出（exit 28），所以只看 http_code，不看退出码。
        # Content-Type 缺失会被服务端判 400，Accept 缺失判 406，两个头都必须带上。
        CODE=$(curl -s -o "$TMPF" -w '%{http_code}' --max-time 8 -X POST "$URL" \
            -H 'Content-Type: application/json' \
            -H 'Accept: application/json, text/event-stream' \
            -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"st-mcp-verify","version":"1.0"}}}' 2>/dev/null)
        [ -n "$CODE" ] || CODE=000
        if [ "$CODE" = 200 ] && grep -q '"serverInfo"' "$TMPF" 2>/dev/null; then
            ok "streamable-http 握手可达 $URL (HTTP $CODE，已取回 serverInfo)"
        else
            bad "streamable-http 端点异常 $URL (HTTP $CODE)"
            printf '         ↳ 401 = token 不在注册表里或容器未挂载 $REGFILE；404/405 = 容器仍按 sse 启动：docker logs %s 看首行参数；改完重跑 sh install-all.sh%s\n' \
                "$CONTAINER_NAME" "${INSTANCE:+ $INSTANCE}"
        fi
        if [ "$TENANT_MODE" = 1 ]; then
            NOAUTH_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X POST "http://127.0.0.1:${HOST_PORT}$(mcp_endpoint)" \
                -H 'Content-Type: application/json' \
                -H 'Accept: application/json, text/event-stream' \
                -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"st-mcp-verify","version":"1.0"}}}' 2>/dev/null)
            [ -n "$NOAUTH_CODE" ] || NOAUTH_CODE=000
            if [ "$NOAUTH_CODE" = 401 ]; then
                ok "不带 token 的请求被拒绝（HTTP 401）"
            else
                bad "不带 token 的请求返回 HTTP $NOAUTH_CODE（应为 401）"
            fi
            HZ_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${HOST_PORT}/healthz" 2>/dev/null)
            [ -n "$HZ_CODE" ] || HZ_CODE=000
            [ "$HZ_CODE" = 200 ] && ok "/healthz 可达（HTTP 200）" || bad "/healthz 不可达（HTTP $HZ_CODE）"
        fi
    else
        # SSE 是长连接，curl 必然因超时退出（exit 28），所以只看 http_code，不看退出码。
        CODE=$(curl -s -o "$TMPF" -w '%{http_code}' --max-time 5 -N "$URL" 2>/dev/null)
        [ -n "$CODE" ] || CODE=000
        if [ "$CODE" = 200 ] || grep -qi 'text/event-stream' "$TMPF" 2>/dev/null; then
            ok "SSE 端点可达 $URL (HTTP $CODE)"
        else
            bad "SSE 端点不可达 $URL (HTTP $CODE)"
            printf '         ↳ 客户端报 405 而这里 200 = 客户端用的是 streamable-http，把 deploy.conf 的 TRANSPORT 改成它\n'
        fi
    fi
    rm -f "$TMPF"
    if docker logs --tail 40 "$CONTAINER_NAME" 2>&1 | grep -q 'Traceback'; then
        bad "容器日志存在 Traceback"
    else
        ok "容器日志无异常堆栈"
    fi
else
    skip "容器 $CONTAINER_NAME 未运行（stdio 模式或尚未启动），跳过端点检查"
fi

log "===== 5/5 数据库连通（真实握手）====="
if [ ! -f "$SECRET" ]; then
    skip "无 $SECRET，未做数据库连通测试"
elif [ "${HAS_DRIVER:-0}" != 1 ]; then
    # 缺驱动时连通失败是已知且预期的，不算部署缺陷：MCP 服务侧已由 1–4 组判定通过。
    skip "镜像内无 STPython 驱动，数据库连通留待驱动到位后验证"
    printf '         ↳ 驱动轮子放进 wheels/ 后：sh install-all.sh，再跑本脚本，本行应变为 [PASS]\n'
else
    # 必须走镜像默认入口：ST_HOST=127.0.0.1 只有在 entrypoint.sh 里才会被重映射成
    # 宿主机网关（172.17.0.1）。用 --entrypoint st-mcp 直起会绕过那段逻辑，
    # 让容器去连它自己的 127.0.0.1，必然 CONNECT_FAILED。
    OUT=$(docker run --rm --env-file "$SECRET" \
        -e ST_HOST="${ST_HOST}" -e ST_PORT="${ST_PORT}" -e ST_USER="${ST_USER}" -e ST_DB="${ST_DB}" \
        -e ST_HOST_GATEWAY="${ST_HOST_GATEWAY:-172.17.0.1}" \
        "${IMAGE_REF}" --test-connection --transport stdio 2>&1 || true)
    case "$OUT" in
        *CONNECT_OK*) ok "$(printf '%s' "$OUT" | grep -o 'CONNECT_OK.*' | head -1)" ;;
        *CONNECT_FAILED*)
            bad "$(printf '%s' "$OUT" | grep -o 'CONNECT_FAILED.*' | head -1)"
            printf '         ↳ 排查：神通服务是否启动、端口是否放通、口令是否被改写\n'
            ;;
        *) bad "连通性测试无明确结果：$(printf '%s' "$OUT" | tail -1)" ;;
    esac
fi

printf '\n===== 验收汇总：%d PASS / %d FAIL / %d SKIP =====\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
