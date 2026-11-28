"""Round-trip Gemini 3 thought signatures through MAF's OpenAI client.

Gemini 3 models (OpenAI-compatible endpoint) return
`tool_calls[i].extra_content.google.thought_signature` and REJECT the next
request with HTTP 400 if the assistant tool call comes back without it. MAF's
OpenAIChatCompletionClient does not carry unknown provider fields, so the
signature is lost. This httpx transport remembers each signature by tool-call
id and puts it back on the outgoing request.
"""

from __future__ import annotations

import json

import httpx


class ThoughtSignatureTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._sigs: dict[str, dict] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        is_chat = request.method == "POST" and request.url.path.endswith("/chat/completions")
        if is_chat and self._sigs:
            body = json.loads(request.content or b"{}")
            changed = False
            for msg in body.get("messages", []):
                for tc in msg.get("tool_calls") or []:
                    sig = self._sigs.get(tc.get("id", ""))
                    if sig and "extra_content" not in tc:
                        tc["extra_content"] = sig
                        changed = True
            if changed:
                content = json.dumps(body).encode()
                headers = {k: v for k, v in request.headers.items() if k.lower() != "content-length"}
                request = httpx.Request(
                    request.method, request.url, headers=headers, content=content, extensions=request.extensions
                )
        response = await self._inner.handle_async_request(request)
        if not is_chat:
            return response
        raw = await response.aread()
        try:
            data = json.loads(raw)
            for choice in data.get("choices", []):
                for tc in (choice.get("message") or {}).get("tool_calls") or []:
                    if tc.get("extra_content") and tc.get("id"):
                        self._sigs[tc["id"]] = tc["extra_content"]
        except (ValueError, AttributeError):
            pass
        headers = [(k, v) for k, v in response.headers.items() if k.lower() not in ("content-encoding", "content-length")]
        return httpx.Response(response.status_code, headers=headers, content=raw, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()
