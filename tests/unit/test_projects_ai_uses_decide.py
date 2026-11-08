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

_CALLS = {"acompletion_with_fallback", "acompletion"}
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
    (T + "ai.py", "_ask_clarify_model"): "real judgement",  # clarify proposals
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
    """Feature names on the decide seam: every dotted string constant in the
    registry file, docstrings aside. This holds on main (name tuples) and after
    DEC-1 (``Feature("projects.align", ...)`` rows)."""
    tree = ast.parse(DECIDE_FEATURES.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for n in ast.walk(tree):
        scoped = isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if scoped and n.body and isinstance(n.body[0], ast.Expr)                 and isinstance(n.body[0].value, ast.Constant):
            skip.add(id(n.body[0].value))
    return {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in skip and _FEATURE.match(n.value)
    }


def _rel(path: Path) -> str:
    try:
        return path.relative_to(GATEWAY).as_posix()
    except ValueError:
        return path.relative_to(REPO).as_posix()


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

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        hit = False
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            hit = name in _CALLS or any(k.arg == "response_format" for k in node.keywords)
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
