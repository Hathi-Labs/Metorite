"""Projects route package — the gateway `/projects` API (native project management).

Spec: ``project-docs/specs/project_management_app.md`` (WS-27a builds §3 + §4).

Same layout as ``routes/crm``, ``routes/tasks`` and ``routes/notes``: ``core`` is
the leaf and the feature modules register their routes on the shared ``router``
as an import side effect.

**Import order is not load-bearing**, for the same reason it is not in
``routes/crm``: every path in this package is a literal (``/projects/tasks/{id}``,
``/projects/nodes/{id}``), never a ``/projects/{kind}/{id}`` template, so no route
can shadow another and alphabetising this list can never change which handler
answers a request.

⚠️ That immunity is a property of the PATHS, not a licence — it holds only while
every new route keeps its literal segment ahead of any ``{id}``. WS-27ae's feed
is ``/projects/delta/tasks`` rather than the more natural
``/projects/tasks/delta`` for exactly this reason: the latter would be matched by
``/projects/tasks/{task_id}`` and the answer would depend on this list's order.
``test_projects_delta.py`` asserts the mounted path, so a later rename back to
the colliding shape fails rather than 404ing at runtime.

⚠️ A feature module left out of this list mounts **nothing**, while every test
that calls its route function directly still passes. That trap is documented in
``department_centers.md`` C1 and it is why ``tests/unit/test_projects_routes.py``
asserts the mounted path set rather than only calling the functions.

⚠️ **There is no ``sync.py``, no ``import_clickup.py``, no ``import_tasks.py`` and
no ``mapping.py``, and their absence is a DECISION rather than a backlog.**
**D52** (2026-08-24, board WS-39 S1) retired ClickUp outright: Metorite is the
project-management system of record, so there is no external system to import
from, sync with or map onto. WS-27c is cancelled, not deferred. Do not re-add an
importer here — the ``gtd_*`` → ``pm_*`` move that D53 still needs is a **backfill
migration**, not an HTTP endpoint, precisely so this package keeps exactly one
write path into ``pm_tasks``.
"""

from gateway.routes.projects import activities as _activities  # noqa: F401
from gateway.routes.projects import assignees as _assignees  # noqa: F401
from gateway.routes.projects import admin as _admin  # noqa: F401
from gateway.routes.projects import attachments as _attachments  # noqa: F401
from gateway.routes.projects import bulk as _bulk  # noqa: F401
from gateway.routes.projects import calendar as _calendar  # noqa: F401
from gateway.routes.projects import custom_fields as _custom_fields  # noqa: F401
from gateway.routes.projects import delta as _delta  # noqa: F401
from gateway.routes.projects import export as _export  # noqa: F401
from gateway.routes.projects import intake as _intake  # noqa: F401
from gateway.routes.projects import me as _me  # noqa: F401
from gateway.routes.projects import notifications as _notifications  # noqa: F401
from gateway.routes.projects import personal as _personal  # noqa: F401
from gateway.routes.projects import planning as _planning  # noqa: F401
from gateway.routes.projects import recurrence as _recurrence  # noqa: F401
from gateway.routes.projects import relations as _relations  # noqa: F401
from gateway.routes.projects import search as _search  # noqa: F401
from gateway.routes.projects import tags as _tags  # noqa: F401
from gateway.routes.projects import tasks as _tasks  # noqa: F401
from gateway.routes.projects import tree as _tree  # noqa: F401
from gateway.routes.projects import views as _views  # noqa: F401
from gateway.routes.projects import watchers as _watchers  # noqa: F401
from gateway.routes.projects.core import router

__all__ = ["router"]
