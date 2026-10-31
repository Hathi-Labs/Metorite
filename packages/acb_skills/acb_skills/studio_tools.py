"""The Studio builder tools for Mira (STU-G1).

Spec: ``project-docs/specs/ai_studio.md`` sections 6.2 and 14 (G1).

Five **stateless** tools. No draft store exists in G1 (A3 is Stage 2). The
model holds the manifest in the chat turn and sends it back on each call.

* :func:`studio_draft` gives a minimal valid skeleton from a short brief.
  It makes no model call.
* :func:`studio_patch` applies ``add``, ``replace`` and ``remove`` ops, then
  validates.
* :func:`studio_validate` validates.
* :func:`studio_outline` is a deterministic text with the counts and the names.
  :func:`outline_text` is the pure function behind it, and the plan card of G2
  will use it.
* :func:`studio_list_actions` lists the action catalog.

**One result shape.** Each manifest tool returns ``{ok, errors, diff,
manifest}``. ``errors`` holds the validator's errors verbatim, as ``path`` and
``message``. ``manifest`` is present only when the manifest is valid, so a
refusal leaves the model with the manifest it sent.

**A cap.** A serialized manifest over :data:`MAX_MANIFEST_CHARS` characters is
refused with one sentence, the outline and the size. ``TOOL_RESULT_SPILL`` is
off by default, so the chat would carry the whole text on each call.

**The catalog comes over HTTP.** A package must not import the gateway. The
tools read ``GET /studio/actions`` as the member, with the idiom of
``crm_tools`` (the internal bearer and ``X-User-Email`` from the run
ContextVar). With no member, a tool refuses. A failed read is reported in the
result and the manifest is not called valid, because the checks for unknown
actions and class X could not run.

**No tool takes an org or a member argument** (R5). The tenant is the
member's, bound by the gateway. No tool runs code. Fence:
``tests/unit/test_studio_builder_tools.py``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from acb_common.studio_manifest import STUDIO_VERSION, ManifestError, validate_manifest
from acb_common.studio_patch import PatchError, apply_patch

from acb_skills.tool_annotations import annotate

#: The most characters of one serialized manifest in a tool call (G1 cap).
MAX_MANIFEST_CHARS = 24_000

#: The most action rows one ``studio_list_actions`` answer holds.
MAX_ACTION_ROWS = 30

#: The longest text of a brief that the skeleton keeps as the purpose.
MAX_PURPOSE_CHARS = 300

CATALOG_PATH = "/studio/actions"

_NO_MEMBER = "There is no member for this run, so the Studio tools cannot act."
_SHARED_ROOM = "A shared room cannot use the Studio tools. Ask in your own chat."


# ── The gateway, as the member (the idiom of ``crm_tools``) ─────────────────


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


def _internal_token() -> str:
    from acb_common import get_settings

    settings = get_settings()
    return (
        getattr(settings, "gateway_internal_token", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, refuse."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if "@" not in user:
        raise _Refused(_SHARED_ROOM)
    return {
        "Authorization": f"Bearer {_internal_token()}",
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


async def _read_catalog() -> list[dict[str, Any]]:
    """The rows of ``GET /studio/actions``. Raise :class:`_Refused` with a sentence."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_gateway_url()}{CATALOG_PATH}", headers=headers)
    except httpx.HTTPError as exc:
        raise _Refused(
            f"The action catalog could not be read ({type(exc).__name__}). Try again.") from None
    if resp.status_code == 404:
        raise _Refused("AI Studio is not open for this organization, so the action catalog is off.")
    if resp.status_code in (401, 403):
        raise _Refused("The action catalog is not open to you.")
    if resp.status_code != 200:
        raise _Refused(
            f"The action catalog could not be read (status {resp.status_code}). Try again.")
    try:
        rows = resp.json().get("actions")
    except Exception:
        rows = None
    if not isinstance(rows, list):
        raise _Refused("The action catalog answered in a shape that this tool cannot read.")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]


def _class_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {str(r["id"]): str(r.get("class") or "") for r in rows}


# ── The result shape ────────────────────────────────────────────────────────


def _errors(errors: list[ManifestError]) -> list[dict[str, str]]:
    return [{"path": e.path, "message": e.message} for e in errors]


def _sentence(message: str, path: str = "$") -> list[dict[str, str]]:
    return [{"path": path, "message": message}]


def _result(
    ok: bool, errors: list[dict[str, str]], diff: list[str],
    manifest: dict[str, Any] | None = None, **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "errors": errors, "diff": diff}
    if ok and manifest is not None:
        out["manifest"] = manifest
    out.update(extra)
    return out


def _size(manifest: Any) -> int:
    return len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), default=repr))


def _too_big(manifest: Any) -> dict[str, Any] | None:
    """The refusal for a manifest over the cap, or None."""
    size = _size(manifest)
    if size <= MAX_MANIFEST_CHARS:
        return None
    sentence = (
        f"The manifest is {size} characters, over the limit of {MAX_MANIFEST_CHARS}. "
        "Split the app into smaller parts.")
    return _result(False, _sentence(sentence), [], size=size,
                   limit=MAX_MANIFEST_CHARS, outline=outline_text(manifest))


def _as_doc(manifest: Any) -> tuple[Any, dict[str, Any] | None]:
    """The manifest as a value. A JSON string is read. A bad string is a refusal."""
    if isinstance(manifest, str):
        try:
            return json.loads(manifest), None
        except ValueError:
            return None, _result(
                False, _sentence("The manifest text is not valid JSON. Send a JSON object."), [])
    return manifest, None


# ── The outline (pure) ──────────────────────────────────────────────────────


def _names(items: Any, key: str) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(i[key]) for i in items if isinstance(i, dict) and isinstance(i.get(key), str)]


def _line(label: str, names: list[str]) -> str:
    return f"{label} ({len(names)}): " + (", ".join(names) if names else "none")


def outline_text(manifest: Any) -> str:
    """A deterministic text of the counts and the names in *manifest*.

    Pure: the same manifest gives the same text. It reads any value and never
    raises, so it also outlines a manifest that is not valid.
    """
    if not isinstance(manifest, dict):
        return "Not a manifest."
    objects = manifest.get("objects")
    objs: list[str] = []
    if isinstance(objects, list):
        for o in objects:
            if isinstance(o, dict) and isinstance(o.get("name"), str):
                count = len(o["fields"]) if isinstance(o.get("fields"), list) else 0
                objs.append(f"{o['name']} ({count} fields)")
    skill = manifest.get("skill")
    skill_line = "Skill: none"
    if isinstance(skill, dict):
        actions = skill.get("actions")
        n = len(actions) if isinstance(actions, list) else 0
        skill_line = f"Skill: yes ({n} actions)"
    conns = manifest.get("connections")
    conn_names = [str(c) for c in conns] if isinstance(conns, list) else []
    head = f"App {manifest.get('id', '?')}: {manifest.get('name', '?')}"
    return "\n".join([
        head,
        _line("Objects", objs),
        _line("Actions", _names(manifest.get("actions"), "id")),
        _line("Views", _names(manifest.get("views"), "id")),
        _line("Workflows", _names(manifest.get("workflows"), "id")),
        skill_line,
        _line("Connections", conn_names),
    ])


# ── The draft (pure) ────────────────────────────────────────────────────────


def _slug(brief: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", brief.lower()).strip("-")
    words = re.sub(r"^[^a-z]+", "", words)[:40].strip("-")
    return words or "my-app"


def _skeleton(brief: str) -> dict[str, Any]:
    text = " ".join(brief.split())
    title = " ".join(text.split()[:5]).strip(" .,;:") or "My app"
    return {
        "studio": STUDIO_VERSION,
        "id": _slug(text),
        "name": title[:1].upper() + title[1:],
        "purpose": text[:MAX_PURPOSE_CHARS] or "To be written.",
        "connections": [],
        "objects": [],
        "actions": [],
        "views": [],
        "workflows": [],
    }


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_draft(brief: str) -> dict[str, Any]:
    """Make a minimal valid app manifest from a short brief. Fill it with studio_patch."""
    if not isinstance(brief, str) or not brief.strip():
        return _result(False, _sentence("Give a short brief of the app."), [])
    manifest = _skeleton(brief)
    errors = validate_manifest(manifest)
    if errors:
        return _result(False, _errors(errors), [])
    return _result(True, [], ["new manifest"], manifest)


async def _checked(manifest: Any, diff: list[str]) -> dict[str, Any]:
    """Validate *manifest* with the catalog, and shape the result."""
    big = _too_big(manifest)
    if big is not None:
        return big
    try:
        catalog = _class_map(await _read_catalog())
    except _Refused as exc:
        # The catalog checks could not run, so the manifest is not called valid.
        structural = _errors(validate_manifest(manifest))
        notice = _sentence(f"{exc} The action checks did not run.")
        return _result(False, [*notice, *structural], diff, catalog_checked=False)
    errors = validate_manifest(manifest, catalog=catalog)
    return _result(not errors, _errors(errors), diff, manifest)


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_validate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Check an app manifest, including its actions. Returns the errors, or the manifest."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    return await _checked(doc, [])


def _diff(ops: list[Any]) -> list[str]:
    return [f"{o.get('op')} {o.get('path')}" if isinstance(o, dict) else "?" for o in ops]


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_patch(manifest: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply JSON Patch ops (add, replace, remove) to a manifest, then check it."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    big = _too_big(doc)
    if big is not None:
        return big
    try:
        patched = apply_patch(doc, ops)
    except PatchError as exc:
        return _result(False, _sentence(str(exc)), [])
    return await _checked(patched, _diff(ops))


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_outline(manifest: dict[str, Any]) -> dict[str, Any]:
    """List the counts and names in a manifest: objects, views, workflows, skill, connections."""
    doc, bad = _as_doc(manifest)
    if bad is not None:
        return bad
    errors = validate_manifest(doc)
    return _result(not errors, _errors(errors), [], doc, outline=outline_text(doc),
                   size=_size(doc))


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"),
        "title": r.get("title"),
        "class": r.get("class"),
        "source": r.get("source"),
        "connection": r.get("connection"),
    }


@annotate(read_only=True, idempotent=True, open_world=False)
async def studio_list_actions(query: str = "") -> dict[str, Any]:
    """List the actions an app may use. The optional query filters by id and title."""
    try:
        rows = await _read_catalog()
    except _Refused as exc:
        return _result(False, _sentence(str(exc)), [])
    needle = (query or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in str(r.get("id", "")).lower() or needle in str(r.get("title", "")).lower()]
    shown = rows[:MAX_ACTION_ROWS]
    return {"ok": True, "errors": [], "diff": [], "actions": [_row(r) for r in shown],
            "total": len(rows), "shown": len(shown)}


#: The five tools, in the order of the skill family ``studio``.
STUDIO_TOOLS = (
    studio_draft,
    studio_list_actions,
    studio_outline,
    studio_patch,
    studio_validate,
)

__all__ = [
    "MAX_ACTION_ROWS", "MAX_MANIFEST_CHARS", "STUDIO_TOOLS", "outline_text",
    "studio_draft", "studio_list_actions", "studio_outline", "studio_patch", "studio_validate",
]
