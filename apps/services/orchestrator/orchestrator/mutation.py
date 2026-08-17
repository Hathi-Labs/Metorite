"""Self_Mutation_Node — agents fix their own code; commits await human approval.

Design constraints (ADR-006, ADR-021):

- ``max_mutation_attempts = 1`` per failure event.  If a run already attempted
  mutation, subsequent errors in the same run are logged and skipped.
- **Commit-gate flow (simplified):** The sandbox now commits locally and stops.
  It does NOT push or open a PR.  A row is inserted into ``pending_commit``
  and surfaces in the Control Plane inbox.  The operator clicks Approve →
  the gateway endpoint runs ``git push origin HEAD`` from the authenticated
  local clone.  Reject → ``git reset HEAD~1``.
- This replaces the PR-first flow for routine self-improvement while keeping
  human gating and ``max_mutation_attempts = 1``.
- If Docker is unavailable (dev), the prompt is logged and the run fails
  gracefully.
- Merge conflicts during ``git pull`` before the commit are auto-resolved
  (see ``acb_skills.loader._pull_latest``).

Sandbox implementation: ``apps/services/orchestrator/Dockerfile.mutation`` —
a slim Python 3.12 image with ``github-copilot-sdk`` installed.

Typical call site (orchestrator.executor)::

    from orchestrator.mutation import attempt_self_mutation

    result = await attempt_self_mutation(
        agent_name="task-manager",
        run_id=run_id,
        error=exc,
    )
    if result.pending_commit_id:
        print(f"Commit awaiting approval: {result.pending_commit_id}")
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from acb_audit import AuditEvent, record
from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.mutation")

MAX_MUTATION_ATTEMPTS: int = 1  # ADR-021: exactly one commit per failure event

# Per-run mutation-attempt tally. The guarantee "≤ MAX_MUTATION_ATTEMPTS per
# failure event" was previously only an emergent property of control flow — both
# call sites pass mutation_attempts=0, so the old `0 >= 1` guard never fired
# (audit H4). This makes it a REAL enforced counter keyed by run_id, so a
# re-entry for the same run is actually refused. In-process (a process restart
# is a fresh slate, which is the correct scope — a human must merge the pending
# commit before the live system retries anyway).
_MUTATION_ATTEMPTS: dict[str, int] = {}
_MUTATION_ATTEMPTS_MAX_KEYS = 10_000  # crude unbounded-growth guard (rare path)


def _mutation_limit_reached(run_id: str, explicit_prior: int = 0) -> bool:
    """Pure peek at the per-run tally — no increment, no side effects."""
    prior = max(int(explicit_prior or 0), _MUTATION_ATTEMPTS.get(run_id, 0))
    return prior >= MAX_MUTATION_ATTEMPTS


def _register_mutation_attempt(run_id: str, explicit_prior: int = 0) -> tuple[bool, int]:
    """Enforce MAX_MUTATION_ATTEMPTS for *run_id*. Pure except for the counter.

    Combines any caller-supplied ``explicit_prior`` with the internal per-run
    tally. Returns ``(allowed, attempts_after)``: when allowed it has already
    recorded this attempt (so a concurrent re-entry for the same run is refused
    even while the first is still in flight); when not allowed the counter is
    left unchanged.
    """
    prior = max(int(explicit_prior or 0), _MUTATION_ATTEMPTS.get(run_id, 0))
    if prior >= MAX_MUTATION_ATTEMPTS:
        return False, prior
    if len(_MUTATION_ATTEMPTS) > _MUTATION_ATTEMPTS_MAX_KEYS:
        _MUTATION_ATTEMPTS.clear()  # bound memory; mutations are rare
    _MUTATION_ATTEMPTS[run_id] = prior + 1
    return True, prior + 1


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class MutationResult:
    agent_name: str
    run_id: str
    attempted: bool
    # Legacy field — kept for callers that surface the PR URL in error messages.
    # Set to the pending_commit URL (/inbox?id=...) if a commit was staged.
    pr_url: str | None = None
    skipped_reason: str | None = None
    sandbox_conversation_id: str | None = None
    # New: UUID of the pending_commit row (None if sandbox failed or skipped)
    pending_commit_id: str | None = None


# ---------------------------------------------------------------------------
# Eval-gate helpers
# ---------------------------------------------------------------------------

def _tests_passed(test_summary: str) -> bool:
    """Return True ONLY when the sandbox output shows tests demonstrably passed.

    A self-mutation must not be treated as verified without POSITIVE evidence
    that tests ran and passed (audit H3). Previously empty / "no tests" output
    counted as "passed", so an agent repo with no tests would auto-approve any
    commit the sandbox produced. Rules now:
    - Empty / "no tests" / "skipped" output → False (no evidence → needs a human).
    - Any "failed" / "error" / "traceback" keyword → False.
    - Explicit "passed" keyword without failure keywords → True.
    - Ambiguous output → False (require human review).
    """
    if not test_summary:
        return False
    s = test_summary.lower().strip()
    if s in ("no tests", "no tests found", "no test files", "skipped"):
        return False
    failure_words = ("failed", " error", "errors", "traceback", "exception")
    if any(w in s for w in failure_words):
        return False
    if "passed" in s:
        return True
    # Ambiguous — hold for human review
    return False


def _auto_push_enabled() -> bool:
    """Whether a green mutation may push to origin WITHOUT human approval.

    Default OFF (audit H3): the documented governance model is that a human
    approves every self-mutation before it is pushed (README / AGENTS.md #5).
    Auto-push is an explicit opt-in escape hatch (``MUTATION_AUTO_PUSH=1``) for
    trusted, well-tested agent repos. When off, a green commit is staged in the
    approval inbox as ``pending`` instead of being pushed by ``system:auto``.
    """
    return os.environ.get("MUTATION_AUTO_PUSH", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _auto_push_commit(agent_dir: str, commit_sha: str) -> bool:
    """Push the staged commit to origin/HEAD from the local clone.

    Returns True on success.  A simple non-rebasing push is used here;
    if this fails (diverged remote) the commit stays as ``eval_failed`` /
    ``pending`` and the operator can use the Approve & Push button which
    uses the full rebase-and-push logic.
    """
    import asyncio  # noqa: PLC0415

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "origin", "HEAD",
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            return True
        _log.warning(
            "mutation.auto_push_failed",
            agent_dir=agent_dir,
            commit_sha=commit_sha[:8],
            stderr=stderr_bytes.decode(errors="replace")[:300],
        )
        return False
    except Exception as exc:  # noqa: BLE001
        _log.warning("mutation.auto_push_error", error=str(exc))
        return False


# ---------------------------------------------------------------------------
# MT-0b — self-mutation is first-party-only
# ---------------------------------------------------------------------------
# Root ``AGENTS.md`` non-negotiable 3, verbatim: native MAF agents "land approved
# self-mutations by opening a PR against THIS Metorite monorepo … It MUST
# be swapped for a tenant-isolated mechanism before any multi-tenant/customer
# deployment — third parties must never push to the shared monorepo."
#
# So: a customer's agent failing in production must not be able to open a pull
# request against Fracktal's repository. The containment is a flag that is FALSE
# by default (``organization.first_party``, migration 157), which means a tenant
# created tomorrow is contained *by construction* rather than by someone
# remembering to contain it.
#
# This is MT-0b in ``saas_multitenancy.md`` §6.2 — the cheapest sufficient fix,
# and enough to unblock everything else. Per-tenant agent repositories and a
# mutation sandbox whose output is a tenant-scoped artifact are the fuller
# answers; neither is required to stop the leak.

#: Operator hard-off, independent of the per-org flag. Unset means "defer to
#: ``organization.first_party``" — this exists so a deployment can kill the
#: whole mechanism without touching data.
_SELF_MUTATION_DISABLED_ENV = "SELF_MUTATION_DISABLED"

_SQL_FIRST_PARTY_BY_ID = """
SELECT first_party FROM organization WHERE id = :org_id
"""
#: Untenanted resolution. Deliberately requires the org to be the ONLY one: on a
#: single-tenant box this is the operator's org and behaviour is unchanged, and
#: the moment a second organization exists it returns no row and the gate fails
#: closed. A "pick the default org" fallback would keep answering true forever,
#: which is the failure this ticket exists to prevent.
_SQL_SOLE_ORG_FIRST_PARTY = """
SELECT first_party FROM organization
 WHERE (SELECT count(*) FROM organization) = 1
"""


async def _self_mutation_permitted(
    organization_id: str | None = None,
) -> tuple[bool, str]:
    """May this organization's agents land a self-mutation? Fails CLOSED.

    Returns ``(allowed, reason)``; *reason* is empty when allowed and is written
    into :attr:`MutationResult.skipped_reason` otherwise, so an operator reading
    the approval inbox can tell containment from a crash.

    **Every failure path returns False.** An unreachable database, a missing
    column, a second organization on a box that resolved untenanted — all of
    them mean "we cannot prove this is first-party", and the only safe answer to
    that is no. Availability of self-mutation is worth far less than the
    guarantee that a customer's agent never pushes to our monorepo.
    """
    if os.environ.get(_SELF_MUTATION_DISABLED_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return False, "self-mutation is disabled on this deployment (SELF_MUTATION_DISABLED)"

    try:
        from acb_common.db import get_db
        from sqlalchemy import text

        session = await get_db()
        try:
            if organization_id:
                row = (await session.execute(
                    text(_SQL_FIRST_PARTY_BY_ID), {"org_id": organization_id},
                )).first()
            else:
                row = (await session.execute(text(_SQL_SOLE_ORG_FIRST_PARTY))).first()
        finally:
            await session.close()
    except Exception as exc:
        _log.warning("mutation.first_party_check_failed", error=str(exc)[:200])
        return False, (
            "could not establish that this organization is first-party "
            "(the check failed, so self-mutation is refused)"
        )

    if row is None:
        return False, (
            "no single first-party organization resolved — self-mutation is "
            "refused for tenants (saas_multitenancy.md §6.2 / MT-0b)"
        )
    if not bool(row[0]):
        return False, (
            "this organization is not flagged first-party; a tenant's agent may "
            "not open a pull request against the Metorite monorepo "
            "(root AGENTS.md non-negotiable 3)"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def attempt_self_mutation(
    agent_name: str,
    run_id: str,
    error: Exception,
    *,
    mutation_attempts: int = 0,
    agent_dir: str | None = None,
    incompatibility: bool = False,
    organization_id: str | None = None,
) -> MutationResult:
    """Attempt to fix a failing agent using an isolated Copilot SDK sandbox.

    Spawns a detached Docker container (``acb-mutation-runner``) that runs the
    Copilot SDK agent against the agent's local clone.  The container handles
    ``pytest``, ``git commit``, ``git push``, and PR creation autonomously.

    Args:
        agent_name:       e.g. ``"task-manager"``
        run_id:           The failing run's unique ID (used for branch naming).
        error:            The exception that caused the failure.
        mutation_attempts: How many times mutation has already been attempted
                          in this run (caller tracks this; executor passes 0).
        agent_dir:        Absolute path to the persistent local clone of the
                          agent repo (from :attr:`LoadedAgent.agent_dir`).
                          Mounted read-write into the container at
                          ``/workspace/repo``.
        incompatibility:  When ``True``, the failure is a structural
                          incompatibility (missing ``agents.py``, empty
                          ``tools=[]``, LangGraph remnant, etc.) rather than a
                          runtime error.  The sandbox receives a targeted prompt
                          referencing ``agent_repo_compatibility.md`` and is
                          asked to generate a compliant ``agents.py``.

        organization_id:  The org this run belongs to. ``None`` resolves to the
                          sole organization, which exists only on a
                          single-tenant box — see
                          :func:`_self_mutation_permitted`.

    Returns:
        A :class:`MutationResult` describing what happened.
    """
    # An at-the-limit run is refused by a pure in-memory peek BEFORE anything
    # else — no database round-trip, no attempt consumed, and the refusal
    # reason names the limit rather than whatever the first-party gate would
    # have said.
    if _mutation_limit_reached(run_id, mutation_attempts):
        reason = (
            f"max_mutation_attempts={MAX_MUTATION_ATTEMPTS} already reached. "
            "A human must merge the pending PR before the live system can retry."
        )
        _log.info(
            "mutation.skipped",
            agent=agent_name,
            run_id=run_id,
            reason=reason,
        )
        return MutationResult(
            agent_name=agent_name,
            run_id=run_id,
            attempted=False,
            skipped_reason=reason,
        )

    # MT-0b — before the tally is consumed, the sandbox, git, or anything that
    # could reach the network. A tenant's failure must cost nothing, including
    # the run's one attempt when this gate itself fails transiently.
    _permitted, _deny_reason = await _self_mutation_permitted(organization_id)
    if not _permitted:
        _log.info(
            "mutation.refused_not_first_party",
            agent=agent_name, run_id=run_id, reason=_deny_reason,
        )
        return MutationResult(
            agent_name=agent_name,
            run_id=run_id,
            attempted=False,
            skipped_reason=_deny_reason,
        )

    _allowed, _attempt_no = _register_mutation_attempt(run_id, mutation_attempts)
    if not _allowed:
        # Lost a race with a concurrent re-entry for the same run between the
        # peek above and this registration.
        reason = (
            f"max_mutation_attempts={MAX_MUTATION_ATTEMPTS} already reached. "
            "A human must merge the pending PR before the live system can retry."
        )
        _log.info(
            "mutation.skipped",
            agent=agent_name,
            run_id=run_id,
            reason=reason,
        )
        return MutationResult(
            agent_name=agent_name,
            run_id=run_id,
            attempted=False,
            skipped_reason=reason,
        )

    settings = get_settings()
    error_text = f"{type(error).__name__}: {error}"
    short_run = run_id[:8]

    _log.info(
        "mutation.start",
        agent=agent_name,
        run_id=run_id,
        error=error_text[:200],
    )

    record(
        AuditEvent(
            actor="system:mutation",
            action="mutation_start",
            target=f"agent:{agent_name}",
            payload={"run_id": run_id, "error": error_text[:500]},
        )
    )

    telemetry = _build_telemetry(agent_name, run_id, short_run, error_text, settings, agent_dir=agent_dir, incompatibility=incompatibility)
    commit_staged, commit_sha, diff_text, test_summary, conversation_id = await _run_mutation_sandbox(
        agent_name, run_id, short_run, telemetry, settings
    )

    # Eval gate: a green commit is STAGED for human approval by default; it is
    # pushed automatically only when auto-push is explicitly opted in (H3).
    # Otherwise every commit — green or not — waits in the approval inbox.
    pending_commit_id: str | None = None
    auto_pushed = False
    if commit_staged and commit_sha and agent_dir:
        if _tests_passed(test_summary) and _auto_push_enabled():
            # Tests green AND operator opted into auto-push → push immediately.
            auto_pushed = await _auto_push_commit(agent_dir, commit_sha)

        commit_status = "approved" if auto_pushed else ("eval_failed" if not _tests_passed(test_summary) else "pending")
        pending_commit_id = await _register_pending_commit(
            agent_name=agent_name,
            run_id=run_id,
            local_clone_dir=agent_dir,
            commit_sha=commit_sha,
            commit_message=telemetry.get("commit_message", f"auto-fix: {error_text[:72]}"),
            diff_text=diff_text,
            test_summary=test_summary,
            status=commit_status,
            reviewed_by="system:auto" if auto_pushed else None,
        )

        if auto_pushed:
            _log.info(
                "mutation.auto_pushed",
                agent=agent_name,
                run_id=run_id,
                commit_sha=commit_sha,
                test_summary=test_summary[:100],
            )
        elif commit_status == "eval_failed":
            _log.warning(
                "mutation.eval_failed",
                agent=agent_name,
                run_id=run_id,
                commit_sha=commit_sha,
                test_summary=test_summary[:200],
                hint="Commit staged but tests failed — awaiting human decision in the agent panel.",
            )

    # pr_url is repurposed to point at the inbox item for backward-compat logging
    pr_url: str | None = f"/inbox?commit={pending_commit_id}" if pending_commit_id else None

    record(
        AuditEvent(
            actor="system:mutation",
            action=(
                "mutation_commit_auto_pushed" if auto_pushed
                else "mutation_eval_failed" if (commit_staged and not auto_pushed and not _tests_passed(test_summary))
                else "mutation_commit_pending" if pending_commit_id
                else "mutation_sandbox_failed"
            ),
            target=f"agent:{agent_name}",
            payload={
                "run_id": run_id,
                "pending_commit_id": pending_commit_id,
                "commit_sha": commit_sha,
                "test_summary": test_summary,
                "auto_pushed": auto_pushed,
                "error_type": type(error).__name__,
                "conversation_id": conversation_id,
            },
        )
    )

    return MutationResult(
        agent_name=agent_name,
        run_id=run_id,
        attempted=True,
        pr_url=pr_url,
        pending_commit_id=pending_commit_id,
        sandbox_conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_telemetry(
    agent_name: str,
    run_id: str,
    short_run: str,
    error_text: str,
    settings: Any,
    *,
    agent_dir: str | None = None,
    incompatibility: bool = False,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the failure context injected into the Copilot SDK sandbox.

    Includes the agent's purpose (instructions.md), the trigger event that
    caused the failure, and relevant skill descriptions so the sandbox
    understands not just what broke, but what the agent was trying to do.
    """
    from pathlib import Path

    org = getattr(settings, "github_org", "FracktalWorks")
    bot_name = getattr(settings, "github_bot_name", "metorite-bot")
    bot_email = getattr(settings, "github_bot_email", "") or f"{bot_name}@users.noreply.github.com"

    # Locate the agent_repo_compatibility.md guide
    compat_guide: str = ""
    if incompatibility:
        for candidate in Path(__file__).parents:
            guide = candidate / "project-docs" / "agent_repo_compatibility.md"
            if guide.exists():
                compat_guide = guide.read_text(encoding="utf-8", errors="replace")
                break

    # Agent purpose context — read instructions.md and skill descriptions
    instructions_md: str = ""
    skills_summary: str = ""
    if agent_dir:
        agent_path = Path(agent_dir)
        instr_path = agent_path / "instructions.md"
        if instr_path.exists():
            instructions_md = instr_path.read_text(encoding="utf-8", errors="replace")[:4000]
        skills_dir = agent_path / "skills"
        if skills_dir.is_dir():
            skill_lines: list[str] = []
            for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
                try:
                    content = skill_md.read_text(encoding="utf-8", errors="replace")
                    in_frontmatter = False
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped == "---":
                            in_frontmatter = not in_frontmatter
                            continue
                        if in_frontmatter or not stripped or stripped.startswith("#"):
                            continue
                        skill_lines.append(f"- {skill_md.parent.name}: {stripped[:200]}")
                        break
                except Exception:
                    pass
            if skill_lines:
                skills_summary = "\n".join(skill_lines)

    # Trigger context — what was the agent doing when it failed?
    trigger_summary: str = ""
    if event_payload:
        user_msg = event_payload.get("message") or event_payload.get("user_query") or ""
        mode = event_payload.get("mode", "unknown")
        event_keys = [k for k in event_payload if k not in ("messages", "integration_warnings")]
        trigger_parts: list[str] = []
        if user_msg:
            trigger_parts.append(f"User asked: {user_msg[:500]}")
        if mode:
            trigger_parts.append(f"Mode: {mode}")
        if event_keys:
            trigger_parts.append(f"Payload keys: {', '.join(event_keys)}")
        if trigger_parts:
            trigger_summary = "\n".join(trigger_parts)

    return {
        "agent_name": agent_name,
        "run_id": run_id,
        "short_run": short_run,
        "error": error_text,
        "repo_url": f"https://github.com/{org}/agent-{agent_name}",
        "branch_name": f"auto-fix/{short_run}",
        "commit_message": f"auto-fix: {error_text[:72]}",
        "local_clone_dir": agent_dir,
        "bot_name": bot_name,
        "bot_email": bot_email,
        "incompatibility": incompatibility,
        "compat_guide": compat_guide,
        "instructions_md": instructions_md,
        "skills_summary": skills_summary,
        "trigger_summary": trigger_summary,
    }


def _build_mutation_prompt(telemetry: dict[str, Any]) -> str:
    """Format the natural-language instruction sent to the Copilot SDK sandbox agent."""
    if telemetry.get("incompatibility"):
        return _build_incompatibility_prompt(telemetry)
    return _build_runtime_fix_prompt(telemetry)


def _build_incompatibility_prompt(telemetry: dict[str, Any]) -> str:
    """Prompt for structural incompatibilities — generates a compliant agents.py."""
    repo = telemetry["local_clone_dir"] or telemetry["repo_url"]
    guide = telemetry.get("compat_guide", "")
    guide_section = (
        f"\n## Metorite Agent Compatibility Guide\n\n{guide}\n"
        if guide else ""
    )
    return (
        f"You are a senior Python engineer tasked with making an agent repository "
        f"compatible with Metorite.\n\n"
        f"## Repository\n"
        f"Path: `{repo}`\n"
        f"Remote: {telemetry['repo_url']}\n\n"
        f"## Incompatibility detected\n"
        f"```\n{telemetry['error']}\n```\n\n"
        f"## Your task\n"
        f"1. `cd {repo}` and inspect the repository structure.\n"
        f"2. Read the compatibility guide below to understand what Metorite requires.\n"
        f"3. Create or fix `agents.py` at the repo root so that:\n"
        f"   a. It exports `build_agents() -> list[Agent]` (synchronous, zero-arg, pure).\n"
        f"   b. `GitHubCopilotAgent` is instantiated inside `build_agents()` only.\n"
        f"   c. `tools=[...]` is populated with async tool functions that call the "
        f"existing scripts in `skills/*/scripts/` and `scripts/` via subprocess or direct import.\n"
        f"   d. The system prompt is built from `prompts/system.md` + all `skills/*/SKILL.md`.\n"
        f"   e. **`graph.py` is left untouched** — only `agents.py` is created/modified.\n"
        f"4. Scan ALL `skills/*/scripts/*.py` files. Every script whose stem does not "
        f"already appear in `agents.py` must be added as an `async def` tool function.\n"
        f"5. Run `python -c \"from agents import build_agents; agents = build_agents(); "
        f"assert agents\"` to verify.\n"
        f"6. Run `pytest` if a `tests/` directory exists. Capture the summary line.\n"
        f"7. `git add agents.py` and commit on the **current branch** (do NOT create a new branch or push):\n"
        f"   `git commit -m 'fix: generate compliant agents.py for Metorite'`\n"
        f"8. Print `COMMIT_SHA: <output of git rev-parse HEAD>` so the orchestrator records it.\n"
        f"9. Print `TEST_SUMMARY: <pytest summary or 'no tests'>` so the orchestrator records it.\n\n"
        f"**Commit author:** `{telemetry['bot_name']}` <`{telemetry['bot_email']}`>\n"
        f"**Do NOT push and do NOT open a PR.** Commit locally only.\n"
        f"The orchestrator will push once a human approves via the inbox.\n"
        f"{guide_section}"
    )


def _build_runtime_fix_prompt(telemetry: dict[str, Any]) -> str:
    """Prompt for runtime errors — finds and patches the root cause."""
    if telemetry.get("local_clone_dir"):
        repo_section = (
            f"## Repository (local clone — already authenticated)\n"
            f"Path: `{telemetry['local_clone_dir']}`\n"
            f"Remote: {telemetry['repo_url']}\n\n"
            f"The repository is already cloned at the path above. "
            f"Git user identity is already configured.\n\n"
            f"**Do NOT re-clone.** Work directly from the local clone.\n"
        )
        cd_step = f"1. `cd {telemetry['local_clone_dir']}` and `git pull --ff-only`."
    else:
        repo_section = (
            f"## Repository\n{telemetry['repo_url']}\n\n"
            f"Configure git identity before committing:\n"
            f"```\ngit config user.name \"{telemetry['bot_name']}\"\n"
            f"git config user.email \"{telemetry['bot_email']}\"\n```\n"
        )
        cd_step = f"1. Clone from `{telemetry['repo_url']}` and configure bot identity."

    return (
        f"You are orchestrating a two-phase code repair using researcher and editor sub-agents.\n\n"
        f"{repo_section}\n"
        f"## Error\n```\n{telemetry['error']}\n```\n\n"
        f"## Sub-agent workflow\n"
        f"### Phase 1 — Researcher (read-only)\n"
        f"{cd_step}\n"
        f"Use grep/view tools to:\n"
        f"  a. Understand the repo structure\n"
        f"  b. Identify the root cause of the error\n"
        f"  c. Find all files that need changing\n"
        f"  d. Write a precise fix plan before touching anything\n\n"
        f"### Phase 2 — Editor (minimal write)\n"
        f"Execute ONLY the fix plan from Phase 1:\n"
        f"  a. Make minimal, correct changes\n"
        f"  b. Run `pytest` — all tests must pass. Capture the summary line.\n"
        f"  c. `git add -A` and commit on the **current branch** (do NOT create a new branch or push):\n"
        f"     `git commit -m 'auto-fix: <short description>'`\n"
        f"  d. Print `COMMIT_SHA: <output of git rev-parse HEAD>` so the orchestrator records it.\n"
        f"  e. Print `TEST_SUMMARY: <pytest summary>` so the orchestrator can record it.\n\n"
        f"**Safety rules:**\n"
        f"- Never write without first completing Phase 1 analysis\n"
        f"- Minimal fix only — do not refactor unrelated code\n"
        f"- Commit author: `{telemetry['bot_name']}` <`{telemetry['bot_email']}`>\n"
        f"- **Do NOT push and do NOT open a PR.** Commit locally only.\n"
        f"  The orchestrator will push once a human approves via the inbox.\n"
    )


async def _stash_pull_before_mutation(
    agent_dir: str, agent_name: str,
) -> None:
    """Stash → pull (rebase) → pop stash to sync the agent clone before mutation.

    This ensures the sandbox fixes code on the latest upstream version, avoiding
    merge conflicts when the operator later approves and pushes.  Local-only
    commits (from prior mutation runs awaiting approval) are preserved via
    rebase instead of reset --hard.

    Non-fatal: if the pull fails, the sandbox runs on the current clone as-is.
    """
    import asyncio

    async def _git(args: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    # 1. Stash any uncommitted changes so pull is clean
    rc, stdout, stderr = await _git([
        "stash", "--include-untracked",
        "-m", "metorite-mutation-auto-stash",
    ])
    stashed = rc == 0 and "No local changes" not in stdout
    if stashed:
        _log.info("mutation.stashed_changes", agent=agent_name)

    # 2. Fetch latest from origin
    rc, _, stderr = await _git(["fetch", "origin"])
    if rc != 0:
        _log.warning(
            "mutation.fetch_failed",
            agent=agent_name,
            stderr=stderr[:200],
        )
        if stashed:
            await _git(["stash", "pop"])
        return

    # 3. Rebase local commits on top of origin/HEAD (preserves pending commits)
    rc, _, stderr = await _git(["rebase", "origin/HEAD"])
    if rc == 0:
        _log.info("mutation.rebase_ok", agent=agent_name)
    else:
        # Rebase conflict — auto-resolve with ours, then continue
        _log.warning(
            "mutation.rebase_conflict",
            agent=agent_name,
            stderr=stderr[:200],
            hint="Auto-resolving with checkout --ours.",
        )
        await _git(["checkout", "--ours", "."])
        await _git(["add", "-A"])
        rc2, _, err2 = await _git(["rebase", "--continue"])
        if rc2 != 0:
            await _git(["rebase", "--abort"])
            _log.warning(
                "mutation.rebase_aborted",
                agent=agent_name,
                stderr=err2[:200],
                hint="Running sandbox on current clone state.",
            )
            if stashed:
                await _git(["stash", "pop"])
            return
        _log.info(
            "mutation.rebase_conflict_resolved",
            agent=agent_name,
            hint="Conflicts auto-resolved with --ours.",
        )

    # 4. Pop the stash so the sandbox sees any workspace changes
    if stashed:
        rc, _, stderr = await _git(["stash", "pop"])
        if rc != 0:
            _log.warning(
                "mutation.stash_pop_failed",
                agent=agent_name,
                stderr=stderr[:200],
                hint="Stashed changes may be lost — sandbox will see clean tree.",
            )

    _log.info("mutation.clone_synced", agent=agent_name)


async def _run_mutation_sandbox(
    agent_name: str,
    run_id: str,
    short_run: str,
    telemetry: dict[str, Any],
    settings: Any,
) -> tuple[bool, str, str, str, str | None]:
    """Spawn an isolated Copilot SDK container and wait for it to finish.

    The container makes the fix, runs pytest, and commits locally (no push).
    It prints:
    - ``COMMIT_SHA: <sha>`` — the commit the sandbox produced
    - ``TEST_SUMMARY: <line>`` — pytest one-liner
    - A final JSON line: ``{"success": true}``

    Returns ``(commit_staged, commit_sha, diff_text, test_summary, container_id)``.
    Returns ``(False, "", "", "", None)`` and logs the prompt when Docker/
    credentials are unavailable so the system degrades gracefully in dev.
    """
    import asyncio

    github_token: str | None = getattr(settings, "github_token", None) or None
    # The LLM API key, NEVER the identity token. This value crosses a process
    # boundary into a container running model-authored code, delivered as an
    # env var — the single worst place to put a credential that grants full
    # platform authority. /v1 accepts it via acb_auth.require_llm_api_auth.
    gateway_key: str | None = (
        getattr(settings, "llm_api_key", None)
        or getattr(settings, "litellm_master_key", None)
        or None
    )
    gateway_url: str = getattr(settings, "litellm_base_url", "http://host.docker.internal:8080")
    mutation_model: str = getattr(settings, "mutation_model", "tier-powerful")
    sandbox_image: str = getattr(settings, "mutation_sandbox_image", "acb-mutation-runner:latest")
    timeout_s: int = int(getattr(settings, "mutation_timeout_seconds", 600))
    agent_dir: str | None = telemetry.get("local_clone_dir")

    if not github_token and not gateway_key:
        _log.warning(
            "mutation.sandbox_not_configured",
            agent=agent_name,
            run_id=run_id,
            hint="Set GITHUB_TOKEN to enable live self-mutation.",
        )
        _log.info(
            "mutation.manual_prompt",
            agent=agent_name,
            run_id=run_id,
            prompt=_build_mutation_prompt(telemetry),
        )
        return False, "", "", "", None

    # ── Ensure the agent clone is up-to-date before sandbox runs ─────────
    # Stash → pull → pop stash so the sandbox works on latest code.  This
    # prevents the sandbox from fixing an already-fixed issue and avoids
    # merge conflicts when the operator later approves and pushes.
    if agent_dir:
        await _stash_pull_before_mutation(agent_dir, agent_name)

    prompt = _build_mutation_prompt(telemetry)
    mem_limit: str = str(getattr(settings, "mutation_memory_limit", "2g"))
    cpu_limit: str = str(getattr(settings, "mutation_cpu_limit", "2"))
    pids_limit: int = int(getattr(settings, "mutation_pids_limit", 512))

    # Run in the foreground (no -d) so we can capture stdout and learn whether
    # the fix branch was pushed.  --rm cleans the container up on exit.
    #
    # Hardening (BO-7 cheap win 3/3): this was the concrete "existing
    # container pattern" the BO-7 backlog item wants generalized to the
    # normal agent load path — but it shipped with none of its own resource/
    # capability limits. --cap-drop ALL strips everything (CAP_SYS_ADMIN,
    # CAP_NET_RAW, CAP_SYS_PTRACE, …) except DAC_OVERRIDE, which is added
    # back deliberately: the container's default user is root (Dockerfile.
    # mutation has no USER directive) but the bind-mounted agent_dir is owned
    # by the host's non-root `acb` service user (deploy/hostinger/acb-
    # gateway.service), so root-in-container needs DAC_OVERRIDE to read/write
    # it — without it every git commit inside the sandbox would fail with
    # permission denied. no-new-privileges blocks setuid-binary privilege
    # escalation. Memory/CPU/pids limits keep a runaway sandbox from starving
    # the rest of the 4GB VPS (Postgres/Redis/gateway/workbench all resident).
    docker_cmd = [
        "docker", "run",
        "--rm",
        "--name", f"acb-mutation-{short_run}",
        "--cap-drop", "ALL",
        "--cap-add", "DAC_OVERRIDE",
        "--security-opt", "no-new-privileges",
        "--memory", mem_limit,
        "--cpus", cpu_limit,
        "--pids-limit", str(pids_limit),
        "-e", f"MUTATION_PROMPT={prompt}",
        "-e", f"MUTATION_TELEMETRY_JSON={json.dumps(telemetry)}",
        "-e", f"COPILOT_GITHUB_TOKEN={github_token or ''}",
        "-e", f"GATEWAY_API_KEY={gateway_key or ''}",
        "-e", f"GATEWAY_BASE_URL={gateway_url}",
        "-e", f"GATEWAY_MODEL={mutation_model}",
        "--add-host", "host.docker.internal:host-gateway",
    ]

    if agent_dir:
        docker_cmd += ["-v", f"{agent_dir}:/workspace/repo"]

    docker_cmd.append(sandbox_image)

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            await _docker_kill(short_run)
            _log.error(
                "mutation.sandbox_timeout",
                agent=agent_name,
                run_id=run_id,
                timeout_s=timeout_s,
            )
            return False, "", "", "", None

        stdout = stdout_bytes.decode(errors="replace").strip()
        stderr = stderr_bytes.decode(errors="replace").strip()

        # Parse sentinel lines the sandbox prints to stdout.
        success = False
        commit_sha = ""
        test_summary = ""
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("COMMIT_SHA:"):
                commit_sha = stripped.split("COMMIT_SHA:", 1)[1].strip()
            elif stripped.startswith("TEST_SUMMARY:"):
                test_summary = stripped.split("TEST_SUMMARY:", 1)[1].strip()
            elif stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    success = bool(parsed.get("success"))
                except json.JSONDecodeError:
                    pass

        # Fallback: if the sandbox committed, treat as success even if no JSON sentinel
        if commit_sha and not success:
            success = True

        if proc.returncode != 0 and not success:
            _log.error(
                "mutation.sandbox_failed",
                agent=agent_name,
                run_id=run_id,
                returncode=proc.returncode,
                stderr=stderr[:500],
            )
            return False, commit_sha, "", test_summary, None

        # Capture the diff for inline review in the inbox
        diff_text = ""
        if commit_sha and agent_dir:
            diff_text = await _git_diff(agent_dir, commit_sha)

        _log.info(
            "mutation.sandbox_done",
            agent=agent_name,
            run_id=run_id,
            commit_sha=commit_sha,
            test_summary=test_summary[:200],
        )
        return success, commit_sha, diff_text, test_summary, f"acb-mutation-{short_run}"

    except Exception as exc:
        _log.error(
            "mutation.docker_error",
            agent=agent_name,
            run_id=run_id,
            error=str(exc),
        )
        return False, "", "", "", None


async def _docker_kill(short_run: str) -> None:
    """Best-effort kill of a hung mutation container."""
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "kill", f"acb-mutation-{short_run}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
    except Exception:
        pass


async def _git_diff(agent_dir: str, commit_sha: str) -> str:
    """Return ``git diff HEAD~1 HEAD`` from the local clone as a string.

    Safe to call even if there is no parent commit (returns full tree diff).
    """
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD~1", "HEAD",
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        diff = stdout_bytes.decode(errors="replace")
        # Truncate very large diffs — the inbox renders it inline
        return diff[:32_000] if len(diff) > 32_000 else diff
    except Exception as exc:
        _log.warning("mutation.diff_failed", error=str(exc))
        return ""


async def _register_pending_commit(
    *,
    agent_name: str,
    run_id: str,
    local_clone_dir: str,
    commit_sha: str,
    commit_message: str,
    diff_text: str,
    test_summary: str,
    status: str = "pending",
    reviewed_by: str | None = None,
) -> str | None:
    """Insert a row into ``pending_commit`` and return its UUID string.

    ``status`` is one of:
    - ``'approved'``   — tests passed, auto-pushed immediately
    - ``'eval_failed'`` — tests failed, awaiting human decision
    - ``'pending'``    — auto-push failed for infra reasons, awaiting human push

    Returns ``None`` if the DB write fails (non-fatal — audit event still fired).
    """
    try:
        import uuid  # noqa: PLC0415

        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        row_id = str(uuid.uuid4())
        reviewed_at_expr = "now()" if reviewed_by is not None else "NULL"
        with get_session() as sess:
            sess.execute(
                text(
                    "INSERT INTO pending_commit "
                    "(id, agent_name, run_id, local_clone_dir, commit_sha, "
                    " commit_message, diff_text, test_summary, status, reviewed_by, reviewed_at) "
                    f"VALUES (:id, :agent_name, :run_id, :local_clone_dir, :commit_sha, "
                    f"        :commit_message, :diff_text, :test_summary, :status, "
                    f"        :reviewed_by, {reviewed_at_expr})"
                ),
                {
                    "id": row_id,
                    "agent_name": agent_name,
                    "run_id": run_id,
                    "local_clone_dir": local_clone_dir,
                    "commit_sha": commit_sha,
                    "commit_message": commit_message,
                    "diff_text": diff_text,
                    "test_summary": test_summary,
                    "status": status,
                    "reviewed_by": reviewed_by,
                },
            )
            sess.commit()
        _log.info(
            "mutation.commit_registered",
            agent=agent_name,
            pending_commit_id=row_id,
            commit_sha=commit_sha,
        )
        return row_id
    except Exception as exc:
        _log.error("mutation.register_commit_failed", agent=agent_name, error=str(exc))
        return None

