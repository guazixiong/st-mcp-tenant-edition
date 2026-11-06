#!/bin/sh
# 离线部署脚本公共函数（POSIX sh，被 install-all.sh / verify.sh 等 source）。

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# 只读 deploy.conf 的 KEY=VALUE，跳过注释与空行；不 source，避免值里的特殊字符被 shell 解析。
load_conf() {
    _conf=$1
    [ -f "$_conf" ] || die "缺少配置文件 $_conf"
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            ''|'#'*) continue ;;
        esac
        case "$_line" in
            *=*) ;;
            *) continue ;;
        esac
        _k=${_line%%=*}
        _v=${_line#*=}
        _k=$(printf '%s' "$_k" | tr -d ' \t')
        _v=$(printf '%s' "$_v" | sed 's/[ \t]*$//')
        case "$_k" in
            ''|*[!A-Za-z0-9_]*) continue ;;
        esac
        # 已存在的环境变量优先于配置文件，便于临时覆盖：ST_PORT=2004 sh install-all.sh
        if [ "$(get_env "$_k")" = "" ]; then
            set_env "$_k" "$_v"
        fi
    done < "$_conf"
}

# 用 env 传递而非赋值：busybox/dash 下赋值后子进程可见性一致，但变量名可能含保留字。
# 统一走一个小的间接层，方便后续替换。
set_env() { export "$1=$2"; }
get_env() { eval "printf '%s' \"\${$1:-}\""; }

# 口令文件：KEY=VALUE，整行原样取值（不做引号剥离、不做 # 截断），权限必须 600。
load_secret_file() {
    _f=$1
    [ -f "$_f" ] || return 1
    _perm=$(stat -c %a "$_f" 2>/dev/null || echo "")
    case "$_perm" in
        600|400|'') : ;;
        *) warn "$_f 权限为 ${_perm:-未知}（应为 600），请 chmod 600" ;;
    esac
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            ''|'#'*) continue ;;
            *=*) ;;
            *) continue ;;
        esac
        _k=${_line%%=*}
        _v=${_line#*=}
        case "$_k" in
            ''|*[!A-Za-z0-9_]*) continue ;;
        esac
        export "$_k"="$_v"
    done < "$_f"
    return 0
}

need_docker() {
    command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker（离线包不含 Docker 本体）"
    docker info >/dev/null 2>&1 || die "docker 守护进程不可用，请检查 systemctl status docker 或当前用户权限"
}

have_image() {
    docker image inspect "$1" >/dev/null 2>&1
}

# 解 gzip 的 docker-archive；同时兼容 .tar.gz 与已解压的 .tar
load_image_archive() {
    _a=$1
    [ -f "$_a" ] || die "找不到镜像文件 $_a"
    case "$_a" in
        *.tar.gz|*.tgz) gunzip -c "$_a" | docker load ;;
        *.tar)          docker load -i "$_a" ;;
        *)              die "不识别的镜像归档格式：$_a" ;;
    esac
}

# ---- 多实例支持 ----
# 一台机器上可以同时跑多个 ST-MCP 容器，各连一个神通库。实例名为空 = 主实例
# （deploy.conf + secrets/st-mcp.env），非空 = instances/<名>.conf + secrets/<名>.env。
# 依赖调用方已设置 PKG（交付包根目录）。
instance_paths() {
    _inst=${1:-}
    if [ -n "$_inst" ]; then
        case "$_inst" in
            *[!A-Za-z0-9_-]*)
                die "实例名只能用字母、数字、下划线和连字符（收到：'$_inst'）"
                ;;
        esac
        CONF_FILE=$PKG/instances/$_inst.conf
        SECRET_FILE=$PKG/secrets/$_inst.env
        [ -f "$CONF_FILE" ] || die "缺少实例配置 $CONF_FILE
         生成它：sh scripts/new-instance.sh $_inst <库地址> [库端口] [库名] [用户] [MCP端口]"
    else
        CONF_FILE=$PKG/deploy.conf
        SECRET_FILE=$PKG/secrets/st-mcp.env
    fi
}

# 宿主机端口现在被哪个容器占着（没有则空输出）。只看 docker，不抓裸进程。
port_owner() {
    _p=$1
    docker ps --format '{{.Names}}|{{.Ports}}' 2>/dev/null | while IFS='|' read -r _n _ports; do
        case "$_ports" in
            *"0.0.0.0:$_p->"*|*"[::]:$_p->"*|*"($_p->"*) printf '%s\n' "$_n" ;;
        esac
    done | head -1
}

# 从 8100 起往后找一个既没被容器映射、也没被本机监听的端口。
pick_free_port() {
    _p=8100
    while [ "$_p" -le 8199 ]; do
        if [ -n "$(port_owner "$_p")" ]; then
            _p=$((_p + 1)); continue
        fi
        if command -v ss >/dev/null 2>&1; then
            if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$_p\$"; then
                _p=$((_p + 1)); continue
            fi
        elif command -v netstat >/dev/null 2>&1; then
            if netstat -ltn 2>/dev/null | grep -qE "[:.]$_p\$"; then
                _p=$((_p + 1)); continue
            fi
        fi
        printf '%s\n' "$_p"
        return 0
    done
    return 1
}

# MCP 客户端要访问的路径。sse 固定是 /sse；streamable-http 用 MCP_PATH（漏写回到 /mcp，
# 缺前导斜杠补上），install-all.sh 与 verify.sh 必须算出同一个值，否则验收判的是另一个端点。
mcp_endpoint() {
    case "$TRANSPORT" in
        streamable-http)
            _p=${MCP_PATH:-/mcp}
            case "$_p" in
                /*) : ;;
                *) _p=/$_p ;;
            esac
            printf '%s\n' "$_p"
            ;;
        *)
            printf '/sse\n'
            ;;
    esac
}
