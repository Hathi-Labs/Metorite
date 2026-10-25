"""The Knowledge sync of company file sources (WS-61).

Spec: ``project-docs/specs/knowledge_base.md`` §7.1 and
``project-docs/specs/knowledge_curation.md`` §7.2.

KC-1a builds ``discovery.py`` only: the first read of a source's folder tree,
run once as a background job after the connect. KC-3 adds the engine, the
cursors, the run lock and the scheduler beside it.
"""
