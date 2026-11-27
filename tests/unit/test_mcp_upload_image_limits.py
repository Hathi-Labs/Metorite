"""CIV-14b — ``upload_task_image``: the limits, the form and the images input.

Spec: ``project-docs/specs/external_agent_access.md`` §12.9 (scope items 1,
2 and 5) and §15 row CIV-14b.

**Database-free, through the real edge app.** The edge app runs in process
with the fake token store and the fake Redis of
``test_mcp_edge_task_writes.py``. :class:`_Upload` is a fake gateway that
reads the multipart form of the upload, and records each request.
``test_mcp_upload_image_types.py`` holds the type rules, and the two R8 files
hold the same rules on a real database, through branch 0.

Run::

    uv run pytest tests/unit/test_mcp_upload_image_limits.py -v -rs

**The mutations (R7).** ``E`` is ``apps/services/agent_edge/agent_edge``.

* L1. ``E/tools.py`` ``decode_image``: no size check.
  ``test_l1_one_byte_over_the_cap_is_refused_with_no_request``.
* L2. ``E/tools.py`` ``decode_image``: ``validate=False``.
  ``test_l2_bad_base64_is_refused_with_no_request``.
* L3. ``E/mcp.py`` ``_serve_body``: no check of the tool of a large body.
  ``test_l3_a_large_body_of_another_call_is_413``.
* L4. ``E/mcp.py`` ``_take_large_slot``: no count.
  ``test_l4_a_fifth_large_body_at_once_is_503``.
* L5. ``gateway/routes/projects/agent_upload_body.py``: no copy in the
  scope. ``test_l5_the_wrapper_keeps_the_signed_body_for_branch_0``.
* L6 (fix round 1, item 4). ``agent_upload_body.py``: keep the copy at the
  first part. ``test_l6_a_signed_form_in_many_parts_verifies_over_every_part``.

Fix round 1 adds the classes ``TestALargeBodyWaitsForItsToken`` (item 1),
``TestALargeBodyIsCountedBeforeTheParse`` (item 2),
``TestTheTypesThatTakeImages`` (item 5), ``TestTheFlagHidesTheImages``
(item 7) and ``TestAQuestionChecksItsImagesFirst`` (item 8). Each test names
the mutation that turns it red.

Fix round 2 adds the classes ``TestAnOrgOffTheFlagKeepsTheOldCap`` (item A),
``TestARefusedQuestionLeavesNoOpenRun`` (item B) and
``TestTheFlagReaderSaysWhoReadsIt`` (item C). Fix round 3 adds the tests
``test_b6`` (a cap answer keeps the run), ``test_b7`` (a question that never
left the edge) and ``test_b8`` and ``test_b9`` (the key of an ended run) to
``TestARefusedQuestionLeavesNoOpenRun``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_signing
from agent_edge import gateway_client, mcp, tools

from tests.unit.test_mcp_edge_attachment import JPEG, TINY_PNG
from tests.unit.test_mcp_edge_task_writes import edge, redis_fake  # noqa: F401 — fixtures
from tests.unit.test_mcp_edge_writes import (
    GOOD,
    ISSUER,
    TASK,
    _call,
    _text,
    _tool,
    _uuid,
)

UPLOAD_PATH = f"/projects/tasks/{TASK}/attachments"
START_PATH = f"/projects/tasks/{TASK}/agent-runs"
RUN = _uuid(7701)
RELEASE_PATH = f"/projects/agent-runs/{RUN}/release"
IMG_A, IMG_B = _uuid(9801), _uuid(9802)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def png_of(size: int) -> bytes:
    """A PNG signature and IHDR, then zero bytes up to *size*. The sniff reads
    the first bytes only, so this is an image for every rule of the edge."""
    head = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108020000009077" "53de")
    return head + b"\x00" * (size - len(head))


def parts(body: bytes, content_type: str) -> list[dict[str, Any]]:
    """The parts of a multipart body: the name, the file name, the type and
    the bytes of each. A test parser, strict on the shape of the edge."""
    boundary = re.search(r"boundary=([0-9a-f]{32})$", content_type)
    assert boundary, content_type
    marker = b"--" + boundary.group(1).encode()
    assert body.endswith(marker + b"--\r\n")
    out = []
    for chunk in body[: -len(marker + b"--\r\n")].split(marker)[1:]:
        head, _, data = chunk.partition(b"\r\n\r\n")
        assert data.endswith(b"\r\n")
        text_ = head.decode("ascii")
        name = re.search(r'name="([^"]+)"', text_)
        filename = re.search(r'filename="([^"]*)"', text_)
        kind = re.search(r"Content-Type: (\S+)", text_)
        out.append({"name": name and name.group(1), "filename": filename and filename.group(1),
                    "type": kind and kind.group(1), "data": data[:-2]})
    return out


class _Upload:
    """The fake gateway: the upload, the list of a task and the activity POST."""

    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.answer: tuple[int, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self.activity_status = 201
        # Fix round 2, item B: the start of `ask_on_task` found an open run
        # (False), and the answer of the release.
        self.start_created = True
        # Fix round 3, item 3: the status of the run that the start gives.
        self.start_status = "in_progress"
        self.release_answer: tuple[int, Any] = (
            200, {"run": {**_run_row(), "status": "released"}, "released": True})

    async def __call__(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request = SimpleNamespace(method=scope["method"], path=scope["path"],
                                  headers=headers, body=body,
                                  query=scope["query_string"].decode("ascii"))
        self.requests.append(request)
        status, payload = 404, {"detail": "Not Found"}
        if scope["method"] == "POST" and scope["path"] == UPLOAD_PATH:
            if self.answer is not None:
                status, payload = self.answer
            else:
                (part,) = parts(body, headers["content-type"])
                row = {"attachment_id": _uuid(9900 + len(self.rows)), "name": part["filename"],
                       "mime": part["type"], "size": len(part["data"]),
                       "added_by": "agent-member@mcpwrites.test", "kind": "image",
                       "url": f"/api/projects/attachments/x/{part['filename']}"}
                self.rows.append({**row, "created_at": "2026-11-12T00:00:00+00:00"})
                status, payload = 201, row
        elif scope["method"] == "GET" and scope["path"] == UPLOAD_PATH:
            status, payload = 200, {"rows": self.rows, "total": len(self.rows)}
        elif scope["method"] == "POST" and scope["path"] == START_PATH:
            # The start of the run of `ask_on_task` (fix round 1, item 8).
            started = {**_run_row(), "status": self.start_status}
            status, payload = ((201, {"run": started, "created": True}) if self.start_created
                               else (200, {"run": started, "created": False}))
        elif scope["method"] == "POST" and scope["path"] == RELEASE_PATH:
            status, payload = self.release_answer
        elif scope["method"] == "POST" and scope["path"] == f"/projects/agent-runs/{RUN}/activities":
            sent = json.loads(body)
            activity = {"id": _uuid(7801), "run_id": RUN, "task_id": TASK, "seq": 2,
                        "type": sent["type"], "content": {"body": sent.get("body")},
                        "content_metadata": None, "ephemeral": False, "signal": None,
                        "signal_metadata": None, "actor": "agent-member@mcpwrites.test",
                        "actor_via": "mcp:client.example:meta", "in_reply_to": None,
                        "pm_activity_id": None, "idempotency_key": sent["idempotency_key"],
                        "created_at": "2026-11-12T00:00:00+00:00"}
            status, payload = self.activity_status, {"activity": activity, "run": _run_row(),
                                                     "created": True}
            if self.answer is not None:
                status, payload = self.answer
        raw = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": raw})

    def uploads(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method == "POST" and r.path == UPLOAD_PATH]

    def starts(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method == "POST" and r.path == START_PATH]

    def releases(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method == "POST" and r.path == RELEASE_PATH]


def _run_row() -> dict[str, Any]:
    return {"id": RUN, "task_id": TASK, "status": "in_progress",
            "member_email": "agent-member@mcpwrites.test",
            "client_via": "mcp:client.example:meta", "started_at": None,
            "updated_at": None}


@pytest.fixture
def gw(edge, monkeypatch):  # noqa: F811 — a fixture
    """The fake edge, with the fake upload gateway."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    # Fix round 1, item 7: the edge offers the images only to an organization
    # of the flag. The gateway reads it too, and the R8 files hold that half.
    monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "*")
    fake = _Upload()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield fake


def _args(data: bytes, *, name: str = "capture.png", key: str = "img-key-0001",
          raw: str | None = None) -> dict[str, Any]:
    return {"task_id": TASK, "filename": name, "idempotency_key": key,
            "content_base64": _b64(data) if raw is None else raw}


async def _upload(args: dict[str, Any]) -> dict[str, Any]:
    return await _tool("upload_task_image", args)


class TestTheSize:
    """Scope item 1: at most 3.75 MB after the decode."""

    async def test_an_image_of_exactly_the_cap_goes_out_whole(self, gw):
        data = png_of(tools.UPLOAD_IMAGE_MAX_BYTES)
        result = await _upload(_args(data))
        assert result["isError"] is False, result
        (sent,) = gw.uploads()
        (part,) = parts(sent.body, sent.headers["content-type"])
        assert part["data"] == data and part["type"] == "image/png"
        assert result["structuredContent"]["attachment"]["size"] == len(data)

    async def test_l1_one_byte_over_the_cap_is_refused_with_no_request(self, gw):
        result = await _upload(_args(png_of(tools.UPLOAD_IMAGE_MAX_BYTES + 1)))
        assert _text(result) == tools.TEXT_IMAGE_TOO_LARGE
        assert gw.requests == []

    def test_the_cap_is_the_one_of_get_attachment(self):
        assert tools.UPLOAD_IMAGE_MAX_BYTES == tools.IMAGE_MAX_BYTES == 3_932_160
        assert agent_oauth.AGENT_IMAGE_MAX_BYTES == tools.IMAGE_MAX_BYTES

    async def test_a_string_over_the_schema_cap_is_refused_before_the_decode(self, gw):
        result = await _upload(_args(b"", raw="A" * (tools.UPLOAD_BASE64_MAX + 1)))
        assert _text(result) == "The input 'content_base64' is too long."
        assert gw.requests == []


class TestTheBase64:
    """Scope item 1: the edge decodes, and refuses an empty file and bad base64."""

    @pytest.mark.parametrize("raw", ["   ", "\r\n\t"], ids=["spaces", "breaks"])
    async def test_an_empty_file_is_refused_with_no_request(self, gw, raw):
        result = await _upload(_args(b"", raw=raw))
        assert _text(result) == tools.TEXT_IMAGE_EMPTY
        assert gw.requests == []

    @pytest.mark.parametrize("raw", [
        "not base64 at all!",
        "data:image/png;base64," + _b64(TINY_PNG),
        _b64(TINY_PNG)[:-2],            # the padding is cut
        _b64(TINY_PNG).replace("+", "-").replace("/", "_") + "-_",  # url-safe letters
        "é" + _b64(TINY_PNG),
    ], ids=["words", "data_url", "padding", "url_safe", "non_ascii"])
    async def test_l2_bad_base64_is_refused_with_no_request(self, gw, raw):
        result = await _upload(_args(b"", raw=raw))
        assert _text(result) == tools.TEXT_IMAGE_BAD_BASE64
        assert gw.requests == []

    async def test_base64_with_line_breaks_decodes(self, gw):
        data = png_of(5000)
        wrapped = "\n".join(_b64(data)[i:i + 76] for i in range(0, len(_b64(data)), 76))
        result = await _upload(_args(b"", raw=wrapped + "\r\n"))
        assert result["isError"] is False, result
        (sent,) = gw.uploads()
        assert parts(sent.body, sent.headers["content-type"])[0]["data"] == data

    def test_a_refusal_comes_before_the_slot(self):
        """A refused file never takes an idempotency key: the decode and the
        type check run before ``_claim_slot`` in the source."""
        import inspect

        source = inspect.getsource(tools._upload_task_image)
        assert source.index("decode_image(") < source.index("_claim_slot(")
        assert source.index("upload_type(") < source.index("_claim_slot(")


class TestTheForm:
    """The one multipart write of the gateway client seam."""

    async def test_the_form_has_one_part_file_and_the_signature_covers_it(self, gw):
        result = await _upload(_args(TINY_PNG, name="shot one.png"))
        assert result["isError"] is False, result
        (sent,) = gw.uploads()
        assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
        (part,) = parts(sent.body, sent.headers["content-type"])
        assert part == {"name": "file", "filename": "shot one.png", "type": "image/png",
                        "data": TINY_PNG}
        # The gateway's own check, run on the bytes that the fake received.
        org, _, conn = sent.headers["x-agent-connection"].partition(".")
        canonical = agent_signing.canonical_string(
            timestamp=sent.headers["x-agent-timestamp"], organization_id=org,
            connection_id=conn, actor_via=sent.headers["x-actor-via"], method="POST",
            path=UPLOAD_PATH, query="", body=sent.body,
            nonce=sent.headers["x-agent-nonce"])
        assert agent_signing.verify(sent.headers["x-agent-signature"], canonical,
                                    agent_signing.usable_keys())
        assert "authorization" not in sent.headers

    async def test_the_result_holds_no_url_and_the_name_as_untrusted_text(self, gw):
        result = await _upload(_args(TINY_PNG))
        body = result["structuredContent"]
        assert set(body) == set(tools.UPLOAD_KEYS)
        assert body["created"] is True
        assert body["attachment"]["name"] == {"untrusted_text": "capture.png"}
        assert "url" not in body["attachment"] and "/api/" not in json.dumps(result)

    def test_the_client_refuses_a_name_with_a_quote_and_a_type_that_is_not_an_image(self):
        lookup = SimpleNamespace()
        for name, kind in (('a".png', "image/png"), ("a\r\nX: y.png", "image/png"),
                           ("a.png", "text/html"), ("a.svg", "image/svg+xml")):
            with pytest.raises(gateway_client.RouteRefused):
                asyncio.run(gateway_client.write_file(
                    "k" * 64, lookup, path_params={"task_id": TASK}, filename=name,
                    content=TINY_PNG, content_type=kind))

    @pytest.mark.parametrize("name", ["../etc/passwd.png", "a/b.png", ".hidden.png", "a\x00.png"])
    async def test_a_path_or_a_control_character_in_the_name_is_refused(self, gw, name):
        result = await _upload(_args(TINY_PNG, name=name))
        assert result["isError"] is True
        assert gw.requests == []


class TestTheKey:
    """The idempotency slot: one image for each key."""

    async def test_the_same_key_gives_the_first_image_and_uploads_nothing(self, gw):
        first = await _upload(_args(TINY_PNG))
        again = await _upload(_args(TINY_PNG))
        assert first["structuredContent"]["created"] is True
        assert again["structuredContent"]["created"] is False
        assert (again["structuredContent"]["attachment"]["attachment_id"]
                == first["structuredContent"]["attachment"]["attachment_id"])
        assert len(gw.uploads()) == 1

    async def test_the_same_key_with_other_bytes_is_refused(self, gw):
        await _upload(_args(TINY_PNG))
        result = await _upload(_args(JPEG))
        assert _text(result) == tools.TEXT_UPLOAD_KEY_REUSED
        assert len(gw.uploads()) == 1

    async def test_the_slot_holds_no_copy_of_the_file(self, gw, edge):  # noqa: F811
        await _upload(_args(TINY_PNG))
        held = json.dumps(edge.redis.__dict__, default=str)
        assert _b64(TINY_PNG) not in held


class TestTheGatewayRefusals:
    """Each refusal of the route has a fixed text. A refusal frees the key."""

    @pytest.mark.parametrize("status,detail,text_", [
        (404, agent_oauth.IMAGE_UPLOAD_OFF, tools.TEXT_IMAGES_OFF),
        (404, "Task not found", tools.TEXT_TASK_NOT_FOUND),
        (413, "This task's attachments would reach 11.0 MB", tools.TEXT_IMAGE_BUDGET),
        # Fix round 1, item 9. Mutation: drop the `(413, AGENT_IMAGE_TOO_LARGE)`
        # entry of `tools._post_image`, and this reads the budget text.
        (413, agent_oauth.AGENT_IMAGE_TOO_LARGE, tools.TEXT_IMAGE_TOO_LARGE),
        (415, "SECRET", tools.TEXT_IMAGE_TYPE),
        (400, "File type not allowed: a.exe", tools.TEXT_IMAGE_NAME),
        (409, "Someone else changed these tasks at the same moment. Try again.",
         tools.TEXT_UPLOAD_RACE),
    ], ids=["flag_off", "hidden_task", "budget", "agent_cap", "type", "name", "race"])
    async def test_each_refusal_and_then_the_key_is_free(self, gw, status, detail, text_):
        gw.answer = (status, {"detail": detail})
        result = await _upload(_args(TINY_PNG))
        assert _text(result) == text_
        assert detail not in json.dumps(result) or detail == "Task not found"
        gw.answer = None
        again = await _upload(_args(TINY_PNG))
        assert again["isError"] is False and again["structuredContent"]["created"] is True


class TestTheImagesOfAnActivity:
    """Scope item 2: ``attachment_ids`` on ``post_agent_activity`` and ``ask_on_task``."""

    def test_the_cap_is_four_in_both_schemas(self):
        for tool in (tools.POST_AGENT_ACTIVITY, tools.ASK_ON_TASK):
            rule = tool.input_schema["properties"]["attachment_ids"]
            assert rule["maxItems"] == agent_oauth.ACTIVITY_IMAGES_MAX == 4
            assert rule["minItems"] == 1
            assert rule["items"]["pattern"] == tools._UUID_PATTERN
            assert tool.params["attachment_ids"] == "attachment_ids"

    def test_the_gateway_body_takes_four_ids_at_most(self):
        from gateway.routes.projects.agent_runs import ActivityIn
        from pydantic import ValidationError

        ok = ActivityIn(type="thought", idempotency_key="k", body="b",
                        attachment_ids=[IMG_A, IMG_B, _uuid(1), _uuid(2)])
        assert len(ok.attachment_ids or []) == 4
        for bad in ([IMG_A] * 5, [], ["not-an-id"], [12], "x" * 10_000):
            with pytest.raises(ValidationError):
                ActivityIn(type="thought", idempotency_key="k", body="b", attachment_ids=bad)

    @pytest.mark.parametrize("ids", [[IMG_A] * 5, ["nope"], []], ids=["five", "shape", "none"])
    async def test_a_bad_list_is_refused_with_no_request(self, gw, ids):
        result = await _tool("post_agent_activity", {
            "run_id": RUN, "type": "thought", "body": "Look", "idempotency_key": "a-1",
            "attachment_ids": ids})
        assert result["isError"] is True
        assert gw.requests == []

    async def test_the_ids_go_out_in_lower_case(self, gw):
        result = await _tool("post_agent_activity", {
            "run_id": RUN, "type": "thought", "body": "Two captures.",
            "idempotency_key": "a-2", "attachment_ids": [IMG_A.upper(), IMG_B]})
        assert result["isError"] is False, result
        (sent,) = [r for r in gw.requests if r.path.endswith("/activities")]
        assert json.loads(sent.body)["attachment_ids"] == [IMG_A, IMG_B]

    @pytest.mark.parametrize("kind", ["response", "error"])
    async def test_a_response_or_an_error_takes_no_images(self, gw, kind):
        """Fix round 1, item 5: a response or an error becomes a comment, and
        a comment draws no image. Mutation: drop the type check of
        ``tools._activity_payload``, and the call goes out."""
        result = await _tool("post_agent_activity", {
            "run_id": RUN, "type": kind, "body": "Done.", "idempotency_key": "a-5",
            "attachment_ids": [IMG_A]})
        assert _text(result) == tools.TEXT_ATTACHMENTS_NOT_ON_TYPE
        assert gw.requests == []

    @pytest.mark.parametrize("kind,extra", [
        ("thought", {}), ("action", {"action": "run_tests"}),
        ("elicitation", {"options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}),
    ])
    async def test_a_thought_an_action_or_an_elicitation_takes_images(self, gw, kind, extra):
        body = {} if kind == "action" else {"body": "Look"}
        result = await _tool("post_agent_activity", {
            "run_id": RUN, "type": kind, "idempotency_key": f"a-6-{kind}",
            "attachment_ids": [IMG_A], **body, **extra})
        assert result["isError"] is False, result

    @pytest.mark.parametrize("code,text_", [
        (agent_oauth.IMAGE_UPLOAD_OFF, tools.TEXT_IMAGES_OFF),
        ("attachment_not_on_task", tools.TEXT_ATTACHMENT_NOT_ON_TASK),
        ("attachment_not_image", tools.TEXT_ATTACHMENT_NOT_IMAGE),
        ("attachments_not_on_type", tools.TEXT_ATTACHMENTS_NOT_ON_TYPE),
    ])
    async def test_each_coded_422_has_a_fixed_text(self, gw, code, text_):
        gw.answer = (422, {"detail": {"error": code, "message": "SECRET"}})
        result = await _tool("post_agent_activity", {
            "run_id": RUN, "type": "thought", "body": "Look", "idempotency_key": "a-3",
            "attachment_ids": [IMG_A]})
        assert _text(result) == text_
        assert "SECRET" not in json.dumps(result)

    def test_the_codes_are_the_gateways(self):
        from gateway.routes.projects import agent_runs

        assert tools.ATTACHMENT_NOT_ON_TASK_CODE == agent_runs.ATTACHMENT_NOT_ON_TASK
        assert tools.ATTACHMENT_NOT_IMAGE_CODE == agent_runs.ATTACHMENT_NOT_IMAGE
        assert tools.ATTACHMENTS_NOT_ON_TYPE_CODE == agent_runs.ATTACHMENTS_NOT_ON_TYPE


class TestTheTypesThatTakeImages:
    """Fix round 1, item 5: the gateway half. Only a thought, an action or an
    elicitation carries ``attachment_ids``, because the card draws no image
    of a response, an error or a prompt."""

    @staticmethod
    def _payload(kind: str) -> Any:
        from gateway.routes.projects.agent_runs import ActivityIn

        extra: dict[str, Any] = {"action": "run_tests"} if kind == "action" else {}
        return ActivityIn(type=kind, idempotency_key="k", body="b",
                          attachment_ids=[IMG_A], **extra)

    @pytest.mark.parametrize("kind", ["response", "error", "prompt"])
    def test_each_other_type_is_a_coded_422(self, kind):
        """Mutation: make the rule of ``agent_runs._validate_fields`` read
        ``kind == "prompt"`` again, and the response and the error pass. Drop
        it, and the prompt passes too."""
        from fastapi import HTTPException
        from gateway.routes.projects import agent_runs

        with pytest.raises(HTTPException) as refused:
            agent_runs._validate_fields(self._payload(kind))
        assert refused.value.status_code == 422
        assert refused.value.detail["error"] == agent_runs.ATTACHMENTS_NOT_ON_TYPE

    @pytest.mark.parametrize("kind", agent_oauth.IMAGE_ACTIVITY_TYPES)
    def test_a_thought_an_action_and_an_elicitation_pass(self, kind):
        from gateway.routes.projects import agent_runs

        agent_runs._validate_fields(self._payload(kind))

    def test_the_types_are_the_ones_the_card_draws(self):
        assert agent_oauth.IMAGE_ACTIVITY_TYPES == ("thought", "action", "elicitation")


class TestTheFlagHidesTheImages:
    """Fix round 1, item 7. The edge offers ``upload_task_image`` and the
    images input only to an organization of ``mcp_image_upload_orgs``. The
    gateway refusal stays the second wall (the R8 file holds it)."""

    async def _listed(self) -> dict[str, Any]:
        r = await _post_raw(json.dumps({"jsonrpc": "2.0", "id": 1,
                                        "method": "tools/list"}).encode())
        assert r.status_code == 200, r.text
        return {t["name"]: t for t in r.json()["result"]["tools"]}

    @pytest.mark.parametrize("orgs", ["", "99999999-2222-4333-8444-555555555555"],
                             ids=["empty", "another_org"])
    async def test_off_the_flag_no_tool_no_input_and_no_sentence(self, gw, monkeypatch, orgs):
        """Mutation: drop the ``tool.images`` check of ``tools.offered``, and
        the tool shows. Make ``Tool.describe`` ignore the organization, and
        the input shows."""
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", orgs)
        listed = await self._listed()
        assert "upload_task_image" not in listed
        for name in ("ask_on_task", "post_agent_activity"):
            assert "attachment_ids" not in listed[name]["inputSchema"]["properties"], name
            assert "upload_task_image" not in listed[name]["description"], name
            assert "attachment_ids" not in listed[name]["description"], name
            assert "[[" not in listed[name]["description"], name
        # The list read the member's delete switch. The call sends nothing.
        before = len(gw.requests)
        result = await _upload(_args(TINY_PNG))
        assert _text(result) == tools.TEXT_IMAGES_OFF
        assert len(gw.requests) == before

    async def test_on_the_flag_the_tool_the_input_and_the_sentence_show(self, gw, monkeypatch):
        from acb_common import get_settings

        from tests.unit.test_mcp_edge_writes import ORG

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", ORG)
        listed = await self._listed()
        assert "upload_task_image" in listed
        for name in ("ask_on_task", "post_agent_activity"):
            assert "attachment_ids" in listed[name]["inputSchema"]["properties"], name
            assert "upload_task_image" in listed[name]["description"], name
            assert "[[" not in listed[name]["description"], name
        assert "never with a response or an error" in listed["post_agent_activity"]["description"]


class TestTheFlagReaderSaysWhoReadsIt:
    """Fix round 2, item C. Since fix round 1 the edge reads the flag too, and
    the edge reads its settings at start. So the docstring of the one reader
    names the edge and its restart, as ``docs/flag_reload.md`` does."""

    def test_c1_the_docstring_names_both_units_and_the_edge_restart(self):
        """Mutation: put back "Only the gateway reads it" in the docstring of
        ``agent_oauth.image_upload_allowed``, and this test fails."""
        import pathlib

        edge_dir = pathlib.Path(tools.__file__).parent
        readers = sorted(p.name for p in edge_dir.glob("*.py")
                         if "agent_oauth.image_upload_allowed(" in p.read_text(encoding="utf-8"))
        assert readers == ["mcp.py", "tools.py"]
        doc = " ".join((agent_oauth.image_upload_allowed.__doc__ or "").split())
        assert "Only the gateway reads it" not in doc
        for words in ("The gateway asks it", "The agent edge asks it too",
                      "restarts the agent edge", "docs/flag_reload.md"):
            assert words in doc, words
        repo = pathlib.Path(__file__).resolve().parents[2]
        reload_doc = (repo / "docs" / "flag_reload.md").read_text(encoding="utf-8")
        assert "restart `acb-agent-edge`" in reload_doc


class TestAQuestionChecksItsImagesFirst:
    """Fix round 1, item 8. ``ask_on_task`` checks the images before it
    starts a run, so a refused question leaves no empty run on the task."""

    async def _ask(self, ids: list[str]) -> dict[str, Any]:
        return await _tool("ask_on_task", {
            "task_id": TASK, "question": "Approve this layout?", "idempotency_key": "q-1",
            "attachment_ids": ids})

    async def test_an_id_of_another_task_starts_no_run(self, gw):
        """Mutation: drop the ``_question_images`` call of
        ``tools._ask_on_task``, and the start goes out first."""
        result = await self._ask([IMG_A])
        assert _text(result) == tools.TEXT_ATTACHMENT_NOT_ON_TASK
        assert gw.starts() == []
        assert not [r for r in gw.requests if r.path.endswith("/activities")]

    async def test_a_file_that_is_not_an_image_starts_no_run(self, gw):
        gw.rows.append({"attachment_id": IMG_A, "name": "spec.pdf", "mime": "application/pdf",
                        "size": 10, "added_by": "x@mcpwrites.test", "kind": "file",
                        "url": "/api/projects/attachments/x/spec.pdf",
                        "created_at": "2026-11-12T00:00:00+00:00"})
        result = await self._ask([IMG_A])
        assert _text(result) == tools.TEXT_ATTACHMENT_NOT_IMAGE
        assert gw.starts() == []

    async def test_with_the_flag_off_nothing_goes_out(self, gw, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "")
        result = await self._ask([IMG_A])
        assert _text(result) == tools.TEXT_IMAGES_OFF
        assert gw.requests == []

    async def test_an_image_of_the_task_starts_the_run_and_asks(self, gw):
        up = await _upload(_args(TINY_PNG))
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        result = await self._ask([aid.upper()])
        assert result["isError"] is False, result
        assert len(gw.starts()) == 1
        (sent,) = [r for r in gw.requests if r.path.endswith("/activities")]
        assert json.loads(sent.body)["attachment_ids"] == [aid]

    async def test_a_question_with_no_images_reads_no_list(self, gw):
        result = await _tool("ask_on_task", {
            "task_id": TASK, "question": "Go on?", "idempotency_key": "q-2"})
        assert result["isError"] is False, result
        assert not [r for r in gw.requests if r.method == "GET"]


class TestARefusedQuestionLeavesNoOpenRun:
    """Fix round 2, item B. The checks of the edge pass and the run starts.
    Then the gateway refuses the question: its flag is off, or a member
    removed the image after the list read. The call then releases the run
    that it started, so no empty run stays open on the task. It never ends
    a run that it did not start."""

    async def _ask(self, gw: _Upload, answer: tuple[int, Any] | None) -> dict[str, Any]:
        up = await _upload(_args(TINY_PNG))
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        gw.answer = answer
        return await self._ask_again(aid)

    @staticmethod
    async def _ask_again(aid: str, key: str = "q-3") -> dict[str, Any]:
        return await _tool("ask_on_task", {
            "task_id": TASK, "question": "Approve this layout?", "idempotency_key": key,
            "attachment_ids": [aid]})

    @pytest.mark.parametrize("answer,text_", [
        ((422, {"detail": {"error": agent_oauth.IMAGE_UPLOAD_OFF, "message": "SECRET"}}),
         tools.TEXT_IMAGES_OFF),
        ((422, {"detail": {"error": "attachment_not_on_task", "message": "SECRET"}}),
         tools.TEXT_ATTACHMENT_NOT_ON_TASK),
        ((422, {"detail": {"error": "attachment_not_image", "message": "SECRET"}}),
         tools.TEXT_ATTACHMENT_NOT_IMAGE),
        ((403, {"detail": "SECRET"}), tools.TEXT_WRITE_FORBIDDEN),
        ((404, {"detail": "Agent run not found"}), tools.TEXT_RUN_NOT_FOUND),
        ((409, {"detail": "SECRET"}), tools.TEXT_RUN_CONFLICT),
    ], ids=["flag_off", "image_removed", "not_an_image", "forbidden", "run_gone",
            "conflict"])
    async def test_b1_a_refused_question_releases_the_run_that_it_started(
            self, gw, answer, text_):
        """The probe of the verifier: the edge flag is on, and the gateway
        answers 422 ``image_upload_off``. Mutation: drop the release of
        ``tools._ask_on_task``, and the run that the call started stays open
        (no release goes out)."""
        result = await self._ask(gw, answer)
        assert _text(result) == text_
        assert "SECRET" not in json.dumps(result)
        assert len(gw.starts()) == 1
        (release,) = gw.releases()
        assert json.loads(release.body) == {}
        assert release.headers.get("x-agent-nonce")

    async def test_b2_a_busy_answer_keeps_the_run(self, gw):
        """A busy answer may come after the question landed. So the call
        ends no run, and a retry with the same key finds the run and the
        question. Mutation: release on each tool error, and a release goes
        out here."""
        result = await self._ask(gw, (503, {"detail": "busy"}))
        assert _text(result) == tools.TEXT_BUSY
        assert len(gw.starts()) == 1
        assert gw.releases() == []

    async def test_b3_an_open_run_that_the_call_did_not_start_stays_open(self, gw):
        """``_start`` gave the open run of this session (``created`` false).
        Mutation: release with no check of ``created``, and the run of the
        session ends."""
        gw.start_created = False
        result = await self._ask(gw, (422, {"detail": {"error": agent_oauth.IMAGE_UPLOAD_OFF}}))
        assert _text(result) == tools.TEXT_IMAGES_OFF
        assert len(gw.starts()) == 1
        assert gw.releases() == []

    @pytest.mark.parametrize("release_answer", [(503, {"detail": "busy"}),
                                                (409, {"detail": "SECRET"})],
                             ids=["busy", "conflict"])
    async def test_b4_a_failed_release_still_gives_the_refusal(self, gw, release_answer):
        gw.release_answer = release_answer
        result = await self._ask(gw, (422, {"detail": {"error": agent_oauth.IMAGE_UPLOAD_OFF}}))
        assert _text(result) == tools.TEXT_IMAGES_OFF
        assert len(gw.releases()) == 1

    async def test_b5_an_answered_question_releases_nothing(self, gw):
        result = await self._ask(gw, None)
        assert result["isError"] is False, result
        assert gw.releases() == []

    async def test_b6_a_cap_answer_keeps_the_run(self, gw):
        """Fix round 3, item 1. The write cap counts each write, and the
        release is a write too. So the real gateway answers the release with
        429 as well, as this fake does, and the run would stay open anyway.
        The call keeps the run, as for a busy answer. After the wait, a retry
        with the same key asks on the same run. Mutation: let
        ``tools._refused`` count the cap text, and a release goes out here."""
        gw.release_answer = (429, {"detail": "Too many writes"})
        result = await self._ask(gw, (429, {"detail": "SECRET"}))
        assert _text(result) == tools.TEXT_CAP
        assert "SECRET" not in json.dumps(result)
        assert len(gw.starts()) == 1
        assert gw.releases() == []
        # The wait is over. The start replays the run that the key started.
        gw.answer, gw.start_created = None, False
        again = await self._ask_again(gw.rows[-1]["attachment_id"])
        assert again["isError"] is False, again
        assert again["structuredContent"]["run"]["id"] == RUN
        assert len(gw.starts()) == 2
        assert gw.releases() == []

    @pytest.mark.parametrize(("detail", "text"), [
        (agent_oauth.WRITE_CAP_DETAIL, tools.TEXT_CALL_CAP),
        (agent_oauth.WRITE_ROW_CAP_DETAIL, tools.TEXT_ROW_CAP),
    ], ids=["calls", "rows"])
    async def test_b6b_a_real_cap_detail_keeps_the_run(self, gw, detail, text):
        """MCP-G28a (D209) after the merge with CIV-14b. The real gateway
        names its counter in the detail of the 429, so the edge gives the
        text of that counter, not ``TEXT_CAP``. That text keeps the run too,
        as b6 does. Mutation: let ``tools._refused`` check ``TEXT_CAP``
        only, and a release goes out here."""
        gw.release_answer = (429, {"detail": detail})
        result = await self._ask(gw, (429, {"detail": detail}))
        assert _text(result) == text
        assert len(gw.starts()) == 1
        assert gw.releases() == []

    async def test_b7_a_question_that_never_left_the_edge_releases_the_run(self, gw):
        """Fix round 3, item 2. A connect error means no byte of the question
        went out, so the gateway wrote nothing. The call answers the busy
        text, and still releases the run that it started. Mutation: drop the
        ``_NotSent`` check of ``tools._refused``, and no release goes out."""
        up = await _upload(_args(TINY_PNG))
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        inner = httpx.ASGITransport(app=gw)

        class _Refusing(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                if request.method == "POST" and request.url.path.endswith("/activities"):
                    raise httpx.ConnectError("refused", request=request)
                return await inner.handle_async_request(request)

        with gateway_client.use_transport(_Refusing()):
            result = await self._ask_again(aid)
        assert _text(result) == tools.TEXT_BUSY
        assert len(gw.starts()) == 1
        assert not [r for r in gw.requests if r.path.endswith("/activities")]
        (release,) = gw.releases()
        assert json.loads(release.body) == {}

    @pytest.mark.parametrize("ended", ["released", "completed", "stopped", "failed",
                                       "abandoned"])
    async def test_b8_the_key_of_an_ended_run_asks_for_a_new_key(self, gw, ended):
        """Fix round 3, item 3. A retry with the key of a run that has ended,
        such as the run that a refused question released. The start replays
        that run, and the gateway refuses the question with 409. The text then
        says to send a new key. Mutation: drop the ``TEXT_KEY_RUN_ENDED`` step
        of ``tools._ask_on_task``, and the call answers TEXT_RUN_CONFLICT."""
        gw.start_created, gw.start_status = False, ended
        result = await self._ask(gw, (409, {"detail": f"This agent run is {ended}."}))
        assert _text(result) == tools.TEXT_KEY_RUN_ENDED
        assert "new key" in tools.TEXT_KEY_RUN_ENDED
        assert gw.releases() == []

    async def test_b9_a_409_on_an_open_run_keeps_its_text(self, gw):
        """Only an ended run gets the key text. Mutation: drop the status check
        of that step, and an open run of this session gets it too."""
        gw.start_created = False
        result = await self._ask(gw, (409, {"detail": "SECRET"}))
        assert _text(result) == tools.TEXT_RUN_CONFLICT
        assert gw.releases() == []


class TestTheBodyCapOfMcp:
    """Check 5 of §9.6 (CIV-14b): 6 MiB for one upload call, 1 MiB for any other."""

    async def test_a_large_upload_call_is_read(self, gw):
        data = png_of(tools.UPLOAD_IMAGE_MAX_BYTES)
        raw = json.dumps(_call("upload_task_image", _args(data))).encode()
        assert mcp.MAX_BODY_BYTES < len(raw) <= mcp.MAX_UPLOAD_BODY_BYTES
        r = await _post_raw(raw)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["isError"] is False

    async def test_l3_a_large_body_of_another_call_is_413(self, gw):
        message = _call("add_comment", {"task_id": TASK, "body": "x" * (2 * 1024 * 1024),
                                        "idempotency_key": "c-1"})
        r = await _post_raw(json.dumps(message).encode())
        assert r.status_code == 413
        assert r.json()["id"] is None
        assert gw.requests == []

    async def test_a_body_over_six_mib_is_413_before_any_read(self, gw):
        raw = b" " * (mcp.MAX_UPLOAD_BODY_BYTES + 1)
        r = await _post_raw(raw)
        assert r.status_code == 413

    async def test_l4_a_fifth_large_body_at_once_is_503(self, gw, monkeypatch):
        monkeypatch.setattr(mcp, "_LARGE_BODIES", [mcp.LARGE_BODIES_AT_ONCE])
        raw = json.dumps(_call("upload_task_image", _args(png_of(2 * 1024 * 1024)))).encode()
        r = await _post_raw(raw)
        assert r.status_code == 503
        assert r.headers["retry-after"] == str(mcp.LARGE_BODY_RETRY_AFTER_S)
        assert gw.requests == []

    async def test_the_slot_goes_back_after_each_large_body(self, gw):
        before = mcp._LARGE_BODIES[0]
        raw = json.dumps(_call("upload_task_image", _args(png_of(2 * 1024 * 1024)))).encode()
        assert (await _post_raw(raw)).status_code == 200
        message = _call("add_comment", {"task_id": TASK, "body": "x" * (2 * 1024 * 1024),
                                        "idempotency_key": "c-2"})
        assert (await _post_raw(json.dumps(message).encode())).status_code == 413
        assert mcp._LARGE_BODIES[0] == before


async def _post_raw(raw: bytes | Any, *, token: str = GOOD) -> httpx.Response:
    from agent_edge import app as edge_app_module

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=ISSUER) as client:
        return await client.post("/mcp", content=raw, headers={
            "authorization": f"Bearer {token}", "content-type": "application/json",
            "mcp-protocol-version": "2025-11-25"})


def _upload_raw(size: int = 2 * 1024 * 1024, key: str = "img-key-0001") -> bytes:
    """The body of one ``tools/call`` of ``upload_task_image``, over 1 MiB."""
    return json.dumps(_call("upload_task_image", _args(png_of(size), key=key))).encode()


async def _asgi(headers: dict[str, str], receive: Any) -> tuple[int, bytes]:
    """One POST to ``/mcp`` on the edge app, with the ASGI *receive* of the
    test. So a test can send a body slowly, in parts, or never."""
    from agent_edge import app as edge_app_module

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "https", "path": "/mcp", "raw_path": b"/mcp",
             "query_string": b"", "root_path": "",
             "headers": [(k.encode("latin-1"), v.encode("latin-1"))
                         for k, v in {"host": "mcp.metorite.com",
                                      "content-type": "application/json",
                                      "mcp-protocol-version": "2025-11-25",
                                      **headers}.items()],
             "client": ("203.0.113.9", 50000), "server": ("mcp.metorite.com", 443)}
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await edge_app_module.create_app()(scope, receive, send)
    (start,) = [m for m in sent if m["type"] == "http.response.start"]
    return start["status"], b"".join(m.get("body", b"") for m in sent
                                      if m["type"] == "http.response.body")


def _stalls(first: bytes = b"", *, then: str = "wait") -> Any:
    """An ASGI *receive* that sends *first* (if any) and then waits forever,
    or, with ``then="leave"``, says that the client went away."""
    parts: list[dict[str, Any]] = []
    if first:
        parts.append({"type": "http.request", "body": first, "more_body": True})
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if parts:
            return parts.pop(0)
        if then == "leave":
            return {"type": "http.disconnect"}
        await never.wait()
        raise AssertionError("unreachable")

    return receive


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _slots_free() -> bool:
    return mcp._LARGE_BODIES == [0] and mcp._LARGE_BY_CONNECTION == {}


async def _until(ready: Any, seconds: float = 3.0) -> None:
    """Wait until *ready()* is true, for *seconds* at most."""
    async with asyncio.timeout(seconds):
        while not ready():
            await asyncio.sleep(0.01)


class TestALargeBodyWaitsForItsToken:
    """Fix round 1 of CIV-14b, item 1 (§9.6 "The body size"). A large body
    runs the token check from the headers first. Only then does it take a
    slot of the edge and of its connection, and the read has a deadline."""

    @pytest.mark.parametrize("token", [None, "wrong-token"], ids=["no_token", "bad_token"])
    async def test_m1_slow_bodies_with_no_good_token_block_no_upload(self, gw, token):
        """The attack of the review: four POSTs that declare 6,000,000 bytes
        and send none. Before the fix they held every slot, and each upload
        in each organization got 503. Mutation: take the slot before
        ``_caller_of`` in ``mcp._serve_large``, and the upload here gets 503."""
        headers = {"content-length": "6000000", **(_bearer(token) if token else {})}
        stalled = [asyncio.create_task(_asgi(headers, _stalls()))
                   for _ in range(mcp.LARGE_BODIES_AT_ONCE)]
        try:
            # Each slow POST is answered, or (before the fix) holds a slot.
            await _until(lambda: all(t.done() for t in stalled)
                         or mcp._LARGE_BODIES[0] >= mcp.LARGE_BODIES_AT_ONCE)
            r = await _post_raw(_upload_raw())
            assert r.status_code == 200, r.text
            assert r.json()["result"]["isError"] is False
            answers = await asyncio.wait_for(asyncio.gather(*stalled), timeout=5)
        finally:
            for task in stalled:
                task.cancel()
        assert [status for status, _ in answers] == [401] * mcp.LARGE_BODIES_AT_ONCE
        assert all(json.loads(body)["id"] is None for _, body in answers)
        assert _slots_free()

    async def test_m1_a_large_body_with_no_declared_length_is_413(self, gw):
        """A body over 1 MiB must declare its length. Mutation: read an
        undeclared body up to 6 MiB, and this upload passes."""
        raw = _upload_raw()

        async def parts():
            for start in range(0, len(raw), 256 * 1024):
                yield raw[start:start + 256 * 1024]

        r = await _post_raw(parts())
        assert r.status_code == 413, r.text
        assert r.json()["id"] is None
        assert gw.requests == []
        assert _slots_free()

    async def test_m1_a_slow_large_body_is_408_and_gives_its_slot_back(self, gw, monkeypatch):
        """Mutation: drop the deadline of ``mcp._read_large``, and the read
        waits forever (the ``wait_for`` here fails)."""
        monkeypatch.setattr(mcp, "LARGE_BODY_READ_TIMEOUT_S", 0.2)
        status, body = await asyncio.wait_for(
            _asgi({"content-length": str(2 * 1024 * 1024), **_bearer(GOOD)},
                  _stalls(b"{" + b" " * 1000)), timeout=5)
        assert status == 408
        assert json.loads(body)["id"] is None
        assert _slots_free()
        assert gw.requests == []

    async def test_m1_a_client_that_goes_away_gives_its_slot_back(self, gw):
        """Mutation: drop ``except ClientDisconnect`` of ``_serve_large``,
        and the edge raises a 500 out of the app."""
        status, _ = await asyncio.wait_for(
            _asgi({"content-length": str(2 * 1024 * 1024), **_bearer(GOOD)},
                  _stalls(b"{" + b" " * 1000, then="leave")), timeout=5)
        assert status == 400
        assert _slots_free()

    async def test_m1_one_connection_holds_two_slots_at_most(self, gw, edge):  # noqa: F811
        """Mutation: drop the count of each connection in ``_large_slot``,
        and the third upload of one connection is read."""
        import dataclasses

        other = "other-good-token"
        edge.tokens[other] = dataclasses.replace(
            edge.tokens[GOOD], connection_id="aaaaaaaa-bbbb-4ccc-8ddd-000000000002")
        headers = {"content-length": str(2 * 1024 * 1024), **_bearer(GOOD)}
        held = [asyncio.create_task(_asgi(headers, _stalls(b"{")))
                for _ in range(mcp.LARGE_BODIES_PER_CONNECTION)]
        try:
            await _until(lambda: mcp._LARGE_BODIES[0] == mcp.LARGE_BODIES_PER_CONNECTION)
            third = await _post_raw(_upload_raw(key="img-key-0003"))
            assert third.status_code == 503
            assert third.headers["retry-after"] == str(mcp.LARGE_BODY_RETRY_AFTER_S)
            theirs = await _post_raw(_upload_raw(key="img-key-0004"), token=other)
            assert theirs.status_code == 200, theirs.text
            assert theirs.json()["result"]["isError"] is False
        finally:
            for task in held:
                task.cancel()
            await asyncio.gather(*held, return_exceptions=True)
        assert _slots_free()
        assert len(gw.uploads()) == 1


class TestALargeBodyIsCountedBeforeTheParse:
    """Fix round 1 of CIV-14b, item 2. The edge counts the shape bytes of a
    large body before ``json.loads``, so a body of many small values never
    builds millions of objects."""

    @pytest.mark.parametrize("raw", [
        b"[" + b"{}," * 1_500_000 + b"{}]",
        b"[" + b"1000," * 1_000_000 + b"1]",
        b'["' + b'a","' * 1_000_000 + b'a"]',
        b'{"a":' * 200_000 + b"1" + b"}" * 200_000,
    ], ids=["objects", "numbers", "strings", "nested"])
    async def test_m2_a_body_of_many_small_values_is_413_with_no_parse(self, gw, monkeypatch,
                                                                       raw):
        """Mutation: drop the ``_shape_allows_upload`` check of
        ``mcp._serve_large``, and the parse runs (the spy fails)."""
        assert mcp.MAX_BODY_BYTES < len(raw) <= mcp.MAX_UPLOAD_BODY_BYTES

        def no_parse(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the edge parsed a body of many small values")

        monkeypatch.setattr(mcp, "json", SimpleNamespace(loads=no_parse, dumps=json.dumps))
        r = await _post_raw(raw)
        assert r.status_code == 413
        assert gw.requests == []
        assert _slots_free()

    def test_m2_the_largest_upload_passes_the_count_with_room(self):
        encoded = _b64(png_of(tools.UPLOAD_IMAGE_MAX_BYTES))
        wrapped = "\r\n".join(encoded[i:i + 64] for i in range(0, len(encoded), 64))
        message = _call("upload_task_image", _args(b"", raw=wrapped,
                                                   name="layout final v2.png"))
        message["params"]["_meta"] = {"progressToken": 7}
        raw = json.dumps(message).encode()
        assert mcp.MAX_BODY_BYTES < len(raw) <= mcp.MAX_UPLOAD_BODY_BYTES
        assert mcp._shape_allows_upload(raw)
        assert raw.count(b"{") + raw.count(b"[") <= mcp.LARGE_BODY_MAX_BRACKETS // 2
        assert raw.count(b",") <= mcp.LARGE_BODY_MAX_COMMAS // 2


#: The answer of a body over 1 MiB before CIV-14b (``git show 7da4de2e8``):
#: HTTP 413 with this JSON-RPC error. An organization off the flag still gets
#: exactly this answer (fix round 2, item A).
OLD_413 = {"jsonrpc": "2.0", "id": None,
           "error": {"code": -32600, "message": "The body is too large."}}
OFF_ORG = "99999999-2222-4333-8444-555555555555"


def _reads(raw: bytes) -> tuple[list[int], Any]:
    """An ASGI *receive* that gives *raw* in one part, and the list of its calls."""
    calls: list[int] = []

    async def receive() -> dict[str, Any]:
        calls.append(len(calls))
        return {"type": "http.request", "body": raw, "more_body": False}

    return calls, receive


def _spy_slots(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The connection id of each slot that ``mcp._large_slot`` gives."""
    import contextlib

    taken: list[str] = []
    real = mcp._large_slot

    @contextlib.contextmanager
    def spy(connection_id: str) -> Any:
        taken.append(connection_id)
        with real(connection_id):
            yield

    monkeypatch.setattr(mcp, "_large_slot", spy)
    return taken


def _off_flag_tokens(edge: Any, count: int) -> list[str]:  # noqa: F811 — a fixture value
    """*count* tokens of :data:`OFF_ORG`, each of its own connection."""
    import dataclasses

    made = []
    for n in range(count):
        token = f"off-flag-token-{n}"
        edge.tokens[token] = dataclasses.replace(
            edge.tokens[GOOD], organization_id=OFF_ORG,
            connection_id=f"aaaaaaaa-bbbb-4ccc-8ddd-00000000010{n}")
        made.append(token)
    return made


class TestAnOrgOffTheFlagKeepsTheOldCap:
    """Fix round 2 of CIV-14b, item A (§9.6 "The body size", step 3). A large
    body of an organization off ``mcp_image_upload_orgs`` gets the old 413
    right after the token check. It takes no slot, and the edge reads no byte
    of it. So an organization off the flag sees no change, and it cannot fill
    the slots of an organization on the flag."""

    @pytest.mark.parametrize("orgs", ["", OFF_ORG], ids=["empty", "another_org"])
    async def test_m3_an_org_off_the_flag_gets_the_old_413_with_no_slot_and_no_read(
            self, gw, monkeypatch, orgs):
        """Mutation: drop the flag check of ``mcp._serve_large``, and the body
        takes a slot, is read and parsed, and the call answers 200."""
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", orgs)
        taken = _spy_slots(monkeypatch)
        raw = _upload_raw()
        reads, receive = _reads(raw)
        status, body = await asyncio.wait_for(
            _asgi({"content-length": str(len(raw)), **_bearer(GOOD)}, receive), timeout=5)
        assert status == 413
        assert json.loads(body) == OLD_413
        assert taken == []
        assert reads == []
        assert _slots_free()
        assert gw.requests == []

    async def test_m3_bodies_of_an_org_off_the_flag_block_no_upload_of_an_org_on_it(
            self, gw, edge, monkeypatch):  # noqa: F811
        """The attack of the verifier: an organization off the flag uses two
        connections and sends four bodies that declare 2 MiB and send one
        byte. Before the fix they held every slot, and the upload of an
        organization on the flag got 503. Mutation: drop the flag check of
        ``mcp._serve_large``, and the upload here gets 503."""
        from acb_common import get_settings

        from tests.unit.test_mcp_edge_writes import ORG

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", ORG)
        stalled = [
            asyncio.create_task(_asgi(
                {"content-length": str(2 * 1024 * 1024), **_bearer(token)}, _stalls(b"{")))
            for token in _off_flag_tokens(edge, 2)
            for _ in range(mcp.LARGE_BODIES_PER_CONNECTION)]
        try:
            await _until(lambda: all(t.done() for t in stalled)
                         or mcp._LARGE_BODIES[0] >= mcp.LARGE_BODIES_AT_ONCE)
            r = await _post_raw(_upload_raw())
            assert r.status_code == 200, r.text
            assert r.json()["result"]["isError"] is False
            answers = await asyncio.wait_for(asyncio.gather(*stalled), timeout=5)
        finally:
            for task in stalled:
                task.cancel()
            await asyncio.gather(*stalled, return_exceptions=True)
        assert [status for status, _ in answers] == [413] * mcp.LARGE_BODIES_AT_ONCE
        assert all(json.loads(body) == OLD_413 for _, body in answers)
        assert _slots_free()
        assert len(gw.uploads()) == 1

    async def test_m3_an_org_on_the_flag_takes_the_large_path(self, gw, monkeypatch):
        from acb_common import get_settings

        from tests.unit.test_mcp_edge_writes import CONN, ORG

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", ORG)
        taken = _spy_slots(monkeypatch)
        raw = _upload_raw()
        reads, receive = _reads(raw)
        status, body = await asyncio.wait_for(
            _asgi({"content-length": str(len(raw)), **_bearer(GOOD)}, receive), timeout=5)
        assert status == 200, body
        assert json.loads(body)["result"]["isError"] is False
        assert taken == [CONN]
        assert reads
        assert _slots_free()
        assert len(gw.uploads()) == 1

    @pytest.mark.parametrize("token", [None, "wrong-token"], ids=["no_token", "bad_token"])
    async def test_m3_off_the_flag_a_bad_token_still_gets_401_first(
            self, gw, monkeypatch, token):
        """The order of fix round 1 holds: the token check comes before the
        flag check, so a request with no good token gets 401, not 413."""
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "")
        raw = _upload_raw()
        reads, receive = _reads(raw)
        status, _ = await asyncio.wait_for(
            _asgi({"content-length": str(len(raw)), **(_bearer(token) if token else {})},
                  receive), timeout=5)
        assert status == 401
        assert reads == []
        assert _slots_free()


class TestTheGatewayKeepsTheSignedBody:
    """The wrapper of the gateway keeps the bytes of a signed form for branch 0."""

    def _app(self, seen: list[Any]):
        from gateway.routes.projects.agent_upload_body import AgentUploadBody

        async def inner(scope, receive, send) -> None:
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            seen.append((scope.get(agent_signing.SIGNED_BODY_SCOPE_KEY), body))
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return AgentUploadBody(inner)

    async def _send(self, app, *, path: str = UPLOAD_PATH, signed: bool = True,
                    body: bytes = b"form-bytes", method: str = "POST") -> httpx.Response:
        headers = {agent_signing.HEADER_SIGNATURE: "v1:x"} if signed else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gw.test") as client:
            return await client.request(method, path, content=body, headers=headers)

    async def test_l5_the_wrapper_keeps_the_signed_body_for_branch_0(self):
        seen: list[Any] = []
        r = await self._send(self._app(seen))
        assert r.status_code == 204
        ((kept, body),) = seen
        assert kept == b"form-bytes" and type(kept) is bytes
        assert body == b"form-bytes"

    @pytest.mark.parametrize("over", [
        {"signed": False}, {"path": f"/projects/tasks/{TASK}/comments"}, {"method": "PUT"},
    ], ids=["unsigned", "other_path", "other_method"])
    async def test_any_other_request_passes_untouched(self, over):
        seen: list[Any] = []
        await self._send(self._app(seen), **over)
        ((kept, _),) = seen
        assert kept is None

    async def test_a_signed_body_over_the_cap_is_413_before_the_route(self):
        from gateway.routes.projects.agent_upload_body import LIMIT_BYTES

        seen: list[Any] = []
        r = await self._send(self._app(seen), body=b"x" * (LIMIT_BYTES + 1))
        assert r.status_code == 413
        # Fix round 1, item 9: the code of the agent cap, not the budget.
        assert r.json() == {"detail": agent_oauth.AGENT_IMAGE_TOO_LARGE}
        assert seen == []

    @staticmethod
    def _branch_0_app(seen: list[Any]) -> Any:
        """The wrapper round an app that does what FastAPI and branch 0 do: it
        parses the form first, so the stream is gone, and then it checks the
        signature over the body that ``deps._signed_body`` gives."""
        from acb_auth import deps
        from gateway.routes.projects.agent_upload_body import AgentUploadBody
        from starlette.requests import Request

        async def inner(scope, receive, send) -> None:
            request = Request(scope, receive)
            form = await request.form()
            data = await form["file"].read()
            body = await deps._signed_body(request)
            verdict = deps._verify_signed_request(
                request, agent_signing.usable_keys(), body,
                request.headers.get(agent_signing.HEADER_NONCE))
            seen.append((verdict, data))
            status = 204 if isinstance(verdict, tuple) else 401
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return AgentUploadBody(inner)

    @staticmethod
    async def _send_parts(app: Any, headers: dict[str, str], chunks: list[bytes]) -> int:
        """One POST whose body comes as one ``http.request`` message for each
        chunk, as a proxy or a slow client sends it."""
        messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
                    for i, c in enumerate(chunks)]
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return messages.pop(0)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": UPLOAD_PATH, "query_string": b"",
                 "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                             for k, v in headers.items()]}
        await app(scope, receive, send)
        assert messages == [], "the app did not read every part"
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    @staticmethod
    def _signed(body: bytes, content_type: str) -> dict[str, str]:
        from tests.unit.test_mcp_edge_writes import CONN, KEY, ORG, R8_VIA

        return {**agent_signing.signed_headers(
            KEY, organization_id=ORG, connection_id=CONN, actor_via=R8_VIA,
            method="POST", path=UPLOAD_PATH, query="", body=body),
            "Content-Type": content_type}

    @staticmethod
    def _chunks(body: bytes, size: int = 4096) -> list[bytes]:
        return [body[i:i + size] for i in range(0, len(body), size)]

    async def test_l6_a_signed_form_in_many_parts_verifies_over_every_part(self, edge):  # noqa: F811
        """Fix round 1, item 4. Mutation: at ``agent_upload_body.py`` keep the
        copy at the FIRST part (``if KEY not in scope:`` in place of the
        ``more_body`` check), and branch 0 checks the signature over the
        first part only: this signed upload is refused."""
        data = png_of(50_000)
        body, kind = gateway_client._form("shot.png", data, "image/png")
        chunks = self._chunks(body)
        assert len(chunks) > 10
        seen: list[Any] = []
        status = await self._send_parts(self._branch_0_app(seen), self._signed(body, kind),
                                        chunks)
        assert status == 204
        ((verdict, stored),) = seen
        assert isinstance(verdict, tuple) and stored == data

    @pytest.mark.parametrize("signed_part", ["whole_body", "first_part"])
    async def test_l6_a_later_part_changed_after_signing_is_refused(self, edge, signed_part):  # noqa: F811
        """A middle part changes after the edge signed. With the signature of
        the whole first body, or of the first part alone, branch 0 refuses
        it. The ``first_part`` case fails under the mutation of l6 above."""
        body, kind = gateway_client._form("shot.png", png_of(50_000), "image/png")
        chunks = self._chunks(body)
        headers = self._signed(body if signed_part == "whole_body" else chunks[0], kind)
        middle = len(chunks) // 2
        chunks[middle] = bytes([chunks[middle][0] ^ 0x01]) + chunks[middle][1:]
        seen: list[Any] = []
        status = await self._send_parts(self._branch_0_app(seen), headers, chunks)
        assert status == 401
        ((verdict, _),) = seen
        assert verdict == "signature"

    async def test_branch_0_reads_the_kept_copy_after_a_form_parse(self):
        from acb_auth import deps
        from starlette.requests import Request

        scope = {"type": "http", "method": "POST", "path": UPLOAD_PATH, "headers": [],
                 agent_signing.SIGNED_BODY_SCOPE_KEY: b"abc"}

        async def receive():  # the form parser read the stream already
            raise AssertionError("the stream is gone")

        assert await deps._signed_body(Request(scope, receive)) == b"abc"
        # A value that is not the whole body as bytes is no copy.
        scope[agent_signing.SIGNED_BODY_SCOPE_KEY] = bytearray(b"abc")
        request = Request(scope, receive)
        request._stream_consumed = True
        assert await deps._signed_body(request) is None
        # ... and branch 0 refuses it as `body` before it reads a header.
        assert deps._verify_signed_request(request, {}, None, "n" * 22) == "body"
