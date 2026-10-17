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
import json
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
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::(rows if isinstance(rows, list) else [])[:rows_max]':
        'A chat card (25 rows). The text the model reads carries the count. The kept '
        'report (§14.10.9) passes a rows_max above every list and cuts with paged.',
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::[(k, _human(k)) for k in rows[0] if not isinstance(rows[0][k], dict | list)][:6]':
        'The columns of a chat card. Not rows.',
    'apps/skills/skill-projects/skill_projects/views.py::_card_section::rows[:rows_max]':
        'A chat card (25 rows). The text the model reads carries the count. The kept '
        'report (§14.10.9) passes a rows_max above every list and cuts with paged.',
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
    'apps/services/orchestrator/orchestrator/agents.py::spawn_copilot_agent::run_id[:8]':
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
    'apps/agents/agent-email-assistant/agents.py::_instructions::text[:start]':
        "Text: the agent's own instructions, split at a marker. Not rows.",
    'apps/agents/agent-whatsapp-assistant/agents.py::_instructions::text[:start]':
        "Text: the agent's own instructions, split at a marker. Not rows.",
    'apps/services/orchestrator/orchestrator/agents.py::_build_memory_block::user_query[:80]':
        'Text, not rows: the cut shortens one string.',
    'apps/services/orchestrator/orchestrator/agents.py::enrich_instructions_with_memory::user_query[:80]':
        'Text, not rows: the cut shortens one string.',
    "apps/skills/skill-projects/skill_projects/forms.py::propose_plan::[r.strip() for r in str(risks or '').splitlines() if r.strip()][:5]":
        'The risk lines the MODEL wrote into its own plan card. Not company data, and the card shows five.',
    'apps/skills/skill-projects/skill_projects/guarded.py::_short_ref::title[:TITLE_CLIP - 1]':
        'Text, not rows: the cut shortens one string.',
    'apps/skills/skill-projects/skill_projects/reads.py::_day::raw[:10]':
        'Text: a date string cut to YYYY-MM-DD.',
    'apps/skills/skill-projects/skill_projects/reads.py::pulse_status_words::text[:1]':
        'Text: the first letter, to capitalise it.',
    'apps/skills/skill-projects/skill_projects/views.py::_human::text[:1]':
        'Text: the first letter, to capitalise it.',
    'apps/skills/skill-projects/skill_projects/views.py::_draw_report::stats[:8]':
        'The stat tiles of a report CARD. The report text the model reads (report_render) prints every section.',
    'packages/acb_skills/acb_skills/code_tools.py::_cap::text[:half]':
        'Text: the head of a long command output. The cut is marked.',
    'packages/acb_skills/acb_skills/history_tools.py::query_history::val[:_CONTENT_CAP]':
        "Text: one message body, cut and marked '...'.",
    'packages/acb_skills/acb_skills/sandbox_tools.py::_long_line::text[:FILE_READ_CHARS]':
        'Text: one line of a sandbox file, longer than the read window. The '
        'answer names its size and how to read the rest.',
    'packages/acb_skills/acb_skills/todo_tools.py::manage_todo_list::title[:120]':
        'Text, not rows: the cut shortens one string.',
    'packages/acb_skills/acb_skills/web_tools.py::fetch_page::text[:max_chars]':
        "Text: one web page, cut and marked '[... truncated'. max_chars asks for more.",
    'apps/agents/agent-email-assistant/agents.py::_inbox_card_parts::rows[:_CARD_MESSAGE_LINES]':
        "A confirm card's message lines (#1019). It ends with '+N more', and the act takes every message.",
    'apps/agents/agent-email-assistant/agents.py::_card_name::\' \'.join(\'\'.join((ch for ch in text if ch not in \'"\\\'`()[]{}\')).split())[:80]':
        'Text: one string with its whitespace collapsed, then cut. A join of words, not of rows.',
    "apps/agents/agent-email-assistant/agents.py::_card_text::' '.join(kept.split())[:limit]":
        'Text: one string with its whitespace collapsed, then cut. A join of words, not of rows.',
    "apps/agents/agent-email-assistant/agents.py::_one_line::' '.join(str(value or '').split())[:limit]":
        'Text: one string with its whitespace collapsed, then cut. A join of words, not of rows.',
}

#: The size of :data:`ALLOWED`. Raising it is a deliberate edit of this file.
#: 48 until the 2026-10-14 review widened the fence to every head slice in a
#: function: 15 entries joined (listed last, each with its reason). Three
#: left, because the scan now reads a slice inside an f-string, and a
#: `" ".join(...)` result, as text. 48 - 3 + 15 = 60. #1019 on main added one
#: confirm-card line (61). The re-review of #1014 stopped exempting a
#: `join(...)[:N]`, so the three email entries that collapse one string's
#: whitespace came back with their reason (64). Part 2 (2026-10-15) added
#: one: the sandbox read cuts a single line that is longer than its window,
#: and says so (65).
ALLOWED_CEILING = 65


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
    """``x[:N]`` or ``x[0:N]`` on something that is not plainly text.

    ``x[:-1]`` drops the last item by construction ("a, b and c"), so it is
    never a page cut."""
    if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
        return False
    lower, upper = node.slice.lower, node.slice.upper
    if isinstance(upper, ast.UnaryOp) and isinstance(upper.op, ast.USub):
        return False
    from_start = lower is None or (isinstance(lower, ast.Constant) and lower.value == 0)
    return (from_start and upper is not None
            and node.slice.step is None and not _stringy(node.value))


def _islice(node: ast.AST) -> bool:
    """``itertools.islice(rows, 30)`` or ``islice(rows, 0, 30)``: a head cut too."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name == "islice"


def _cuts(path: Path) -> set[str]:
    rel = path.relative_to(REPO).as_posix()
    return {f"{rel}::{hit}" for hit in _cuts_in_source(path.read_text(encoding="utf-8-sig"))}


def _formatted_text(fn: ast.AST) -> set[int]:
    """The ids of the slices that format ONE text value inside an f-string.

    Exempt: ``f"{body[:80]}"`` and ``f"{_data(summary[:160])}"``, where the
    slice sits on a Name, an Attribute or a Call and only calls lie between
    it and the f-string. Not exempt (review of #1014): a slice that a
    generator or a comprehension iterates (``{', '.join(r for r in rows[:30])}``),
    and a slice of a ``join`` (``' | '.join(lines)[:4000]`` cuts rows' text)."""
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    exempt: set[int] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
            continue
        value = node.value
        one_item = isinstance(value, ast.Subscript) and not isinstance(value.slice, ast.Slice)
        if not (isinstance(value, ast.Name | ast.Attribute | ast.Call) or one_item):
            continue  # `row["due"][:10]` slices ONE item, so it is text too
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)                 and value.func.attr == "join":
            continue
        up = parent.get(id(node))
        while isinstance(up, ast.Call):
            up = parent.get(id(up))
        if isinstance(up, ast.FormattedValue):
            exempt.add(id(node))
    return exempt


def _cuts_in_source(source: str) -> set[str]:
    """``"<function>::<slice source>"`` for EVERY head slice and every islice
    in a function: in a loop, a literal, an ``+=``, a concatenation, a tuple
    assign or a call. The one exemption is a slice that formats one text
    value inside an f-string (:func:`_formatted_text`)."""
    tree = ast.parse(source)
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        text_nodes = _formatted_text(fn)
        for node in ast.walk(fn):
            if id(node) in text_nodes:
                continue
            if _head_cut(node) or _islice(node):
                found.add(f"{fn.name}::{ast.unparse(node)}")
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


def test_the_fence_catches_the_shape_that_hid_items_31_to_35() -> None:
    """The fence is live: the 2026-10-14 line, written fresh, is a hit."""
    src = ("async def tool(items):\n    shown = _ordered(items)[:30]\n"
           "    for s in senders[:20]:\n        pass\n"
           "    return str(x)[:10]\n")
    assert _cuts_in_source(src) == {"tool::_ordered(items)[:30]", "tool::senders[:20]"}
    assert _cuts_in_source("def t(rows):\n    return Page(rows[:30], 1, 0, '')\n") == {
        "t::rows[:30]"}
    assert _cuts_in_source("def t(n):\n    return ', '.join(n[:-1])\n") == set()
    assert _cuts_in_source("def t(r):\n    return f'{r[:9]}'\n") == set()


@pytest.mark.parametrize(("shape", "hit"), [
    ("return json.dumps({'rows': rows[:30]})", "rows[:30]"),
    ("return rows[0:30]", "rows[0:30]"),
    ("out += rows[:30]", "rows[:30]"),
    ("return ['h'] + rows[:30]", "rows[:30]"),
    ("a, b = rows[:30], 1", "rows[:30]"),
    ("return list(itertools.islice(rows, 30))", "itertools.islice(rows, 30)"),
    # Re-review of #1014: two cuts that hid behind the text exemptions.
    ("return f\"{', '.join(r['t'] for r in rows[:30])}\"", "rows[:30]"),
    ("return ' | '.join(rows)[:4000]", "' | '.join(rows)[:4000]"),
])
def test_the_fence_catches_the_six_shapes_the_review_named(shape: str, hit: str) -> None:
    """Review of #1014: a slice in a literal, a 0 lower bound, an ``+=``, a
    concatenation, a tuple assign, and islice all cut a list too."""
    assert _cuts_in_source(f"def t(rows, out):\n    {shape}\n") == {f"t::{hit}"}


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


def test_analytics_load_takes_a_bad_offset_as_zero(monkeypatch) -> None:
    """Review of #1014: ``bounds()``, never a bare ``int(offset)``."""
    calls = fake_gateway(monkeypatch, lambda c: (
        {"people": [], "people_listed": 0, "total_tasks": 0}
        if c["path"] == "/projects/analytics/load" else empty_list(c)))
    asyncio.run(skill_projects.analytics_load(offset="soon"))
    assert calls[-1]["params"]["people_offset"] == 0
    asyncio.run(skill_projects.analytics_load(offset=-4))
    assert calls[-1]["params"]["people_offset"] == 0


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
    # Review of #1014: never "the last page" beside "the source stops here",
    # and no "narrow the filter" from a tool that has no filter.
    assert page.line == "Showing 191-200. Older items are past the source's limit of 200."
    narrow = prefix_page(list(range(200)), 190, 15, cap=200,
                         narrow="Use more exact words to reach them.")
    assert narrow.line.endswith("limit of 200. Use more exact words to reach them.")


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


def test_unsubscribe_candidates_say_the_scan_stopped_at_200(monkeypatch, ea) -> None:
    """The scan reads the 200 biggest senders. A route that sends no total
    still gets the line, because 200 rows back means the scan was full."""
    rows = [{"email": f"n{k}@x.io", "count": 3, "read_rate": 0.1} for k in range(200)]
    _gw(monkeypatch, ea, lambda p, q: {"senders": rows})
    out = asyncio.run(ea.list_senders("a1", view="unsubscribe"))
    assert "Checked only the 200 biggest senders." in out
    _gw(monkeypatch, ea, lambda p, q: {"senders": rows, "total": 900})
    assert "Checked only the 200 biggest of 900 senders." in asyncio.run(
        ea.list_senders("a1", view="unsubscribe"))


def test_no_unsubscribe_candidate_still_says_the_scan_stopped_at_200(monkeypatch, ea) -> None:
    """Re-review of #1014: "no newsletters" from a scan of the 200 biggest
    senders must say that it read only those."""
    rows = [{"email": f"n{k}@x.io", "count": 3, "read_rate": 0.9} for k in range(200)]
    _gw(monkeypatch, ea, lambda p, q: {"senders": rows, "total": 900})
    out = asyncio.run(ea.list_senders("a1", view="unsubscribe"))
    assert out.startswith("No obvious newsletters")
    assert "Checked only the 200 biggest of 900 senders." in out


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

    def answer(path, _params):
        # CRM-Q31-1: the catalogue is off, so the tool lists /crm/modules.
        if path == "/crm/catalogue":
            raise crm_tools._GatewayError("off", "catalogue_off")
        return mods

    _gw(monkeypatch, crm_tools, answer)
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
    assert "Older owners are past the source's limit of 500." in last
    assert "last page" not in last


def test_crm_more_line_is_the_shared_hint(monkeypatch) -> None:
    rows = [{"id": f"r{k}", "name": f"Deal {k}"} for k in range(50)]
    _gw(monkeypatch, crm_tools, lambda p, q: {"rows": rows, "total": 120})
    out = asyncio.run(crm_tools.crm_list_records("deals", page_size=50))
    assert out.rstrip().endswith("Showing 1-50 of 120. Call again with page=2 for the rest.")


@pytest.fixture(scope="module")
def crm_agent():
    from tests.unit._crm_agent_fakes import load_agent_module

    return load_agent_module()


def _timeline_route(emails: int, notes: int):
    """A fake of ``/crm/{entity}/{id}/timeline`` that keeps the REAL rules.

    The emails are the newest rows, the notes older. Unpaged, the route gives
    email half the page (``_email_budget``), merges and cuts. Paged
    (``offset``), each source reads ``offset + limit + 1`` rows in its own
    order and the real ``merge_page`` cuts one total order. A fake that
    answered ``entries[:limit]`` hid the review's P1: page 2 of a budgeted
    read repeated notes and skipped mail."""
    from datetime import UTC, datetime, timedelta

    from gateway.routes.crm import activities

    base = datetime(2026, 10, 1, tzinfo=UTC)
    mail = [activities.TimelineEntry(
        kind="email_thread", at=(base + timedelta(days=60 - k)).isoformat(),
        email_thread={"message_id": f"e{k:02d}", "subject": f"mail {k}"})
        for k in range(emails)]
    note = [activities.TimelineEntry(
        kind="activity", at=(base + timedelta(days=-k)).isoformat(),
        activity={"id": f"n{k:02d}", "type": "note", "subject": f"note {k}"})
        for k in range(notes)]

    def first(rows, n):
        return sorted(rows, key=activities._entry_key, reverse=True)[:n]

    def answer(path, q):
        limit = int(q["limit"])
        if q.get("offset") is None:
            merged = first(note, limit) + first(mail, activities._email_budget(limit))
            merged.sort(key=lambda e: e.at or "", reverse=True)
            return {"entries": [e.model_dump() for e in merged[:limit]]}
        offset = int(q["offset"])
        want = offset + limit + 1
        page, more = activities.merge_page(first(note, want) + first(mail, want),
                                           offset, limit)
        return {"entries": [e.model_dump() for e in page], "has_more": more}

    return answer


def test_crm_timeline_pages_with_no_repeat_and_no_gap(monkeypatch, crm_agent) -> None:
    """30 newer emails and 30 notes, 20 a page: 3 pages hold all 60 once."""
    pytest.importorskip("gateway.routes.crm.activities")
    seen = _gw(monkeypatch, crm_agent, _timeline_route(30, 30))
    rid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    pages = [asyncio.run(crm_agent.get_timeline("deal", rid, offset=k)) for k in (0, 20, 40)]
    assert [q.get("offset") for _, q in seen] == [0, 20, 40]
    subjects = [ln.split(": ", 1)[1] for page in pages for ln in page.splitlines()
                if ln.startswith("• ") and ": " in ln]
    want = [f"mail {k}" for k in range(30)] + [f"note {k}" for k in range(30)]
    assert len(subjects) == 60 and len(set(subjects)) == 60
    assert [x.split(" — ")[0].split(" (")[0] for x in subjects] == want
    assert "Call again with offset=20 for the rest." in pages[0]
    assert "This is the last page." in pages[2]


def test_crm_timeline_clamps_a_deep_offset_and_says_so(monkeypatch, crm_agent) -> None:
    """Re-review of #1014: the route refuses an offset past 5,000 with a 422.
    The tool clamps it and prints a line, so the model never sees the 422."""
    pytest.importorskip("gateway.routes.crm.activities")
    from gateway.routes.crm.activities import MAX_TIMELINE_OFFSET

    seen = _gw(monkeypatch, crm_agent, lambda p, q: {"entries": [], "has_more": False})
    rid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    out = asyncio.run(crm_agent.get_timeline("deal", rid, offset=9000))
    assert crm_agent._TIMELINE_OFFSET_MAX == MAX_TIMELINE_OFFSET
    assert seen[-1][1]["offset"] == MAX_TIMELINE_OFFSET
    assert "The timeline reads back at most 5000 entries" in out


# ── 9. The shared tools: the parallel join and the attachment listing ──────

def test_call_agents_parallel_names_the_tasks_it_did_not_run(monkeypatch) -> None:
    """Five run in one call. Tasks 6 and 7 used to vanish from the join."""
    import json

    executor = pytest.importorskip("orchestrator.executor")
    from acb_skills import agent_tools

    async def fake_run(agent_name, message, run_id, queue, model=None):
        return f"done: {message}"

    monkeypatch.setattr(executor, "_run_sub_agent_streaming", fake_run)
    tasks = [{"agent": f"agent-{k}", "message": f"task {k}"} for k in range(1, 8)]
    out = asyncio.run(agent_tools.call_agents_parallel(json.dumps(tasks)))
    assert "done: task 5" in out and "done: task 6" not in out
    assert "Ran 5 of 7 tasks" in out
    assert "agent-6" in out and "agent-7" in out


def test_call_agents_parallel_counts_only_the_tasks_that_ran(monkeypatch) -> None:
    """Review of #1014: 'Ran 5 of 7' was printed even when some of the five
    were unreadable. Tasks 6 and up are named even when all five are bad."""
    import json

    executor = pytest.importorskip("orchestrator.executor")
    from acb_skills import agent_tools

    async def fake_run(agent_name, message, run_id, queue, model=None):
        return "ok"

    monkeypatch.setattr(executor, "_run_sub_agent_streaming", fake_run)
    tasks = [{"agent": "a", "message": "m"}, {"agent": "b"}, *(
        {"agent": f"x{k}", "message": "m"} for k in range(3, 8))]
    out = asyncio.run(agent_tools.call_agents_parallel(json.dumps(tasks)))
    assert "Ran 4 of 7 tasks" in out and "x6, x7" in out
    bad = [{"agent": f"b{k}"} for k in range(5)] + [{"agent": "late", "message": "m"}]
    out = asyncio.run(agent_tools.call_agents_parallel(json.dumps(bad)))
    assert "late" in out and "did not run" in out


def test_call_agents_parallel_names_a_task_it_could_not_read(monkeypatch) -> None:
    import json

    executor = pytest.importorskip("orchestrator.executor")
    from acb_skills import agent_tools

    async def fake_run(agent_name, message, run_id, queue, model=None):
        return "ok"

    monkeypatch.setattr(executor, "_run_sub_agent_streaming", fake_run)
    tasks = [{"agent": "a", "message": "m"}, {"agent": "b"}]
    out = asyncio.run(agent_tools.call_agents_parallel(json.dumps(tasks)))
    assert "1 task had no agent or no message, so it did not run." in out


def test_the_attachment_listing_names_every_file(tmp_path) -> None:
    from acb_skills import attachment_tools

    folder = tmp_path / "thread"
    folder.mkdir()
    for k in range(60):
        (folder / f"f{k:02d}.pdf").write_bytes(b"x")
    names = attachment_tools._listing(str(tmp_path), "thread")
    assert len(names) == 60


# ── 10. Part 2: query_history and the reads that had no cap ────────────────


def test_a_one_based_argument_is_named_with_its_own_next_value() -> None:
    """A line number starts at 1, so the next start is one past the last shown."""
    assert page_line(500, 0, 400, param="start_line", base=1, noun="lines") == (
        "Showing 1-400 of 500. Call again with start_line=401 for the rest.")
    assert page_line(500, 0, 400, param="start_line", base=1,
                     tool="file_access_read_lines") == (
        "Showing 1-400 of 500. Call file_access_read_lines with start_line=401 "
        "for the rest.")


class _HistoryResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        return list(self._rows)[:n]

    def keys(self) -> list[str]:
        return ["role", "content", "created_at", "thread_id", "agent_name", "title"]


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``query_history`` over a fake session that answers ``sink["rows"]``."""
    import acb_skills.history_tools as ht
    from orchestrator import executor

    sink: dict[str, Any] = {"rows": []}

    class _Session:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def execute(self, stmt: Any, params: Any = None) -> _HistoryResult:
            sink["sql"], sink["params"] = str(stmt), params
            # The database applies the OFFSET and the LIMIT, as Postgres would.
            start, limit = int(params["offset"]), int(params["limit"]) + 1
            return _HistoryResult(sink["rows"][start:start + limit])

    monkeypatch.setattr(ht, "_acting_user", lambda: "alice@example.test")
    monkeypatch.setattr(executor, "_graph_session_opener_current", lambda: _Session)
    return sink


def _said(k: int) -> tuple[Any, ...]:
    return ("user", f"message {k}", "2026-10-01", "t1", "orchestrator", "T")


def _history_rows(out: str) -> tuple[list[Any], str]:
    """The JSON array of an answer, and the line after it."""
    rows, end = json.JSONDecoder().raw_decode(out)
    return rows, out[end:].strip()


def test_query_history_takes_an_offset_and_breaks_a_tie(history: dict[str, Any]) -> None:
    from acb_skills.history_tools import query_history

    history["rows"] = [_said(k) for k in range(25)]
    out = run(query_history(limit=10, offset=10))
    rows, line = _history_rows(out)
    assert [r["content"] for r in rows] == [f"message {k}" for k in range(10, 20)]
    assert line == "Showing 11-20. More messages exist. Call again with offset=20 for the rest."
    assert history["params"]["offset"] == 10
    # One message id is unique in its session only (the key is both).
    assert "m.created_at DESC, m.session_id DESC, m.id DESC" in history["sql"]
    assert "OFFSET" in history["sql"]


def test_query_history_says_the_last_page_and_past_the_end(history: dict[str, Any]) -> None:
    from acb_skills.history_tools import query_history

    history["rows"] = [_said(k) for k in range(25)]
    rows, line = _history_rows(run(query_history(limit=10, offset=20)))
    assert len(rows) == 5 and line == "Showing 21-25. This is the last page."
    rows, line = _history_rows(run(query_history(limit=10, offset=40)))
    assert rows == [] and line == "Nothing at offset=40. This is past the end."
    # One page that holds everything stays a bare JSON array.
    history["rows"] = [_said(k) for k in range(3)]
    assert json.loads(run(query_history()))[2]["content"] == "message 2"


def _notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> Any:
    from acb_skills import note_tools

    (tmp_path / "agent-data").mkdir(parents=True)
    (tmp_path / "agent-data" / "NOTES.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(note_tools, "_get_agent_dir", lambda: str(tmp_path))
    return note_tools


def test_recall_notes_pages_a_long_file(monkeypatch, tmp_path) -> None:
    nt = _notes(monkeypatch, tmp_path, "\n".join(f"fact {k}" for k in range(450)))
    out = run(nt.recall_notes("NOTES.md"))
    assert out.splitlines()[:2] == ["fact 0", "fact 1"]
    assert "fact 199" in out and "fact 200" not in out
    assert out.endswith("Showing 1-200 of 450. Call again with offset=200 for the rest.")
    out = run(nt.recall_notes("NOTES.md", offset=400))
    assert out.splitlines()[0] == "fact 400"
    assert out.endswith("Showing 401-450 of 450. This is the last page.")


def test_recall_notes_pages_the_matching_lines_and_bounds_the_characters(
        monkeypatch, tmp_path) -> None:
    nt = _notes(monkeypatch, tmp_path, "\n".join(
        [f"lead {k}" for k in range(300)] + ["x" * 9_000 for _ in range(5)]))
    out = run(nt.recall_notes("NOTES.md", query="lead", offset=200))
    assert out.splitlines()[0] == "lead 200"
    assert out.endswith("Showing 201-300 of 300. This is the last page.")
    # Long lines end the page early, on a line, and the hint names the next.
    out = run(nt.recall_notes("NOTES.md", offset=300))
    assert len(out) < nt.NOTES_PAGE_CHARS + 200
    assert out.endswith("Showing 301-302 of 305. Call again with offset=302 for the rest.")
    # A short file reads as before, with no line.
    nt2 = _notes(monkeypatch, tmp_path / "s", "one\ntwo")
    assert run(nt2.recall_notes("NOTES.md")) == "one\ntwo"


def test_list_workflows_pages(monkeypatch) -> None:
    import gateway.routes.workflows.service as svc
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import workflow_tools

    orgs: list[str] = []

    async def published(*, organization_id: str) -> list[dict[str, Any]]:
        orgs.append(organization_id)
        return [{"id": f"w{k}", "name": f"Flow {k}", "description": "", "version": 1}
                for k in range(30)]

    monkeypatch.setattr(svc, "list_published_workflows", published)
    # The tool takes the org from the run binding (PR #1052). The real
    # binding under FORCE RLS: ``test_workflows_tool_rls_r8.py``.
    token = bind_tenant("org-paging")
    try:
        out = run(workflow_tools.list_workflows())
        rows, line = _history_rows(out)
        assert len(rows) == workflow_tools.WORKFLOWS_PAGE == 25
        assert line == "Showing 1-25 of 30. Call again with offset=25 for the rest."
        rows, line = _history_rows(run(workflow_tools.list_workflows(offset=25)))
        assert [r["id"] for r in rows] == [f"w{k}" for k in range(25, 30)]
        assert line == "Showing 26-30 of 30. This is the last page."
    finally:
        release_tenant(token)
    assert orgs == ["org-paging", "org-paging"]


def test_my_tasks_list_schedule_pages(monkeypatch) -> None:
    blocks = [{**TASK, "id": f"{k:032x}", "title": f"block {k}",
               "scheduled_start": f"2026-10-15T{k // 4:02d}:{k % 4 * 15:02d}:00+00:00"}
              for k in range(60)]

    async def request(method: str, path: str, **kw: Any) -> Any:
        return {"rows": blocks, "total": len(blocks)}

    monkeypatch.setattr(core, "_request", request)
    out = run(core.my_tasks_list_schedule("2026-10-15T00:00", "2026-10-16T00:00"))
    assert "block 49" in out and "block 50" not in out
    assert out.endswith("Showing 1-50 of 60. Call again with offset=50 for the rest.")
    out = run(core.my_tasks_list_schedule("2026-10-15T00:00", "2026-10-16T00:00",
                                          offset=50))
    assert "block 50" in out and "block 49" not in out
    assert out.endswith("Showing 51-60 of 60. This is the last page.")


def test_the_calendar_route_breaks_a_tie_of_the_start_by_id() -> None:
    from gateway.routes.projects.personal import calendar_key

    rows = [{"id": i, "scheduled_start": "2026-10-15T09:00:00+00:00"} for i in "cab"]
    rows.append({"id": "z", "scheduled_start": "2026-10-15T08:00:00+00:00"})
    assert [r["id"] for r in sorted(rows, key=calendar_key)] == ["z", "a", "b", "c"]


# ── 11. The sandbox file tools ──────────────────────────────────────────────

async def _file_tools(files: dict[str, str]) -> dict[str, Any]:
    """MAF's file tools over an in-memory store, after the paging wrap."""
    from acb_skills.sandbox_tools import page_file_tools
    from agent_framework import FileAccessProvider, InMemoryAgentFileStore, SessionContext

    store = InMemoryAgentFileStore()
    for name, body in files.items():
        await store.write(name, body)
    context = SessionContext(input_messages=[])
    provider = FileAccessProvider(store, disable_readonly_tool_approval=True,
                                  disable_write_tool_approval=True)
    await provider.before_run(agent=None, session=None, context=context, state={})
    return {t.name: t for t in page_file_tools(list(context.tools))}


def test_a_long_sandbox_file_reads_in_windows() -> None:
    from acb_skills import sandbox_tools as st

    body = "".join(f"row {k}\n" for k in range(1000))

    async def go() -> tuple[str, str, str]:
        tools = await _file_tools({"big.csv": body, "small.txt": "a\nb"})
        whole = await tools["file_access_read"].func(file_name="big.csv")
        part = await tools["file_access_read_lines"].func(file_name="big.csv",
                                                          start_line=401)
        small = await tools["file_access_read"].func(file_name="small.txt")
        return whole, part, small

    whole, part, small = run(go())
    assert whole.startswith("row 0\n") and "row 399\n" in whole and "row 400" not in whole
    assert whole.endswith(
        f"Showing 1-{st.FILE_READ_LINES} of 1001. Call file_access_read_lines with "
        f"start_line={st.FILE_READ_LINES + 1} for the rest.")
    assert part.startswith("401\trow 400\n") and "800\trow 799\n" in part
    assert "801\t" not in part
    assert part.endswith("Showing 401-800 of 1001. Call again with start_line=801 for the rest.")
    assert small == "a\nb", "a short file reads as before"


def test_a_sandbox_file_of_long_lines_reads_in_character_windows() -> None:
    from acb_skills import sandbox_tools as st

    body = "\n".join("y" * 30_000 for _ in range(5))

    async def go() -> tuple[str, str]:
        tools = await _file_tools({"wide.txt": body, "one.txt": "z" * 90_000})
        return (await tools["file_access_read"].func(file_name="wide.txt"),
                await tools["file_access_read"].func(file_name="one.txt"))

    wide, one = run(go())
    assert len(wide) < st.FILE_READ_CHARS + 200
    assert wide.endswith("Showing 1-1 of 5. Call file_access_read_lines with start_line=2 "
                         "for the rest.")
    assert len(one) < st.FILE_READ_CHARS + 300
    assert "Line 1 holds 90000 characters" in one


def test_a_large_sandbox_folder_lists_in_pages() -> None:
    from acb_skills import sandbox_tools as st

    files = {f"outputs/f{k:04d}.txt": "x" for k in range(450)}

    async def go() -> tuple[Any, Any, dict[str, Any]]:
        tools = await _file_tools(files)
        ls = tools["file_access_ls"]
        return (await ls.func(directory="outputs"),
                await ls.func(directory="outputs", offset=400),
                ls.parameters())

    first, last, schema = run(go())
    rows, line = _history_rows(first)
    assert len(rows) == st.LS_PAGE == 200 and rows[0]["name"] == "f0000.txt"
    assert line == "Showing 1-200 of 450. Call again with offset=200 for the rest."
    rows, line = _history_rows(last)
    assert [r["name"] for r in rows][:1] == ["f0400.txt"] and len(rows) == 50
    assert line == "Showing 401-450 of 450. This is the last page."
    assert set(schema["properties"]) == {"directory", "glob_pattern", "offset"}
