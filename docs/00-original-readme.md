# ST-MCP 离线交付包（aarch64 / 麒麟 V10）

版本：`st-mcp 0.1.0` ｜ 打包日期：2026-10-15（rev.6 2026-10-16：带 ARM64 数据库介质与 STPython 驱动，装库+建库+连库闭环）｜ 目标架构：**linux/arm64** ｜ 全程**不需要外网**

本包在麒麟 V10 SP3 / aarch64 上做过整包实机验收：
**装库 → 建库 → 起服务 → 建镜像 → 起容器 → 特殊符号口令连库 → MCP 工具真实执行 SQL**，
`scripts/verify.sh` 结果 **22 PASS / 0 FAIL / 0 SKIP**，末尾 `[PASS] CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB (password hidden)`。

---

## 一、包内容

```
st-mcp-offline-arm64-20261015/
├── install-all.sh              一键部署入口（幂等，可反复执行）
├── set-sysdba-password.sh      改 SYSDBA 口令（装完库的必经手工步骤，口令只走文件）
├── deploy.conf                 部署参数（不含口令）
├── README.md                   本文件（结构与原理）
├── RUNBOOK.md                  一页版运行手册（照这个做即可）
├── PATCH-NOTES.md              特殊符号口令的改动说明 + 回归测试跑法
├── SHA256SUMS.txt              整包校验
├── filelist.txt                文件清单
├── images/
│   └── 01-python-3.11-slim.tar.gz        基础镜像（docker-archive，50 MB，构建 st-mcp 用）
├── wheels/                     31 个轮子：30 个依赖 + 厂商 STPython 驱动
│   ├── STPython-2.0.19-cp311-cp311-linux_aarch64.whl   神通 Python 驱动（连库用）
│   ├── cffi / pydantic_core / rpds_py 的 aarch64 cp311 轮子
│   └── st_mcp-0.1.0-py3-none-any.whl                   改造后的 MCP 主体
├── aci/                        神通 ACI 客户端共享库（STPython 运行期依赖）
│   ├── libaci.so               2,409,448 字节，随介质版本一致
│   └── README.md               版本一致性与换库时的替换说明
├── docker/
│   ├── Dockerfile.offline      离线构建（pip --no-index，只用本地 wheels + aci）
│   └── entrypoint.sh           容器入口（POSIX sh，口令只走环境变量）
├── scripts/
│   ├── common.sh               公共函数（含实例路径解析、端口占用检测）
│   ├── build-image.sh          离线构建 st-mcp 镜像（可选 --save 导出归档）
│   ├── verify.sh               离线自检（5 组 22 项检查；可加实例名只验那一个）
│   ├── new-instance.sh         生成一个新实例（连另一个神通库，一条命令）
│   └── list-instances.sh       列出主实例 + 所有实例的状态/端口/连接目标
├── instances/                  多实例配置目录（一台机器连多个神通库时用）
│   └── README.md               实例文件的规则与样例命令
├── shentong/
│   └── install-shentong.sh     神通数据库静默安装 + 建库 + 初始化 + 服务注册 + 驱动收割
├── src/                        改造后的 Python 源码 + 回归测试（审计与内网热替换用）
│   ├── st_mcp/                 15 个 .py（关键：sql/connstr.py、sql/sql_driver.py、server.py）
│   └── test/                   16 项测试，不需要数据库也不需要厂商驱动
├── vendor/
│   ├── PUT-FILES-HERE.md       投递位说明
│   └── ShenTong7.0.8_342.185_arm64.zip   官方 OSCAR V7.0.8 aarch64 服务端介质（665 MB）
└── secrets/
    ├── st-mcp.env.example      口令文件模板（主实例）
    └── <实例名>.env            各实例的口令（new-instance.sh 生成，权限 600）
```

## 二、前置条件

| 项 | 要求 |
|---|---|
| CPU 架构 | aarch64（飞腾 / 鲲鹏） |
| 操作系统 | 麒麟 V10 SP3 或其他 aarch64 Linux |
| Docker | 已安装且守护进程可用（本包不含 Docker 本体） |
| 权限 | root（要装数据库、注册服务）；只部署 MCP 时 docker 组即可 |
| 磁盘 | ≥ 6 GB（介质 665 MB + 解压后的 `/opt/ShenTong` 约 1.8 GB + 镜像 202 MB） |
| 内存 | ≥ 4 GB 跑库；更低时见 §三"小内存机器"（`install-shentong.sh` 会自动压低缓冲池） |
| 端口 | `8100`（MCP，可改 `deploy.conf`）、`2003`（神通，装库时用） |
| 工具 | 解 `.zip` 介质需 `unzip`；`.rar` 形态需 `unrar`/`7z`/`bsdtar` |

## 三、部署（三步）

### 先选一种场景（决定你要不要改 `deploy.conf`）

| 场景 | 连的是哪个神通库 | 要改什么 | 部署命令 |
|------|------------------|----------|----------|
| **A** | 这台机器上**由本包新装**的库（默认） | 只填口令 | `sh install-all.sh` |
| **B** | **另一台机器上**已经装好的神通服务器 | `deploy.conf`：`ST_HOST=<那台库的IP>`、按需改 `ST_PORT/ST_DB/ST_USER`，并把 `INSTALL_SHENTONG` 改成 `no` | `sh install-all.sh` |
| **C** | 这台机器同时连**多个**神通库 | 每个库生成一个实例文件（不动 `deploy.conf`） | `sh install-all.sh <实例名>` |

- 场景 A 与 B 用的是**同一个实例**（主实例），区别只在 `deploy.conf` 里 `ST_HOST` 填谁 + `INSTALL_SHENTONG` 是否装本机库。
- "只填口令就行"**只对场景 A 成立**：默认值连的就是这台机器上新建的那个库。要连已有/远端的库，必须按 B 改这 4+1 个字段。
- 场景 C 详见下面「§五之补充：一台机器跑多个 ST-MCP」。

```sh
# 1) 校验并解包（U 盘上只有 tar.gz 与 .sha256 两个文件）
sha256sum -c st-mcp-offline-arm64-rev10-20261101.tar.gz.sha256
tar xzf st-mcp-offline-arm64-rev10-20261101.tar.gz
cd st-mcp-offline-arm64-20261015

# 2) 填口令（唯一必须手工输入的信息）
cp secrets/st-mcp.env.example secrets/st-mcp.env
vi secrets/st-mcp.env
chmod 600 secrets/st-mcp.env

# 3) 一键部署 + 自检（装库 → 建库 → 建镜像 → 起容器 → 验收）
sh install-all.sh

# 4) 改掉数据库的厂商默认口令（场景 A 必经；本包不会自动改）
vi /tmp/ST_PASSWORD_NEW        # 只写一行：新口令
sh set-sysdba-password.sh      # 期望 PW_OK，并把新口令同步进 secrets/st-mcp.env
```

第 4 步只在**本包新装库**（场景 A）时才需要做：新库的 SYSDBA 还是厂商默认口令（首次登录后必须修改），
现场安全检查会盯这一条。场景 B（连已有库）口令由对方 DBA 给，直接填进 `secrets/*.env` 即可，
**不要**跑这个脚本去改别人的库。多实例要改某个实例连的库：`sh set-sysdba-password.sh <实例名>`。

只想先看环境是否就绪、不做任何改动：

```sh
sh scripts/verify.sh
```

### 数据库由本包安装（默认，`INSTALL_SHENTONG=yes`）

`install-all.sh` 会调 `shentong/install-shentong.sh`，它在 aarch64 上完成四件事：

1. **装软件**：解开 `vendor/` 介质，优先用介质自带的 `auto-install.xml` 应答文件跑 izpack `setup`，装到 `ST_INSTALL_PREFIX`（默认 `/opt/ShenTong`）。
2. **建库并初始化**（这一步是老脚本最容易漏的地方，缺了就报"数据库还未进行初始化"、2003 不起）：
   - `oscar -e "CREATE DATABASE …"` 建物理库；
   - `oscar -o restrict -d OSRDB` 引导系统目录；
   - `oscar -o install -d OSRDB` 注册 init/systemd 服务（需 root）；
   - `bin/dbcontrol start $SZ_OSCAR_HOME OSRDB` 启动实例。
3. **小内存保护**：向 `admin/OSRDB.conf` 追加 `BUF_DATA_BUFFER_PAGES=65536 / SORT_MEM=65536 / THREADPOOL_MAX_THREADS=200`（带标记、可幂等重跑、首次改动留 `.orig` 备份）。厂商默认 `409600` 页 ≈ 3.2 GB 缓冲池，在 4 GB 机器上会被内核 OOM killer 打掉 `oscar` 进程。
4. **驱动收割**：把介质里的 `drivers/STPython/Python311/*cp311*linux_aarch64.whl` 复制进 `wheels/`、`drivers/aci/arm64/libaci.so` 复制进 `aci/`。两者都已预先放进本包，正常情况只会打印"已在包内且一致"。

**幂等语义**（可反复执行，不会把已装好的库搞坏）：

- 检测到 `$ST_INSTALL_PREFIX/bin/oscar` 存在 → 跳过 `setup`，不再要求 `vendor/` 有介质；
- 端口 `2003` 已在监听 → 跳过建库/初始化/启动整段；
- 物理库已存在 → 跳过 `CREATE DATABASE`（否则会 FATAL "物理数据库已经存在" 并 exit 2）；
- 介质名含 `Gauss` 的包（ShenTongGauss，openGauss 系）**会被显式跳过**，它没有 izpack `setup`，混进 `vendor/` 会被误选。

正因为全程幂等，**新机第一次装如果卡在"库没初始化"**（`setup` 成功但 2003 未监听，脚本会打印
`Timeout. Manually check whether the service starts successfully.`），处理办法只有一个：
**再执行一次 `sh install-all.sh`**（或单独 `sh shentong/install-shentong.sh`）。第二次进来时 `setup` 已被跳过，
脚本直接补做 `restrict` / `install` / `dbcontrol start` 与驱动收割，几分钟内把库拉起来；本包的验收过程
就是这么从"装完不起来"修到 `CONNECT_OK` 的（见 `PATCH-NOTES.md` §七 3）。

### 数据库已经在这台机器上跑着（本包不负责装库）

把 `deploy.conf` 改成 `INSTALL_SHENTONG=no`，`install-all.sh` 就不会碰数据库，只做镜像与容器。
此时只需要把连接四项填对：

```
ST_HOST=<神通监听地址>     # 库在同一台机器上填 127.0.0.1（容器内会自动重映射成 172.17.0.1）
                           # 库在另一台机器上直接填它的 IP，不做重映射
ST_PORT=2003
ST_USER=SYSDBA
ST_DB=OSRDB
```

驱动已经在 `wheels/` 与 `aci/` 里，构建镜像时自动打进，**不需要再向厂商索取任何文件**。
只有在你要连的库版本与本包 ACI 版本差异很大、`import STPython` 报符号缺失时，才需要从**该库自己的安装目录**
`$SZ_OSCAR_HOME/drivers/aci/arm64/libaci.so` 换一份（见 `aci/README.md`），或退一步挂载宿主机目录：

```
-v /opt/ShenTong/drivers/aci/arm64:/opt/st-mcp/aci:ro
```

（容器内 `LD_LIBRARY_PATH` 已指向 `/opt/st-mcp/aci`，挂载即生效。）

## 四、部署结果

端点路径由 `deploy.conf` 的 `TRANSPORT` 决定，两种二选一（`install-all.sh` 结束时也会把实际端点打印出来）：

```
TRANSPORT=sse              -> http://<本机IP>:8100/sse
TRANSPORT=streamable-http  -> http://<本机IP>:8100/mcp/<token>   # 路径取自 MCP_PATH
docker logs -f st-mcp      # 运行日志；首行 "ST-MCP: 启动 st-mcp ..." 就是实际生效的参数
```

本包默认是 streamable-http + 单端口多租户：`install-all.sh` 结束后会生成
`tenants/tenants.json`（含主账号 `default`），并打印它的完整地址。
不带 token 访问 `/mcp` 返回 **401**；`GET /healthz` 不需要 token，用于探活。

**主实例启用租户注册表后只支持 streamable-http**，改回 sse 会在启动时被拒绝。
只会讲 sse 的老客户端，另建一个不挂注册表的实例（见第五节末「一台机器跑多个 ST-MCP」）。

## 五、客户端接入

**Streamable HTTP（单端点，本包默认）**

```json
{
  "mcpServers": {
    "shentong": { "url": "http://127.0.0.1:8100/mcp/<token>" }
  }
}
```

`<token>` 来自 `tenants/tenants.json`（或 `tools/list-tenants.sh --base http://127.0.0.1:8100/mcp`）。
也可以把地址写成 `/mcp`，再加请求头 `Authorization: Bearer <token>` 或 `X-ST-Token: <token>`。

**SSE（长连接；只支持 sse 的老客户端，需把 `TRANSPORT` 改回 sse 或另建 sse 实例）**

```json
{
  "mcpServers": {
    "shentong": { "url": "http://127.0.0.1:8100/sse" }
  }
}
```

两者**不能混用**：服务端一次只监听其中一种。下面是实测的状态码矩阵（`POST` 指客户端发 initialize）：

| 现象 | 容器实际在跑的 | 结论 |
|------|----------------|------|
| `POST /sse` → **405** | sse | 客户端用的是 streamable-http，`/sse` 只接受 GET：改 `TRANSPORT=streamable-http` 重跑 |
| `GET /sse` → **200** | sse | 正常 |
| `POST /mcp/<token>` → **200** | streamable-http + 租户注册表 | 正常 |
| `POST /mcp` → **401** | streamable-http + 租户注册表 | 正常：没带 token，改用 `list-tenants.sh` 打印的地址 |
| `POST /mcp/<错 token>` → **401** | streamable-http + 租户注册表 | token 不在注册表、租户被删或 `enabled=false` |
| `GET /mcp/<token>` → **406** | streamable-http | 属正常——它只收 POST，别用浏览器/curl 直接 GET 探活 |
| `GET /healthz` → **200** | 新版 | 正常，探活用这个 |
| 任意 `/mcp` → **421** | streamable-http，但镜像是旧的 | 旧镜像的 Host 校验只放行 127.0.0.1/localhost：重跑 `sh install-all.sh` 重建镜像 |

**stdio（客户端自己拉起容器，不占端口）**

```json
{
  "mcpServers": {
    "shentong": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "--env-file", "/绝对路径/secrets/st-mcp.env",
               "-e", "ST_HOST=127.0.0.1", "-e", "ST_USER=SYSDBA", "-e", "ST_DB=OSRDB",
               "st-mcp:0.1.0",
               "--transport", "stdio", "--access-mode", "restricted"]
    }
  }
}
```

**不要写 `--entrypoint st-mcp`**：默认入口 `docker/entrypoint.sh` 负责把 `ST_HOST=127.0.0.1` 重映射成宿主机网桥
`172.17.0.1`，覆盖入口会让容器去连它自己的 127.0.0.1，必然 `CONNECT_FAILED connection refused`。

### 补充：一台机器跑多个 ST-MCP（各连一个神通库）

一个镜像 `st-mcp:0.1.0` 可以起任意多个容器，每个容器连一个库、占一个 MCP 端口。
每个实例 = `instances/<名字>.conf`（连接参数 + 端口 + 容器名）+ `secrets/<名字>.env`（该库的口令）：

```sh
# 第 1 个：连 10.20.30.41 上的 ERP 库（MCP 端口自动挑空闲的，默认从 8100 往后找）
sh scripts/new-instance.sh erp 10.20.30.41
vi secrets/erp.env                       # 填这个库的口令
sh install-all.sh erp                    # 部署 + 验收（只动 st-mcp-erp 这一个容器）

# 第 2 个：连 10.20.30.42 上的核心库，指定库端口/库名/用户/MCP 端口
sh scripts/new-instance.sh core 10.20.30.42 2003 CORE SYSDBA 8102
vi secrets/core.env
sh install-all.sh core

sh scripts/list-instances.sh             # 看所有实例：容器状态、连接目标、端口、口令是否就位
sh scripts/verify.sh core                # 单独验收某个实例
docker rm -f st-mcp-core                 # 只停这一个实例，其它实例不受影响
```

规则与限制：

- **实例的 `INSTALL_SHENTONG=no`**（`new-instance.sh` 已写死默认）——实例只连库不在本机装库；
  本机要装库请走主实例（场景 A）。
- **实例的传输方式**由环境变量决定，默认与主实例一致（`streamable-http`）：
  `TRANSPORT=sse sh scripts/new-instance.sh legacy 10.20.30.41` 给老客户端起一个走 `/sse` 的实例。
  生成完会把该实例的完整 URL（`http://<本机IP>:<端口>/mcp`）打印出来。事后想换：编辑 `instances/erp.conf` 的
  `TRANSPORT` 再 `sh install-all.sh erp`。
- `CONTAINER_NAME`（自动为 `st-mcp-<名字>`）与 `HOST_PORT` 必须每个实例唯一。端口撞了的话
  `install-all.sh` 会在起容器**之前**直接报错并告诉你是哪个容器占的，不会出现"静默把别人容器删掉"。
  主实例本身也受这条保护：`sh install-all.sh` 不会去动 `st-mcp-erp`。
- 客户端每个实例一条配置，URL 换成各自端口（路径按该实例 `TRANSPORT` 取 `/sse` 或 `MCP_PATH`）：

```json
{
  "mcpServers": {
    "shentong-erp":  { "url": "http://127.0.0.1:8101/mcp" },
    "shentong-core": { "url": "http://127.0.0.1:8102/mcp" }
  }
}
```

- 实例是"一个实例 = 一个库账号"，**不挂租户注册表**；同一个库上给多个用户开账号请用
  `tools/create-tenant.sh`（单端口多租户，见交付夹 `05-多租户.md`），不要为加用户新建实例。

- 各实例的口令独立轮换、独立重启；`secrets/<名字>.env` 权限 600，配置文件里**不含口令**。
- 连远端库的机器**不需要装神通客户端**：驱动与 `libaci.so` 已打进镜像。只要网络能到对方的
  `ST_PORT`（默认 2003）即可，必要时让对方防火墙放行该端口。

## 六、账号密码支持特殊符号（本包的关键改动）

旧版把 `user/password@host` 整串交给神通 C 扩展解析，密码里的 `@ : / # %` 会被当成结构分隔符，导致连不上或连错库。
新版在 Python 侧解析连接串，**永远**以 `connect(user=..., password=..., dsn="host:port/db")` 调用驱动，密码不再被二次拆分。

规则：

1. **不要**把口令写进 `deploy.conf`，只写 `secrets/st-mcp.env` 的 `ST_PASSWORD=`。
2. 等号后**原样**取值：不加引号、不转义。密码含 `@`、`:`、`/`、`#`、`%`、`!`、空格、中文均可。
   例：`ST_PASSWORD=Zz#kjt/2026@测试!p7cur:e%40?q`（文档与自测里出现的都是这条合成样例，不是任何环境的真口令）
3. 唯一限制：密码不能含**换行**；行首不能是 `#`（那样会被当成注释行）。
4. 若要改用户名/端口/库名，编辑 `deploy.conf`；口令文件只放口令。

数据库侧改口令的语法是 `ALTER USER SYSDBA WITH PASSWORD '<新口令>'`（**不是** `IDENTIFIED BY`，
后者在神通里只用于 DATABASELINK / WALLET / `EXEC SQL CONNECT`）。默认口令 `<厂商默认口令>`，
受 `MinPasswordLen` 约束，太短会被拒。

改口令用包内脚本，不要手敲 `python -c "..."`（会把口令送进 shell history 与 `ps`，特殊符号还要被 shell 二次解析）：

```sh
vi /tmp/ST_PASSWORD_NEW        # 只写一行新口令
sh set-sysdba-password.sh      # 期望 PW_OK；成功后 secrets/*.env 自动更新为新口令
```

脚本用镜像里的驱动连库，改完再用新口令回连 `SELECT 1` 验证，验证不过不会改写 `secrets/*.env`；
临时文件用后即 `shred -u`。细节见 `RUNBOOK.md` 第 3 节。

改完口令**要重跑 `sh install-all.sh`**（`docker restart` 不会换容器里的环境变量）。
`verify.sh` 在这种情形下仍会 `22 PASS`（第 5 组是新起容器读 env 文件，不查长期容器的环境变量），
所以别把 22 PASS 当成"容器已带新口令"的证据。

验证方式（不需要业务 SQL）：

```sh
docker run --rm --env-file secrets/st-mcp.env \
  -e ST_HOST=127.0.0.1 -e ST_USER=SYSDBA -e ST_DB=OSRDB \
  st-mcp:0.1.0 --test-connection --transport stdio
# 期望输出 CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB (password hidden)
```

代码层面的改动点、原版为什么会解析错、以及 16 项回归测试的跑法，见 `PATCH-NOTES.md`。

## 七、故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `verify.sh` 报 `[FAIL] 缺少 STPython 轮子` / `缺少 aci/libaci.so` | 包被裁剪过，从原始交付包补回 `wheels/STPython-*-cp311-cp311-linux_aarch64.whl` 与 `aci/libaci.so` |
| `CONNECT_FAILED ... module STPython not found` | 镜像是旧版（无驱动）。`docker rm -f st-mcp && IMAGE_MODE=build sh install-all.sh` 重建 |
| `CONNECT_FAILED ... libaci.so` / `undefined symbol` | ACI 与被连库版本不一致；用该库安装目录里的 `drivers/aci/arm64/libaci.so` 替换 `aci/libaci.so` 后重建镜像 |
| `CONNECT_FAILED invalid username/password` | 口令被改过或被加了引号；按 §六 原样重填 |
| `CONNECT_FAILED connection refused` | 神通未启动或端口非 2003；`/etc/init.d/oscardb_OSRDBd status` 或 `dbcontrol status /opt/ShenTong OSRDB`；库在宿主机而 `ST_HOST=127.0.0.1` 时，入口脚本重映射成 `ST_HOST_GATEWAY`（默认 `172.17.0.1`，网桥地址不同就改 `deploy.conf`） |
| 装库时报 `数据库还未进行初始化`、2003 不起 | 漏了 `oscar -o restrict` + `oscar -o install`；重跑 `sh shentong/install-shentong.sh`（幂等，会自动补齐） |
| `oscar` 进程被 killed、内核日志 `Out of memory: Killed process … (oscar)` | 缓冲池按厂商默认 409600 页（≈3.2 GB）太大；`admin/OSRDB.conf` 尾部应有本脚本追加的小内存段，改后 `dbcontrol restart` |
| `$INSTALL_PREFIX 已存在且非空，但没有可执行的 bin/oscar` | 上一次安装被中断；清掉该目录或换 `ST_INSTALL_PREFIX` 重跑（脚本不会覆盖已有目录） |
| `Package 'st-mcp' requires a different Python: 3.11.x not in '>=3.12'` | 用了旧版 `wheels/st_mcp-0.1.0-py3-none-any.whl`；本包已把 `requires-python` 下限改到 3.11（驱动只发布到 cp311） |
| 端口 8100 被占 | 改 `deploy.conf` 的 `HOST_PORT` 后重跑 `sh install-all.sh` |
| 想只读部署 | `deploy.conf` 设 `ACCESS_MODE=restricted`（拒绝写 SQL，30 秒超时） |
| 重装/换轮子 | `docker rm -f st-mcp && IMAGE_MODE=build sh install-all.sh` |

## 八、许可与版本事实（现场常被问到）

| 项 | 值 |
|---|---|
| 数据库版本 | OSCAR **V7.0.8.191204** aarch64（`ShenTong7.0.8_342.185_arm64.zip`，官网 ARM64 介质） |
| 许可 | 介质自带**试用许可**，版本串含 `(license invalid after 90 days)`；正式授权需向厂商索取 license 文件替换 |
| Python 驱动 | `STPython 2.0.19`，轮子 ABI 标签 `cp311-cp311-linux_aarch64`；介质 `drivers/STPython/` 里最高只到 **Python311**，所以基础镜像固定 `python:3.11-slim`，`pyproject.toml` 的 `requires-python` 下限同步改为 `>=3.11` |
| 依赖轮子 ABI | `cffi`、`pydantic_core`、`rpds_py` 均为 aarch64/**cp311** 构建，与基础镜像严格对应 |
| ACI | `drivers/aci/arm64/libaci.so`，`ldd` 只需 `pthread/stdc++/dl/gcc_s/libc/libm`（Debian slim 自带，无需额外安装） |
| 官方另有 | `V7.0.21 aarch64` 是 **ShenTongGauss**（openGauss 分支），不是 OSCAR，无 izpack `setup`，本包不支持（`install-shentong.sh` 会显式跳过该介质） |
