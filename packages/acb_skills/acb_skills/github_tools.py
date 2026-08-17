"""GitHub code-search tools — auto-injected into every loaded agent.

Provides ``github_search`` and ``github_repo_search`` which mirror VS Code
Copilot's ``github_text_search`` and ``github_repo`` tools.  Agents can
search public GitHub repositories for code examples, bug fixes, and library
usage patterns.

Design
------
- ``github_search(query, scope?, maxResults?)`` — Lexical code search across
  GitHub repositories.  Returns matching file paths with line excerpts.
- ``github_repo_search(repo, query?)`` — Semantic code search within a
  specific GitHub repository.  Returns relevant code snippets.

Both tools use the GitHub REST API (no auth required for public repos).
Results are capped and truncated for LLM context efficiency.

Usage by agents::

    await github_search("FastAPI middleware authentication pattern")
    await github_repo_search("microsoft/vscode", "manage_todo_list tool")
"""
from __future__ import annotations

import asyncio
import json as _json
from urllib.parse import quote as _url_quote


async def github_search(
    query: str,
    scope: str = "",
    maxResults: int = 10,
) -> str:
    """Search public GitHub repositories for code matching a keyword query.

    Uses GitHub's code-search API.  Returns file paths with short line
    excerpts containing the match.  No authentication required for public
    repos.

    **Use this tool when:**
    - You need to find real-world code examples for a library or pattern
    - You are debugging and want to see how others solved a similar problem
    - You want to discover which repos use a specific API or function

    Args:
        query: Keyword search query.  Supports GitHub code-search syntax
               like ``language:python``, ``path:src/``, ``repo:owner/name``,
               etc.
        scope: Optional GitHub org or user to scope the search to, e.g.
               ``"microsoft"`` or ``"microsoft/vscode"``.
        maxResults: Maximum results (1-30).  Default 10.

    Returns:
        Text block with matching files and excerpts, or an error message.

    Example::

        await github_search("FastAPI Depends get_current_user language:python")
        await github_search("agent_framework_github_copilot", scope="microsoft")
    """
    max_results = max(1, min(30, int(maxResults)))

    # Build the search URL.
    q_parts = [query.strip()]
    if scope.strip():
        org = scope.strip().split("/")[0]
        if "/" in scope:
            q_parts.append(f"repo:{scope.strip()}")
        else:
            q_parts.append(f"org:{org}")

    full_q = " ".join(q_parts)
    url = (
        "https://api.github.com/search/code"
        f"?q={_url_quote(full_q)}"
        f"&per_page={max_results}"
    )

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return "github_search unavailable: httpx not installed (uv add httpx)"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Metorite/1.0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            return (
                "github_search: GitHub API rate limit exceeded. "
                "Wait 60 seconds or set GITHUB_TOKEN for higher limits."
            )
        return f"github_search HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"github_search failed: {exc}"

    items = data.get("items", [])
    if not items:
        return f"No results found for: {query!r}"

    lines: list[str] = [
        f"GitHub code search results for: {query!r} ({data.get('total_count', 0)} total)\n"
    ]
    for item in items[:max_results]:
        repo = item.get("repository", {}).get("full_name", "unknown")
        path = item.get("path", "?")
        html_url = item.get("html_url", "")
        lines.append(f"📁 {repo}/{path}")
        if html_url:
            lines.append(f"   {html_url}")
        # Show text matches if available.
        text_matches = item.get("text_matches", [])
        for tm in text_matches[:3]:
            fragment = tm.get("fragment", "").strip()[:200]
            if fragment:
                lines.append(f"   ...{fragment}...")
        lines.append("")

    return "\n".join(lines).strip()


async def github_repo_search(repo: str, query: str = "") -> str:
    """Semantically search within a specific GitHub repository.

    Uses GitHub's code-search API scoped to a single repo.  Returns
    relevant file paths and code snippets.

    **Use this tool when:**
    - You know a specific repo has relevant code (e.g. a library you use)
    - You want to understand how a feature is implemented in a known repo
    - You are reading a repo and need to find where something is defined

    Args:
        repo: Repository name in ``owner/repo`` format, e.g.
              ``"microsoft/vscode"`` or ``"fastapi/fastapi"``.
        query: Optional keyword query to narrow results.

    Returns:
        Text block with matching files and excerpts.

    Example::

        await github_repo_search("microsoft/vscode", "chatTodoListWidget")
    """
    # Build the GitHub code-search URL scoped to the repo.
    q_parts = [f"repo:{repo.strip()}"]
    if query.strip():
        q_parts.append(query.strip())

    full_q = " ".join(q_parts)
    url = (
        "https://api.github.com/search/code"
        f"?q={_url_quote(full_q)}"
        f"&per_page=10"
    )

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return "github_repo_search unavailable: httpx not installed"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Metorite/1.0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            return (
                "github_repo_search: GitHub API rate limit exceeded. "
                "Set GITHUB_TOKEN for higher limits."
            )
        return f"github_repo_search HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"github_repo_search failed: {exc}"

    items = data.get("items", [])
    if not items:
        return f"No results found in {repo!r}" + (
            f" for {query!r}" if query else ""
        )

    lines: list[str] = [
        f"GitHub repo search: {repo}"
        + (f" ({query})" if query else "")
        + f" — {data.get('total_count', 0)} results\n"
    ]
    for item in items[:10]:
        path = item.get("path", "?")
        html_url = item.get("html_url", "")
        lines.append(f"📁 {path}")
        if html_url:
            lines.append(f"   {html_url}")
        text_matches = item.get("text_matches", [])
        for tm in text_matches[:2]:
            fragment = tm.get("fragment", "").strip()[:200]
            if fragment:
                lines.append(f"   ...{fragment}...")
        lines.append("")

    return "\n".join(lines).strip()
