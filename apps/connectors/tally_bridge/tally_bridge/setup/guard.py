"""Who may use the setup page (brief section 6.7, "Protect the setup page").

Any web page in the customer's browser can send a request to 127.0.0.1. So
every request to ``/setup`` passes four checks, and fails closed:

1. **Host.** The ``Host`` header is exactly ``127.0.0.1:<port>``. In address
   mode the tunnel publishes this port too. A request through the tunnel
   carries the tunnel's host name, so it fails here.
2. **No forwarding header.** A request with ``X-Forwarded-For``,
   ``Forwarded``, ``Tailscale-Funnel-Request``, ``CF-Connecting-IP`` or a
   similar header came through a proxy or a tunnel, and fails.
3. **The per-install secret.** The installer (``setup-link`` on a dev box)
   makes a random secret, stores only its SHA-256, and opens the page with the
   secret in the address. The page sends it on each API call in the
   ``X-Setup-Key`` header. The comparison runs in constant time. With no stored
   secret, setup is closed.
4. **Origin.** An API call carries ``Origin: http://127.0.0.1:<port>``, and no
   other. A page load from the address bar has no ``Origin``, or this one.

A refusal says only "refused", so it tells a probe nothing.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tally_bridge.secret_store import SecretStore, token_matches

KEY_HEADER = "x-setup-key"
_FORWARDED = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "forwarded",
    "x-real-ip",
    "tailscale-funnel-request",
    "tailscale-user-login",
    "cf-connecting-ip",
    "cf-ray",
)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


def refused(status: int = 403) -> Response:
    return JSONResponse({"error": "refused"}, status_code=status, headers=SECURITY_HEADERS)


class SetupGuard:
    def __init__(self, port: int, secrets: SecretStore) -> None:
        self.origin = f"http://127.0.0.1:{port}"
        self._host = f"127.0.0.1:{port}"
        self._secrets = secrets

    def check(self, request: Request, *, key: str, page: bool) -> Response | None:
        """None when the request may go on, or the refusal to send."""
        headers = request.headers
        if headers.get("host") != self._host:
            return refused()
        if any(name in headers for name in _FORWARDED):
            return refused()
        origin = headers.get("origin")
        if page:
            if origin is not None and origin != self.origin:
                return refused()
        elif origin != self.origin:
            return refused()
        if not token_matches(key, self._secrets.setup_secret_sha256()):
            return refused()
        return None
