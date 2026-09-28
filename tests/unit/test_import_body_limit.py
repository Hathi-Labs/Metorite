"""WS-41 I-7 — the import upload is capped before it is read (spec §7.4)."""

from __future__ import annotations

import pathlib
from typing import Any

from gateway.routes.projects.import_body_limit import PREFIX, ImportBodyLimit


class _App:
    """Reads the whole body, as the multipart parser does, then answers 201."""

    def __init__(self) -> None:
        self.read = 0
        self.called = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True
        while True:
            message = await receive()
            self.read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


async def _call(
    app: Any, path: str, chunks: list[bytes], declared: int | None, method: str = "POST"
) -> list[Any]:
    headers = [] if declared is None else [(b"content-length", str(declared).encode())]
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    queue = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[Any] = []

    async def receive() -> Any:
        return queue.pop(0)

    async def send(message: Any) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _status(sent: list[Any]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def test_a_declared_length_over_the_cap_is_refused_unread() -> None:
    inner = _App()
    sent = await _call(
        ImportBodyLimit(inner, limit=100), PREFIX + "runs", [b"x" * 10], declared=101
    )
    assert _status(sent) == 413 and not inner.called


async def test_an_undeclared_body_is_cut_off_at_the_cap() -> None:
    inner = _App()
    sent = await _call(
        ImportBodyLimit(inner, limit=100), PREFIX + "runs", [b"x" * 60, b"x" * 60], declared=None
    )
    assert _status(sent) == 413 and inner.read <= 120


async def test_an_upload_under_the_cap_passes() -> None:
    inner = _App()
    sent = await _call(
        ImportBodyLimit(inner, limit=100), PREFIX + "runs", [b"x" * 50, b"x" * 40], declared=90
    )
    assert _status(sent) == 201 and inner.read == 90


async def test_any_other_route_is_untouched() -> None:
    inner = _App()
    sent = await _call(
        ImportBodyLimit(inner, limit=10), "/projects/tasks", [b"x" * 50], declared=50
    )
    assert _status(sent) == 201
    inner = _App()
    sent = await _call(
        ImportBodyLimit(inner, limit=10), PREFIX + "runs", [b"x" * 50], declared=50, method="GET"
    )
    assert _status(sent) == 201


def test_the_gateway_wraps_itself_in_it() -> None:
    main = pathlib.Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/main.py"
    assert "app.add_middleware(ImportBodyLimit)" in main.read_text(encoding="utf-8")
