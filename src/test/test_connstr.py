"""Tests for the connection specification parser.

Run directly (no pytest needed, it is not on the air-gapped machine):
    python3 test_connstr.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from st_mcp.sql.connstr import ConnectionSpecError, parse_connection_uri, resolve_spec

NASTY = [
    "hnsjt2026",
    "p@ss!w0rd",
    "a/b@c:d",
    "p%40ss",
    "p#ss?x",
    "!$%^&*()_+-=[]{};',.<>|~",
    'quote"and\'single',
    "  padded pass word  ",
    "尾@/号中文密码",
    "@leading",
    "trailing@",
    "s3cr3t:5520",
    "x" * 200,
]


def check(name, got, want):
    if got != want:
        raise AssertionError(f"{name}: got {got!r}, want {want!r}")


def test_scheme_urls():
    for password in NASTY:
        uri = f"oscar://SYSDBA:{password}@10.0.2.15:2003/OSRDB"
        parts = parse_connection_uri(uri)
        check("user", parts["user"], "SYSDBA")
        check(f"password {password!r}", parts["password"], password)
        check("host", parts["host"], "10.0.2.15")
        check("port", parts["port"], 2003)
        check("database", parts["database"], "OSRDB")


def test_dsn_style():
    for password in NASTY:
        parts = parse_connection_uri(f"SYSDBA/{password}@127.0.0.1:2003/OSRDB")
        check("user", parts["user"], "SYSDBA")
        check(f"password {password!r}", parts["password"], password)
        check("host", parts["host"], "127.0.0.1")


def test_defaults_and_no_port():
    parts = parse_connection_uri("shentong://SYSDBA/p@ss@dbhost/OSRDB")
    check("host", parts["host"], "dbhost")
    check("port", "port" in parts, False)
    check("password", parts["password"], "p@ss")
    check("database", parts["database"], "OSRDB")


def test_credentials_only():
    # Without '@' the whole value is credentials; the first separator wins.
    check("colon form", parse_connection_uri("SYSDBA:pss/word")["password"], "pss/word")
    check("slash form", parse_connection_uri("SYSDBA/pss:word")["password"], "pss:word")
    check("user", parse_connection_uri("SYSDBA/pss:word")["user"], "SYSDBA")
    # A password holding '@' given without a host is ambiguous. The parser
    # rejects it instead of guessing, and ST_PASSWORD is the documented way
    # to express it.
    try:
        parse_connection_uri("SYSDBA/p@ss:word")
    except ConnectionSpecError:
        pass
    else:
        raise AssertionError("ambiguous credentials-only URI should raise")


def test_first_separator_wins_whichever_it_is():
    check("slash first", parse_connection_uri("user/pa:ss@h:2003/db")["password"], "pa:ss")
    check("colon first", parse_connection_uri("user:pa/ss@h:2003/db")["password"], "pa/ss")


def test_query_and_fragment_are_not_credentials():
    parts = parse_connection_uri("oscar://SYSDBA:p%40ss@h:2003/OSRDB?foo=bar")
    check("password stays literal", parts["password"], "p%40ss")
    check("database", parts["database"], "OSRDB")


def test_url_encoded_mode():
    parts = parse_connection_uri("oscar://SYSDBA:p%40ss%2Fw%3Ad@h:2003/OSRDB", url_encoded=True)
    check("password", parts["password"], "p@ss/w:d")
    parts = parse_connection_uri("oscar://SYSDBA:p%40ss@h:2003/OSRDB")
    check("plain mode keeps %", parts["password"], "p%40ss")


def test_ipv6():
    parts = parse_connection_uri("oscar://SYSDBA:pwd@[::1]:2003/OSRDB")
    check("host", parts["host"], "::1")
    check("port", parts["port"], 2003)


def test_dsn_format_from_spec():
    spec = resolve_spec("oscar://SYSDBA:p@ss/w:rd#1@10.0.2.15:2003/OSRDB")
    check("dsn", spec.dsn(), "10.0.2.15:2003/OSRDB")
    check("port", spec.port, 2003)
    check("password", spec.password, "p@ss/w:rd#1")
    check("redacted hides password", "p@ss" in spec.redacted(), False)


def test_precedence_and_env():
    env = {
        "ST_HOST": "env-host",
        "ST_PORT": "2100",
        "ST_USER": "envuser",
        "ST_PASSWORD": "envp@ss/1",
        "ST_DB": "ENVD",
    }
    spec = resolve_spec(None, env=env)
    check("env host", spec.host, "env-host")
    check("env port", spec.port, 2100)
    check("env password", spec.password, "envp@ss/1")
    check("env db", spec.database, "ENVD")

    # URI wins over env for the fields it carries.
    spec = resolve_spec("oscar://u:p@h:1234/DBX", env=env)
    check("uri host", spec.host, "h")
    check("uri port", spec.port, 1234)
    check("uri user", spec.user, "u")

    # Explicit arguments win over everything.
    spec = resolve_spec("oscar://u:p@h:1234/DBX", host="arg-h", port=5555, password="arg p@ss", env=env)
    check("arg host", spec.host, "arg-h")
    check("arg port", spec.port, 5555)
    check("arg password", spec.password, "arg p@ss")


def test_password_empty_string_is_honoured():
    spec = resolve_spec("oscar://SYSDBA:@h:2003/OSRDB", env={})
    check("empty password accepted", spec.password, "")


def test_errors():
    for bad in ["", "   ", "oscar://@h:2003/db"]:
        try:
            resolve_spec(bad, env={})
        except ConnectionSpecError:
            continue
        raise AssertionError(f"expected ConnectionSpecError for {bad!r}")

    try:
        parse_connection_uri("oscar://SYSDBA:p@ss@h:notaport/db")
    except ConnectionSpecError:
        pass
    else:
        raise AssertionError("bad port should raise")

    try:
        resolve_spec("SYSDBA", env={})
    except ConnectionSpecError as e:
        check("missing password message mentions the safe option", "ST_PASSWORD" in str(e), True)
    else:
        raise AssertionError("missing password should raise")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    main()
