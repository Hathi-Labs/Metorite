"""Who may use the setup page (brief section 6.7, "Protect the setup page").

Any web page in the customer's browser can send a request to 127.0.0.1. So
every request to ``/setup`` passes four checks, and fails closed:

1. **Host.** The ``Host`` header is exactly ``127.0.0.1:<port>``. In address
   mode the tunnel publishes this port too. A request through the tunnel
   carries the tunnel's host name, so it fails here.
2. **No forwarding header.** A request with ``Forwarded``, ``Via``, or a
   header that starts with ``X-Forwarded-``, ``CF-`` or ``Tailscale-`` (and a
   few more, in ``_forwarded``) came through a proxy or a tunnel, and fails.
3. **The per-install secret.** The installer (``setup-link`` on a dev box)
   makes a random secret, stores only its SHA-256, and opens the page with the
   secret in the address. The page sends it on each API call in the
   ``X-Setup-Key`` header. The comparison runs in constant time. With no stored
   secret, setup is closed.
4. **Origin.** A POST (a write) carries ``Origin: http://127.0.0.1:<port>``,
   and no other. Browsers send ``Origin`` on every POST. A GET (the page, its
   assets, the state read) may carry no ``Origin``, because a browser sends
   none on a same-origin GET. When it carries one, it must be this one. Its
   ``Sec-Fetch-Site``, when the browser sends it, must be ``same-origin`` or
   ``none``. So a page on another site cannot use the API.

A refusal says only "refused", so it tells a probe nothing.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tally_bridge.secret_store import SecretStore, token_matches

KEY_HEADER = "x-setup-key"
# A request through a proxy or a tunnel carries one of these. The match is by
# prefix as well as by name, so a new header of the same family fails too.
_FORWARDED_NAMES = ("forwarded", "via", "x-real-ip", "true-client-ip", "x-client-ip")
_FORWARDED_PREFIXES = ("x-forwarded-", "x-original-", "cf-", "tailscale-", "fly-", "x-amzn-")


def _forwarded(name: str) -> bool:
    return name in _FORWARDED_NAMES or name.startswith(_FORWARDED_PREFIXES)


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

    def check(self, request: Request, *, key: str, write: bool) -> Response | None:
        """None when the request may go on, or the refusal to send."""
        headers = request.headers
        if headers.get("host") != self._host:
            return refused()
        if any(_forwarded(name) for name in headers):
            return refused()
        origin = headers.get("origin")
        if write:
            if origin != self.origin:
                return refused()
        elif origin is not None and origin != self.origin:
            return refused()
        if headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return refused()
        if not token_matches(key, self._secrets.setup_secret_sha256()):
            return refused()
        return None
