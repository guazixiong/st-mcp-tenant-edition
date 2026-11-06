#!/bin/sh
# 提交前哨兵：扫现场痕迹（内网 IP、客户路径、真实口令、webhook/密钥、大二进制）。
# 用法：sh .secrets-scan.sh        命中即退出码 1
set -u
cd "$(dirname "$0")"

PATTERNS='100\.80\.[0-9]+\.[0-9]+|172\.17\.0\.1:8100|/usr/local/yidong|open-apis/bot/|szoscar55.{0,4}$'

# 允许的写法：占位符、文档里的合成样例口令、代码里读取变量的语句
ALLOW='请填写|示例|合成样例|<[^>]*>|\$|%s|ST_PASSWORD=请|Zz#kjt'

hits=$(grep -rnIE "$PATTERNS" \
    --include='*.md' --include='*.sh' --include='*.py' --include='*.conf' \
    --include='*.txt' --include='*.toml' --include='*.example' --exclude-dir=.git --exclude-dir=release --exclude-dir=.release-stage . 2>/dev/null | \
    grep -v '^\./\.secrets-scan\.sh:')

pw=$(grep -rnIE 'ST_PASSWORD=[^ "'"'"']*[^ "'"'"'<>$%]' --include='*.md' --include='*.conf' --include='*.example' --exclude-dir=.git --exclude-dir=release --exclude-dir=.release-stage . 2>/dev/null \
    | grep -E 'ST_PASSWORD=[^ "'"'"']{9,}' | grep -vE "$ALLOW" | grep -v '^\./\.secrets-scan\.sh:')

if [ -n "$hits$pw" ]; then
    [ -n "$hits" ] && printf '%s\n' "$hits"
    [ -n "$pw" ] && printf '%s\n' "$pw"
    echo
    echo "FAIL: 上面这些行含现场/凭据痕迹，公开仓库里要么删掉要么换成占位符（如 <库机IP>）。"
    echo "说明：'厂商默认口令 szoscar55 必须改掉' 这类句子是允许的，但出现在行尾（像是待填值）会被拦。"
    exit 1
fi

big=$(find . -type f -not -path './release/*' -not -path './.release-stage/*' \( -name '*.whl' -o -name '*.so' -o -name '*.zip' -o -name '*.tar.gz' \) -size +1M -print)
if [ -n "$big" ]; then
    echo "$big"
    echo "FAIL: 仓库里有大二进制（厂商介质/驱动/镜像），不得公开分发。"
    exit 1
fi

echo "PASS: 未发现现场痕迹，也没有大二进制。"
