"""Projects · a pull request links itself to its task (WS-54 GH-4, G-3).

Spec: ``project-docs/specs/github_integration.md`` §10.

Two halves:

* **The grammar** (:func:`candidates`), pure. It reads two places of a pull
  request, in this order:

  1. the description, for a Metorite task URL ``https://<app host>/projects?…
     task=<uuid>…``. ``<app host>`` is the host of ``app_public_url``, so a
     staging URL never links in production;
  2. the head branch, for a full task UUID or the short form ``mt-<8 hex>``
     (§10.2). Each must be a whole segment: a segment ends at ``/``, ``_``,
     ``-`` or an end of the name.

  ``tests/fixtures/github_autolink_cases.json`` holds the cases. The gateway
  test reads it, and so does EA-13f for the short form that an agent puts in a
  branch name. ONE grammar.

* **The links** (:func:`link_pull_request`), in the caller's bound session. The
  rules of §10.3: the org is the installation's org (the session's), a task
  must be live and not in a personal project, a pull request from a fork links
  only when its author is ``OWNER``, ``MEMBER`` or ``COLLABORATOR``, at most
  five candidates, and each link goes through ``refs.insert_ref`` (the ONE
  insert of ``pm_task_refs``) with ``via = 'github'`` and ``created_by =
  'system:github'``. A later edit that drops the URL keeps the link.

**Why the short form is safe** (§10.2): the read is a range over the primary
key in the bound org, and it links only when exactly one task matches. A
collision is no link, never a wrong link.

Fences: ``tests/unit/test_github_autolink.py`` (the grammar) and
``tests/unit/test_github_webhook_r8.py`` (the rules, R8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from acb_common import get_logger
from sqlalchemy import text

_log = get_logger("gateway.projects.github_autolink")


def _obj(value: Any) -> dict[str, Any]:
    """*value* when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}

#: At most this many candidates link for one pull request (§10.3).
MAX_LINKS = 5

#: The actor of every link and every move that GitHub causes (D-PM-4 shape).
GITHUB_ACTOR = "system:github"
GITHUB_VIA = "github"

#: The authors that may link a pull request from another repository (§10.3).
TRUSTED_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_UUID_RE = re.compile(rf"\A{_UUID}\Z")
#: A whole segment of a branch name: it starts at the start or after one of
#: ``/ _ -`` and ends at the end or before one of them.
_BRANCH_UUID_RE = re.compile(rf"(?:^|[/_-])({_UUID})(?=$|[/_-])")
_BRANCH_SHORT_RE = re.compile(r"(?:^|[/_-])mt-([0-9a-f]{8})(?=$|[/_-])")
#: A URL in a description. It stops at white space and the closers of
#: Markdown, so ``[text](url)`` and ``<url>`` both read.
_URL_RE = re.compile(r"https://[^\s<>()\[\]\"'`]+")

#: The longest description and branch name that the grammar reads.
MAX_BODY_CHARS = 65_536
MAX_REF_CHARS = 255


@dataclass(frozen=True)
class Candidate:
    """One task named by a pull request: a full UUID or an 8-hex prefix."""

    kind: str   # "uuid" or "short"
    value: str  # lower case


def app_host(app_public_url: str) -> str:
    """The host of ``app_public_url``, lower case, or ``""``."""
    try:
        return (urlsplit(app_public_url or "").hostname or "").lower()
    except ValueError:
        return ""


def _from_body(body: str, host: str) -> list[Candidate]:
    found: list[Candidate] = []
    if not host:
        return found
    for match in _URL_RE.finditer(body[:MAX_BODY_CHARS]):
        raw = match.group(0).rstrip(".,;:!?")
        try:
            parts = urlsplit(raw)
            port = parts.port
        except ValueError:
            continue
        if (parts.hostname or "").lower() != host or port not in (None, 443):
            continue
        if parts.username or parts.password:
            continue
        if parts.path.rstrip("/") != "/projects":
            continue
        for value in parse_qs(parts.query).get("task", []):
            if _UUID_RE.match(value):
                found.append(Candidate("uuid", value.lower()))
    return found


def _from_branch(head_ref: str) -> list[Candidate]:
    ref = head_ref[:MAX_REF_CHARS]
    found = [Candidate("uuid", m.group(1).lower()) for m in _BRANCH_UUID_RE.finditer(ref)]
    found += [Candidate("short", m.group(1)) for m in _BRANCH_SHORT_RE.finditer(ref)]
    return found


def candidates(body: str | None, head_ref: str | None, host: str) -> list[Candidate]:
    """The tasks a pull request names, in order, each once (§10.1)."""
    out: list[Candidate] = []
    for cand in _from_body(body or "", host) + _from_branch(head_ref or ""):
        if cand not in out:
            out.append(cand)
    return out


def trusted_source(pull_request: dict[str, Any]) -> bool:
    """§10.3: the head repository is the base repository, or the author is a
    member of it. A fork of a public repository from an outsider is not."""
    head = _obj(pull_request.get("head"))
    base = _obj(pull_request.get("base"))
    head_repo = _obj(head.get("repo"))
    base_repo = _obj(base.get("repo"))
    head_name = str(head_repo.get("full_name") or "").lower()
    base_name = str(base_repo.get("full_name") or "").lower()
    if head_name and head_name == base_name:
        return True
    return str(pull_request.get("author_association") or "").upper() in TRUSTED_ASSOCIATIONS


# ── The links (in the caller's bound session) ───────────────────────────────

#: A task of the bound org that may take a link: live, and not personal.
_ELIGIBLE_SQL = (
    "SELECT t.id, t.archived_at, p.personal_owner FROM pm_tasks t "
    "JOIN pm_projects p ON p.id = t.project_id "
    "WHERE t.organization_id = CAST(:org AS uuid) AND t.id = CAST(:id AS uuid)"
)

#: Every task of the bound org whose id starts with the prefix. A range over
#: the primary key, so the index serves it. Two rows means no link.
_PREFIX_SQL = (
    "SELECT t.id FROM pm_tasks t "
    "WHERE t.organization_id = CAST(:org AS uuid) "
    "AND t.id >= CAST(:lo AS uuid) AND t.id <= CAST(:hi AS uuid) LIMIT 2"
)


async def _resolve(db: Any, org: str, cand: Candidate) -> str | None:
    """The task id of one candidate, or ``None`` when it may not link."""
    task_id = cand.value
    if cand.kind == "short":
        rows = (await db.execute(text(_PREFIX_SQL), {
            "org": org,
            "lo": f"{cand.value}-0000-0000-0000-000000000000",
            "hi": f"{cand.value}-ffff-ffff-ffff-ffffffffffff",
        })).fetchall()
        if len(rows) != 1:
            return None
        task_id = str(rows[0].id)
    row = (await db.execute(text(_ELIGIBLE_SQL), {"org": org, "id": task_id})).first()
    if row is None or row.archived_at is not None or row.personal_owner:
        return None
    return str(row.id)


async def link_pull_request(
    db: Any, org: str, pull_request: dict[str, Any], *, host: str,
) -> list[str]:
    """Link the tasks that *pull_request* names. Returns the task ids linked now.

    Runs in the caller's ``tenant_session(org)``. A second link of the same
    reference is a no-op, through the unique key of ``pm_task_refs``.
    """
    from gateway.routes.projects.refs import insert_ref
    from gateway.routes.projects.refs_classify import RefRefused, classify

    head = _obj(pull_request.get("head"))
    found = candidates(
        str(pull_request.get("body") or ""), str(head.get("ref") or ""), host,
    )
    if not found:
        return []
    if not trusted_source(pull_request):
        _log.info("github.autolink.outsider", candidates=len(found))
        return []
    try:
        classified = classify(str(pull_request.get("html_url") or ""))
    except RefRefused:
        return []
    if classified.kind != "pull_request" or classified.provider != "github":
        return []
    if len(found) > MAX_LINKS:
        _log.info("github.autolink.capped", candidates=len(found), linked=MAX_LINKS)
    linked: list[str] = []
    for cand in found[:MAX_LINKS]:
        task_id = await _resolve(db, org, cand)
        if task_id is None or task_id in linked:
            continue
        row, made = await insert_ref(
            db, task_id, classified, title=None, via=GITHUB_VIA, by=GITHUB_ACTOR,
        )
        if row is not None and made:
            linked.append(task_id)
    return linked


__all__ = [
    "GITHUB_ACTOR",
    "GITHUB_VIA",
    "MAX_LINKS",
    "Candidate",
    "app_host",
    "candidates",
    "link_pull_request",
    "trusted_source",
]
