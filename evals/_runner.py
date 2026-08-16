"""Shared promptfoo provider for skill golden cases (ADR-017, HH-1).

Every ``skills/<domain>/<skill>/evals/cases.yaml`` points its provider at this
file.  For each test case the runner:

1. Resolves the skill (provider ``config.skill`` or the ``_skill`` test var).
2. Loads the skill's ``SKILL.md`` and uses its body as the system prompt.
3. Resolves entity fixtures for the case's ``*_id`` vars from
   ``evals/fixtures/entities.json`` (CI has no live graph DB — fixtures stand
   in for the ``graph.read.*`` tools the skill would call in production).
4. Calls the configured OpenAI-compatible endpoint (``LITELLM_BASE_URL``).

promptfoo python-provider protocol: expose ``call_api(prompt, options,
context)`` returning ``{"output": str}`` or ``{"error": str}``.

Env:
    LITELLM_BASE_URL   OpenAI-compatible base URL (default local proxy)
    LITELLM_API_KEY    bearer key for that endpoint
    EVAL_MODEL_TIER1 / EVAL_MODEL_TIER2 / EVAL_MODEL_TIER3
                       model overrides per tier label
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "entities.json"

# Test var name → fixture kind (mirrors the graph entity the id refers to).
_VAR_KIND = {
    "email_id": "message",
    "task_id": "task",
    "deal_id": "deal",
    "project_id": "project",
    "customer_id": "customer",
    "transcript_id": "meeting",
}

_TIER_MODEL_DEFAULTS = {
    "acb-tier1": os.environ.get("EVAL_MODEL_TIER1", "groq/llama-3.3-70b-versatile"),
    "acb-tier2": os.environ.get("EVAL_MODEL_TIER2", "deepseek/deepseek-chat"),
    "acb-tier3": os.environ.get("EVAL_MODEL_TIER3", "deepseek/deepseek-reasoner"),
}


def _load_fixtures() -> dict[str, dict[str, Any]]:
    with _FIXTURES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _load_skill_prompt(skill: str) -> str:
    """SKILL.md frontmatter + body → a system prompt for the golden case."""
    skill_md = _REPO_ROOT / "skills" / skill / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8-sig")

    frontmatter: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "-", "\t")):
                k, _, v = line.partition(":")
                frontmatter[k.strip()] = v.strip().strip('"')

    outputs = frontmatter.get("outputs", "")
    return (
        "You are executing the Metorite skill "
        f"'{frontmatter.get('name', skill)}'.\n"
        f"Skill description: {frontmatter.get('description', '')}\n"
        f"{outputs and f'Declared outputs: {outputs}' or ''}\n\n"
        "Follow the skill instructions below. The entity data you would "
        "normally load via graph.read.* tools is provided inline in the user "
        "message — treat it as the tool results. Always include the entity "
        "citation token given in the data (e.g. [message:<uuid>]) in your "
        "output exactly as provided.\n\n"
        "--- SKILL INSTRUCTIONS ---\n"
        f"{body.strip()}"
    )


def _resolve_case_input(variables: dict[str, Any]) -> str:
    """Render the case's vars + matching fixtures as the user message."""
    fixtures = _load_fixtures()
    parts: list[str] = []
    for name, value in variables.items():
        if name.startswith("_"):
            continue
        kind = _VAR_KIND.get(name)
        record = fixtures.get(kind, {}).get(str(value)) if kind else None
        if record is not None:
            parts.append(
                f"{name} = {value}\n"
                f"Entity data ({kind}):\n"
                f"{json.dumps(record, indent=2, ensure_ascii=False)}"
            )
        else:
            parts.append(f"{name} = {value}")
    return "\n\n".join(parts) or "(no input vars)"


def _call_llm(model: str, system: str, user: str, *, json_mode: bool) -> str:
    import urllib.request

    base = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    key = os.environ.get("LITELLM_API_KEY", "sk-local-dev-change-me")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }
    if json_mode:
        # Several tier models (e.g. deepseek) return empty content in
        # structured tasks unless JSON mode is explicit.
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"] or ""


def call_api(
    prompt: str,
    options: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options or {}
    context = context or {}
    cfg = options.get("config") or {}
    variables = context.get("vars") or {}

    skill = cfg.get("skill") or variables.get("_skill")
    if not skill:
        return {"error": "No skill configured (provider config.skill or _skill var)"}

    label = options.get("label") or options.get("id") or ""
    model = cfg.get("model") or _TIER_MODEL_DEFAULTS.get(
        str(label), _TIER_MODEL_DEFAULTS["acb-tier1"]
    )
    json_mode = bool(cfg.get("json", variables.get("_json", False)))

    try:
        system = _load_skill_prompt(str(skill))
        user = _resolve_case_input(dict(variables))
        output = _call_llm(str(model), system, user, json_mode=json_mode)
    except Exception as exc:  # noqa: BLE001 — surface as an eval error row
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"output": output}
