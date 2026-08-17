"""Fences for ``acb_common.dsn`` — the one SQLAlchemy-URL → libpq parser (R7).

It replaced four hand-rolled regexes whose dbname group swallowed the query
string (``…/acb?sslmode=require`` → ``dbname=acb?sslmode=require``) and
dropped every libpq parameter, which forbade managed-Postgres DSN forms.
Also fences the asyncpg statement-cache connect-arg passthrough
(``DB_STATEMENT_CACHE_SIZE``) added for transaction-mode poolers.
"""
from __future__ import annotations

import pytest
from acb_common.dsn import conninfo, parse_postgres_dsn


def test_plain_local_dsn_round_trips() -> None:
    url = "postgresql+psycopg://acb:acb_dev_change_me@localhost:5432/acb"
    dsn = parse_postgres_dsn(url)
    assert (dsn.user, dsn.password, dsn.host, dsn.port, dsn.dbname) == (
        "acb", "acb_dev_change_me", "localhost", 5432, "acb",
    )
    assert dsn.params == {}
    assert conninfo(url) == (
        "host=localhost port=5432 dbname=acb user=acb password=acb_dev_change_me"
    )


def test_query_params_do_not_pollute_dbname() -> None:
    # The exact failure the old regexes had: ?sslmode=require became part of
    # dbname. It must land as its own conninfo keyword instead.
    url = "postgresql+psycopg://u:p@db.abc.supabase.co:5432/postgres?sslmode=require"
    dsn = parse_postgres_dsn(url)
    assert dsn.dbname == "postgres"
    assert dsn.params == {"sslmode": "require"}
    info = conninfo(url)
    assert "dbname=postgres " in info
    assert info.endswith("sslmode=require")


def test_multiple_params_all_survive() -> None:
    url = "postgresql://u:p@h/db?sslmode=require&application_name=acb"
    assert parse_postgres_dsn(url).params == {
        "sslmode": "require",
        "application_name": "acb",
    }
    info = conninfo(url)
    assert "sslmode=require" in info and "application_name=acb" in info


def test_port_defaults_to_5432() -> None:
    assert parse_postgres_dsn("postgresql://u:p@h/db").port == 5432


def test_percent_encoded_password_is_decoded() -> None:
    dsn = parse_postgres_dsn("postgresql+psycopg://u:p%40ss%25@h:5432/db")
    assert dsn.password == "p@ss%"


def test_asyncpg_and_bare_schemes_accepted() -> None:
    for scheme in ("postgresql", "postgresql+asyncpg", "postgresql+psycopg"):
        assert parse_postgres_dsn(f"{scheme}://u:p@h/db").dbname == "db"


@pytest.mark.parametrize(
    "bad",
    [
        "mysql://u:p@h/db",
        "not-a-url",
        "postgresql://u:p@h/",       # no dbname
        "postgresql:///db",          # no host
    ],
)
def test_non_postgres_or_incomplete_urls_raise(bad: str) -> None:
    with pytest.raises(RuntimeError, match="Cannot parse database_url"):
        parse_postgres_dsn(bad)


def test_conninfo_quotes_awkward_values() -> None:
    url = "postgresql://u:p%20a%27s@h/db"  # password: "p a's"
    info = conninfo(url)
    assert "password='p a\\'s'" in info


def test_empty_password_is_quoted_not_dropped() -> None:
    info = conninfo("postgresql://u:@h/db")
    assert "password=''" in info


def test_async_url_translates_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLAlchemy's asyncpg dialect passes query params as connect() kwargs,
    and asyncpg spells the TLS knob ``ssl=`` — a surviving ``sslmode=`` makes
    every async connection raise TypeError while /health stays green."""
    import acb_common.db as db

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db?sslmode=require"
    )
    assert db.async_database_url() == (
        "postgresql+asyncpg://u:p@h:5432/db?ssl=require"
    )

    # Other params survive untouched, in order.
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@h/db?application_name=acb&sslmode=verify-full",
    )
    assert db.async_database_url() == (
        "postgresql+asyncpg://u:p@h/db?application_name=acb&ssl=verify-full"
    )

    # No query string: unchanged.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    assert db.async_database_url() == "postgresql+asyncpg://u:p@h:5432/db"


def test_statement_cache_connect_arg_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB_STATEMENT_CACHE_SIZE=0 must reach asyncpg's connect args — that is
    the whole transaction-pooler story — and stay absent when unset."""
    import acb_common.db as db

    class _S:
        db_connect_timeout = 10
        db_statement_cache_size: int | None = None

    monkeypatch.setattr(db, "get_settings", lambda: _S())
    assert "statement_cache_size" not in db.engine_connect_args()

    _S.db_statement_cache_size = 0
    args = db.engine_connect_args()
    assert args["statement_cache_size"] == 0
