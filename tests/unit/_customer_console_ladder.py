"""The Customer Console migration ladder — discovered, never transcribed —
plus the ONE seed row every suite that provisions now needs.

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

⚠️ **:func:`ensure_deployment` lives here for the same reason the ladder
does.** Since WS-29 MT-1j slice 4 ``POST /orgs/provision`` requires a
``deployment_label`` that resolves to a real ``deployment`` row, and the ladder
seeds none — so every Console R8 suite that provisions needs one. Six copies of
that INSERT is precisely the shape this module exists to prevent, and the
alternative (a helper in whichever suite happened to need it first) would make
five suites import a sixth.

⚠️ **:func:`mint_deployment_key` joined it for the same reason** (WS-31 CP-2c
slice 1, 2026-08-19). It was ``test_customer_console_resolve.py::_depl_key``
while exactly one suite minted deployment keys; slice 1 gave the credential a
second capability and therefore a second suite that has to mint one, and the
moment there are two the mint site is a thing to keep in step — the capability
vocabulary in particular. Moved rather than copied: the resolve suite's helper
now delegates here.
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


#: The deployment a Console R8 suite provisions onto when the box is not the
#: subject of the test. Suites whose subject IS placement (``test_customer_
#: console_resolve.py``) mint their own uniquely-labelled rows instead.
DEFAULT_DEPLOYMENT_LABEL = "test-box"


def ensure_deployment(
    conn,
    *,
    label: str = DEFAULT_DEPLOYMENT_LABEL,
    base_url: str = "https://box.invalid",
) -> str:
    """Ensure one ``deployment`` row with *label* exists; return its id.

    ``DO UPDATE`` rather than ``DO NOTHING`` so the statement always RETURNS —
    ``DO NOTHING`` returns no row on conflict, and a helper that returned
    ``None`` on the second call would be an idempotent helper that is not.
    (``store.ensure_organization`` makes the same argument for the same
    reason.)

    Called per TEST rather than per module on purpose: one fence deliberately
    empties this table to construct the sole-deployment world adjudication item
    3 forbids inferring from, and a module-scoped row would not come back.
    """
    from sqlalchemy import text

    row = conn.execute(
        text(
            """
            INSERT INTO deployment (label, base_url)
            VALUES (:label, :base_url)
            ON CONFLICT (label) DO UPDATE SET base_url = EXCLUDED.base_url
            RETURNING id
            """
        ),
        {"label": label, "base_url": base_url},
    ).first()
    assert row is not None  # guaranteed by DO UPDATE
    return str(row[0])


def mint_deployment_key(
    conn,
    *,
    deployment_id: str,
    capabilities: list[str] | None = None,
) -> str:
    """Mint a ``cc_depl_`` key straight into the table; return the TOKEN.

    ``mint_key(env=ENV_DEPLOYMENT)`` + :func:`store.issue_deployment_key`,
    because the Console specifies **no** HTTP route that issues one: no
    done-when asks for it, and issuing a real ``cc_depl_`` key into a live
    deployment is OWNER-GATE (``customer_console.md`` §8 gate 7). Minting one
    into a scratch database from a fixture is not that act and is agent-safe;
    the capability SET is the only thing that varies, which is why it is the
    only parameter.

    ``capabilities=None`` takes the column default — exactly ``{resolve}``,
    written in SQL by ``store.issue_deployment_key`` — so a caller that does
    not care gets the narrow credential rather than an accidentally wide one.
    Callers that mean a capability say its NAME from
    ``customer_console.auth``; the string is defined once there precisely so it
    is not retyped at the mint site, the check and the tests.
    """
    from customer_console import store
    from customer_console.keys import ENV_DEPLOYMENT, mint_key

    minted = mint_key(env=ENV_DEPLOYMENT)
    store.issue_deployment_key(
        conn,
        deployment_id=deployment_id,
        prefix=minted.prefix,
        key_hash=minted.key_hash,
        label="fixture",
        created_by="fixture",
        capabilities=capabilities,
    )
    return minted.token


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
