"""Mutation sandbox runner — executed inside the Copilot SDK Docker container.

Env vars (set by orchestrator.mutation._run_mutation_sandbox):
    MUTATION_PROMPT         Full task prompt for the coding agent.
    COPILOT_GITHUB_TOKEN    GitHub OAuth token (standard Copilot auth).
    GATEWAY_API_KEY         API key for the gateway's /v1 endpoint.
    GATEWAY_BASE_URL        Gateway base URL (e.g. http://host.docker.internal:8080).
    GATEWAY_MODEL           Model name (e.g. openai/tier-powerful).

The agent repo is expected to be mounted at /workspace/repo (read-write).

Output contract (printed to stdout, one per line):
    COMMIT_SHA: <sha>       — the commit the sandbox produced (if any)
    TEST_SUMMARY: <text>    — pytest result summary
    {"success": true|false, "commit_sha": "...", "test_summary": "..."}

Exits 0 on success, 1 on failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys

# Regex to detect commit-sha and test-summary sentinels in agent output
_COMMIT_SHA_RE = re.compile(r"COMMIT_SHA:\s*([a-f0-9]{7,40})", re.IGNORECASE)
_TEST_SUMMARY_RE = re.compile(r"TEST_SUMMARY:\s*(.+)$", re.IGNORECASE)
# Also detect PR URLs (legacy — some agents may still create PRs)
_GITHUB_PR_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


async def main() -> None:
    prompt = os.environ.get("MUTATION_PROMPT", "").strip()
    if not prompt:
        _die("MUTATION_PROMPT is not set")

    gateway_key = os.environ.get("GATEWAY_API_KEY", "").strip()
    gateway_url = os.environ.get("GATEWAY_BASE_URL", "").strip()
    gateway_model = os.environ.get("GATEWAY_MODEL", "openai/tier-powerful").strip()
    github_token = os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()

    if not gateway_key and not github_token:
        _die("No auth configured: set GATEWAY_API_KEY+GATEWAY_BASE_URL or COPILOT_GITHUB_TOKEN")

    # SDK 1.0 (H-181): the client and create_session take keyword arguments,
    # ``cwd`` became ``working_directory``, and ``send`` takes the prompt.
    from copilot import CopilotClient, PermissionHandler  # noqa: PLC0415
    from copilot.session_events import SessionEventType  # noqa: PLC0415

    client_options: dict = {}
    session_config_kwargs: dict = {
        "on_permission_request": PermissionHandler.approve_all,
        "model": gateway_model,
    }

    repo_dir = "/workspace/repo"
    if os.path.isdir(repo_dir):
        client_options["working_directory"] = repo_dir

    if gateway_key and gateway_url:
        session_config_kwargs["provider"] = {
            "type": "openai",
            "base_url": f"{gateway_url.rstrip('/')}/v1",
            "api_key": gateway_key,
        }
    else:
        client_options["github_token"] = github_token

    messages: list[str] = []
    pr_url: str | None = None
    commit_sha: str = ""
    test_summary: str = ""
    done = asyncio.Event()

    client = CopilotClient(**client_options)
    await client.start()
    try:
        session = await client.create_session(**session_config_kwargs)

        def on_event(event) -> None:  # noqa: ANN001
            nonlocal pr_url, commit_sha, test_summary
            if event.type == SessionEventType.ASSISTANT_MESSAGE:
                content = event.data.content or ""
                messages.append(content)
                # Scan for COMMIT_SHA sentinel
                if not commit_sha:
                    m = _COMMIT_SHA_RE.search(content)
                    if m:
                        commit_sha = m.group(1)
                # Scan for TEST_SUMMARY sentinel
                if not test_summary:
                    m = _TEST_SUMMARY_RE.search(content)
                    if m:
                        test_summary = m.group(1).strip()
                # Legacy: scan for PR URL
                if not pr_url:
                    m = _GITHUB_PR_RE.search(content)
                    if m:
                        pr_url = m.group(0)
            elif event.type == SessionEventType.SESSION_IDLE:
                done.set()

        session.on(on_event)
        await session.send(prompt)
        await done.wait()
    finally:
        await client.stop()

    # Final scan across all captured messages for missed sentinels
    if not pr_url:
        for msg in messages:
            m = _GITHUB_PR_RE.search(msg)
            if m:
                pr_url = m.group(0)
                break
    if not commit_sha:
        for msg in messages:
            m = _COMMIT_SHA_RE.search(msg)
            if m:
                commit_sha = m.group(1)
                break
    if not test_summary:
        for msg in messages:
            m = _TEST_SUMMARY_RE.search(msg)
            if m:
                test_summary = m.group(1).strip()
                break

    # Fallback: check git directly in the mounted repo
    if not commit_sha and os.path.isdir(repo_dir):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir, capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                commit_sha = r.stdout.strip()
        except Exception:
            pass

    # Print sentinels for the orchestrator to parse
    if commit_sha:
        print(f"COMMIT_SHA: {commit_sha}", flush=True)
    if test_summary:
        print(f"TEST_SUMMARY: {test_summary}", flush=True)

    success = bool(commit_sha)  # commit-gate: success = a commit was produced
    result = {
        "success": success,
        "commit_sha": commit_sha,
        "test_summary": test_summary,
        "pr_url": pr_url,
    }
    print(json.dumps(result), flush=True)
    sys.exit(0 if success else 1)


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
