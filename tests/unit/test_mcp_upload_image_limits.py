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

from tests.unit.test_mcp_edge_attachment import GIF, JPEG, TINY_PNG, WEBP
from tests.unit.test_mcp_edge_task_writes import edge, redis_fake  # noqa: F401 — fixtures
from tests.unit.test_mcp_edge_writes import (
    GOOD,
    ISSUER,
    TASK,
    _call,
    _post,
    _text,
    _tool,
    _uuid,
)

UPLOAD_PATH = f"/projects/tasks/{TASK}/attachments"
RUN = _uuid(7701)
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
        elif scope["method"] == "POST" and scope["path"] == f"/projects/agent-runs/{RUN}/activities":
            sent = json.loads(body)
            activity = {"id": _uuid(7801), "run_id": RUN, "task_id": TASK, "seq": 2,
                        "type": sent["type"], "content": {"body": sent.get("body")},
                        "content_metadata": None, "ephemeral": False, "signal": None,
                        "signal_metadata": None, "actor": "agent-member@mcpwrites.test",
                        "actor_via": "mcp:client.example:meta", "in_reply_to": None,
                        "pm_activity_id": None, "idempotency_key": sent["idempotency_key"],
                        "created_at": "2026-11-12T00:00:00+00:00"}
            run = {"id": RUN, "task_id": TASK, "status": "in_progress",
                   "member_email": "agent-member@mcpwrites.test",
                   "client_via": "mcp:client.example:meta", "started_at": None,
                   "updated_at": None}
            status, payload = self.activity_status, {"activity": activity, "run": run,
                                                     "created": True}
            if self.answer is not None:
                status, payload = self.answer
        raw = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": raw})

    def uploads(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method == "POST" and r.path == UPLOAD_PATH]


@pytest.fixture
def gw(edge, monkeypatch):  # noqa: F811 — a fixture
    """The fake edge, with the fake upload gateway."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
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
        (415, "SECRET", tools.TEXT_IMAGE_TYPE),
        (400, "File type not allowed: a.exe", tools.TEXT_IMAGE_NAME),
        (409, "Someone else changed these tasks at the same moment. Try again.",
         tools.TEXT_UPLOAD_RACE),
    ], ids=["flag_off", "hidden_task", "budget", "type", "name", "race"])
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
            "run_id": RUN, "type": "response", "body": "Two captures.",
            "idempotency_key": "a-2", "attachment_ids": [IMG_A.upper(), IMG_B]})
        assert result["isError"] is False, result
        (sent,) = [r for r in gw.requests if r.path.endswith("/activities")]
        assert json.loads(sent.body)["attachment_ids"] == [IMG_A, IMG_B]

    @pytest.mark.parametrize("code,text_", [
        (agent_oauth.IMAGE_UPLOAD_OFF, tools.TEXT_IMAGES_OFF),
        ("attachment_not_on_task", tools.TEXT_ATTACHMENT_NOT_ON_TASK),
        ("attachment_not_image", tools.TEXT_ATTACHMENT_NOT_IMAGE),
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


async def _post_raw(raw: bytes) -> httpx.Response:
    from agent_edge import app as edge_app_module

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=ISSUER) as client:
        return await client.post("/mcp", content=raw, headers={
            "authorization": f"Bearer {GOOD}", "content-type": "application/json",
            "mcp-protocol-version": "2025-11-25"})


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
        assert seen == []

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
