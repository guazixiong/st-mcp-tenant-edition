# Tenant registry

`tenants.json` maps a bearer token to one ShenTong database account. The server
re-reads the file when its mtime changes, so adding or removing a tenant does
not require a restart. Keep the file at mode `600`: it contains database
passwords and bearer tokens.

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
| `access_mode` | no | `unrestricted` | `restricted` blocks write SQL. |
| `enabled` | no | `true` | Set to `false` to disable the record. |

Prefer creating tenants through the admin UI or `tools/create-tenant.sh`, which
also creates the matching database account when a database connection is
available.
