"""The two checks before a sweep spends a model call (WS-43v).

1. :func:`sandbox_tools` — are the Projects sandbox tools of WS-43d (PR #603)
   in this checkout, and does the broker cover projects-assistant for the
   test organization? If not, every task is SKIPPED, never passed.
   :func:`tools_offered_problem` checks it again from the first request that
   the model received.
2. :func:`router` — does the local stack serve the Router? If not, the sweep
   is NO-GO. Model access goes through the gateway's ``/v1`` on the local
   stack only. The runner adds no provider key, and it refuses an address
   that is not on this machine (a run on the production Router is WS43-G6).
"""
from __future__ import annotations

import importlib.util
import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

AGENT = "projects-assistant"

SKIPPED_ABSENT = (
    "SKIPPED: the Projects sandbox tools are not present (P1 not merged / scope not set)."
)

#: The tools that a sandboxed projects-assistant run must offer the model.
REQUIRED_TOOLS = frozenset({"run_command", "file_access_write", "file_access_read"})


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str


def sandbox_tools(org: str) -> Check:
    """Whether a run of projects-assistant in *org* gets the sandbox tools."""
    if importlib.util.find_spec("acb_skills.sandbox_tools") is None:
        return Check(False, f"{SKIPPED_ABSENT} acb_skills.sandbox_tools does not exist here.")
    try:
        from orchestrator import sandbox_broker
    except ImportError as exc:
        return Check(False, f"{SKIPPED_ABSENT} The orchestrator does not import: {exc}.")
    covers = getattr(sandbox_broker, "covers", None)
    if covers is None:
        return Check(False, f"{SKIPPED_ABSENT} sandbox_broker.covers does not exist here.")
    try:
        covered = bool(covers(AGENT, org))
    except Exception as exc:
        return Check(False, f"{SKIPPED_ABSENT} covers() raised {type(exc).__name__}: {exc}.")
    if not covered:
        return Check(False, (
            f"{SKIPPED_ABSENT} covers({AGENT!r}, {org!r}) is false. It needs "
            f"MAF_CODING_SCOPE=projects:{org}, a healthy broker (Docker and a pinned "
            "SANDBOX_IMAGE) and the D85 seam of PR #598."
        ))
    return Check(True, f"the broker covers {AGENT} for {org}")


def tool_names(body: dict[str, Any]) -> frozenset[str]:
    """The tool names of one ``/v1/chat/completions`` request body."""
    names = set()
    for item in body.get("tools") or []:
        fn = item.get("function") if isinstance(item, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return frozenset(names)


def tools_offered_problem(offered: Iterable[str]) -> str | None:
    """A SKIPPED reason when the model was not offered the sandbox tools."""
    missing = sorted(REQUIRED_TOOLS - set(offered))
    if not missing:
        return None
    return f"{SKIPPED_ABSENT} The model was not offered {', '.join(missing)}."


# ── the Router ──────────────────────────────────────────────────────────────

NO_GO = "NO-GO: the local stack does not serve the Router."


def is_local(url: str) -> bool:
    """True for a loopback address or a name of this machine."""
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def router(
    base_url: str, api_key: str, tier: str, member: str, *, client: Any = None,
) -> Check:
    """Steps 5 and 6 of WS-43a "The stack that serves the Router", as far as a client sees.

    The gateway at *base_url* must say ``router_serving`` (the flag and the
    Console wiring), and one call on *tier* must answer 200. Step 6 also
    wants one ``usage_event`` row on the local Console. Only the Console's
    database shows it, so the README says how to read it.
    """
    import httpx

    base = base_url.rstrip("/").removesuffix("/v1")
    if not is_local(base):
        return Check(False, (
            f"{NO_GO} {base} is not on this machine. The eval runs on a local stack only. "
            "A run on the production Router is owner gate WS43-G6."
        ))
    headers = {"Authorization": f"Bearer {api_key}", "X-User-Email": member,
               "X-CC-Agent": AGENT, "X-CC-Source": "eval"}
    http = client or httpx.Client(timeout=60.0)
    try:
        return _probe(http, base, headers, tier)
    except httpx.HTTPError as exc:
        return Check(False, f"{NO_GO} {base} does not answer: {type(exc).__name__}: {exc}.")
    finally:
        if client is None:
            http.close()


def _probe(http: Any, base: str, headers: dict[str, str], tier: str) -> Check:
    resp = http.get(f"{base}/settings/llm", headers=headers)
    if resp.status_code != 200:
        return Check(False, f"{NO_GO} GET {base}/settings/llm answered {resp.status_code}.")
    if not (resp.json() or {}).get("router_serving"):
        return Check(False, (
            f"{NO_GO} {base} says router_serving is false. Set ROUTER_SERVING_ENABLED=1, "
            "the Console address and a Router credential on the gateway (WS-43a steps 2 to 5)."
        ))
    probe = http.post(f"{base}/v1/chat/completions", headers=headers, json={
        "model": tier, "max_tokens": 8, "stream": False,
        "messages": [{"role": "user", "content": "Answer with the word OK."}],
    })
    if probe.status_code != 200:
        return Check(False, (
            f"{NO_GO} one call on {tier} answered {probe.status_code}: {probe.text[:200]}. "
            "Bind the tier on the local Console (POST /catalog/bindings, WS-43a step 4)."
        ))
    return Check(True, f"{base} serves the Router, and {tier} answers")
