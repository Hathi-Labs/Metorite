"""WS-61 KC-8a — the entity registry reads each type through its owner rule.

Spec: ``project-docs/specs/knowledge_curation.md`` §6.2, §6.4 and §16.

``kb_entities`` holds one pointer for each thing the brain knows about. Most
entities have no page, so the page clause cannot gate them. Each type follows
the rule of the app that owns the record (§6.4), and these fences hold the
shape of that rule without a database. ``tests/unit/test_kc_entities_r8.py``
proves the behaviour against a real one.

What the fences hold.

1. **One entity for each record.** The migration carries the partial unique
   index ``kb_entities_org_ref_key``. ``registry.ensure_entity`` is the only
   ``INSERT INTO kb_entities`` in the gateway tree, and it conflicts on that
   index.
2. **Each read goes through the owner rule.** Each string literal under
   ``gateway/kb_entities/`` and ``gateway/routes/knowledge/`` that reads
   ``kb_entities`` (``FROM`` or ``JOIN``) sits in one of the per-type rule
   builders, and names the owner table of its type there. A bare read
   anywhere else fails, unless ``_INTEGRITY_READS`` lists it with a reason.
   A statement that puts in ``<rule>.source`` must put in ``<rule>.where``
   too, so no read takes the join without the predicate.
3. **The vocabulary is one list.** ``registry.ENTITY_TYPES`` equals the CHECK
   of the migration, and the type-to-table pairs equal the CHECK pairs.
4. **The rule calls the owning app, never a copy of its SQL.** The customer
   rule calls ``require_crm_enabled`` and ``require_crm_access``. The
   workstream rule calls ``resolve_visibility`` and ``project_clause``. The
   Knowledge-owned rule calls ``readable_spaces_clause``. No module of the
   package names a table of another app's grant model.
5. **The person resolver reuses PZ-5.** It imports ``fold``,
   ``email_in_org`` and ``_directory_tier`` from
   ``routes/admin/assistant_profile_people.py``, and never names
   ``acb_graph``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_PACKAGE = _GATEWAY / "kb_entities"
_REGISTRY = _PACKAGE / "registry.py"
_RESOLVER = _PACKAGE / "resolver.py"
_ROUTE = _GATEWAY / "routes" / "knowledge" / "entities.py"
_MIGRATIONS = _ROOT / "infra" / "postgres"

#: A statement that reads the registry.
_READS_ENTITIES = re.compile(r"\b(?:FROM|JOIN)\s+kb_entities\b", re.IGNORECASE)

#: A statement that writes a new row of the registry.
_INSERTS_ENTITIES = re.compile(r"\bINSERT\s+INTO\s+kb_entities\b", re.IGNORECASE)

#: The function that hands out the rules, and the attributes a statement must
#: put in together.
_RULE_FACTORY = "owner_rules"

#: ``builder name -> the owner table its read must name``. The Knowledge-owned
#: rule reads ``kb_entities`` itself, under the space clause, so its owner
#: table is ``kb_spaces``.
_RULE_BUILDERS: dict[str, str] = {
    "_company_rule": "organization",
    "_person_rule": "people",
    "_customer_rule": "crm_companies",
    "_workstream_rule": "pm_projects",
    "_workflow_rule": "workflows",
    "_knowledge_rule": "kb_spaces",
}

#: ``(module file name, function name) -> reason``. A read here returns no
#: name and no row of an owner app to a caller. Add one only with a reason a
#: reviewer can test.
_INTEGRITY_READS: dict[tuple[str, str], str] = {
    ("registry.py", "ensure_entity"): (
        "After the INSERT ... ON CONFLICT DO NOTHING, the get-or-create reads "
        "back the id of the row that won. It returns one id, never a name, "
        "and the caller reads the row through the owner rule."
    ),
    ("registry.py", "entity_type"): (
        "A GET by id reads the type of the entity, to pick the owner rule "
        "of that type. It returns the type only. The route answers 404 "
        "unless the rule then returns the row."
    ),
    ("registry.py", "lock_page_slot"): (
        "The lazy page route locks the entity row and reads its raw page_id, "
        "so two first writes make one page and a hidden page is never "
        "replaced. The caller already read the entity through the rule."
    ),
}

#: Tables of another app's access model. A module of the registry that names
#: one copied SQL that the owning app holds.
_FOREIGN_RULE_TABLES = (
    "crm_settings", "pm_project_grants", "org_group_member", "kb_space_grants",
    "user_permission_override",
)


# ── Parse helpers ───────────────────────────────────────────────────────────

def _text_of(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else "{}"
            for v in node.values
        )
    return ""


def _owners(tree: ast.AST) -> dict[int, ast.AST]:
    """The outermost function of each node."""
    owner: dict[int, ast.AST] = {}
    for top in ast.walk(tree):
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(top):
                owner.setdefault(id(node), top)
    return owner


def _literals(tree: ast.AST) -> list[ast.AST]:
    """Each string literal and f-string, but not the parts of an f-string."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                inside.add(id(part))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.Constant, ast.JoinedStr)) and id(n) not in inside
    ]


def _called_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _calls(func: ast.AST) -> set[str]:
    return {_called_name(n) for n in ast.walk(func) if isinstance(n, ast.Call)}


def _rule_names(func: ast.AST) -> set[str]:
    """The names a function binds from an ``owner_rules(...)`` call.

    ``rules = await owner_rules(...)``, ``for rule in await owner_rules(...)``
    and ``for rule in rules`` all count.
    """
    names: set[str] = set()

    def _is_factory(value: ast.AST) -> bool:
        if isinstance(value, ast.Await):
            value = value.value
        return isinstance(value, ast.Call) and _called_name(value) == _RULE_FACTORY

    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and _is_factory(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for node in ast.walk(func):
        if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
            it = node.iter
            if _is_factory(it) or (isinstance(it, ast.Name) and it.id in names):
                names.add(node.target.id)
    return names


def _rule_attrs(node: ast.JoinedStr, names: set[str]) -> set[str]:
    """The ``<rule>.<attr>`` names an f-string puts in, for a bound rule."""
    found: set[str] = set()
    for part in node.values:
        if not isinstance(part, ast.FormattedValue):
            continue
        for inner in ast.walk(part.value):
            if (isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name)
                    and inner.value.id in names):
                found.add(inner.attr)
    return found


def _registry_reads(source: str, file_name: str) -> list[tuple[str, int, str]]:
    """``(function, line, verdict)`` for each read of ``kb_entities``.

    The verdict is ``builder``, ``integrity``, ``rule-without-where`` or
    ``bare``. Only ``bare`` and ``rule-without-where`` fail.
    """
    tree = ast.parse(source)
    owner = _owners(tree)
    docs = _docstrings(tree)
    out: list[tuple[str, int, str]] = []
    for node in _literals(tree):
        if id(node) in docs:
            continue
        func = owner.get(id(node))
        name = getattr(func, "name", "<module>")
        if isinstance(node, ast.JoinedStr) and func is not None:
            attrs = _rule_attrs(node, _rule_names(func))
            if "source" in attrs:
                verdict = "rule" if "where" in attrs else "rule-without-where"
                out.append((name, node.lineno, verdict))
                continue
        if not _READS_ENTITIES.search(_text_of(node)):
            continue
        if name in _RULE_BUILDERS and file_name == "registry.py":
            out.append((name, node.lineno, "builder"))
        elif (file_name, name) in _INTEGRITY_READS:
            out.append((name, node.lineno, "integrity"))
        else:
            out.append((name, node.lineno, "bare"))
    return out


def _scanned_modules() -> list[Path]:
    roots = [_PACKAGE, _GATEWAY / "routes" / "knowledge"]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.rglob("*.py")))
    return found


def _all_reads() -> dict[tuple[str, str, int], str]:
    found: dict[tuple[str, str, int], str] = {}
    for path in _scanned_modules():
        for func, line, verdict in _registry_reads(
                path.read_text(encoding="utf-8"), path.name):
            found[(path.name, func, line)] = verdict
    return found


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _migration() -> str:
    """The migration that creates ``kb_entities``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
        if "CREATE TABLE IF NOT EXISTS kb_entities" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0].read_text(encoding="utf-8")


def _flat(sql: str) -> str:
    return " ".join(sql.split())


# ── 1. One entity for each record ───────────────────────────────────────────

def test_kc_entities_one_per_record() -> None:
    sql = _flat(_migration())
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS kb_entities_org_ref_key ON kb_entities "
        "(organization_id, ref_table, ref_id) WHERE ref_id IS NOT NULL;"
    ) in sql, "the partial unique index of §6.2 is missing or changed"

    inserts: list[str] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "kb_entities" not in source:
            continue
        tree = ast.parse(source)
        owner = _owners(tree)
        docs = _docstrings(tree)
        for node in _literals(tree):
            text = _text_of(node)
            if id(node) not in docs and _INSERTS_ENTITIES.search(text):
                func = getattr(owner.get(id(node)), "name", "<module>")
                inserts.append(f"{path.relative_to(_GATEWAY).as_posix()}:{func}")
                assert "ON CONFLICT (organization_id, ref_table, ref_id) " \
                       "WHERE ref_id IS NOT NULL DO NOTHING" in _flat(text), (
                           "the insert must conflict on kb_entities_org_ref_key")
    assert inserts == ["kb_entities/registry.py:ensure_entity"], (
        "ensure_entity is the only writer of a new kb_entities row "
        f"(knowledge_curation.md §6.4). Found: {inserts}"
    )


def test_the_owner_row_is_checked_in_the_bound_tenant() -> None:
    """``ensure_entity`` inserts only when the owner row is in the bound
    tenant. So org B cannot point at a record of org A."""
    func = _functions(_REGISTRY)["ensure_entity"]
    texts = [_flat(_text_of(n)) for n in _literals(func)]
    insert = next(t for t in texts if _INSERTS_ENTITIES.search(t))
    assert "WHERE EXISTS (" in insert
    from gateway.kb_entities import registry

    for table, check in registry.OWNER_EXISTS_SQL.items():
        assert "current_setting('app.tenant_id', true)" in check, table
        assert "CAST(:ke_ref AS uuid)" in check or table == registry.KNOWLEDGE_TABLE
    assert set(registry.OWNER_EXISTS_SQL) == {
        t for tables in registry.OWNER_TABLES.values() for t in tables
    }


async def test_ensure_entity_refuses_an_unknown_table() -> None:
    from gateway.kb_entities import registry

    with pytest.raises(ValueError):
        await registry.ensure_entity(object(), "app_user", "00000000-0000-0000-0000-000000000001")
    with pytest.raises(ValueError):
        await registry.ensure_entity(object(), "people", None, type="topic")


# ── 2. Each read goes through the owner rule ────────────────────────────────

def test_the_sweep_is_not_vacuous() -> None:
    reads = _all_reads()
    builders = {func for (_f, func, _l), v in reads.items() if v == "builder"}
    assert builders == set(_RULE_BUILDERS), builders
    assert any(v == "rule" for v in reads.values()), (
        "no statement puts in a rule's source and where, so the parse is wrong"
    )


def test_kc_directory_goes_through_owner_rule() -> None:
    bad = [
        f"  {name}:{line} in {func}() — {verdict}"
        for (name, func, line), verdict in sorted(_all_reads().items())
        if verdict in {"bare", "rule-without-where"}
    ]
    assert not bad, (
        "These statements read kb_entities outside the owner rule of "
        "knowledge_curation.md §6.4. Take the FROM of the read from "
        "registry.owner_rules(...), and put in the rule's where:\n" + "\n".join(bad)
    )


def test_each_builder_joins_its_owner_table() -> None:
    funcs = _functions(_REGISTRY)
    for builder, table in _RULE_BUILDERS.items():
        func = funcs[builder]
        reads = [_flat(_text_of(n)) for n in _literals(func)
                 if _READS_ENTITIES.search(_text_of(n))]
        assert reads, f"{builder} reads no kb_entities"
        for sql in reads:
            assert re.search(rf"\b(?:FROM|JOIN)\s+{table}\b", sql), (builder, sql)
            if builder != "_knowledge_rule":
                assert f"e.ref_table = '{table}'" in sql, (builder, sql)
                assert "e.ref_id = o.id" in sql, (builder, sql)


def test_the_factory_reaches_every_builder() -> None:
    calls = _calls(_functions(_REGISTRY)[_RULE_FACTORY])
    missing = sorted(set(_RULE_BUILDERS) - calls)
    assert not missing, f"owner_rules() never calls {missing}"


def test_the_integrity_list_is_not_stale() -> None:
    seen = {(name, func) for (name, func, _l), v in _all_reads().items() if v == "integrity"}
    stale = sorted(set(_INTEGRITY_READS) - seen)
    assert not stale, f"{stale} no longer read kb_entities. Remove them here."


# ── The fence sees what it claims to see ────────────────────────────────────

def test_the_fence_flags_a_bare_read() -> None:
    src = (
        "async def names(db, user):\n"
        "    return await db.execute(text('SELECT name FROM kb_entities'))\n"
    )
    assert _registry_reads(src, "registry.py") == [("names", 2, "bare")]


def test_the_fence_flags_a_bare_join_in_a_route() -> None:
    src = (
        "async def pages(db, user):\n"
        "    return 'SELECT p.id FROM kb_pages p JOIN kb_entities e ON e.page_id = p.id'\n"
    )
    assert _registry_reads(src, "pages.py") == [("pages", 2, "bare")]


def test_the_fence_flags_a_rule_source_without_its_where() -> None:
    src = (
        "async def rows(db, user, t):\n"
        "    for rule in await owner_rules(db, user, t):\n"
        "        sql = f'SELECT e.id {rule.source} LIMIT 5'\n"
    )
    assert _registry_reads(src, "registry.py") == [("rows", 3, "rule-without-where")]


def test_a_read_through_the_rule_passes() -> None:
    src = (
        "async def rows(db, user, t):\n"
        "    rules = await owner_rules(db, user, t)\n"
        "    for rule in rules:\n"
        "        sql = f'SELECT e.id {rule.source} WHERE {rule.where}'\n"
    )
    assert _registry_reads(src, "registry.py") == [("rows", 4, "rule")]


def test_a_builder_name_outside_the_registry_is_bare() -> None:
    src = "def _person_rule(user):\n    return 'FROM people o JOIN kb_entities e ON 1=1'\n"
    assert _registry_reads(src, "entities.py") == [("_person_rule", 2, "bare")]


# ── 3. One vocabulary ───────────────────────────────────────────────────────

def test_the_type_vocabulary_equals_the_check() -> None:
    from gateway.kb_entities import registry

    sql = _flat(_migration())
    match = re.search(r"kb_entities_type_check CHECK \(type IN \(([^)]*)\)\)", sql)
    assert match, "the type CHECK of the migration is missing"
    in_check = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert in_check == registry.ENTITY_TYPES


def test_the_owner_tables_equal_the_check_pairs() -> None:
    from gateway.kb_entities import registry

    sql = _flat(_migration())
    block = sql.split("CONSTRAINT kb_entities_type_ref_check CHECK (", 1)[1]
    block = block.split("CONSTRAINT kb_entities_owned_check", 1)[0]
    pairs: dict[str, set[str]] = {}
    for m in re.finditer(
            r"type = '(\w+)' AND ref_table (?:= '(\w+)'|IN \(([^)]*)\))", block):
        kind, one, many = m.group(1), m.group(2), m.group(3)
        names = {one} if one else {v.strip().strip("'") for v in many.split(",")}
        pairs.setdefault(kind, set()).update(names)
    assert pairs == {k: set(v) for k, v in registry.OWNER_TABLES.items()}
    assert set(registry.CREATABLE_TYPES) <= {
        k for k, v in registry.OWNER_TABLES.items() if registry.KNOWLEDGE_TABLE in v
    }


# ── 4. The rule calls the owning app ────────────────────────────────────────

def test_the_rules_call_the_owning_apps() -> None:
    funcs = _functions(_REGISTRY)
    assert {"require_crm_enabled", "require_crm_access"} <= _calls(funcs["_customer_rule"])
    assert {"resolve_visibility", "project_clause"} <= _calls(funcs["_workstream_rule"])
    assert "readable_spaces_clause" in _calls(funcs["_knowledge_rule"])
    for builder in ("_person_rule", "_workflow_rule", "_workstream_rule"):
        assert "has_permission" in _calls(funcs[builder]), builder
    workstream = " ".join(_flat(_text_of(n)) for n in _literals(funcs["_workstream_rule"]))
    assert "o.personal_owner IS NULL" in workstream
    person = " ".join(_flat(_text_of(n)) for n in _literals(funcs["_person_rule"]))
    assert "o.status = 'active'" in person


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def test_no_module_copies_another_apps_access_sql() -> None:
    for path in [*sorted(_PACKAGE.rglob("*.py")), _ROUTE]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        for node in _literals(tree):
            if id(node) in docs:
                continue
            text = _text_of(node)
            for table in _FOREIGN_RULE_TABLES:
                assert not re.search(rf"\b{table}\b", text), (
                    f"{path.name}:{node.lineno} names {table}. Call the owning "
                    "app's function, never a copy of its SQL (§6.4)")


def test_a_person_row_carries_the_name_only() -> None:
    """§6.4: a person row returns the name only, never an email, skills or HR
    fields. The read selects the rule's name and the registry columns."""
    funcs = _functions(_REGISTRY)
    person = " ".join(_flat(_text_of(n)) for n in _literals(funcs["_person_rule"]))
    for column in ("o.email", "o.skills", "o.role", "o.department",
                   "o.capacity_hours_per_week", "o.resume_summary"):
        assert column not in person, column
    rows = " ".join(_flat(_text_of(n)) for n in _literals(funcs["directory_rows"]))
    assert "SELECT {} AS ref_id, {} AS name, e.id AS entity_id" in rows


# ── 5. The person resolver reuses PZ-5 ──────────────────────────────────────

def test_the_resolver_reuses_the_pz5_helpers() -> None:
    tree = ast.parse(_RESOLVER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom)
                and node.module == "gateway.routes.admin.assistant_profile_people"):
            imported.update(a.name for a in node.names)
    assert {"fold", "email_in_org", "_directory_tier"} <= imported, imported
    for path in sorted(_PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("acb_graph"), path.name
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("acb_graph") for a in node.names), path.name


def test_the_route_module_is_registered() -> None:
    init = (_GATEWAY / "routes" / "knowledge" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r"^\s+entities,$", init, re.M), "routes/knowledge/__init__ must import entities"
    from gateway.routes import knowledge

    paths = {getattr(r, "path", "") for r in knowledge.router.routes}
    assert {"/knowledge/entities", "/knowledge/entities/{entity_id}",
            "/knowledge/entities/{entity_id}/page",
            "/knowledge/entities/by-ref"} <= paths, paths


# ── Repair round 1 ──────────────────────────────────────────────────────────

def test_a_page_title_is_cut_never_refused() -> None:
    """An owner name over the limit, or none, still gives a title (P2-4)."""
    from gateway.kb_entities import registry

    assert registry.page_title("x" * 250) == "x" * registry.NAME_MAX
    assert registry.page_title("  Asha   Rao ") == "Asha Rao"
    assert registry.page_title("") == registry.UNTITLED
    assert registry.page_title(None) == registry.UNTITLED


def test_the_sql_fold_strips_the_marks_fold_strips() -> None:
    """The SQL half of a search folds as ``fold()`` does, letter by letter."""
    from gateway.kb_entities import registry, resolver

    assert len(registry.FOLD_FROM) == len(registry.FOLD_TO)
    for ch, plain in zip(registry.FOLD_FROM, registry.FOLD_TO, strict=True):
        assert resolver.fold(ch) == plain, ch
    for ch in "éúñçåš":
        assert ch in registry.FOLD_FROM, ch


def test_the_customer_rule_checks_feature_crm_first() -> None:
    func = _functions(_REGISTRY)["_customer_rule"]
    source = ast.unparse(func)
    assert "CRM_FEATURE" in source and "has_permission" in _calls(func)
    assert source.index("CRM_FEATURE") < source.index("require_crm_enabled(user)")


def test_by_ref_reads_the_owner_row_under_the_rule_before_it_writes() -> None:
    source = ast.unparse(_functions(_REGISTRY)["ensure_by_ref"])
    assert source.index("directory_rows(") < source.index("ensure_entity(")


def test_q_is_matched_in_sql_before_the_limit() -> None:
    rows = ast.unparse(_functions(_REGISTRY)["directory_rows"])
    assert "strpos(" in rows and "unnest(e.aliases)" in rows
