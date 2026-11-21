"""The AST source fence for the audit writers with no tenant (task 2221, slice 2).

Six modules write an audit row with no tenant source. Each row that holds
tenant data must go through the module's one ``_audit`` guard. Two named rows
hold no tenant data, and they call ``record`` directly (Option-A).
``test_audit_tick_writers_org.py`` runs this fence on each module.

**The threat model.** This fence catches an honest new audit write that skips
the guard. It also catches each known shape of a bypass: an alias, a second
or nested ``_audit``, a decorator or a default on the guard, a rebound name,
a dunder attribute, reflection, and an import that reaches ``record`` through
another module. The fence does not prove the code against a hostile author,
because no source fence can do that. The behaviour tests in
``test_audit_tick_writers_org.py`` and the R8 tests in
``test_audit_tick_writers_org_r8.py`` pin the twelve named rows at run time.

The checks are allow-lists where possible:

* The guard has one allowed text. The fence compares the whole
  ``ast.unparse`` of the function, without its docstring.
* A ``record`` call is legal at two places only. One is the single statement
  of the guard's ``if``, by node identity. The other is a statement in the
  body of a module-level function that ``SYSTEM_CALLS`` names with its action.
* ``acb_audit`` is imported once, at module level, with the allowed names.
* A writer module imports another writer module only where
  ``WRITER_IMPORTS`` names it.
"""
from __future__ import annotations

import ast
from copy import deepcopy

#: The source path and the dotted name of each writer module.
WRITERS = {
    "apps/services/ingestion/ingestion/scheduler.py": "ingestion.scheduler",
    "apps/services/ingestion/ingestion/sources/gmail/webhook.py":
        "ingestion.sources.gmail.webhook",
    "apps/services/ingestion/ingestion/sources/zoho/webhook.py":
        "ingestion.sources.zoho.webhook",
    "apps/services/reconciler/reconciler/main.py": "reconciler.main",
    "scripts/reconciler.py": "scripts.reconciler",
    "scripts/zoho_sync.py": "scripts.zoho_sync",
}
#: The Option-A system rows: (path, module-level function, action).
SYSTEM_CALLS = {
    ("apps/services/ingestion/ingestion/scheduler.py", "_run_zoho", "run_ok"),
    ("apps/services/ingestion/ingestion/scheduler.py", "_serve", "scheduler_started"),
}
#: The only import of one writer module by another: (path, dotted name, alias).
WRITER_IMPORTS = {
    ("apps/services/ingestion/ingestion/scheduler.py", "scripts.zoho_sync", None),
    ("apps/services/ingestion/ingestion/scheduler.py", "scripts.reconciler", None),
    ("apps/services/reconciler/reconciler/main.py", "scripts.reconciler", "core"),
}
#: The one allowed line that imports from ``acb_audit``.
AUDIT_IMPORT = ("AuditEvent", "record", "refuse_tenantless")
#: The names that the guard depends on. Nothing else may bind them.
PINNED = {"record", "_persist", "refuse_tenantless", "AuditEvent"}
#: The names that write a row.
SINKS = {"record", "_persist"}
#: Modules that reach an attribute by a string or a frame.
FORBIDDEN_MODULES = {
    "operator", "inspect", "functools", "importlib", "sys", "builtins", "types",
    "ctypes", "gc", "runpy", "pickle", "marshal", "code", "codeop",
}
#: Built-in names that reach an attribute by a string.
REFLECTION = {
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "__import__",
    "eval", "exec", "compile", "breakpoint",
}
#: The only dunder names that a writer module uses.
ALLOWED_DUNDER_NAMES = {"__name__", "__all__", "__doc__"}

_BODY = "    if not refuse_tenantless(event):\n        record(event)"
#: The allowed texts of the guard: the annotations are optional.
GUARD_TEXTS = {
    f"def _audit({arg}){ret}:\n{_BODY}"
    for arg in ("event: AuditEvent", "event")
    for ret in (" -> None", "")
}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def action_of(call: ast.Call) -> str | None:
    """The ``action=`` constant of the ``AuditEvent`` in *call*, if any."""
    if call.args and isinstance(call.args[0], ast.Call):
        for kw in call.args[0].keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    return None


def _without_docstring(fn: ast.FunctionDef) -> ast.FunctionDef:
    copy = deepcopy(fn)
    body = copy.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        copy.body = body[1:]
    return copy


def _guard(tree: ast.Module, path: str, out: list[str]) -> ast.Call | None:
    """The ``record(event)`` call node of THE guard, or ``None`` with a reason."""
    audits = [n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS) and n.name == "_audit"]
    if len(audits) != 1:
        out.append(f"{path}: expected one _audit in the module, found {len(audits)}")
        return None
    [fn] = audits
    if fn not in tree.body or not isinstance(fn, ast.FunctionDef):
        out.append(f"{path}: _audit must be a module-level def")
        return None
    if fn.decorator_list:
        out.append(f"{path}: _audit has a decorator")
    a = fn.args
    if (a.defaults or a.kw_defaults or a.vararg or a.kwarg or a.kwonlyargs
            or a.posonlyargs or len(a.args) != 1):
        out.append(f"{path}: _audit must take one positional parameter and no default")
    text = ast.unparse(_without_docstring(fn))
    if text not in GUARD_TEXTS:
        out.append(f"{path}: the guard text is not the allowed one:\n{text}")
        return None
    return fn.body[-1].body[0].value


def _system_call_ok(call: ast.Call, parents: dict, top: set[int], path: str) -> bool:
    """*call* is a statement in the body of a named module-level system function."""
    child, up = call, parents.get(call)
    while up is not None and not isinstance(up, _FUNCTIONS):
        if isinstance(up, ast.Lambda | ast.ClassDef):
            return False
        child, up = up, parents.get(up)
    if up is None or id(up) not in top or child not in up.body:
        return False
    return (path, up.name, action_of(call)) in SYSTEM_CALLS


def _binds(node: ast.AST) -> list[str]:
    """The names that *node* binds, other than through an import."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return [node.id]
    if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Global | ast.Nonlocal):
        return list(node.names)
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
        return [node.name]
    return []


def _import_violations(node: ast.AST, path: str, at: str, tree: ast.Module) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Import):
        pairs = [(a.name, a.asname, a.asname or a.name.split(".")[0]) for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            return [f"{at}: a relative import"]
        base = node.module or ""
        if base == "acb_audit":
            names = tuple(a.name for a in node.names)
            if (node not in tree.body or names != AUDIT_IMPORT
                    or any(a.asname for a in node.names)):
                out.append(f"{at}: the acb_audit import is not the one allowed line")
            return out
        pairs = [(f"{base}.{a.name}", a.asname, a.asname or a.name) for a in node.names]
        pairs.append((base, None, ""))
    else:
        return out
    for dotted, alias, bound in pairs:
        root = dotted.split(".")[0]
        if root == "acb_audit":
            out.append(f"{at}: an import of {dotted}")
        if root in FORBIDDEN_MODULES:
            out.append(f"{at}: an import of {root} can reach record around _audit")
        if dotted in WRITERS.values() and (path, dotted, alias) not in WRITER_IMPORTS:
            out.append(f"{at}: an import of the writer module {dotted}")
        if bound in PINNED:
            out.append(f"{at}: an import binds {bound}")
    return out


def _node_violations(node: ast.AST, path: str, at: str, parents: dict,
                     sink: ast.Call | None, top: set[int]) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            out.append(f"{at}: the dunder attribute .{node.attr}")
        if node.attr in SINKS:
            out.append(f"{at}: .{node.attr} reaches the audit seam around _audit")
    elif isinstance(node, ast.Name):
        if node.id in REFLECTION:
            out.append(f"{at}: {node.id} can reach record around _audit")
        if (node.id.startswith("__") and node.id.endswith("__")
                and node.id not in ALLOWED_DUNDER_NAMES):
            out.append(f"{at}: the dunder name {node.id}")
        if node.id in SINKS and isinstance(node.ctx, ast.Load):
            call = parents.get(node)
            if not (isinstance(call, ast.Call) and call.func is node):
                out.append(f"{at}: {node.id} used as a value, not called")
            elif call is not sink and not _system_call_ok(call, parents, top, path):
                out.append(f"{at}: {node.id}() outside the guard and the system rows")
    elif isinstance(node, ast.keyword) and node.arg == "organization_id":
        out.append(f"{at}: organization_id= on an audit row with no tenant source")
    bound = [n for n in _binds(node) if n in PINNED]
    if bound and not (isinstance(node, ast.arg) and node.arg == "event"):
        out.append(f"{at}: {bound[0]} is bound again")
    return out


def audit_fence_violations(src: str, path: str) -> list[str]:
    """Each way that *src* writes an audit row around the guard."""
    tree = ast.parse(src)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out: list[str] = []
    sink = _guard(tree, path, out)
    top = {id(n) for n in tree.body if isinstance(n, _FUNCTIONS)}
    audit_imports = [n for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module == "acb_audit"]
    if len(audit_imports) != 1:
        out.append(f"{path}: expected one acb_audit import, found {len(audit_imports)}")
    for node in ast.walk(tree):
        at = f"{path}:{getattr(node, 'lineno', '?')}"
        out += _import_violations(node, path, at, tree)
        out += _node_violations(node, path, at, parents, sink, top)
    return out
