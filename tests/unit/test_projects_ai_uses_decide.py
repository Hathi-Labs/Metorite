"""Rule D fence: a Projects choice runs on the decision tier (task 2308, DEC-3).

``project_management_app.md`` section 14.1 rule 8 says a step that sorts,
ranks, selects or classifies uses ``acb_llm.decide``. It does not use a JSON
prompt on an LLM tier. This test scans the Projects code for model calls and
fails on any call that the allow-list below does not name.

The scan reads the AST, so a moved line changes nothing. An entry is keyed by
file plus function name. A STALE entry (the function no longer calls a model)
also fails, so the list shrinks when DEC-2 moves a step.

A model call is one of:
  * a call to ``acompletion_with_fallback`` or ``acompletion``,
  * a keyword argument ``response_format``,
  * a string constant that names an LLM tier (``tier-fast`` and its kin).
Docstrings and comments do not count.

Reasons:
  * "prose a person reads" and "real judgement": an LLM tier is right here.
  * "moving in DEC-2": a pick that DEC-2 (task 2307) moves to the decision tier.
  * "fallback of <feature>": the old call stays as the shadow comparison and the
    fallback of a decide feature. The fence accepts it only when ``<feature>``
    is registered in ``gateway/decide_features.py``. A DEC-2 step changes its
    entry from "moving in DEC-2" to "fallback of projects.<feature>" when it
    registers the feature.
  * "tier config": a module-level constant that names a tier. It picks no
    answer. Only the ``<module>`` scope may use it.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps" / "services" / "gateway" / "gateway"
SCAN_ROOTS = [
    GATEWAY / "routes" / "projects",
    GATEWAY / "routes" / "tasks",
    REPO / "apps" / "skills" / "skill-projects",
    GATEWAY / "routes" / "project_rules_propose.py",  # a single-file root
]
DECIDE_FEATURES = GATEWAY / "decide_features.py"
_FEATURE = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_0-9]*)+$")

# Chat-completion entry points of packages/acb_llm (aembedding is no model pick).
# DISTINCT names count on any call. GENERIC names count only when the file
# imports them from, or reaches them through, an ``acb_llm`` module.
_DISTINCT = {
    "acompletion_with_fallback", "acompletion", "acompletion_stream_text",
    "completion_on_router", "complete_with_tools",
}
_GENERIC = {"complete"}
_CALLS = _DISTINCT | _GENERIC
_TIER = re.compile(r"^tier-(fast|balanced|powerful|code|reasoning|[0-9]+)$")

T = "routes/tasks/"
# (path relative to the gateway package or repo, function) -> reason.
ALLOW: dict[tuple[str, str], str] = {
    # --- DEC-2 (task 2307) moves these six picks. Each leaves the list. ---
    (T + "ai.py", "_find_parent_task"): "moving in DEC-2",  # parent-step check
    (T + "ai.py", "_llm_enrich"): "moving in DEC-2",  # enrich picks
    (T + "ai.py", "_llm_suggest_title"): "moving in DEC-2",  # vague-title gate
    (T + "ai.py", "_llm_atomize"): "moving in DEC-2",  # atomize dedup
    (T + "capture_email.py", "_llm_detect_commitment"): "moving in DEC-2",
    (T + "capture_email.py", "_llm_capture"): "moving in DEC-2",  # delegate pick
    # --- Prose a person reads, or real judgement. ---
    # Serves _llm_propose: picks project, assignee and type. A Choice, so it moves.
    # The SEVENTH DEC-2 step (DEC-2b).
    (T + "ai.py", "_ask_clarify_model"): "moving in DEC-2",
    (T + "planning.py", "_llm_plan"): "real judgement",  # a plan from a brief
    (T + "resume_parse.py", "llm_extract_profile"): "real judgement",  # parse text
    # Ranks the day. calendar_decide.py runs my_day.day_plan in front of it.
    (T + "calendar.py", "_llm_rank_day"): "fallback of my_day.day_plan",
    # Reads a page and proposes rules for a person to approve.
    ("routes/project_rules_propose.py", "_ask"): "real judgement",
    ("routes/project_rules_propose.py", "<module>"): "tier config",  # MODEL
    # --- A tier name that picks no answer. ---
    (T + "calendar.py", "<module>"): "tier config",  # PLAN_MODEL
    (T + "settings.py", "<module>"): "tier config",  # per-function default tiers
}

REASONS = {"prose a person reads", "real judgement", "moving in DEC-2", "tier config"}
FALLBACK = "fallback of "


def registered_features() -> set[str]:
    """Feature names on the decide seam. Two sources, and no other string counts:
    the first argument of each ``Feature(...)`` call (the DEC-1 table), and the
    strings in a module-level ``*FEATURES`` / ``REGISTERED`` name (the main tuples).
    """
    tree = ast.parse(DECIDE_FEATURES.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Feature" and n.args:
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.add(a0.value)
    for n in tree.body:
        if isinstance(n, ast.AnnAssign):
            targets, value = [n.target], n.value
        elif isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        else:
            continue
        if value is None or not any(
            isinstance(t, ast.Name) and (t.id.endswith("FEATURES") or t.id == "REGISTERED")
            for t in targets
        ):
            continue
        out |= {
            c.value for c in ast.walk(value)
            if isinstance(c, ast.Constant) and isinstance(c.value, str) and _FEATURE.match(c.value)
        }
    return out


def _rel(path: Path) -> str:
    try:
        return path.relative_to(GATEWAY).as_posix()
    except ValueError:
        return path.relative_to(REPO).as_posix()


Names = dict[str, tuple[str, str | None]]


def _import_names(tree: ast.AST) -> Names:
    """Per file: local name -> (module, original name, or None for a module alias)."""
    names: Names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for al in node.names:
                names[al.asname or al.name] = (node.module, al.name)
        elif isinstance(node, ast.Import):
            for al in node.names:
                names[al.asname or al.name.split(".")[0]] = (al.name, None)
    return names


def _from_llm(names: Names, local: str) -> bool:
    mod = names.get(local, ("", None))[0]
    return mod == "acb_llm" or mod.startswith("acb_llm.")


def _is_model_call(fn: ast.expr, names: Names) -> bool:
    if isinstance(fn, ast.Name):
        orig = names.get(fn.id, ("", fn.id))[1] or fn.id
        return orig in _DISTINCT or (orig in _GENERIC and _from_llm(names, fn.id))
    if isinstance(fn, ast.Attribute):
        if fn.attr in _DISTINCT:
            return True
        base = fn.value
        while isinstance(base, ast.Attribute):
            base = base.value
        return fn.attr in _GENERIC and isinstance(base, ast.Name) and _from_llm(names, base.id)
    return False


def model_calls(source: str) -> set[str]:
    """Return the names of the functions in ``source`` that hold a model call."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: set[str] = set()
    names = _import_names(tree)

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        hit = False
        if isinstance(node, ast.Call):
            hit = _is_model_call(node.func, names) or any(
                k.arg == "response_format" for k in node.keywords)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = id(node) not in docstrings and bool(_TIER.match(node.value))
        if hit:
            found.add(scope)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, "<module>")
    return found


def scan() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for root in SCAN_ROOTS:
        for path in sorted([root] if root.is_file() else root.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            for fn in model_calls(src):
                out.add((_rel(path), fn))
    return out


def test_every_model_call_is_on_the_allow_list() -> None:
    new = sorted(scan() - set(ALLOW))
    assert not new, (
        "Rule D (project_management_app.md 14.1 rule 8): a Projects step that "
        "sorts, ranks, selects or classifies uses acb_llm.decide through "
        "gateway/decide_features.py, not a JSON prompt on an LLM tier. "
        f"These functions call a model and are not on the allow-list: {new}. "
        "Move the step to the decision tier, or add it with a reason."
    )


def test_no_stale_allow_list_entry() -> None:
    stale = sorted(set(ALLOW) - scan())
    assert not stale, (
        f"These allow-list entries no longer call a model: {stale}. "
        "Delete them. The list shrinks when DEC-2 moves a step."
    )


def test_every_entry_has_a_known_reason() -> None:
    feats = registered_features()
    bad = {}
    for key, why in ALLOW.items():
        if why.startswith(FALLBACK):
            if why[len(FALLBACK):] not in feats:
                bad[key] = f"{why}: feature not registered in decide_features.py"
        elif why not in REASONS:
            bad[key] = why
        elif why == "tier config" and key[1] != "<module>":
            bad[key] = "tier config is for module-level constants only"
    assert not bad, f"Entries with no valid reason: {bad}"


def test_an_unregistered_fallback_feature_is_refused() -> None:
    feats = registered_features()
    assert "my_day.day_plan" in feats
    assert "projects.no_such_feature" not in feats


def test_a_planted_json_pick_on_tier_fast_fails(tmp_path: Path) -> None:
    scratch = tmp_path / "planted.py"
    scratch.write_text(
        "async def pick_owner(items):\n"
        "    resp, _ = await acompletion_with_fallback(\n"
        "        model='tier-fast', response_format={'type': 'json_object'})\n"
        "    return resp\n",
        encoding="utf-8",
    )
    found = model_calls(scratch.read_text(encoding="utf-8"))
    assert found == {"pick_owner"}
    assert ("planted.py", "pick_owner") not in ALLOW


def test_docstring_and_comment_mentions_do_not_count() -> None:
    src = '"""Uses tier-fast."""\n# tier-fast\ndef f():\n    """tier-balanced"""\n    return 1\n'
    assert model_calls(src) == set()


def test_aliased_import_is_seen() -> None:
    src = (
        "from acb_llm.context import acompletion_with_fallback as ac\n"
        "async def f():\n    return await ac(model='m')\n"
        "import acb_llm.context as ctx\n"
        "async def g():\n    return await ctx.acompletion_with_fallback()\n"
    )
    assert model_calls(src) == {"f", "g"}


def test_other_acb_llm_entry_points_are_seen() -> None:
    src = (
        "from acb_llm.client import complete as c, complete_with_tools\n"
        "from acb_llm import routed\n"
        "async def a():\n    return await c([])\n"
        "async def b():\n    return await complete_with_tools([])\n"
        "async def d():\n    return await routed.completion_on_router()\n"
    )
    assert model_calls(src) == {"a", "b", "d"}


def test_a_local_function_named_complete_is_not_a_model_call() -> None:
    src = "def complete(x):\n    return x\ndef f():\n    return complete(1)\n"
    assert model_calls(src) == set()


def test_registered_features_is_strict() -> None:
    feats = registered_features()
    assert "my_day.day_plan" in feats
    assert "decide.fallback" not in feats
    assert "gateway.decide_features" not in feats


def test_scan_reports_a_planted_pick_as_unlisted(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "routes" / "tasks"
    root.mkdir(parents=True)
    (root / "planted.py").write_text(
        "async def pick(x):\n    return await acompletion_with_fallback(\n"
        "        model='tier-fast', response_format={'type': 'json_object'})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "SCAN_ROOTS", [root])
    monkeypatch.setattr(sys.modules[__name__], "GATEWAY", tmp_path)
    unlisted = scan() - set(ALLOW)
    assert unlisted == {("routes/tasks/planted.py", "pick")}
