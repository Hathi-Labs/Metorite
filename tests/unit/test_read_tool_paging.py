"""No data out of reach: every agent read pages, counts, and never cuts silently.

Owner report, 2026-10-14. The orchestrator asked the Task manager for items 31
to 35 of a 35-item inbox. ``my_tasks_list`` loaded all 35, printed 30, and had
no parameter that could reach the other 5.

The contract (``acb_common.paging``):

1. A read returns one page, with an ``offset`` (or a page or a cursor where
   the backend already has one) and an optional ``limit``.
2. A page that does not hold everything says how many items exist and how to
   get the next page, in one line.
3. No tool writes ``rows[:30]`` on its result. It calls ``paged``.

Fences here (R7):

* the helper's own lines, item by item;
* an AST scan of every module that defines agent tools. A head slice
  (``x[:N]``) that is a loop's source, a comprehension's source, an
  assignment or a return fails, unless :data:`ALLOWED` names it with a
  reason. The allowlist only shrinks: an entry whose slice is gone fails,
  and :data:`ALLOWED_CEILING` stops a new entry going in quietly;
* per-tool tests: page 2 holds items 31 to 35, the hint says the total, and
  an offset past the end gives an empty page with a clear line.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from acb_common.paging import PAGE_MAX, bounds, page_line, paged

REPO = Path(__file__).resolve().parents[2]


# ── 1. The helper ───────────────────────────────────────────────────────────

def test_a_first_page_of_35_names_the_total_and_the_next_offset() -> None:
    page = paged(list(range(35)), 0, 0, default=30)
    assert page.items == list(range(30))
    assert page.total == 35
    assert page.line == "Showing 1-30 of 35. Call again with offset=30 for the rest."


def test_page_two_holds_items_31_to_35_and_says_it_is_the_last() -> None:
    page = paged(list(range(1, 36)), 30, 0, default=30)
    assert page.items == [31, 32, 33, 34, 35]
    assert page.line == "Showing 31-35 of 35. This is the last page."


def test_an_offset_past_the_end_is_an_empty_page_with_a_clear_line() -> None:
    page = paged(list(range(35)), 40, 0, default=30)
    assert page.items == []
    assert page.line == "Nothing at offset=40. There are 35 items, so start below offset=35."


def test_a_page_that_holds_everything_says_nothing() -> None:
    assert paged([1, 2, 3], 0, 0, default=30).line == ""


def test_the_limit_is_bounded_and_a_bad_one_is_the_default() -> None:
    assert bounds(0, 10_000, default=30) == (0, PAGE_MAX)
    assert bounds(-5, 0, default=30) == (0, 30)
    assert bounds("x", "y", default=30) == (0, 30)


def test_a_backend_with_no_total_still_names_the_next_page() -> None:
    assert page_line(None, 0, 50, more=True) == (
        "Showing 1-50. More items exist. Call again with offset=50 for the rest.")
    assert page_line(None, 0, 50, cursor="abc").endswith(
        "Call again with cursor='abc' for the rest.")
    assert page_line(None, 0, 7) == ""


def test_a_page_number_tool_is_told_the_next_page_number() -> None:
    assert page_line(60, 25, 25, page_size=25) == (
        "Showing 26-50 of 60. Call again with page=3 for the rest.")
    assert page_line(60, 50, 10, page_size=25) == (
        "Showing 51-60 of 60. This is the last page.")
    assert page_line(60, 75, 0, page_size=25) == (
        "Nothing on page 4. There are 60 items, so the last page is 3.")


# ── 2. The fence: no silent head slice in a tool module ─────────────────────

#: Every module that defines agent-callable tools.
TOOL_MODULES: tuple[str, ...] = (
    "apps/skills/*/*/*.py",
    "apps/agents/*/agents.py",
    "packages/acb_skills/acb_skills/*_tools.py",
    "apps/services/orchestrator/orchestrator/agents.py",
    "apps/services/orchestrator/orchestrator/workflow_tools.py",
)

#: ``"<path>::<function>::<slice source>"`` → why the cut is not a lost row.
#: This list only shrinks. Fix the tool with ``paged`` and delete the line.
ALLOWED: dict[str, str] = {
    'apps/agents/agent-email-assistant/agents.py::_card_detail::detail[:limit]':
        'Text, not rows: the cut shortens one string.',
    "apps/agents/agent-email-assistant/agents.py::_card_text::' '.join(kept.split())[:limit]":
        'Text, not rows: the cut shortens one string.',
    "apps/agents/agent-email-assistant/agents.py::_one_line::' '.join(str(value or '').split())[:limit]":
        'Text, not rows: the cut shortens one string.',
    "apps/skills/skill-projects/skill_projects/forms.py::_plan_row::_clean(item.get('due'))[:10]":
        'Text: a date string cut to YYYY-MM-DD.',
    "apps/skills/skill-projects/skill_projects/forms.py::_plan_row::_clean(item.get('start'))[:10]":
        'Text: a date string cut to YYYY-MM-DD.',
    'apps/skills/skill-projects/skill_projects/forms.py::_task_changes::_clean(values.get(key))[:10]':
        'Text: a date string cut to YYYY-MM-DD.',
    'apps/skills/skill-projects/skill_projects/views.py::_card_cell::value[:10]':
        'Text: a date string cut to YYYY-MM-DD.',
    'packages/acb_skills/acb_skills/ask_tools.py::clip_words::cut[:space]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/ask_tools.py::clip_words::t[:room]':
        'Text, not rows: the cut shortens one string.',
    'apps/agents/agent-email-assistant/agents.py::_card_files::items[:cap]':
        "A confirm card's file line. It ends with '+N more', and the files are the member's own.",
    'apps/agents/agent-email-assistant/agents.py::_card_targets::addrs[:cap]':
        "A confirm card's address line. It ends with '+N more'. The act itself takes every address.",
    'apps/skills/skill-my-tasks/skill_my_tasks/core.py::_query_words::list(dict.fromkeys(query.lower().split()))[:8]':
        "The gateway's word rule: at most 8 query words count. Words, not rows.",
    'apps/skills/skill-projects/skill_projects/choices.py::close_rows::scored[:NAME_CANDIDATES_MAX]':
        'The options of one typed question. A question card holds a few choices, not a read.',
    'apps/skills/skill-projects/skill_projects/choices.py::twin_pairs::pairs[:TWIN_PAIRS_MAX]':
        'The options of one typed question. Not a read.',
    'apps/skills/skill-projects/skill_projects/choices.py::twin_pairs::scored[:TWINS_PER_TITLE]':
        'The options of one typed question. Not a read.',
    'apps/skills/skill-projects/skill_projects/guarded.py::_spread::others[:index]':
        'A splice: others[index:] follows on the next line. Nothing is cut.',
    'apps/skills/skill-projects/skill_projects/writes.py::_resolve_assignee_name::found[:8]':
        "The names in an 'ask which' refusal. people_for lists every match.",
    'packages/acb_skills/acb_skills/error_tools.py::get_errors::errors[:50]':
        "Lint output. It prints '... and N more issues', and a rerun after the fix shows the rest.",
    'packages/acb_skills/acb_skills/error_tools.py::get_errors::sorted(targets)[:20]':
        'Which files a no-argument lint run checks. filePaths names any file.',
    'apps/agents/agent-email-assistant/agents.py::_fmt_digest_sections::(items or [])[:5]':
        'A digest. Each section prints its count, and query_inbox pages the mail.',
    'apps/agents/agent-email-assistant/agents.py::_one::(items or [])[:5]':
        'A digest. Each section prints its count, and query_inbox pages the mail.',
    "apps/agents/agent-email-assistant/agents.py::get_account_overview::overview.get('top_senders', [])[:5]":
        "A 'top 5 senders' summary. list_senders pages every sender.",
    'apps/skills/skill-my-tasks/skill_my_tasks/core.py::_fmt_plan::evicted[:5]':
        "A plan receipt. It prints the count and '…', and my_tasks_list(view='next') lists the tasks.",
    'apps/skills/skill-my-tasks/skill_my_tasks/core.py::_fmt_plan::unplaced[:5]':
        "A plan receipt. It prints the count and '…', and my_tasks_list(view='next') lists the tasks.",
    'apps/skills/skill-projects/skill_projects/reads.py::_hygiene_groups::members[:HYGIENE_ROWS_PER_KIND]':
        "A rendered report section. It prints '(and N more)', and list_tasks pages the tasks.",
    'apps/skills/skill-projects/skill_projects/reads.py::_report_section::rows[:25]':
        "A rendered report section. It prints '(and N more rows)', and the section's own read pages.",
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::(rows if isinstance(rows, list) else [])[:25]':
        'A chat card. The text the model reads carries the count.',
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::[(k, _human(k)) for k in rows[0] if not isinstance(rows[0][k], dict | list)][:6]':
        'The columns of a chat card. Not rows.',
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::rows[:25]':
        'A chat card. The text the model reads carries the count.',
    "apps/skills/skill-projects/skill_projects/views.py::status_report::[p for p in people if int(p.get('open_tasks') or 0)][:10]":
        'A written status report. analytics_load pages every person.',
    'apps/skills/skill-projects/skill_projects/views.py::status_report::blocked_rows[:20]':
        'A written status report. It prints blocked_total, and analytics_stuck reads the rows.',
    'packages/acb_skills/acb_skills/github_tools.py::github_repo_search::items[:10]':
        "GitHub, not company data. It prints GitHub's total_count.",
    'packages/acb_skills/acb_skills/github_tools.py::github_repo_search::text_matches[:2]':
        'Snippets inside one hit. Not rows.',
    'packages/acb_skills/acb_skills/github_tools.py::github_search::items[:max_results]':
        "GitHub, not company data. It prints GitHub's total_count, and maxResults asks for more.",
    'packages/acb_skills/acb_skills/github_tools.py::github_search::text_matches[:3]':
        'Snippets inside one hit. Not rows.',
    "packages/acb_skills/acb_skills/web_tools.py::_serpapi_search::(data.get('organic_results') or [])[:max_results]":
        'The web, not company data. max_results asks for more.',
    'apps/agents/agent-email-assistant/agents.py::search_emails::emails[:10]':
        "Not a tool: it is not in _TOOLS, so no model calls it. It prints '… and N more'.",
    'packages/acb_skills/acb_skills/agent_tools.py::call_agents_parallel::task_list[:5]':
        'Silent cut. The second PR of the agent-read-paging change fixes it.',
    "packages/acb_skills/acb_skills/attachment_tools.py::_listing::[n for n, kind in entries if kind == 'file' and (not n.startswith('.'))][:50]":
        'Silent cut. The second PR of the agent-read-paging change fixes it.',
    'apps/services/orchestrator/orchestrator/agents.py::spawn_copilot_agent::run_id[:8]':
        'Text, not rows: the cut shortens one string.',
    'apps/skills/skill-my-tasks/skill_my_tasks/core.py::my_tasks_people::summary[:160]':
        'Text, not rows: the cut shortens one string.',
    "apps/skills/skill-my-tasks/skill_my_tasks/core.py::my_tasks_people::(p.get('skills') or [])[:6]":
        "Six skills of one person, inside that person's line. Not rows.",
    'apps/skills/skill-projects/skill_projects/reads.py::_fenced::flat[:DATASET_CELL]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_agent_memory::agent[:30]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_agent_memory::fact[:80]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_episode::name[:60]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_memory::fact[:80]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_memory::user_id[:20]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/memory_tools.py::save_org_memory::fact[:80]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/sandbox_tools.py::_format::saved[:20]':
        "A run receipt. It prints the count and '…', and the files are in outputs/.",
}

#: The size of :data:`ALLOWED`. Raising it is a deliberate edit of this file.
ALLOWED_CEILING = 50


def _stringy(node: ast.AST) -> bool:
    """True when the sliced value is plainly text, not a list of rows."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in ("str", "repr")
    if isinstance(node, ast.BoolOp):
        return _stringy(node.values[-1])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in ("strip", "lower", "upper", "replace", "dumps",
                              "isoformat", "decode", "read_text"):
            return True
        return node.func.attr == "get" and len(node.args) == 2 and _stringy(node.args[1])
    return False


def _head_cut(node: ast.AST) -> bool:
    """``x[:N]`` on something that is not plainly text. ``x[:-1]`` drops the
    last item by construction ("a, b and c"), so it is never a page cut."""
    if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
        return False
    upper = node.slice.upper
    if isinstance(upper, ast.UnaryOp) and isinstance(upper.op, ast.USub):
        return False
    return (node.slice.lower is None and upper is not None
            and node.slice.step is None and not _stringy(node.value))


def _cuts(path: Path) -> set[str]:
    rel = path.relative_to(REPO).as_posix()
    return {f"{rel}::{hit}" for hit in _cuts_in_source(path.read_text(encoding="utf-8-sig"))}


def _cuts_in_source(source: str) -> set[str]:
    """``"<function>::<slice source>"`` for each head slice that a loop, a
    comprehension, an assignment, a return or a call takes."""
    tree = ast.parse(source)
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                sources = [node.iter]
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.Return):
                sources = [node.value] if node.value is not None else []
            elif isinstance(node, ast.Call):
                # `Page(rows[:30], ...)` or `", ".join(lines[:52])`: a cut
                # handed straight to a call is the same cut.
                sources = [*node.args, *(k.value for k in node.keywords)]
            else:
                continue
            found |= {f"{fn.name}::{ast.unparse(s)}" for s in sources if _head_cut(s)}
    return found


def _all_cuts() -> set[str]:
    out: set[str] = set()
    for pattern in TOOL_MODULES:
        for path in sorted(REPO.glob(pattern)):
            out |= _cuts(path)
    return out


def test_the_scan_reads_the_tool_modules() -> None:
    """A glob that matches nothing would make the fence pass on nothing."""
    for pattern in TOOL_MODULES:
        assert list(REPO.glob(pattern)), pattern


def test_no_tool_module_cuts_a_list_without_the_paging_helper() -> None:
    new = sorted(_all_cuts() - set(ALLOWED))
    assert not new, (
        "a head slice on a list in a tool module. Use acb_common.paging.paged, "
        "so the model reads the total and the next page, or add the line to "
        "ALLOWED with the reason no row is lost:\n" + "\n".join(new))


def test_the_allowlist_only_shrinks() -> None:
    stale = sorted(set(ALLOWED) - _all_cuts())
    assert not stale, "these cuts are gone, so delete them from ALLOWED:\n" + "\n".join(stale)
    assert len(ALLOWED) <= ALLOWED_CEILING
    assert all(reason.strip() for reason in ALLOWED.values())


def test_the_fence_catches_the_shape_that_hid_items_31_to_35(tmp_path: Path) -> None:
    """The fence is live: the 2026-10-14 line, written fresh, is a hit."""
    src = tmp_path / "probe.py"
    src.write_text("async def tool(items):\n    shown = _ordered(items)[:30]\n"
                   "    for s in senders[:20]:\n        pass\n"
                   "    return str(x)[:10]\n", encoding="utf-8")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    hits = [ast.unparse(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and _head_cut(n.value)]
    hits += [ast.unparse(n.iter) for n in ast.walk(tree)
             if isinstance(n, ast.For) and _head_cut(n.iter)]
    hits += [ast.unparse(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Return) and _head_cut(n.value)]
    assert hits == ["_ordered(items)[:30]", "senders[:20]"]
    assert _cuts_in_source("def t(rows):\n    return Page(rows[:30], 1, 0, '')\n") == {
        "t::rows[:30]"}
    assert _cuts_in_source("def t(n):\n    return ', '.join(n[:-1])\n") == set()


# ── 3. My Tasks ─────────────────────────────────────────────────────────────

core = pytest.importorskip("skill_my_tasks.core", reason="skill-my-tasks not installed")

from tests.unit.test_skill_task_lens import ME, TASK, Recorder  # noqa: E402


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def gw(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(core, "_request", rec)
    monkeypatch.setattr(core, "_current_user_email", lambda: ME)
    return rec


def _inbox(n: int) -> list[dict[str, Any]]:
    """``n`` inbox rows, hand-ranked 1..n so the order is known."""
    return [{**TASK, "id": f"{k:032x}", "title": f"item {k}", "sort_key": float(k),
             "disposition": "INBOX"} for k in range(1, n + 1)]


def _ids(out: str) -> list[str]:
    return [ln.split("full_id: ")[1] for ln in out.splitlines() if "full_id:" in ln]


def test_my_tasks_list_page_two_holds_items_31_to_35(gw: Recorder) -> None:
    gw.inbox_pages = [{"rows": _inbox(35), "total": 35}]
    out = run(core.my_tasks_list(view="inbox", offset=30))
    assert _ids(out) == [f"{k:032x}" for k in range(31, 36)]
    assert "Showing 31-35 of 35. This is the last page." in out


def test_my_tasks_list_first_page_says_the_total_and_the_next_offset(gw: Recorder) -> None:
    gw.inbox_pages = [{"rows": _inbox(35), "total": 35}]
    out = run(core.my_tasks_list(view="inbox"))
    assert len(_ids(out)) == 30
    assert out.startswith("35 item(s) in inbox")
    assert out.endswith("Showing 1-30 of 35. Call again with offset=30 for the rest.")


def test_my_tasks_list_offset_past_the_end_is_a_clear_empty_page(gw: Recorder) -> None:
    gw.inbox_pages = [{"rows": _inbox(35), "total": 35}]
    out = run(core.my_tasks_list(view="inbox", offset=40))
    assert _ids(out) == []
    assert "There are 35 items" in out


def test_my_tasks_list_takes_a_bigger_page_up_to_the_bound(gw: Recorder) -> None:
    gw.inbox_pages = [{"rows": _inbox(35), "total": 35}]
    out = run(core.my_tasks_list(view="inbox", limit=500))
    assert len(_ids(out)) == 35
    assert "Showing" not in out


def _areas_and_nodes(gw: Recorder, areas: int, nodes: int) -> None:
    base = gw.answer

    def answer(method: str, path: str, kw: dict[str, Any]) -> Any:
        if path == "/projects/my/areas":
            return {"rows": [{"id": f"a{k}", "name": f"Area {k}", "open_tasks": 0}
                             for k in range(areas)], "total": areas}
        if path == "/projects/nodes":
            return {"rows": [{"id": f"n{k}", "name": f"Proj {k}", "kind": "project"}
                             for k in range(nodes)], "total": nodes}
        return base(method, path, kw)

    gw.answer = answer  # type: ignore[method-assign]


def test_my_tasks_list_projects_reaches_every_project(gw: Recorder) -> None:
    _areas_and_nodes(gw, 60, 60)
    first = run(core.my_tasks_list_projects())
    assert first.count("[AREA]") == 60 and first.count("[PROJECT]") == 40
    assert first.endswith("Showing 1-100 of 120. Call again with offset=100 for the rest.")
    rest = run(core.my_tasks_list_projects(offset=100))
    assert rest.count("[PROJECT]") == 20 and "[AREA]" not in rest
    assert "id=n59" in rest
    assert "60 company project(s)" in rest
    assert "This is the last page." in rest


def test_my_tasks_list_projects_offset_past_the_end(gw: Recorder) -> None:
    _areas_and_nodes(gw, 2, 3)
    assert "There are 5 items" in run(core.my_tasks_list_projects(offset=9))


def test_my_tasks_people_pages_past_25(gw: Recorder) -> None:
    base = gw.answer

    def answer(method: str, path: str, kw: dict[str, Any]) -> Any:
        if path == "/tasks/people":
            return [{"name": f"P{k}", "role": "r", "department": "d"} for k in range(30)]
        return base(method, path, kw)

    gw.answer = answer  # type: ignore[method-assign]
    first = run(core.my_tasks_people())
    assert "Showing 1-25 of 30. Call again with offset=25" in first
    rest = run(core.my_tasks_people(offset=25))
    assert "«P29»" in rest and "«P0»" not in rest
    assert "There are 30 people" in run(core.my_tasks_people(offset=30))


def test_my_tasks_subtasks_reads_past_the_first_hundred(gw: Recorder) -> None:
    base = gw.answer
    steps = [{"id": f"s{k}", "title": f"step {k}", "completed_at": None} for k in range(150)]

    def answer(method: str, path: str, kw: dict[str, Any]) -> Any:
        if path == "/projects/tasks" and method == "GET":
            page = int(kw["params"]["page"])
            return {"rows": steps[(page - 1) * 100: page * 100], "total": 150}
        return base(method, path, kw)

    gw.answer = answer  # type: ignore[method-assign]
    first = run(core.my_tasks_subtasks("x"))
    assert first.startswith("150 subtask(s)")
    assert "Showing 1-100 of 150. Call again with offset=100" in first
    rest = run(core.my_tasks_subtasks("x", offset=100))
    assert "(id: s149)" in rest and "(id: s0)" not in rest


def test_my_tasks_detail_pages_the_comment_thread(gw: Recorder) -> None:
    base = gw.answer
    seen: list[dict[str, Any]] = []

    def answer(method: str, path: str, kw: dict[str, Any]) -> Any:
        if path.endswith("/timeline"):
            seen.append(dict(kw["params"]))
            page = int(kw["params"]["page"])
            size = int(kw["params"]["page_size"])
            rows = [{"created_by": "bob@fracktal.in", "body": f"c{k}"} for k in range(12)]
            return {"rows": rows[(page - 1) * size: page * size], "total": 12}
        return base(method, path, kw)

    gw.answer = answer  # type: ignore[method-assign]
    first = run(core.my_tasks_detail(TASK["id"]))
    assert "Showing 1-5 of 12. Call again with comments_offset=5" in first
    second = run(core.my_tasks_detail(TASK["id"], comments_offset=5))
    assert seen[-1]["page"] == 2 and seen[-1]["page_size"] == 5
    assert "«c5»" in second and "«c0»" not in second
    last = run(core.my_tasks_detail(TASK["id"], comments_offset=10))
    assert "«c11»" in last and "This is the last page." in last


def test_my_tasks_day_digest_names_the_blocks_it_left_out(gw: Recorder) -> None:
    base = gw.answer

    def answer(method: str, path: str, kw: dict[str, Any]) -> Any:
        if path == "/tasks/calendar/day-summary":
            return {"day": "today", "scheduled": [
                {"title": f"b{k}", "start": "2026-10-14T09:00", "end": "2026-10-14T10:00"}
                for k in range(10)]}
        return base(method, path, kw)

    gw.answer = answer  # type: ignore[method-assign]
    out = run(core.my_tasks_day_digest())
    assert "10 block(s) still to do today" in out
    assert "2 more block(s): my_tasks_list_schedule lists them all." in out


# ── 4. Projects ─────────────────────────────────────────────────────────────

skill_projects = pytest.importorskip("skill_projects", reason="skill-projects not installed")

from tests.unit._projects_agent_fakes import empty_list, fake_gateway  # noqa: E402

UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _task_rows(start: int, n: int, roots: int = 1) -> list[dict[str, Any]]:
    return [{"id": f"{k:08x}-d9cb-469f-a165-70867728950e", "title": f"t{k}",
             "task_number": k, "status_id": f"s{k % roots}",
             "root_project_id": f"{k % roots:08x}-d9cb-469f-a165-70867728950e",
             "assignees": []} for k in range(start, start + n)]


def test_list_tasks_says_a_short_last_page_is_the_last(monkeypatch) -> None:
    """60 tasks, 25 a page: page 3 holds 10. The old test (10 x 3 < 60) said
    "page 4 has more", and page 4 is empty."""
    fake_gateway(monkeypatch, lambda c: (
        {"rows": _task_rows(50, 10), "total": 60} if c["path"] == "/projects/tasks"
        else empty_list(c)))
    out = asyncio.run(skill_projects.list_tasks(page=3, page_size=25))
    assert "Showing 51-60 of 60. This is the last page." in out
    assert "page 4" not in out


def test_list_tasks_names_the_next_page(monkeypatch) -> None:
    fake_gateway(monkeypatch, lambda c: (
        {"rows": _task_rows(25, 25), "total": 60} if c["path"] == "/projects/tasks"
        else empty_list(c)))
    out = asyncio.run(skill_projects.list_tasks(page=2, page_size=25))
    assert "Showing 26-50 of 60. Call again with page=3 for the rest." in out


def test_list_tasks_names_the_status_of_every_root_on_the_page(monkeypatch) -> None:
    """Seven roots on one page. The old read named the statuses of five."""
    def responder(call: dict) -> Any:
        if call["path"] == "/projects/tasks":
            return {"rows": _task_rows(0, 7, roots=7), "total": 7}
        if call["path"].endswith("/statuses"):
            k = int(call["path"].split("/")[3][:8], 16)
            return {"rows": [{"id": f"s{k}", "name": f"Lane {k}", "category": "todo"}]}
        return empty_list(call)

    fake_gateway(monkeypatch, responder)
    out = asyncio.run(skill_projects.list_tasks())
    assert all(f"status «Lane {k}»" in out for k in range(7))


def test_analytics_load_pages_past_twenty_people(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, lambda c: (
        {"people": [{"assignee": f"p{k}@x.io", "open_tasks": 1} for k in range(20, 25)],
         "people_listed": 25, "people_total": 25, "total_tasks": 25}
        if c["path"] == "/projects/analytics/load" else empty_list(c)))
    out = asyncio.run(skill_projects.analytics_load(offset=20))
    assert calls[-1]["params"]["people_offset"] == 20
    assert "«p24@x.io»" in out
    assert "Showing 21-25 of 25. This is the last page." in out


def test_intake_queue_pages_and_names_the_next_offset(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, lambda c: (
        {"rows": _task_rows(0, 50), "total": 70} if c["path"] == "/projects/intake"
        else empty_list(c)))
    out = asyncio.run(skill_projects.intake_queue())
    assert "Showing 1-50 of 70. Call again with offset=50 for the rest." in out
    asyncio.run(skill_projects.intake_queue(offset=50))
    assert calls[-1]["params"]["page"] == 2


def test_notifications_say_a_full_page_has_a_next(monkeypatch) -> None:
    rows = [{"id": str(k), "task_number": k, "task_title": "t", "kind": "mention",
             "actor": "a", "task_id": UUID} for k in range(50)]
    calls = fake_gateway(monkeypatch, lambda c: (
        {"rows": rows, "total": 50, "unread": {"total": 3, "mentions": 1}}
        if c["path"] == "/projects/notifications" else empty_list(c)))
    out = asyncio.run(skill_projects.notifications(unread_only=False))
    assert "Call again with offset=50 for the rest." in out
    asyncio.run(skill_projects.notifications(unread_only=False, offset=50))
    assert calls[-1]["params"]["page"] == 2


def test_find_tasks_names_the_tool_that_pages_past_fifty(monkeypatch) -> None:
    fake_gateway(monkeypatch, lambda c: (
        {"rows": _task_rows(0, 10), "total": 10, "truncated": True}
        if c["path"] == "/projects/search" else empty_list(c)))
    out = asyncio.run(skill_projects.find_tasks("vendor"))
    assert "list_tasks with the same query pages through all of them" in out


def test_people_for_says_when_the_route_cap_was_hit(monkeypatch) -> None:
    fake_gateway(monkeypatch, lambda c: (
        {"people": [{"name": f"P{k}", "assignee": f"p{k}@x.io"} for k in range(8)],
         "agents": []} if c["path"] == "/projects/assignees" else empty_list(c)))
    out = asyncio.run(skill_projects.people_for(query="p"))
    assert "At most 8 people match one query" in out


def test_report_list_says_when_it_holds_only_the_newest(monkeypatch) -> None:
    fake_gateway(monkeypatch, lambda c: (
        {"reports": [{"id": str(k), "name": f"r{k}"} for k in range(200)]}
        if c["path"] == "/projects/reports" else empty_list(c)))
    out = asyncio.run(skill_projects.report_list())
    assert "These are the newest 200 reports. Older ones exist." in out


def test_the_load_route_pages_its_people(monkeypatch) -> None:
    """``people_offset`` moves the window of MAX_PEOPLE, and ``people_listed``
    counts what it pages over. No offset is the first page, as the panel
    reads it."""
    import contextlib

    analytics = pytest.importorskip("gateway.routes.projects.analytics")

    @contextlib.asynccontextmanager
    async def session():
        yield object()

    async def visibility(_db, _user):
        return object()

    async def body(*_a, **_kw):
        return {"people": [{"assignee": f"p{k}"} for k in range(25)],
                "people_total": 25, "total_tasks": 25}

    async def everyone(*_a):
        return None

    monkeypatch.setattr(analytics, "_tenant_session", session)
    monkeypatch.setattr(analytics, "resolve_visibility", visibility)
    monkeypatch.setattr(analytics, "load_body", body)
    monkeypatch.setattr(analytics, "reportable_people", everyone)
    first = asyncio.run(analytics.load(user=object()))
    assert [p["assignee"] for p in first["people"]] == [f"p{k}" for k in range(20)]
    assert first["people_listed"] == 25
    rest = asyncio.run(analytics.load(people_offset=20, user=object()))
    assert [p["assignee"] for p in rest["people"]] == [f"p{k}" for k in range(20, 25)]


# ── 5. The prefix helper (a backend with a LIMIT and no OFFSET) ─────────────

def test_a_prefix_page_asks_for_one_more_row_than_it_shows() -> None:
    from acb_common.paging import prefix_limit, prefix_page

    assert prefix_limit(0, 15, default=15, cap=200) == (0, 15, 16)
    assert prefix_limit(190, 15, default=15, cap=200) == (190, 15, 200)
    page = prefix_page(list(range(16)), 0, 15, cap=200, asked=16)
    assert page.items == list(range(15))
    assert page.line == "Showing 1-15. More items exist. Call again with offset=15 for the rest."
    # Fewer rows than asked for: the backend sent them all, so the total is known.
    last = prefix_page(list(range(20)), 15, 15, cap=200, asked=31)
    assert last.items == [15, 16, 17, 18, 19]
    assert last.line == "Showing 16-20 of 20. This is the last page."
    assert "There are 20 items" in prefix_page(list(range(20)), 40, 15, cap=200, asked=56).line


def test_a_prefix_page_at_the_backend_cap_says_so() -> None:
    from acb_common.paging import prefix_page

    page = prefix_page(list(range(200)), 190, 15, cap=200)
    assert page.items == list(range(190, 200))
    assert "The source returns at most 200 items. Narrow the filter" in page.line


# ── 6. Email ────────────────────────────────────────────────────────────────

def _email_agent():
    import importlib.util

    path = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"
    spec = importlib.util.spec_from_file_location("ea_paging", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ea():
    return _email_agent()


def _gw(monkeypatch, mod, answer) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(path, params=None):
        seen.append((path, dict(params or {})))
        return answer(path, dict(params or {}))

    monkeypatch.setattr(mod, "_get", fake_get)
    return seen


def _messages(n: int) -> list[dict[str, Any]]:
    return [{"id": f"m{k}", "subject": f"s{k}", "thread_id": "t1", "account_id": "a1",
             "from_address": {"email": f"x{k}@y.io"}, "received_at": "2026-10-01"}
            for k in range(1, n + 1)]


def _paged_messages(rows):
    def answer(path, params):
        size = int(params.get("page_size") or 50)
        page = int(params.get("page") or 1)
        return {"emails": rows[(page - 1) * size: page * size], "total": len(rows)}
    return answer


def test_query_inbox_page_two_holds_items_31_to_35(monkeypatch, ea) -> None:
    seen = _gw(monkeypatch, ea, _paged_messages(_messages(35)))
    out = asyncio.run(ea.query_inbox("a1", limit=30, offset=30))
    assert seen[-1][1]["page"] == "2"
    assert "id=m31 " in out and "id=m35 " in out and "id=m30 " not in out
    assert "Showing 31-35 of 35. This is the last page." in out
    first = asyncio.run(ea.query_inbox("a1", limit=30))
    assert "Showing 1-30 of 35. Call again with offset=30 for the rest." in first


def test_read_thread_pages_the_newest_messages_in(monkeypatch, ea) -> None:
    """The thread is oldest first. The old read showed the oldest 25 of at
    most 50, and called the dropped NEWEST ones 'earlier'."""
    _gw(monkeypatch, ea, _paged_messages(_messages(60)))
    first = asyncio.run(ea.read_thread(thread_id="t1"))
    assert "60 message(s)" in first
    assert "Showing 1-25 of 60. Call again with offset=25 for the rest." in first
    last = asyncio.run(ea.read_thread(thread_id="t1", offset=50))
    assert "[51]" in last and "[60]" in last and "x60@y.io" in last
    assert "This is the last page." in last


def test_find_urgent_names_the_total(monkeypatch, ea) -> None:
    _gw(monkeypatch, ea, _paged_messages(_messages(14)))
    out = asyncio.run(ea.find_urgent("a1"))
    assert "Showing 1-10 of 14. Call again with offset=10 for the rest." in out
    assert "id=m14 " in asyncio.run(ea.find_urgent("a1", offset=10))


def test_find_needs_reply_pages_past_fifteen(monkeypatch, ea) -> None:
    rows = [{"message_id": f"m{k}", "from": "x", "subject": f"s{k}"} for k in range(1, 36)]
    seen = _gw(monkeypatch, ea, lambda p, q: {"threads": rows[: int(q["limit"])]})
    first = asyncio.run(ea.find_priority("a1"))
    assert "Call again with offset=15 for the rest." in first
    last = asyncio.run(ea.find_priority("a1", offset=30))
    assert seen[-1][1]["limit"] == "46"
    assert "id=m31 " in last and "id=m35 " in last and "This is the last page." in last


def test_important_emails_page(monkeypatch, ea) -> None:
    rows = [{"message_id": f"m{k}", "from": "x", "subject": "s", "reason": "r"}
            for k in range(1, 26)]
    _gw(monkeypatch, ea, lambda p, q: {"emails": rows[: int(q["limit"])]})
    first = asyncio.run(ea.find_priority("a1", kind="important"))
    assert "Call again with offset=20 for the rest." in first
    assert "id=m25 " in asyncio.run(ea.find_priority("a1", kind="important", offset=20))


def test_list_senders_pages_through_the_route(monkeypatch, ea) -> None:
    rows = [{"email": f"s{k}@x.io", "count": 1} for k in range(1, 36)]
    seen = _gw(monkeypatch, ea, lambda p, q: {
        "senders": rows[int(q.get("offset") or 0): int(q.get("offset") or 0) + int(q["limit"])],
        "total": 35})
    first = asyncio.run(ea.list_senders("a1", limit=30))
    assert "Showing 1-30 of 35. Call again with offset=30 for the rest." in first
    rest = asyncio.run(ea.list_senders("a1", limit=30, offset=30))
    assert seen[-1][1]["offset"] == "30"
    assert "s35@x.io" in rest and "s30@x.io" not in rest


def test_cold_senders_rule_history_patterns_and_files_page(monkeypatch, ea) -> None:
    cold = [{"from_email": f"c{k}@x.io", "status": "COLD"} for k in range(1, 36)]
    hist = [{"id": f"h{k}", "status": "APPLIED", "rule_name": "r", "actions": []}
            for k in range(1, 36)]
    pats = [{"id": f"p{k}", "pattern": "x"} for k in range(1, 36)]
    arts = [{"path": f"f{k}.pdf", "size": 1} for k in range(1, 36)]

    def answer(path, q):
        return {"/email/cold-senders": {"cold_senders": cold},
                "/email/rules/history": {"history": hist[: int(q.get("limit") or 100)]},
                "/email/learned-patterns": {"patterns": pats},
                "/email/rules/patterns": {"patterns": pats},
                "/agent/artifacts": {"artifacts": arts}}[path]

    _gw(monkeypatch, ea, answer)
    assert "c35@x.io" in asyncio.run(ea.list_senders("a1", view="cold", offset=30))
    assert "Showing 1-20 of 35" in asyncio.run(ea.list_senders("a1", view="cold"))
    assert "id=h35 " in asyncio.run(ea.list_rule_history("a1", offset=30))
    assert "id=p35 " in asyncio.run(ea.list_patterns("a1", offset=30))
    assert "id=p35 " in asyncio.run(ea.list_patterns("a1", kind="rule", offset=30))
    assert "f35.pdf" in asyncio.run(ea.list_artifacts(offset=30))
    assert "There are 35 files" in asyncio.run(ea.list_artifacts(offset=40))


def test_unsubscribe_candidates_page(monkeypatch, ea) -> None:
    rows = [{"email": f"n{k}@x.io", "count": 3, "read_rate": 0.1} for k in range(1, 16)]
    _gw(monkeypatch, ea, lambda p, q: {"senders": rows, "total": 15})
    first = asyncio.run(ea.list_senders("a1", view="unsubscribe"))
    assert "Showing 1-10 of 15. Call again with offset=10 for the rest." in first
    assert "n15@x.io" in asyncio.run(ea.list_senders("a1", view="unsubscribe", offset=10))


# ── 7. WhatsApp ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def wa():
    import importlib.util

    path = REPO / "apps" / "agents" / "agent-whatsapp-assistant" / "agents.py"
    spec = importlib.util.spec_from_file_location("wa_paging", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_whatsapp_chats_page_past_twenty(monkeypatch, wa) -> None:
    chats = [{"id": f"c{k}", "name": f"Chat {k}"} for k in range(1, 36)]
    seen = _gw(monkeypatch, wa, lambda p, q: chats[: int(q.get("limit") or 50)])
    first = asyncio.run(wa.list_whatsapp_chats())
    assert seen[-1][1]["limit"] == 21
    assert "Call again with offset=20 for the rest." in first
    last = asyncio.run(wa.list_whatsapp_chats(offset=30))
    assert "chat_id=c35)" in last and "chat_id=c30)" not in last
    assert "Showing 31-35 of 35. This is the last page." in last


def test_whatsapp_search_pages_past_fifteen(monkeypatch, wa) -> None:
    msgs = [{"id": f"m{k}", "chat_id": "c1", "body_text": f"b{k}"} for k in range(1, 36)]
    _gw(monkeypatch, wa, lambda p, q: msgs[: int(q.get("limit") or 50)])
    assert "Call again with offset=15" in asyncio.run(wa.search_whatsapp("b"))
    last = asyncio.run(wa.search_whatsapp("b", offset=30))
    assert "message_id=m35)" in last and "This is the last page." in last


def test_whatsapp_chat_reads_back_past_the_newest(monkeypatch, wa) -> None:
    """The route gives the newest `limit`, oldest first. offset counts back
    from the newest, so offset=20 reads the 20 before them."""
    msgs = [{"id": f"m{k}", "direction": "in", "body_text": f"b{k}"} for k in range(1, 36)]

    def answer(path, q):
        n = int(q.get("limit") or 100)
        return msgs[-n:]

    _gw(monkeypatch, wa, answer)
    first = asyncio.run(wa.read_whatsapp_chat("c1"))
    assert "Them: b35" in first and "Them: b16" in first and "Them: b15" not in first
    assert "Call again with offset=20 for the rest." in first
    older = asyncio.run(wa.read_whatsapp_chat("c1", offset=20))
    assert "Them: b15" in older and "Them: b1\n" in older + "\n" and "Them: b16" not in older
    assert older.index("Them: b1\n") < older.index("Them: b15")
    assert "This is the last page." in older


def test_whatsapp_context_prints_what_the_route_sent(monkeypatch, wa) -> None:
    ctx = {"contact": {"display_name": "Ann"},
           "open_loops": [{"title": f"loop {k}"} for k in range(10)],
           "waiting_on": [{"text": f"owes {k}", "id": f"w{k}"} for k in range(5)]}
    _gw(monkeypatch, wa, lambda p, q: ctx)
    out = asyncio.run(wa.whatsapp_chat_context("c1"))
    assert "loop 9" in out and "owes 4" in out
    assert "That is the first 10 open loops" in out
    assert "whatsapp_waiting_on lists every open promise" in out


# ── 8. CRM ──────────────────────────────────────────────────────────────────

crm_tools = pytest.importorskip("acb_skills.crm_tools")


def test_crm_list_modules_pages_past_fifty(monkeypatch) -> None:
    mods = [{"label": f"M{k}", "api_name": f"m{k}", "count": 1} for k in range(1, 61)]
    _gw(monkeypatch, crm_tools, lambda p, q: mods)
    first = asyncio.run(crm_tools.crm_list_modules())
    assert "Showing 1-50 of 60. Call again with page=2 for the rest." in first
    last = asyncio.run(crm_tools.crm_list_modules(page=2))
    assert "api_name m60" in last and "api_name m50)" not in last
    assert "This is the last page." in last


def test_crm_report_pages_its_lines_and_names_the_omitted_owners(monkeypatch) -> None:
    owners = [{"owner_email": f"o{k}@x.io", "open_deals": 1, "weighted": 1.0,
               "won_amount": 1.0} for k in range(1, 61)]
    _gw(monkeypatch, crm_tools, lambda p, q: {"owners": owners, "omitted": 4})
    first = asyncio.run(crm_tools.crm_report("owners"))
    assert "Call again with page=2 for the rest." in first
    assert "4 more owners are not in the report." in first
    assert "o60@x.io" in asyncio.run(crm_tools.crm_report("owners", page=2))


def test_crm_fields_print_every_option_and_page_the_fields(monkeypatch) -> None:
    defs = [{"label": f"F{k}", "key": f"cf_{k}", "type": "select",
             "options": [{"label": f"opt{j}"} for j in range(25)]} for k in range(1, 61)]
    _gw(monkeypatch, crm_tools, lambda p, q: defs)
    first = asyncio.run(crm_tools.crm_list_fields("deal"))
    assert "opt24" in first
    assert "Showing 1-50 of 60. Call again with page=2 for the rest." in first
    assert "(key cf_60," in asyncio.run(crm_tools.crm_list_fields("deal", page=2))


def test_crm_deal_contacts_page(monkeypatch) -> None:
    rows = [{"contact": {"id": f"c{k}", "first_name": f"N{k}"}} for k in range(1, 61)]
    _gw(monkeypatch, crm_tools, lambda p, q: {"rows": rows})
    deal = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert "Call again with page=2" in asyncio.run(crm_tools.crm_deal_contacts(deal))
    assert "(id=c60)" in asyncio.run(crm_tools.crm_deal_contacts(deal, page=2))


def test_crm_module_record_pages_its_fields(monkeypatch) -> None:
    rid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    defs = [{"key": f"f{k}", "label": f"Field {k}", "type": "text"} for k in range(1, 61)]
    record = {"record": {"id": rid, "name": "R", "fields": {f"f{k}": f"v{k}" for k in range(1, 61)}},
              "field_defs": defs}
    _gw(monkeypatch, crm_tools, lambda p, q: record)
    first = asyncio.run(crm_tools.crm_get_module_record("Products", rid))
    assert "Call again with page=2 for the rest." in first
    assert "Field 60: v60" in asyncio.run(crm_tools.crm_get_module_record("Products", rid, page=2))


def test_crm_owners_say_when_the_route_cap_is_hit(monkeypatch) -> None:
    owners = [{"email": f"o{k}@x.io", "member": True} for k in range(500)]
    _gw(monkeypatch, crm_tools, lambda p, q: {"owners": owners})
    last = asyncio.run(crm_tools.crm_list_owners(page=10))
    assert "The source returns at most 500 owners." in last


def test_crm_more_line_is_the_shared_hint(monkeypatch) -> None:
    rows = [{"id": f"r{k}", "name": f"Deal {k}"} for k in range(50)]
    _gw(monkeypatch, crm_tools, lambda p, q: {"rows": rows, "total": 120})
    out = asyncio.run(crm_tools.crm_list_records("deals", page_size=50))
    assert out.rstrip().endswith("Showing 1-50 of 120. Call again with page=2 for the rest.")


@pytest.fixture(scope="module")
def crm_agent():
    from tests.unit._crm_agent_fakes import load_agent_module

    return load_agent_module()


def test_crm_timeline_pages_back_in_time(monkeypatch, crm_agent) -> None:
    entries = [{"kind": "activity", "at": f"t{k}", "activity": {"type": "note", "subject": f"s{k}"}}
               for k in range(1, 36)]
    seen = _gw(monkeypatch, crm_agent, lambda p, q: {"entries": entries[: int(q["limit"])]})
    rid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    first = asyncio.run(crm_agent.get_timeline("deal", rid))
    assert seen[-1][1]["limit"] == 21
    assert "Call again with offset=20 for the rest." in first
    last = asyncio.run(crm_agent.get_timeline("deal", rid, offset=30))
    assert "s35" in last and "Showing 31-35 of 35. This is the last page." in last
