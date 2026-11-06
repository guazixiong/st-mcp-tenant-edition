# ST-MCP 内网部署运行手册（一页版）

适用：aarch64 / 麒麟 V10，全程无外网。本包**自带数据库介质与 Python 驱动**，
默认从装库开始一路做到 MCP 连库成功；如果目标机已有神通在运行，把 `INSTALL_SHENTONG` 改成 `no` 即可只部署服务。
命令一律用 `sh` 前缀执行（本包脚本按 POSIX sh 写，`bash` 也可）。

---

## 0. 准备

U 盘只需要两个文件：

```
st-mcp-offline-arm64-rev10-20261101.tar.gz          （约 719 MB，其中 665 MB 是数据库介质）
st-mcp-offline-arm64-rev10-20261101.tar.gz.sha256
```

准确字节数与摘要以随包 `.sha256` 为准（`cat st-mcp-offline-arm64-rev10-20261101.tar.gz.sha256`）。

目标机需要：aarch64 CPU、Docker 可用（`docker info` 不报错）、**root**（装库/注册服务需要）、
空闲端口（默认 MCP 8100、数据库 2003）、磁盘 ≥ 6 GB、`unzip`。

## 1. 校验并解包

```sh
sha256sum -c st-mcp-offline-arm64-rev10-20261101.tar.gz.sha256     # 必须输出 ...: OK
tar xzf st-mcp-offline-arm64-rev10-20261101.tar.gz
cd st-mcp-offline-arm64-20261015
```

`sha256sum -c` 不通过说明 U 盘拷贝不完整，**不要继续**，重拷。

## 2. 填连接参数（不含口令）

**先确认你要连哪个库**，这决定 `deploy.conf` 怎么填：

| 场景 | 连哪个库 | `deploy.conf` 要改的 | `INSTALL_SHENTONG` |
|---|---|---|---|
| A | 这台机器上由本包新装的库（默认） | 不用改 | `yes` |
| B | **另一台机器**上已装好的神通服务器 | `ST_HOST=<那台库的 IP>`（`ST_PORT/ST_DB/ST_USER` 按现场改） | **`no`** |
| C | 这台机器同时连多个库 | 不动 `deploy.conf`，改用实例文件（见第 8 节） | 实例内为 `no` |

场景 B 是本包最容易踩的空档：**只填口令不够**，默认值连的是这台机器上新建的那个库。
另外被连的那台库要满足：`ST_PORT`（默认 2003）对这台机器可达（防火墙放行），
服务端大版本与本包 ACI/驱动一致（差异大时按第 4 节换 `libaci.so`）。

主实例的参数（编辑 `deploy.conf`）：

```
ST_HOST=127.0.0.1           # 库在本机填 127.0.0.1，容器内会自动改写成网桥网关 172.17.0.1
                            # 库在另一台机器上就填那台机器的 IP，不做改写
ST_PORT=2003
ST_USER=SYSDBA
ST_DB=OSRDB
HOST_PORT=8100              # 对外 MCP 端口，被占就改成空闲端口
TRANSPORT=streamable-http   # 默认值：端点取下面的 MCP_PATH(/mcp)；改成 sse 则端点为 /sse
MCP_PATH=/mcp               # 只在 TRANSPORT=streamable-http 时生效
INSTALL_SHENTONG=yes        # yes：库不存在时由本包装；已装好则自动跳过 setup 只补齐初始化
ST_INSTALL_PREFIX=/opt/ShenTong
```

`INSTALL_SHENTONG` 三种取值：`yes`（装/补齐，介质在 `vendor/`）、`auto`（有介质或已装才动）、`no`（完全不碰数据库）。

## 3. 填口令（唯一必须手工输入的信息）

```sh
cp secrets/st-mcp.env.example secrets/st-mcp.env
vi secrets/st-mcp.env       # 只写一行 ST_PASSWORD=<原样口令>
chmod 600 secrets/st-mcp.env
```

特殊符号规则（这是本包改造过的重点，照做就不会解析异常）：

- 等号后面**原样写**：不加引号、不反斜杠转义、不 URL 编码。
- `@ : / # % ! $ 空格 中文` 全都允许，例如 `ST_PASSWORD=Zz#kjt/2026@测试!p7cur:e%40?q`。
- 只有两条硬限制：口令里不能含**换行**；该行行首不能是 `#`。
- 口令**绝不**写进 `deploy.conf`、命令行或镜像里（命令行传参在 `ps` 可见）。

数据库侧改口令的语法是 `ALTER USER SYSDBA WITH PASSWORD '<新口令>'`——**不是** `IDENTIFIED BY`
（后者在神通里只用于 DATABASELINK / WALLET / `EXEC SQL CONNECT`，写成 `IDENTIFIED BY` 会直接 syntax error）。
默认口令 `sysdba/szoscar55`，受 `MinPasswordLen` 约束，太短会被拒。**装完库必须改掉它**，用包内脚本一条命令完成：

```sh
vi /tmp/ST_PASSWORD_NEW       # 只写一行：新口令，保存退出（原样写，规则同上面第 3 节）
sh set-sysdba-password.sh     # 期望最后一行 PW_OK
```

脚本做的事：用包内镜像自带的驱动连库执行 `ALTER USER`，再用新口令重连 `SELECT 1` 验证，
验证通过后把新口令同步写回 `secrets/<实例>.env` 的 `ST_PASSWORD=`（属性 600），最后 `shred -u` 掉临时文件。
口令只经文件传递，不进命令行、不进 shell history（`ps` / `/proc/<pid>/cmdline` 里看不到），
特殊符号 `@ : / # % ?` 也不会被 shell 二次解析——这正是不要手敲 `python -c "...ALTER USER..."` 的原因。

```sh
sh set-sysdba-password.sh erp   # 改的是实例 erp 连的那个库，口令写进 secrets/erp.env
```

失败行的含义：`OLDPW_BAD|...` = 现用口令不是 `secrets/*.env` 里写的那个（已被人改过）；
`NEWPW_VERIFY_FAILED|...` = 改了但新口令连不上，此时 `secrets/*.env` 不会被改写，仍能按旧口令回退。
（图形客户端 OEM 或 `bin/isql` 也能执行同一条 `ALTER USER`；用 OEM 改完记得手工把新口令写进 `secrets/*.env`。）

改完口令**必须重跑 `sh install-all.sh`（或 `sh install-all.sh <实例>`）重建容器**：
容器是创建时把口令读进环境变量的，`docker restart` 复用的还是老环境变量。
这一条不能靠 `verify.sh` 判断——第 5 组是**新起一个容器**读 `secrets/*.env` 做连库自检，
第 4 组只测 HTTP 握手，所以长期容器手里是旧口令时验收照样能 22 PASS。
真判据是比对生效环境变量，或用客户端跑一次 `execute_sql`：

```sh
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' st-mcp | grep '^ST_PASSWORD='
grep '^ST_PASSWORD=' secrets/st-mcp.env      # 两边应当一致
```

## 4. 驱动（无需再找厂商）

`wheels/STPython-2.0.19-cp311-cp311-linux_aarch64.whl` 与 `aci/libaci.so` 已在包内，构建镜像时自动打进去。
只有当你要连的库版本与本包 ACI 差异较大时，才用**该库自己**的
`$SZ_OSCAR_HOME/drivers/aci/arm64/libaci.so` 覆盖 `aci/libaci.so`，或把 `drivers/STPython/Python311/` 里的轮子覆盖 `wheels/` 那份。
介质自带这些文件，`install-shentong.sh` 装完库也会自动收割回包内。

## 5. 一键部署

```sh
sh install-all.sh
```

期望关键输出：

```
校验交付包完整性
  全部文件摘要一致
检测到 vendor/ 下的神通安装介质，开始安装
  … setup → CREATE DATABASE → oscar -o restrict → oscar -o install → dbcontrol start
  端口 2003 已监听
  驱动已就位：wheels/STPython-2.0.19-cp311-cp311-linux_aarch64.whl
离线构建 st-mcp:0.1.0 …（--network=none，只用包内 wheels/ + aci/）
IMPORT_OK
启动容器 st-mcp（宿主机端口 8100 -> 容器 8100，传输 streamable-http，端点 /mcp）
===== 验收汇总：22 PASS / 0 FAIL / 0 SKIP =====
部署完成：实例 主 的 MCP 端点 http://127.0.0.1:8100/mcp
主账号（default）客户端地址：http://<本机IP>:8100/mcp/<token>
```

刚解包、只填了 `secrets/st-mcp.env` 的口令（没动 `deploy.conf`）就是上面第 2 行 `全部文件摘要一致`。
改过 `deploy.conf` / `instances/*.conf` / `secrets/*.env` 之后再跑，那几行会被列为「现场修改过（属预期）」并放行。
出现 `ERROR: 以下文件与打包时不一致，U 盘拷贝可能不完整：…` 才是真的拷贝缺损，需要重拷 U 盘。

`只有 deploy.conf / instances/*.conf / secrets/*.env` 的差异会被放行（那是现场自己配的）。
文件**根本不存在**（`sha256sum` 报 `…: FAILED open or read`）与摘要不符同等对待、同样中止：
旧版只匹配 `: FAILED` 结尾的形态，缺文件时被过滤成空，697 MB 的库介质少拷一半也会被放行。

**麒麟中文环境要特别注意**：`sha256sum -c` 会把 `OK/FAILED` 印成`确定/失败`，脚本原来抓 `: FAILED`
的判据在中文机器上一行也抓不到，等于整包校验静默失效（2026-10-26 真机现场实测暴露）。
脚本调用处已钉成 `LC_ALL=C sha256sum -c SHA256SUMS.txt`；你手工敲校验时也照这个写，
否则 `grep -v ': OK$'` 会把 71 行全部留下，看着像全坏了。

现场把 `vendor/` 的介质删掉腾磁盘（库已经装好，介质用不上）之后再重跑部署，会在这里 die。
这时明确跳过校验，而不是让脚本猜：

```sh
SKIP_CHECKSUM=1 sh install-all.sh
```

代价是自己确认过剩下的文件都没坏。别默认带上这个变量。

装库阶段耗时最长（TCG 虚拟机上解压 + setup 约十几分钟；真机飞腾/鲲鹏几分钟）。

## 6. 自检

```sh
sh scripts/verify.sh        # 期望 0 个 FAIL
```

```
===== 验收汇总：22 PASS / 0 FAIL / 0 SKIP =====   ← 退出码 0
```

连库自检（走镜像默认入口，**不要**加 `--entrypoint st-mcp`，否则会绕过网关重映射）：

```sh
docker run --rm --env-file secrets/st-mcp.env \
  -e ST_HOST=127.0.0.1 -e ST_USER=SYSDBA -e ST_DB=OSRDB \
  st-mcp:0.1.0 --test-connection --transport stdio
# 期望：CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB (password hidden)
```

服务在线判定（主实例是单端口多租户，**探活要带 token**；`/healthz` 不需要）：

```sh
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:8100/healthz   # 200

# 带租户 token 发一次 MCP initialize 握手（token 从 tenants/tenants.json 或 list-tenants.sh 取）
curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 -X POST http://127.0.0.1:8100/mcp/<token> \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'   # 200

# 不带 token 必须 401（服务端启用了注册表）
curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 -X POST http://127.0.0.1:8100/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'   # 401
docker logs --tail 20 st-mcp
```

## 7. 客户端接入

本包默认 `TRANSPORT=streamable-http`，端点是 `/mcp`，地址最后一段是租户 token：

```json
{ "mcpServers": { "shentong": { "url": "http://<目标机IP>:8100/mcp/<token>" } } }
```

**主实例启用租户注册表后只支持 streamable-http**，改回 sse 会被拒绝启动。
只会讲 sse 的老客户端，另建一个不挂注册表的实例（单库单账号，不参与多租户）：

```sh
cd "$PKG" && TRANSPORT=sse sh scripts/new-instance.sh legacy <库IP>
vi secrets/legacy.env && sh install-all.sh legacy
```

```json
{ "mcpServers": { "shentong": { "url": "http://<目标机IP>:<该实例端口>/sse" } } }
```

同一台机器上两种客户端都要服务时，别改主实例：给老客户端单独建一个 sse 实例
（`TRANSPORT=sse sh scripts/new-instance.sh legacy <库IP>`），一个走 `/sse`、一个走 `/mcp`，共用同一镜像和同一个库。

接错传输的现象（实测）：客户端 `POST /sse` 得到 **405** = 容器是 sse、客户端用的是 streamable-http，
按上面改 `TRANSPORT` 重跑；连 `/mcp` 得到 **404** = 容器还是 sse（`/mcp` 路由不存在）；
`GET /mcp` 得到 **406** 是正常的，streamable-http 只收 POST，别拿它当探活；
非本机地址连 `/mcp` 得到 **421** = 镜像还是旧的那份（Host 校验只放行 127.0.0.1/localhost），重跑
`sh install-all.sh` 重建镜像即可。

多个实例就多条配置，端口各用自己的 `HOST_PORT`（见第 8 节）。

## 8. 一台机器连多个神通库（多实例）

一个镜像起 N 个容器，每个容器连一个库、占一个 MCP 端口、有自己独立的口令文件：

```sh
# 生成实例 erp，连 10.20.30.41:2003 的 OSRDB（用户 SYSDBA；MCP 端口自动挑空闲的）
sh scripts/new-instance.sh erp 10.20.30.41
vi secrets/erp.env                    # 填这个库的口令（原样写，规则同第 3 节）
sh install-all.sh erp                 # 只创建/替换 st-mcp-erp 这一个容器

# 需要指定库端口/库名/用户/MCP 端口时：第 3~6 个参数依次是 库端口 库名 用户 MCP端口
sh scripts/new-instance.sh core 10.20.30.42 2003 CORE SYSDBA 8102
vi secrets/core.env
sh install-all.sh core

sh scripts/list-instances.sh          # 所有实例：状态 / 连接目标 / 端口 / 口令是否就位
sh scripts/verify.sh core             # 单独验收一个实例
docker rm -f st-mcp-core              # 只停这一个实例
```

实例的传输方式由环境变量决定（不设就是 `streamable-http`，与主实例一致）：

```sh
TRANSPORT=sse sh scripts/new-instance.sh legacy 10.20.30.41
# 生成完会直接告诉你客户端该连 http://<本机IP>:<端口>/sse（默认 streamable-http 则是 /mcp）
```

生成后想换：编辑 `instances/erp.conf` 里的 `TRANSPORT`（`sse` 或 `streamable-http`），再 `sh install-all.sh erp`。

要点：

- 实例文件落在 `instances/<名字>.conf` + `secrets/<名字>.env`，主实例的 `deploy.conf` 保持不动。
- 实例生成时 `INSTALL_SHENTONG=no`：**实例不在本机装库**，要装库用主实例（场景 A）。
- 容器名自动为 `st-mcp-<名字>`；端口撞了会在起容器前报错并指出被哪个容器占用，
  不会误删别的实例（主实例 `sh install-all.sh` 也只会替换 `st-mcp` 自己）。
- 连远端库的这台机器**不需要装神通客户端**：STPython 与 `libaci.so` 都在镜像里。

## 9. 改参数 / 重装 / 卸载

```sh
vi deploy.conf && sh install-all.sh     # 幂等，可反复执行；重建容器但不动数据
vi instances/erp.conf && sh install-all.sh erp   # 改某个实例（换库地址、换端口…）
sh shentong/install-shentong.sh         # 只重跑装库/建库/驱动收割（已装则跳过 setup）
docker rm -f st-mcp                     # 卸载主实例（数据库数据在 $ST_INSTALL_PREFIX 下，不受影响）
docker rm -f st-mcp-erp                 # 卸载某个实例
```

## 10. 故障速查

| 现象 | 处理 |
|---|---|
| `sha256sum -c` 失败 | U 盘重拷 |
| `ERROR: 以下文件与打包时不一致…：vendor/…zip` 但库早装好了 | 现场删过介质腾磁盘：`SKIP_CHECKSUM=1 sh install-all.sh`（确认其余文件没坏再用） |
| 改了库口令后验收仍 22 PASS，客户端却报 `invalid username/password` | 长期容器还是老环境变量：`sh install-all.sh` 重建，判据见第 3 节 |
| `docker: not found` / 权限拒绝 | 装 Docker 或把用户加 `docker` 组 |
| 端口占用 | 改 `deploy.conf` 的 `HOST_PORT` 重跑 |
| `宿主机端口 8102 已被容器 st-mcp-erp 占用` | 两个实例撞端口了：改后建那个实例的 `instances/<名字>.conf` 里 `HOST_PORT`（或重跑 `new-instance.sh` 让它自动选空闲端口） |
| 连远端库报 `connection refused`，但对端库确实在跑 | ①`ST_HOST` 填的必须是对端 IP，不能是 `127.0.0.1`（那会被当成本机库改写）；②对端 2003 防火墙是否放行；③对端监听地址是否含外部网卡（`netstat -ltn | grep 2003` 应为 `0.0.0.0:2003`） |
| `实例 x 已存在` | 不用重新生成，直接编辑 `instances/x.conf` 后 `sh install-all.sh x` |
| `数据库还未进行初始化`、2003 不起 | 重跑 `sh shentong/install-shentong.sh`（会补 `oscar -o restrict` 与 `oscar -o install`） |
| `oscar` 被 OOM killed | `admin/OSRDB.conf` 尾部加小内存段（脚本会自动加），`dbcontrol restart` |
| `connection refused` | 库没起或端口不是 2003；容器内连宿主机库要靠 `ST_HOST_GATEWAY` |
| `invalid username/password` | 口令被转义或被加了引号；按第 3 节原样重填 |
| `module STPython not found` | 镜像是旧版；`IMAGE_MODE=build sh install-all.sh` 重建 |
| `libaci.so` 符号缺失 | 用被连库自己的 `drivers/aci/arm64/libaci.so` 覆盖包内那份后重建 |
| 非 aarch64 机器 | `build-image.sh` 会拒绝；包内轮子/ACI 均为 arm64，x86_64 需另打包 |
