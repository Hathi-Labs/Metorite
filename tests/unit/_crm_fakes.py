"""In-memory doubles for the ``gateway.routes.crm`` package.

One fake, shared by ``test_crm_routes.py`` / ``test_crm_pipeline.py`` /
``test_crm_convert.py``: two copies of a DB mirror drift, and the copy that
drifts is the one that stops failing (the ``_admin_fakes.py`` lesson).

Not named ``test_*``, so pytest imports it without collecting it.

Convention: the route functions are called directly as async functions with
``core._tenant_session`` (H2) monkeypatched — and ``core._get_db`` for the H4
leaves that stay on the unbound seam — so nothing here touches Postgres and no
TestClient is started. Same shape as ``test_tasks_people_scoping.py``.

⚠️ **This is a MIRROR, and a mirror can only agree with itself.** It reads the
statement *text* to decide which rows a clause addresses — which column, which
bound parameter, which ``IS NULL`` filter, which ``ORDER BY`` — rather than
restating each route's predicate in Python, so a clause that changes column or
drops a filter changes the answer here too. That is a weaker mirror than a
hand-written copy, on purpose. It still cannot see:

* **foreign keys and therefore cascades.** Deleting a ``crm_deals`` row leaves
  the seeded ``crm_activities`` rows where they are; Postgres would take them.
  The delete route's blast-radius counts are therefore asserted against the
  statement text and the entity registry, not against survivors here.
* **CHECK constraints.** ``crm_activities``' "at least one target" and the
  ``source``/``type`` vocabularies are enforced by the database; the API's
  matching guards are asserted structurally against the migration text in
  ``test_crm_migration.py`` and behaviourally against the route's own 422.

An unparseable WHERE clause raises rather than matching every row: a clause the
fake cannot read must never silently mean "the whole table".
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

# ── Statement shapes the routes actually emit ───────────────────────────────

_TABLE_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM)\s+([a-z_][a-z0-9_]*)", re.I
)
_WHERE_RE = re.compile(r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bRETURNING\b|$)", re.I | re.S)
#: The first ORDER BY term, with an optional table alias in front of it
#: (``ORDER BY dc.is_primary DESC``) — a joined read orders by a qualified
#: column and the unqualified form would read the alias as the column.
_ORDER_RE = re.compile(
    r"\bORDER\s+BY\s+(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*(ASC|DESC)?", re.I
)
#: ``SELECT base.*`` / ``SELECT c.*`` — which relation supplies the row body.
_SELECT_STAR_RE = re.compile(r"SELECT\s+(\w+)\.\*", re.I)
_LIMIT_RE = re.compile(r"\bLIMIT\s+:(\w+)", re.I)
_OFFSET_RE = re.compile(r"\bOFFSET\s+:(\w+)", re.I)
_INSERT_COLS_RE = re.compile(r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)", re.I)
_SET_RE = re.compile(r"\bSET\b(.*?)\bWHERE\b", re.I | re.S)
_MAX_RE = re.compile(r"SELECT\s+max\((\w+)\)", re.I)
#: ``SUM(amount * COALESCE(probability, :stage_probability) / 100.0)`` — the
#: WS-26f weighted-₹ aggregate. Read as an EXPRESSION (which column is summed,
#: which column falls back to which parameter, what it is divided by) rather
#: than reproduced in Python: a mirror that hard-coded ``amount * probability``
#: would keep agreeing after the route stopped inheriting the stage default.
_WEIGHTED_SUM_RE = re.compile(
    r"SUM\(\s*(\w+)\s*\*\s*COALESCE\(\s*(\w+)\s*,\s*:(\w+)\s*\)\s*/\s*([\d.]+)\s*\)",
    re.I,
)
#: The bound parameter inside a SET value — ``:meta`` or ``CAST(:meta AS jsonb)``.
_BOUND_RE = re.compile(r":(\w+)")

#: ``<col> = CAST(:param AS uuid)``
_UUID_EQ = re.compile(r"(\w+)\s*=\s*CAST\(:(\w+)\s+AS\s+uuid\)", re.I)
#: ``lower(<col>) = :param`` and ``lower(trim(<col>)) = :param``.
#:
#: The optional wrapper is read as an EXPRESSION and then APPLIED (see
#: :func:`_normalized`), not tolerated and discarded. WS-26g's leaderboard
#: groups its buckets on ``.strip().lower()`` in Python while the aggregate
#: matches ``lower(trim(owner_email))`` in SQL, and the two only agree while
#: both normalisations are present — so dropping ``trim()`` from the route has
#: to change the answer here, exactly as dropping the weighted ``COALESCE``
#: does. A reader that skipped the wrapper would agree with the bug.
_LOWER_EQ = re.compile(
    r"lower\((?P<wrap>trim|btrim)?\(?\s*(?P<column>\w+)\s*\)?\)\s*=\s*:(?P<param>\w+)",
    re.I,
)
#: ``<col> = :param`` — never inside a lower() or a CAST.
_PLAIN_EQ = re.compile(r"(?<!lower\()\b(\w+)\s*=\s*:(\w+)\b")
#: ``<col> = 'literal'``
_LITERAL_EQ = re.compile(r"\b(\w+)\s*=\s*'([^']*)'")
#: ``<col> IS [NOT] NULL``
_IS_NULL = re.compile(r"\b(\w+)\s+IS\s+(NOT\s+)?NULL", re.I)
#: ``<col> ILIKE :q``
_ILIKE = re.compile(r"(\w+)\s+ILIKE\s+:(\w+)", re.I)
#: ``<col> IN ('a', 'b')`` — a closed literal vocabulary. Added for WS-26b's
#: activity push, whose whole safety property is the ``type IN ('note','task')``
#: predicate that keeps ``status_change`` rows out of Zoho: without a reader
#: for it the fake would answer with every activity and the test would pass
#: against a query that pushes the funnel's own history upstream.
_IN_LITERALS = re.compile(r"\b(\w+)\s+IN\s*\(\s*'([^)]*)'\s*\)", re.I)
#: ``coalesce(<col>, :default) <= :param`` — the retry queues' "is it due yet"
#: gate. Written as a coalesce rather than ``IS NULL OR <=`` so the predicate
#: is ONE comparison the fake can read; a disjunction it read as "IS NULL"
#: alone would answer that a backed-off row is due.
_COALESCE_CMP = re.compile(
    r"coalesce\((\w+),\s*:(\w+)\)\s*(<=|<|>=|>)\s*:(\w+)", re.I
)
#: ``<col> < :param`` — the attempt ceiling.
_NUM_CMP = re.compile(r"\b(\w+)\s*(<=|<|>=|>)\s*:(\w+)")

#: ``LEFT JOIN crm_organizations org ON org.id = base.organization_id`` — the
#: display-name projection ``core.project_joined`` wraps a list/board SELECT
#: in. Read from the statement (which table, which alias, which foreign key)
#: rather than hard-coded, so changing the join changes the answer here too.
_LEFT_JOIN = re.compile(
    r"LEFT\s+JOIN\s+(\w+)\s+(\w+)\s+ON\s+\2\.id\s*=\s*base\.(\w+)", re.I
)
#: ``JOIN crm_contacts c ON c.id = dc.contact_id`` — the deal-contacts read.
_INNER_JOIN = re.compile(
    r"(?<!LEFT\s)JOIN\s+(\w+)\s+(\w+)\s+ON\s+\2\.id\s*=\s*(\w+)\.(\w+)", re.I
)
#: ``org.name AS organization_name`` — one projected column.
_ALIASED_COL = re.compile(r"\b(\w+)\.(\w+)\s+AS\s+(\w+)", re.I)

# ── WS-26d-email: the shapes the CRM timeline's email source emits ──────────
#
# Four readers, added together because the email join is unreadable without
# all four — and, worse than unreadable, MISREAD without the first. Same
# reason `_IN_LITERALS` above exists: a missing reader once made a safety
# predicate invisible, and a fake that cannot see a predicate agrees with the
# bug that deletes it.

#: ``em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)``
#: — the caller-scoping predicate (`activities._email_account_scope`), the one
#: thing standing between a CRM record and somebody else's mailbox.
#: ⚠️ Without this reader `_PLAIN_EQ` matches the ``user_id = :uid`` text
#: *inside* the subquery: that sets ``seen`` (silencing the "refusing to match
#: every row" guard) and then filters OUTER ``email_messages`` rows on a
#: ``user_id`` column they do not have — wrong in both directions, silently.
#: The matched text is stripped from the WHERE clause once it has been
#: evaluated, so the generic readers never see the subquery's own predicates.
_ACCOUNT_SCOPE = re.compile(
    r"(?:(\w+)\.)?(\w+)\s+IN\s*\(\s*SELECT\s+id\s+FROM\s+(\w+)\s+"
    r"WHERE\s+user_id\s*=\s*:(\w+)(?:\s+AND\s+id\s*=\s*:(\w+))?\s*\)",
    re.I,
)
#: ``LOWER(em.from_address->>'email') IN (:addr_0, :addr_1)`` — and the
#: ``= :addr`` form the email app's contact card uses. `_LOWER_EQ` reads
#: neither: the name is qualified and the value lives inside a JSONB blob.
#: Both arms are read so that rewriting one into the other cannot make the
#: address filter invisible, which would put every message in scope on every
#: record's timeline.
_JSONB_LOWER_CMP = re.compile(
    r"LOWER\((?:(\w+)\.)?(\w+)->>'(\w+)'\)\s*(?:=\s*:(\w+)|IN\s*\(([^)]*)\))",
    re.I,
)
#: ``to_addresses @> :recipient`` — jsonb CONTAINMENT. The "have we ever
#: emailed them" probe `senders._maybe_block_cold` answers with, and the one
#: WS-26d-autolead's `_is_unknown_sender` mirrors. **No other reader here sees
#: `@>`**: `_PLAIN_EQ` needs an `=` and `_NUM_CMP` needs a bare `>`, so without
#: this the clause is invisible and the fake answers "yes, we have emailed
#: them" for every message in the Sent folder regardless of who it went to —
#: which turns the already-known-contact case into a test of nothing. Same
#: reason `_IN_LITERALS` exists: a fake that cannot see a predicate agrees with
#: the bug that deletes it.
_JSONB_CONTAINS = re.compile(r"(?:(\w+)\.)?(\w+)\s*@>\s*:(\w+)", re.I)
#: ``EXISTS (SELECT 1 FROM jsonb_array_elements(to_addresses) recipient
#: WHERE lower(recipient->>'email') = :address)`` — the CASE-FOLDING form of
#: the same "have we ever emailed them" question, which WS-26d-autolead asks
#: because `@>` is case-exact and a reply from `asha@` after we wrote to
#: `Asha@` is the same person.
#:
#: ⚠️ **Reading it is not the hard part; STRIPPING it is.** Its inner
#: comparison is literally `lower(recipient->>'email') = :address`, which
#: `_JSONB_LOWER_CMP` below matches — and that reader would then filter the
#: OUTER `email_messages` rows on a `recipient` column they do not have,
#: answering "no rows" for a correct query. Same trap `_ACCOUNT_SCOPE`
#: documents, one subquery shape further on. Evaluated and removed FIRST.
#:
#: The element alias is a backreference, so a statement whose WHERE clause
#: compares a DIFFERENT alias than the one `jsonb_array_elements` declared
#: does not match here and fails the "could not read the WHERE clause" guard
#: rather than being quietly accepted.
_JSONB_ANY_LOWER = re.compile(
    r"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+jsonb_array_elements\(\s*"
    r"(?:(\w+)\.)?(\w+)\s*\)\s+(\w+)\s+WHERE\s+lower\(\s*\3->>'(\w+)'\s*\)"
    r"\s*=\s*:(\w+)\s*\)",
    re.I,
)
#: ``LEFT JOIN email_thread_status ts ON ts.account_id = em.account_id AND
#: ts.thread_id = em.thread_id`` — a COMPOSITE key. `_LEFT_JOIN` above demands
#: literally ``ON <alias>.id = base.<fk>`` and matches nothing here.
_COMPOSITE_LEFT_JOIN = re.compile(
    r"LEFT\s+JOIN\s+(\w+)\s+(\w+)\s+ON\s+\2\.(\w+)\s*=\s*(\w+)\.(\w+)"
    r"\s+AND\s+\2\.(\w+)\s*=\s*\4\.(\w+)",
    re.I,
)
#: ``SELECT * FROM ( SELECT DISTINCT ON (…) … ) t ORDER BY … LIMIT :limit`` —
#: newest-message-per-thread. The wrapper exists because Postgres makes
#: ``DISTINCT ON`` dictate the inner ``ORDER BY``, so the newest-first ordering
#: the timeline wants has to happen outside it.
_DERIVED = re.compile(
    r"^SELECT\s+\*\s+FROM\s*\((?P<inner>.+)\)\s+\w+\s+"
    r"ORDER\s+BY\s+(?P<order>.+?)(?:\s+LIMIT\s+:(?P<limit>\w+))?$",
    re.I | re.S,
)
#: ``FROM email_messages em`` — the base relation and the alias expressions
#: qualify themselves with.
_FROM_ALIAS = re.compile(r"\bFROM\s+(\w+)\s+(\w+)", re.I)
#: One SELECT-list item: ``<expression> AS <name>``.
_PROJECTION = re.compile(r"^(?P<expr>.*?)\s+AS\s+(?P<name>\w+)$", re.I | re.S)
#: ``em.from_address->>'email'`` — one key out of a JSONB column.
_JSONB_FIELD = re.compile(r"(?:(\w+)\.)?(\w+)->>'(\w+)'")
#: ``em.subject`` / ``subject``.
_QUALIFIED = re.compile(r"(?:(\w+)\.)?(\w+)")


def _split_terms(clause: str) -> list[str]:
    """Split on commas at paren depth 0 — ``COALESCE(a, b), c`` is two terms."""
    terms: list[str] = []
    depth = start = 0
    for index, char in enumerate(clause):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            terms.append(clause[start:index])
            start = index + 1
    terms.append(clause[start:])
    return [term.strip() for term in terms if term.strip()]


def _evaluate(expression: str, context: dict[str, Any]) -> Any:
    """One SELECT-list / ORDER BY / DISTINCT ON expression against a row set.

    ``context`` maps a table alias to its row — ``None`` for a LEFT JOIN that
    matched nothing — plus ``""`` for unqualified names.

    Deliberately tiny: it reads the four shapes the email query emits
    (``COALESCE``, ``::text``, a JSONB key, a qualified column) and RAISES on
    anything else. A query that grows a fifth shape fails loudly here rather
    than projecting silently as NULL, which is the failure a fake exists to
    make impossible.
    """
    expression = expression.strip()
    lowered = expression.lower()
    if lowered.startswith("coalesce(") and expression.endswith(")"):
        for part in _split_terms(expression[len("coalesce(") : -1]):
            value = _evaluate(part, context)
            if value is not None:
                return value
        return None
    if lowered.endswith("::text"):
        value = _evaluate(expression[: -len("::text")], context)
        return None if value is None else str(value)
    jsonb = _JSONB_FIELD.fullmatch(expression)
    if jsonb:
        blob = (context.get(jsonb.group(1) or "") or {}).get(jsonb.group(2))
        return blob.get(jsonb.group(3)) if isinstance(blob, dict) else None
    column = _QUALIFIED.fullmatch(expression)
    if column:
        return (context.get(column.group(1) or "") or {}).get(column.group(2))
    raise AssertionError(f"fake cannot evaluate the expression: {expression!r}")


def _balanced(clause: str, opened: int) -> int:
    """Index of the ``)`` closing the ``(`` at *opened*."""
    depth = 0
    for index in range(opened, len(clause)):
        if clause[index] == "(":
            depth += 1
        elif clause[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced parentheses in: {clause}")


def _parse_distinct_on(inner: str) -> tuple[list[str], list[tuple[str, str]], str]:
    """``SELECT DISTINCT ON (keys) projections FROM …`` → its three parts.

    Parsed rather than regexed because a key can itself contain parentheses
    (``COALESCE(em.thread_id, em.id::text)``) and a regex that stops at the
    first ``)`` would silently group by half the key.
    """
    head = re.match(r"\s*SELECT\s+DISTINCT\s+ON\s*\(", inner, re.I)
    if head is None:
        raise AssertionError(f"fake expected a DISTINCT ON select: {inner}")
    close = _balanced(inner, head.end() - 1)
    rest = inner[close + 1 :]
    at_from = re.search(r"\sFROM\s", rest, re.I)
    if at_from is None:
        raise AssertionError(f"fake could not find the FROM clause in: {inner}")
    projections: list[tuple[str, str]] = []
    for term in _split_terms(rest[: at_from.start()]):
        named = _PROJECTION.match(term)
        if named is None:
            raise AssertionError(
                "fake requires every projected column to be named "
                f"`<expression> AS <name>`, so the answer is the statement's "
                f"own output shape rather than the whole row: {term!r}"
            )
        projections.append((named.group("expr"), named.group("name")))
    return _split_terms(inner[head.end() : close]), projections, rest[at_from.start() :]


def _order_terms(clause: str) -> list[tuple[str, bool]]:
    """``ORDER BY a, COALESCE(b, c), d DESC`` → ``[(expr, descending), …]``."""
    found = re.search(r"\bORDER\s+BY\b(.*?)(?:\bLIMIT\b|$)", clause, re.I | re.S)
    if found is None:
        return []
    terms: list[tuple[str, bool]] = []
    for term in _split_terms(found.group(1)):
        descending = bool(re.search(r"\bDESC\b", term, re.I))
        expression = re.sub(r"\s+(ASC|DESC)\s*$", "", term, flags=re.I)
        terms.append((expression.strip(), descending))
    return terms


def _sorted_by(rows: list[Any], terms: list[tuple[str, bool]]) -> list[Any]:
    """A multi-key sort, applied right-to-left so each key is stable."""
    for expression, descending in reversed(terms):
        rows = sorted(
            rows,
            key=lambda row, e=expression: (
                _evaluate(e, row) is None, _sortable(_evaluate(e, row)),
            ),
            reverse=descending,
        )
    return rows


#: SQL literals a SET clause can name instead of a bound parameter.
_SQL_LITERALS: dict[str, Any] = {"false": False, "true": True, "null": None}


def _table(sql: str) -> str:
    match = _TABLE_RE.search(sql)
    if match is None:  # pragma: no cover — every statement names a table
        raise AssertionError(f"fake could not find a table in: {sql}")
    return match.group(1)


class _Result:
    """The slice of the SQLAlchemy result surface these routes use."""

    def __init__(self, rows: list[Any], scalar: Any = None):
        self._rows = rows
        self._scalar = scalar

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def first(self) -> Any:
        return self.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        if self._scalar is not None:
            return self._scalar
        return None


def _now() -> datetime:
    return datetime.now(UTC)


#: Column defaults migration 144 declares. Kept here so a row read back after an
#: INSERT looks like Postgres would have returned it.
_DEFAULTS: dict[str, dict[str, Any]] = {
    "crm_organizations": {"source": "manual"},
    "crm_contacts": {"source": "manual"},
    "crm_leads": {"source": "manual"},
    "crm_deals": {"source": "manual", "currency": "INR", "probability": None},
    "crm_lead_statuses": {"color": "gray", "position": 0, "is_default": False},
    "crm_deal_statuses": {
        "color": "gray", "position": 0, "is_default": False, "probability": 0,
    },
    "crm_lost_reasons": {"position": 0},
    "crm_deal_contacts": {"is_primary": False},
    # Migration 145's retry trio on the activity queue.
    "crm_activities": {
        "zoho_push_attempts": 0, "zoho_push_error": None,
        "zoho_next_attempt_at": None,
    },
    "crm_status_changes": {},
    # Migration 145 (WS-26b). `zoho_dirty` defaults false on the four record
    # tables too — see `_ZOHO_TRACKED` below, which cannot live here because
    # those tables already have entries.
    "crm_zoho_tombstones": {
        "pushed_at": None, "attempts": 0, "last_error": None,
        "next_attempt_at": None,
    },
    "crm_sync_cursors": {
        "last_pulled_at": None, "last_run_at": None, "last_status": None,
    },
}

#: Migration 145's column defaults on the four record tables. A row seeded
#: without them must read back as Postgres would: `zoho_dirty = false`, not
#: `None` — otherwise "is this row dirty" is answered by a missing key and the
#: push phase's predicate looks correct while matching nothing. Same for the
#: retry trio: `zoho_push_attempts = 0`, not NULL, or the `< :max_attempts`
#: gate would exclude every row that has never failed.
_ZOHO_TRACKED: dict[str, Any] = {
    "zoho_dirty": False, "zoho_synced_at": None,
    "zoho_push_attempts": 0, "zoho_push_error": None,
    "zoho_next_attempt_at": None,
}

#: Tables whose rows carry the timestamp trio.
_TIMESTAMPED = {
    "crm_organizations", "crm_contacts", "crm_leads", "crm_deals",
}


class _FakeSavepoint:
    """What :meth:`FakeCrmDB.begin_nested` hands back — an async CM."""

    def __init__(self, db: FakeCrmDB) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeSavepoint:
        self._db.savepoints += 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            self._db.savepoint_rollbacks += 1
        return False  # never swallow — the caller's except decides


class FakeCrmDB:
    """An in-memory ``crm_*`` schema that answers the package's statements."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.statements: list[str] = []
        #: ``(statement, params)`` in order — how a test proves a write happened
        #: with the values it expects rather than merely that a write happened.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.committed = 0
        self.rollbacks = 0
        self.closed = False
        #: Savepoints opened / exited through the rollback arm (WS-26b).
        self.savepoints = 0
        self.savepoint_rollbacks = 0
        #: ``[needle, remaining]`` — see :meth:`fail_on`.
        self._failures: list[list[Any]] = []

    # seeding ------------------------------------------------------------
    def seed(self, table: str, **columns: Any) -> SimpleNamespace:
        row = {
            "id": columns.pop("id", str(uuid4())),
            **_DEFAULTS.get(table, {}),
            **columns,
        }
        if table in _TIMESTAMPED:
            row.setdefault("created_at", _now() - timedelta(days=1))
            row.setdefault("updated_at", _now() - timedelta(days=1))
            row.setdefault("last_activity_at", None)
            for column, default in _ZOHO_TRACKED.items():
                row.setdefault(column, default)
        self.tables.setdefault(table, []).append(row)
        return SimpleNamespace(**row)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.get(table, [])

    def statements_touching(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle in s]

    # session surface ----------------------------------------------------
    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closed = True

    def begin_nested(self) -> _FakeSavepoint:
        """The SAVEPOINT seam ``core.savepoint`` uses.

        ⚠️ It does NOT undo writes — an in-memory dict has no MVCC. What it
        models is the *shape*: that the SUT opens a savepoint per record and
        that a raising record exits through the rollback arm. The property
        that matters against a real database — "the transaction is not
        poisoned" — is unobservable here, which is why the tests assert the
        savepoint was TAKEN and that the batch, the cursor write and the
        commit all still happen after a statement error.
        """
        return _FakeSavepoint(self)

    def fail_on(self, needle: str, *, times: int = 1, after: int = 0) -> None:
        """Make ``times`` statements containing *needle* raise, skipping the
        first ``after`` matches.

        Simulates a driver-level statement error — a `numeric field overflow`,
        a CHECK violation — which is the class of failure a plain
        ``try/except`` around a record cannot actually contain in Postgres.

        ``after`` exists because *where* in a batch the failure lands is the
        property under test in WS-26d-autolead: a cursor that advances over the
        successful PREFIX behaves identically to one that advances
        unconditionally when the very first record is the one that fails.
        """
        self._failures.append([needle, times, after])

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        args = dict(params or {})
        self.statements.append(statement)
        self.calls.append((statement, args))
        for entry in self._failures:
            if entry[0] in statement and entry[1] > 0:
                if entry[2] > 0:
                    entry[2] -= 1
                    continue
                entry[1] -= 1
                raise RuntimeError(
                    f"fake driver error on statement containing {entry[0]!r}"
                )
        # The auto-lead H4 fix's owner-organization lookup: a `lower() =
        # lower()` WHERE the generic reader rightly refuses to guess at.
        # Statement-keyed like every special arm, R10 honoured on both sides.
        if "FROM app_user" in statement and "lower(email)" in statement:
            wanted = str(args.get("e") or "").lower()
            return _Result([
                SimpleNamespace(**r) for r in self.rows("app_user")
                if str(r.get("email") or "").lower() == wanted
            ])
        head = statement.split(None, 1)[0].upper()
        table = _table(statement)
        if head == "INSERT":
            return self._insert(statement, table, args)
        if head == "UPDATE":
            return self._update(statement, table, args)
        if head == "DELETE":
            return self._delete(statement, table, args)
        return self._select(statement, table, args)

    # verbs --------------------------------------------------------------
    def _insert(self, statement: str, table: str, args: dict) -> _Result:
        columns = [
            c.strip() for c in _INSERT_COLS_RE.search(statement).group(1).split(",")
        ]
        # A column NAMED in the INSERT takes the bound value even when that
        # value is None — Postgres applies a DEFAULT only to an omitted column,
        # and collapsing the two would hide "the route explicitly sent NULL".
        row = {
            "id": str(uuid4()),
            **_DEFAULTS.get(table, {}),
            **{c: args.get(c) for c in columns},
        }
        if table in _TIMESTAMPED:
            row.setdefault("created_at", _now())
            row.setdefault("updated_at", _now())
            row.setdefault("last_activity_at", None)
            for column, default in _ZOHO_TRACKED.items():
                row.setdefault(column, default)
        if table == "crm_deals":
            row.setdefault("status_changed_at", _now())
            row.setdefault("closed_at", None)
        if table in ("crm_status_changes", "crm_activities"):
            row.setdefault("created_at", _now())
            row.setdefault("changed_at", _now())
        self.tables.setdefault(table, []).append(row)
        return _Result([SimpleNamespace(**row)])

    def _update(self, statement: str, table: str, args: dict) -> _Result:
        assignments = _SET_RE.search(statement)
        matched = self._matching(statement, table, args)
        for row in matched:
            for part in (assignments.group(1) if assignments else "").split(","):
                column, _, value = part.partition("=")
                column, value = column.strip(), value.strip()
                if not column:
                    continue
                bound = _BOUND_RE.search(value)
                if bound:
                    # Covers both `col = :param` and the jsonb form
                    # `col = CAST(:param AS jsonb)`.
                    row[column] = args.get(bound.group(1))
                elif value.lower().startswith("now()"):
                    row[column] = _now()
                elif value.lower() in _SQL_LITERALS:
                    # `SET is_primary = false` — a literal, not a parameter.
                    # Skipping it would leave the demote-the-incumbent write
                    # in link_deal_contact silently inert here, and a fake
                    # that ignores a write agrees with the bug.
                    row[column] = _SQL_LITERALS[value.lower()]
                elif value in row:
                    # `SET closed_at = expected_close_date` — WS-26f f4 copies
                    # one column onto another rather than binding a value per
                    # row. Before this arm the fake silently ignored it, which
                    # is the shape of "the backfill wrote nothing and every
                    # test agreed".
                    row[column] = row[value]
        return _Result([SimpleNamespace(**r) for r in matched])

    def _delete(self, statement: str, table: str, args: dict) -> _Result:
        matched = self._matching(statement, table, args)
        keep = [r for r in self.tables.get(table, []) if r not in matched]
        self.tables[table] = keep
        return _Result([])

    def _select(self, statement: str, table: str, args: dict) -> _Result:
        derived = _DERIVED.match(statement)
        if derived:
            return self._distinct_on(derived, args)
        matched = self._matching(statement, table, args)
        matched = self._ordered(statement, matched)
        matched = self._paged(statement, matched, args)
        upper = statement.upper()

        if "COUNT(*) AS COUNT" in upper:
            total = sum(float(r.get("amount") or 0) for r in matched)
            return _Result([SimpleNamespace(
                count=len(matched), amount=total,
                weighted=_weighted(statement, matched, args),
            )])
        if upper.startswith("SELECT COUNT(*)"):
            return _Result([], scalar=len(matched))
        aggregate = _MAX_RE.search(statement)
        if aggregate:
            values = [
                r.get(aggregate.group(1)) for r in matched
                if r.get(aggregate.group(1)) is not None
            ]
            return _Result([], scalar=max(values) if values else None)
        matched = self._joined(statement, matched)
        return _Result([SimpleNamespace(**r) for r in matched])

    # newest-per-group ----------------------------------------------------
    def _distinct_on(self, derived: re.Match[str], args: dict) -> _Result:
        """``SELECT * FROM (SELECT DISTINCT ON (…) …) t ORDER BY … LIMIT …``.

        Postgres's ``DISTINCT ON`` keeps the FIRST row of each key group under
        the inner ``ORDER BY``; the wrapper then re-orders what survived. Both
        halves are read off the statement — change the grouping key or the
        tie-break in the route and the answer here changes with it. That is the
        whole point: "newest message per thread" is a property of the SQL, and
        a fake that hard-coded it could not tell a correct query from one that
        groups by ``account_id`` alone.
        """
        inner = derived.group("inner")
        keys, projections, body = _parse_distinct_on(inner)
        base = _FROM_ALIAS.search(body)
        if base is None:  # pragma: no cover — the statement always aliases it
            raise AssertionError(f"fake could not find the base alias in: {body}")
        table, alias = base.group(1), base.group(2)

        joins = {
            join[1]: (join[0], ((join[2], join[4]), (join[5], join[6])))
            for join in _COMPOSITE_LEFT_JOIN.findall(body)
        }
        contexts = [
            {
                "": row, alias: row,
                **{
                    joined: self._by_columns(joined_table, row, pairs)
                    for joined, (joined_table, pairs) in joins.items()
                },
            }
            for row in self._matching(body, table, args)
        ]

        picked: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, ...]] = set()
        for context in _sorted_by(contexts, _order_terms(body)):
            key = tuple(str(_evaluate(k, context)) for k in keys)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            picked.append(context)

        rows = [
            {"": {name: _evaluate(expression, context)
                  for expression, name in projections}}
            for context in picked
        ]
        rows = _sorted_by(rows, _order_terms(f"ORDER BY {derived.group('order')}"))
        limit = derived.group("limit")
        if limit:
            rows = rows[: int(args.get(limit, len(rows)))]
        return _Result([SimpleNamespace(**row[""]) for row in rows])

    def _by_columns(
        self, table: str, base: dict, pairs: tuple[tuple[str, str], ...],
    ) -> dict[str, Any] | None:
        """A composite-key join. A NULL on either side never matches, which is
        SQL's own answer and the reason an un-threaded message picks up no
        thread status rather than the first one in the table."""
        for row in self.tables.get(table, []):
            if all(
                row.get(column) is not None
                and base.get(base_column) is not None
                and str(row.get(column)) == str(base.get(base_column))
                for column, base_column in pairs
            ):
                return row
        return None

    # joins --------------------------------------------------------------
    def _joined(self, statement: str, rows: list[dict]) -> list[dict]:
        """Resolve the JOINs a statement declares, reading it for the shape.

        Two shapes exist in this package and both are read here rather than
        special-cased: ``core.project_joined``'s display-name wrapper
        (``LEFT JOIN crm_organizations org ON org.id = base.organization_id``
        plus ``org.name AS organization_name``) and the deal-contacts read
        (``JOIN crm_contacts c ON c.id = dc.contact_id``, whose ``SELECT c.*``
        makes the CONTACT the row body). Change the join in the route and this
        follows it; delete the join and ``organization_name`` disappears from
        the answer, which is what the assertion is for.
        """
        joins: dict[str, tuple[str, str]] = {
            alias: (table, fk)
            for table, alias, fk in _LEFT_JOIN.findall(statement)
        }
        inner = {
            alias: (table, fk)
            for table, alias, _qualifier, fk in _INNER_JOIN.findall(statement)
        }
        joins.update(inner)
        projected = _ALIASED_COL.findall(statement)
        if not joins and not projected:
            return rows

        body = _SELECT_STAR_RE.search(statement)
        out: list[dict] = []
        for row in rows:
            resolved = {
                alias: self._by_id(table, row.get(fk))
                for alias, (table, fk) in joins.items()
            }
            base = row
            if body and body.group(1) in resolved:
                found = resolved[body.group(1)]
                if found is None:
                    # An INNER JOIN with no match drops the row; a fake that
                    # kept it would report a link to a deleted contact.
                    continue
                base = found
            merged = dict(base)
            for alias, column, name in projected:
                source = resolved.get(alias) if alias in resolved else row
                merged[name] = (source or {}).get(column)
            out.append(merged)
        return out

    def _by_id(self, table: str, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        for row in self.tables.get(table, []):
            if str(row.get("id")) == str(value):
                return row
        return None

    # predicate ----------------------------------------------------------
    def _email_predicates(
        self, where: str, rows: list[dict], args: dict,
    ) -> tuple[str, list[dict], bool]:
        """Evaluate the two WS-26d-email clauses, then STRIP them from *where*.

        Stripping matters as much as reading. The account-scope subquery
        contains ``user_id = :uid``; left in place, `_PLAIN_EQ` matches that
        text and filters OUTER ``email_messages`` rows on a column they do not
        have — answering "no rows" for a correct query and setting ``seen`` so
        the refuse-to-match-everything guard below never fires.
        """
        seen = False
        scope = _ACCOUNT_SCOPE.search(where)
        if scope:
            seen = True
            _alias, column, table, uid, aid = scope.groups()
            allowed = {
                str(row.get("id")) for row in self.tables.get(table, [])
                if row.get("user_id") == args.get(uid)
            }
            if aid:
                allowed &= {str(args.get(aid))}
            rows = [row for row in rows if str(row.get(column)) in allowed]
            where = where.replace(scope.group(0), "")
        # FIRST, and stripped before anything else looks at the clause — its
        # inner `lower(recipient->>'email') = :address` is exactly what
        # `_JSONB_LOWER_CMP` below matches, and that reader would filter the
        # OUTER rows on a `recipient` column they do not have.
        for _alias, column, _element, key, param in _JSONB_ANY_LOWER.findall(where):
            seen = True
            wanted = str(args.get(param) or "").lower()
            rows = [
                row for row in rows
                if any(
                    str((element or {}).get(key) or "").lower() == wanted
                    for element in (row.get(column) or [])
                )
            ]
        where = _JSONB_ANY_LOWER.sub("", where)
        for _alias, column, key, single, listed in _JSONB_LOWER_CMP.findall(where):
            seen = True
            names = (
                [single] if single
                else [p.strip().lstrip(":") for p in listed.split(",") if p.strip()]
            )
            wanted = {str(args.get(name) or "").lower() for name in names}
            rows = [
                row for row in rows
                if str((row.get(column) or {}).get(key) or "").lower() in wanted
            ]
        for _alias, column, param in _JSONB_CONTAINS.findall(where):
            seen = True
            rows = [
                row for row in rows
                if _jsonb_contains(row.get(column), args.get(param))
            ]
        where = _JSONB_CONTAINS.sub("", where)
        return _JSONB_LOWER_CMP.sub("", where), rows, seen

    def _matching(
        self, statement: str, table: str, args: dict,
    ) -> list[dict[str, Any]]:
        rows = list(self.tables.get(table, []))
        clause = _WHERE_RE.search(statement)
        if clause is None:
            return rows
        where, rows, seen = self._email_predicates(clause.group(1), rows, args)

        for column, param in _UUID_EQ.findall(where):
            seen = True
            want = str(args.get(param))
            rows = [r for r in rows if str(r.get(column)) == want]
        for wrap, column, param in _LOWER_EQ.findall(where):
            seen = True
            want = str(args.get(param) or "").lower()
            # ⚠️ A NULL column never satisfies an equality — `lower(NULL) = ''`
            # is UNKNOWN in SQL, not true. Coercing it to `""` first (which is
            # what `r.get(column) or ""` did) made `lower(owner_email) = :owner`
            # with an empty parameter match every unowned row, so a bucket
            # counted rows its own aggregate would never have summed. Same rule
            # the `_compare` helper below already follows.
            rows = [
                r for r in rows
                if r.get(column) is not None
                and _normalized(str(r.get(column)), wrap) == want
            ]
        for column, param in _PLAIN_EQ.findall(where):
            seen = True
            rows = [r for r in rows if r.get(column) == args.get(param)]
        for column, literal in _LITERAL_EQ.findall(where):
            seen = True
            rows = [r for r in rows if str(r.get(column)) == literal]
        for column, negated in _IS_NULL.findall(where):
            seen = True
            rows = [r for r in rows if (r.get(column) is not None) is bool(negated)]
        for column, listed in _IN_LITERALS.findall(where):
            seen = True
            wanted = {v.strip().strip("'") for v in f"'{listed}'".split(",")}
            rows = [r for r in rows if str(r.get(column)) in wanted]
        for column, default, operator, param in _COALESCE_CMP.findall(where):
            seen = True
            fallback = args.get(default)
            rows = [
                r for r in rows
                if _compare(
                    r.get(column) if r.get(column) is not None else fallback,
                    operator, args.get(param),
                )
            ]
        # Plain comparisons, with the coalesce ones removed first so their
        # inner `<=` is not read twice.
        for column, operator, param in _NUM_CMP.findall(_COALESCE_CMP.sub("", where)):
            seen = True
            rows = [
                r for r in rows if _compare(r.get(column), operator, args.get(param))
            ]
        ilike = _ILIKE.findall(where)
        if ilike:
            seen = True
            needle = str(args.get(ilike[0][1]) or "").strip("%").lower()
            rows = [
                r for r in rows
                if any(needle in str(r.get(c) or "").lower() for c, _ in ilike)
            ]
        if re.search(r"WHERE\s+is_default\b", statement, re.I):
            seen = True
            rows = [r for r in rows if r.get("is_default")]

        if not seen:
            raise AssertionError(
                f"fake could not read the WHERE clause — refusing to match every "
                f"row of {table}: {statement}"
            )
        return rows

    def _ordered(self, statement: str, rows: list[dict]) -> list[dict]:
        order = _ORDER_RE.search(statement)
        if order is None:
            return rows
        column = order.group(1)
        reverse = (order.group(2) or "ASC").upper() == "DESC"
        return sorted(
            rows,
            key=lambda r: (r.get(column) is None, _sortable(r.get(column))),
            reverse=reverse,
        )

    def _paged(self, statement: str, rows: list[dict], args: dict) -> list[dict]:
        offset = _OFFSET_RE.search(statement)
        if offset:
            rows = rows[int(args.get(offset.group(1), 0)):]
        limit = _LIMIT_RE.search(statement)
        if limit:
            rows = rows[: int(args.get(limit.group(1), len(rows)))]
        elif re.search(r"\bLIMIT\s+1\b", statement, re.I):
            rows = rows[:1]
        return rows


def _weighted(statement: str, rows: list[dict], args: dict) -> float:
    """Sum of amount * COALESCE(probability, stage default) / 100 over the lane.

    Computed from the statement's own expression (:data:`_WEIGHTED_SUM_RE`), so
    dropping the ``COALESCE`` onto the stage default — the one thing that makes
    a pre-move NULL probability behave — changes the answer here too. Returns
    0.0 when the aggregate is absent, which is what a lane query that never
    asked for it should read as.
    """
    match = _WEIGHTED_SUM_RE.search(statement)
    if match is None:
        return 0.0
    amount_column, probability_column, parameter, divisor = match.groups()
    fallback = args.get(parameter)
    total = 0.0
    for row in rows:
        amount = row.get(amount_column)
        probability = row.get(probability_column)
        if probability is None:
            probability = fallback
        if amount is None or probability is None:
            continue  # SQL: NULL * anything is NULL, and SUM skips it
        total += float(amount) * float(probability) / float(divisor)
    return total


def _jsonb_contains(haystack: Any, needle: Any) -> bool:
    """Postgres ``@>`` for the shapes this repo binds to it.

    An array contains another array when EVERY element of the right side is
    contained by SOME element of the left; an object contains another object
    when every key/value pair of the right side is present on the left.

    ⚠️ Comparison is EXACT, because ``@>`` is: ``'[{"email":"A@x.com"}]'`` does
    not contain ``'[{"email":"a@x.com"}]'``. Case-folding here would make the
    fake kinder than the database and hide a real miss in the "have we ever
    emailed them" probe, whose parameter is lowercased while the stored
    recipient is whatever the provider sent.
    """
    if isinstance(needle, str):
        try:
            needle = json.loads(needle)
        except ValueError:  # a plain string value, not a JSON document
            return haystack == needle
    if isinstance(needle, list):
        candidates = haystack if isinstance(haystack, list) else []
        return all(
            any(_jsonb_contains(item, want) for item in candidates)
            for want in needle
        )
    if isinstance(needle, dict):
        return isinstance(haystack, dict) and all(
            haystack.get(key) == value for key, value in needle.items()
        )
    return haystack == needle


def _normalized(value: str, wrap: str) -> str:
    """Apply the normalisation the predicate wraps its column in, then lower().

    ``wrap`` comes from the STATEMENT (:data:`_LOWER_EQ`), so this models what
    the SQL says rather than what the route meant. ``trim``/``btrim`` strip
    leading and trailing whitespace, which is what Postgres does; the empty
    string means the column was compared unwrapped.
    """
    return (value.strip() if wrap else value).lower()


def _compare(left: Any, operator: str, right: Any) -> bool:
    """One SQL comparison, with SQL's NULL semantics: unknown never matches."""
    if left is None or right is None:
        return False
    try:
        if operator == "<":
            return bool(left < right)
        if operator == "<=":
            return bool(left <= right)
        if operator == ">":
            return bool(left > right)
        return bool(left >= right)
    except TypeError:  # naive/aware or mixed types — SQL would error; we skip
        return False


def _sortable(value: Any) -> Any:
    """A total order across the mixed types one column can hold in a fake."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, bool | int | float):
        return float(value)
    return str(value)


# ── Principals ──────────────────────────────────────────────────────────────

#: The organization of every :func:`crm_user` (WS-53 CRM-0). No CRM route
#: reads it today. The kill switch reads it, and :func:`crm_switch_on` lists it.
CRM_TEST_ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"


def crm_user(
    email: str = "vjvarada@fracktal.in",
    *,
    features: str = "*",
    organization_id: str | None = CRM_TEST_ORG,
) -> Any:
    """A caller holding ``feature:crm``, a member of :data:`CRM_TEST_ORG`.

    Built with the real ``build_access`` so the permission the routes are gated
    on is the one this principal actually resolves.
    """
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access([features]),
        organization_id=organization_id,
    )


def crm_switch_on(monkeypatch: Any, orgs: str = CRM_TEST_ORG) -> None:
    """Turn the CRM kill switch on for *orgs* (WS-53 CRM-0).

    The ONE place a test opens the switch. A suite that mounts the ``/crm``
    router calls it once. Without it, each ``/crm`` route answers 404. The
    suites that call a route function directly do not run the router
    dependencies, so they do not need it.
    """
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "crm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "crm_orgs", orgs, raising=False)


def bind_db(monkeypatch: Any, fake: FakeCrmDB, modules: tuple[Any, ...]) -> None:
    """Point each CRM submodule's DB seam at ``fake``.

    Per-module and not per-package because each module imports its seam from
    ``core`` by name, so patching ``core`` alone would not reach them.

    H2 note: the request-handler modules now acquire sessions through
    ``_tenant_session`` — an async context manager that begins a transaction,
    issues ``SET LOCAL app.tenant_id`` and commits on clean exit. The fake
    mirrors only the SHAPE (``async with … as db``) plus commit-on-clean-exit,
    so "these writes share one transaction" stays an OBSERVABLE fact
    (``db.committed == 1``) and a handler that raises mid-block commits
    nothing here just as it commits nothing against Postgres. Transaction/GUC
    plumbing stays out of the mirror — ``test_tenant_session.py`` owns that.

    The three H4 leaves (``sync_zoho``, ``auto_lead``, ``broker_handlers``)
    deliberately stay on the unbound ``_get_db`` seam, so both names are
    patched wherever a module carries them.
    """
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None) -> Any:
        yield fake
        await fake.commit()

    async def _get_db() -> FakeCrmDB:
        return fake

    for module in modules:
        if hasattr(module, "_tenant_session"):
            monkeypatch.setattr(module, "_tenant_session", _tenant_session)
        if hasattr(module, "_get_db"):
            monkeypatch.setattr(module, "_get_db", _get_db)
