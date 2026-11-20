"""TOF-2b — the failure fingerprint and its normaliser (fence P2).

Spec: ``project-docs/specs/tool_failure_log.md`` §3 and §9 P2. The golden
rows: ``project-docs/specs/tool_failure_log_examples.md``. Code:
``acb_common.failure_fingerprint`` (the pure rules),
``acb_common.failure_log.set_builtin_names`` (the set of built-in names) and
``acb_skills.builtin_tools`` (the one giver of that set).

What this file proves:

* Each of the 12 golden rows hashes to its fingerprint. The rows that a
  normaliser can make with no database come out of the real rules, from real
  exceptions and real frames under a temporary root.
* Tenant text never reaches a field: not the message, not the arguments, not
  a name, not a path, not a class or a frame of tenant code.
* The walk follows ``__cause__`` and the groups, and never ``__context__``
  (rows 11 and 12).
* ``failure_fingerprint.py`` and ``failure_log.py`` import no ``acb_skills``
  module, and a fresh interpreter that imports only ``acb_skills.crm_tools``
  has the set in ``failure_log``.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest
from acb_common import failure_fingerprint as ff

_REPO = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO / "project-docs" / "specs" / "tool_failure_log_examples.md"
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


# ── The golden rows of the appendix ──────────────────────────────────────────


def _golden_rows() -> list[dict[str, str]]:
    """The 12 rows of the appendix table, read from the doc itself."""
    rows: list[dict[str, str]] = []
    for line in _EXAMPLES.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not cells[0].isdigit():
            continue
        tool, code, exc, site, fp = (c.strip("`") for c in cells[2:7])
        rows.append({"n": cells[0], "tool": tool, "error_code": code,
                     "exc_type": exc, "code_site": site, "fingerprint": fp})
    return rows


GOLDEN = {r["n"]: r for r in _golden_rows()}


def test_the_appendix_holds_twelve_rows() -> None:
    assert sorted(GOLDEN, key=int) == [str(n) for n in range(1, 13)]


@pytest.mark.parametrize("n", [str(n) for n in range(1, 13)])
def test_each_golden_row_hashes_to_its_fingerprint(n: str) -> None:
    row = GOLDEN[n]
    got = ff.fingerprint(row["tool"], row["error_code"], row["exc_type"], row["code_site"])
    assert got == row["fingerprint"]
    line = "|".join(("v1", row["tool"], row["error_code"], row["exc_type"], row["code_site"]))
    assert got == hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
    assert ff.fields_problem(row["tool"], row["error_code"], row["exc_type"],
                             row["code_site"]) is None


def _expect(n: str, fields: ff.Fields) -> None:
    row = GOLDEN[n]
    assert (fields.tool, fields.error_code, fields.exc_type, fields.code_site) == (
        row["tool"], row["error_code"], row["exc_type"], row["code_site"])
    assert fields.fingerprint == row["fingerprint"]


# ── Fixture code under a temporary root ──────────────────────────────────────


def _load(root: Path, rel: str, name: str, source: str,
          extra: dict[str, object] | None = None) -> types.ModuleType:
    """Write *source* at ``root/rel`` and run it as the module *name*.

    The module never enters ``sys.modules``, so the real package of that name
    stays as it is. Its frames carry ``__name__`` = *name* and the file path
    under *root*, which is all that rule 4 reads."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    if extra:
        mod.__dict__.update(extra)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


_CLIENT = """
import httpx

def _post():
    request = httpx.Request("POST", "https://gateway.test/projects/tasks")
    response = httpx.Response(410, request=request)
    response.raise_for_status()
"""

_WRITES = """
import httpx

def create_task(mode):
    if mode == "body":
        return {}["Amount for Ravi Kumar"]
    if mode == "context":
        try:
            _post()
        except httpx.HTTPStatusError:
            raise KeyError("title of Ravi's task")
    if mode == "cause":
        request = httpx.Request("POST", "https://gateway.test/projects/tasks")
        response = httpx.Response(410, request=request)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise KeyError("title of Ravi's task") from exc
    return None
"""

_GUARD = """
def guard(fn, *args):
    try:
        return None, fn(*args)
    except Exception as exc:
        return exc, None
"""


@pytest.fixture
def tree(tmp_path: Path) -> types.SimpleNamespace:
    """The fixture of the appendix: ``skill_projects.writes`` and
    ``skill_projects.client`` under ``apps/skills``, and a guard under
    ``packages``, all below one temporary root."""
    skills = "apps/skills/skill-projects/skill_projects"
    client = _load(tmp_path, f"{skills}/client.py", "skill_projects.client", _CLIENT)
    writes = _load(tmp_path, f"{skills}/writes.py", "skill_projects.writes", _WRITES,
                   extra={"_post": client._post})
    guard = _load(tmp_path, "packages/acb_skills/acb_skills/pack_tool_guard.py",
                  "acb_skills.pack_tool_guard", _GUARD)
    return types.SimpleNamespace(root=tmp_path, writes=writes, guard=guard)


def _caught(tree: types.SimpleNamespace, mode: str) -> BaseException:
    exc, _ = tree.guard.guard(tree.writes.create_task, mode)
    assert exc is not None
    return exc


# ── The rows that the rules make from real failures ──────────────────────────


def test_rows_1_and_2_one_fingerprint_for_two_tenants(tmp_path: Path) -> None:
    crm = _load(tmp_path, "packages/acb_skills/acb_skills/crm_tools.py", "acb_skills.crm_tools",
                "def crm_get_record(text):\n    raise KeyError(text)\n")
    fields = []
    for text in ("Amount for Ravi Kumar", "Amount for Acme Pvt Ltd"):
        try:
            crm.crm_get_record(text)
        except KeyError as exc:
            fields.append(ff.normalise(exc, error_code="exception", tool="crm_get_record",
                                       builtin_names={"crm_get_record"}, repo_root=tmp_path))
    _expect("1", fields[0])
    _expect("2", fields[1])


def test_row_3_a_known_name_that_the_turn_did_not_load() -> None:
    _expect("3", ff.normalise(None, error_code="unknown_tool", tool="create_task",
                              builtin_names={"create_task"}))


def test_row_4_a_name_that_no_registry_holds() -> None:
    _expect("4", ff.normalise(None, error_code="unknown_tool", tool="send_invoice_to_ravi",
                              builtin_names={"create_task"}))


def test_row_5_a_refused_argument_shape_keeps_no_type_or_site(tree) -> None:
    exc = _caught(tree, "body")
    _expect("5", ff.normalise(exc, error_code="bad_arguments", tool="create_tasks",
                              builtin_names={"create_tasks"}, repo_root=tree.root))


def test_row_6_a_route_that_raised(tmp_path: Path) -> None:
    import asyncpg.exceptions

    route = _load(
        tmp_path, "apps/services/gateway/gateway/routes/crm/deal_contacts.py",
        "gateway.routes.crm.deal_contacts",
        "def add_deal_contact(err):\n    raise err('key (email)=(ravi@acme.in) is absent')\n")
    try:
        route.add_deal_contact(asyncpg.exceptions.ForeignKeyViolationError)
    except Exception as exc:
        fields = ff.normalise(
            exc, error_code="unhandled_500", tool="POST /crm/deals/{deal_id}/contacts",
            kind="route", route_paths={"/crm/deals/{deal_id}/contacts"}, repo_root=tmp_path)
    _expect("6", fields)


def test_row_7_a_query_string_never_reaches_the_template(tmp_path: Path) -> None:
    route = _load(tmp_path, "apps/services/gateway/gateway/routes/crm/aggregate.py",
                  "gateway.routes.crm.aggregate",
                  "def aggregate(owner):\n    return len(owner) / 0\n")
    try:
        route.aggregate("meera@x.in")
    except ZeroDivisionError as exc:
        _expect("7", ff.normalise(exc, error_code="unhandled_500", tool="GET /crm/aggregate",
                                  kind="route", route_paths={"/crm/aggregate"},
                                  repo_root=tmp_path))
        # A raw path with the query string is never the template.
        odd = ff.normalise(exc, error_code="unhandled_500",
                           tool="GET /crm/aggregate?owner=meera@x.in", kind="route",
                           route_paths={"/crm/aggregate"}, repo_root=tmp_path)
        assert odd.tool == "route:other"


@pytest.mark.parametrize("server", ["shaurrya-app/list_orders", "acme-erp/stock"])
def test_rows_8_and_9_an_outside_timeout_gives_one_fingerprint(server: str) -> None:
    fields = ff.normalise(TimeoutError(f"{server} did not answer"), error_code="mcp_timeout",
                          tool=server, kind="mcp")
    _expect("8", fields)
    _expect("9", fields)


def test_row_10_the_skill_frame_wins_over_the_guard_frame(tree) -> None:
    _expect("10", ff.normalise(_caught(tree, "body"), error_code="exception",
                               tool="create_task", builtin_names={"create_task"},
                               repo_root=tree.root))


def test_row_11_the_walk_never_follows_the_context(tree) -> None:
    exc = _caught(tree, "context")
    assert exc.__context__ is not None and exc.__cause__ is None
    _expect("11", ff.normalise(exc, error_code="exception", tool="create_task",
                               builtin_names={"create_task"}, repo_root=tree.root))


def test_row_12_the_walk_follows_the_cause(tree) -> None:
    exc = _caught(tree, "cause")
    assert exc.__cause__ is not None
    _expect("12", ff.normalise(exc, error_code="exception", tool="create_task",
                               builtin_names={"create_task"}, repo_root=tree.root))


def test_rows_11_and_12_fail_with_the_repository_root(tree) -> None:
    """With the repository root, the fixture frames are out of every tree."""
    fields = ff.normalise(_caught(tree, "cause"), error_code="exception", tool="create_task",
                          builtin_names={"create_task"})
    assert fields.code_site == "-"


# ── Adversarial inputs: no tenant text reaches a field (P2) ──────────────────

_MARKERS = (
    "ravi.kumar@acme.in", "c0ffee00-1111-4222-8333-444455556666", "https://acme.in/a?b=c",
    "/home/acme/secret.txt", "C:\\Users\\Ravi\\notes.txt", "9876543210", "\"Ravi Kumar\"",
    "'Acme Pvt Ltd'", "Amount " * 400, "ரவி குமார்", "Ünïcödé ✓", "\u202e\u200bhidden",
)


@pytest.mark.parametrize("marker", _MARKERS)
def test_the_message_and_the_arguments_never_enter_a_field(tree, marker: str) -> None:
    plain = ff.normalise(_caught(tree, "body"), error_code="exception", tool="create_task",
                         builtin_names={"create_task"}, repo_root=tree.root)
    try:
        tree.writes.create_task("body")
    except KeyError as exc:
        exc.args = (marker, {"to": marker}, 42)
        exc.add_note(marker)
        got = ff.normalise(exc, error_code="exception", tool="create_task",
                           builtin_names={"create_task"}, repo_root=tree.root)
    assert got == plain
    for value in (got.tool, got.error_code, got.exc_type, got.code_site):
        assert marker.strip() not in value


@pytest.mark.parametrize("name", [
    "ravi.kumar@acme.in", "crm_get_record ", "CRM_GET_RECORD", "créer_tâche", "x" * 81,
    "../../etc/passwd", "https://acme.in/tool", "Ravi Kumar", "", "1tool", "tool-name",
    "route:GET /x", "other\nline",
])
def test_a_chat_name_off_the_grammar_is_other_even_in_the_set(name: str) -> None:
    fields = ff.normalise(None, error_code="unknown_tool", tool=name, builtin_names={name})
    assert fields.tool == "other"


@pytest.mark.parametrize("kind", ["studio", "mcp", "user_tool"])
def test_a_tenant_named_tool_is_a_placeholder(kind: str) -> None:
    fields = ff.normalise(None, error_code="mcp_timeout", tool="Ravi's ERP/stock", kind=kind)
    assert fields.tool == kind


def test_an_unknown_kind_is_other() -> None:
    assert ff.normalise(None, error_code="unknown_tool", tool="create_task", kind="plugin",
                        builtin_names={"create_task"}).tool == "other"


def test_no_set_of_builtin_names_gives_other() -> None:
    assert ff.normalise(None, error_code="unknown_tool", tool="create_task").tool == "other"


@pytest.mark.parametrize("raw", [
    "POST /crm/deals/123/contacts", "POST /crm/deals/ravi@acme.in/contacts",
    "GET /crm/aggregate?owner=meera@x.in", "get /crm/aggregate", "TRACE /crm/aggregate",
    "GET  /crm/aggregate", "/crm/aggregate", "GET", "",
])
def test_a_concrete_or_strange_route_is_route_other(raw: str) -> None:
    fields = ff.normalise(None, error_code="route_500", tool=raw, kind="route",
                          route_paths={"/crm/aggregate", "/crm/deals/{deal_id}/contacts"})
    assert fields.tool == "route:other"


def test_a_route_template_off_the_grammar_is_route_other() -> None:
    odd = "/crm/ravi kumar"
    assert ff.normalise(None, error_code="route_500", tool=f"GET {odd}", kind="route",
                        route_paths={odd}).tool == "route:other"


def _raise_in(root: Path, module: str, cls: type[BaseException]) -> BaseException:
    mod = _load(root, "packages/acb_common/acb_common/fixture_raise.py", module,
                "def boom(cls):\n    raise cls('Ravi Kumar')\n")
    try:
        mod.boom(cls)
    except BaseException as exc:
        return exc
    raise AssertionError("no raise")


def test_a_class_off_the_allow_list_is_other(tmp_path: Path) -> None:
    tenant = types.ModuleType("acme_private_code")
    exec("class AcmeLedgerError(Exception):\n    pass\n", tenant.__dict__)
    sys.modules["acme_private_code"] = tenant
    try:
        exc = _raise_in(tmp_path, "acb_common.fixture_raise", tenant.AcmeLedgerError)
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).exc_type == "other"
    finally:
        sys.modules.pop("acme_private_code", None)


@pytest.mark.parametrize(("module", "qualname"), [
    ("builtins", "KeyError"), ("httpx", "HTTPStatusError"), ("acb_common.db", "TenantUnbound"),
    ("builtins", "NoSuchError"), ("gateway", "Ravi"),
])
def test_a_class_whose_module_lies_is_other(tmp_path: Path, module: str, qualname: str) -> None:
    liar = type(qualname, (Exception,), {"__module__": module})
    liar.__qualname__ = qualname
    exc = _raise_in(tmp_path, "acb_common.fixture_raise", liar)
    assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                        ).exc_type == "other"


def test_a_local_class_is_other(tmp_path: Path) -> None:
    class Local(Exception):
        pass

    assert "<locals>" in Local.__qualname__
    exc = _raise_in(tmp_path, "acb_common.fixture_raise", Local)
    assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                        ).exc_type == "other"


def test_a_real_class_of_our_code_is_kept(tmp_path: Path) -> None:
    from acb_common.db import TenantUnbound

    exc = _raise_in(tmp_path, "acb_common.fixture_raise", TenantUnbound)
    fields = ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path)
    assert fields.exc_type == "acb_common.db.TenantUnbound"
    assert fields.code_site == "acb_common.fixture_raise:boom"


def test_a_string_frame_never_counts(tmp_path: Path) -> None:
    glb: dict[str, object] = {"__name__": "gateway.routes.crm.aggregate"}
    exec(compile("def aggregate():\n    raise KeyError('Ravi')\n", "<string>", "exec"), glb)
    try:
        glb["aggregate"]()  # type: ignore[operator]
    except KeyError as exc:
        fields = ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path)
    assert fields.code_site == "-"
    assert fields.exc_type == "builtins.KeyError"


def test_a_site_packages_file_inside_a_tree_never_counts(tmp_path: Path) -> None:
    mod = _load(tmp_path, "packages/acb_common/.venv/lib/site-packages/lib.py", "lib",
                "def go():\n    raise KeyError('x')\n")
    try:
        mod.go()
    except KeyError as exc:
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).code_site == "-"


def test_a_file_outside_the_four_trees_never_counts(tmp_path: Path) -> None:
    mod = _load(tmp_path, "scripts/tool.py", "scripts.tool", "def go():\n    raise KeyError()\n")
    try:
        mod.go()
    except KeyError as exc:
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).code_site == "-"


def test_a_lambda_and_module_code_give_fixed_names(tmp_path: Path) -> None:
    mod = _load(tmp_path, "packages/acb_common/acb_common/fixture_lambda.py",
                "acb_common.fixture_lambda", "boom = lambda: {}['Ravi']\n")
    try:
        mod.boom()
    except KeyError as exc:
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).code_site == "acb_common.fixture_lambda:lambda"
    path = tmp_path / "packages/acb_common/acb_common/fixture_top.py"
    path.write_text("raise KeyError('Ravi')\n", encoding="utf-8")
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"),
             {"__name__": "acb_common.fixture_top"})
    except KeyError as exc:
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).code_site == "acb_common.fixture_top:module"


def test_a_frame_with_a_strange_module_name_is_skipped(tmp_path: Path) -> None:
    mod = _load(tmp_path, "packages/acb_common/acb_common/odd.py", "Ravi Kumar's module",
                "def go():\n    raise KeyError()\n")
    try:
        mod.go()
    except KeyError as exc:
        assert ff.normalise(exc, error_code="exception", tool="x", repo_root=tmp_path
                            ).code_site == "-"


@pytest.mark.parametrize("code", sorted(ff.DASH_CODES))
def test_a_dash_code_keeps_no_type_and_no_site(tree, code: str) -> None:
    fields = ff.normalise(_caught(tree, "body"), error_code=code, tool="create_task",
                          builtin_names={"create_task"}, repo_root=tree.root)
    assert (fields.exc_type, fields.code_site) == ("-", "-")


def test_no_exception_keeps_a_dash_for_a_kept_code() -> None:
    fields = ff.normalise(None, error_code="exception", tool="x")
    assert (fields.exc_type, fields.code_site) == ("-", "-")


def test_an_unknown_code_is_refused() -> None:
    with pytest.raises(ValueError):
        ff.normalise(None, error_code="Ravi failed", tool="x")


def test_the_closed_lists_hold_the_spec() -> None:
    assert set(ff.ERROR_CODES) == {
        "exception", "bad_arguments", "unknown_tool", "auth_401", "unhandled_500",
        "route_500", "route_upstream", "gateway_unreachable", "outside_unreachable",
        "mcp_exception", "mcp_timeout", "mcp_unreachable", "mcp_refused", "mcp_too_large",
        "mcp_tool_error"}
    assert {"exception", "unhandled_500", "mcp_exception"} == ff.KEPT_CODES
    assert {c for c, owned in ff.ERROR_CODES.items() if owned} == {
        "exception", "bad_arguments", "unknown_tool", "auth_401", "unhandled_500",
        "route_500", "mcp_exception"}
    assert {"chat_tool", "mcp_call", "door_5xx", "issue_report", "sandbox"} == ff.SOURCES
    assert {"chat", "whatsapp", "projects_assistant", "automation", "mcp_edge", "studio",
            "app", "other"} == ff.SURFACES


def test_a_cycle_of_causes_ends() -> None:
    a, b = KeyError("a"), ValueError("b")
    a.__cause__, b.__cause__ = b, a
    fields = ff.normalise(a, error_code="exception", tool="x")
    assert fields.exc_type in {"builtins.KeyError", "builtins.ValueError"}


def test_a_group_gives_its_first_member() -> None:
    group = ExceptionGroup("Ravi", [KeyError("Ravi"), ValueError("x")])
    assert ff.normalise(group, error_code="exception", tool="x").exc_type == "builtins.KeyError"


def test_the_fingerprint_is_sixteen_hex_and_stable() -> None:
    first = ff.fingerprint("create_task", "exception", "builtins.KeyError", "a.b:c")
    assert _HEX16.match(first)
    assert first == ff.fingerprint("create_task", "exception", "builtins.KeyError", "a.b:c")


@pytest.mark.parametrize("fields", [
    ("Ravi Kumar", "exception", "-", "-"), ("x", "boom", "-", "-"),
    ("x", "exception", "Ravi Kumar", "-"), ("x", "exception", "-", "a.b"),
    ("route:GET /x?y=1", "route_500", "-", "-"), ("x", "mcp_timeout", "builtins.KeyError", "-"),
])
def test_the_grammar_check_names_a_bad_field(fields: tuple[str, str, str, str]) -> None:
    assert ff.fields_problem(*fields) is not None


def test_the_normaliser_output_always_passes_its_own_grammar(tree) -> None:
    for mode in ("body", "context", "cause"):
        f = ff.normalise(_caught(tree, mode), error_code="exception", tool="create_task",
                         builtin_names={"create_task"}, repo_root=tree.root)
        assert ff.fields_problem(f.tool, f.error_code, f.exc_type, f.code_site) is None


# ── The import fences ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["failure_fingerprint.py", "failure_log.py"])
def test_the_store_modules_import_no_acb_skills_module(name: str) -> None:
    path = _REPO / "packages" / "acb_common" / "acb_common" / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not any(n == "acb_skills" or n.startswith("acb_skills.") for n in names), names


def test_a_fresh_interpreter_has_the_set_after_the_crm_tools_import() -> None:
    code = (
        "import acb_skills.crm_tools\n"
        "from acb_common import failure_log\n"
        "names = failure_log.builtin_names()\n"
        "assert 'crm_get_record' in names, sorted(names)[:5]\n"
        "print(len(names))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=180, cwd=_REPO, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-2000:]
    assert int(done.stdout.strip().splitlines()[-1]) > 100


def test_the_builtin_set_holds_only_names_of_the_grammar() -> None:
    from acb_skills import builtin_tools, skill_families

    names = builtin_tools.builtin_tool_names()
    assert names
    assert all(ff.TOOL_RE.match(n) for n in names)
    assert names <= skill_families.all_registered_tool_names()
    assert "crm_get_record" in names and "create_task" in names


def test_the_package_import_gives_the_set_to_the_store() -> None:
    import acb_skills  # noqa: F401
    from acb_common import failure_log
    from acb_skills import builtin_tools

    assert failure_log.builtin_names() == builtin_tools.builtin_tool_names()
