"""The FK-cascade graph, read out of ``infra/postgres/`` rather than remembered.

Why this exists: a hand-written list of "what a delete takes with it" is a
comment, and a comment cannot fail. `members.purge_member` reports counts per
table and its whole safety argument is that the admin is told the blast radius
before clicking — so an *understated* map is the wrong direction of error, and
the way to stop it drifting is to derive it from the schema in a test.

⚠️ **Source of record is the numbered migrations**, not
``infra/postgres/schema.generated.sql``: that dump predates migration 130 and
contains no ``user_role`` / ``org_group`` / ``access_request`` at all
(``colleague_onboarding.md`` §2 Step 5). It is skipped here on purpose.

Not named ``test_*``, so pytest imports it without collecting it.

The parser is deliberately small and syntactic. It reads what our own DDL
actually uses — inline ``REFERENCES … ON DELETE CASCADE`` on a column, and
``ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY … REFERENCES``. It is not a SQL
parser and does not need to be: if a migration ever writes an FK in a shape it
cannot see, the derived map shrinks, the pinning test fails, and somebody
teaches it the shape. Failing that way round is the point.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "infra" / "postgres"

#: ``CREATE TABLE [IF NOT EXISTS] <name> (``
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)\s*\(", re.I)
#: ``ALTER TABLE [ONLY] <name> …;``
_ALTER = re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?([\w.]+)(.*?);", re.I | re.S)
#: ``REFERENCES <table>[(col)] [ON DELETE <action>]``
_REFERENCES = re.compile(
    r"REFERENCES\s+([\w.]+)\s*(?:\([^)]*\))?\s*(ON\s+DELETE\s+(?:NO\s+ACTION|\w+))?",
    re.I,
)
#: ``DROP TABLE [IF EXISTS] <name>``
_DROP = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.]+)", re.I)
_ADD_COLUMN = re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I)
#: Table-level constraint clauses — not column definitions.
_NOT_A_COLUMN = {
    "primary", "unique", "foreign", "check", "constraint", "exclude", "like",
}


def _strip_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _balanced(text: str, open_paren: int) -> str:
    """The body between ``text[open_paren]`` = ``(`` and its matching ``)``."""
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    return text[open_paren + 1:]


def _split_top_level(body: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


@lru_cache(maxsize=1)
def _parse() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """``(cascade_parent → children, table → columns)`` over every migration."""
    children: dict[str, set[str]] = {}
    columns: dict[str, set[str]] = {}

    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name == "schema.generated.sql":
            continue
        sql = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))

        for match in _CREATE.finditer(sql):
            table = match.group(1).rsplit(".", 1)[-1]
            body = _balanced(sql, match.end() - 1)
            cols = columns.setdefault(table, set())
            for part in _split_top_level(body):
                head = part.strip().split(None, 1)
                if head and head[0].lower() not in _NOT_A_COLUMN:
                    cols.add(head[0].strip('"'))
            for ref in _REFERENCES.finditer(body):
                if _is_cascade(ref):
                    parent = ref.group(1).rsplit(".", 1)[-1]
                    children.setdefault(parent, set()).add(table)

        for match in _ALTER.finditer(sql):
            table = match.group(1).rsplit(".", 1)[-1]
            rest = match.group(2)
            for col in _ADD_COLUMN.finditer(rest):
                columns.setdefault(table, set()).add(col.group(1))
            if "FOREIGN KEY" not in rest.upper() and "REFERENCES" not in rest.upper():
                continue
            for ref in _REFERENCES.finditer(rest):
                if _is_cascade(ref):
                    parent = ref.group(1).rsplit(".", 1)[-1]
                    children.setdefault(parent, set()).add(table)

    # A table a later migration drops is not in the graph. Migration 216
    # (WS-39 S8) dropped the `gtd_*` task store, which `task_accounts` used
    # to cascade. Read from comment-stripped text, like everything above.
    dropped: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name == "schema.generated.sql":
            continue
        sql = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        dropped |= {m.group(1).rsplit(".", 1)[-1] for m in _DROP.finditer(sql)}

    return (
        {k: frozenset(v - dropped) for k, v in children.items() if k not in dropped},
        {k: frozenset(v) for k, v in columns.items() if k not in dropped},
    )


def _is_cascade(ref: re.Match[str]) -> bool:
    action = (ref.group(2) or "").upper()
    return "CASCADE" in action


def cascade_children(table: str) -> frozenset[str]:
    """Tables whose rows Postgres deletes when a row of ``table`` goes — one hop."""
    return _parse()[0].get(table, frozenset())


def cascade_closure(table: str) -> frozenset[str]:
    """Every table reachable from ``table`` by ``ON DELETE CASCADE``, any depth.

    Excludes ``table`` itself unless the schema declares a self-referencing
    cascade (``gtd_items.parent_id`` does), which is the honest answer either
    way: the question this answers is "what else goes".
    """
    seen: set[str] = set()
    stack = list(cascade_children(table))
    while stack:
        child = stack.pop()
        if child in seen:
            continue
        seen.add(child)
        stack.extend(cascade_children(child))
    return frozenset(seen)


def columns_of(table: str) -> frozenset[str]:
    """Column names declared for ``table`` across every migration."""
    return _parse()[1].get(table, frozenset())
