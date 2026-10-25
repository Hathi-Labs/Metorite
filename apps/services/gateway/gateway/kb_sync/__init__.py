"""The Knowledge sync of company file sources (WS-61).

Spec: ``project-docs/specs/knowledge_base.md`` §7.1 and
``project-docs/specs/knowledge_curation.md`` §7.2.

* ``discovery.py`` (KC-1a): the first read of a source's folder tree, run as a
  background job after the connect. Since KC-3 it takes the run lock and
  stores the change cursor of "now".
* ``runs.py`` (KC-3): the SQL of ``kb_sync_runs`` and ``kb_sync_cursors``,
  and the run lock of each source.
* ``engine.py`` (KC-3): one run of one source, the change feed or the daily
  relist. Metadata only. KC-5 reads the text.
* ``scheduler.py`` (KC-3): the loop and its start and stop, behind
  ``KNOWLEDGE_SYNC``.
"""
