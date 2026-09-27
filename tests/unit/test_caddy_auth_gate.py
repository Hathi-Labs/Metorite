"""A change to the Caddy sign-in and identity lines needs the owner (gate (a)).

Owner decision, 2026-09-28: "Auto, except sign-in changes". Since PR #498 the
deploy installs `deploy/hostinger/caddy/Caddyfile` on the box by itself. So a
merged Caddyfile is live on the next deploy. Most edits are routine. An edit
to the lines that decide WHO a request is, is not. One `header_up X-User-Email`
from a client header, and any caller can sign in as anybody.

This test reads the AUTH-RELEVANT directives out of the Caddyfile:

* every `header_up`, `header_down`, `request_header` and `header` line;
* what a path reaches: `handle`, `handle_path`, `route`, `rewrite`, `uri`
  and `redir`;
* `tls` and `import`;
* the WHOLE block of `basic_auth`, `basicauth`, `forward_auth`, `handle`,
  `handle_path`, `route`, `tls` and `transport`;
* every named matcher (`@name ...`) and every line that uses one;
* the label of every top-level block, so a new site changes the hash;
* the `admin` global option.

An `import` of a FILE fails outright (`test_the_caddyfile_imports_no_file`).
The deploy installs only this file, so an imported one comes from the box.
Harmless edits keep the hash: `lb_try_*`, comments, whitespace, and the order
of lines and of blocks.

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

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CADDYFILE = _ROOT / "deploy/hostinger/caddy/Caddyfile"
_GRANTS = _ROOT / ".claude/OWNER_GRANTS.md"

# The auth-relevant lines as merged in PR #498 (2026-09-28). See the
# docstring: a change here is an owner-gated act, not a routine edit.
# Checked on 2026-09-28: the box's live /etc/caddy/Caddyfile hashes to this
# same value, so the first deploy after the merge changes no auth line.
_BASELINE = "b134ae7063033a70ecae2793d5b5e64db89ad7c76d7b2ecccb23eb4cdbd4b1cd"

# Directives whose LINE is auth-relevant.
_AUTH_FIRST_TOKENS = {
    # who the request is
    "header_up",
    "header_down",
    "request_header",
    "header",
    "basic_auth",
    "basicauth",
    "forward_auth",
    # what a path reaches (H-60 re-review, P2): a `handle /internal/*` or a
    # `rewrite` can expose a route that `@internal` hides today
    "handle",
    "handle_path",
    "route",
    "rewrite",
    "uri",
    "redir",
    # who may connect, and what the file pulls in from elsewhere
    "tls",
    "import",
}
# Directives whose WHOLE block is auth-relevant, every line inside it.
_BLOCK_DIRECTIVES = {
    "basic_auth",
    "basicauth",
    "forward_auth",
    "handle",
    "handle_path",
    "route",
    "tls",
    "transport",
}
_GRANT_RE = re.compile(r"^\s*CADDY-AUTH-APPROVED\s+([0-9a-f]{64})\b", re.MULTILINE)


def _strip(line: str) -> str:
    # A `#` starts a comment only at the line start or after whitespace.
    return re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()


def auth_lines(text: str) -> dict[str, list[str]]:
    """Site label -> the sorted auth-relevant lines inside it.

    EVERY top-level block is a key, even one with no auth line, so a new site
    (for example `:8090 { reverse_proxy … }`) changes the hash.
    """
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
            out.setdefault(site, [])
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
    blob = "\n".join(
        f"{site} ::" + "".join(f"\n{site} :: {ln}" for ln in lines[site]) for site in sorted(lines)
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def foreign_imports(text: str) -> list[str]:
    """Every `import` that names a FILE rather than a snippet in this file.

    The deploy installs this one file. An imported file resolves on the box
    (relative to /etc/caddy), where no test and no review ever sees it. So its
    content could change auth with no diff here at all.
    """
    snippets = {
        m.group(1)
        for m in re.finditer(r"^\(([^)\s]+)\)\s*\{", text, re.MULTILINE)
    }
    bad = []
    for raw in text.splitlines():
        tokens = _strip(raw).split()
        if len(tokens) >= 2 and tokens[0] == "import" and tokens[1] not in snippets:
            bad.append(" ".join(tokens))
    return bad


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
    edited = text.replace("lb_try_duration 30s", "lb_try_duration 45s").replace(
        "lb_try_interval 250ms", "lb_try_interval 500ms"
    )
    assert edited != text
    assert auth_hash(edited) == auth_hash(text)


def _blocks(text: str) -> list[str]:
    """The top-level blocks of a Caddyfile, comments dropped."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for raw in text.splitlines():
        line = _strip(raw)
        if not line:
            continue
        cur.append(raw)
        depth += line.count("{") - line.count("}")
        if depth == 0 and cur:
            out.append("\n".join(cur) + "\n")
            cur = []
    return out


def test_reordering_the_site_blocks_does_not_change_the_hash() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    blocks = _blocks(text)
    assert len(blocks) >= 6, blocks
    reordered = blocks[0] + "".join(reversed(blocks[1:]))
    assert reordered != "".join(blocks)
    assert auth_hash(reordered) == auth_hash(text)


def _api_insert(line_block: str) -> str:
    """The real Caddyfile with `line_block` added inside the api site."""
    text = _CADDYFILE.read_text(encoding="utf-8")
    anchor = "\t@internal path /internal/*\n"
    assert anchor in text
    return text.replace(anchor, anchor + line_block)


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param("\thandle /internal/* {\n\t\treverse_proxy 127.0.0.1:8080\n\t}\n",
                     id="handle-exposes-internal"),
        pytest.param("\thandle_path /x/* {\n\t\treverse_proxy 127.0.0.1:8080\n\t}\n",
                     id="handle_path"),
        pytest.param("\troute {\n\t\treverse_proxy 127.0.0.1:8080\n\t}\n", id="route"),
        pytest.param("\trewrite /internal/* /public{uri}\n", id="rewrite"),
        pytest.param("\turi strip_prefix /api\n", id="uri"),
        pytest.param("\tredir /login https://evil.example.com\n", id="redir"),
        pytest.param("\ttls {\n\t\tclient_auth {\n\t\t\tmode request\n\t\t}\n\t}\n",
                     id="tls-client_auth"),
    ],
)
def test_a_change_to_what_the_api_exposes_changes_the_hash(edit: str) -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    assert auth_hash(_api_insert(edit)) != auth_hash(text)


def test_an_edit_inside_a_tls_block_changes_the_hash() -> None:
    a = "x.example.com {\n\ttls {\n\t\tclient_auth {\n\t\t\tmode request\n\t\t}\n\t}\n}\n"
    b = a.replace("mode request", "mode require_and_verify")
    assert auth_hash(a) != auth_hash(b)


def test_a_transport_block_is_captured_whole() -> None:
    a = (
        "x.example.com {\n\treverse_proxy 10.0.0.1:443 {\n"
        "\t\ttransport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}\n\t}\n}\n"
    )
    # `dial_timeout` is captured ONLY because it sits in a transport block.
    b = a.replace("\t\t\ttls_insecure_skip_verify\n", "\t\t\tdial_timeout 5s\n")
    assert auth_hash(a) != auth_hash(b)


def test_a_new_site_block_changes_the_hash() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    added = text + "\n:8090 {\n\treverse_proxy 127.0.0.1:8090\n}\n"
    assert auth_hash(added) != auth_hash(text)


def test_an_import_changes_the_hash() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")
    added = text.replace("\tencode zstd gzip\n", "\tencode zstd gzip\n\timport common\n", 1)
    added = "(common) {\n\theader_up X-A 1\n}\n" + added
    assert auth_hash(added) != auth_hash(text)


def test_the_caddyfile_imports_no_file() -> None:
    """The deploy installs only this file. An imported file lives on the box,
    where no test and no review sees it."""
    bad = foreign_imports(_CADDYFILE.read_text(encoding="utf-8"))
    assert not bad, (
        f"deploy/hostinger/caddy/Caddyfile imports a FILE: {bad}. The deploy installs\n"
        "only this one file, so the imported one comes from the box, and it can change\n"
        "sign-in with no diff in this repo. Inline it, or define it as a snippet\n"
        "`(name) { … }` in this file and `import name`. (work_plan.md §6 gate (a))"
    )


@pytest.mark.parametrize(
    "line", ["import /etc/caddy/auth.caddy", "import ../x", "import sites/*", "import extra"]
)
def test_a_file_import_is_refused(line: str) -> None:
    assert foreign_imports(f"x.example.com {{\n\t{line}\n}}\n") == [line]


def test_a_snippet_import_is_allowed() -> None:
    text = "(common) {\n\tencode gzip\n}\nx.example.com {\n\timport common\n}\n"
    assert foreign_imports(text) == []
