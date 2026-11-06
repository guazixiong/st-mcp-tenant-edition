#!/bin/sh
# 改神通 SYSDBA 口令，并把新口令同步进交付包的 secrets/<实例>.env。
# 为什么要这个脚本：交付包不做任何自动改口令（厂商默认口令 szoscar55 必须现场改掉），
# 而手工敲 python -c "...ALTER USER..." 会把口令送进 shell history 和 /proc/<pid>/cmdline，
# 特殊符号（@ : / # % ?）还会被 shell 二次解析。本脚本让口令只经文件传递。
#
# 用法（在交付包根目录下，root 执行）：
#   vi /tmp/ST_PASSWORD_NEW          # 只写一行：新口令，保存退出
#   sh set-sysdba-password.sh        # 默认改主实例
#   sh set-sysdba-password.sh erp    # 改名为 erp 的实例
set -e

PKG=${ST_MCP_PKG:-$(cd "$(dirname "$0")" && pwd)}
[ -f "$PKG/deploy.conf" ] || PKG=$(pwd)
INSTANCE=${1:-}
NEWF=/tmp/ST_PASSWORD_NEW
[ -f "$PKG/deploy.conf" ] || { echo "找不到 $PKG/deploy.conf，请先 cd 到交付包根目录（或用 ST_MCP_PKG=路径 指定）"; exit 1; }
[ -s "$NEWF" ] || { echo "先执行：vi $NEWF   （只写一行新口令，保存退出）"; exit 1; }

# 实例的 conf / env 名字规律与 install-all.sh 一致
if [ -n "$INSTANCE" ]; then
    CONF="$PKG/instances/$INSTANCE.conf"
    ENVF="$PKG/secrets/$INSTANCE.env"
    [ -f "$CONF" ] || { echo "没有 $CONF（用 sh scripts/new-instance.sh $INSTANCE <库IP> 先建实例）"; exit 1; }
else
    CONF="$PKG/deploy.conf"
    ENVF="$PKG/secrets/st-mcp.env"
    [ -f "$ENVF" ] || { echo "没有 $ENVF，先按 RUNBOOK 第 3 节把当前口令写进去"; exit 1; }
fi
[ -f "$ENVF" ] || { echo "没有 $ENVF"; exit 1; }

getk() {
    # 与 scripts/common.sh 的 load_conf 同规则：按每行第一个 = 拆分，值原样保留
    _v=$(sed -n "s/^$2=//p" "$1" | tail -1)
    printf '%s' "$_v"
}
ST_HOST=$(getk "$CONF" ST_HOST)
ST_PORT=$(getk "$CONF" ST_PORT)
ST_USER=$(getk "$CONF" ST_USER)
ST_DB=$(getk "$CONF" ST_DB)
IMAGE_REF="$(getk "$CONF" IMAGE_NAME):$(getk "$CONF" IMAGE_TAG)"
[ -n "$ST_PORT" ] || ST_PORT=2003
[ -n "$ST_USER" ] || ST_USER=SYSDBA
[ -n "$ST_DB" ] || ST_DB=OSRDB
[ "$IMAGE_REF" = ":" ] && IMAGE_REF=st-mcp:0.1.0
OLDPW=$(getk "$ENVF" ST_PASSWORD)
NEWPW=$(tr -d '\n' < "$NEWF")
[ -n "$NEWPW" ] || { echo "$NEWF 是空的"; exit 1; }

# 容器里的 ST_HOST=127.0.0.1 指的是容器自己，库在同机时要换成宿主机网桥（和 entrypoint.sh 同规则）
if [ "$ST_HOST" = 127.0.0.1 ] || [ "$ST_HOST" = localhost ]; then
    GW=$(getk "$CONF" ST_HOST_GATEWAY)
    [ -n "$GW" ] || GW=172.17.0.1
    echo "[info] ST_HOST=$ST_HOST 是本机库，改从容器访问宿主机网桥 $GW"
    CONNECT_HOST=$GW
else
    CONNECT_HOST=$ST_HOST
fi

WORK=/tmp/st-setpw.$$
mkdir -p "$WORK"; chmod 700 "$WORK"
printf '%s' "$OLDPW" > "$WORK/OLD"
printf '%s' "$NEWPW" > "$WORK/NEW"
cat > "$WORK/setpw.py" <<'PY'
import sys, STPython
old = open("/pw/OLD").read()
new = open("/pw/NEW").read()
host, port, user, db = sys.argv[1:5]
dsn = "%s:%s/%s" % (host, port, db)
def conn(pw):
    return STPython.connect(user=user, password=pw, dsn=dsn)
try:
    c = conn(old)
except Exception as e:
    print("OLDPW_BAD|%s" % type(e).__name__)
    sys.exit(3)
cur = c.cursor()
sql = "ALTER USER " + user + " WITH PASSWORD '" + new.replace("'", "''") + "'"
cur.execute(sql)
c.commit(); c.close()
try:
    c2 = conn(new)
    c2.cursor().execute("SELECT 1")
    c2.close()
except Exception as e:
    print("NEWPW_VERIFY_FAILED|%s" % type(e).__name__)
    sys.exit(4)
print("PW_OK")
PY

echo "[info] 用镜像 $IMAGE_REF 连 $CONNECT_HOST:$ST_PORT 改 $ST_USER 的口令"
# 镜像里是 USER stmcp（uid 10001），读不到 root 的 0700 临时目录，所以这一条显式用 uid 0；
# 只读两个口令文件，不回写任何东西。
docker run --rm --user 0:0 --network=host \
    -v "$WORK:/pw:ro" -v "$WORK/setpw.py:/setpw.py:ro" \
    --entrypoint python "$IMAGE_REF" /setpw.py "$CONNECT_HOST" "$ST_PORT" "$ST_USER" "$ST_DB" \
    || { echo "[FAIL] 改口令失败。常见原因：旧口令与 $ENVF 里的不一致（先把真正生效的旧口令写进去再跑）；或新口令不满足 MinPasswordLen"; rm -rf "$WORK"; exit 1; }

# 原子替换 env 里的 ST_PASSWORD，其它行不动
printf 'ST_PASSWORD=%s\n' "$NEWPW" > "$WORK/env.new"
sed "/^ST_PASSWORD=/d" "$ENVF" > "$WORK/env.head"
cat "$WORK/env.head" "$WORK/env.new" > "$ENVF"
chmod 600 "$ENVF"
rm -rf "$WORK"
shred -u "$NEWF" 2>/dev/null || rm -f "$NEWF"

echo "[ok] 口令已改并用新口令回连验证通过；$ENVF 已更新（权限 600），临时口令文件已删除"
echo "[next] 让容器带新口令重启：sh install-all.sh${INSTANCE:+ $INSTANCE}"
