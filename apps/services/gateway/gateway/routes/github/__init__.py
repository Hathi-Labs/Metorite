"""The GitHub App routes (WS-54, ``project-docs/specs/github_integration.md``).

* ``connect.py`` — the connect flow of the App (§7.1 to §7.4) and the Advanced
  token (§7.6).
* ``webhook.py`` — ``POST /webhooks/github`` and its handlers (§8).

Each module owns its own router, and ``gateway/main.py`` includes each one.
"""
