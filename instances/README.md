# instances/ —— 一台机器同时连多个神通库

这个目录里每个 `<名字>.conf` 就是一个独立的 ST-MCP 实例：各自连接一个神通数据库、
各自占用一个 MCP 端口、各自一个容器名，互不干扰，共用同一个镜像 `st-mcp:0.1.0`。

| 实例 | 配置文件 | 口令文件 | 部署命令 |
|------|----------|----------|----------|
| 主实例 | `../deploy.conf` | `../secrets/st-mcp.env` | `sh install-all.sh` |
| `<名字>` | `instances/<名字>.conf` | `secrets/<名字>.env` | `sh install-all.sh <名字>` |

新增一个实例（不用手写这些文件）：

```sh
sh scripts/new-instance.sh erp 10.20.30.41          # 名字 + 库地址，其余用默认值
vi secrets/erp.env                                   # 填一行 ST_PASSWORD=
sh install-all.sh erp                                # 部署 + 自动验收
sh scripts/list-instances.sh                         # 看所有实例的状态与端口
```

要点：

- 实例的 `INSTALL_SHENTONG` 默认是 `no`——**实例只连库，不在本机装库**；要装的库请用主实例流程。
- `HOST_PORT` 与 `CONTAINER_NAME` 必须每个实例唯一（`new-instance.sh` 会自动保证；
  手工编辑时若撞端口，`install-all.sh` 会在启动前直接报错并指出被哪个容器占用）。
- 停用某个实例：`docker rm -f st-mcp-<名字>`（只删这一个容器）。
- 某个实例的口令轮换后：`sh install-all.sh <名字>` 重启该实例即可，其它实例不受影响。
