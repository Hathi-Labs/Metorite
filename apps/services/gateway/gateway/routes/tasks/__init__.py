"""Tasks route package — the gateway `/tasks` API (My Tasks: AI, intake, planner, people).

Import order matters only in that ``core`` is the leaf; the feature modules
register their routes on the shared ``router`` as an import side effect.
"""

from gateway.routes.tasks import ai as _ai  # noqa: F401
from gateway.routes.tasks import attachments as _attachments  # noqa: F401
from gateway.routes.tasks import calendar as _calendar  # noqa: F401
from gateway.routes.tasks import capability as _capability  # noqa: F401
from gateway.routes.tasks import capture_email as _capture_email  # noqa: F401
from gateway.routes.tasks import people as _people  # noqa: F401
from gateway.routes.tasks import planning as _planning  # noqa: F401
from gateway.routes.tasks import settings as _settings  # noqa: F401
from gateway.routes.tasks.core import router

__all__ = ["router"]
