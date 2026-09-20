"""People Center · the directory as its own app.

Spec: ``project-docs/specs/people_center_app.md`` §3, §6 · ticket WS-28b.

    GET   /people                 → the directory, searchable and filterable
    GET   /people/me              → the caller's own row (UNGATED — see selfservice)
    PATCH /people/me              → your own record (UNGATED)
    POST  /people/me/resume       → your own CV (UNGATED)
    GET   /people/{id}            → one person, with the login badge
    GET   /people/{id}/work       → their open tasks, scoped by the VIEWER
    GET   /people/{id}/editable   → what THIS caller may write on that row
    GET   /people/{id}/absences   → when they are away (HR tier)
    GET   /people/schedule        → the company's working week
    PUT   /people/schedule        → edit it (admin:members:manage)
    GET   /people/dashboard       → the people-management dashboard rows
    GET   /people/quality         → skills coverage & data quality (§5.10)
    GET   /people/overview        → the Center landing rollup (§5.9)
    GET   /people/chart           → the org chart's flat node list (§5.4)
    PATCH /people/{id}            → a class-checked write (admin OR the subject)
    POST  /people/{id}/resume     → the CV, same rule

**Why this exists beside ``/tasks/people``.** The read API already shipped
(WS-24 N4) with the HR projection this app needs — but it is gated on
``feature:tasks``, and the People Center is a different audience: a manager who
needs the org chart and the assignee picker should not have to be handed the
personal GTD task manager to get them. So the *gate* is new and the
*projection* is imported, never re-implemented (§6: "a restriction that already
exists and must not be re-implemented here").
"""

# ruff: noqa: I001 — the import ORDER below is load-bearing and alphabetising
# it (which is exactly what `ruff --fix` would do) breaks `/people/me`. The
# fence is `test_people_profile.test_me_is_registered_before_the_person_id_pattern`,
# which fails on the swapped order rather than leaving this comment to be
# believed.

# Imported for the side effect that matters: each module's decorators are what
# attach its routes to `router`. Nothing here reads a name from either.
#
# ⚠️ **ORDER IS LOAD-BEARING, in two places.**
#
# FastAPI matches routes in REGISTRATION order, and `directory.py` registers
# `/people/{person_id}` — which happily matches the literal path `/people/me`.
# So:
#
#   1. WITHIN this package, `profile.py`, `schedule.py` and `dashboard.py` are
#      imported before `directory.py` — `/people/schedule` and
#      `/people/dashboard` are literal paths and would otherwise be swallowed
#      by `/people/{person_id}` too
#      (none imports another; their shared read seam lives in `core.py`,
#      which is what makes this order hold at runtime rather than being undone
#      by a transitive import).
#   2. ACROSS routers, `main.py` includes `self_router` BEFORE `router`. The
#      `/people/me` routes live on the ungated router (D-PC-15), so if the
#      gated one were included first, a member without `feature:people` would
#      be refused at their own profile by the directory's gate — which is the
#      exact defect WS-28g-2 exists to fix, reintroduced by an include order.
#
# `test_people_profile.py` asserts the second rather than trusting this
# comment, because it fails as a 403 that looks like a permissions problem; and
# `test_people_dashboard.py` asserts the first by building the router in a
# FRESH process and checking that no literal `/people/<word>` route shares a
# method with an earlier `/people/{person_id}` one — the general rule, so the
# next literal path added here inherits the fence.
from gateway.routes.people import selfservice as _selfservice  # noqa: F401
from gateway.routes.people import profile as _profile  # noqa: F401
from gateway.routes.people import schedule as _schedule  # noqa: F401
from gateway.routes.people import absences as _absences  # noqa: F401
from gateway.routes.people import dashboard as _dashboard  # noqa: F401
from gateway.routes.people import search as _search  # noqa: F401
from gateway.routes.people import skills as _skills  # noqa: F401
from gateway.routes.people import suggestions as _suggestions  # noqa: F401
from gateway.routes.people import quality as _quality  # noqa: F401
from gateway.routes.people import overview as _overview  # noqa: F401
from gateway.routes.people import chart as _chart  # noqa: F401
from gateway.routes.people import directory as _directory  # noqa: F401
from gateway.routes.people.core import router
from gateway.routes.people.selfservice import router as self_router

__all__ = ["router", "self_router"]
