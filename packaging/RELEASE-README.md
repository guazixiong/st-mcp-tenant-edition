# ST-MCP Tenant Edition {PLATFORM}

Self-contained, offline runtime for the single-port multi-tenant ST-MCP server.
It bundles the source code, the wheel, a portable CPython and all open-source
dependencies. No network access is required.

> The ShenTong database server, the closed-source `STPython` driver and
> `libaci.so` are **not** included. Without the driver the server still starts
> and completes MCP handshakes, but SQL tools cannot query a real database.
> See `source/vendor/PUT-FILES-HERE.md`.

## Quick start

```sh
tar -xzf st-mcp-tenant-{VER}-{PLATFORM}.tar.gz
cd st-mcp-tenant-{VER}-{PLATFORM}
sh install.sh
sh admin.sh          # http://127.0.0.1:8001, create tenants here
sh run.sh            # MCP endpoint http://0.0.0.0:8100/mcp
```

Windows: unpack the zip, run `admin.bat` to create tenants, then `start.bat`.

## Endpoint

All tenants share one endpoint. The token selects the database account:

```text
POST http://<server>:8100/mcp/<token>
Authorization: Bearer <token>
X-ST-Token: <token>
```

Requests without a valid token return `401` unless `ST_ALLOW_DEFAULT=1` and
`ST_DEFAULT_TENANT` names a tenant.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ST_TENANT_REGISTRY` | `<bundle>/tenants/tenants.json` | Tenant registry file. |
| `ST_ALLOW_DEFAULT` | unset (deny) | Allow requests without a token to use the default account. |
| `ST_DEFAULT_TENANT` | unset | Tenant used when no token is supplied (with `ST_ALLOW_DEFAULT=1`). Every connection comes from the registry; `ST_HOST` / `ST_PORT` / `ST_USER` / `ST_DB` are unused. |
| `SSE_HOST` / `SSE_PORT` | `0.0.0.0` / `8100` | Listen address and port. |
| `MCP_PATH` | `/mcp` | HTTP path of the MCP endpoint. |
| `ADMIN_HOST` / `ADMIN_PORT` | `127.0.0.1` / `8001` | Admin UI bind address. |
| `ADMIN_ALLOW_REMOTE` | unset | Set to `1` to allow remote access (requires `secrets/admin.env`). |

## Security

- The token is a credential: anyone who has the URL can query what that
  database account can see.
- The server does not filter SQL per tenant; isolation depends on database
  privileges. Create one database account per tenant with the minimum grants.
- The admin UI displays tokens and passwords. Keep it on loopback, or configure
  `secrets/admin.env` plus `ADMIN_ALLOW_CIDR` before exposing it.
