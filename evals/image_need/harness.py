"""What the one assistant sees when the member attaches an image (VOX-5).

This harness subclasses the REAL one: :class:`evals.routing.harness.RoutingHarness`
drives ``run_agent_stream("orchestrator")``. So the system prompt, the
addendum and the tool list are the real ones. This module adds three things:

1. ``IMAGE_ANALYSIS_ORGS`` is on for the eval org, so ``analyze_image`` is a
   tool and its addendum text is in the prompt.
2. The upload note. The chat adds it to the typed words when a member
   attaches a file (``chatUpload.ts`` ``uploadNote``). :func:`upload_note`
   ports it, and :func:`drift` fails when the TypeScript changes.
3. A stand-in for the body of ``analyze_image``. It keeps the real name,
   docstring and signature, so the model sees the real schema. It sends no
   image, so no run draws credits. :attr:`ImageHarness.analyzed` records each
   call.

The score reads the tool calls of the reply (:func:`score`).
"""
from __future__ import annotations

import functools
import json
from typing import Any

from evals.image_need.dataset import CALL, NO_CALL, Case
from evals.routing.dataset import ORG
from evals.routing.harness import RoutingHarness, RunRecord

TOOL = "analyze_image"
#: What the stand-in answers. It is data, as the real answer is.
STAND_IN = (
    "(The image-need eval stops here: analyze_image was called and no model "
    "looked at the image. Tell the member in one line that the image is read.)"
)


def _uploads_dir(thread_id: str) -> str:
    from acb_skills.agent_paths import thread_inputs_rel

    return thread_inputs_rel(thread_id)


def upload_note(files: list[tuple[str, str]]) -> str:
    """The port of ``uploadNote(files)``: *files* are ``(name, path)`` pairs."""
    names = ", ".join(n for n, _ in files)
    paths = ", ".join(f"`{p}`" for _, p in files)
    return (f"\U0001F4CE Uploaded {len(files)} file(s): {names}\n\n"
            f"Paths: {paths}\n\nRead them with read_attachment.")


def message_for(case: Case, thread_id: str) -> str:
    """The text the chat sends: the words, a blank line, and the note."""
    path = f"{_uploads_dir(thread_id)}/{case.file_name}"
    return f"{case.words}\n\n{upload_note([(case.file_name, path)])}"


def drift() -> list[str]:
    """Each line of the port that ``chatUpload.ts`` no longer holds."""
    from evals.routing.shell import CONTROL_PLANE

    source = (CONTROL_PLANE / "chatUpload.ts").read_text(encoding="utf-8")
    wanted = {
        "the count line": "Uploaded ${files.length} file(s): ${names}\\n\\n",
        "the paths line": "`Paths: ${paths}\\n\\n`",
        "the read line": '"Read them with read_attachment."',
        "the names join": 'files.map((f) => f.name).join(", ")',
        "the paths join": 'files.map((f) => `\\`${f.path}\\``).join(", ")',
    }
    return [f"uploadNote drifted: {what} ({literal}) is not in chatUpload.ts"
            for what, literal in wanted.items() if literal not in source]


class ImageHarness(RoutingHarness):
    """The real routing harness, with the image flag and the stand-in."""

    def __init__(self, *, live: bool = False, image_on: bool = True) -> None:
        super().__init__(live=live)
        #: False turns ``IMAGE_ANALYSIS_ORGS`` off: the tool must be absent.
        self.image_on = image_on
        #: Each call of the stand-in: ``(file_name, question)``.
        self.analyzed: list[tuple[str, str]] = []

    def __enter__(self) -> ImageHarness:
        super().__enter__()
        from acb_common import get_settings
        from acb_skills import attachment_tools
        from orchestrator import _tool_injection as ti

        mp = self._mp
        mp.setattr(get_settings(), "image_analysis_orgs", ORG if self.image_on else "",
                   raising=False)
        real = attachment_tools.analyze_image
        sink = self.analyzed

        @functools.wraps(real)
        async def analyze_image(file_name: str, question: str) -> str:
            sink.append((file_name, question))
            return STAND_IN

        mp.setattr(attachment_tools, "analyze_image", analyze_image)
        ti._build_injected_tools_addendum.cache_clear()
        return self


def tool_names(body: dict[str, Any]) -> list[str]:
    """The names of the tools in one model request body."""
    names: list[str] = []
    for tool in body.get("tools") or []:
        fn = tool.get("function") or tool
        if fn.get("name"):
            names.append(fn["name"])
    return names


def calls_of(record: RunRecord) -> list[dict[str, Any]]:
    """The ``analyze_image`` calls of the reply, each with its parsed arguments."""
    out: list[dict[str, Any]] = []
    for name, args in record.tool_calls:
        if name != TOOL:
            continue
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except (ValueError, TypeError):
            parsed = {}
        out.append(parsed if isinstance(parsed, dict) else {})
    return out


def score(case: Case, calls: list[dict[str, Any]]) -> list[str]:
    """The problems of one reply. Empty means the reply is right."""
    if case.expected == NO_CALL:
        return [f"{case.id}: expected no analyze_image call, got {len(calls)}"] if calls else []
    if case.expected != CALL:
        return [f"{case.id}: unknown expected outcome {case.expected!r}"]
    if len(calls) != 1:
        return [f"{case.id}: expected exactly one analyze_image call, got {len(calls)}"]
    named = str(calls[0].get("file_name") or "")
    if named != case.file_name:
        return [f"{case.id}: analyze_image named {named!r}, not {case.file_name!r}"]
    return []
