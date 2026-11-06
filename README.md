# ST-MCP — Tenant Edition

**One MCP endpoint, one token per database account.** This edition of
[ST-MCP](https://github.com/guazixiong/st-mcp) serves many ShenTong (OSCAR)
database accounts from a single port. The caller supplies a token; the server
routes the request to the matching database account. All tenants share port
`8100`, and isolation is enforced by database privileges, not by the MCP layer.

English | [简体中文](README.zh-CN.md)

![Tenant admin console](docs/images/admin-console.png)

The console lists every tenant, its database account, access mode and ready-to-paste
MCP URL. UI text is Chinese; the HTTP API and the MCP endpoint are language-neutral.

- **Add a tenant without a restart** — `tenants/tenants.json` is reloaded when its
  mtime changes. Remove a record and its token returns `401` immediately.
- **Add a tenant without a port** — every tenant uses the same endpoint.
- **Admin UI on the bundled Python** — create and delete tenants, copy client config.
- **Offline bundles** — Linux arm64, Linux x86_64 and Windows x64, with a portable
  CPython. No system Python or network access required.

Tools exposed to every tenant:

```
list_schemas · list_objects · get_object_details · execute_sql
get_performance_metrics · explain_query · analyze_db_health · get_top_queries
analyze_workload_indexes · analyze_query_indexes
```

## Requirements

- A reachable ShenTong / OSCAR server (TCP `2003` by default) and one database
  account per tenant.
- **Vendor driver for real SQL**: the closed-source `STPython` package and
  `libaci.so` (Linux) / `ACI.dll` (Windows) are **not** distributed here. Without
  them the server starts and completes MCP handshakes, but SQL tools return an
  error. See [`vendor/PUT-FILES-HERE.md`](vendor/PUT-FILES-HERE.md).
- Linux glibc ≥ 2.28 or Windows x64. The Linux bundles include a portable CPython
  3.11, the Windows bundle a portable CPython 3.13.
- Docker only if you use the container deployment (`install-all.sh`).

## Quick start from a release

Each release ships five packages plus `SHA256SUMS`:

| Asset | Contents |
|---|---|
| `st-mcp-tenant-<ver>-source.tar.gz` | Full source for the tag. |
| `st_mcp-<ver>-py3-none-any.whl` | Universal wheel for Python ≥ 3.11. |
| `st-mcp-tenant-<ver>-linux-arm64.tar.gz` | Source + portable CPython + wheels + `install.sh` / `run.sh` / `admin.sh`. |
| `st-mcp-tenant-<ver>-linux-x86_64.tar.gz` | Same for x86_64. |
| `st-mcp-tenant-<ver>-windows-x64.zip` | Portable CPython 3.13 with the dependencies installed + `start.bat` / `admin.bat` + source. |

### Linux bundle (no Docker)

```sh
tar -xzf st-mcp-tenant-<ver>-linux-x86_64.tar.gz
cd st-mcp-tenant-<ver>-linux-x86_64
sh install.sh
sh admin.sh                       # http://127.0.0.1:8001
# create at least one tenant, then:
sh run.sh                         # http://0.0.0.0:8100/mcp
```

`install.sh` unpacks the bundled CPython into `./python` and installs the
open-source dependencies offline.

### Windows bundle

Unzip, run `admin.bat` to create tenants, then `start.bat` to start the MCP
server. Both read the registry from `tenants\tenants.json`; `start.bat` exits
with an error if the registry does not exist.

### Wheel

```sh
python -m pip install st_mcp-<ver>-py3-none-any.whl
ST_TENANT_REGISTRY=/etc/st-mcp/tenants.json \
  st-mcp --transport streamable-http --sse-host 0.0.0.0 --sse-port 8100 --mcp-path /mcp
```

## Connecting a client

All tenants use the same port. The token selects the account:

```text
POST http://db-host:8100/mcp/<token>
Authorization: Bearer <token>
X-ST-Token: <token>
```

Any one of the three is enough. Requests to the MCP path without a valid token
return `401` unless `ST_ALLOW_DEFAULT=1` and `ST_DEFAULT_TENANT` names a tenant.
`GET /healthz` is the exception: it answers without a token and reports the tenant
count. Example client configuration:

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

Prefer the `Authorization` header when the client supports it: a token in the URL
also ends up in browser history and proxy logs.

## Tenant registry

`tenants/tenants.json` maps a token to one database account. Create it with the
admin UI or edit it directly (mode `600`). The server reloads it on mtime change.

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

| Field | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | — | Tenant name, unique in the file. |
| `token` | yes | — | Bearer token, at least 16 characters. |
| `user` | yes | — | Database account used for this tenant. |
| `password` | no | empty | Database password. |
| `host` | no | `127.0.0.1` | Database host. |
| `port` | no | `2003` | Database port. |
| `database` | no | `OSRDB` | Database name. |
| `access_mode` | no | `unrestricted` | `restricted` rejects write SQL. |
| `enabled` | no | `true` | Set to `false` to disable the record. |

Deleting a tenant removes its registry record (the token stops working
immediately) and any tenant-specific helper files; the database account is kept.

## Admin console

`admin.sh` / `admin.bat` starts a web console on `http://127.0.0.1:8001` using
the bundle's own Python. It can:

- list tenants with their database account, access mode and client URL;
- create a tenant (name, account, password, host, port, database, mode);
- delete a tenant record;
- copy a ready-to-use MCP client configuration.

Access rules:

- Loopback requests need no login.
- Remote access requires `ADMIN_ALLOW_REMOTE=1` and `ADMIN_USER` /
  `ADMIN_PASSWORD` in `secrets/admin.env`. Without credentials the server refuses
  to start with `--allow-remote` (fail closed).
- `ADMIN_ALLOW_CIDR` optionally restricts remote source networks.
- Copy `secrets/admin.env.example` to `secrets/admin.env` (mode `600`) and set
  the credentials before enabling remote access.

Creating and deleting tenants calls the POSIX shell tools in `tools/`. On Linux
`admin.sh` puts the bundle's `python/bin` on `PATH`, so no system Python is
needed. On Windows those actions require Git for Windows (`bash.exe`); listing
and copying client configuration work without it.

When Docker is available the console finds the running container and creates the
database account as well as the token. Without Docker it runs in **preview mode**:
it only writes the registry, and the database account must be created manually
(shown as a yellow badge in the UI).

The console displays passwords and tokens. Keep it on loopback, or expose it only
behind the Basic auth and CIDR restrictions.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ST_TENANT_REGISTRY` | `<bundle>/tenants/tenants.json` | Registry file. |
| `ST_ALLOW_DEFAULT` | `0` | `1` accepts requests without a token as `ST_DEFAULT_TENANT`. |
| `ST_DEFAULT_TENANT` | unset | Tenant used for tokenless requests. In tenant mode every connection comes from the registry; `ST_HOST` / `ST_PORT` / `ST_USER` / `ST_DB` are not used. |
| `SSE_HOST` / `SSE_PORT` | `0.0.0.0` / `8100` | MCP listen address and port. |
| `MCP_PATH` | `/mcp` | MCP endpoint path. |
| `LOG_LEVEL` | `INFO` | Log level. |
| `ST_HOST_GATEWAY` | unset | Rewrites `127.0.0.1` targets inside Docker (default `172.17.0.1`). |
| `ADMIN_HOST` / `ADMIN_PORT` | `127.0.0.1` / `8001` | Admin console bind address. |
| `ADMIN_ALLOW_REMOTE` | unset | `1` allows remote console access. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | unset | Basic auth credentials for remote access. |
| `ADMIN_ALLOW_CIDR` | unset | Comma-separated networks allowed to reach the console. |

`SSE_HOST`, `SSE_PORT`, `MCP_PATH` and `LOG_LEVEL` are read by the bundled
wrappers (`run.sh`, `start.bat`, `docker/entrypoint.sh`), which pass them to the
server's flags; the Python CLI itself reads only the flags.

### Docker deployment

The repository also keeps the original container workflow:

```sh
cp secrets/st-mcp.env.example secrets/st-mcp.env
vi secrets/st-mcp.env                  # ST_PASSWORD only
chmod 600 secrets/st-mcp.env
sh install-all.sh                      # build image, start container, self-check
sh tools/create-tenant.sh -t tenant_alice
sh tools/install-admin-service.sh      # optional systemd unit for the console
```

## Security model

- **The token is a credential.** Anyone holding it can query everything that
  database account can see. Do not post tenant URLs or the console publicly.
- `access_mode: restricted` rejects write SQL at the MCP layer before it reaches
  the database. The MCP layer does not otherwise scope queries, so isolation still
  depends on database privileges: create one database account per tenant with the
  minimum grants and use `restricted` for read-only tenants.
- The admin console shows passwords and tokens; source restriction matters more
  than password strength.
- Prefer `Authorization: Bearer` over URL tokens to keep credentials out of logs.

## Relationship to the dynamic edition

[`st-mcp`](https://github.com/guazixiong/st-mcp) adds **dynamic connections**:
a caller can also pick a target database per request with `X-ST-DB-*` headers or
`st_*` URL parameters. In that edition a tenant token still takes precedence over
a dynamic target. Use the tenant edition when each caller must be limited to a
fixed account; use the dynamic edition when callers need to choose the database.

## Development

```sh
PYTHONPATH=src sh -c 'for t in src/test/test_*.py; do python "$t"; done'
sh -n install-all.sh scripts/*.sh
sh .secrets-scan.sh
```

Rebuild the release assets locally:

```sh
VER=0.1.1 ARM64_PY_TGZ=... X64_PY_TGZ=... \
ARM64_WHEELS=... X64_WHEELS=... WIN_BUNDLE=... \
sh tools/build-release.sh
```

## License

MIT — see [`LICENSE`](LICENSE). `src/` is derived from vendor-delivered code;
scope and attribution are in [`NOTICE.md`](NOTICE.md).
