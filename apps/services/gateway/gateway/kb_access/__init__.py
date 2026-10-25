"""The readers of a connected document (WS-61 KC-4a).

Spec: ``project-docs/specs/knowledge_curation.md`` §8 and §16.

``resolve.py`` turns the provider readers of a node (``kb_node_access``) into
proven members, in the one subject grammar. ``filing.py`` holds the filing
rule: a document fits a space only when every reader of the space is a
proven reader of the document. The SQL twin of the resolver is
``routes/knowledge/core.document_reader_clause``, and the read of a document
goes through ``core.visible_documents_clause``.

The access pass that writes ``kb_node_access`` is ``kb_sync/access.py``.

The package name starts with ``kb_``, so the one-seam fence of the
visibility clauses (``tests/unit/test_kb_visibility_one_seam.py``) scans it.
The fences are ``tests/unit/test_kc_document_visibility.py`` and, R8,
``tests/unit/test_kc_access_r8.py``.
"""
