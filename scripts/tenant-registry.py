#!/usr/bin/env python3
"""租户注册表读写（单端口多租户）。

一个 MCP 进程、一个端口。每个租户一条记录：一个 token 对应一个神通库账号。
server 端按请求里的 token 选账号，账号自身的权限就是隔离边界。

用法（由 create-tenant.sh / list-tenants.sh 调用，也可单独用）：
  tenant-registry.py list  --file F [--json]
  tenant-registry.py get   --file F --name N [--field token]
  tenant-registry.py add   --file F --name N --token T --user U --password P
                           [--host H] [--port P] [--database D]
                           [--access-mode unrestricted|restricted] [--note S]
  tenant-registry.py remove --file F --name N
  tenant-registry.py init  --file F

写入是原子的（同目录临时文件 + rename），权限 600；root 执行时把属主改成
容器用户 10001，这样 --volume 只读挂进容器也能读，宿主其他用户读不到。
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

CONTAINER_UID = 10001
CONTAINER_GID = 10001


def load(path):
    if not os.path.exists(path):
        return {"version": 1, "tenants": []}
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("tenants"), list):
        raise SystemExit("注册表格式不正确：%s" % path)
    doc.setdefault("version", 1)
    return doc


def save(path, doc):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tenants-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            try:
                os.chown(tmp, CONTAINER_UID, CONTAINER_GID)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def find(doc, name):
    for tenant in doc["tenants"]:
        if tenant.get("name") == name:
            return tenant
    return None


def cmd_init(args):
    save(args.file, load(args.file))
    return {"success": True, "file": args.file}


def cmd_list(args):
    tenants = load(args.file)["tenants"]
    if args.json:
        print(json.dumps({"success": True, "tenants": tenants}, ensure_ascii=False))
        return {"success": True}
    if not tenants:
        print("（还没有租户）")
        return {"success": True}
    width = max([len(t.get("name", "")) for t in tenants] + [4])
    if args.base:
        print("%-*s  %-16s  %-12s  %s" % (width, "租户", "库账号", "访问模式", "客户端地址"))
        for t in tenants:
            print(
                "%-*s  %-16s  %-12s  %s/%s"
                % (
                    width,
                    t.get("name", ""),
                    t.get("user", ""),
                    t.get("access_mode", "unrestricted"),
                    args.base.rstrip("/"),
                    t.get("token", ""),
                )
            )
        return {"success": True}
    print("%-*s  %-20s  %-28s  %-12s  %s" % (width, "租户", "库账号", "token", "访问模式", "创建时间"))
    for t in tenants:
        print(
            "%-*s  %-20s  %-28s  %-12s  %s"
            % (
                width,
                t.get("name", ""),
                t.get("user", ""),
                t.get("token", ""),
                t.get("access_mode", "unrestricted"),
                t.get("created_at", ""),
            )
        )
    return {"success": True}


def cmd_get(args):
    tenant = find(load(args.file), args.name)
    if tenant is None:
        return {"success": False, "error": "没有租户 %s" % args.name}
    if args.field:
        print(tenant.get(args.field, ""))
    else:
        print(json.dumps(tenant, ensure_ascii=False))
    return {"success": True}


def cmd_add(args):
    if args.password_stdin:
        args.password = sys.stdin.readline().rstrip("\n")
    if len(args.token) < 16:
        return {"success": False, "error": "token 至少 16 个字符"}
    if args.access_mode not in ("unrestricted", "restricted"):
        return {"success": False, "error": "access_mode 只能是 unrestricted 或 restricted"}
    doc = load(args.file)
    existing = find(doc, args.name)
    for tenant in doc["tenants"]:
        if tenant.get("token") == args.token and tenant.get("name") != args.name:
            return {"success": False, "error": "token 已被租户 %s 使用" % tenant.get("name")}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "name": args.name,
        "token": args.token,
        "host": args.host,
        "port": int(args.port),
        "database": args.database,
        "user": args.user,
        "password": args.password,
        "access_mode": args.access_mode,
        "enabled": True,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    if args.note:
        entry["note"] = args.note
    if existing is None:
        doc["tenants"].append(entry)
        action = "created"
    else:
        existing.clear()
        existing.update(entry)
        action = "updated"
    save(args.file, doc)
    print(json.dumps({"success": True, "action": action, "tenant": entry}, ensure_ascii=False))
    return {"success": True}


def cmd_remove(args):
    doc = load(args.file)
    existing = find(doc, args.name)
    if existing is None:
        return {"success": False, "error": "没有租户 %s" % args.name}
    doc["tenants"] = [t for t in doc["tenants"] if t.get("name") != args.name]
    save(args.file, doc)
    print(json.dumps({"success": True, "removed": existing["name"], "user": existing.get("user")}, ensure_ascii=False))
    return {"success": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="ST-MCP 租户注册表")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list")
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--base", default="", help="显示完整客户端地址，例如 http://10.0.0.5:8100/mcp")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get")
    p.add_argument("--file", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--field")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("add")
    p.add_argument("--file", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", default="")
    p.add_argument("--password-stdin", action="store_true", help="从 stdin 读口令，避免出现在 ps 里")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=2003)
    p.add_argument("--database", default="OSRDB")
    p.add_argument("--access-mode", default="unrestricted")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove")
    p.add_argument("--file", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("init")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_init)

    args = parser.parse_args(argv)
    result = args.func(args)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
