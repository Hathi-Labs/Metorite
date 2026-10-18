"""Unit tests for the search bar's query surface (`/email/search`).

Covers what the Outlook-style search bar composes onto a query: a folder SCOPE
(a real folder, `all`, or `starred`), closable tag pills, and from:/to: pills —
plus the filters-only case, where pills narrow the mail with no search text.

The DB session is mocked, so the generated SQL and its bound params are
inspected rather than executed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m
from tests.unit._email_fakes import bind_db


async def _run_search(**kw):
    """Drive search_messages with a mocked DB; return (resp, sql, params)."""
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.scalar.return_value = 0     # count query
        r.fetchall.return_value = []  # page query → no rows
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    args = dict(
        q=None, account_id="a0000000-0000-4000-8000-000000000001", folder=None, label=None, labels=None,
        from_addr=None, to_addr=None, received_after=None, received_before=None,
        is_read=None, is_starred=None, has_attachments=None,
        sender_category=None, importance=None, hybrid=False,
        page=1, page_size=50,
        user=SimpleNamespace(email="u@example.com"),
    )
    args.update(kw)
    with patch.object(m.transport.search, "_tenant_session", bind_db(db)):
        resp = await m.search_messages(**args)
    sql = " ".join(s for s, _ in captured)
    params: dict = {}
    for _s, p in captured:
        params.update(p)
    return resp, sql, params


# ── Folder scope ──────────────────────────────────────────────────────────────


async def test_scope_all_excludes_junk_and_trash():
    # The "All folders" scope means every folder EXCEPT the mail the user threw
    # away — otherwise spam would bury real hits.
    _resp, sql, params = await _run_search(q="invoice", folder="all")
    assert "LOWER(em.folder) <> ALL(:folder_excludes)" in sql
    assert params["folder_excludes"] == ["junk", "trash"]
    assert "folder" not in params  # no equality predicate for a real folder


async def test_searching_all_folders_still_finds_your_own_mail():
    """Browsing All hides the user's own mail; SEARCHING All must not.

    "What did I tell them?" is one of the commonest reasons to search, and a
    scope the UI labels "All folders" that quietly skipped the user's own
    replies would fail the query it exists to serve. Drafts are the same case:
    half-written text is a poor thing to browse but a fine thing to go looking
    for. This is the one place the two meanings of "all" are allowed to
    diverge, so it is pinned explicitly — otherwise someone later "restores
    consistency" and breaks search."""
    _resp, _sql, params = await _run_search(q="invoice", folder="all")
    assert not {"sent", "drafts"} & set(params["folder_excludes"])


async def test_scope_real_folder_matches_case_insensitively():
    _resp, sql, params = await _run_search(q="invoice", folder="inbox")
    assert "LOWER(em.folder) = LOWER(:folder)" in sql
    assert params["folder"] == "inbox"
    assert "folder_excludes" not in params


async def test_scope_starred_is_a_flag_not_a_folder():
    _resp, sql, params = await _run_search(q="invoice", folder="starred")
    assert "em.is_starred = true" in sql
    assert "folder" not in params


async def test_no_scope_spans_every_folder():
    # `em.folder` is still SELECTed as a column — assert on the PREDICATES.
    _resp, sql, params = await _run_search(q="invoice")
    assert "LOWER(em.folder) = LOWER(:folder)" not in sql
    assert "LOWER(em.folder) <> ALL(:folder_excludes)" not in sql
    assert "folder" not in params and "folder_excludes" not in params


async def test_junk_stays_reachable_when_scoped_explicitly():
    # "all" hides junk, but selecting Junk as the scope must still search it.
    _resp, sql, params = await _run_search(q="invoice", folder="junk")
    assert "LOWER(em.folder) = LOWER(:folder)" in sql
    assert params["folder"] == "junk"


# ── Filters-only search (no text) ─────────────────────────────────────────────


async def test_filters_only_search_skips_fts_and_orders_by_recency():
    # Pills with no typed text is a first-class search ("everything tagged
    # Newsletter"). There is no relevance to rank by, so recency orders it, and
    # no headline is computed (an empty tsquery would yield an unmarked snippet
    # that the list would render as a match that matched nothing).
    _resp, sql, _params = await _run_search(q=None, labels=["Newsletter"])
    assert "websearch_to_tsquery" not in sql
    assert "ts_headline" not in sql
    assert "0.0 AS rank" in sql
    assert "'' AS highlight" in sql
    assert "ORDER BY em.received_at DESC" in sql


async def test_blank_text_is_treated_as_no_text():
    _resp, sql, _params = await _run_search(q="   ", labels=["Newsletter"])
    assert "websearch_to_tsquery" not in sql
    assert "ORDER BY em.received_at DESC" in sql


async def test_text_search_ranks_and_highlights():
    _resp, sql, params = await _run_search(q="invoice")
    assert "websearch_to_tsquery('english', :q)" in sql
    assert "ts_rank_cd" in sql
    assert "ts_headline" in sql
    # Only the newest RANK_WINDOW matches pay for ts_rank_cd, and they come
    # first (incident 2026-10-18, fix round 1: 22 000 ranked hits took 52.8 s).
    assert "ORDER BY (rw.id IS NOT NULL) DESC, rank DESC, em.received_at DESC" in sql
    assert "CASE WHEN rw.id IS NOT NULL THEN ts_rank_cd(" in sql
    assert "LIMIT :rank_window) rw ON rw.id = em.id" in sql
    assert params["rank_window"] == 500
    assert params["q"] == "invoice"


async def test_hybrid_without_text_does_not_embed():
    # Nothing to embed with no query — must not attempt a vector join.
    with patch("email_ingestion.email_embeddings.embed_query",
               AsyncMock(return_value=[0.1] * 1536)) as embed:
        resp, sql, _params = await _run_search(
            q=None, labels=["Newsletter"], hybrid=True)
    embed.assert_not_called()
    assert resp["hybrid"] is False
    assert "email_embeddings" not in sql


# ── Tag pills ─────────────────────────────────────────────────────────────────


async def test_tag_pills_and_together_over_labels_or_categories():
    # Rule-engine tags ("Reply", "Newsletter") live in categories; a user's own
    # labels live in labels. Both are searchable the same way, and stacked pills
    # narrow (AND), which is how a row of filter chips reads.
    _resp, sql, params = await _run_search(q=None, labels=["Reply", "Done"])
    # Each pill matches a user label OR a rule-engine category, normalised on
    # both sides: the rule engine stores a rule's name VERBATIM, so an exact
    # comparison dropped any label differing by case or a stray space.
    assert "LOWER(TRIM(l)) = :tag_0" in sql
    assert "LOWER(TRIM(c)) = :tag_0" in sql
    assert "LOWER(TRIM(l)) = :tag_1" in sql
    assert params["tag_0"] == "reply"
    assert params["tag_1"] == "done"


async def test_legacy_single_label_still_applies_alongside_pills():
    _resp, _sql, params = await _run_search(q=None, label="Work", labels=["Reply"])
    assert params["tag_0"] == "work"   # legacy single label first
    assert params["tag_1"] == "reply"


async def test_blank_tags_are_ignored():
    _resp, sql, params = await _run_search(q="hi", labels=["", "   "])
    assert "tag_0" not in params
    assert "ANY(COALESCE(em.categories" not in sql


# ── from: / to: pills ─────────────────────────────────────────────────────────


async def test_from_pill_matches_address_or_display_name():
    _resp, sql, params = await _run_search(q=None, from_addr="Fracktal Finance")
    assert "LOWER(em.from_address->>'email') LIKE :from_addr" in sql
    assert "LOWER(em.from_address->>'name') LIKE :from_addr" in sql
    assert params["from_addr"] == "%fracktal finance%"


async def test_to_pill_matches_any_to_or_cc_recipient():
    _resp, sql, params = await _run_search(q=None, to_addr="Alice@X.com")
    assert "jsonb_array_elements" in sql
    assert "em.to_addresses" in sql and "em.cc_addresses" in sql
    assert params["to_addr"] == "%alice@x.com%"


async def test_no_address_pills_means_no_address_predicates():
    _resp, sql, params = await _run_search(q="hi")
    assert "from_addr" not in params and "to_addr" not in params
    assert "jsonb_array_elements" not in sql


# ── State filters still compose with everything above ─────────────────────────


async def test_state_filters_compose_with_scope_and_pills():
    from datetime import datetime
    _resp, sql, params = await _run_search(
        q="invoice", folder="all", labels=["Receipt"], from_addr="acme",
        is_read=False, has_attachments=True, sender_category="Marketing",
        received_after="2026-05-01",
    )
    assert "em.is_read = :is_read" in sql
    assert "em.has_attachments = :has_attachments" in sql
    assert "EXISTS (SELECT 1 FROM email_senders se" in sql
    assert "em.received_at >= :received_after" in sql
    assert params["is_read"] is False
    assert params["has_attachments"] is True
    assert isinstance(params["received_after"], datetime)
    # …and the scope + pills are still applied alongside them.
    assert params["folder_excludes"] == ["junk", "trash"]
    assert params["tag_0"] == "receipt"
    assert params["from_addr"] == "%acme%"


async def test_importance_filter_matches_case_insensitively():
    # The importance pill ("High"/"Normal"/"Low") filters provider importance;
    # stored casing varies ('High' vs 'high'), so the predicate lowercases both.
    _resp, sql, params = await _run_search(q=None, importance="High")
    assert "LOWER(em.importance) = LOWER(:importance)" in sql
    assert params["importance"] == "High"


async def test_no_importance_pill_means_no_importance_predicate():
    _resp, sql, params = await _run_search(q="hi")
    assert "em.importance) = LOWER(:importance)" not in sql
    assert "importance" not in params


# ── The All view and the All scope differ, on purpose ─────────────────────────


async def _run_messages_list(folder: str) -> tuple[str, dict]:
    """Drive the sidebar's message list and return its SQL + bound params."""
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.scalar.return_value = 0
        r.fetchall.return_value = []
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        await m.list_messages(
            account_id="a0000000-0000-4000-8000-000000000001", folder=folder, label=None, query=None,
            thread_id=None, received_after=None, received_before=None,
            is_read=None, is_starred=None, has_attachments=None,
            importance=None, from_email=None, sender_category=None,
            sort="newest", page=1, page_size=50,
            user=SimpleNamespace(email="u@example.com"),
        )
    params: dict = {}
    for _s, p in captured:
        params.update(p)
    return " ".join(s for s, _ in captured), params


async def test_browsing_all_hides_the_users_own_mail():
    """All answers "what came in?" — and the user's own mail never came in.

    Sent used to fall into it (442 messages on the live account), so every
    conversation appeared twice and the list read as an activity log rather than
    an inbox; drafts are unfinished text with no counterparty, which belongs in
    a composer rather than a reading list. Excluding both costs nothing in
    reach: each has its own sidebar entry, and the thread view ignores the
    folder filter entirely, so opening a conversation still shows both sides."""
    _sql, params = await _run_messages_list(folder="all")
    assert {"sent", "drafts"} <= set(params["folder_excludes"])


async def test_messages_list_all_folder_matches_search_all_scope():
    """The sidebar's All view (`/email/messages`) and the search bar's All scope
    (`/email/search`) resolve through the one core.folder_scope, and agree on
    everything the user has THROWN AWAY. They diverge on sent mail alone, which
    is asserted separately above and below — this test guards the shared part,
    so junk/trash can't come to mean two different things."""
    sql, browse = await _run_messages_list(folder="all")
    _resp, _ssql, search = await _run_search(q="invoice", folder="all")

    assert "LOWER(em.folder) <> ALL(:folder_excludes)" in sql
    thrown_away = {"junk", "trash"}
    assert thrown_away <= set(browse["folder_excludes"])
    assert thrown_away <= set(search["folder_excludes"])
    # The user's OWN mail — sent and drafts — is the only sanctioned difference:
    # neither ever arrived, so neither belongs in a view of what came in, but
    # both are legitimate things to go looking for. If any other folder ever
    # diverges, one of the two surfaces has drifted rather than been designed.
    assert (set(browse["folder_excludes"]) ^ set(search["folder_excludes"])
            == {"sent", "drafts"})


async def test_facet_chips_count_the_same_mail_the_list_shows():
    """The chip row sits directly above the list and claims to count it.

    Facets are the third caller of folder_scope, and the one most likely to be
    missed: it takes the same `folder` argument, so if it had picked up search's
    wider meaning the All view would offer a "Receipt 12" chip whose mail the
    list beneath it never shows — the windowed-caption-over-unwindowed-query
    defect, one surface further along."""
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.fetchall.return_value = []
        r.fetchone.return_value = None
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        await m.transport.messages.message_facets(
            account_id="a0000000-0000-4000-8000-000000000001", folder="all",
            user=SimpleNamespace(email="u@example.com"),
        )
    params: dict = {}
    for _s, p in captured:
        params.update(p)

    _sql, browse = await _run_messages_list(folder="all")
    assert set(params["folder_excludes"]) == set(browse["folder_excludes"])


# ── Frontend ⟷ backend constant parity ────────────────────────────────────────


def test_folder_all_excludes_matches_the_frontend_constant():
    """The set of folders the "All" scope hides is declared TWICE — the server's
    ``core.FOLDER_ALL_EXCLUDES`` and the client's ``FOLDER_ALL_EXCLUDES`` in
    ``emailStore.ts`` (the sidebar All *view* filters locally off it). They must
    stay identical, else the All view and the All search scope silently disagree
    on whether a message is shown. This guards the client copy, since the CI
    lint/test gate never builds the frontend."""
    import re
    from pathlib import Path

    from gateway.routes.email.core import FOLDER_ALL_EXCLUDES

    repo = Path(__file__).resolve().parents[2]
    store = (
        repo / "workbench/control_plane/src/app/email/lib/emailStore.ts"
    ).read_text(encoding="utf-8")
    m_ = re.search(r"FOLDER_ALL_EXCLUDES\s*=\s*new Set\(\[([^\]]*)\]\)", store)
    assert m_, "FOLDER_ALL_EXCLUDES not found in emailStore.ts"
    frontend = {
        tok.strip().strip("\"'") for tok in m_.group(1).split(",") if tok.strip()
    }
    assert frontend == set(FOLDER_ALL_EXCLUDES), (
        f"folder-exclude drift: backend={set(FOLDER_ALL_EXCLUDES)} "
        f"frontend={frontend} — update both core.py and emailStore.ts together"
    )
