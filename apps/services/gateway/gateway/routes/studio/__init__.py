"""AI Studio routes (``project-docs/specs/ai_studio.md``).

STU-B1 ships one route, ``GET /studio/actions``. It is read-only. It never
runs an action. Gated on ``STUDIO_ORGS`` (default OFF), and on the
``workflows`` feature until a ``studio`` feature exists.
"""

from gateway.routes.studio.actions import router

__all__ = ["router"]
