"""Unit tests for the uncategorized-inbox sweep (automation/cleanup.py).

The sweep PROJECTS existing categorization onto inbox mail the rules never
reached — learned patterns first, then per-sender history, then per-domain
history. It must never invent a category: a message with no evidence is reported
as `no_evidence` and left alone for an actual rules run. These tests pin that
boundary, because a second classifier here is exactly the parallel-categorization
drift the rest of the email stack keeps paying down.
"""
from __future__ import annotations

from types import SimpleNamespace

from gateway.routes.email.automation import cleanup as c
from tests.unit._email_fakes import bind_db


def _as_push(fake_apply):
    """Adapt an ``apply_label``-shaped fake to the sweep's ``push_label`` seam.

    Since EM-T1b-2 fix round 1 the sweep calls the provider half
    (``push_label``) with no session open, then mirrors in its own block. The
    fake runs where the provider call runs, and a raise there still means
    "nothing written"."""
    async def _push(provider, mid, pmid, label):
        await fake_apply(None, provider, mid, pmid, label)
        return label
    return _push


def _msg(sender, subject="Hi", mid="m1"):
    return SimpleNamespace(
        id=mid, provider_message_id=f"p-{mid}", subject=subject,
        from_address={"email": sender, "name": ""}, received_at=None,
    )


def _decide(msg, *, patterns=None, rule_labels=None, sender=None, domain=None):
    return c._decide(msg, patterns or {}, rule_labels or {},
                     sender or {}, domain or {})


# ── evidence 1: learned patterns ────────────────────────────────────────────


def test_learned_from_pattern_wins() -> None:
    verdict = _decide(
        _msg("deals@shop.com"),
        patterns={"r1": {"include": [("FROM", "deals@shop.com")], "exclude": []}},
        rule_labels={"r1": "Marketing"},
    )
    assert verdict == ("Marketing", "learned pattern")


def test_learned_exclude_pattern_blocks_its_own_rule() -> None:
    """The user explicitly taught us this sender is NOT that rule. Honouring the
    exclude is the difference between learning and nagging."""
    verdict = _decide(
        _msg("deals@shop.com"),
        patterns={"r1": {"include": [("FROM", "shop.com")],
                         "exclude": [("FROM", "deals@shop.com")]}},
        rule_labels={"r1": "Marketing"},
    )
    assert verdict is None


def test_pattern_pointing_at_a_conversation_rule_is_ignored() -> None:
    """Only rules whose LABEL is a cleanup category feed the sweep. A pattern on
    a Reply/Awaiting rule is Reply Zero's business — the sweep must not stamp a
    conversation label as if it were a cleanup category."""
    verdict = _decide(
        _msg("boss@work.com"),
        patterns={"r1": {"include": [("FROM", "boss@work.com")], "exclude": []}},
        rule_labels={},          # the Reply rule was filtered out upstream
    )
    assert verdict is None


# ── evidence 2 & 3: sender / domain consensus ───────────────────────────────


def test_sender_history_consensus() -> None:
    verdict = _decide(_msg("news@site.com"),
                      sender={"news@site.com": {"Newsletter": 6}})
    assert verdict == ("Newsletter", "sender history")


def test_split_sender_history_is_not_a_coin_flip() -> None:
    """A sender evenly split between two categories teaches nothing. Guessing
    would be worse than leaving it uncategorized, because the cleaner offers
    destructive bulk actions on top of the category."""
    verdict = _decide(_msg("mixed@site.com"),
                      sender={"mixed@site.com": {"Newsletter": 3, "Receipt": 3}})
    assert verdict is None


def test_single_labelled_message_is_below_the_sender_bar() -> None:
    verdict = _decide(_msg("new@site.com"),
                      sender={"new@site.com": {"Newsletter": 1}})
    assert verdict is None


def test_domain_history_covers_a_brand_new_subaddress() -> None:
    verdict = _decide(_msg("billing@stripe.com"),
                      domain={"stripe.com": {"Receipt": 9}})
    assert verdict == ("Receipt", "domain history (stripe.com)")


def test_your_own_company_domain_never_forms_a_consensus() -> None:
    """Your own domain is a shared domain — every colleague sends from it.

    Found live: a dozen automated @company alerts labelled Notification sat six
    messages short of a domain consensus that would have stamped Notification
    across thousands of internal colleague emails. Same failure mode as
    gmail.com, except it lands on the people you actually work with.
    """
    verdict = c._decide(
        _msg("colleague@fracktal.in"), {}, {}, {},
        {"fracktal.in": {"Notification": 40}},
        internal_domains=frozenset({"fracktal.in"}),
    )
    assert verdict is None


def test_sender_level_evidence_survives_the_internal_domain_block() -> None:
    """Only the domain BLANKET is removed. A specific internal address the rules
    have labelled consistently is still real evidence about that address."""
    verdict = c._decide(
        _msg("alerts@fracktal.in"), {}, {},
        {"alerts@fracktal.in": {"Notification": 9}},
        {"fracktal.in": {"Notification": 40}},
        internal_domains=frozenset({"fracktal.in"}),
    )
    assert verdict == ("Notification", "sender history")


def test_shared_free_mail_domains_never_form_a_consensus() -> None:
    """Every personal contact shares gmail.com. Inheriting one newsletter's
    category across that domain would bulk-label the user's actual humans."""
    verdict = _decide(_msg("friend@gmail.com"),
                      domain={"gmail.com": {"Marketing": 50}})
    assert verdict is None


def test_sender_history_outranks_domain_history() -> None:
    verdict = _decide(
        _msg("support@stripe.com"),
        sender={"support@stripe.com": {"Notification": 5}},
        domain={"stripe.com": {"Receipt": 40}},
    )
    assert verdict == ("Notification", "sender history")


def test_no_evidence_yields_nothing() -> None:
    assert _decide(_msg("stranger@nowhere.io")) is None


def test_message_with_no_sender_is_skipped() -> None:
    row = SimpleNamespace(id="m", provider_message_id="p", subject="x",
                          from_address={}, received_at=None)
    assert _decide(row) is None


# ── consensus helper ────────────────────────────────────────────────────────


def test_consensus_requires_both_volume_and_dominance() -> None:
    assert c._consensus({"Newsletter": 5}, 2, 0.8) == "Newsletter"
    assert c._consensus({"Newsletter": 1}, 2, 0.8) is None          # too few
    assert c._consensus({"Newsletter": 5, "Receipt": 4}, 2, 0.8) is None  # split


# ── Label restore (repair path) ─────────────────────────────────────────────
# This job WRITES to email_messages.categories, so a mistake here is destructive.
# It exists because labels live upstream while our copy can be lost.

async def test_restore_only_touches_messages_the_provider_reports() -> None:
    """A message with no upstream labels must be left ALONE, not cleared.

    Clearing it would let the repair job destroy a label that only ever existed
    locally — the exact failure it is meant to undo.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    updates: list[dict] = []

    class _DB:
        async def execute(self, clause, params=None):
            sql = str(clause)
            if "SELECT provider, credentials_encrypted" in sql:
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="gmail", credentials_encrypted="x")))
            if "UPDATE email_messages SET categories" in sql:
                updates.append(params or {})
                return SimpleNamespace(rowcount=1)
            return MagicMock(fetchone=MagicMock(return_value=None))

        async def commit(self): ...
        async def close(self): ...

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=True)
    provider.fetch_label_assignments = AsyncMock(return_value={
        "pm-1": ["Newsletter"],
        "pm-2": ["Marketing", "Receipt"],
        "pm-3": ["Newsletter"],
    })

    with patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_instantiate_provider", MagicMock(return_value=provider)), \
            patch.object(c, "_persist_rotated_creds", AsyncMock()), \
            patch("acb_llm.key_store.get_key_store",
                  MagicMock(return_value=MagicMock(decrypt=MagicMock(
                      return_value="{}")))):
        res = await c.restore_provider_labels("acc-1")

    # Exactly the three reported messages were written — nothing else.
    written = {p for u in updates for p in u["pmids"]}
    assert written == {"pm-1", "pm-2", "pm-3"}
    cats_by_pmid = {p: u["cats"] for u in updates for p in u["pmids"]}
    assert cats_by_pmid["pm-2"] == ["Marketing", "Receipt"]
    assert cats_by_pmid["pm-1"] == ["Newsletter"]
    # ...in one statement per DISTINCT label-set, not one per message. At
    # mailbox scale the per-message version is tens of thousands of round-trips
    # and the route times out on exactly the mailbox that needs repairing.
    assert len(updates) == 2
    assert res["messages"] == 3
    assert res["labels"] == 3      # Newsletter, Marketing, Receipt
    assert res["updated"] == 2


# ── paging: the sweep must FINISH, not stop after one page ──────────────────
# A cleaner that quietly handles the first N and reports success is worse than
# one that refuses, because the user stops looking.


async def test_sweep_pages_until_the_mailbox_runs_dry() -> None:
    """Every uncategorized message is seen exactly once, across pages.

    The tricky part is the offset. Categorized rows drop OUT of the query (they
    now carry a label), while no-evidence rows stay in it forever. Advancing the
    offset by a whole page would skip mail; not advancing it at all would re-read
    the same no-evidence rows until the runaway backstop fired. It must advance
    by exactly what stayed behind.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    msgs = [_msg("news@site.com", mid=f"m{i}") for i in (1, 2, 3)] + [
        _msg("stranger@nowhere.io", mid=f"m{i}") for i in (4, 5)
    ]
    labelled: set[str] = set()
    seen: list[str] = []
    pages: list[tuple[int, int]] = []

    async def fake_page(db, aid, limit, offset=0, internal=frozenset()):
        pages.append((limit, offset))
        remaining = [m for m in msgs if m.id not in labelled]
        return remaining[offset:offset + limit]

    async def fake_apply(db, provider, mid, pmid, label):
        labelled.add(mid)
        seen.append(mid)

    class _DB:
        async def execute(self, clause, params=None):
            # A live sweep looks up the account so it can authenticate before
            # writing labels; give it one (auth succeeds below).
            if "SELECT provider, credentials_encrypted" in str(clause):
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="gmail", credentials_encrypted="x")))
            return MagicMock(fetchone=MagicMock(return_value=None),
                             fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=True)

    import gateway.routes.email.automation.runner as runner

    with patch.object(c, "_SWEEP_PAGE", 2), \
            patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_provider_for_account_any", AsyncMock(
                return_value=(provider, MagicMock(), "own@x.com"))), \
            patch.object(c, "_persist_rotated_creds", AsyncMock()), \
            patch.object(c, "_uncategorized_inbox", fake_page), \
            patch.object(c, "_load_rule_patterns", AsyncMock(return_value={})), \
            patch.object(c, "_rule_label_by_id", AsyncMock(return_value={})), \
            patch.object(c, "_label_tallies", AsyncMock(return_value=(
                {"news@site.com": {"Newsletter": 6}}, {}))), \
            patch.object(runner, "push_label", _as_push(fake_apply)):
        res = await c.sweep_uncategorized("acc-1", 100, dry_run=False)

    # All five were scanned, each exactly once — no skips, no re-reads.
    assert res["scanned"] == 5
    assert res["categorized"] == 3
    assert res["no_evidence"] == 2
    assert res["exhausted"] is True
    assert seen == ["m1", "m2", "m3"]
    # Offsets step past only the rows that stayed uncategorized.
    assert pages == [(2, 0), (2, 0), (2, 1)]


async def test_live_sweep_aborts_when_provider_auth_fails() -> None:
    """A live run whose provider won't authenticate must ABORT with an error,
    not press on writing local-only labels. Those get logged APPLIED but Outlook
    wipes them on the next sync, so the message re-enters scope and the sweep
    re-applies it every cycle — false audit rows for writes that never landed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    applied: list[str] = []

    async def fake_page(db, aid, limit, offset=0, internal=frozenset()):
        return [_msg("news@site.com", mid="m1")]

    async def fake_apply(db, provider, mid, pmid, label):
        applied.append(mid)

    class _DB:
        async def execute(self, clause, params=None):
            if "SELECT provider, credentials_encrypted" in str(clause):
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="outlook", credentials_encrypted="x")))
            return MagicMock(fetchone=MagicMock(return_value=None),
                             fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=False)  # auth fails
    import gateway.routes.email.automation.runner as runner

    with patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_provider_for_account_any", AsyncMock(
                return_value=(provider, MagicMock(), "own@x.com"))), \
            patch.object(c, "_uncategorized_inbox", fake_page), \
            patch.object(c, "_load_rule_patterns", AsyncMock(return_value={})), \
            patch.object(c, "_rule_label_by_id", AsyncMock(return_value={})), \
            patch.object(c, "_label_tallies", AsyncMock(return_value=(
                {"news@site.com": {"Newsletter": 6}}, {}))), \
            patch.object(runner, "push_label", _as_push(fake_apply)):
        res = await c.sweep_uncategorized("acc-1", 100, dry_run=False)

    assert res.get("error") == "provider authentication failed"
    assert res["categorized"] == 0
    assert applied == []  # nothing was written


async def test_a_failed_apply_is_counted_not_swallowed() -> None:
    """When a provider label write fails (e.g. Graph throttling), the row is
    counted in `failed` — otherwise `categorized` and the decided total silently
    disagree and a throttled run still reports success."""
    from unittest.mock import AsyncMock, MagicMock, patch

    async def fake_page(db, aid, limit, offset=0, internal=frozenset()):
        if offset == 0:
            return [_msg("news@site.com", mid="m1"),
                    _msg("news@site.com", mid="m2")]
        return []

    async def flaky_apply(db, provider, mid, pmid, label):
        if mid == "m2":
            raise RuntimeError("429 Too Many Requests")

    class _DB:
        async def execute(self, clause, params=None):
            if "SELECT provider, credentials_encrypted" in str(clause):
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="gmail", credentials_encrypted="x")))
            return MagicMock(fetchone=MagicMock(return_value=None),
                             fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=True)
    import gateway.routes.email.automation.runner as runner

    with patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_provider_for_account_any", AsyncMock(
                return_value=(provider, MagicMock(), "own@x.com"))), \
            patch.object(c, "_persist_rotated_creds", AsyncMock()), \
            patch.object(c, "_uncategorized_inbox", fake_page), \
            patch.object(c, "_load_rule_patterns", AsyncMock(return_value={})), \
            patch.object(c, "_rule_label_by_id", AsyncMock(return_value={})), \
            patch.object(c, "_label_tallies", AsyncMock(return_value=(
                {"news@site.com": {"Newsletter": 6}}, {}))), \
            patch.object(runner, "push_label", _as_push(flaky_apply)):
        res = await c.sweep_uncategorized("acc-1", 100, dry_run=False)

    assert res["categorized"] == 1        # only m1 landed
    assert res["failed"] == 1             # m2 was counted, not swallowed
    assert res["by_category"]["Newsletter"] == 2  # both were DECIDED


async def test_sweep_honours_an_explicit_limit_and_says_it_stopped_short() -> None:
    """A bounded run must NOT claim the mailbox is exhausted."""
    from unittest.mock import AsyncMock, MagicMock, patch

    msgs = [_msg("stranger@nowhere.io", mid=f"m{i}") for i in range(20)]

    async def fake_page(db, aid, limit, offset=0, internal=frozenset()):
        return msgs[offset:offset + limit]

    class _DB:
        async def execute(self, clause, params=None):
            return MagicMock(fetchone=MagicMock(return_value=None),
                             fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    with patch.object(c, "_SWEEP_PAGE", 2), \
            patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_uncategorized_inbox", fake_page), \
            patch.object(c, "_load_rule_patterns", AsyncMock(return_value={})), \
            patch.object(c, "_rule_label_by_id", AsyncMock(return_value={})), \
            patch.object(c, "_label_tallies", AsyncMock(return_value=({}, {}))):
        res = await c.sweep_uncategorized("acc-1", 5, dry_run=True)

    assert res["scanned"] == 5
    assert res["exhausted"] is False


async def test_dry_run_pages_by_the_full_window() -> None:
    """Nothing is written in a preview, so nothing drops out of the query — the
    offset has to advance by the whole page or the preview loops forever on the
    same mail."""
    from unittest.mock import AsyncMock, MagicMock, patch

    msgs = [_msg("news@site.com", mid=f"m{i}") for i in range(4)]
    pages: list[int] = []

    async def fake_page(db, aid, limit, offset=0, internal=frozenset()):
        pages.append(offset)
        return msgs[offset:offset + limit]

    class _DB:
        async def execute(self, clause, params=None):
            return MagicMock(fetchone=MagicMock(return_value=None),
                             fetchall=MagicMock(return_value=[]))

        async def commit(self): ...
        async def close(self): ...

    with patch.object(c, "_SWEEP_PAGE", 2), \
            patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_uncategorized_inbox", fake_page), \
            patch.object(c, "_load_rule_patterns", AsyncMock(return_value={})), \
            patch.object(c, "_rule_label_by_id", AsyncMock(return_value={})), \
            patch.object(c, "_label_tallies", AsyncMock(return_value=(
                {"news@site.com": {"Newsletter": 6}}, {}))):
        res = await c.sweep_uncategorized("acc-1", 100, dry_run=True)

    assert pages == [0, 2, 4]
    assert res["scanned"] == 4
    assert res["categorized"] == 4
    assert res["exhausted"] is True


def test_the_sweep_never_touches_conversation_threads() -> None:
    """A message inside a live conversation is not the cleaner's to label.

    The thread's status IS its classification (#110), and most messages of a
    statused thread legitimately carry no chip — the status label sits on the
    latest inbound message only. The sweep read those bare messages as
    "uncategorized" and projected the sender's category back on: observed live
    2026-07-22, a repair stripped stale Receipt/Marketing chips from
    conversation threads and the very next sweep cycle re-applied 36 of them.
    The two systems fought; this is the armistice line.

    FYI is deliberately absent from the exclusion: it is also the default
    stamp for "nothing matched" (#111), so excluding FYI threads would have
    put 3,226 of the live account's 3,535 threads — newsletters included —
    beyond the cleaner's reach.
    """
    src = c._CLEANUP_SCOPE
    assert "email_thread_status" in src, (
        "the sweep no longer excludes conversation threads"
    )
    assert "'NEEDS_REPLY', 'AWAITING', 'DONE'" in src
    assert "'FYI'" not in src, (
        "excluding FYI threads makes almost the whole mailbox unsweepable"
    )


def test_the_sweep_never_scans_outbound_mail() -> None:
    """Cleanup categories describe INBOUND bulk mail — a message you wrote is
    never a Newsletter.

    Widening this sweep from the inbox to the whole mailbox silently pulled Sent
    into scope. Combined with a domain consensus on the user's own company
    domain, that would have stamped a category across everything they ever sent.
    """
    # Both conditions moved into _CLEANUP_SCOPE, the one definition the sweep
    # and the Cleaner's badge now share.
    src = c._CLEANUP_SCOPE
    assert "<> 'sent'" in src, "the sweep no longer excludes the Sent folder"
    assert "FROM email_accounts" in src, (
        "the sweep no longer excludes mail from the user's own address"
    )


async def test_restore_says_unsupported_rather_than_no_labels() -> None:
    """On a provider that can't list messages per label, an empty result is
    indistinguishable from "your mailbox has no labels".

    Reporting the latter told a real Outlook user with 1,909 labelled messages
    that they had none — the exact opposite of the truth this path restores.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    class _DB:
        async def execute(self, clause, params=None):
            if "SELECT provider, credentials_encrypted" in str(clause):
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="microsoft", credentials_encrypted="x")))
            raise AssertionError("must not touch messages on an unsupported provider")

        async def commit(self): ...
        async def close(self): ...

    # A provider WITHOUT the capability flag — i.e. everything but Gmail.
    provider = MagicMock()
    provider.SUPPORTS_LABEL_READBACK = False
    provider.authenticate = AsyncMock(
        side_effect=AssertionError("must not even authenticate"))

    with patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_instantiate_provider", MagicMock(return_value=provider)), \
            patch("acb_llm.key_store.get_key_store",
                  MagicMock(return_value=MagicMock(decrypt=MagicMock(
                      return_value="{}")))):
        res = await c.restore_provider_labels("acc-1")

    assert res["error"] == "unsupported"
    assert res["provider"] == "microsoft"
    assert res["updated"] == 0


def test_only_gmail_claims_label_readback() -> None:
    from email_ingestion.providers.base import BaseEmailProvider
    from email_ingestion.providers.gmail import GmailProvider
    from email_ingestion.providers.outlook import OutlookProvider

    assert BaseEmailProvider.SUPPORTS_LABEL_READBACK is False
    assert GmailProvider.SUPPORTS_LABEL_READBACK is True
    # Outlook inherits the default — Graph has no "list messages by category".
    assert OutlookProvider.SUPPORTS_LABEL_READBACK is False


async def test_restore_reports_auth_failure_instead_of_wiping() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    class _DB:
        async def execute(self, clause, params=None):
            if "SELECT provider, credentials_encrypted" in str(clause):
                return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
                    provider="gmail", credentials_encrypted="x")))
            raise AssertionError("must not touch messages when auth fails")

        async def commit(self): ...
        async def close(self): ...

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=False)

    with patch.object(c, "_tenant_session", bind_db(_DB())), \
            patch.object(c, "_instantiate_provider", MagicMock(return_value=provider)), \
            patch("acb_llm.key_store.get_key_store",
                  MagicMock(return_value=MagicMock(decrypt=MagicMock(
                      return_value="{}")))):
        res = await c.restore_provider_labels("acc-1")

    assert res["error"] == "auth-failed"
    assert res["updated"] == 0
