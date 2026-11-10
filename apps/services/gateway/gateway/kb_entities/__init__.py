"""The entity registry of Knowledge (WS-61 KC-8a).

Spec: ``project-docs/specs/knowledge_curation.md`` §6.1 to §6.4 and §16, and
migration ``kb_entities`` (found by content, R1).

``registry.py`` holds the page-type vocabulary, the one writer
(``ensure_entity``) and the owner rule of each type (``owner_rules``).
``resolver.py`` matches aliases with ``fold()`` and maps a person to
``people.id``. The routes are ``routes/knowledge/entities.py``.

The package name starts with ``kb_``, so the one-seam fence of the page
clause (``tests/unit/test_kb_visibility_one_seam.py``) scans it. Its own
fence is ``tests/unit/test_kc_entities.py``, and the R8 suite is
``tests/unit/test_kc_entities_r8.py``.
"""
