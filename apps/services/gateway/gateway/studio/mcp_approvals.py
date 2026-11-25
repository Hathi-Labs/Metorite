"""The one store of the approved text of each MCP tool of an org (WS-STU B1b-3).

Spec: ``project-docs/specs/ai_studio.md`` §14 B1b-3. Migration: the file that
adds ``studio_mcp_tool_approvals`` (455 at build). The callers are
``gateway.connections.mcp_tools`` (the catalog read, the live read and the
approval). ``tests/unit/test_studio_mcp_approvals_r8.py`` runs this SQL on a
real Postgres.

**One module names the table.** This is the one module that runs SQL on
``studio_mcp_tool_approvals``. Fence:
``test_studio_mcp_approvals.py::test_one_module_runs_sql_on_the_table``.

**A row holds a hash, never text.** It holds the server key, the tool name,
the SHA-256 of the approved text (``mcp_actions.tool_text_hash``), the member
who approved it and the time. It holds no description, no url and no secret.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. No function takes an
org. An INSERT writes no ``organization_id``: the column DEFAULT reads the
bound tenant, and each statement filters on ``_BOUND_ORG`` too.

**The member.** :func:`upsert` takes the member as a required argument. ⚠️ The
store does not authenticate. The caller takes the member from the session,
never from request input, and the route checks the right of the member.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

#: The most rows that one read returns. A server keeps 200 tools at most in
#: the cache, so this holds 50 servers in full.
MAX_READ = 10_000

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The ONE read of the approvals of the bound org. The catalog and the live
#: read run it once for each request, never once for each tool.
_READ_SQL = text(
    "SELECT server, tool, text_hash FROM studio_mcp_tool_approvals "
    f"WHERE organization_id = {_BOUND_ORG} "
    "ORDER BY server, tool LIMIT :cap"
)

#: The write of one approval. A second approval of a tool replaces the hash,
#: the member and the time.
_UPSERT_SQL = text(
    "INSERT INTO studio_mcp_tool_approvals (server, tool, text_hash, approved_by) "
    "VALUES (:server, :tool, :hash, :member) "
    "ON CONFLICT (organization_id, server, tool) DO UPDATE SET "
    "text_hash = EXCLUDED.text_hash, approved_by = EXCLUDED.approved_by, "
    "approved_at = now() "
    f"WHERE studio_mcp_tool_approvals.organization_id = {_BOUND_ORG} "
    "RETURNING approved_at"
)


async def approved_hashes(db: Any) -> dict[tuple[str, str], str]:
    """The approved hash of each ``(server, tool)`` of the bound org, in ONE
    query. A tool with no row is not in the map."""
    rows = (await db.execute(_READ_SQL, {"cap": MAX_READ})).fetchall()
    return {(str(r[0]), str(r[1])): str(r[2]) for r in rows}


async def upsert(db: Any, *, server: str, tool: str, text_hash: str, member: str) -> Any:
    """Write the approval of ONE tool of the bound org, and give its time.

    *member* is the address of the session member. It is kept in lower case.
    A failed statement raises, and ``tenant_session`` rolls it back."""
    row = (await db.execute(_UPSERT_SQL, {
        "server": server, "tool": tool, "hash": text_hash,
        "member": member.strip().lower()})).fetchone()
    return row[0] if row is not None else None


__all__ = ["MAX_READ", "approved_hashes", "upsert"]
