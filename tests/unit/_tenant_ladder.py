"""The TENANT migration ladder — discovered, never transcribed.

The analogue of ``_customer_console_ladder.py`` for the other database. A suite
that reads or writes tenant tables under R8 has to build the tenant schema
first, and ``_customer_console_ladder.py`` reads ``infra/customer_console/`` and
can never build one.

⚠️ **A second MECHANISM, not a second source of truth — and the divergence is
argued, per root ``CLAUDE.md`` §5 (*do not invent a second way to do an existing
thing*).** Tenant-ladder machinery already exists in CI: ``pr-check.yml``'s
``migrations`` job replays this exact ladder on ``pgvector/pgvector:pg16`` via
``psql -f infra/postgres/01_schema.sql`` and then the shell replayer three
times. It is the same ladder in the same order, and it is *not* the thing a
pytest fixture needs:

* **A pytest fixture cannot shell out to ``psql`` mid-suite.** That replayer is
  a bash script driving the ``psql`` client through docker/local modes, with a
  ledger table, a pre-migration backup hook and its own environment contract
  (``PG_MODE``, ``PG_USER``, ``PG_DB``, ``APP_DIR``, ``MIGRATIONS_DIR``). A
  suite needs a **programmatic** schema on a SQLAlchemy connection it already
  holds, inside the transaction it controls.
* **The two answer different questions.** That job proves the ladder is
  *idempotent against a real server* — replay it, replay it again, assert 0
  applied. This module proves a suite's assertions run against *the schema the
  ladder produces* rather than against a fake. Neither substitutes: a green
  ``migrations`` job says nothing about whether an R8 suite skipped.
* **One ladder, two readers, and the ladder is DISCOVERED by both.** Neither
  transcribes a file list, which is the property that matters — the failure
  ``_customer_console_ladder.py``'s docstring records (five hand-copied lists,
  three stale) is structurally impossible for either reader.
* **The order is mirrored, not re-invented.** ``01_schema.sql`` first, then the
  numbered ladder sorted on the leading integer, which is exactly what the
  shell replayer does across its init-only skip and its numeric sort (whose own
  comment records the "100 before 99" bug that makes lexical sorting wrong).
  **If the two ever disagree about order, the bash script is the fact and this
  module is the defect.**

Two differences from the Console ladder, both load-bearing:

1. **00/01 are init-only and not re-runnable.** ``00_create_databases.sql`` is a
   ``docker-entrypoint-initdb.d`` script for creating SIBLING databases and has
   nothing to say to a connection already inside one — it is applied **NEVER**.
   ``01_schema.sql`` is what initdb lays down on a fresh volume, so it is
   applied **once**, against the empty database, before ``02+``.
2. **``schema.generated.sql`` must never be replayed** — it is a raw ``pg_dump``
   snapshot and is not idempotent. The numbered-prefix regex already excludes
   it, and the ``generated/`` subdirectory is not read at all.
   *Rejected alternative, named so it is not re-proposed:* seeding a test
   database from that snapshot is faster and proves the **snapshot**, not the
   migrations — and a column added in a file newer than the snapshot would be
   missing, so the suite would test a schema without the thing under test.

⚠️ **The schema needs pgvector.** ``infra/postgres/01_schema.sql`` runs
``CREATE EXTENSION "uuid-ossp"`` **and** ``CREATE EXTENSION vector``; stock
``postgres:16`` ships neither the extension nor the type. Point
``TENANT_LADDER_DATABASE_URL`` at a ``pgvector/pgvector:pg16`` server, which is
the image the ``migrations`` job already names for exactly this file.
"""
from __future__ import annotations

import os
import re

#: Repository root — this file is ``<root>/tests/unit/_tenant_ladder.py``.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LADDER_DIR = os.path.join(_ROOT, "infra", "postgres")

_NUMBERED = re.compile(r"^(\d+)_.*\.sql$")

#: Applied by initdb on a fresh volume, never by the replayer. Skipped here for
#: the same reason and by the same rule: ``00_`` creates sibling DATABASES
#: (meaningless inside one) and ``01_`` is the init schema, applied separately
#: and exactly once by :func:`apply_ladder`.
_INIT_ONLY = ("00_", "01_")

#: The init schema, applied FIRST and by itself. Named rather than derived so
#: the ordering contract is legible at the point it is relied on.
INIT_SCHEMA = os.path.join(_LADDER_DIR, "01_schema.sql")


def ladder() -> tuple[str, ...]:
    """Absolute paths of every replayable tenant migration, in application order.

    ``01_schema.sql`` is NOT in this tuple — :func:`apply_ladder` applies it
    first and separately, mirroring the init-only skip the shell replayer makes.

    Raises rather than returning empty: an empty ladder would let an R8 suite
    "pass" against a database with no tables, which is the failure mode this
    module exists to remove.
    """
    found: list[tuple[int, str]] = []
    for name in os.listdir(_LADDER_DIR):
        match = _NUMBERED.match(name)
        if not match or name.startswith(_INIT_ONLY):
            continue
        found.append((int(match.group(1)), os.path.join(_LADDER_DIR, name)))

    if not found:
        raise RuntimeError(
            f"no numbered migrations found in {_LADDER_DIR} — the tenant "
            "ladder cannot be empty, and an empty one would make every R8 "
            "suite pass against a schema-less database"
        )

    numbers = [n for n, _ in found]
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    if duplicates:
        # R1: migration numbers are taken at build time and re-checked at
        # merge. Two files claiming 177 apply in filesystem order, which is not
        # an order — and the collision is invisible until the second one loses.
        raise RuntimeError(
            f"duplicate tenant migration numbers {sorted(duplicates)} in "
            f"{_LADDER_DIR} — R1: renumber before merging"
        )

    # Sorted on the leading INTEGER, not the string: `010` sorts before `002`
    # lexically, and this ladder passed 99 long ago. `sort -V` in the shell
    # replayer is the same decision, and its comment records the bug.
    return tuple(path for _, path in sorted(found))


def _exec_file(conn, path: str) -> None:
    """Run one migration file verbatim on *conn*'s underlying DBAPI cursor.

    ⚠️ **Not ``exec_driver_sql``, and the reason is a real failure, not taste.**
    SQLAlchemy hands that call an (empty) parameter set, which makes psycopg3
    run its client-side placeholder pass over the SQL — and this ladder is full
    of ``LIKE '%status%'``. The first attempt died on::

        only '%s', '%b', '%t' are allowed as placeholders, got '%''

    The Console ladder never hit it because that SQL happens to contain no
    ``%``. Doubling every ``%`` to escape it was rejected: it is a text
    transformation on 176 files of production DDL, and it is wrong the day one
    of them legitimately contains ``%%``. Passing **no parameters at all** is
    the honest fix — psycopg3 skips interpolation entirely when ``params`` is
    None, and then uses the simple query protocol, which is also what lets one
    file carry many statements.

    The cursor comes from the connection the caller already opened, so this
    still runs inside the caller's transaction.
    """
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


_IS_EMPTY_SQL = (
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
)


def apply_ladder(conn) -> None:
    """Build the whole tenant schema on an open connection.

    Takes a connection rather than an engine so the caller decides the
    transaction. Every numbered migration is written to be idempotent
    (``IF NOT EXISTS``), which is what lets a suite apply it twice to prove
    replay-safety against a real server instead of against our reading of the
    DDL — and this function is therefore safe to call on every run.

    ``01_schema.sql`` is the exception and is applied **only to an empty
    database**, which is exactly when initdb would have applied it on a real
    box. It is not re-runnable (that is why the shell replayer skips it
    outright, leaving it to the container's entrypoint), so applying it to a
    database that already carries the schema would fail — and failing there
    would look like a migration bug rather than a fixture one.
    """
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(_IS_EMPTY_SQL)
        already_built = (cur.fetchone() or (0,))[0] > 0
    if not already_built:
        _exec_file(conn, INIT_SCHEMA)
    for path in ladder():
        _exec_file(conn, path)
