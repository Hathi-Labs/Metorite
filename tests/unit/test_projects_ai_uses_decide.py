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

Reasons: "prose a person reads", "real judgement", "moving in DEC-2",
"tier config" (a name that picks no answer, it only names a tier).
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
]

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
    # Ranks the day with a one-line rationale. Not a DEC-2 step. Review in DEC-2.
    (T + "calendar.py", "_llm_rank_day"): "real judgement",
    # --- A tier name that picks no answer. ---
    (T + "calendar.py", "<module>"): "tier config",  # PLAN_MODEL
    (T + "settings.py", "<module>"): "tier config",  # per-function default tiers
}

REASONS = {"prose a person reads", "real judgement", "moving in DEC-2", "tier config"}


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
        for path in sorted(root.rglob("*.py")):
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
    bad = {k: v for k, v in ALLOW.items() if v not in REASONS}
    assert not bad, f"Entries with no valid reason: {bad}"


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
