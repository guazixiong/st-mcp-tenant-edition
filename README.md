# ST-MCP（租户版 / Tenant Edition）

> 这是 ST-MCP 的 **rev.10「单端口 + 每租户 token」** 方案的完整快照，作为历史版本归档保留。
> 新需求（客户端在请求头/URL 里直接指定目标库）请用主仓库
> [`st-mcp`](https://github.com/guazixiong/st-mcp)：它保留了本文档的全部部署方式，并增加了 **动态连接**。

把神通数据库（ShenTong / OSCAR）暴露成 MCP 工具。一个容器、一个端口（默认 8100），
`/mcp/<token>` 或 `Authorization: Bearer <token>` 决定用哪个数据库账号，
**隔离由数据库账号权限保证**，服务端只做路由。

    list_schemas · list_objects · get_object_details · execute_sql · get_performance_metrics
    explain_query · analyze_db_health · get_top_queries · analyze_workload_indexes · analyze_query_indexes

## 这一版的特点

- **加租户不重启**：注册表 `tenants/tenants.json` 按 mtime 热加载；删掉记录，token 立即 401。
- **加租户不开端口**：所有租户共用 8100，只发 URL，不用给每人映射容器端口。
- **管理界面**：`st-mcp-admin.py` 零依赖 Web 页，建/删租户、查看 MCP 地址；
  本机免登录，远程必须 HTTP Basic，可加来源网段白名单。
- **宿主机没装 python3 也能跑**：注册表脚本与 Web 页需要 Python 3；
  离线环境可用 python-build-standalone 的独立解释器（见 `docs/06-内网部署一页纸.md`）。

## 目录

| 路径 | 内容 |
|---|---|
| `install-all.sh` | 一键部署：建镜像（或 load）、起容器、注册表就位、自验收 |
| `deploy.conf` | 唯一需要改的部署参数（不含口令） |
| `secrets/st-mcp.env.example` | 口令文件模板；复制成 `secrets/st-mcp.env` 并 `chmod 600` |
| `src/` | ST-MCP Python 源码（含 `tenancy.py` 单端口多租户路由） |
| `docker/` | 离线镜像 Dockerfile 与入口脚本 |
| `scripts/` | 构建镜像、验收、实例管理、注册表工具 |
| `tools/` | 租户创建/删除/列举、管理界面、探针、排障脚本 |
| `docs/` | 多租户说明、内网部署一页纸、原版 README |
| `wheels/` `vendor/` `aci/` `images/` | 离线依赖放置位置（**仓库不含**厂商文件与大二进制） |

## 快速开始

```sh
cp secrets/st-mcp.env.example secrets/st-mcp.env
vi secrets/st-mcp.env          # 只填 ST_PASSWORD
chmod 600 secrets/st-mcp.env

sh install-all.sh              # 期望末行：验收汇总 N PASS / 0 FAIL
sh tools/create-tenant.sh -t tenant_alice -D "$PWD"
```

`create-tenant.sh` 会打印该租户的 MCP URL：

```json
{ "mcpServers": { "shentong": { "url": "http://<服务器IP>:8100/mcp/<token>" } } }
```

管理界面（可选，常驻）：

```sh
sh tools/install-admin-service.sh     # systemd，开机自启，0.0.0.0:8001 + 登录
```

## 安全模型（请先读）

- **token 就是账号凭证**：拿到 URL 能查该账号可见的所有数据；不要把管理页或 URL 贴到公开渠道。
- token 走 URL 时会出现在浏览器历史与代理日志里；能用 `Authorization: Bearer` 的客户端优先用头。
- 管理页会展示库口令与 token，因此**来源限制比口令强度更重要**：
  远程访问必须配置 `ADMIN_USER/ADMIN_PASSWORD`，推荐再加 `ADMIN_ALLOW_CIDR`。
- 本方案依赖数据库账号权限做隔离；服务端不做 SQL 级租户过滤。

## 不包含什么

- 神通数据库介质、闭源 STPython 驱动、`libaci.so`（见 `vendor/PUT-FILES-HERE.md`）。
- 预构建镜像与全部离线 wheel（体积原因）；用 `tools/fetch-offline-deps.sh` 或手工放入。

## 许可

MIT，见 `LICENSE`。`src/` 基于厂商交付代码改造，来源与边界见 `NOTICE.md`。
