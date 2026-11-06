#!/bin/sh
# 在 aarch64 / 麒麟 V10 上静默安装神通数据库（OSCAR V7）。
# 安装介质需由厂商提供，放到交付包的 vendor/ 目录，支持三种形态：
#   vendor/ShenTong7.0_linux64.rar      （厂商官网常见形态，需系统有 unrar/7z）
#   vendor/<archive>.tar.gz|.tgz|.zip
#   vendor/<已解压目录>/                 （内含 setup 可执行文件）
# 本脚本不做任何联网操作。
set -eu

PKG=${1:-$(cd "$(dirname "$0")/.." && pwd)}
. "$PKG/scripts/common.sh"

INSTALL_PREFIX=${ST_INSTALL_PREFIX:-/opt/ShenTong}
DB_NAME=${ST_DB_NAME:-OSRDB}
DB_PORT=${ST_PORT:-2003}
DB_ENCODING=${ST_ENCODING:-utf8}

[ "$(id -u)" = 0 ] || die "安装神通数据库需要 root 权限"

ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    warn "当前架构为 $ARCH；vendor/ 里的介质必须是 aarch64 构建，否则 setup 会失败或装上 x86 二进制"
fi

# 幂等：已经装好（有 oscar 可执行文件）时不再跑 setup，直接把后面几步补齐。
# 现场最常见的失败形态是"软件装上但库没初始化"，只重跑本脚本即可修复。
SKIP_SETUP=0
if [ -x "$INSTALL_PREFIX/bin/oscar" ]; then
    SKIP_SETUP=1
    log "检测到 $INSTALL_PREFIX 已有 oscar，跳过 setup，直接做建库/初始化/服务与驱动收割"
elif [ -d "$INSTALL_PREFIX" ] && [ -n "$(ls -A "$INSTALL_PREFIX" 2>/dev/null)" ]; then
    die "$INSTALL_PREFIX 已存在且非空，但没有可执行的 bin/oscar。请确认安装是否被中断，或用 ST_INSTALL_PREFIX 换一个路径（本脚本不会覆盖已有目录）"
fi

# ---- 1. 找到介质 ----
# 注意：官网那个 "V7.0.21 aarch64" 实际是 ShenTongGauss（openGauss 系），不吃 izpack setup，
# 混进 vendor/ 会被误选，这里显式跳过。
MEDIA=""
for cand in "$PKG"/vendor/*.rar "$PKG"/vendor/*.tar.gz "$PKG"/vendor/*.tgz "$PKG"/vendor/*.tar.bz2 "$PKG"/vendor/*.zip; do
    [ -f "$cand" ] || continue
    case "$(basename "$cand")" in
        *Gauss*|*gauss*) warn "跳过 ShenTongGauss 介质（不是 OSCAR，无 setup）：$(basename "$cand")"; continue ;;
    esac
    case "$(basename "$cand")" in
        *aarch64*|*arm64*|*arm*) MEDIA=$cand; break ;;
    esac
    [ -n "$MEDIA" ] || MEDIA=$cand
done
if [ -z "$MEDIA" ]; then
    for d in "$PKG"/vendor/*/; do
        [ -d "$d" ] || continue
        if [ -n "$(find "$d" -maxdepth 3 -name setup -type f -perm -u+x 2>/dev/null | head -1)" ]; then
            MEDIA=$d
            break
        fi
    done
fi
if [ -z "$MEDIA" ]; then
    if [ "$SKIP_SETUP" = 1 ]; then
        log "已安装且无需介质，跳过 vendor/ 介质检查"
    else
        die "vendor/ 下没有找到神通安装介质（.rar/.tar.gz/.zip 或已解压目录）"
    fi
fi
[ -n "$MEDIA" ] && log "使用安装介质：$MEDIA"

# ---- 2. 解包 ----
WORK=$(mktemp -d /tmp/st-install.XXXXXX)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
SRC_DIR=""
if [ "$SKIP_SETUP" != 1 ]; then
if [ -d "$MEDIA" ]; then
    SRC_DIR=$MEDIA
fi
if [ -z "$SRC_DIR" ]; then
    case "$MEDIA" in
        *.rar)
            if command -v unrar >/dev/null 2>&1; then
                unrar x -idq "$MEDIA" "$WORK/"
            elif command -v 7z >/dev/null 2>&1; then
                7z x -y "-o$WORK" "$MEDIA" >/dev/null
            elif command -v bsdtar >/dev/null 2>&1; then
                bsdtar -xf "$MEDIA" -C "$WORK"
            else
                die "介质是 RAR，但系统缺少 unrar/7z/bsdtar。请先从离线源安装 unrar，或改放 tar.gz 形态的介质"
            fi
            ;;
        *.zip)
            command -v unzip >/dev/null 2>&1 || die "系统缺少 unzip"
            # 官网 arm64 介质里 doc/（Sphinx 手册）有 9000+ 文件，TCG 下白解十几分钟，先排除；
            # 排除后找不到 setup 再全量解一次，避免厂商换目录结构导致装不上。
            unzip -q "$MEDIA" -d "$WORK" -x "*/doc/*" "*/doc" >/dev/null 2>&1
            if [ -z "$(find "$WORK" -maxdepth 3 -type f -name setup 2>/dev/null | head -1)" ]; then
                warn "排除 doc/ 后没找到 setup，改为全量解压"
                find "$WORK" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
                unzip -q "$MEDIA" -d "$WORK"
            fi
            ;;
        *)
            tar xf "$MEDIA" -C "$WORK"
            ;;
    esac
    SRC_DIR=$(find "$WORK" -maxdepth 3 -type f -name setup 2>/dev/null | head -1 | xargs -r dirname)
    [ -n "$SRC_DIR" ] || SRC_DIR=$WORK
fi
[ -f "$SRC_DIR/setup" ] || die "解压后未找到 setup 程序（目录：$SRC_DIR）"
log "安装程序目录：$SRC_DIR"

# ---- 3. 应答文件：优先用厂商介质自带的那份 ----
# 官网 aarch64 介质（ShenTong7.0.8_342.185_arm64）在 setup 同级目录放了 auto-install.xml，
# 其面板类名是 izpack 新式（panels.target.TargetPanel / panels.userinput.UserInputPanel
# + OSCAR_DB_NAME 等 entry）。下面这份老式写法（panels.TargetPanel / DBConfigPanel）只在
# 介质没带应答文件时兜底，遇到新介质会直接被厂商那份覆盖。
VENDOR_XML=""
for d in "$SRC_DIR" "$SRC_DIR"/.. "$WORK"; do
    cand=$(find "$d" -maxdepth 2 -type f -name 'auto-install*.xml' 2>/dev/null | head -1)
    [ -n "$cand" ] && VENDOR_XML=$cand && break
done

if [ -n "$VENDOR_XML" ]; then
    XML="$WORK/install-answer.xml"
    sed -e "s#<installpath>.*</installpath>#<installpath>$INSTALL_PREFIX</installpath>#" \
        "$VENDOR_XML" > "$XML"
    log "使用介质自带应答文件：$(basename "$VENDOR_XML")"
    grep -q "<installpath>$INSTALL_PREFIX</installpath>" "$XML" || \
        warn "厂商应答文件里没有可替换的 <installpath>，将按介质默认路径安装（请核对最终安装位置）"
    if [ "$DB_NAME" != "OSRDB" ] || [ "$DB_ENCODING" != "utf8" ]; then
        warn "应答文件用厂商默认值：OSRDB / utf8。若 deploy.conf 改了库名或编码，请手工编辑 $XML 后重跑"
    fi
    warn "端口不在应答文件中指定时由 setup 取默认 2003；如现场端口不同，装完后用 ST_PORT 覆盖再验端口"
else
    XML="$WORK/auto-install.xml"
    log "介质未带应答文件，生成兜底应答文件：$XML"
    cat > "$XML" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.HTMLLicencePanel/>
    <com.izforge.izpack.panels.TargetPanel>
        <installpath>$INSTALL_PREFIX</installpath>
    </com.izforge.izpack.panels.TargetPanel>
    <com.izforge.izpack.panels.PackagesPanel>
        <packages>oscar_setup,oscar_server,oscar_client,oscar_agent,oscar_tools</packages>
    </com.izforge.izpack.panels.PackagesPanel>
    <com.oscar.panels.DBConfigPanel>
        <dbname>$DB_NAME</dbname>
        <port>$DB_PORT</port>
        <encoding>$DB_ENCODING</encoding>
        <jvmdir/>
        <hainable>false</hainable>
    </com.oscar.panels.DBConfigPanel>
</AutomatedInstallation>
EOF
fi
warn "auto-install.xml 的面板/字段名与厂商安装包版本强相关。若 setup 报面板不识别，请改用交互式 ./setup 并人工确认一次，再据实回填。"

# ---- 4. 安装 ----
chmod +x "$SRC_DIR/setup" 2>/dev/null || true
( cd "$SRC_DIR" && ./setup -f "$XML" ) || die "setup 执行失败，请查看上方输出"
fi  # SKIP_SETUP

# ---- 5. 建库与初始化 ----
# 厂商 setup 只把软件装上；库要按 bin/createdatabase.sh 的顺序走完三步才算可用：
#   CREATE DATABASE → oscar -o restrict（系统目录初始化）→ oscar -o install（注册服务）。
# 少任何一步，实例启动时都会报 "数据库还未进行初始化"，端口 2003 不起来。
OS_USER=$(stat -c %U "$INSTALL_PREFIX" 2>/dev/null || echo root)
[ "$OS_USER" = root ] && warn "$INSTALL_PREFIX 属主是 root，建议 chown -R oscar:oscar 后重跑（否则实例会以 root 运行）"
log "数据库运行用户：$OS_USER"

[ -f "$INSTALL_PREFIX/bin/oscar" ] || die "未找到 $INSTALL_PREFIX/bin/oscar"
[ -f "$INSTALL_PREFIX/admin/${DB_NAME}.conf" ] || die "未找到 $INSTALL_PREFIX/admin/${DB_NAME}.conf（setup 是否装到了 $INSTALL_PREFIX？）"

# 小内存保护：厂商默认 BUF_DATA_BUFFER_PAGES=409600（≈3.2 GB）+ SORT_MEM=1204000，
# 在 4 GB 的机器/虚机上会被内核 OOM 杀掉，现象是 2003 始终不起。同名键后者生效，
# 所以用追加覆盖的方式，不动厂商写好的那些行，随时可删掉尾部三行回退。
CONF="$INSTALL_PREFIX/admin/${DB_NAME}.conf"
MEMTOTAL_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${ST_SKIP_MEMTUNE:-0}" != "1" ] && [ "${MEMTOTAL_KB:-0}" -lt 8000000 ]; then
    if grep -q 'install-shentong.sh 追加' "$CONF" 2>/dev/null; then
        log "内存上限追加已存在，跳过（需回退请删掉 $CONF 尾部那段并重启实例）"
    else
        log "内存 $((MEMTOTAL_KB / 1024)) MB < 8 GB，追加缓冲池上限（原 $(wc -l < "$CONF") 行，已备份 .orig）"
        cp -p "$CONF" "$CONF.orig" 2>/dev/null || true
        printf '\n# 由 install-shentong.sh 追加：小内存机器保护\nBUF_DATA_BUFFER_PAGES=65536\nSORT_MEM=65536\nTHREADPOOL_MAX_THREADS=200\n' >> "$CONF"
        chown "$OS_USER:$(id -gn "$OS_USER" 2>/dev/null || echo oscar)" "$CONF"
    fi
fi

DBFILES=$(find "$INSTALL_PREFIX/odbs/$DB_NAME" -maxdepth 1 -name '*.dbf' 2>/dev/null | head -1)
ENC_UP=$(printf '%s' "$DB_ENCODING" | tr '[:lower:]' '[:upper:]')
if [ -n "$DBFILES" ]; then
    log "物理库已存在（$INSTALL_PREFIX/odbs/$DB_NAME），跳过 CREATE DATABASE"
else
    log "创建数据库 $DB_NAME（编码 $ENC_UP）"
    # 与 createdatabase.sh 组装出的语句一致；ARCHIVELOG 是厂商默认。
    su - "$OS_USER" -c "$INSTALL_PREFIX/bin/oscar -e \"CREATE DATABASE $DB_NAME ENCODING '$ENC_UP' LC_COLLATE 'C' LC_CTYPE 'C' DECRYPT ARCHIVELOG\"" \
        || die "CREATE DATABASE 失败，查看 $INSTALL_PREFIX/log/ 下的 elog"
fi

# 实例已经在跑就什么都不做：restrict 引导只能对未运行的实例下手，重复执行会打架。
if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":$DB_PORT"; then
    log "端口 $DB_PORT 已在监听，跳过初始化与启动（要重做请先 dbcontrol stop）"
else
    log "初始化系统目录（oscar -o restrict -d $DB_NAME）"
    su - "$OS_USER" -c "$INSTALL_PREFIX/bin/oscar -o restrict -d $DB_NAME" >/dev/null 2>&1 \
        || die "系统目录初始化失败，查看 $INSTALL_PREFIX/log/$DB_NAME/ 下的 elog"
    log "注册数据库服务（oscar -o install，需 root）"
    "$INSTALL_PREFIX/bin/oscar" -o install >/dev/null 2>&1 || warn "服务注册返回非零，继续用 dbcontrol 启动"

    # ---- 6. 服务 ----
    SVC="/etc/init.d/oscardb_${DB_NAME}d"
    if [ -x "$INSTALL_PREFIX/bin/dbcontrol" ]; then
        su - "$OS_USER" -c "$INSTALL_PREFIX/bin/dbcontrol start $INSTALL_PREFIX $DB_NAME" || {
            [ -x "$SVC" ] && "$SVC" start
        }
    elif [ -x "$SVC" ]; then
        "$SVC" start || warn "数据库服务启动失败"
    elif command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "oscardb"; then
        systemctl enable --now "oscardb_${DB_NAME}d" || warn "systemctl 启动数据库失败"
    else
        die "既没有 $INSTALL_PREFIX/bin/dbcontrol，也没有 $SVC，无法启动实例"
    fi
    if command -v chkconfig >/dev/null 2>&1 && [ -x "$SVC" ]; then chkconfig "$SVC" on; fi
fi

sleep 3
if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":$DB_PORT"; then
    log "端口 $DB_PORT 已监听"
else
    die "端口 $DB_PORT 未监听，神通数据库可能没起来（日志：$INSTALL_PREFIX/log/$DB_NAME/）"
fi

# ---- 7. 把驱动收进交付包 ----
# STPython 轮子随数据库安装目录交付（drivers/STPython/Python3xx/），ACI 在 drivers/aci/<arch>/。
# 镜像基础固定 python:3.11-slim，所以优先取 Python311 的那份。
PYTAG=311
WHL=$(find "$INSTALL_PREFIX/drivers/STPython" -maxdepth 2 -name "*cp${PYTAG}*cp${PYTAG}*linux_aarch64.whl" 2>/dev/null | head -1)
if [ -z "$WHL" ]; then
    warn "没找到 cp${PYTAG} 的 STPython 轮子；驱动目录里有：$(ls "$INSTALL_PREFIX/drivers/STPython" 2>/dev/null | tr '\n' ' ')"
    warn "要么换对应 Python 版本的基础镜像，要么手工把轮子放进 $PKG/wheels/ 后重跑"
else
    mkdir -p "$PKG/wheels"
    if cmp -s "$WHL" "$PKG/wheels/$(basename "$WHL")" 2>/dev/null; then
        log "驱动已在包内且一致：wheels/$(basename "$WHL")"
    else
        cp -f "$WHL" "$PKG/wheels/"
        log "已收驱动：$WHL -> wheels/$(basename "$WHL")"
    fi
fi

ACIDIR="$INSTALL_PREFIX/drivers/aci/arm64"
[ -d "$ACIDIR" ] || ACIDIR="$INSTALL_PREFIX/drivers/aci/linux64"
if [ -f "$ACIDIR/libaci.so" ]; then
    mkdir -p "$PKG/aci"
    if cmp -s "$ACIDIR/libaci.so" "$PKG/aci/libaci.so" 2>/dev/null; then
        log "ACI 与包内一致：aci/libaci.so"
    else
        cp -f "$ACIDIR/libaci.so" "$PKG/aci/libaci.so"
        log "已按现场版本更新 ACI：$ACIDIR/libaci.so -> aci/libaci.so"
    fi
else
    warn "$ACIDIR 下没有 libaci.so，STPython 将无法导入（请确认介质装了驱动组件）"
fi

log "神通数据库安装完成：$INSTALL_PREFIX / $DB_NAME / 端口 $DB_PORT"
log "SYSDBA 初始口令为厂商默认（szoscar55），请现场改库后把新口令写进 secrets/st-mcp.env"
log "真实握手验收由 scripts/verify.sh 第 5 组（容器内 CONNECT_OK）负责，本脚本不重复测。"
