"""Guard against duplicate migration numbers (audit BO-6 partial).

apply_migrations.sh applies infra/postgres/NN_*.sql in NUMERIC (`sort -V`) order,
so two files sharing a numeric prefix have a filename-lexical (fragile) relative
order and any tooling assuming unique prefixes is ambiguous. This test fails CI
if a collision is (re)introduced — the #50 duplicate (fixed in F5) is how it
happened the first time.

Prefixes are 2-OR-MORE digits: the 2-digit space filled up at 99, so migrations
continue at 100+. Numbers are compared as integers, so a zero-padded ``097`` and
a bare ``97`` are correctly flagged as the SAME migration number.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_NN = re.compile(r"^(\d+)_.*\.sql$")


def test_migration_numeric_prefixes_are_unique():
    by_num: dict[int, list[str]] = defaultdict(list)
    for f in _MIGRATIONS.glob("[0-9][0-9]*_*.sql"):
        m = _NN.match(f.name)
        if m:
            by_num[int(m.group(1))].append(f.name)
    dupes = {n: names for n, names in by_num.items() if len(names) > 1}
    assert not dupes, f"duplicate migration number(s): {dupes}"


# ── The Customer Console ladder (infra/customer_console) ────────────────────
#
# A second ladder, applied to a different database (D34: Supabase), so it needs
# the same collision guard. It also needs one the app ladder does not, because
# it failed in a way the app ladder cannot: its suites each transcribed the list
# of migrations by hand, three copies went stale when 004 and 005 landed, and a
# real Postgres answered `column "client_ref" of relation "usage_event" does not
# exist` — in an assertion about billing, several steps from the missing
# migration. Root CLAUDE.md §5: mirrors go stale and then lie.

_PLATFORM = Path(__file__).resolve().parents[2] / "infra" / "customer_console"


def test_control_plane_migration_numbers_are_unique():
    by_num: dict[int, list[str]] = defaultdict(list)
    for f in _PLATFORM.glob("[0-9][0-9]*_*.sql"):
        m = _NN.match(f.name)
        if m:
            by_num[int(m.group(1))].append(f.name)
    dupes = {n: names for n, names in by_num.items() if len(names) > 1}
    assert not dupes, f"duplicate Customer Console migration number(s): {dupes}"


def test_the_shared_ladder_finds_every_control_plane_migration():
    """The discovery must see the whole directory, in numeric order.

    This is the assertion the hand-written tuples could not make about
    themselves: it is derived from the filesystem on both sides, so adding
    `006_*.sql` keeps it true and deleting one keeps it true, while a discovery
    that silently missed a file fails here rather than four suites later.
    """
    from tests.unit._customer_console_ladder import ladder

    found = [Path(p).name for p in ladder()]
    on_disk = sorted(
        (f.name for f in _PLATFORM.glob("[0-9][0-9]*_*.sql")),
        key=lambda n: int(_NN.match(n).group(1)),  # type: ignore[union-attr]
    )
    assert found == on_disk
    assert found, "the Customer Console ladder is empty"


def test_no_platform_suite_transcribes_the_ladder_by_hand():
    """The fence for the defect itself (R7).

    Mutation-checked: paste `"infra/customer_console/001_customer_console.sql"`
    back into any of these suites and this test fails. Without it, a
    re-transcribed list is invisible until it goes stale, which is exactly how
    the last one shipped.

    The set of suites is **globbed, not listed**. An earlier draft of this test
    carried a five-name tuple of the suites to check, which is the same mirror
    one level up: a sixth suite added later would transcribe the ladder freely
    and this fence would say nothing. The rule is about the directory, so the
    directory is what it reads.
    """
    here = Path(__file__).resolve().parent
    offenders = sorted(
        f.name for f in here.glob("test_customer_console_*.py")
        if "infra/customer_console/" in f.read_text(encoding="utf-8")
    )
    assert not offenders, (
        f"{offenders} name migration files directly — use "
        "`from tests.unit._customer_console_ladder import apply_ladder` so the "
        "ladder cannot go stale"
    )
