"""Connection specification parsing for ShenTong (OSCAR) databases.

The STPython driver is a cx_Oracle derivative: ``connect()`` only understands
``user=``, ``password=`` and ``dsn=``. Handing it a combined
``user/password@host:port/db`` string makes the extension split the string
itself, which breaks on any password containing ``/`` or ``@``. This module
splits the specification here instead and passes every field separately, so
passwords may contain arbitrary characters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2003
DEFAULT_USER = "SYSDBA"
DEFAULT_DATABASE = "OSRDB"

ENV_URI = "DATABASE_URI"
ENV_HOST = "ST_HOST"
ENV_PORT = "ST_PORT"
ENV_USER = "ST_USER"
ENV_PASSWORD = "ST_PASSWORD"
ENV_DATABASE = "ST_DB"
ENV_URL_ENCODED = "ST_URI_URL_ENCODED"


class ConnectionSpecError(ValueError):
    """Raised when a connection specification cannot be used."""


@dataclass(frozen=True)
class ConnectionSpec:
    host: str
    port: int
    user: str
    password: str
    database: str
    url_encoded: bool = field(default=False, repr=False)

    def dsn(self) -> str:
        """DSN in the form the OSCAR server expects (no credentials)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}/{self.database}"

    def redacted(self) -> str:
        """Displayable form; the password is never included."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        shown = f"{self.user}@{host}:{self.port}/{self.database}"
        return shown + (" (password hidden)" if self.password else " (no password)")


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


def _coerce_port(value: object, source: str) -> int:
    text = str(value).strip()
    if not text:
        raise ConnectionSpecError(f"Empty port in {source}.")
    try:
        port = int(text)
    except (TypeError, ValueError):
        raise ConnectionSpecError(f"Port {text!r} in {source} is not a number.")
    if not 1 <= port <= 65535:
        raise ConnectionSpecError(f"Port {port} in {source} is out of range 1-65535.")
    return port


def _split_hostport(text: str) -> tuple[str, Optional[int]]:
    """Return (host, port|None). Handles bracketed and bare IPv6 hosts."""
    if text.startswith("["):
        closing = text.find("]")
        if closing == -1:
            raise ConnectionSpecError(f"Unbalanced '[' in host part {text!r}.")
        host = text[1:closing]
        rest = text[closing + 1 :]
        if rest.startswith(":"):
            return host, _coerce_port(rest[1:], "host:port")
        if rest:
            raise ConnectionSpecError(f"Unexpected text after IPv6 host: {rest!r}.")
        return host, None

    head, sep, tail = text.rpartition(":")
    if sep and tail.isdigit():
        return head, _coerce_port(tail, "host:port")
    if ":" in text:
        raise ConnectionSpecError(
            f"Cannot read a port from {text!r}; an unbracketed IPv6 host needs [ ] around it."
        )
    return text, None


def _split_credentials(credentials: str) -> tuple[str, Optional[str]]:
    """Split "user<sep>password" at the first separator, ':' or '/' alike.

    Checking which separator comes first keeps a password that contains the
    other separator intact: ``user/pa:ss`` and ``user:pa/ss`` both parse the
    way the two URL shapes mean them. With no separator at all there is no
    password, which is reported as ``None`` rather than an empty string so a
    missing password is not silently accepted as a blank one.
    """
    colon = credentials.find(":")
    slash = credentials.find("/")
    candidates = [index for index in (colon, slash) if index != -1]
    if not candidates:
        return credentials, None
    index = min(candidates)
    return credentials[:index], credentials[index + 1 :]


def parse_connection_uri(uri: str, url_encoded: bool = False) -> dict:
    """Split a connection URI into its fields.

    Accepted shapes (the host part always starts after the last ``@``):

    * ``[scheme://]user:password@host[:port][/database][?param=value]``
    * ``[scheme://]user/password@host[:port][/database]``
    * ``user:password`` or ``user/password`` with no ``@`` — host taken from
      the environment; a password that contains ``@`` has to be given either a
      host in the same string or supplied through ``ST_PASSWORD``.

    Splitting on the last ``@`` and the first ``:`` or ``/`` keeps every
    remaining character literal, so passwords may contain ``@ : / # ? % !``
    and spaces. Percent-decoding is off unless ``url_encoded`` is true.
    """
    if uri is None:
        raise ConnectionSpecError("No connection URI given.")
    text = uri.strip()
    if not text:
        raise ConnectionSpecError("Connection URI is empty.")

    scheme, sep, remainder = text.partition("://")
    if not sep:
        remainder = text
    elif not scheme:
        raise ConnectionSpecError(f"Connection URI {uri!r} starts with '://' but has no scheme.")

    parts: dict = {}
    if url_encoded:
        remainder = _unquote(remainder)

    credentials, at, hostpart = remainder.rpartition("@")
    if not at:
        # No '@': the whole value is credentials; host/port/db come from env.
        parts["user"], parts["password"] = _split_credentials(remainder)
        return parts

    if not hostpart:
        raise ConnectionSpecError(
            f"Connection URI ends with '@' so the host is missing: {uri!r}."
        )
    if not credentials:
        raise ConnectionSpecError(
            f"Connection URI has no user name before '@': {uri!r}."
        )

    parts["user"], parts["password"] = _split_credentials(credentials)

    # Parameters may only follow the host/database section.
    hostpart, _, _query = hostpart.partition("?")
    hostpart, _frag, _rest = hostpart.partition("#")

    hostport, slash, database = hostpart.partition("/")
    host, port = _split_hostport(hostport)
    parts["host"] = host
    if port is not None:
        parts["port"] = port
    if slash:
        parts["database"] = database

    if url_encoded:
        for key in ("user", "password", "database"):
            if parts.get(key):
                parts[key] = _unquote(parts[key])

    return parts


def resolve_spec(
    uri: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    url_encoded: Optional[bool] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ConnectionSpec:
    """Merge CLI arguments, an optional URI and environment variables.

    Precedence per field: explicit argument > URI > environment variable >
    default. Passwords are never read from a URI-less default.
    """
    environ = os.environ if env is None else env
    text = uri if uri is not None else environ.get(ENV_URI, "")

    if url_encoded is None:
        flag = (environ.get(ENV_URL_ENCODED) or "").strip().lower()
        url_encoded = flag in ("1", "true", "yes", "on")

    from_uri = parse_connection_uri(text, url_encoded=url_encoded) if text else {}

    def pick(arg_value, key, env_name, default=None):
        if arg_value not in (None, ""):
            return arg_value
        if from_uri.get(key) not in (None, ""):
            return from_uri[key]
        env_value = environ.get(env_name)
        if env_value not in (None, ""):
            return env_value
        return default

    resolved_host = pick(host, "host", ENV_HOST, DEFAULT_HOST)
    resolved_user = pick(user, "user", ENV_USER, DEFAULT_USER)
    resolved_database = pick(database, "database", ENV_DATABASE, DEFAULT_DATABASE)

    # Password is special: '' is a legitimate value, so presence is checked
    # explicitly instead of falling back through falsiness.
    if password not in (None, ""):
        resolved_password: Optional[str] = password
    elif "password" in from_uri:
        resolved_password = from_uri["password"]
    elif ENV_PASSWORD in environ and environ[ENV_PASSWORD] != "":
        resolved_password = environ[ENV_PASSWORD]
    else:
        resolved_password = None

    if port not in (None, ""):
        resolved_port = _coerce_port(port, "--port")
    elif "port" in from_uri:
        resolved_port = from_uri["port"]
    elif environ.get(ENV_PORT):
        resolved_port = _coerce_port(environ[ENV_PORT], ENV_PORT)
    else:
        resolved_port = DEFAULT_PORT

    missing = [
        name
        for name, value in (("host", resolved_host), ("user", resolved_user), ("database", resolved_database))
        if not str(value).strip()
    ]
    if missing:
        raise ConnectionSpecError(
            f"Connection specification is missing: {', '.join(missing)}."
        )
    if resolved_password is None:
        raise ConnectionSpecError(
            "No password supplied. Put it in the URI (user:password@host:port/db), "
            f"set {ENV_PASSWORD}, or pass --password. "
            "Using the separate variable is the safest option for passwords with "
            "special characters."
        )

    return ConnectionSpec(
        host=str(resolved_host).strip(),
        port=int(resolved_port),
        user=str(resolved_user).strip(),
        password=resolved_password,
        database=str(resolved_database).strip(),
        url_encoded=bool(url_encoded),
    )
