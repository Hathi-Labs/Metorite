"""The Knowledge manifest: every ``/knowledge`` route, and what each door does.

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 (the classes) and §8.1,
and ``external_agent_access.md`` §19.3 (MCP-P1a, AD-1). Board rows WS-61
(KB-4a) and WS-MCP (MCP-P1a).

**This table is the allowlist of both doors and their coverage record.**
Mira's Knowledge client (``acb_skills.knowledge_tools``) refuses a verb and
path that the table does not give to a tool. The MCP door serves only a row
with an ``op``. ``tests/unit/test_knowledge_chat_coverage.py`` walks the real
Knowledge router and fails on a route that the table does not carry, and on
a row that names a route the router does not serve.
``tests/unit/test_app_door_coverage.py`` holds the door half.

It moved here from ``acb_skills/knowledge_manifest.py`` in MCP-P1a, because
the edge and ``acb_auth`` cannot import ``acb_skills``. That module stays as
a re-export, so its readers do not change.

**The chat.** KB-4a builds two read tools. KB-5a gives
``POST /knowledge/proposals`` to the two propose tools, and nothing else.

**The door (MCP-P1a).** The reads that call no model, and ``propose``. The
search makes an embedding call, so it is off the door (AD-11). Each other
write waits for the approval path of MCP-P1b.

**An agent never publishes (§8.1, F2).** No tool and no op maps to a publish,
an accept, a reject, a restore or an archive. ``tests/unit/test_kb_proposals.py``
and ``tests/unit/test_app_door_coverage.py`` fail if one does.
"""

from __future__ import annotations

from acb_common.app_manifests.base import App, Page, Route

#: D-PM-40: the chat does not write who can see a thing.
_GRANT_REASON = (
    "D-PM-40: the chat does not write grants. Propose the grant in words, "
    "and a person applies it in the app."
)

#: F2 (§8.1): the agent never publishes. A person decides in the app.
_DECIDE_REASON = (
    "D-KB-4 and F2: an agent never publishes. A person accepts or rejects a "
    "proposal in the Knowledge app, and the route refuses chat and mcp."
)

#: KB-9a: a gap is closed by a person in the Knowledge app.
_GAP_DECISION_REASON = (
    "A person closes a gap in the Knowledge app: Dismiss needs "
    "knowledge:manage, and \"I wrote it\" links a page the person wrote. The "
    "chat does not close a gap for the member (knowledge_base.md §8.4)."
)

#: KB-2f: a favourite is a personal shortcut of the Knowledge app.
_FAVORITE_REASON = (
    "A favourite is the member's own shortcut in the Knowledge app, not "
    "company knowledge. The member stars a page in the app."
)

#: KC-1a: a consent and a disconnect are the member's own acts, in the browser.
_SOURCE_CONSENT_REASON = (
    "A member connects a file source in the browser: the consent page of the "
    "provider needs the member, and the callback checks the member of the "
    "session (knowledge_curation.md §7.1). The chat does not connect a drive."
)
_SOURCE_DISCONNECT_REASON = (
    "A disconnect can end the shared Google grant of the member, Gmail "
    "included (knowledge_base.md §7.4). The member or a manager does it in "
    "the Knowledge app, never the chat."
)
#: KC-8a: the entity registry. No chat tool reads or writes it yet. KC-10 and
#: KC-13 decide how the chat and an agent reach it (knowledge_curation.md §22).
_ENTITY_REASON = (
    "KC-8a builds the entity registry for the Knowledge app only. No chat tool "
    "maps to it yet, and KC-10 and KC-13 decide that (knowledge_curation.md §22)."
)

#: KB-AI-4: verifying a page is a person's act.
_VERIFY_REASON = (
    "A person verifies a page in the app. An agent never vouches for knowledge."
)

# ── The door reasons (MCP-P1a, external_agent_access.md §19) ────────────────

#: AD-18: a write that is not a proposal needs the member's approval, and
#: the approval path of MCP-P1b does not exist yet.
_WAITS_FOR_P1B = "Waits for the approval path of MCP-P1b."

#: F2 on the door: a publish, an archive and a restore are a person's acts.
_PERSON_PUBLISHES = (
    "D-KB-4 and F2: an agent never publishes. A person publishes, archives and "
    "restores a page in the Knowledge app."
)

#: AD-11: the door makes no model call.
_SEARCH_CALLS_A_MODEL = (
    "AD-11: the search makes an embedding call, and the door calls no model. "
    "Use list_pages with q to find a page by its title."
)

#: AD-10: the keys of a Knowledge answer that may leave as plain strings. The
#: edge sends each one plain only in a plain shape: an id, a time or a short
#: token. Each other string leaves as untrusted text.
_PLAIN: tuple[str, ...] = (
    "id", "space_id", "parent_id", "page_id", "target_id", "source", "kind",
    "state", "level", "status", "via", "reason", "target_kind", "provider",
    "archived_from_state", "created_at", "updated_at", "published_at",
    "archived_at", "review_at", "decided_at", "first_asked_at", "last_asked_at",
    "dismissed_at", "discovered_at", "modified_at", "ext_id", "parent_ext_id",
    "drive_id", "mime", "scope",
)

#: The cursor paging of ``GET /knowledge/recent`` (KB-2f).
_RECENT_PAGE = Page(limit="limit", cursor="cursor", next="next_cursor")

KNOWLEDGE = App(
    id="knowledge",
    name="Knowledge",
    reach="The spaces and pages of company knowledge that the member can read.",
    pane="/knowledge",
    feature="knowledge",
    router="gateway.routes.knowledge",
    finish_in="Knowledge, Review",
    routes=(
        # ── Spaces ──
        Route("GET", "/knowledge/spaces", "list_spaces", "A",
              op="list_spaces", title="List the spaces that the member can open.",
              plain=_PLAIN),
        Route("POST", "/knowledge/spaces", "", "C", planned="KB-5", off_door=_WAITS_FOR_P1B),
        Route("PATCH", "/knowledge/spaces/{space_id}", "", "C", planned="KB-5",
              off_door=_WAITS_FOR_P1B),
        Route("PUT", "/knowledge/spaces/{space_id}/grants", "", "X", reason=_GRANT_REASON),
        # ── Pages ──
        Route("GET", "/knowledge/pages", "", "A", planned="KB-5",
              op="list_pages",
              title="List the pages that the member can see, with no body. "
                    "Filter by space, parent, state or a word of the title (q).",
              plain=_PLAIN),
        Route("GET", "/knowledge/pages/{page_id}", "read_page", "A",
              op="get_page", title="Read one page, with its body, versions and links.",
              plain=_PLAIN),
        Route("POST", "/knowledge/pages", "", "B", planned="KB-5", off_door=_WAITS_FOR_P1B),
        Route("PATCH", "/knowledge/pages/{page_id}", "", "B", planned="KB-5",
              off_door=_WAITS_FOR_P1B),
        Route("POST", "/knowledge/pages/{page_id}/publish", "", "B", planned="KB-5",
              off_door=_PERSON_PUBLISHES),
        Route("POST", "/knowledge/pages/{page_id}/move", "", "B", planned="KB-5",
              off_door=_WAITS_FOR_P1B),
        Route("POST", "/knowledge/pages/{page_id}/archive", "", "C", planned="KB-5",
              off_door=_PERSON_PUBLISHES),
        Route("POST", "/knowledge/pages/{page_id}/restore", "", "C", planned="KB-5",
              off_door=_PERSON_PUBLISHES),
        Route("POST", "/knowledge/pages/{page_id}/revert", "", "B", planned="KB-5",
              off_door=_WAITS_FOR_P1B),
        # ── Home and favourites (KB-2f, §21.7) ── A favourite is the member's
        # own shortcut in the app, so neither door reads nor writes it.
        Route("GET", "/knowledge/recent", "", "A", planned="KB-5",
              op="list_recent", title="List the pages that changed last, newest first.",
              plain=_PLAIN, page=_RECENT_PAGE),
        Route("GET", "/knowledge/favorites", "", "X", reason=_FAVORITE_REASON),
        Route("POST", "/knowledge/pages/{page_id}/favorite", "", "X", reason=_FAVORITE_REASON),
        Route("DELETE", "/knowledge/pages/{page_id}/favorite", "", "X",
              reason=_FAVORITE_REASON),
        # KB-2g: one old version with its body, for the history modal.
        Route("GET", "/knowledge/pages/{page_id}/versions/{version}", "", "A",
              planned="KB-5", op="get_page_version",
              title="Read one old version of a page, with its body.",
              plain=_PLAIN, path_shapes=(("version", r"[0-9]{1,10}"),)),
        # KB-2h: the pages that link here, for the "Linked from" list.
        Route("GET", "/knowledge/pages/{page_id}/backlinks", "", "A", planned="KB-5",
              op="list_backlinks", title="List the pages that link to a page.",
              plain=_PLAIN),
        # ── Proposals (KB-5a, §8.1) ── The two propose tools and the door op
        # file a proposal and nothing else. Accept and reject are a person's.
        Route("POST", "/knowledge/proposals", "propose_page", "B", also=("propose_edit",),
              op="propose", proposal=True,
              title="Propose a new page (kind page) or an edit of a page (kind edit). "
                    "A person accepts or rejects it in Knowledge.",
              plain=_PLAIN),
        Route("GET", "/knowledge/proposals", "", "A", planned="KB-5b",
              op="list_proposals", title="List the proposals that the member can see.",
              plain=_PLAIN),
        Route("POST", "/knowledge/proposals/{proposal_id}/accept", "", "X",
              reason=_DECIDE_REASON),
        Route("POST", "/knowledge/proposals/{proposal_id}/reject", "", "X",
              reason=_DECIDE_REASON),
        # ── Search ── `search_company` already asks Knowledge (§20). The
        # Knowledge-only tool waits (§8.1, `search_knowledge` is deferred).
        Route("GET", "/knowledge/search", "", "A", planned="search_knowledge, deferred (§8.1)",
              off_door=_SEARCH_CALLS_A_MODEL),
        # ── Gaps (KB-9a, §8.4 door 3) ── the questions Knowledge could not
        # answer. A read may join the chat later. A person decides each close.
        Route("GET", "/knowledge/gaps", "", "A", planned="KB-AI-3 (§22)",
              op="list_gaps",
              title="List the questions that Knowledge could not answer, that the member "
                    "can see.",
              plain=_PLAIN),
        Route("GET", "/knowledge/maintenance", "", "A", planned="KB-AI-3 (§22)",
              op="list_maintenance",
              title="List the pages that are expired, unowned or stale. It needs "
                    "knowledge:manage.",
              plain=_PLAIN),
        Route("POST", "/knowledge/pages/{page_id}/verify", "", "X", reason=_VERIFY_REASON),
        Route("POST", "/knowledge/gaps/{gap_id}/dismiss", "", "X", reason=_GAP_DECISION_REASON),
        Route("POST", "/knowledge/gaps/{gap_id}/answered", "", "X",
              reason=_GAP_DECISION_REASON),
        Route("POST", "/knowledge/gaps/{gap_id}/reopen", "", "X", reason=_GAP_DECISION_REASON),
        # ── Sources (KC-1a, knowledge_curation.md §7) ── the tree is names only. A
        # read may join the chat with `search_knowledge` (knowledge_base.md §7.2).
        Route("POST", "/knowledge/sources/google_drive/connect", "", "X",
              reason=_SOURCE_CONSENT_REASON),
        Route("GET", "/knowledge/sources/google_drive/callback", "", "X",
              reason=_SOURCE_CONSENT_REASON),
        Route("GET", "/knowledge/sources", "", "A", planned="search_knowledge, deferred (§8.1)",
              op="list_sources", title="List the file sources that the member can see.",
              plain=_PLAIN),
        Route("GET", "/knowledge/sources/{source_id}/tree", "", "A",
              planned="search_knowledge, deferred (§8.1)",
              op="get_source_tree",
              title="Read one level of the folder tree of a file source. Names only.",
              plain=_PLAIN),
        Route("DELETE", "/knowledge/sources/{source_id}", "", "X",
              reason=_SOURCE_DISCONNECT_REASON),
        # ── Entities (KC-8a, knowledge_curation.md §6) ── the directory under the
        # owner rule of each type. On neither door yet.
        Route("GET", "/knowledge/entity-types", "", "X", reason=_ENTITY_REASON),
        Route("GET", "/knowledge/entities", "", "X", reason=_ENTITY_REASON),
        Route("GET", "/knowledge/entities/{entity_id}", "", "X", reason=_ENTITY_REASON),
        Route("POST", "/knowledge/entities", "", "X", reason=_ENTITY_REASON),
        Route("POST", "/knowledge/entities/by-ref", "", "X", reason=_ENTITY_REASON),
        Route("POST", "/knowledge/entities/{entity_id}/page", "", "X", reason=_ENTITY_REASON),
    ),
)

#: The rows, for the re-export ``acb_skills.knowledge_manifest.MANIFEST``.
MANIFEST: tuple[Route, ...] = KNOWLEDGE.routes

__all__ = ["KNOWLEDGE", "MANIFEST"]
