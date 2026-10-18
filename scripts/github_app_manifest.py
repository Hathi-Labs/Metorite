"""Register the Metorite GitHub App from a manifest (WS-54 GH-0, GH-1).

Spec: ``project-docs/specs/github_integration.md`` §13.2, §14 and §15.

⚠️ **Run this on your own PC, never on a box and never in CI.** It writes a
private key into ``~/.metorite/secrets`` (``%USERPROFILE%\\.metorite\\secrets``
on Windows), the drop folder of ``scripts/secrets.sh``. Nothing goes into the
repository.

    uv run python scripts/github_app_manifest.py --env staging --owner <org>
    uv run python scripts/github_app_manifest.py --env production --owner <org>

What it does, in order (§15):

1. It serves one page on ``http://127.0.0.1:<port>``. The page holds a hidden
   form that POSTs the manifest to GitHub, with a random ``state``.
2. You click **Create GitHub App** on GitHub. GitHub sends the browser back to
   the local page with a ``code`` and the ``state``.
3. The script refuses a wrong ``state``. Then it calls ``POST
   /app-manifests/{code}/conversions``, which must happen within one hour.
4. It writes ``github-app[-staging].env`` and ``github-app[-staging].pem``. It
   refuses to overwrite a file that exists.
5. It stops after one exchange, or after 15 minutes.

It prints the App name and the two file names, and never a value.

The env file holds the eight settings of §14. Five come from GitHub
(``GITHUB_APP_ID``, ``_SLUG``, ``_CLIENT_ID``, ``_CLIENT_SECRET`` and
``_WEBHOOK_SECRET``). Three are fixed for the environment:
``GITHUB_APP_ENABLED=false``, the key path on the box and ``APP_PUBLIC_URL``.
So the push of GH-0 step 5 lands the flag OFF.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import os
import secrets
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

#: The drop folder of scripts/secrets.sh. The CLI has no option to change it.
DROP_DIR = Path.home() / ".metorite" / "secrets"

#: How long the local server waits for GitHub to come back.
WAIT_SECONDS = 15 * 60

#: The only host this script calls.
GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"

#: The key path on the box. The `*-key` entries of deploy/secrets/manifest.json
#: write the PEM file here.
BOX_KEY_PATH = "/opt/acb/github-app.pem"

#: §13.2: the least privilege. No permission is `write`.
PERMISSIONS: dict[str, str] = {
    "metadata": "read",
    "pull_requests": "read",
    "checks": "read",
    "statuses": "read",
    "members": "read",
}

#: §13.2: the events. `installation` and `installation_repositories` come to
#: every App by default, so they are not listed.
EVENTS: tuple[str, ...] = ("pull_request", "check_suite", "check_run", "status")


@dataclass(frozen=True)
class Environment:
    """One column of the §14 table."""

    key: str
    app_name: str
    homepage: str
    webhook_url: str
    callback_url: str
    app_public_url: str
    public: bool
    file_stem: str


ENVIRONMENTS: dict[str, Environment] = {
    "staging": Environment(
        key="staging",
        app_name="Metorite (staging)",
        homepage="https://dev.metorite.com",
        webhook_url="https://api.dev.metorite.com/webhooks/github",
        callback_url="https://dev.metorite.com/api/integrations/github/callback",
        app_public_url="https://dev.metorite.com",
        public=False,
        file_stem="github-app-staging",
    ),
    "production": Environment(
        key="production",
        app_name="Metorite",
        homepage="https://metorite.com",
        webhook_url="https://api.metorite.com/webhooks/github",
        callback_url="https://app.metorite.com/api/integrations/github/callback",
        app_public_url="https://app.metorite.com",
        public=True,
        file_stem="github-app",
    ),
}

#: The keys of the env file, in the order the file lists them.
ENV_KEYS: tuple[str, ...] = (
    "GITHUB_APP_ENABLED",
    "GITHUB_APP_ID",
    "GITHUB_APP_SLUG",
    "GITHUB_APP_CLIENT_ID",
    "GITHUB_APP_CLIENT_SECRET",
    "GITHUB_APP_WEBHOOK_SECRET",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "APP_PUBLIC_URL",
)


class ManifestError(Exception):
    """A refusal. The message names no secret."""


# ── The pure parts (tests call these) ───────────────────────────────────────


def build_manifest(env: Environment, redirect_url: str) -> dict[str, Any]:
    """The manifest of §15 for *env*. *redirect_url* is the local page."""
    return {
        "name": env.app_name,
        "url": env.homepage,
        "description": (
            "Metorite reads the state of pull requests and checks, so a task "
            "shows its pull request. It writes nothing to GitHub."
        ),
        "hook_attributes": {"url": env.webhook_url, "active": True},
        "redirect_url": redirect_url,
        "callback_urls": [env.callback_url],
        "public": env.public,
        "request_oauth_on_install": True,
        "setup_on_update": False,
        "default_permissions": dict(PERMISSIONS),
        "default_events": list(EVENTS),
    }


def form_action(owner: str | None, state: str) -> str:
    """Where the form POSTs (§4.2 fact 1). No owner means a personal account."""
    query = f"?state={quote(state, safe='')}"
    if owner:
        if not valid_login(owner):
            raise ManifestError("the --owner value is not a GitHub organization name")
        return f"{GITHUB_WEB}/organizations/{owner}/settings/apps/new{query}"
    return f"{GITHUB_WEB}/settings/apps/new{query}"


def valid_login(login: str) -> bool:
    """A GitHub account name: letters, digits and single hyphens, 1 to 39."""
    return (
        0 < len(login) <= 39
        and all(ch.isalnum() or ch == "-" for ch in login)
        and not login.startswith("-") and not login.endswith("-")
    )


def check_state(expected: str, got: str | None) -> None:
    """Refuse a returned ``state`` that is not the one this run sent."""
    if not got or not secrets.compare_digest(expected.encode(), got.encode()):
        raise ManifestError("the state from GitHub does not match. Nothing was written.")


def env_lines(env: Environment, conversion: dict[str, Any]) -> str:
    """The env file text for one conversion answer (§4.2 fact 2)."""
    values = {
        "GITHUB_APP_ENABLED": "false",
        "GITHUB_APP_ID": str(conversion.get("id") or ""),
        "GITHUB_APP_SLUG": str(conversion.get("slug") or ""),
        "GITHUB_APP_CLIENT_ID": str(conversion.get("client_id") or ""),
        "GITHUB_APP_CLIENT_SECRET": str(conversion.get("client_secret") or ""),
        "GITHUB_APP_WEBHOOK_SECRET": str(conversion.get("webhook_secret") or ""),
        "GITHUB_APP_PRIVATE_KEY_PATH": BOX_KEY_PATH,
        "APP_PUBLIC_URL": env.app_public_url,
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise ManifestError(f"the GitHub answer has no value for {', '.join(missing)}")
    for key, value in values.items():
        if any(ch in value for ch in "\r\n\0 ") or "=" in key:
            raise ManifestError(f"the value of {key} is not one plain line")
    header = (
        f"# The {env.app_name} GitHub App. Written by scripts/github_app_manifest.py.\n"
        "# Push it with scripts/secrets.sh. Never commit this file.\n"
    )
    return header + "".join(f"{k}={values[k]}\n" for k in ENV_KEYS)


def output_paths(drop: Path, env: Environment) -> tuple[Path, Path]:
    return drop / f"{env.file_stem}.env", drop / f"{env.file_stem}.pem"


def is_empty_template(path: Path) -> bool:
    """True for an env file that ``scripts/secrets.sh init`` wrote and nobody
    filled: only comments, blank lines and ``KEY=`` lines with no value.

    Such a file holds no secret, so replacing it loses nothing. Without this
    rule an owner who ran ``init`` first could never run this script.
    """
    if path.suffix != ".env" or not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        bare = line.strip()
        if not bare or bare.startswith("#"):
            continue
        _key, sep, value = bare.partition("=")
        if not sep or value.strip():
            return False
    return True


def refuse_existing(drop: Path, env: Environment) -> None:
    """Refuse to start when either output file holds anything (§15 step 4).

    An empty template of ``secrets.sh init`` is the one exception.
    """
    for path in output_paths(drop, env):
        if path.exists() and not is_empty_template(path):
            raise ManifestError(
                f"{path.name} exists in the drop folder. Move it away first. "
                "This script never overwrites a file."
            )


def _write_new(path: Path, text: str) -> None:
    """Create *path* with mode 600. Fails if it exists, with no race. An empty
    template (:func:`is_empty_template`) is removed first."""
    if is_empty_template(path):
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_outputs(drop: Path, env: Environment, conversion: dict[str, Any]) -> tuple[Path, Path]:
    """Write the env file and the key file. Refuses to overwrite either."""
    pem = str(conversion.get("pem") or "")
    if "PRIVATE KEY-----" not in pem:
        raise ManifestError("the GitHub answer holds no private key")
    text = env_lines(env, conversion)
    drop.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(drop, 0o700)
    env_path, pem_path = output_paths(drop, env)
    refuse_existing(drop, env)
    _write_new(pem_path, pem if pem.endswith("\n") else pem + "\n")
    try:
        _write_new(env_path, text)
    except Exception:
        pem_path.unlink(missing_ok=True)
        raise
    return env_path, pem_path


def form_page(action: str, manifest: dict[str, Any], app_name: str) -> bytes:
    """The one local page: a form that the browser POSTs to GitHub."""
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Register {html.escape(app_name)}</title></head><body>"
        f"<p>Register the <b>{html.escape(app_name)}</b> GitHub App.</p>"
        f"<form method='post' action='{html.escape(action, quote=True)}'>"
        "<input type='hidden' name='manifest' "
        f"value='{html.escape(json.dumps(manifest), quote=True)}'>"
        "<button type='submit'>Continue to GitHub</button></form>"
        "</body></html>"
    )
    return body.encode("utf-8")


# ── The network parts ───────────────────────────────────────────────────────


def convert(code: str) -> dict[str, Any]:
    """``POST /app-manifests/{code}/conversions`` (§4.2 fact 2)."""
    import httpx

    if not code or not all(ch.isalnum() or ch in "-_" for ch in code) or len(code) > 200:
        raise ManifestError("the code from GitHub has a bad shape")
    resp = httpx.post(
        f"{GITHUB_API}/app-manifests/{code}/conversions",
        headers={"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=False,
    )
    if resp.status_code != 201:
        raise ManifestError(
            f"GitHub refused the exchange (status {resp.status_code}). A code "
            "older than one hour fails. Delete the half-made App and run again."
        )
    body = resp.json()
    if not isinstance(body, dict):
        raise ManifestError("GitHub sent an answer this script cannot read")
    return body


class _Flow:
    def __init__(self, env: Environment, owner: str | None, drop: Path) -> None:
        self.env = env
        self.owner = owner
        self.drop = drop
        self.state = secrets.token_urlsafe(24)
        self.done = threading.Event()
        self.result: tuple[Path, Path] | None = None
        self.error: str | None = None
        self.port = 0


def _handler(flow: _Flow) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # the query holds a code
            return

        def _send(self, status: int, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                redirect = f"http://127.0.0.1:{flow.port}/redirect"
                manifest = build_manifest(flow.env, redirect)
                page = form_page(form_action(flow.owner, flow.state), manifest,
                                 flow.env.app_name)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if url.path != "/redirect" or flow.done.is_set():
                self._send(404, "Not found.")
                return
            query = parse_qs(url.query)
            try:
                check_state(flow.state, (query.get("state") or [None])[0])
                conversion = convert((query.get("code") or [""])[0])
                flow.result = write_outputs(flow.drop, flow.env, conversion)
                self._send(200, "<p>Done. Go back to the terminal.</p>")
            except ManifestError as exc:
                flow.error = str(exc)
                self._send(400, f"<p>{html.escape(str(exc))}</p>")
            except Exception as exc:
                flow.error = f"the exchange failed ({type(exc).__name__})"
                self._send(500, "<p>The exchange failed. See the terminal.</p>")
            flow.done.set()

    return Handler


def run(env_key: str, owner: str | None, *, drop: Path = DROP_DIR,
        open_browser: bool = True, wait_seconds: float = WAIT_SECONDS) -> int:
    env = ENVIRONMENTS[env_key]
    if owner is not None and not valid_login(owner):
        raise ManifestError("the --owner value is not a GitHub organization name")
    refuse_existing(drop, env)
    flow = _Flow(env, owner, drop)
    server = HTTPServer(("127.0.0.1", 0), _handler(flow))
    flow.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start = f"http://127.0.0.1:{flow.port}/"
    print(f"Open {start} to register {env.app_name}.")
    if open_browser:
        webbrowser.open(start)
    try:
        finished = flow.done.wait(timeout=wait_seconds)
    finally:
        server.shutdown()
        server.server_close()
    if not finished:
        print("Stopped after 15 minutes. Nothing was written.", file=sys.stderr)
        return 1
    if flow.error or flow.result is None:
        print(f"Refused: {flow.error or 'no result'}", file=sys.stderr)
        return 1
    env_path, pem_path = flow.result
    print(f"Registered {env.app_name}. Wrote {env_path.name} and {pem_path.name}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", required=True, choices=sorted(ENVIRONMENTS))
    parser.add_argument("--owner", default=None,
                        help="the GitHub organization that owns the App")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args.env, args.owner, open_browser=not args.no_browser)
    except ManifestError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
