"""The registry of external connections (WS-53 CRM-Z2, ``crm_platform.md`` §5.3).

``store`` is the ONLY module that reads or writes ``credentials_encrypted``.
``shared_token`` gives each worker one fresh access token through that store.
"""
