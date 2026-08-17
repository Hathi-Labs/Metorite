"""ClickUp data retrieval — core logic for skill-clickup-sync.

These functions are called by agent-task-manager to answer questions about
tasks and projects.  They hit the ClickUp REST API directly.

All functions return plain-text strings suitable for embedding in the agent
context window (they will be shown to the LLM as tool results).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

try:
    # MCP-style risk annotations (HH-2) — both tools are read-only probes of
    # the ClickUp API (open-world: they leave Metorite over the network).
    from acb_skills.tool_annotations import annotate as _annotate_risk
except Exception:  # pragma: no cover - platform package absent in isolation
    def _annotate_risk(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn
        return _wrap


def _api_token() -> str:
    # MT-0a: read the RUN's credential, not the process environment. This skill
    # runs IN-PROCESS as a MAF tool, so `os.environ` here would return whatever
    # a concurrent run had exported — the leak `saas_multitenancy.md` §6.1
    # describes. `credential()` still lets an operator-provided value win.
    from acb_skills.integrations import credential

    tok = credential("CLICKUP_API_TOKEN")
    if not tok:
        raise RuntimeError("CLICKUP_API_TOKEN is not set")
    return tok


@_annotate_risk(read_only=True, idempotent=True, open_world=True)
async def get_task_status(task_id: str) -> str:
    """Return the current status, assignees, and due date of a ClickUp task.

    Args:
        task_id: The ClickUp task ID (e.g. "abc1234").

    Returns:
        A plain-text summary of the task for the agent context window.
    """
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(
            f"https://api.clickup.com/api/v2/task/{task_id}",
            headers={"Authorization": _api_token()},
        )
        if r.status_code == 404:
            return f"Task {task_id!r} not found in ClickUp."
        r.raise_for_status()
        t: dict[str, Any] = r.json()

    status = (t.get("status") or {}).get("status", "unknown")
    assignees = ", ".join(a.get("username", "?") for a in (t.get("assignees") or []))
    due_raw = t.get("due_date")
    due = ""
    if due_raw:
        try:
            dt = datetime.fromtimestamp(int(due_raw) / 1000, tz=UTC)
            due = f" · due {dt.strftime('%Y-%m-%d')}"
        except (TypeError, ValueError):
            pass

    return (
        f"Task: {t.get('name', task_id)}\n"
        f"Status: {status}{due}\n"
        f"Assignees: {assignees or 'none'}\n"
        f"URL: {t.get('url', '')}"
    )


@_annotate_risk(read_only=True, idempotent=True, open_world=True)
async def list_project_tasks(project_name: str, *, status_filter: str = "") -> str:
    """List open tasks in a ClickUp list matching project_name.

    Searches all spaces/lists in the configured workspace for a list whose
    name contains project_name (case-insensitive).  Returns up to 20 tasks.

    Args:
        project_name:  Partial or full project/list name to search for.
        status_filter: Optional status to filter by (e.g. "in progress").
                       Empty string means all open statuses.

    Returns:
        A plain-text task list for the agent context window.
    """
    from acb_skills.integrations import credential  # MT-0a — see _api_token

    workspace_id = credential("CLICKUP_WORKSPACE_ID")
    if not workspace_id:
        return "CLICKUP_WORKSPACE_ID is not configured — cannot list tasks."

    headers = {"Authorization": _api_token()}
    query = project_name.lower().strip()

    async with httpx.AsyncClient(timeout=20.0) as http:
        # Get all spaces
        spaces_r = await http.get(
            f"https://api.clickup.com/api/v2/team/{workspace_id}/space",
            headers=headers,
            params={"archived": "false"},
        )
        spaces_r.raise_for_status()
        spaces: list[dict] = spaces_r.json().get("spaces", [])

        matched_lists: list[dict] = []
        for space in spaces:
            lists_r = await http.get(
                f"https://api.clickup.com/api/v2/space/{space['id']}/list",
                headers=headers,
                params={"archived": "false"},
            )
            if lists_r.status_code != 200:
                continue
            for lst in lists_r.json().get("lists", []):
                if query in lst.get("name", "").lower():
                    matched_lists.append(lst)

        if not matched_lists:
            return f"No ClickUp list found matching {project_name!r}."

        list_id = matched_lists[0]["id"]
        list_name = matched_lists[0].get("name", list_id)

        tasks_r = await http.get(
            f"https://api.clickup.com/api/v2/list/{list_id}/task",
            headers=headers,
            params={
                "archived": "false",
                "include_closed": "false",
                "subtasks": "false",
                "page": "0",
            },
        )
        tasks_r.raise_for_status()
        tasks: list[dict] = tasks_r.json().get("tasks", [])

    if status_filter:
        sf = status_filter.lower()
        tasks = [t for t in tasks if sf in (t.get("status") or {}).get("status", "").lower()]

    if not tasks:
        return f"No open tasks found in {list_name!r}."

    lines = [f"Open tasks in {list_name!r} ({len(tasks[:20])} shown):"]
    for t in tasks[:20]:
        status = (t.get("status") or {}).get("status", "?")
        assignee = (t.get("assignees") or [{}])[0].get("username", "unassigned")
        lines.append(f"  [{status}] {t.get('name', '?')} — {assignee}")

    return "\n".join(lines)
