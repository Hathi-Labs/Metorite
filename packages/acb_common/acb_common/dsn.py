"""The one SQLAlchemy-URL → libpq parser.

Four call sites (``acb_llm.key_store``, ``acb_llm.model_config``,
``acb_common.org_settings``, ``acb_memory.mem0_client``) each carried a
hand-rolled regex whose dbname group ``(.+)`` swallowed the query string —
``…/acb?sslmode=require`` became ``dbname=acb?sslmode=require`` — and silently
dropped every libpq parameter. That forbade the DSN forms managed Postgres
providers hand out (``?sslmode=require`` et al.). One parser, one behaviour:
query params survive into the conninfo, percent-encoding is decoded, IPv6
hosts and portless URLs parse.

Fence (R7): ``tests/unit/test_dsn.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

__all__ = ["PostgresDsn", "conninfo", "parse_postgres_dsn"]


@dataclass(frozen=True)
class PostgresDsn:
    """A postgres URL, split into libpq-shaped pieces."""

    user: str
    password: str
    host: str
    port: int
    dbname: str
    #: Query-string parameters, e.g. ``{"sslmode": "require"}``. Forwarded
    #: verbatim into the conninfo, so ONLY libpq connection keywords belong in
    #: ``DATABASE_URL``'s query string — a SQLAlchemy dialect-only arg (e.g.
    #: ``prepared_statement_cache_size``) reaches psycopg as an invalid
    #: connection option and fails the connect.
    params: dict[str, str] = field(default_factory=dict)


def parse_postgres_dsn(url: str) -> PostgresDsn:
    """Parse ``postgresql[+driver]://user:pass@host[:port]/dbname[?k=v…]``.

    Raises ``RuntimeError`` (same contract the four regexes had) when the URL
    is not a postgres DSN or lacks a host or database name.
    """
    parts = urlsplit(url)
    if not re.fullmatch(r"postgresql(\+\w+)?", parts.scheme or ""):
        raise RuntimeError(f"Cannot parse database_url: {url[:50]}...")
    dbname = unquote((parts.path or "").lstrip("/"))
    if not parts.hostname or not dbname:
        raise RuntimeError(f"Cannot parse database_url: {url[:50]}...")
    return PostgresDsn(
        user=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        host=parts.hostname,
        port=parts.port or 5432,
        dbname=dbname,
        params=dict(parse_qsl(parts.query, keep_blank_values=True)),
    )


def _quote(value: str) -> str:
    # libpq conninfo quoting: needed for empty values and anything holding
    # whitespace, quotes or backslashes.
    if value == "" or re.search(r"[\s'\\]", value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return value


def conninfo(url: str) -> str:
    """The URL as a psycopg/libpq conninfo string, query params included."""
    dsn = parse_postgres_dsn(url)
    pairs = [
        ("host", dsn.host),
        ("port", str(dsn.port)),
        ("dbname", dsn.dbname),
        ("user", dsn.user),
        ("password", dsn.password),
        *dsn.params.items(),
    ]
    return " ".join(f"{k}={_quote(v)}" for k, v in pairs)
