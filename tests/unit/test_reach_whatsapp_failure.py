"""WS-59 RE-5 fix round 2: which send failures Reach tries again.

``whatsapp.send_failure`` sorts an exception. Only a send that surely did
not reach Meta, or a 429, is tried again. A timeout or a 5xx may have
delivered, and another 4xx fails the same way again.
"""

from __future__ import annotations

import httpx
import pytest
from gateway.reach import whatsapp


def _status(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://graph.facebook.com/x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


@pytest.mark.parametrize(("exc", "reason"), [
    (httpx.ConnectError("x"), "send_retry"),
    (httpx.ConnectTimeout("x"), "send_retry"),
    (_status(429), "send_retry"),
    (_status(400), "send_refused"),
    (_status(403), "send_refused"),
    (httpx.ReadTimeout("x"), "send_unsure"),
    (_status(500), "send_unsure"),
    (RuntimeError("unexpected send response shape"), "send_unsure"),
])
def test_send_failure(exc, reason):
    assert whatsapp.send_failure(exc) == reason
