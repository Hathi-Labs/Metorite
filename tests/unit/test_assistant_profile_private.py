"""The assistant profile is private to its member (WS-63 PZ-9).

Spec: ``personalization.md`` §7 point 5 and D-PZ-3. "No admin route reads
another member's profile. No export includes it."

No database. The fences read the source and the route table:

(a) Outside the member's own routes, no admin, operator, console, CRM or
    People source names the column in a string, so no SQL there reads it.
    The one exception is the removal of a member, which clears the column
    through ``acb_common.assistant_profile.CLEAR_FOR_MEMBER_SQL_TEMPLATE``.
    That file may import the clear and ``mark_changed``, and nothing else.
    No staff source imports the parser module or the member's own route
    modules. ``routes/admin/__init__.py`` attaches the routes, and only it
    may name them.
(b) ``/tasks/settings`` loads the row with ``SELECT *``, so its model must
    not carry the column, or the settings answer would hold the profile. No
    other source reads the whole row.
(e) Each function that sets ``app_user.status`` to ``removed`` calls
    ``members.forget_member_profile``. A suspension keeps the profile.
(c) No route of the profile takes an address, a user or a member in its
    path or its query, so no caller can name somebody else.
(d) ``render_export`` writes the member's words and nothing else.

Mutation: add ``assistant_profile: Any = None`` to ``UserSettingsModel``,
and (b) fails. Add ``{email}`` to the export path, and (c) fails. Put
``SELECT assistant_profile`` in ``members.py``, and (a) fails.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"

#: The trees that a member of staff or an admin reaches.
SCANNED = (
    GATEWAY / "routes" / "admin",
    GATEWAY / "routes" / "operator.py",
    GATEWAY / "routes" / "crm",
    GATEWAY / "routes" / "people",
    # The People app's directory and HR records live here (`/tasks/people`).
    GATEWAY / "routes" / "tasks" / "people.py",
    ROOT / "apps" / "services" / "customer_console",
)

#: The member's own routes. They read the caller's profile only, and (c)
#: holds them to that.
OWN_ROUTES = ("assistant_profile.py", "assistant_profile_people.py")

#: The one file that may import from the parser, and the names it may
#: import: the removal forgets the profile and moves the change count.
CLEAR_IMPORTER = GATEWAY / "routes" / "admin" / "members.py"
CLEAR_NAMES = {"CLEAR_FOR_MEMBER_SQL_TEMPLATE", "mark_changed"}

#: The package file that attaches the member's own routes to `me_router`.
#: It imports the two route modules for that side effect, and nothing else.
ROUTE_WIRING = GATEWAY / "routes" / "admin" / "__init__.py"

COLUMN = "assistant_profile"

#: Every source of the platform, for the fences that read the whole tree.
PLATFORM = (ROOT / "apps", ROOT / "packages")


def _sources() -> list[Path]:
    out: list[Path] = []
    for root in SCANNED:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        out += [f for f in files if f.name not in OWN_ROUTES]
    return out


def _platform_sources() -> list[Path]:
    out: list[Path] = []
    for root in PLATFORM:
        out += [f for f in sorted(root.rglob("*.py"))
                if not {"tests", ".venv", "__pycache__", "node_modules"} & set(f.parts)]
    return out


def _strings(path: Path) -> list[tuple[int, str]]:
    """Every string token of *path*, with its line. Comments are not code."""
    src = path.read_text(encoding="utf-8-sig")
    return [(t.start[0], t.string)
            for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type == tokenize.STRING
            or getattr(tokenize, "FSTRING_MIDDLE", -1) == t.type]


def test_the_scan_reaches_the_trees_it_names() -> None:
    files = _sources()
    assert CLEAR_IMPORTER in files
    assert ROUTE_WIRING in files
    assert GATEWAY / "routes" / "tasks" / "people.py" in files
    assert any(f.name == "operator.py" for f in files)
    assert any("customer_console" in f.parts for f in files)
    assert len(files) > 20, len(files)


def test_no_staff_source_names_the_column_in_a_string() -> None:
    """(a) A string that names the column is SQL, a key or a docstring that
    describes a read. None belongs outside the member's own routes."""
    found = [f"{f.relative_to(ROOT)}:{n}" for f in _sources()
             for n, s in _strings(f) if COLUMN in s]
    assert not found, f"the profile column is named outside its routes: {found}"


def _import_problems(path: Path, tree: ast.AST) -> list[str]:
    """Each import in *path* that reaches the parser or the member's routes."""
    where = str(path.relative_to(ROOT))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"{where}: import {a.name}" for a in node.names if COLUMN in a.name]
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        names = {a.name for a in node.names}
        if module == f"acb_common.{COLUMN}":
            if path != CLEAR_IMPORTER or not names <= CLEAR_NAMES:
                out.append(f"{where}: from {module} import {sorted(names)}")
        elif COLUMN in module:
            # `gateway.routes.admin.assistant_profile[_people]`: the member's
            # own routes, and their helpers read the caller's profile.
            out.append(f"{where}: from {module} import {sorted(names)}")
        elif any(COLUMN in n for n in names):
            # `from acb_common import assistant_profile`, or a route module
            # by name. Only the wiring file attaches the routes.
            wiring = path == ROUTE_WIRING and module == "gateway.routes.admin"
            if not wiring:
                out.append(f"{where}: from {module or '.'} import {sorted(names)}")
    return out


def test_only_the_removal_imports_from_the_parser_and_only_the_clear() -> None:
    """(a) The removal clears the column through the parser's one statement.
    Any other import of the parser, or of the member's own route modules,
    from a staff source is a read path."""
    problems: list[str] = []
    for path in _sources():
        problems += _import_problems(path, ast.parse(path.read_text(encoding="utf-8-sig")))
    assert not problems, problems


def test_the_import_rule_refuses_each_shape_it_names() -> None:
    """The rule above, against the shapes that a reviewer found it missed."""
    staff = GATEWAY / "routes" / "crm" / "x.py"
    for src in ("from acb_common import assistant_profile",
                "from acb_common import assistant_profile as ap",
                "from gateway.routes.admin.assistant_profile import _read",
                "from gateway.routes.admin.assistant_profile_people import fold",
                "from gateway.routes.admin import assistant_profile",
                "from . import assistant_profile",
                "import acb_common.assistant_profile",
                "from acb_common.assistant_profile import stored_profile"):
        assert _import_problems(staff, ast.parse(src)), src
    wiring = "from gateway.routes.admin import assistant_profile as _ap"
    assert not _import_problems(ROUTE_WIRING, ast.parse(wiring))
    clear = "from acb_common.assistant_profile import CLEAR_FOR_MEMBER_SQL_TEMPLATE, mark_changed"
    assert not _import_problems(CLEAR_IMPORTER, ast.parse(clear))
    read = "from acb_common.assistant_profile import stored_profile"
    assert _import_problems(CLEAR_IMPORTER, ast.parse(read))


#: An ``app_user`` status write that can set ``removed``.
_REMOVED_WRITE = re.compile(
    r"UPDATE\s+app_user\s+SET\s+status\s*=\s*(?::status|'removed')", re.IGNORECASE)


def _function_strings(fn: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _calls(fn: ast.AST, name: str) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                    isinstance(f, ast.Attribute) and f.attr == name):
                return True
    return False


def test_every_status_write_of_removed_forgets_the_profile() -> None:
    """§7 point 6: each door that sets a member to ``removed`` runs
    ``members.forget_member_profile`` in the same function, so in the same
    transaction. The PATCH once wrote ``removed`` and kept the profile.

    Mutation: drop the call from ``update_member``, and this fails."""
    problems: list[str] = []
    for path in _platform_sources():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        fns = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        inside = {id(c) for fn in fns for c in ast.walk(fn)}
        for fn in fns:
            if any(_REMOVED_WRITE.search(s) for s in _function_strings(fn)) and not _calls(
                    fn, "forget_member_profile"):
                problems.append(f"{path.relative_to(ROOT)}::{fn.name}")
        # A module-level SQL constant that writes `removed` hides its caller.
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in inside and _REMOVED_WRITE.search(node.value)):
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno} (module level)")
    assert not problems, problems
    # The rule reaches both known doors.
    from gateway.routes.admin import members

    src = Path(members.__file__).read_text(encoding="utf-8-sig")
    assert src.count("await forget_member_profile(db,") == 2


#: The one reader of the whole settings row. Its model is the filter, and
#: (b) holds the model.
SELECT_ALL_OK = {GATEWAY / "routes" / "tasks" / "settings.py"}
_SELECT_ALL = re.compile(r"SELECT\s+(?:\w+\.)?\*\s+FROM\s+user_settings\b", re.IGNORECASE)


def test_no_other_source_reads_the_whole_settings_row() -> None:
    """``SELECT *`` on ``user_settings`` carries the profile with every other
    column. Only ``/tasks/settings`` does it, behind its model."""
    found = [f"{f.relative_to(ROOT)}:{n}" for f in _platform_sources()
             if f not in SELECT_ALL_OK
             for n, s in _strings(f) if _SELECT_ALL.search(" ".join(s.split()))]
    assert not found, found
    ok = next(iter(SELECT_ALL_OK))
    assert any(_SELECT_ALL.search(s) for _n, s in _strings(ok)), "the allowed site moved"


def test_the_clear_writes_null_and_reads_nothing() -> None:
    from acb_common.assistant_profile import CLEAR_FOR_MEMBER_SQL_TEMPLATE as sql

    assert sql.startswith("UPDATE user_settings SET assistant_profile = NULL")
    assert "RETURNING" not in sql
    assert "organization_id = {org}" in sql
    assert "lower(user_id) = lower(:email)" in sql


def test_the_settings_model_does_not_carry_the_profile() -> None:
    """(b) ``_load`` runs ``SELECT *``. The model is the filter."""
    from gateway.routes.tasks.settings import UserSettingsModel

    assert COLUMN not in UserSettingsModel.model_fields


def _profile_routes() -> list:
    from gateway.routes.admin.me import me_router

    return [r for r in me_router.routes
            if getattr(r, "path", "").startswith("/auth/me/assistant-profile")]


def test_no_profile_route_takes_a_person() -> None:
    """(c) The member comes from the session. A path or a query parameter
    named for a person would let a caller ask about somebody else."""
    import gateway.routes.admin  # noqa: F401 — attaches the routes

    routes = _profile_routes()
    assert any(r.path == "/auth/me/assistant-profile/export" for r in routes)
    banned = {"email", "user", "member", "user_id", "member_email", "uid", "who"}
    for route in routes:
        params = [p.name for p in (*route.dependant.path_params,
                                   *route.dependant.query_params)]
        assert not banned & {p.lower() for p in params}, (route.path, params)


def test_the_text_export_holds_only_the_members_words() -> None:
    """(d) The switch, the version and the last chat change stay out, and
    each stored value is one line."""
    from acb_common.assistant_profile import EXPORT_EMPTY, render_export

    raw = {
        "v": 1, "learning": False,
        "address": {"name": "VJ", "formality": "casual", "greeting": "warm"},
        "style": {"length": "short", "language": "en-IN", "notes": ["Answer first"]},
        "facts": [{"id": "f1", "text": "I run on Mondays", "kind": "routine",
                   "source": "chat", "at": "2026-10-23T11:00:00Z"},
                  {"id": "f2", "text": "I work in Pune", "kind": "work"}],
        "aliases": [{"id": "a1", "alias": "Mum", "email": "mum@example.com"}],
        "last_change": {"op": "put", "key": "address", "prior": {"name": "SECRET"}},
        "stray": "SECRET",
    }
    text = render_export(raw)
    for want in ("Name: VJ", "Tone: casual", "Greeting: warm", "Answer length: short",
                 "Language: en-IN", "Note: Answer first",
                 "I run on Mondays (routine, learned in chat, 2026-10-23)",
                 "I work in Pune (work)", "Mum: mum@example.com"):
        assert want in text, want
    assert "SECRET" not in text
    assert "learning" not in text.lower()
    assert "f1" not in text and "a1" not in text
    assert render_export(None) == EXPORT_EMPTY
    assert render_export({"v": 1, "learning": False}) == EXPORT_EMPTY
    assert "\n" not in EXPORT_EMPTY
