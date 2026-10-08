"""Unit tests for the centralized guards in `_upsert_rule_pattern` — the single
write choke point for learned classification patterns. It must refuse to:
  1. sender-pin a conversation-status rule (Reply / Awaiting / FYI / Done);
  2. pin the mailbox's OWN address to any rule.
Both anti-patterns were seen live ("vjvarada@… → Reply") from the (formerly
unguarded) label-sync learner; the guard here is the backstop for every path."""
from __future__ import annotations

from types import SimpleNamespace

from gateway.routes import email as m

_upsert = m.automation.rules._upsert_rule_pattern


class _Result:
    def __init__(self, row: object | None, rows: list | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> object | None:
        return self._row

    def fetchall(self) -> list:
        return self._rows


class _FakeDB:
    """Minimal async DB stub that answers the two metadata SELECTs and records
    whether an INSERT (i.e. an actual pattern write) happened.

    Since EM-T8e-1 the own-address guard reads EACH mailbox of the member
    (``identity.resolve_self_addresses``). ``acct_row`` is the one mailbox of
    that member here, so the guard sees the same address it saw before."""

    def __init__(self, rule_row: object | None, acct_row: object | None) -> None:
        self.rule_row = rule_row
        self.acct_row = acct_row
        self.inserted = False

    async def execute(self, clause: object, params: dict | None = None) -> _Result:
        sql = str(clause)
        if "FROM email_rules" in sql:
            return _Result(self.rule_row)
        if "FROM email_accounts" in sql:
            box = SimpleNamespace(
                id="acc-1", address=getattr(self.acct_row, "email_address", ""),
                label=None)
            return _Result(self.acct_row, [box] if self.acct_row else [])
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserted = True
        return _Result(None)


async def test_conversation_rule_is_never_pinned_by_name() -> None:
    # system_type NULL → recognized by name fallback ("Reply" → REPLY).
    db = _FakeDB(SimpleNamespace(name="Reply", system_type=None),
                 SimpleNamespace(email_address="me@fracktal.in"))
    stored = await _upsert(db, "acc-1", "rule-reply", "souradeep@iisc.ac.in",
                           False, "LABEL_ADDED", "why", None, None,
                           pattern_type="FROM")
    assert db.inserted is False
    assert stored is False


async def test_conversation_rule_is_never_pinned_by_system_type() -> None:
    db = _FakeDB(SimpleNamespace(name="Custom", system_type="AWAITING_REPLY"),
                 SimpleNamespace(email_address="me@fracktal.in"))
    stored = await _upsert(db, "acc-1", "rule-x", "someone@x.com", True,
                           "LABEL_REMOVED", "why", None, None,
                           pattern_type="FROM")
    assert db.inserted is False
    assert stored is False


async def test_own_address_is_never_pinned() -> None:
    # A non-conversation rule, but the value IS the mailbox's own address.
    db = _FakeDB(SimpleNamespace(name="Newsletter", system_type=None),
                 SimpleNamespace(email_address="vjvarada@fracktal.in"))
    stored = await _upsert(db, "acc-1", "rule-news", "vjvarada@fracktal.in",
                           False, "LABEL_ADDED", "why", None, None,
                           pattern_type="FROM")
    assert db.inserted is False
    assert stored is False


async def test_normal_cleanup_pattern_is_written() -> None:
    # External sender + a sender-stable cleanup rule → the pattern is persisted.
    # The return value matters as much as the write: the Fix flow honours it to
    # decide whether to tell the user the correction was learned. A success path
    # that stored the row but returned None (the #105 regression) is a lie the
    # UI relays verbatim — so assert BOTH the write and the True return.
    db = _FakeDB(SimpleNamespace(name="Newsletter", system_type=None),
                 SimpleNamespace(email_address="me@fracktal.in"))
    stored = await _upsert(db, "acc-1", "rule-news", "digest@substack.com",
                           False, "AI", "why", None, None, pattern_type="FROM")
    assert db.inserted is True
    assert stored is True
