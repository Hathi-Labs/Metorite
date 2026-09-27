"""A change to the Caddy sign-in and identity lines needs the owner (gate (a)).

Owner decision, 2026-09-28: "Auto, except sign-in changes". Since PR #498 the
deploy installs `deploy/hostinger/caddy/Caddyfile` on the box by itself. So a
merged Caddyfile is live on the next deploy. Most edits are routine. An edit
to the lines that decide WHO a request is, is not. One `header_up X-User-Email`
from a client header, and any caller can sign in as anybody.

This test reads the AUTH-RELEVANT directives out of the Caddyfile:

* every `header_up`, `header_down`, `request_header` and `header` line;
* `basic_auth`, `basicauth` and `forward_auth`, with their whole block;
* every named matcher (`@name ...`) and every line that uses one;
* the `admin` global option.

It normalises the whitespace and the order inside each site block, and it
hashes the result with sha256. The test passes only when:

1. the hash equals `_BASELINE` below, or
2. `.claude/OWNER_GRANTS.md` holds the line `CADDY-AUTH-APPROVED <sha256>`.

Only the owner can write that file. `plan-guard.mjs` refuses every agent write
to it, and no grant unlocks that refusal.

⚠️ **AN AGENT THAT EDITS `_BASELINE` DEFEATS THIS FENCE.** The test cannot
stop that edit. What stops it is review: a change to `_BASELINE` is a change to
this file, and `work_plan.md` §6 gate (a) names this file and this constant. So
a reviewer must treat any diff to `_BASELINE` as an owner-gated act, exactly as
a diff to the Caddyfile's auth lines. The approved route never touches this
file: the owner adds the grant line, and the baseline stays where it is.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CADDYFILE = _ROOT / "deploy/hostinger/caddy/Caddyfile"
_GRANTS = _ROOT / ".claude/OWNER_GRANTS.md"

# The auth-relevant lines as merged in PR #498 (2026-09-28). See the
# docstring: a change here is an owner-gated act, not a routine edit.
_BASELINE = "8188e88b74158552ccb01a5037ab59ed17522f8c99494694cf34810b517ddc2f"

_AUTH_FIRST_TOKENS = {
    "header_up",
    "header_down",
    "request_header",
    "header",
    "basic_auth",
    "basicauth",
    "forward_auth",
}
_BLOCK_DIRECTIVES = {"basic_auth", "basicauth", "forward_auth"}
_GRANT_RE = re.compile(r"^\s*CADDY-AUTH-APPROVED\s+([0-9a-f]{64})\b", re.MULTILINE)


def _strip(line: str) -> str:
    # A `#` starts a comment only at the line start or after whitespace.
    return re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()


def auth_lines(text: str) -> dict[str, list[str]]:
    """Site label -> the sorted auth-relevant lines inside it."""
    out: dict[str, list[str]] = {}
    depth = 0
    site = ""
    capture_until: int | None = None
    for raw in text.splitlines():
        line = " ".join(_strip(raw).split())
        if not line:
            continue
        opens = line.endswith("{")
        closes = line == "}"
        if depth == 0 and opens:
            site = line[:-1].strip() or "(global)"
            depth = 1
            continue
        if closes:
            depth -= 1
            if capture_until is not None and depth < capture_until:
                capture_until = None
            if depth == 0:
                site = ""
            continue
        tokens = line.split()
        keep = (
            capture_until is not None
            or tokens[0] in _AUTH_FIRST_TOKENS
            or any(t.startswith("@") for t in tokens)
            or (site == "(global)" and tokens[0] == "admin")
        )
        if keep:
            out.setdefault(site, []).append(line)
        if opens:
            if tokens[0] in _BLOCK_DIRECTIVES and capture_until is None:
                capture_until = depth + 1
            depth += 1
    return {k: sorted(v) for k, v in out.items()}


def auth_hash(text: str) -> str:
    lines = auth_lines(text)
    blob = "\n".join(f"{site} :: {ln}" for site in sorted(lines) for ln in lines[site])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _approved() -> set[str]:
    try:
        return set(_GRANT_RE.findall(_GRANTS.read_text(encoding="utf-8")))
    except OSError:
        return set()


def test_the_auth_lines_are_the_baseline_or_owner_approved() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    got = auth_hash(text)
    if got == _BASELINE or got in _approved():
        return
    shown = "\n".join(
        f"    [{site}] {ln}" for site, lines in sorted(auth_lines(text).items()) for ln in lines
    )
    raise AssertionError(
        "The sign-in / identity lines of deploy/hostinger/caddy/Caddyfile changed.\n"
        "Since PR #498 the deploy installs this file on the box by itself, so this\n"
        "change would go live on the next deploy. work_plan.md §6 gate (a) makes it\n"
        "an OWNER act (owner decision 2026-09-28, 'Auto, except sign-in changes').\n\n"
        f"The auth-relevant lines are now:\n{shown}\n\n"
        f"Their hash is {got}\n"
        f"The baseline is {_BASELINE}\n\n"
        "To approve it, the OWNER adds this line to .claude/OWNER_GRANTS.md:\n\n"
        f"    CADDY-AUTH-APPROVED {got}\n\n"
        "An agent must not add that line, and must not edit _BASELINE in this test."
    )


def test_an_owner_grant_line_approves_a_new_hash(tmp_path, monkeypatch) -> None:
    sha = "a" * 64
    grants = tmp_path / "OWNER_GRANTS.md"
    grants.write_text(f"ALLOW 2026-09-28 deploy — x\nCADDY-AUTH-APPROVED {sha}\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_GRANTS", grants)
    assert sha in _approved()
    grants.write_text(f"# CADDY-AUTH-APPROVED-ish {sha}\n", encoding="utf-8")
    assert sha not in _approved()


def test_the_extractor_sees_the_lines_it_guards() -> None:
    """A fence whose extractor reads nothing passes everything."""
    lines = auth_lines(_CADDYFILE.read_text(encoding="utf-8"))
    assert "admin off" in lines.get("(global)", [])
    api = lines.get("api.metorite.com", [])
    assert "header_up X-Real-IP {remote_host}" in api
    assert "@internal path /internal/*" in api
    assert "respond @internal 404" in api


def test_an_injected_identity_header_changes_the_hash() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    injected = text.replace(
        "\t\theader_up X-Real-IP {remote_host}\n",
        "\t\theader_up X-Real-IP {remote_host}\n"
        "\t\theader_up X-User-Email {http.request.header.X-Foo}\n",
    )
    assert injected != text
    assert auth_hash(injected) != auth_hash(text)


def test_a_forward_auth_block_is_captured_whole() -> None:
    text = (
        "app.example.com {\n"
        "\tforward_auth 127.0.0.1:9000 {\n"
        "\t\turi /verify\n"
        "\t\tcopy_headers X-User\n"
        "\t}\n"
        "\treverse_proxy 127.0.0.1:3001\n"
        "}\n"
    )
    lines = auth_lines(text)["app.example.com"]
    assert "uri /verify" in lines and "copy_headers X-User" in lines
    assert "reverse_proxy 127.0.0.1:3001" not in lines


def test_whitespace_order_and_comments_do_not_change_the_hash() -> None:
    a = "x.example.com {\n\theader_up A 1\n\theader_up B 2\n}\n"
    b = "# note\nx.example.com {\n    header_up   B 2   # why\n  header_up A 1\n}\n"
    assert auth_hash(a) == auth_hash(b)


def test_a_non_auth_edit_does_not_need_the_owner() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    edited = text.replace("lb_try_duration 30s", "lb_try_duration 45s")
    assert edited != text
    assert auth_hash(edited) == auth_hash(text)
