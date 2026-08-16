"""Module Studio — the org module library + conversational generator (spec §5).

The loop: describe → generate (gateway ``/v1`` tier routing) → validate (AST,
rung 2) → test against sample input → refine → save. A module only becomes
usable in workflows when its status is ``ready``; every generated module
carries provenance (prompt, model, who saved it).
"""

from __future__ import annotations

import json
import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.workflows.core import (
    _log,
    _tenant_session,
    _uid,
    iso,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.engine.modules import (
    MAX_CODE_BYTES,
    ModuleExecutionError,
    run_module_code,
    validate_module_code,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

GENERATOR_TIER = "tier-balanced"
GENERATOR_FALLBACK_TIER = "tier-fast"
GENERATOR_MAX_TOKENS = 3000

#: The contract the model writes inside — states the validator's rules so the
#: generation succeeds on the first pass (spec §5 step 2).
GENERATOR_SYSTEM_PROMPT = """\
You write small pure-transform Python modules for the Metorite Workflows
app. A module is a single piece of data-processing logic used as a node in an
automation workflow.

Hard rules (a mechanical validator rejects violations):
- Define exactly one top-level `def run(inputs):` taking one dict argument and
  returning a dict. Helper functions and simple constant assignments are
  allowed at top level; nothing else.
- NO imports of any kind. NO classes, async, yield, global/nonlocal.
- NO underscore-prefixed attributes, NO dunder names, and none of: eval, exec,
  compile, open, input, __import__, globals, locals, vars, getattr, setattr,
  delattr, hasattr, dir, type, object, super, breakpoint.
- Only pure computation over the inputs — no file, network, or system access.
- Available builtins: len str int float bool dict list set tuple sorted sum
  min max enumerate range zip map filter any all abs round isinstance
  reversed print and the standard exceptions.

Respond with ONLY a JSON object (no prose, no markdown fence):
{
  "name": "snake_case_short_name",
  "description": "one sentence",
  "code": "def run(inputs):\\n    ...",
  "input_schema": {"field": "type — description", ...},
  "output_schema": {"field": "type — description", ...}
}
"""


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    current_code: str | None = None  # refine an existing draft
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class ModuleSave(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    code: str = Field(min_length=1, max_length=MAX_CODE_BYTES)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"


class ModuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    code: str | None = Field(default=None, min_length=1, max_length=MAX_CODE_BYTES)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    status: str | None = None


class ModuleTest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    code: str | None = None  # test unsaved code


def _module_row(row: Any, *, with_code: bool = True) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        "language": row.language,
        "input_schema": parse_jsonb(row.input_schema, {}),
        "output_schema": parse_jsonb(row.output_schema, {}),
        "provenance": parse_jsonb(row.provenance, {}),
        "status": row.status,
        "created_by": row.created_by,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }
    if with_code:
        out["code"] = row.code
    return out


@router.get("/modules")
async def list_modules(
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text("SELECT * FROM workflow_modules ORDER BY updated_at DESC"),
            )
        ).fetchall()
    return [_module_row(r, with_code=False) for r in rows]


@router.post("/modules", status_code=201)
async def create_module(
    body: ModuleSave,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    _assert_valid_status(body.status)
    _assert_valid_code(body.code)
    async with _tenant_session() as db:
        existing = (
            await db.execute(
                text("SELECT 1 FROM workflow_modules WHERE name = :name"),
                {"name": body.name.strip()},
            )
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A module with that name exists")
        row = (
            await db.execute(
                text(
                    """INSERT INTO workflow_modules
                   (name, description, code, input_schema, output_schema,
                    provenance, status, created_by)
                   VALUES (:name, :description, :code, :input_schema ::jsonb,
                           :output_schema ::jsonb, :provenance ::jsonb,
                           :status, :by)
                   RETURNING *"""
                ),
                {
                    "name": body.name.strip(),
                    "description": body.description.strip(),
                    "code": body.code,
                    "input_schema": json.dumps(body.input_schema, default=str),
                    "output_schema": json.dumps(body.output_schema, default=str),
                    "provenance": json.dumps(
                        {**body.provenance, "saved_by": _uid(user)},
                        default=str,
                    ),
                    "status": body.status,
                    "by": _uid(user),
                },
            )
        ).fetchone()
    return _module_row(row)


@router.get("/modules/{module_id}")
async def get_module(
    module_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text("SELECT * FROM workflow_modules WHERE id = :id"),
                {"id": module_id},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Module not found")
    return _module_row(row)


@router.put("/modules/{module_id}")
async def update_module(
    module_id: str,
    body: ModuleUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    if body.status is not None:
        _assert_valid_status(body.status)
    if body.code is not None:
        _assert_valid_code(body.code)
    sets, params = ["updated_at = now()"], {"id": module_id}
    for column in ("name", "description", "code", "status"):
        value = getattr(body, column)
        if value is not None:
            sets.append(f"{column} = :{column}")
            params[column] = value.strip() if column in ("name", "description") else value
    for column in ("input_schema", "output_schema"):
        value = getattr(body, column)
        if value is not None:
            sets.append(f"{column} = :{column} ::jsonb")
            params[column] = json.dumps(value, default=str)
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text(f"UPDATE workflow_modules SET {', '.join(sets)} WHERE id = :id RETURNING *"),
                params,
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Module not found")
    return _module_row(row)


@router.delete("/modules/{module_id}", status_code=204)
async def delete_module(
    module_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    async with _tenant_session() as db:
        await db.execute(
            text("DELETE FROM workflow_modules WHERE id = :id"),
            {"id": module_id},
        )


@router.post("/modules/test")
async def test_module(
    body: ModuleTest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Run code (saved or draft) against sample inputs (spec §5 step 4)."""
    code = body.code
    if not code:
        raise HTTPException(status_code=422, detail="Provide code to test")
    violations = validate_module_code(code)
    if violations:
        return {"ok": False, "violations": [v.as_dict() for v in violations]}
    try:
        output = await run_module_code(code, body.inputs)
    except ModuleExecutionError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "output": output}


@router.post("/modules/{module_id}/test")
async def test_saved_module(
    module_id: str,
    body: ModuleTest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text("SELECT code FROM workflow_modules WHERE id = :id"),
                {"id": module_id},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Module not found")
    try:
        output = await run_module_code(row.code, body.inputs)
    except ModuleExecutionError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "output": output}


# ── The conversational generator ─────────────────────────────────────────────


@router.post("/modules/generate")
async def generate_module(
    body: GenerateRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Describe → generate → validate; one automatic repair round (spec §5)."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
    ]
    for turn in body.history:
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:8000]})
    prompt = body.prompt.strip()
    if body.current_code:
        prompt = (
            f"Current module code:\n```python\n{body.current_code[:8000]}\n```\n\n"
            f"Requested change: {prompt}"
        )
    messages.append({"role": "user", "content": prompt})

    generated = await _call_generator(messages)
    violations = validate_module_code(generated.get("code", ""))
    if violations:
        # One mechanical repair round with the violations named (spec §5.3).
        messages.append({"role": "assistant", "content": json.dumps(generated)})
        messages.append(
            {
                "role": "user",
                "content": (
                    "The validator rejected that code:\n"
                    + "\n".join(f"- line {v.line}: {v.message}" for v in violations[:10])
                    + "\nEmit the corrected JSON object."
                ),
            }
        )
        generated = await _call_generator(messages)
        violations = validate_module_code(generated.get("code", ""))

    return {
        "ok": not violations,
        "module": {
            "name": str(generated.get("name") or "generated_module")[:80],
            "description": str(generated.get("description") or "")[:2000],
            "code": str(generated.get("code") or ""),
            "input_schema": generated.get("input_schema") or {},
            "output_schema": generated.get("output_schema") or {},
            "provenance": {
                "prompt": body.prompt[:2000],
                "model": generated.get("_model", ""),
                "generated_by": _uid(user),
            },
        },
        "violations": [v.as_dict() for v in violations],
    }


async def _call_generator(messages: list[dict[str, str]]) -> dict[str, Any]:
    try:
        from acb_llm import acompletion_with_fallback

        resp, used_model = await acompletion_with_fallback(
            model=GENERATOR_TIER,
            fallback_model=GENERATOR_FALLBACK_TIER,
            messages=messages,
            max_tokens=GENERATOR_MAX_TOKENS,
            temperature=0.2,
            source="workflows",
        )
    except Exception as exc:
        _log.warning("workflows.generator_failed", error=str(exc)[:200])
        raise HTTPException(status_code=502, detail="Module generation failed") from exc
    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        content = ""
    parsed = _extract_json(content)
    if not isinstance(parsed, dict) or "code" not in parsed:
        raise HTTPException(
            status_code=502,
            detail="The generator returned an unparseable result — try rephrasing",
        )
    parsed["_model"] = used_model
    return parsed


def _extract_json(content: str) -> Any:
    """Parse the model's JSON, tolerating a stray markdown fence."""
    stripped = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    try:
        return json.loads(stripped)
    except ValueError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(stripped[start : end + 1])
            except ValueError:
                return None
    return None


def _assert_valid_status(status: str) -> None:
    if status not in ("draft", "ready", "disabled"):
        raise HTTPException(status_code=422, detail=f"Invalid status '{status}'")


def _assert_valid_code(code: str) -> None:
    violations = validate_module_code(code)
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "module_invalid",
                "violations": [v.as_dict() for v in violations],
            },
        )
