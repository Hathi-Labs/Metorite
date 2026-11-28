"""Company retrieval — one question, every app (WS-61 KR-1, D105).

Spec: ``project-docs/specs/knowledge_base.md`` §20.

* ``sources`` — the source record, the hit, and ``REGISTRY``: one source for
  each app, each over the app's OWN search, behind the app's own feature.
  Nine sources: tasks, email, people, crm, artifacts, whatsapp, knowledge
  (KR-3), studio, and chat (HRD-21, last). ``test_company_search_sources.py``
  pins the registry.
* ``company`` — ``GET /search/company``, the route that asks every source.
* ``names`` — ``GET /search/names``, the route of ``resolve_name`` (FIND-5):
  the records that hold one name, from each source with ``named``.

The command bar's ``PROVIDERS`` (``routes/shell/search.py``) is a projection
of ``REGISTRY``. No module here writes SQL or opens a session.
"""
