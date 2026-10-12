"""The CRM mirror sync (WS-53 CRM-Z4 and CRM-Z5, ``crm_platform.md`` §5.4).

``engine`` runs one organization once. ``scheduler`` runs the loop and the
background start. ``runs`` holds the SQL of ``crm_sync_runs`` and the cursors.
This package is the ONLY caller of ``core.upsert_from_source`` and
``core.delete_from_source``, the two writes that bypass the write guard.
The routes (``routes/crm/sync.py``) import this package lazily.
"""
