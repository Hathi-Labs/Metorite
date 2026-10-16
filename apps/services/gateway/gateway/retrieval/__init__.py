"""Company retrieval — one question, every app (WS-61 KR-1, D105).

Spec: ``project-docs/specs/knowledge_base.md`` §20.

* ``sources`` — the source record, the hit, and ``REGISTRY``: one source for
  each app, each over the app's OWN search, behind the app's own feature.
  Seven sources: tasks, email, people, crm, artifacts, whatsapp and, from
  KR-3, knowledge.
* ``company`` — ``GET /search/company``, the route that asks every source.

The command bar's ``PROVIDERS`` (``routes/shell/search.py``) is a projection
of ``REGISTRY``. No module here writes SQL or opens a session.
"""
