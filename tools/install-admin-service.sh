#!/bin/sh
# 把租户管理界面装成 systemd 常驻服务：监听 0.0.0.0:8001，远程访问必须登录。
# 用法：sh tools/install-admin-service.sh [端口]
# 指定交付夹位置：ROOT=/opt/ST-MCP-DELIVERY-20261026 sh tools/install-admin-service.sh
set -eu
SELF=$(cd "$(dirname "$0")" && pwd)
ROOT=${ROOT:-$(cd "$SELF/.." && pwd)}
PORT=${1:-8001}
UNIT=/etc/systemd/system/st-mcp-admin.service
AUTH="$ROOT/secrets/admin.env"

[ "$(id -u)" = 0 ] || { echo "请用 root 执行"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "本机没有 systemd，无法常驻"; exit 1; }

PY=$(command -v python3 || true)
[ -n "$PY" ] || { echo "缺少 python3：先装 fix-python3-standalone 里的独立解释器"; exit 1; }

if [ ! -f "$AUTH" ]; then
    mkdir -p "$ROOT/secrets"
    umask 077
    PASS=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(9))')
    printf 'ADMIN_USER=admin\nADMIN_PASSWORD=%s\n' "$PASS" > "$AUTH"
    echo "已生成管理界面登录口令：$AUTH（用户 admin）"
fi
chmod 600 "$AUTH" 2>/dev/null || true

cat > "$UNIT" <<EOF
[Unit]
Description=ST-MCP tenant admin UI
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$ROOT
ExecStart=$PY $ROOT/st-mcp-admin.py --host 0.0.0.0 --port $PORT --allow-remote --auth-file $AUTH
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable st-mcp-admin.service >/dev/null 2>&1
systemctl stop st-mcp-admin.service 2>/dev/null || true

# 手动跑着的旧界面会占住端口，先结束它（只匹配本脚本路径）
if ss -ltn 2>/dev/null | grep -q ":$PORT " || netstat -ltn 2>/dev/null | grep -q ":$PORT "; then
    echo "端口 $PORT 被占用，结束手动启动的 st-mcp-admin.py"
    pkill -f "st-mcp-admin.py.*--port $PORT" 2>/dev/null || true
    sleep 1
fi

systemctl restart st-mcp-admin.service
sleep 1
systemctl --no-pager --lines=5 status st-mcp-admin.service || true

cat <<EOF

访问：http://<本机IP>:$PORT/    用户 admin，口令在 $AUTH
来源限制（建议）：在 $AUTH 里加一行 ADMIN_ALLOW_CIDR=10.0.0.0/24，再 systemctl restart st-mcp-admin
开机自启：已 enable；停止：systemctl stop st-mcp-admin；日志：journalctl -u st-mcp-admin -f
EOF
