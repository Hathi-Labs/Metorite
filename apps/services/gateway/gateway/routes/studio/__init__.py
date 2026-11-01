"""AI Studio routes (``project-docs/specs/ai_studio.md``).

STU-B1 ships ``GET /studio/actions`` (``actions.py``). It is read-only, and
it never runs an action. STU-F2a adds the skill routes (``skills.py``) on the
same router, as an import side effect. STU-B2a adds the action door
(``invoke.py``) on the same router in the same way. Each route is gated on
``STUDIO_ORGS`` (default OFF), and on the ``workflows`` feature until a
``studio`` feature exists (J1).
"""

from gateway.routes.studio import invoke as _invoke  # noqa: F401
from gateway.routes.studio import skills as _skills  # noqa: F401
from gateway.routes.studio.actions import router

__all__ = ["router"]
