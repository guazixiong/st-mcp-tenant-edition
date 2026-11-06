# ST-MCP — 租户版

**一个 MCP 端点，一个 token 对应一个数据库账号。** 本版是
[ST-MCP](https://github.com/guazixiong/st-mcp) 的多租户版本：单个端口服务多个
神通（OSCAR）数据库账号，调用方携带 token，服务端按 token 路由到对应账号。
所有租户共用 `8100` 端口，隔离由数据库账号权限保证，MCP 层只做路由。

[English](README.md) | 简体中文

![租户管理界面](docs/images/admin-console.png)

管理界面列出全部租户、数据库账号、访问模式，并给出可直接粘贴的 MCP 地址。
界面文案为中文；HTTP API 与 MCP 端点与语言无关。

- **加租户不重启** —— `tenants/tenants.json` 按 mtime 热加载；删除记录后该 token
  立即返回 `401`。
- **加租户不开端口** —— 所有租户共用一个端点。
- **管理界面** —— 用包内 Python 运行，可建/删租户、复制客户端配置。
- **离线发布包** —— Linux arm64、Linux x86_64、Windows x64，自带便携 CPython，
  不需要系统 Python，也不需要联网。

每个租户可用的工具：

```
list_schemas · list_objects · get_object_details · execute_sql
get_performance_metrics · explain_query · analyze_db_health · get_top_queries
analyze_workload_indexes · analyze_query_indexes
```

## 运行要求

- 可访问的神通 / OSCAR 服务器（默认 TCP `2003`），以及每个租户一个数据库账号。
- **真实 SQL 需要厂商驱动**：闭源的 `STPython` 与 `libaci.so`（Linux）/
  `ACI.dll`（Windows）**不在本仓库中**。没有它们服务也能启动、能完成 MCP 握手，
  但 SQL 工具会返回错误。见 [`vendor/PUT-FILES-HERE.md`](vendor/PUT-FILES-HERE.md)。
- Linux glibc ≥ 2.28 或 Windows x64；Linux 发布包自带便携 CPython 3.11，Windows 包自带
  便携 CPython 3.13。
- 只有容器部署（`install-all.sh`）需要 Docker。

## 从发布包快速开始

每个 Release 提供 5 个包，外加 `SHA256SUMS`：

| 产物 | 内容 |
|---|---|
| `st-mcp-tenant-<ver>-source.tar.gz` | 对应 tag 的完整源码。 |
| `st_mcp-<ver>-py3-none-any.whl` | 通用 wheel，适用于 Python ≥ 3.11。 |
| `st-mcp-tenant-<ver>-linux-arm64.tar.gz` | 源码 + 便携 CPython + 依赖 + `install.sh` / `run.sh` / `admin.sh`。 |
| `st-mcp-tenant-<ver>-linux-x86_64.tar.gz` | 同上，x86_64。 |
| `st-mcp-tenant-<ver>-windows-x64.zip` | 便携 CPython 3.13（依赖已装好）+ `start.bat` / `admin.bat` + 源码。 |

### Linux 包（不需要 Docker）

```sh
tar -xzf st-mcp-tenant-<ver>-linux-x86_64.tar.gz
cd st-mcp-tenant-<ver>-linux-x86_64
sh install.sh
sh admin.sh                       # http://127.0.0.1:8001
# 至少创建一个租户，然后：
sh run.sh                         # http://0.0.0.0:8100/mcp
```

`install.sh` 把随包 CPython 解压到 `./python`，离线安装开源依赖。

### Windows 包

解压后先运行 `admin.bat` 创建租户，再运行 `start.bat` 启动 MCP 服务。两者都从
`tenants\tenants.json` 读取注册表；注册表不存在时 `start.bat` 会报错退出。

### Wheel

```sh
python -m pip install st_mcp-<ver>-py3-none-any.whl
ST_TENANT_REGISTRY=/etc/st-mcp/tenants.json \
  st-mcp --transport streamable-http --sse-host 0.0.0.0 --sse-port 8100 --mcp-path /mcp
```

## 客户端接入

所有租户共用一个端口，token 决定使用哪个账号：

```text
POST http://db-host:8100/mcp/<token>
Authorization: Bearer <token>
X-ST-Token: <token>
```

三者任选其一。MCP 路径上没有有效 token 的请求返回 `401`，除非设置了
`ST_ALLOW_DEFAULT=1` 且 `ST_DEFAULT_TENANT` 指定了租户。`GET /healthz` 是例外：
不需要 token，返回租户数量。客户端配置示例：

```json
{
  "mcpServers": {
    "shentong": {
      "url": "http://db-host:8100/mcp/demo-finance-token-0123456789",
      "headers": { "Authorization": "Bearer demo-finance-token-0123456789" }
    }
  }
}
```

客户端支持请求头时优先用 `Authorization`：token 放在 URL 里会进入浏览器历史和
代理日志。

## 租户注册表

`tenants/tenants.json` 把一个 token 映射到一个数据库账号。可以用管理界面创建，
也可以直接编辑（权限 `600`）。服务端按 mtime 热加载。

```json
{
  "tenants": [
    {
      "name": "tenant_alice",
      "token": "replace-with-at-least-16-random-characters",
      "host": "10.0.0.21",
      "port": 2003,
      "database": "OSRDB",
      "user": "tenant_alice",
      "password": "database-password",
      "access_mode": "restricted",
      "enabled": true
    }
  ]
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `name` | 是 | — | 租户名，文件内唯一。 |
| `token` | 是 | — | Bearer token，至少 16 个字符。 |
| `user` | 是 | — | 该租户使用的数据库账号。 |
| `password` | 否 | 空 | 数据库口令。 |
| `host` | 否 | `127.0.0.1` | 数据库地址。 |
| `port` | 否 | `2003` | 数据库端口。 |
| `database` | 否 | `OSRDB` | 数据库名。 |
| `access_mode` | 否 | `unrestricted` | `restricted` 拒绝写 SQL。 |
| `enabled` | 否 | `true` | 设为 `false` 可停用记录。 |

删除租户会立即移除注册表记录（token 随即失效）并清理该租户的辅助文件，
数据库账号会保留。

## 管理界面

`admin.sh` / `admin.bat` 用发布包自带的 Python 在 `http://127.0.0.1:8001`
启动 Web 管理界面，可以：

- 列出全部租户及其数据库账号、访问模式、客户端地址；
- 开通租户（租户名、账号、口令、地址、端口、库名、模式）；
- 删除租户记录；
- 一键复制可用的 MCP 客户端配置。

访问规则：

- 本机（loopback）请求免登录。
- 远程访问必须设置 `ADMIN_ALLOW_REMOTE=1`（Linux 直接加该环境变量；Windows 在运行
  `admin.bat` 前设置同名变量），**并且**在 `secrets/admin.env` 中配置
  `ADMIN_USER` / `ADMIN_PASSWORD`。缺少口令时带 `--allow-remote` 会拒绝启动
  （fail closed）。
- `ADMIN_ALLOW_CIDR` 可进一步限制远程来源网段。
- 开启远程访问前，把 `secrets/admin.env.example` 复制为 `secrets/admin.env`
  （权限 `600`）并填写账号口令。

开通和删除租户会调用 `tools/` 下的 POSIX shell 脚本：Linux 上 `admin.sh` 会把包内的
`python/bin` 加进 `PATH`，因此不需要系统 Python；Windows 上这两个操作需要 Git for
Windows（`bash.exe`），查看列表和复制客户端配置不受影响。

有 Docker 时，管理界面会找到正在运行的容器，同时创建数据库账号与 token；
没有 Docker 时进入**预览模式**：只写注册表，数据库账号需要手工创建
（界面显示黄色标记）。

管理界面会展示口令和 token。请只监听本机，或在远程访问时同时启用 Basic 认证与
网段限制。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `ST_TENANT_REGISTRY` | `<包目录>/tenants/tenants.json` | 注册表文件。 |
| `ST_ALLOW_DEFAULT` | `0` | 设为 `1` 时，无 token 请求按 `ST_DEFAULT_TENANT` 处理。 |
| `ST_DEFAULT_TENANT` | 未设置 | 无 token 请求使用的租户。租户模式下所有连接都来自注册表，`ST_HOST` / `ST_PORT` / `ST_USER` / `ST_DB` 不生效。 |
| `SSE_HOST` / `SSE_PORT` | `0.0.0.0` / `8100` | MCP 监听地址与端口。 |
| `MCP_PATH` | `/mcp` | MCP 端点路径。 |
| `LOG_LEVEL` | `INFO` | 日志级别。 |
| `ST_HOST_GATEWAY` | 未设置 | 在 Docker 内改写 `127.0.0.1` 目标（默认 `172.17.0.1`）。 |
| `ADMIN_HOST` / `ADMIN_PORT` | `127.0.0.1` / `8001` | 管理界面监听地址。 |
| `ADMIN_ALLOW_REMOTE` | 未设置 | `1` 允许远程访问管理界面。 |
| `ADMIN_USER` / `ADMIN_PASSWORD` | 未设置 | 远程访问的 Basic 认证账号。 |
| `ADMIN_ALLOW_CIDR` | 未设置 | 允许访问管理界面的来源网段。 |

`SSE_HOST`、`SSE_PORT`、`MCP_PATH`、`LOG_LEVEL` 由发布包的包装脚本（`run.sh`、
`start.bat`、`docker/entrypoint.sh`）读取后转成服务端参数；Python 服务端只认这些参数。

### Docker 部署

仓库也保留原有容器流程：

```sh
cp secrets/st-mcp.env.example secrets/st-mcp.env
vi secrets/st-mcp.env                  # 只填 ST_PASSWORD
chmod 600 secrets/st-mcp.env
sh install-all.sh                      # 建镜像、起容器、自验收
sh tools/create-tenant.sh -t tenant_alice
sh tools/install-admin-service.sh      # 可选：管理界面的 systemd 常驻单元
```

## 安全模型

- **token 就是账号凭证。** 拿到它就能查询该数据库账号可见的一切。不要把租户 URL
  或管理界面贴到公开渠道。
- `access_mode: restricted` 会在 MCP 层拒绝写 SQL，语句不会到达数据库；除此之外
  MCP 层不做查询范围隔离，隔离强度仍取决于数据库权限：每个租户使用独立账号，
  按最小权限授权；只读租户设置 `access_mode: restricted`。
- 管理界面会展示口令与 token，来源限制比口令强度更重要。
- 优先用 `Authorization: Bearer`，避免 token 出现在日志里。

## 与动态连接版的关系

[`st-mcp`](https://github.com/guazixiong/st-mcp) 增加了**动态连接**：调用方还可以
通过 `X-ST-DB-*` 请求头或 `st_*` URL 参数按请求指定目标库；在该版本中，租户 token
的优先级高于动态目标。需要把调用方限制在固定账号时用租户版；需要让调用方自选
数据库时用动态版。

## 开发

```sh
PYTHONPATH=src sh -c 'for t in src/test/test_*.py; do python "$t"; done'
sh -n install-all.sh scripts/*.sh
sh .secrets-scan.sh
```

本地重新打包：

```sh
VER=0.1.1 ARM64_PY_TGZ=... X64_PY_TGZ=... \
ARM64_WHEELS=... X64_WHEELS=... WIN_BUNDLE=... \
sh tools/build-release.sh
```

## 许可

MIT，见 [`LICENSE`](LICENSE)。`src/` 基于厂商交付代码改造，来源与边界见
[`NOTICE.md`](NOTICE.md)。
