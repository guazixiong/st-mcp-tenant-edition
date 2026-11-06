# PATCH-NOTES —— 特殊符号口令改造了什么代码

对比基线：厂商交付的 `ST-mcp.zip`（`ST-mcp/src/st_mcp/`，2025-10-23 版）。
本包 `src/` 目录是改造后的完整 Python 源码（纯 Python，无编译扩展），可直接审计。

---

## 一、原版为什么会把口令弄错

`sql/sql_driver.py` 原版 `_create_connection()`（zip 内 89–121 行）有两条路径：

```python
if '/' in self.url and '@' in self.url and '://' not in self.url:
    conn = await loop.run_in_executor(None, connect, self.url)      # 整串交给 C 扩展
else:
    parsed_url = urlparse(self.url)                                  # shentong://u:p@h:port/db
    conn_params = {"user": parsed_url.username, "password": parsed_url.password, ...}
```

三个必然出错点：

1. **整串交给驱动**：STPython 是 cx_Oracle 分支，它自己对 `user/password@host:port/db` 做字符切分，
   遇到口令里的 `/` 或 `@` 就在**第一个**分隔符处切断——口令被截断，剩下的部分被当成主机名。
2. **`urlparse` 的语义**：`shentong://SYSDBA:p@ss@10.0.0.5:2003/OSRDB` 里第一个 `@` 即被视为 userinfo
   的终点，解析出 `password="p"`、`host="ss"`；含 `#` 会被当 fragment、含 `?` 被当 query 截掉。
3. **`%` 的二次解释**：URL 形式下 `%40` 会被 percent-decode，口令里真含 `%` 时反而被吞掉一段。

结论：只要把口令和主机拼在**一条字符串**里交给任何下游解析器，特殊符号就不可靠。

## 二、改造后的三条硬性规则

1. **切分只在 Python 侧做一次**，驱动永远只收到三个彼此独立的字段：

   ```python
   # sql/sql_driver.py:107 _open()
   dsn = spec.dsn()                                   # "host:port/db"，不含任何凭证
   return connect(user=spec.user, password=spec.password, dsn=dsn)     # :117
   ```

   万一某个 STPython 构建只吃位置参数，退到同样拆开的形式（:122），仍然不拼串：

   ```python
   return connect(spec.user, spec.password, dsn)
   ```

2. **口令优先走独立环境变量**，而不是 URI：`ST_PASSWORD` 由 `docker --env-file` 原样读入，
   `--env-file` 按**第一个 `=`** 分割、值不做引号剥离也不认中段 `#`，所以 `@ # % ! : / 空格 中文` 都能原样到达进程环境。
   命令行 `--password` 保留但**不推荐**（会出现在 `ps` 与 shell 历史里）。
3. **URI 形式按字面量切分**（`sql/connstr.py:117 parse_connection_uri`）：先以**最后一个** `@`
   分离 userinfo 与 host（口令里的 `@` 因此无害），口令内部第一个 `:` 为 user/password 分隔（:99），
   host 段再取 `:` 端口、`/` 库名（:75，支持 `[::1]` 方括号 IPv6），并先剥掉 host 段之后的 `?`/`#`。
   只有显式开启 `--url-encoded` / `ST_URI_URL_ENCODED=1` 时才做 percent-decode（默认关闭，`%` 视作字面量）。

## 三、取值优先级与校验

`connstr.py:185 resolve_spec()` 合并三路来源，逐字段优先级：

```
显式命令行参数  >  连接 URI  >  环境变量  >  默认值
ST_HOST / ST_PORT / ST_USER / ST_PASSWORD / ST_DB / DATABASE_URI
```

- 口令是**特判**的：空串 `ST_PASSWORD=` 视为合法（不回落成默认口令），只有完全未提供才报
  `ConnectionSpecError: No password supplied ...`（提示优先用独立变量）。
- 端口做数值与 1–65535 范围校验，非数字直接报错而不是悄悄用默认值。
- 缺 host/user/database 任一为空 → 明确报缺哪项。

## 四、日志与回显绝不泄密

`server.py:52 obfuscate_password()` 改为**先用真实口令做整体替换**再走正则兜底：
口令可能是任意字符，只有按已知值整串替换才不会被 `@`/`/` 之类的字符骗过。
启动与自检打印的是 `ConnectionSpec.redacted()`：

```
Connecting as SYSDBA@10.0.0.5:2003/OSRDB (password hidden)
CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB (password hidden)
```

`--test-connection`（`server.py:581`）就是给部署脚本用的：连接 + `SELECT 1`，成功打 `CONNECT_OK`、
失败打 `CONNECT_FAILED <已脱敏原因>`，退出码 0/1，永不打印口令。

## 五、回归测试（16 项，不需要数据库也不需要厂商驱动）

```
src/test/test_connstr.py         12 项：分隔符优先级 / IPv6 / 空口令 / 优先级与 env / URL 编码模式 / 报错路径
src/test/test_connect_kwargs.py   4 项：用假 STPython 断言驱动只收到拆分字段、口令绝不进 dsn、日志已脱敏
```

在容器里跑（无需宿主机装 Python）：

```sh
docker run --rm -v "$PWD/src":/s:ro --entrypoint sh st-mcp:0.1.0 \
  -c 'cd /s && PYTHONPATH=/s python test/test_connstr.py && PYTHONPATH=/s python test/test_connect_kwargs.py'
```

## 六、内网改代码后怎么生效

本包的 `wheels/` 只含运行时依赖，**没有** `hatchling` 等构建后端，所以在内网重打 `.whl` 不可行。
纯 Python 包可以热替换（挂载覆盖站点目录）：

```sh
# 1) 把改过的源码放到位（例：/opt/st-mcp/src）
# 2) 用 -v 覆盖容器内的 st_mcp 包，其余参数照 install-all.sh
docker run -d --name st-mcp --restart=always \
    --env-file secrets/st-mcp.env -p 8100:8100 \
    -v /opt/st-mcp/src/st_mcp:/usr/local/lib/python3.11/site-packages/st_mcp:ro \
    st-mcp:0.1.0 --transport sse --sse-host 0.0.0.0 --sse-port 8100
```

验证挂载版本已加载：`docker exec st-mcp python -c "import st_mcp,inspect;print(inspect.getsourcefile(st_mcp))"`。
改回原状：去掉该 `-v` 重跑 `sh install-all.sh`。

---

## 七、rev.6（2026-10-16）：把整包从 Python 3.13 切到 3.11，并补全装库流程

这一版是在麒麟 V10 SP3 / aarch64 的机器上**真装真连**之后反向修出来的，改动都不涉及口令逻辑本身。

### 1. 基础镜像与 `requires-python` 降到 3.11

厂商介质的 Python 驱动只发布到 `drivers/STPython/Python311/`（**没有 cp312/cp313**），
所以连库唯一通道 `STPython` 的 ABI 标签是 `cp311-cp311-linux_aarch64`，基础镜像必须跟着降到 `python:3.11-slim`。

- `src/pyproject.toml`：`requires-python = ">=3.11"`，并注明原因（驱动只到 cp311）。
- `docker/Dockerfile.offline`、`scripts/build-image.sh`：`FROM python:3.11-slim`，`images/01-python-3.11-slim.tar.gz` 取代 3.13 归档。
- `cffi` / `pydantic_core` / `rpds_py` 换成 aarch64 **cp311** 构建；`st_mcp-0.1.0-py3-none-any.whl` 的
  `Requires-Python` 用 `tools/rewheel-python-min.py` **原地改写元数据并重建 RECORD**（内网没有构建后端，不能重打轮子）。
- 不降版的后果：`ERROR: Package 'st-mcp' requires a different Python: 3.11.17 not in '>=3.12'`，镜像根本建不起来。
  降版前先确认源码没有 3.12-only API（`py_compile` + 逐 API 扫描），16 项回归测试在 cp311 下全绿。

### 2. ACI 共享库进包

`STPython.cpython-311-aarch64-linux-gnu.so` 运行期要 `libaci.so`。包内新增 `aci/libaci.so`
（2,409,448 字节，取自介质 `drivers/aci/arm64/`），`Dockerfile.offline` 里 `COPY aci/ /opt/st-mcp/aci/`
并 `ENV LD_LIBRARY_PATH=/opt/st-mcp/aci`。`ldd libaci.so` 只需要 `pthread/stdc++/dl/gcc_s/libc/libm`，
Debian slim 自带，不需要额外装任何厂商 RPM。

### 3. `install-shentong.sh` 补齐厂商的三步建库

只跑 `setup` 装完软件，2003 是不会起来的（报"数据库还未进行初始化"）。按介质 `bin/createdatabase.sh` 的
真实顺序补齐：`CREATE DATABASE` → `oscar -o restrict -d OSRDB`（引导系统目录）→
`oscar -o install -d OSRDB`（注册服务，**需 root**，否则 `/etc/init.d` 与 `/usr/lib/systemd/system` Permission denied）
→ `dbcontrol start`。同时全部改成幂等：`bin/oscar` 已存在就跳过 `setup`；端口已监听就跳过整段建库；
物理库已存在就跳过 `CREATE`（否则 FATAL "物理数据库已经存在" + exit 2）。

### 4. 小内存保护

`admin/OSRDB.conf` 厂商默认 `BUF_DATA_BUFFER_PAGES=409600`（≈3.2 GB）+ `SORT_MEM=1204000`，
4 GB 机器上 `oscar` 会被内核 OOM killer 打掉（日志 `Out of memory: Killed process …(oscar)`）。
脚本按标记幂等追加 `65536/65536/200` 覆盖段，首次修改留 `.orig` 备份。注意该文件是**同名键后者生效**。

### 5. `verify.sh` 第 5 组必须走默认入口

之前写成 `--entrypoint st-mcp`，绕过了 `docker/entrypoint.sh` 里"`ST_HOST=127.0.0.1` → 重映射成
`ST_HOST_GATEWAY`（172.17.0.1）"那段，容器去连它自己的 127.0.0.1，必然 `CONNECT_FAILED 无法建立连接`。
现在第 5 组用镜像默认入口、并把 `ST_HOST_GATEWAY` 透传进去，实测输出
`[PASS] CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB (password hidden)`。
§六 的热替换示例与 stdio 客户端配置同样**不要**加 `--entrypoint`。

### 6. 介质识别

`vendor/` 里名字含 `Gauss` 的包是 **ShenTongGauss**（openGauss 分支，V7.0.21 aarch64），
它没有 izpack `setup`，会被脚本显式跳过，避免被当成 OSCAR 介质误选。
OSCAR 侧用的是官网 ARM64 介质 `ShenTong7.0.8_342.185_arm64.zip`，装完版本串为
`神通数据库7.0.8.191204 for Linux(arm 64bit) (65535 connections) (license invalid after 90 days)`
——**自带的是 90 天试用许可**，正式环境要换厂商 license 文件。

### 7. 整包验收（rev.6）

`sh scripts/verify.sh` → **18 PASS / 0 FAIL / 0 SKIP**，其中第 5 组为上面的真实 `CONNECT_OK`；
口令用的是 27 位含特殊符号样例 `Zz#kjt/2026@测试!p7cur:e%40?q`（含 `# / @ 中文 ! : % ?`，合成样例非真口令）。
SSE 端点 HTTP 200，并通过 MCP 真实调用工具：`list_schemas`、`execute_sql`、
`create table` / `insert` / `select count(*)` / `drop table` 全部 `isError=false`。


## 八、rev.7（2026-10-19）：支持一台机器连多个神通库，并把"连哪个库"写进文档

rev.6 之前这份包只回答了一个问题："在本机装一个库并连它"。现场真实的两个诉求没有覆盖：

- **连别的神通服务器**（库不在这台机器上）——`deploy.conf` 里明明有 `ST_HOST`，但所有文档都在讲"只填口令"；
- **一台机器同时连多个库**——`install-all.sh` 里容器名和口令路径都是写死的，第二个实例会把第一个删掉。

rev.7 补齐这两件事，主实例的默认行为**完全不变**（不带参数就还是 rev.6 那套）。

### 1. 实例化的配置文件

| 实例 | 配置文件 | 口令文件 | 容器名 | 部署 |
|---|---|---|---|---|
| 主实例 | `deploy.conf` | `secrets/st-mcp.env` | `st-mcp` | `sh install-all.sh` |
| `<名字>` | `instances/<名字>.conf` | `secrets/<名字>.env` | `st-mcp-<名字>` | `sh install-all.sh <名字>` |

`install-all.sh`、`scripts/verify.sh` 都接受同一个可选实例名参数；实例名会做字符合法性校验，
`st-mcp`/`default`/`main` 被保留、不允许与主实例撞名。

### 2. 三个新脚本

- `scripts/new-instance.sh <名字> <库IP> [库端口] [库名] [用户] [MCP端口]`
  一条命令生成 `instances/<名字>.conf` + `secrets/<名字>.env`（口令留空待填，权限 600），
  并在最后打印"下一步两条命令"。MCP 端口不给就自动从 8100 往后找空闲的。
- `scripts/list-instances.sh` 列出主实例与全部实例：容器状态、连接目标、MCP 端口、口令是否已填。
  每个实例在独立变量空间里读配置，避免上一个实例的端口/库名串到下一个。
- `scripts/common.sh` 新增 `instance_paths` / `port_owner` / `pick_free_port`。

### 3. 端口冲突提前拦住

起容器前检查 `HOST_PORT` 是否被**别的**容器映射着，是就直接报错并指出是哪个容器，
不再出现"两个实例配同一个端口 → 后跑的把先跑的 `docker rm -f` 掉"这种静默互删。

### 4. 实例不装库

`new-instance.sh` 生成的实例配置里 `INSTALL_SHENTONG=no`：实例只负责连一个已经运行着的库。
要在本机装库仍然走主实例（场景 A），避免多实例重复触发装库流程。

### 5. "连哪个库"进了每一份文档

README §三 与 RUNBOOK §2 开头都加了场景表：**A** 本机现装库并连它（默认）；
**B** 连另一台机器上已有的库——改 `ST_HOST/ST_PORT/ST_DB/ST_USER` 四个字段并把
`INSTALL_SHENTONG` 改成 `no`；**C** 一台机器连多个库——每个库一个实例。
B 场景另有三条硬前提：对端 `ST_PORT` 网络可达（防火墙放行）、对端监听地址包含外部网卡
（`0.0.0.0:2003` 而非只对 127.0.0.1）、被连库版本与本包 ACI/驱动一致。

### 6. U 盘入口页与 `deploy.conf` 对齐

`ST-MCP-START-HERE.md` 之前只写了"只填口令 + `sh install-all.sh`"，与 `deploy.conf` 的 4 个连接字段矛盾
（那套默认值连的是**这台机器上新装的库**）。现按 A/B/C 三种场景重写，并把场景 B 的 5 行改法逐行给出。

### 7. 整包验收（rev.7）

环境：麒麟 V10 aarch64（QEMU/TCG）+ docker，机上有一个已建好的 OSCAR V7.0.8 库（监听宿主机 `2003`）。
测试目录为解包后的完整交付包，口令用含 `# @ ! : % /` 与中文的特殊符号口令。

| # | 用例 | 命令 | 结果 |
|---|---|---|---|
| T1 | 主实例回归（行为与 rev.6 一致） | `sh install-all.sh` | 18 PASS / 0 FAIL / 0 SKIP，退出码 0 |
| T2 | 生成实例 | `sh scripts/new-instance.sh erp 172.17.0.1` | 自动选到空闲端口 8101，生成 `instances/erp.conf` + `secrets/erp.env`（600） |
| T3 | 部署实例 | `sh install-all.sh erp` | 18 PASS / 0 FAIL / 0 SKIP，`CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB` |
| T4 | 实例列表 | `sh scripts/list-instances.sh` | 主实例(127.0.0.1/8100) 与 erp(172.17.0.1/8101) 均"运行中 / 口令已填" |
| T5 | 端口撞车拦截 | 把 `instances/erp.conf` 的 `HOST_PORT` 改成 8100 后 `sh install-all.sh erp` | 起容器前即 `ERROR: 宿主机端口 8100 已被容器 st-mcp 占用；请把本实例的 HOST_PORT（或 …/instances/erp.conf 里的）改成空闲端口，再重跑`，退出码 1，**主实例容器 `st-mcp` 仍在运行、未被删除** |
| T6 | 双实例并存 | `curl -N --max-time 8 http://127.0.0.1:<port>/sse` | 8100 与 8101 同时 HTTP 200；`docker ps` 见 `st-mcp`、`st-mcp-erp` 两个容器 |
| T7 | 重名实例拦截 | 连续两次 `sh scripts/new-instance.sh probe 172.17.0.1` | 第二次退出码 1，不覆盖已有配置 |

`sh` 与 `bash` 双跑（同一批脚本、同一台机器）：

| 脚本 | `bash` 实跑 | `sh` 实跑 | `bash -n` | `sh -n` |
|---|---|---|---|---|
| `install-all.sh` | 主实例 RC=0 / 实例 erp RC=0 | 主实例、实例 erp 各 18 PASS | OK | OK |
| `scripts/verify.sh` | 实例 erp RC=0 | 主实例 RC=0 | OK | OK |
| `scripts/list-instances.sh` | RC=0，两行实例信息正确 | RC=0 | OK | OK |
| `scripts/new-instance.sh` | — | RC=0（重名时 RC=1，符合预期） | OK | OK |
| `scripts/common.sh`、`docker/entrypoint.sh` | — | — | OK | OK |

另外对全部脚本做了 bash 专用语法扫描（进程替换 `<(`/`>(`、`[[`、数组下标、`let`、`local -`、`declare`），
命中 0 处——这是 Kylin 上 `/bin/sh` 为 bash 的 sh 兼容模式时必须守住的那条线。

### 8. 补记：一处输出文案修正

T7 之后发现 `new-instance.sh` 结尾"连的库：…"那句把端口写死成 `2003`，传非默认端口时会显示错。
改为使用 `$PORT`，并在真机重跑：

```
$ sh scripts/new-instance.sh demo 10.20.30.77 2109 DEMODB DEMOUSER
客户端连这个实例用：http://<本机IP>:8101/sse
连的库：DEMOUSER@10.20.30.77:2109/DEMODB（本机不装库，要求该地址的 2109 端口网络可达）
```

改的是纯文案，逻辑与其余 5 个脚本一字未动；改后包内与测试机该文件 md5 同为
`2d4cc5500bc31fc0411b1944d8b3c63a`，随后重打终包（见 `ST-MCP-START-HERE.md` 首页的字节数与摘要）。

---

## 三、追加：streamable-http 传输（2026-10-23，overlay 补丁件）

现场反馈客户端连 `/sse` 得到 **405**，实测确认该客户端走的是 streamable-http（它向端点 POST
initialize，而 sse 的 `/sse` 只接受 GET）。本包原先只有 `stdio` 与 `sse` 两种传输，遂新增第三种。

**不涉及重打 754 MB 终包**：改动只有 1 只纯 Python 轮子（`wheels/st_mcp-0.1.0-py3-none-any.whl`，
31,052 字节）加 6 个小文本文件，作为 overlay 覆盖即可；`mcp-1.27.2` 与 `uvicorn-0.54.0` 轮子早就在
`wheels/` 里，没有新增依赖。

| 文件 | 改动 |
|---|---|
| `src/st_mcp/server.py`（打进轮子） | `--transport` 增加 `streamable-http` 取值；新增 `--mcp-path`（默认 `/mcp`，缺前导斜杠自动补）；分支走 `FastMCP.run_streamable_http_async()`（内部即 `uvicorn.run(streamable_http_app())`） |
| `wheels/st_mcp-0.1.0-py3-none-any.whl` | 用上述源码重打，RECORD 全部重算（pip 安装时会校验该摘要） |
| `deploy.conf`、`scripts/new-instance.sh` | 新增 `MCP_PATH=/mcp`；`TRANSPORT` 注释列出三种取值 |
| `docker/entrypoint.sh` | 认 `MCP_PATH`；`MCP_TRANSPORT` 作为 `TRANSPORT` 的别名；仅在 streamable-http 时追加 `--mcp-path`（其它传输传这个参数 argparse 会报错） |
| `docker/Dockerfile.offline` | `CMD` 由写死 `--transport sse` 改为空，参数统一交给 entrypoint 按环境变量拼装，`docker run -e MCP_TRANSPORT=streamable-http` 才真正生效 |
| `install-all.sh` | `docker run` 不再硬编码 `--transport sse`，改传 `-e TRANSPORT/-e MCP_PATH`；新增 `TRANSPORT` 取值校验；结束行打印实际生效端点 |
| `scripts/verify.sh` | 第 1 组增加 uvicorn 轮子在位检查（18 项 → **19 项**）；第 4 组按传输分支：sse 仍判 `GET /sse` 200，streamable-http 判 `POST /mcp` 的 initialize 握手 200 且响应含 `serverInfo` |
| `scripts/common.sh` | 新增 `mcp_endpoint()`，install-all 与 verify 共用同一套端点算法，避免两边判的不是同一个 URL |
| `README.md`、`RUNBOOK.md`、`ST-MCP-START-HERE.md` | 端点示例、切换方法、状态码判读表；`18 PASS` 全部改标 `19 PASS` |

### 一个必须写死的坑：421

`FastMCP("st-mcp")` 是用默认 `host="127.0.0.1"` 构造的，mcp 1.27.2 在这个条件下**自动**打开了
DNS-rebinding 保护，把 `allowed_hosts` 钉成 `127.0.0.1:* / localhost:* / [::1]:*`。而 `main()` 是
在构造之后才 `mcp.settings.host = args.sse_host`，改了 host 并不会重算那份白名单——于是绑 `0.0.0.0`
之后，任何按 IP 连进来的客户端都会收到 **421 Invalid Host header**。新分支在绑定地址非回环时把
`transport_security` 置空来放行（本机回环仍保留原保护）。这个坑只在"从别的机器连"时才现形，
`verify.sh` 用 127.0.0.1 自测是看不到的。

### 实测（本机 1.27.2 轮子 + 重打后的 st_mcp 轮子，`--sse-host 0.0.0.0`）

```
POST /mcp  initialize                 -> 200，响应含 mcp-session-id 与 serverInfo
POST /mcp  带 Host: <现场机IP>:8100 -> 200（421 已规避）
POST /mcp  缺 Content-Type            -> 400
POST /mcp  缺 Accept                  -> 406
GET  /mcp                             -> 406
GET  /sse                             -> 404     ← 旧 verify.sh 判据因此必须分支
--mcp-path st-mcp（无前导斜杠）        -> POST /st-mcp 200、POST /mcp 404
```

跨传输的判读（两套服务各起一个端口实测）：

| 客户端动作 | 容器=sse | 容器=streamable-http |
|---|---|---|
| `POST /sse` | **405** | 404 |
| `GET /sse` | 200 | 404 |
| `POST /mcp` | 404 | **200** |
| `GET /mcp` | 404 | 406 |

`POST /sse → 405` 就是现场那次故障的签名：服务端没问题，是客户端只会讲 streamable-http。

### 尚未处理

待办 #21（rev.8）那三条已确认缺陷与本次改动无关，仍然挂着：建库前的 `ldd` 依赖预检随包 `libs/`、
起库等待改为轮询 90–120 秒并以 root 走 `/etc/init.d` 服务脚本、`oscar -o install -d <库名>`。
是否为此重打 754 MB 终包仍需现场确认，本 overlay 刻意不动终包。

---

## 四、追加二：overlay 的第二轮补齐（2026-10-23 晚）

上面那份 overlay 在真机（麒麟 V10 aarch64）跑通两条连库路径后，补了三处现场真正会碰到的缺口。

| 文件 | 改动 | 为什么 |
|---|---|---|
| `set-sysdba-password.sh`（新增，100 行） | 一条命令改 SYSDBA 口令并同步回 `secrets/<实例>.env` | RUNBOOK 原来给的是手写 `cat > /tmp/setpw.py` 六步，现场要照着抄；且 `docker run` 那条没带 `--user` |
| `install-all.sh` | 完整性校验把 `FAILED open or read`（文件缺失）与摘要不符同等对待；现场删过 `vendor/` 介质时用 `SKIP_CHECKSUM=1` 显式跳过 | 旧版 `sed` 只匹配 `: FAILED` 后紧跟行尾的形态，缺文件时输出是 `path: FAILED open or read`，被过滤成空 → 697 MB 介质少拷一半也照样放行 |
| `scripts/new-instance.sh` | 按 `TRANSPORT=` 生成实例配置，并在结束时打印该实例的完整 URL（`/sse` 或 `/mcp`） | 主实例能切 streamable-http 之后，多实例仍只会生成 sse，现场第二个库必踩 |

### `--user 0:0` 不是可选项

`docker/Dockerfile.offline:41` 是 `USER stmcp`（uid 10001）。脚本把两个口令文件放在
root 的 `/tmp/st-setpw.$$`（0700）里只读挂载，容器内以 10001 跑 `python` 时读不到，第一次实测就是
`PermissionError: [Errno 13] Permission denied: '/pw/OLD'`。当时先怀疑 SELinux，实机
`getenforce` = **Disabled**，再用一个空目录的最小复现（`docker run --user 0:0 -v …:/pw:ro` 读到内容）
确认是 uid 而非 MAC。因此这条 `docker run` 显式 `--user 0:0`，只读两个文件、不回写任何东西。

### 实测（同一台麒麟 V10，两个 ST-MCP 实例并存，不动原有 octop/movo 容器）

```
场景 A 本地库   实例 st-mcp-shhttp  HOST_PORT 8000->8100  ST_HOST=127.0.0.1（入口点改写为 172.17.0.1）
                sh scripts/verify.sh shhttp        -> 19 PASS / 0 FAIL / 0 SKIP
                --test-connection                  -> CONNECT_OK SYSDBA@172.17.0.1:2003/OSRDB
场景 B 远程形态 实例 st-mcp-remote   HOST_PORT 8101        ST_HOST=10.0.2.15（非回环，不做改写）
                sh scripts/verify.sh remote        -> 19 PASS / 0 FAIL / 0 SKIP
                --test-connection                  -> CONNECT_OK SYSDBA@10.0.2.15:2003/OSRDB
真实 MCP 客户端  mcp 1.27.2 initialize                -> HANDSHAKE_OK server=st-mcp 1.27.2 protocol=2025-11-25
                list_schemas / execute_sql         -> isError=False，SELECT VERSION() 返回 7.0.8.191204
跨机连接        Windows runtime/python.exe -> http://<服务器IP>:8000/mcp   （421 已规避）
改口令          sh set-sysdba-password.sh          -> PW_OK，secrets/*.env 内 ST_PASSWORD 已更新
                新口令是 20 位含 # / @ ! 的那条      -> 改完 --test-connection 仍 CONNECT_OK
                旧口令写错时                        -> OLDPW_BAD|OperationalError，且不改写 env
                再改回原口令                        -> PW_OK，env 与改前逐字节相同
完整性校验      临时把 vendor/ 介质改名后跑 install-all.sh -> 报缺失并 die（旧版会放行）
                正常覆盖后                          -> 只有 deploy.conf 一行差异，放行
```

诚实边界：现场并没有第二台数据库机器，"场景 B"是用**非回环的可路由地址**走同一条代码路径验证的；
客户端一侧是 Windows 宿主上的 MCP 客户端连 QEMU 客户机里的服务，走的是宿主 LAN 网卡地址
（非回环，与"从别的机器连"同一条 Host 校验路径），**两台物理机之间还没验过**。
本机 Windows 侧直连不走驱动路线——宿主机没有 `ACI.dll`（`find -iname 'ACI*'` 为空），
STPython 在 x86_64 Windows 上也无轮子。

### 本轮顺带查出来的两个判据盲区（记入 rev.8）

1. **`verify.sh` 测不到"长期容器手里还是旧口令"。** 第 5 组是 `docker run --rm --env-file secrets/…`
   现起一个新容器去连库，读的是**文件**；第 4 组只测 HTTP 握手，不碰数据库。
   于是改完库口令、只 `docker restart` 不重建容器时，验收仍是 `19 PASS`，客户端却报
   `invalid username/password`。本轮在验收机上按这个顺序跑过一遍，确认盲区真实存在。
   现在文档给的判据是 `docker inspect … | grep '^ST_PASSWORD='` 与 `secrets/*.env` 比对；
   rev.8 计划把第 5 组改成先查长期容器的生效环境变量。
2. **收紧后的完整性校验会把"现场精简过的包"判成拷贝不完整。** 库装好后 `vendor/` 介质用不上，
   删掉腾磁盘是合理动作，但那两个文件在 `SHA256SUMS.txt` 里，缺了就 die。
   没有做成"介质类文件降级为 WARN"的特殊分支（判据会变复杂，也容易把真的拷贝缺损放过去），
   改成给一个显式开关：`SKIP_CHECKSUM=1 sh install-all.sh`，并在 RUNBOOK 第 5 节与故障表里写清楚。

## 五、追加三：r3（2026-10-26）—— 中文环境下整包校验静默失效

真机现场实跑暴露：麒麟 zh_CN 下 `sha256sum -c` 把 `OK/FAILED` 印成`确定/失败`，
`install-all.sh:51` 判据抓的是 `: FAILED`，中文机器上一行也抓不到 —— 文件真坏了也只剩一句
`WARN: 整包校验仅存在现场配置类差异，继续部署`，2026-10-23 那次"缺文件也要中止"的加固等于白做。

改一处：调用处钉英文 locale。

```sh
SUMOUT=$( cd "$PKG" && LC_ALL=C sha256sum -c SHA256SUMS.txt 2>&1 ) || SUMRC=$?
```

判据里凡是解析**命令输出**的英文关键字都得钉 locale（我们自己的 `CONNECT_OK` 这类输出不受影响，
`verify.sh` 全量查过没有别处踩这个）。验收 VM 是英文环境，两轮验收都没暴露它 ——
基线偏差又一次是现场才查出来的。

现场当时是手打 sed 补的并把新摘要写回它自己的 `SHA256SUMS.txt`；r3 已内含，后续机器不用手打。

---

## 六、rev.9（2026-10-26）：r3 合进包体，主实例默认切到 streamable-http

真机跑通之后把覆盖件的内容并回交付包本体，新机器解包即终态，不再需要"先解包再打覆盖件"两步。
代码改动只有 r3 已验过的那些；这一节记的是**默认值**变化：

| 文件 | 改动 | 为什么 |
|---|---|---|
| `deploy.conf` | `TRANSPORT=sse` → `streamable-http`（`MCP_PATH=/mcp` 上一版已在） | 现场客户端只会讲 streamable-http，连 `/sse` 得 **405**；默认值应当是多数客户端能直接用那个 |
| `docker/entrypoint.sh` | 未注入环境变量时的兜底同改为 `streamable-http` | 兜底和文档默认不一致时，`docker run` 手起的容器会悄悄跑成 `/sse` |
| `scripts/new-instance.sh` | 实例兜底 `T=${TRANSPORT:-sse}` → `streamable-http` | 与主实例保持一致，别再要求"想换新协议就多敲一个环境变量" |
| `README.md` / `RUNBOOK.md` | 客户端章节主次对调；`install-all.sh` 期望输出样例改为 `传输 streamable-http，端点 /mcp` 与 `部署完成：实例 主 的 MCP 端点 http://127.0.0.1:8100/mcp`（这两行照 `install-all.sh:179,197` 的实际格式抄） | 操作卡的判据是逐行比对的，样例错了等于把现场往错方向领 |

退回 sse 的写法在 README 第四节和 RUNBOOK 第 7 节；只会讲 sse 的老客户端推荐用
`TRANSPORT=sse sh scripts/new-instance.sh legacy <库IP>` 单独起实例，主实例不动。

另外两条是**文档纠错**（脚本本身没改）：RUNBOOK 的期望输出样例以前把结束行写成 `部署完成：MCP 端点 …/sse`，
漏了脚本实际打印的 `实例 主 的` 前缀（`install-all.sh:197`）；`curl-mcp-probe.sh` 的参数是**完整 URL** 不是端口，且它不打印
`HANDSHAKE_OK`，判据以 `"isError":false` + 版本行为准。

复跑记录：16 项自测全过（`src/test/`），九个脚本 `sh -n` 与 `bash -n` 双过，
`LC_ALL=C sha256sum -c SHA256SUMS.txt` 71 条全部 `: OK`。

---

## 七、rev.10（2026-11-01）：多租户从"一人一容器"改成"一个端口、一人一 token"

### 改的是什么

rev.9 以前，"多用户"的做法是每个用户起一个容器、各占一个宿主机端口（8100/8101/…），
客户端配置里复制的是整段 `{url, port}`。运维要记 N 个端口，端口撞了还会互相顶掉容器。
rev.10 把服务端改成**单进程单端口**：

- 所有租户共用一个容器（默认 `8100`），一个 MCP 端点 `/mcp`；
- **token 区分租户**：`http://<IP>:8100/mcp/<token>`，或地址填 `/mcp` + 请求头
  `Authorization: Bearer <token>` / `X-ST-Token: <token>`；
- 每个租户在注册表里是一条 `token → 神通库账号` 记录，服务端按注册表文件 mtime
  热加载，**加/删租户不重启、不开端口**；
- 隔离靠库账号自己的权限：MCP 只负责把请求路由到该账号的连接池，查询走
  `ALL_*` 视图（按当前账号权限过滤），账号看不到别人的 schema。MCP 层不做数据过滤，
  **建账号时不给额外授权**是隔离的唯一前提。

### 代码与脚本改动

| 文件 | 改动 |
|---|---|
| `src/st_mcp/tenancy.py`（新增） | 注册表加载/热更新、token 解析（路径优先，其次 Bearer / X-ST-Token，两者不一致拒绝）、`Mcp-Session-Id` 绑定租户、`GET /healthz` 免鉴权、每租户独立 `DbConnPool` |
| `src/st_mcp/tenancy.py:_connect_host` | 容器内把注册表里的 `127.0.0.1/localhost/::1` 改写为 `ST_HOST_GATEWAY`（默认 `172.17.0.1`），与 `entrypoint.sh` 同规则。**不修这个，`create-tenant.sh` 用回环地址写的注册表在容器里会连自己**，报 `无法建立连接` |
| `src/st_mcp/server.py` | 有 `ST_TENANT_REGISTRY` 时套租户中间件；工具从请求 scope 头 `x-st-tenant-resolved` 取租户（客户端伪造的同名头先被剥掉）。工具跑在 session manager 的任务里，看不到中间件的 ContextVar，所以不能靠 ContextVar 传 |
| `install-all.sh` | **只有主实例**挂 `-e ST_TENANT_REGISTRY=/data/tenants.json -v $PKG/tenants:/data:ro` 并初始化 `default` 租户；多实例（`install-all.sh <实例>`）保持原样：单库单账号、不挂注册表。以前是无条件挂载，实例会被迫进租户模式、注册表还指向主库 |
| `scripts/verify.sh` | 新增租户模式三项：容器已启用注册表 / 无 token 返回 401 / `/healthz` 返回 200；URL 只在租户模式下拼 token。验收总数 19 → **22 PASS**。实例模式的 SKIP 文案单独写，不再提示"旧镜像" |
| `src/st_mcp/*` 其余 | 工具实现改为 `current_tenant` 取连接池；无注册表时行为与 rev.9 完全相同（单库单账号） |

交付夹（`ST-MCP-DELIVERY-20261026/`）新增/重写的工具：

- `tools/create-tenant.sh`：生成库口令（18 位）+ token（24 位 URL 安全字符）→ 用主容器里的
  STPython 驱动建库账号 → 写 600 注册表 → 打印 URL 与客户端 JSON。**不建容器、不改 `deploy.conf`**；
- `tools/list-tenants.sh`：列表 / `--base` 只打印 base URL；口令默认打码；
- `tools/delete-tenant.sh`：删注册表记录（该 token 立即 401），库账号保留并打印
  `DROP USER ... CASCADE` 让人自己决定；
- `tools/create-db-user.sh`：新增 `--password-stdin`，口令从标准输入读，不进命令行/进程列表：
  `python3 -c '...' | sh tools/create-db-user.sh ... --password-stdin`。

顺手修掉的一个坑：三个脚本里的 `conf_get` 原来写成 `[ -f "$1" ] && sed ...`，
文件不存在时返回 1，被 `set -e` 静默中止，表现为"一条命令下去什么都没打印、rc=1"。
现在无论如何 `return 0`。

### QEMU 实测（麒麟 ARM64 + 神通 V7.0.8，2026-10-31）

镜像重打（wheel 内已含 `_connect_host`），主实例 `verify.sh` **22 PASS / 0 FAIL**，
随后在同一容器上：

- `tenant_demo` 的 `list_schemas` 只返回 `TENANT_DEMO`；主账号建 `DEMO_SECRET` 后，
  租户查 `DEMO_SECRET` 返回 `Relation "DEMO_SECRET" does not exist`，`ALL_TABLES` 为 0；
- 错 token 401、不带 token 401、`GET /healthz` 200、带 token 的 `GET /mcp/<token>` 406；
- 新租户写进注册表后 1 秒内新 URL 握手 200（容器未重启）；`delete-tenant.sh` 之后
  原 URL 立即 401；
- URL 形式与 `Authorization: Bearer` 形式都能用；同 session 跨租户复用被 401。

### 现场迁移（旧部署可直接废弃）

用户已确认"按新流程来，旧的历史数据不要"。迁移步骤见 `05-多租户.md` 末节：

1. 主实例重装/升级到 rev.10 镜像，`verify.sh` 到 22 PASS；
2. 对每个旧用户跑一次 `create-tenant.sh -t <名字> -u <原库账号> --no-db-user`
   （账号复用，不重建）；
3. 新 URL 能查库后再删旧容器 `st-mcp-tenant_*`，旧 `instances/ secrets/` 可删。
   **删容器/删账号是不可逆动作**，逐条确认后执行，本文不代为删除。

### 已知边界（没做，别当成做了）

- 没有 per-tenant 限流/配额：一个租户的慢查询会占住自己的连接池，不影响别人的连接，
  但会占 CPU/内存。
- token 放在 URL 里会进代理日志、浏览器历史，**当成口令保管**；泄露就
  `create-tenant.sh -t <租户> --token <新 token>` 轮换（同名租户默认保留原 token，
  换 token 必须显式给 `--token`）。
- 管理界面（`st-mcp-admin.py`）显示明文口令与 token，只监听本机时最安全；
  远程看请走 SSH 隧道。
- 多实例（`new-instance.sh`）仍然是"一个实例一个库、一个账号"，不参与租户路由。
  多用户场景统一用 `create-tenant.sh`，别再给每人起实例。
