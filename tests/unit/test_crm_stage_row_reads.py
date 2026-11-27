"""CRM-M3b R7: a deal reader never reads a stage-row value alone.

This source scan has its own file. In ``test_crm_pipelines_native.py`` it made
that slow suite a scanner file, which ``test_scanner_budget.py`` refuses.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: CRM-M3b round 2 (P3): the reader fence reads each module of the CRM
#: routes, not five of them.
_CRM_ROUTES = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
#: The two values of a deal stage that sit on its membership (migration 513).
_VALUE_COLUMNS = frozenset({"probability", "max_dwell_days"})

#: The form that reads the membership first. The fence removes it before it looks.
_MEMBERSHIP_FIRST = re.compile(r"COALESCE\(ps\.(probability|max_dwell_days), s\.\1\)")

#: Each SQL form that reads a stage-row value alone, over the text of one
#: string. The fence joins adjacent literals, the parts of an f-string and a
#: ``+`` of literals first, so a SELECT split across two literals is one text.
_SQL_FORMS: dict[str, re.Pattern[str]] = {
    # A bare column, not ``x.column`` and not an ``AS column`` name.
    "select": re.compile(
        r"\bSELECT\b(?:(?!\bFROM\b).)*(?<![\w.])(?<!AS )(?:probability|max_dwell_days)\b"
        r"(?:(?!\bSELECT\b).)*\bFROM crm_deal_statuses\b", re.S),
    "star": re.compile(r"\bSELECT \* FROM crm_deal_statuses\b"),
    "alias": re.compile(r"\bs\.(?:probability|max_dwell_days)\b"),
}

#: Repair 3 (P3): each name that one statement gives to ``crm_deal_statuses``,
#: with or without ``AS``. A word of SQL after the table is no name.
_STAGE_ALIAS = re.compile(
    r"\bcrm_deal_statuses(?:\s+AS)?\s+(?!(?:WHERE|ON|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS"
    r"|NATURAL|ORDER|GROUP|LIMIT|OFFSET|FOR|USING|SET|UNION|EXCEPT|INTERSECT|HAVING"
    r"|WINDOW|RETURNING|AND|OR|AS|VALUES|SELECT|FROM|TABLESAMPLE)\b)([A-Za-z_]\w*)", re.I)


def _named_stage_value(sql: str) -> bool:
    """True when *sql* reads a value through a name of ``crm_deal_statuses``:
    each alias that the statement gives it (:data:`_STAGE_ALIAS`), or the
    table name itself (repair 3). ``s.`` stays the ``alias`` form, because a
    fragment can name it with no table."""
    names = {"crm_deal_statuses", *_STAGE_ALIAS.findall(sql)}
    pattern = "|".join(re.escape(n) for n in sorted(names))
    return re.search(
        rf"(?<![\w.])(?:{pattern})\.(?:probability|max_dwell_days)\b", sql, re.I,
    ) is not None


def _sql_forms(sql: str) -> list[str]:
    """Each form of :data:`_SQL_FORMS` that *sql* matches, and ``named``."""
    forms = [form for form, rule in _SQL_FORMS.items() if rule.search(sql)]
    return [*forms, "named"] if _named_stage_value(sql) else forms


#: The helpers that load one stage row or the stage-row list.
_LOADERS = frozenset({"load_row", "require_row", "read_status", "read_statuses"})

#: The functions that may read the stage-row value alone, each with its
#: reason. Each one must still read it, so the list cannot rot.
_STAGE_ROW_OWNERS: dict[tuple[str, str], str] = {
    ("core.py", "_effective_columns"): (
        "it builds the stage-row list of GET /crm/statuses, and the list that reads "
        "the membership first"),
    ("core.py", "read_deal_stages"): "an org with no deal pipeline has no membership",
    ("cutover.py", "fold_cutover_overlay"): "a write: the switch folds the overlay",
    ("seed.py", "<module>"): "a write: a new membership copies the stage row",
    ("stage_metadata.py", "import_zoho_stages"): "a write: the repair plans the stage row",
}

#: One mutation of each form that the fence catches, as (form, source). Each
#: must turn it red. The three ``named`` ones are repair 3: a JOIN alias, an
#: ``AS`` alias and the table name.
_MUTATIONS: tuple[tuple[str, str], ...] = (
    ("select", (
        "async def lanes(db):\n"
        "    return await db.execute(text('SELECT id, probability '\n"
        "                                 'FROM crm_deal_statuses'))\n")),
    ("star", "SQL = 'SELECT * FROM crm_deal_statuses ORDER BY position, name'\n"),
    ("alias", "LIMIT = 'CAST(s.max_dwell_days AS int) AS limit_days'\n"),
    ("named", (
        "SQL = ('SELECT ps.status_id, ds.probability FROM crm_pipeline_stages ps '\n"
        "       'JOIN crm_deal_statuses ds ON ds.id = ps.status_id')\n")),
    ("named", "SQL = 'SELECT st.max_dwell_days FROM crm_deal_statuses AS st'\n"),
    ("named", "SQL = 'SELECT crm_deal_statuses.probability FROM crm_deal_statuses'\n"),
    ("attr", (
        "async def chance(db, sid):\n"
        "    stage = await load_row(db, 'crm_deal_statuses', sid)\n"
        "    return stage.probability\n")),
    ("getattr", (
        "TABLE = 'crm_deal_statuses'\n"
        "async def limit(db, sid):\n"
        "    stage = await require_row(db, TABLE, sid, 'Status')\n"
        "    return getattr(stage, 'max_dwell_days', None)\n")),
    ("read_status", (
        "async def one(db, sid):\n"
        "    return status_wire(await read_status(db, 'crm_deal_statuses', sid))\n")),
    ("read_statuses",
     "async def all_(db):\n    return await read_statuses(db, 'crm_deal_statuses')\n"),
)


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """The string constants of the module level, by name."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update({t.id: value.value for t in targets if isinstance(t, ast.Name)})
    return found


def _text_of(node: ast.AST, names: dict[str, str]) -> str | None:
    """The text of a string node: a literal, adjacent literals (the AST joins
    them), an f-string with each module constant in place, or a ``+`` of
    those. None when a part is no string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
                parts.append(names.get(part.value.id, "{}"))
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text_of(node.left, names), _text_of(node.right, names)
        return left + right if left is not None and right is not None else None
    return None


def _stage_row_reads(source: str) -> list[tuple[str, str]]:
    """Each read of a stage-row value alone in *source*, as (function, form)."""
    tree = ast.parse(source)
    names = _module_strings(tree)
    parent: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node: ast.AST) -> str:
        chain = []
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(node.name)
        return ".".join(reversed(chain)) or "<module>"

    def loads_a_stage(node: ast.AST) -> str | None:
        """The name of the loader when *node* loads a deal stage row."""
        call = node.value if isinstance(node, ast.Await) else node
        if not isinstance(call, ast.Call):
            return None
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        tables = [a.value if isinstance(a, ast.Constant) else names.get(getattr(a, "id", ""))
                  for a in call.args]
        return name if name in _LOADERS and "crm_deal_statuses" in tables else None

    found: list[tuple[str, str]] = []
    done: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in done or not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        sql = _text_of(node, names)
        if sql is None:
            continue
        done.update(id(sub) for sub in ast.walk(node))
        found += [(owner(node), form) for form in _sql_forms(_MEMBERSHIP_FIRST.sub("", sql))]
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rows = {t.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
                and loads_a_stage(node.value) for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            loader = loads_a_stage(node) if isinstance(node, ast.Call) else None
            if loader in ("read_status", "read_statuses"):
                found.append((owner(node), loader))
            if isinstance(node, ast.Attribute) and node.attr in _VALUE_COLUMNS and (
                    getattr(node.value, "id", None) in rows or loads_a_stage(node.value)):
                found.append((owner(node), "attr"))
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in _VALUE_COLUMNS
                    and (getattr(node.args[0], "id", None) in rows
                         or loads_a_stage(node.args[0]))):
                found.append((owner(node), "getattr"))
    return found


def test_no_deal_reader_reads_the_stage_row_value_alone() -> None:
    """R7 for the rule of ``apps/services/gateway/AGENTS.md`` (CRM-M3b repair
    5, widened in round 2). A deal reader never reads ``probability`` or
    ``max_dwell_days`` of the stage row alone, so one deal never shows two
    win chances. The fence parses each module of ``routes/crm/`` and finds:

    - SQL that names a bare column, ``s.<column>`` or ``SELECT *`` with
      ``FROM crm_deal_statuses``. Adjacent literals, an f-string with a
      module constant, and a ``+`` of literals join first.
    - SQL that reads a column through each alias that the statement gives
      to ``crm_deal_statuses``, with or without ``AS``, or through the table
      name itself (repair 3).
    - ``read_status`` or ``read_statuses`` of ``crm_deal_statuses``.
    - ``load_row``, ``require_row``, ``read_status`` or ``read_statuses`` of
      ``crm_deal_statuses``, and then ``.probability``, ``.max_dwell_days``
      or ``getattr`` of either, in the same function.

    Only the functions of ``_STAGE_ROW_OWNERS`` may read the stage row, each
    for its reason. The held-stage read of the board is the one use of the
    stage-row list: a stage that the pipeline does not list has no
    membership.

    Out of reach, so a reviewer checks them: a table name in a local name or
    a parameter (``load_row(db, table, ...)``), a row passed to another
    function and read there, ``getattr`` with a column in a name, SQL that
    joins a runtime value between SELECT and FROM, and each reader outside
    ``routes/crm/`` (``crm_sync``). Mutation: each entry of ``_MUTATIONS``
    turns the scan red."""
    for form, mutation in _MUTATIONS:
        assert form in {f for _owner, f in _stage_row_reads(mutation)}, (
            "non-vacuity", form, mutation)
    found: dict[tuple[str, str], set[str]] = {}
    held: dict[str, int] = {}
    for path in sorted(_CRM_ROUTES.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for where, form in _stage_row_reads(source):
            found.setdefault((path.name, where), set()).add(form)
        held[path.name] = source.count('EFFECTIVE_STATUS_COLUMNS["crm_deal_statuses"]')
    assert len(held) > 40, "the fence reads each module"
    unexpected = {k: sorted(v) for k, v in found.items() if k not in _STAGE_ROW_OWNERS}
    assert unexpected == {}, unexpected
    assert set(_STAGE_ROW_OWNERS) <= set(found), set(_STAGE_ROW_OWNERS) - set(found)
    assert {name: n for name, n in held.items() if n} == {"pipeline.py": 1}, held
