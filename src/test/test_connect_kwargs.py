"""Prove the driver is called with separated fields, never a combined string.

Uses a fake STPython module, so it runs anywhere Python runs — no database and
no vendor driver required. The real regression this guards against is the old
code path that handed ``user/password@host:port/db`` to the extension and let
its own splitter cut the password at the first ``/`` or ``@``.

Run directly:  python3 test_connect_kwargs.py
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

CALLS = []


class _FakeCursor:
    def execute(self, sql, params=None):
        return 1

    def close(self):
        return None


class _FakeConnection:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        return None


def _fake_connect(*args, **kwargs):
    CALLS.append({"args": args, "kwargs": kwargs})
    return _FakeConnection()


fake = types.ModuleType("STPython")
fake.connect = _fake_connect
sys.modules["STPython"] = fake

from st_mcp.sql.connstr import resolve_spec  # noqa: E402
from st_mcp.sql.sql_driver import DbConnPool  # noqa: E402
from st_mcp.sql.sql_driver import obfuscate_password  # noqa: E402

PASSWORD = "Zz#kjt/2026@测试!p7cur:e%40?q"


def check(name, got, want):
    if got != want:
        raise AssertionError(f"{name}: got {got!r}, want {want!r}")


def test_kwargs_are_separated():
    CALLS.clear()
    spec = resolve_spec(f"oscar://mcp_rw:{PASSWORD}@10.0.2.15:2003/OSRDB")
    pool = DbConnPool()
    asyncio.run(pool.pool_connect(spec=spec))

    check("one connect call", len(CALLS), 1)
    call = CALLS[0]
    check("no positional string", call["args"], ())
    check("user", call["kwargs"]["user"], "mcp_rw")
    check("password survives every special char", call["kwargs"]["password"], PASSWORD)
    check("dsn carries no credentials", call["kwargs"]["dsn"], "10.0.2.15:2003/OSRDB")
    check("pool reports healthy", pool._is_valid, True)


def test_password_never_reaches_the_dsn():
    for uri in [
        f"mcp_rw/{PASSWORD}@10.0.2.15:2003/OSRDB",
        f"oscar://mcp_rw:{PASSWORD}@10.0.2.15:2003/OSRDB",
        f"oscar://mcp_rw:{PASSWORD}@[::1]:2003/OSRDB",
    ]:
        spec = resolve_spec(uri)
        if PASSWORD in spec.dsn():
            raise AssertionError(f"password leaked into dsn for {uri!r}")
        if PASSWORD not in (spec.password or ""):
            raise AssertionError(f"password was truncated for {uri!r}")


def test_logs_are_masked():
    raw = f"ORA-ish error: cannot connect mcp_rw/{PASSWORD}@10.0.2.15:2003/OSRDB"
    masked = obfuscate_password(raw, PASSWORD)
    check("exact value removed", PASSWORD in masked, False)
    check("user still visible", "mcp_rw" in masked, True)

    without_secret = obfuscate_password("oscar://mcp_rw:p@ss!w0rd@10.0.2.15:2003/OSRDB")
    check("scheme URL masked", "ss!w0rd@" in without_secret, False)
    check("user kept in URL mask", without_secret.startswith("oscar://mcp_rw:"), True)


def test_env_only_configuration():
    CALLS.clear()
    env = {
        "ST_HOST": "10.0.2.15",
        "ST_PORT": "2003",
        "ST_USER": "SYSDBA",
        "ST_PASSWORD": PASSWORD,
        "ST_DB": "OSRDB",
    }
    spec = resolve_spec(None, env=env)
    pool = DbConnPool()
    asyncio.run(pool.pool_connect(spec=spec))
    check("env password used verbatim", CALLS[0]["kwargs"]["password"], PASSWORD)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    main()
