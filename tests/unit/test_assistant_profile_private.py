"""The assistant profile is private to its member (WS-63 PZ-9).

Spec: ``personalization.md`` §7 point 5 and D-PZ-3. "No admin route reads
another member's profile. No export includes it."

No database. The fences read the source and the route table:

(a) Outside the member's own routes, no admin, operator, console, CRM or
    People source names the column in a string, so no SQL there reads it.
    The one exception is the removal of a member, which clears the column
    through ``acb_common.assistant_profile.CLEAR_FOR_MEMBER_SQL_TEMPLATE``.
    That file may import that one name, and nothing else of the module.
(b) ``/tasks/settings`` loads the row with ``SELECT *``, so its model must
    not carry the column, or the settings answer would hold the profile.
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
    ROOT / "apps" / "services" / "customer_console",
)

#: The member's own routes. They read the caller's profile only, and (c)
#: holds them to that.
OWN_ROUTES = ("assistant_profile.py", "assistant_profile_people.py")

#: The one file that may import from the parser, and the one name it may
#: import: the removal of a member forgets the profile (§7 point 6).
CLEAR_IMPORTER = GATEWAY / "routes" / "admin" / "members.py"
CLEAR_NAME = "CLEAR_FOR_MEMBER_SQL_TEMPLATE"

COLUMN = "assistant_profile"


def _sources() -> list[Path]:
    out: list[Path] = []
    for root in SCANNED:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        out += [f for f in files if f.name not in OWN_ROUTES]
    return out


def _strings(path: Path) -> list[tuple[int, str]]:
    """Every string token of *path*, with its line. Comments are not code."""
    src = path.read_text(encoding="utf-8")
    return [(t.start[0], t.string)
            for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type == tokenize.STRING
            or getattr(tokenize, "FSTRING_MIDDLE", -1) == t.type]


def test_the_scan_reaches_the_trees_it_names() -> None:
    files = _sources()
    assert CLEAR_IMPORTER in files
    assert any(f.name == "operator.py" for f in files)
    assert any("customer_console" in f.parts for f in files)
    assert len(files) > 20, len(files)


def test_no_staff_source_names_the_column_in_a_string() -> None:
    """(a) A string that names the column is SQL, a key or a docstring that
    describes a read. None belongs outside the member's own routes."""
    found = [f"{f.relative_to(ROOT)}:{n}" for f in _sources()
             for n, s in _strings(f) if COLUMN in s]
    assert not found, f"the profile column is named outside its routes: {found}"


def test_only_the_removal_imports_from_the_parser_and_only_the_clear() -> None:
    """(a) The removal clears the column through the parser's one statement.
    Any other import of the parser from a staff source is a read path."""
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == f"acb_common.{COLUMN}":
                names = [a.name for a in node.names]
                assert path == CLEAR_IMPORTER and names == [CLEAR_NAME], (
                    f"{path.relative_to(ROOT)} imports {names} from the parser")
            if isinstance(node, ast.Import):
                assert not any(COLUMN in a.name for a in node.names), path


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
