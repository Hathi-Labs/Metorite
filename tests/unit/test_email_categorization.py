"""Unit tests for sender auto-categorization (`_categorize_senders_job`).

A sender's category is PROJECTED from the rule engine's per-message labels
(email_messages.categories) — the same categorization the rest of the app shows.
There is no second classifier: the old cold-start LLM fallback (persisted as
provisional 'inferred' guesses) ran on a thin signal, never self-corrected and
misled the cleaner, so it was deleted outright. The job only projects rules,
clears projections whose labels are gone, and retires leftover 'inferred' rows.
DB mocked.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.routes import email as m

from tests.unit._email_fakes import bind_db


def _srow(email, *, name="N", volume=10, cur_source=None):
    return SimpleNamespace(
        email=email, name=name, volume=volume, cur_source=cur_source)


def _trow(email, label, n):
    return SimpleNamespace(email=email, label=label, n=n)


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows, self._one = rows or [], one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _FakeDB:
    """Answers each of the job's distinct queries by SQL shape and records the
    sender upserts + commit/close lifecycle."""

    def __init__(self, senders, tally=None, account_email="me@acme.com"):
        self.senders, self.tally = senders, tally or []
        self.account_email = account_email
        self.inserts: list[dict] = []
        self.deleted_inferred = False
        self.cleared_rule: list[str] = []
        self.commits = 0
        self.closed = False

    async def execute(self, clause, params=None):
        sql = str(clause)
        if "LEFT JOIN email_senders" in sql and "ORDER BY COUNT(*) DESC" in sql:
            return _Result(rows=self.senders)
        if "SELECT email_address FROM email_accounts" in sql:
            return _Result(one=SimpleNamespace(email_address=self.account_email))
        if "unnest(em.categories)" in sql:
            return _Result(rows=self.tally)
        upper = sql.lstrip().upper()
        if upper.startswith("INSERT INTO EMAIL_SENDERS"):
            self.inserts.append(params or {})
        if upper.startswith("DELETE FROM EMAIL_SENDERS"):
            if "'INFERRED'" in upper:
                self.deleted_inferred = True
            elif "'RULE'" in upper:
                self.cleared_rule.extend((params or {}).get("emails", []))
        return _Result()

    async def commit(self):
        self.commits += 1

    async def close(self):
        self.closed = True


def _run(db):
    # EM-T1b-2: the job opens `_tenant_session()` blocks, never `_get_db`.
    return patch.object(
        m.automation.senders, "_tenant_session", bind_db(db)
    ), patch.object(
        m.automation.identity, "resolve_org_domains",
        AsyncMock(return_value=frozenset())
    )


def test_the_sender_llm_classifier_no_longer_exists() -> None:
    """Structural guard: the parallel LLM sender classifier is gone for good.

    Its replacement is a pure projection of the rule engine's labels. If someone
    reintroduces a second opinion here, the Inbox Cleaner starts disagreeing with
    the chips, the quick filters and the digest all over again — which is the
    exact drift this deletion paid off.
    """
    assert not hasattr(m.automation.senders, "_llm_categorize_senders")


async def test_projects_rule_category() -> None:
    # The rules labelled 4 of this sender's messages "Marketing" → project it
    # straight from the rule engine; the source is 'rule'.
    db = _FakeDB([_srow("promos@shop.com")],
                 tally=[_trow("promos@shop.com", "marketing", 4)])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert len(db.inserts) == 1
    assert db.inserts[0]["cat"] == "Marketing"
    assert db.inserts[0]["src"] == "rule"
    assert db.commits >= 1


async def test_label_casing_and_whitespace_do_not_defeat_the_rollup() -> None:
    """The LABEL action stores a rule's label verbatim, so a hand-edited rule can
    write " NEWSLETTER ". The rollup matches on LOWER(TRIM(...)) in SQL; this
    pins the Python side to the same canonicalisation."""
    assert m.automation.senders.canonical_cleanup_category(" NEWSLETTER ") \
        == "Newsletter"
    assert m.automation.senders.canonical_cleanup_category("cold email") \
        == "Cold Email"
    # Conversation labels are not cleanup categories.
    assert m.automation.senders.canonical_cleanup_category("Reply") is None


async def test_reply_active_sender_projects_conversation() -> None:
    db = _FakeDB([_srow("colleague@x.com")],
                 tally=[_trow("colleague@x.com", "reply", 3)])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert db.inserts[0]["cat"] == "Conversation"
    assert db.inserts[0]["src"] == "rule"


async def test_cold_start_writes_no_guess() -> None:
    # No rule labels yet → NOTHING is written. The sender stays uncategorized
    # until the rules (or the uncategorized sweep) label its mail.
    db = _FakeDB([_srow("news@a.com")], tally=[])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert db.inserts == []


async def test_projection_is_cleared_when_its_labels_are_gone() -> None:
    """A sender whose labels were removed (rule deleted, chips cleared) must lose
    its projection, else the digest and the sender_category search filter keep
    reporting a category the cleaner no longer shows."""
    db = _FakeDB([_srow("news@a.com")], tally=[])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert db.cleared_rule == ["news@a.com"]


async def test_leftover_inferred_guesses_are_retired() -> None:
    # A sender still carrying a provisional 'inferred' guess with no rule
    # coverage: no new category is written, and the stale inferred row is
    # deleted so it stops surfacing in the cleaner.
    db = _FakeDB([_srow("news@a.com", cur_source="inferred")], tally=[])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert db.inserts == []
    assert db.deleted_inferred is True


async def test_excludes_the_account_owner_as_a_sender() -> None:
    # The account's own address must never be categorized (else it surfaces as
    # "you email yourself"); teammates/externals are kept.
    db = _FakeDB(
        [_srow("me@acme.com"), _srow("news@a.com")],
        tally=[_trow("me@acme.com", "marketing", 5),
               _trow("news@a.com", "marketing", 5)],
    )
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert {i["email"] for i in db.inserts} == {"news@a.com"}   # self dropped


async def test_user_override_is_never_touched() -> None:
    db = _FakeDB([_srow("pinned@x.com", cur_source="user")],
                 tally=[_trow("pinned@x.com", "marketing", 9)])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    assert db.inserts == []          # 'user' rows are excluded from candidates
    assert db.cleared_rule == []     # and are never cleared either


async def test_no_work_when_nothing_to_categorize() -> None:
    db = _FakeDB([], tally=[])
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)
    # The seam commits the read block on exit. Nothing else may run: no
    # upsert, no clear, and no second block for the `inferred` delete.
    assert db.inserts == [] and db.cleared_rule == []
    assert db.deleted_inferred is False


async def test_failure_during_job_is_swallowed_and_cleans_up() -> None:
    db = _FakeDB([_srow("x@a.com")], tally=[])
    db.commit = AsyncMock(side_effect=RuntimeError("db down"))
    p1, p2 = _run(db)
    with p1, p2:
        await m._categorize_senders_job("acc-1", 25)  # must not raise
        opened = len(m.automation.senders._tenant_session.calls)
    # The first block failed on exit, so the second block never opened. The
    # seam closes the session itself, so the fake records no close.
    assert opened == 1
    assert db.deleted_inferred is False
