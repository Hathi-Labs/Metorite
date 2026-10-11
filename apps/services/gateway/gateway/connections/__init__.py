"""The registry of external connections (WS-53 CRM-Z2, spec section 5.3).

``store`` is the ONLY module that reads or writes the credential blob of the
registry table. ``shared_token`` gives each worker one fresh access token
through that store.
"""
