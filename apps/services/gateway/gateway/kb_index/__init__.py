"""The Knowledge index — one table, hybrid retrieval (WS-61 KB-3).

Spec: ``project-docs/specs/knowledge_base.md`` §6.1 to §6.4.

The home is the gateway, not a package: the reads need
``routes.knowledge.core.visible_pages_clause`` and the request's tenant
session, and a package would import both upward (§6.3, KB-3 audit).

* ``chunk`` — pure functions. A page becomes chunks: one per heading section,
  a long section split on paragraphs, a table on its own. Each chunk has a
  ``content_sha``.
* ``embed`` — ``embed(texts)``, the ONE function that makes a vector. It
  asks the Console Router's ``/v1/embeddings`` on ``tier-embed`` (CP-EMB,
  ``customer_console.md`` §6A.16). A zero vector, a refusal, an error or a
  timeout is ``None``, and it never raises.
* ``index`` — ``reindex_page`` runs in the page write's transaction and
  leaves new rows with a NULL vector. ``embed_pending`` runs after the
  commit, as a background task, in three steps: select, compute with no
  session, write.
* ``query`` — the lexical half, the semantic half, and the fusion. Each read
  of ``kb_chunks`` joins ``kb_pages`` under the one visibility clause.

This ``__init__`` imports nothing, so that importing one module does not
load the routes package through another. Fences:
``tests/unit/test_kb_one_index.py``,
``tests/unit/test_kb_visibility_one_seam.py``, ``tests/unit/test_kb_chunk.py``
and, against a real database, ``tests/unit/test_kb_search_r8.py``.
"""
