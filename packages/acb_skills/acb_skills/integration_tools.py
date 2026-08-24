"""Integration discoverability for agents (agent_coding_skill.md §9).

``list_integrations`` reports which Integration Registry services this agent
DECLARED (``config.json: integrations`` / ``optional_integrations``) and which
resolved for the current run, plus the canonical env-var NAMES a script may
read for each. It never returns credential VALUES — secrets flow only through
the run-scoped process env (executor ``_inject_integrations_to_env``) into
``run_script`` / ``code_task`` subprocess envs (``code_tools._script_env``).
"""
from __future__ import annotations

from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT


async def list_integrations() -> str:
    """List the integrations available to you and how scripts access them.

    Shows which platform integrations (Zoho CRM, Gmail, SerpAPI, …)
    are configured for you this run, and the environment-variable NAMES your
    scripts (``run_script`` / ``code_task``) can read for each — credential
    values are injected at run time and are never shown here. Also lists any
    declared-but-unavailable integrations with the reason, so you can tell the
    user what needs configuring instead of failing silently.
    """
    try:
        from acb_skills.integrations import FIELD_TO_ENV  # noqa: PLC0415
    except ImportError:
        return "list_integrations unavailable: integration registry not importable."

    raw = _WRITE_ARTIFACT_CONTEXT.get("integrations")
    resolved = [s for s in raw if isinstance(s, str)] if isinstance(raw, list) else []
    warnings = _WRITE_ARTIFACT_CONTEXT.get("integration_warnings")
    warnings = warnings if isinstance(warnings, dict) else {}

    if not resolved and not warnings:
        return (
            "No integrations are declared for this agent. To use one, it must "
            "be added to the agent's config.json (integrations / "
            "optional_integrations) and configured by an operator."
        )

    lines: list[str] = []
    if resolved:
        lines.append("Available integrations (env vars your scripts can read):")
        for svc in resolved:
            env_vars = ", ".join(v for _, v in FIELD_TO_ENV.get(svc, []))
            lines.append(f"- {svc}: {env_vars or '(no env vars mapped)'}")
        lines.append(
            "Scripts run via run_script/code_task receive exactly these vars "
            "(read with os.getenv); values are injected at run time — never "
            "hard-code or print them."
        )
    if warnings:
        lines.append("Declared but UNAVAILABLE (tell the user, don't retry):")
        for svc, reason in sorted(warnings.items()):
            lines.append(f"- {svc}: {reason}")
    return "\n".join(lines)
