"""The Customer Console migration ladder — discovered, never transcribed.

Every R8 suite that talks to the Customer Console database has to build the schema
first, and each one used to carry its own hand-written tuple of
``infra/customer_console/00N_*.sql`` paths. Five copies. Three of them stopped at 003,
because 004 and 005 were added to the ladder and to the code that needs them but
not to those copies.

That is not a hypothetical. It shipped: ``store.record_usage`` writes
``client_ref``, a column ``005_metering_identity.sql`` adds, and
``test_customer_console_api.py`` applied only 001-003, so a real Postgres answered

    column "client_ref" of relation "usage_event" does not exist

while the suites whose copies happened to be current passed. The stale copies
did not fail on the migration they were missing — they failed later, in an
unrelated assertion about billing, which is what makes a mirror worse than no
list at all (root ``CLAUDE.md`` §5: mirrors go stale and then lie).

So the ladder is **read off the filesystem**, sorted by the numeric prefix that
already orders it. Adding ``006_*.sql`` cannot strand a suite again, because
there is nothing left to remember to update.

Sorting is on the leading integer, not on the string: ``010`` sorts before
``002`` lexically the moment the ladder reaches double digits, and a migration
ladder applied out of order fails in ways that look like a schema bug rather
than an ordering one.
"""
from __future__ import annotations

import os
import re

#: Repository root — this file is ``<root>/tests/unit/_customer_console_ladder.py``.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LADDER_DIR = os.path.join(_ROOT, "infra", "customer_console")

_NUMBERED = re.compile(r"^(\d+)_.*\.sql$")


def ladder() -> tuple[str, ...]:
    """Absolute paths of every Customer Console migration, in application order.

    Raises rather than returning empty: an empty ladder would let every R8 suite
    "pass" against a database with no tables, which is the failure mode this
    module exists to remove.
    """
    found: list[tuple[int, str]] = []
    for name in os.listdir(_LADDER_DIR):
        match = _NUMBERED.match(name)
        if match:
            found.append((int(match.group(1)), os.path.join(_LADDER_DIR, name)))

    if not found:
        raise RuntimeError(
            f"no numbered migrations found in {_LADDER_DIR} — the Customer Console "
            "ladder cannot be empty, and an empty one would make every R8 suite "
            "pass against a schema-less database"
        )

    numbers = [n for n, _ in found]
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    if duplicates:
        # R1: migration numbers are taken at build time and re-checked at merge.
        # Two files claiming 006 apply in filesystem order, which is not an
        # order — and the collision is invisible until the second one loses.
        raise RuntimeError(
            f"duplicate Customer Console migration numbers {sorted(duplicates)} in "
            f"{_LADDER_DIR} — R1: renumber before merging"
        )

    return tuple(path for _, path in sorted(found))


def apply_ladder(conn) -> None:
    """Replay the whole ladder on an open connection.

    Takes a connection rather than an engine so the caller decides the
    transaction. Every migration here is written to be idempotent
    (``IF NOT EXISTS``), which is what lets a suite apply it twice to prove
    replay-safety against a real server instead of against our reading of the
    DDL.
    """
    for path in ladder():
        with open(path, encoding="utf-8") as fh:
            conn.exec_driver_sql(fh.read())
