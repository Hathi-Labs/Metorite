"""The platform Control Plane — organizations, seats, subscriptions, AI credits.

WS-31 · owning spec ``project-docs/specs/platform_control_plane.md``.

This is ``saas_multitenancy.md`` §0.9.2's **Control plane**, extracted from each
Metorite deployment into one central service. It is deliberately the only
place in this monorepo that reads across tenants: it answers "which companies
exist, what did they buy, and what have they burned" — questions the tenant
plane's row-level security exists to make unanswerable.

⚠️ It holds **no tenant business data**. No mail, no CRM rows, no documents. If a
column here would hold something a customer typed, it belongs in the tenant
database instead.
"""
