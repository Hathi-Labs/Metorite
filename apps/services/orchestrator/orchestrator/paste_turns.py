"""The seam of a paste: ONE per-run MAF chat middleware (HRD-14b, task 3667).

Spec: ``project-docs/specs/chat_failure_modes.md`` section 4.7b, "The seam",
"The raw binding" and "Which text the middleware frames", and the row
HRD-14b of section 6. Dark behind ``CHAT_PASTE_FRAME_ORGS``
(``acb_skills.paste_frame.paste_frame_on``, empty is off). Fence:
``tests/unit/test_paste_frame_seam.py``.

``_tool_injection._apply_paste_frame`` attaches :class:`PasteFrameProvider`
right after ``_apply_steer_turns``, so :class:`PasteFrame` sees each model
request after ``steer.SteerTurns`` adds the steer turns. With the flag off it
attaches nothing, so the provider list and each request stay byte-identical.

Rules that bind:

1. **A copy, never an edit.** For each user turn that it frames, the
   middleware builds a NEW ``Message`` and puts it in the request list. It
   changes no message object, and it marks nothing. The session store and
   ``SteerTurns`` share the message objects of the run, and ``SteerTurns``
   finds its turns by identity. So the ``chat_message`` row, the payload
   ``message``, the save digest and the stored session stay raw.
2. **One token for each run** (PROPOSED, Q-HRD-14-12). The run's
   ``UntrustedFrame`` frames the same text the same way in each request, so
   a vendor that caches by prefix does not miss at each tool step.
3. **The binding is read once, at the start of the run.** ``before_run``
   takes the raw parts (``paste_frame.RAW_PARTS``), the shape that the
   composer recorded for THIS run id, the current turn of the input and the
   run's ``SteerTurns``. A run that starts inside this run cannot change them.
4. **Which turn is which, by identity.** The current turn is the last user
   message of the run input. A live steer turn is a turn that the run's
   ``SteerTurns`` placed. Each other user turn is an earlier turn, also a
   stored steer turn of an earlier run: no rule reads its mark, because
   nobody verified that the session store keeps ``additional_properties``.
5. **Unbound or string: no frame.** No raw binding (None), or a shape that is
   not ``session`` or ``list``, frames nothing on the run.
6. **Fails closed.** Each part that fails frames whole. A proof of an upload
   note that raises frames its part whole. A binding that ``before_run``
   cannot read, a turn that fails and a request that fails frame each user
   turn whole, with its other parts and its properties kept. Only when even
   that fails does the request go as it came: the seam never fails a model
   request, so the chat turn goes on.

WARNING: the frame is advice to the model, and no test can prove that a
model obeys it.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from acb_common import get_logger
from acb_skills import paste_frame as pf
from acb_skills.untrusted import UntrustedFrame
from agent_framework import ChatMiddleware, ContextProvider, Message

from orchestrator.steer import STEER_TURNS_KEY, steers_frame

_log = get_logger("orchestrator.paste_turns")

#: The ``source_id`` of :class:`PasteFrameProvider`. MAF keys provider state on it.
SOURCE_ID = "metorite-paste-frame"

#: The shapes on which the seam frames (spec section 4.7b, the table).
FRAMED_SHAPES = frozenset({"session", "list"})

#: The prefix that ``steer.buffer_guidance`` writes before a live steer.
_STEER_HEAD = re.compile(r"\A\[steer from [^\]\n]*\] ")
#: The ``steer.steers_frame`` line at the end of the newest steer turn. Built
#: from the function, so the words live in one place.
_COUNT = "1234567"
_FRAME_LINE = re.compile(
    "\n" + re.escape(steers_frame(int(_COUNT))).replace(_COUNT, r"\d+") + r"\Z")

#: The async proof of the paths of an upload note.
Prover = Callable[[tuple[str, ...]], Awaitable[bool]]


class _ProofFailed(Exception):
    """The proof of an upload note raised. The part then frames whole."""


def _role(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


def _texts(message: Any) -> list[Any]:
    return [c for c in (getattr(message, "contents", None) or [])
            if getattr(c, "type", None) == "text"]


def steer_text_span(raw: str) -> tuple[int, int]:
    """``(start, end)`` of the member's words in a live steer turn *raw*: after
    the ``[steer from ...]`` prefix, and before a ``steers_frame`` line."""
    head = _STEER_HEAD.match(raw)
    start = head.end() if head else 0
    tail = _FRAME_LINE.search(raw, start)
    return start, tail.start() if tail else len(raw)


class PasteFrame(ChatMiddleware):
    """Frame each pasted block of the member's turns in a COPY of each request.

    One object serves ONE run. :class:`PasteFrameProvider` makes it."""

    def __init__(
        self,
        *,
        parts: tuple[object, ...] | None,
        shape: str | None,
        current: Any = None,
        steer_turns: Any = None,
        prove: Prover | None = None,
        frame: UntrustedFrame | None = None,
        whole: bool = False,
    ) -> None:
        #: Fail closed (rule 6): the binding could not be read, so each user
        #: turn of each request is ONE block, with no lead.
        self._whole = whole
        self._parts = parts
        self._shape = shape
        self._current = current
        self._steers = steer_turns
        self._prove = prove
        #: ONE token for the run (rule 2).
        self._frame = frame or UntrustedFrame.new()
        #: The proof of each note: True, False, or None when the proof raised.
        self._proofs: dict[tuple[str, ...], bool | None] = {}
        #: ``(kind, raw text)`` to its framed text, so each request of the run
        #: gives the same bytes with no second pass.
        self._memo: dict[tuple[str, str], str] = {}
        self._logged = False

    @property
    def active(self) -> bool:
        """True when the run is bound and its shape is framed (rule 5), or
        when the seam frames each turn whole (rule 6)."""
        return self._whole or (self._parts is not None and self._shape in FRAMED_SHAPES)

    # -- the proof of an upload note ------------------------------------------

    def _paths_exist(self, paths: tuple[str, ...]) -> bool:
        if paths in self._proofs and self._proofs[paths] is None:
            raise _ProofFailed("the proof of the upload note raised")
        return self._proofs.get(paths) is True

    def _note_texts(self, messages: Sequence[Any]) -> list[str]:
        out = [p.strip() for p in (self._parts or ()) if isinstance(p, str)]
        for message in messages:
            if _role(message) != "user":
                continue
            for content in _texts(message):
                raw = str(getattr(content, "text", "") or "")
                out.append(raw)
                start, end = steer_text_span(raw)
                out.append(raw[start:end])
        return out

    async def _prove_notes(self, messages: Sequence[Any]) -> None:
        """Prove each new upload note of the request once for the run."""
        for text in self._note_texts(messages):
            paths = pf.note_paths(text)
            if paths is None or paths in self._proofs:
                continue
            if self._prove is None:
                self._proofs[paths] = False
                continue
            try:
                self._proofs[paths] = (await self._prove(paths)) is True
            except Exception:
                _log.warning("paste_frame.proof_failed")
                self._proofs[paths] = None

    # -- the rules for one text -----------------------------------------------

    def _framed_text(self, kind: str, raw: str) -> str:
        key = (kind, raw)
        if key in self._memo:
            return self._memo[key]
        frame, exist = self._frame, self._paths_exist
        try:
            if kind == "current":
                out = pf.frame_current_turn(raw, self._parts or (), frame, paths_exist=exist)
            elif kind == "current-many":
                # A current turn of two or more texts: no part is placed, so
                # each text is one block when a raw part is long (rule 6).
                parts = self._parts or ()
                long_part = any(not isinstance(p, str) or pf.is_long(p) for p in parts)
                out = pf.frame_whole(raw, frame) if long_part else raw
            elif kind == "steer":
                start, end = steer_text_span(raw)
                out = (raw[:start] + pf.frame_part(raw[start:end], frame, paths_exist=exist)
                       + raw[end:])
            else:
                out = pf.frame_earlier_turn(raw, frame, paths_exist=exist)
        except Exception as exc:  # fail closed: the whole text is one block
            _log.warning("paste_frame.text_failed", kind=kind, error=type(exc).__name__)
            out = pf.frame_whole(raw, frame)
        self._memo[key] = out
        return out

    def _copy(self, message: Any, kind: str) -> Any:
        """*message*, or a NEW message with its text framed. Never an edit."""
        contents = list(getattr(message, "contents", None) or [])
        changed = False
        new_contents: list[Any] = []
        for content in contents:
            if getattr(content, "type", None) != "text":
                new_contents.append(content)
                continue
            raw = str(getattr(content, "text", "") or "")
            framed = self._framed_text(kind, raw)
            if framed == raw:
                new_contents.append(content)
            else:
                changed = True
                new_contents.append(framed)
        if not changed:
            return message
        return Message(
            "user", new_contents,
            author_name=getattr(message, "author_name", None),
            message_id=getattr(message, "message_id", None),
            additional_properties=dict(getattr(message, "additional_properties", None) or {}),
        )

    def _whole_copy(self, message: Any) -> Any:
        """A NEW message with each text of *message* as ONE block, with no
        lead. Each other part (an image, a file) and the properties stay."""
        new_contents: list[Any] = []
        for content in list(getattr(message, "contents", None) or []):
            if getattr(content, "type", None) != "text":
                new_contents.append(content)
                continue
            raw = str(getattr(content, "text", "") or "")
            key = ("whole", raw)
            if key not in self._memo:
                self._memo[key] = pf.frame_whole(raw, self._frame)
            new_contents.append(self._memo[key])
        return Message(
            "user", new_contents,
            author_name=getattr(message, "author_name", None),
            message_id=getattr(message, "message_id", None),
            additional_properties=dict(getattr(message, "additional_properties", None) or {}),
        )

    def _all_whole(self, messages: Sequence[Any]) -> list[Any]:
        """*messages* with each user turn that holds text as ONE block."""
        return [self._whole_copy(m) if _role(m) == "user" and _texts(m) else m
                for m in messages]

    def _current_index(self, messages: Sequence[Any], placed: set[int]) -> int:
        for i, message in enumerate(messages):
            if self._current is not None and message is self._current:
                return i
        # The input object is not in the request: the last user turn that the
        # run did not place as a steer.
        for i in range(len(messages) - 1, -1, -1):
            if _role(messages[i]) == "user" and id(messages[i]) not in placed:
                return i
        return -1

    def framed(self, messages: Sequence[Any]) -> list[Any]:
        """A NEW list: *messages* with each user turn framed by its rule. The
        proofs of the upload notes must be in place (:meth:`process`)."""
        if self._whole:
            return self._all_whole(messages)
        placed = {id(turn) for _anchor, turn in getattr(self._steers, "_placed", ()) or ()}
        current = self._current_index(messages, placed)
        out: list[Any] = []
        for i, message in enumerate(messages):
            if _role(message) != "user" or not _texts(message):
                out.append(message)
                continue
            if i == current:
                kind = "current" if len(_texts(message)) == 1 else "current-many"
            elif id(message) in placed:
                kind = "steer"
            else:
                kind = "earlier"
            try:
                out.append(self._copy(message, kind))
            except Exception as exc:  # fail closed: the whole turn is one block
                _log.warning("paste_frame.turn_failed", error=type(exc).__name__)
                out.append(self._whole_copy(message))
        return out

    async def process(self, context: Any, call_next: Any) -> None:
        if self.active:
            try:
                messages = list(context.messages or [])
                await self._prove_notes(messages)
                framed = self.framed(messages)
                if not self._logged:
                    self._logged = True
                    blocks = sum(1 for a, b in zip(messages, framed, strict=True) if a is not b)
                    _log.info("paste_frame.framed", turns=blocks, shape=self._shape,
                              parts=len(self._parts or ()))
                context.messages = framed
            except Exception as exc:
                # Fail closed (rule 6): each user turn is one block. If even
                # that fails, the request goes as it came: the seam never
                # fails a model request, so the chat turn goes on.
                _log.warning("paste_frame.request_failed", error=type(exc).__name__)
                try:
                    context.messages = self._all_whole(list(context.messages or []))
                except Exception as again:
                    _log.warning("paste_frame.whole_failed", error=type(again).__name__)
        await call_next()


def _current_turn(context: Any) -> Any:
    """The last user message of the run input, or None."""
    for message in reversed(list(getattr(context, "input_messages", None) or [])):
        if _role(message) == "user":
            return message
    return None


class PasteFrameProvider(ContextProvider):
    """Put a NEW :class:`PasteFrame` on each ``agent.run`` of a native agent.

    ``_tool_injection._apply_paste_frame`` attaches it as a per-run view,
    right after ``steer.SteerTurnsProvider``, so the shared agent object never
    holds it. It adds no tool, no instruction and no message of its own."""

    def __init__(self, prove: Prover | None = None) -> None:
        super().__init__(SOURCE_ID)
        self._prove = prove

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        """Read the binding of the run ONCE. It fails closed (rule 6): when the
        read raises, the run gets a middleware that frames each user turn
        whole, and the seam logs once. It never fails the run."""
        try:
            prove = self._prove
            if prove is None:
                from acb_skills.attachment_tools import upload_paths_exist

                prove = upload_paths_exist
            from acb_skills.write_artifact import artifact_context

            run_id = str(artifact_context().get("run_id") or "")
            metadata = getattr(context, "metadata", None) or {}
            middleware = PasteFrame(
                parts=pf.raw_parts(),
                shape=pf.shape_of(run_id),
                current=_current_turn(context),
                steer_turns=metadata.get(STEER_TURNS_KEY),
                prove=prove,
            )
        except Exception as exc:
            _log.warning("paste_frame.bind_failed", error=type(exc).__name__)
            middleware = PasteFrame(parts=(), shape="list", whole=True)
        try:
            context.extend_middleware(self.source_id, [middleware])
        except Exception as exc:  # the run goes on with no seam
            _log.warning("paste_frame.attach_failed", error=type(exc).__name__)
