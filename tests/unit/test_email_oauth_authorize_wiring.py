"""Nobody could connect an email account, and the reason was wiring.

``GET /email/oauth/{provider}/authorize`` is gated (correctly — its handler
binds the new mailbox to the caller). Both Connect buttons navigated the browser
*straight at the gateway host*, and a top-level navigation carries no Bearer and
no ``X-User-Email`` — the session cookie is on the workbench origin, not
``api.*``. From 57ec82d9 (2026-07-29), when default-deny landed app-wide, that
request 401'd with ``{"detail":"Authentication required"}`` for every user,
before the handler — and its ``user_email`` fallback — ever ran. It went
unnoticed because the owner's mailbox was already connected.

The repair routes the navigation through a Next BFF route that has the session.
There is no new public surface, and this file exists to keep it that way:

1. The BFF route exists and BOTH ``handleConnect`` call sites target it —
   asserted inside each callback body, not by substring presence in the file. A
   test that passes on a declaration alone is this repo's recurring defect
   (see ``test_admin_member_offboarding.py::
   test_no_destructive_control_is_rendered_on_the_viewers_own_row``).
2. ``/email/oauth/{provider}/authorize`` is STILL NOT in ``PUBLIC_ROUTES``.
   That is the dangerous "fix", and it is the one somebody reaches for first.
   Since EM-T1a the callback is not public either. It runs behind the session
   through its own BFF route (``email_app_master_plan.md`` §10.4.1, risk R-4).
3. The BFF callback route acts as the member, keeps the 302, forwards four
   parameters only, and refuses a ``Location`` of another origin.
4. The identity comes from the session only. The state is signed and carries
   the organization and the member, and the ``user_email`` fallback is gone.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from acb_auth.roles import UserContext, UserRole

REPO = Path(__file__).resolve().parents[2]
CONTROL_PLANE = REPO / "workbench" / "control_plane" / "src"

BFF_ROUTE = (
    CONTROL_PLANE / "app" / "api" / "email" / "oauth" / "[provider]"
    / "authorize" / "route.ts"
)
BFF_CALLBACK_ROUTE = (
    CONTROL_PLANE / "app" / "api" / "email" / "oauth" / "[provider]"
    / "callback" / "route.ts"
)
#: The path the browser is navigated to, with the provider interpolated.
BFF_NAVIGATION = "/api/email/oauth/${provider}/authorize"

CALL_SITES = (
    CONTROL_PLANE / "app" / "email" / "page.tsx",
    CONTROL_PLANE / "app" / "integrations" / "page.tsx",
)

AUTHORIZE_TEMPLATE = "/email/oauth/{provider}/authorize"
CALLBACK_TEMPLATE = "/email/oauth/{provider}/callback"


def _read(path: Path) -> str:
    # utf-8 explicitly: these files carry em-dashes and the Windows default
    # (cp1252) fails on them — the same trap that breaks two other test files.
    return path.read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """``source`` with **every** comment removed, wherever it sits on the line.

    These assertions are about what the code *does*. Without this, a comment
    explaining why ``user_email`` is no longer sent reads, to a substring check,
    exactly like sending it — which is how the first draft of this file failed.

    ⚠️ **A line-based version of this helper is not enough, and shipping one
    was this file's second false pass.** It dropped only comments that *began*
    a line, so::

        redirect: "follow", // was redirect: "manual"

    left the string ``redirect: "manual"`` in the assertion input and the
    mutation passed all eleven tests — the exact defect the helper exists to
    prevent, one comment-position away. It also deleted real code: on a line
    where a block comment *ended*, everything after ``*/`` went with it, so a
    banned token could vanish and turn a ``not in`` assertion green.

    So this is a character scanner that tracks string state, not a line filter.
    ``//`` inside a string literal stays, because the scanner knows it is in a
    string; ``{/* … */}`` in JSX goes, because it does not care about position.

    **Stated limit:** regex literals are not modelled, so ``/ab\\/\\/c/`` would
    confuse it. Nothing in the files this reads uses one, and a scanner that
    parsed them would need to disambiguate division — worth knowing before
    pointing this at other sources.
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote: str | None = None
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:      # escape: take the next char whole
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue                           # leave the newline for the loop
        if ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def test_the_comment_stripper_is_not_line_based() -> None:
    """`_code_only` is a fence for other fences, so it needs one of its own.

    Every case below is a position a line-based stripper got wrong. The last
    two are the converse: it must not eat code or string contents while it is
    at it.
    """
    banned = 'redirect: "manual"'

    for label, src in (
        ("trailing //", 'const f = 6; // redirect: "manual" matters'),
        ("trailing block", 'const g = 7; /* redirect: "manual" */'),
        ("jsx block", '<div>{/* redirect: "manual" */}</div>'),
        ("own-line //", '// redirect: "manual"'),
        ("indented //", '    // redirect: "manual"'),
        ("multiline block", '/*\n redirect: "manual"\n*/\nconst h = 1;'),
    ):
        assert banned not in _code_only(src), f"{label}: comment survived"

    # …and it must not delete code that shares a line with a comment.
    assert "const i = 8;" in _code_only('/* note */ const i = 8;')
    assert "const j = 9;" in _code_only('const j = 9; // note')
    # A `//` inside a string is not a comment.
    assert "http://x" in _code_only('const k = "http://x";')


def _callback_body(source: str, name: str) -> str:
    """The body of ``const <name> = useCallback(...)``, brace-matched.

    Slicing by braces rather than by a trailing ``}, []);`` literal means the
    extraction survives a dependency-array edit, and — more importantly — cannot
    silently widen to the whole file and start matching text from an unrelated
    handler.
    """
    start = source.index(f"const {name} = useCallback")
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# ── 1. The BFF route, and both call sites pointing at it ────────────────────

def test_the_bff_authorize_route_exists() -> None:
    assert BFF_ROUTE.is_file(), (
        f"{BFF_ROUTE} is missing — the Connect buttons navigate to it and a "
        "missing file is a 404 in the address bar"
    )


def test_the_bff_route_authenticates_and_keeps_the_redirect_intact() -> None:
    """The three properties that make it a working substitute for the direct
    navigation, each of which is silently droppable.

    - ``gatewayHeaders()`` is what attaches the internal bearer AND the signed-in
      member's ``X-User-Email``. ``serviceHeaders()`` would compile, reach the
      gateway, and bind every mailbox to the platform principal.
    - ``redirect: "manual"`` is what leaves the 302 intact. Without it Node's
      fetch follows it server-side, fetching the provider's consent page with our
      credentials and returning HTML the browser has no provider cookies for.
    - reading ``location`` off the upstream response is the only way the consent
      URL reaches the browser.

    ⚠️ Comment-stripped, and that is not a nicety. The first version of this test
    asserted against the raw file and **survived** the mutation that downgrades
    ``redirect: "manual"`` to ``"follow"`` — the header comment on that file
    explains the flag by name, so the prose satisfied the check while the code
    did the wrong thing. Exactly the failure mode this file exists to prevent,
    found in this file. Every source assertion here reads code only.
    """
    src = _code_only(_read(BFF_ROUTE))
    assert "gatewayHeaders" in src, "the BFF must act as the signed-in member"
    assert "serviceHeaders" not in src, (
        "serviceHeaders is bearer-only — it would bind the mailbox to the "
        "platform rather than to the person who clicked Connect"
    )
    assert 'redirect: "manual"' in src, (
        "without redirect:'manual' the 302 is followed server-side and the "
        "consent screen never reaches the browser"
    )
    assert 'headers.get("location")' in src


def test_the_bff_does_not_forward_the_user_email_parameter() -> None:
    """Identity comes from the session, asserted in the header. A ``user_email``
    query parameter must not be relayed upstream from here — the gateway now
    prefers the header, but the parameter should not even make the trip."""
    handler = _code_only(_read(BFF_ROUTE)).split(
        "export async function GET"
    )[1]
    assert "user_email" not in handler, (
        "the GET handler references user_email — the only correct handling is "
        "to drop it"
    )
    assert 'searchParams.get("redirect_after")' in handler, (
        "redirect_after is the one parameter that must survive the hop, or the "
        "user is not returned to the page they clicked Connect on"
    )


@pytest.mark.parametrize("path", CALL_SITES, ids=lambda p: p.parent.name)
def test_connect_navigates_to_the_bff_and_never_to_the_gateway_host(
    path: Path,
) -> None:
    """Positional, inside the callback body.

    ``"/api/email/oauth/" in page`` would pass on a comment, an import, or a
    neighbouring handler. What has to be true is that the assignment which moves
    the browser is the one naming the BFF path.
    """
    body = _code_only(_callback_body(_read(path), "handleConnect"))

    assert f"window.location.href = `{BFF_NAVIGATION}" in body, (
        f"handleConnect in {path.name} does not navigate to {BFF_NAVIGATION}"
    )
    # The bug itself: a navigation to the gateway origin. Any of these three
    # spellings reintroduces it.
    for banned in ("NEXT_PUBLIC_GATEWAY_URL", "gatewayUrl", "${gatewayUrl}"):
        assert banned not in body, (
            f"{path.name}'s handleConnect reaches the gateway host via "
            f"{banned!r} — that navigation carries no identity and 401s"
        )
    assert "user_email" not in body, (
        f"{path.name}'s handleConnect still sends a user_email parameter; "
        "identity is the session's, resolved server-side"
    )
    # Exactly one navigation to the authorize endpoint, so a second, unguarded
    # copy cannot sit further down the same handler.
    assert body.count("/oauth/") == 1


# ── 2. The dangerous repair, pinned shut ────────────────────────────────────

def _public_routes() -> frozenset[str]:
    import gateway.main as main

    return main.PUBLIC_ROUTES


def test_the_authorize_leg_is_not_public_and_must_not_become_public() -> None:
    """Do not "fix" a 401 here by adding this template to PUBLIC_ROUTES.

    ``oauth_authorize`` writes ``{"user_id": …}`` into the state the callback
    later uses to create the ``email_accounts`` row. Anonymous + an identity
    taken from a query parameter means any caller can bind a mailbox to somebody
    else's account — strictly worse than nobody being able to connect one.
    """
    assert AUTHORIZE_TEMPLATE not in _public_routes()


def test_the_callback_leg_is_not_public() -> None:
    """EM-T1a inverted this pin (spec §10.4.1, risk R-4).

    The callback used to be public, with trust from the state alone. A signed
    state is a bearer value for ten minutes, so an attacker could start the
    flow and a victim's consent would attach the victim's mailbox to the
    attacker. The callback now runs behind the session through the BFF, and
    the member of the session must be the member in the state.
    """
    assert CALLBACK_TEMPLATE not in _public_routes()


def test_the_callback_template_is_absent_from_every_exempt_list() -> None:
    """The three lists named in §10.4.1: PUBLIC_ROUTES, the feature-router
    exemption of the email package, and the enforcement test's own copy."""
    from gateway.routes.email.core import router

    from tests.unit.test_org_access_enforcement import GATED_ROUTERS

    assert CALLBACK_TEMPLATE not in _public_routes()
    assert CALLBACK_TEMPLATE not in GATED_ROUTERS["gateway.routes.email"]
    core_src = _read(
        REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "email"
        / "core.py"
    )
    exempt_block = core_src.split("require_feature_router(", 1)[1].split("])", 1)[0]
    assert CALLBACK_TEMPLATE not in exempt_block
    # And it is still mounted, so the three absences mean "gated".
    paths = {getattr(r, "path", "") for r in router.routes}
    assert CALLBACK_TEMPLATE in paths


def test_the_authorize_route_is_registered_and_behind_the_app_guard() -> None:
    """PUBLIC_ROUTES is only meaningful for a route that exists. This is what
    turns "not public" into "actually authenticated"."""
    import gateway.main as main

    from tests.unit._routes import served_routes

    paths = {getattr(r, "path", "") for r in served_routes(main.app.routes)}
    assert AUTHORIZE_TEMPLATE in paths, (
        "the authorize route is not mounted; the exemption test above would "
        "then be vacuously true"
    )


# ── 3. The BFF callback route (EM-T1a) ──────────────────────────────────────


def test_the_bff_callback_route_exists() -> None:
    assert BFF_CALLBACK_ROUTE.is_file(), (
        f"{BFF_CALLBACK_ROUTE} is missing — it is the redirect URI the "
        "provider sends the browser to"
    )


def test_the_bff_callback_acts_as_the_member_and_keeps_the_redirect() -> None:
    """The same three properties as the authorize route, plus the origin
    check. ``route.test.ts`` beside the file runs the handler. This reads the
    code, comments stripped, so a header comment cannot satisfy it."""
    src = _code_only(_read(BFF_CALLBACK_ROUTE))
    assert "gatewayHeaders" in src
    assert "serviceHeaders" not in src, (
        "serviceHeaders is bearer-only — the gateway would see the platform, "
        "not the member, and the member check would refuse every connect"
    )
    assert 'redirect: "manual"' in src
    assert 'headers.get("location")' in src
    assert "target.origin !== req.nextUrl.origin" in src, (
        "the BFF must refuse a Location of another origin"
    )
    assert '"callback_bad_location"' in src


def test_the_bff_callback_forwards_only_the_four_oauth_parameters() -> None:
    src = _code_only(_read(BFF_CALLBACK_ROUTE))
    allow = src.split("FORWARDED_PARAMS = [", 1)[1].split("]", 1)[0]
    named = sorted(p.strip().strip('"') for p in allow.split(",") if p.strip())
    assert named == ["code", "error", "error_description", "state"]
    handler = src.split("export async function GET", 1)[1]
    # The handler reads the query through the allowlist and nowhere else.
    assert handler.count("searchParams.get(") == 1
    assert "searchParams.get(name)" in handler
    assert "user_email" not in handler
    # The code is a credential for a short time. Nothing logs it.
    assert "console." not in src


def test_the_authorize_url_carries_no_form_post() -> None:
    """A cross-site POST drops the Lax session cookie, so the BFF callback
    would not see the member. Read from the gateway code that builds the URL."""
    src = _read(
        REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "email"
        / "transport" / "oauth.py"
    )
    code = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    assert "form_post" not in code
    assert "response_mode" not in code


# ── 4. Identity comes from the session only (EM-T1a item 2) ─────────────────

ORG = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def oauth_module(monkeypatch: pytest.MonkeyPatch):
    from acb_common import get_settings
    from gateway.routes.email.transport import oauth as mod

    # A configured client id, so the handler reaches the state rather than
    # 400ing on "Microsoft OAuth is not configured".
    monkeypatch.setenv("MSFT_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(
        get_settings(), "gateway_session_secret", "wiring-test-secret", raising=False,
    )
    return mod


async def _authorize_state(mod, user: UserContext) -> dict:
    from urllib.parse import parse_qs, urlparse

    from gateway.routes.email.transport.signing import verify_oauth_state

    resp = await mod.oauth_authorize("microsoft", user=user, redirect_after="")
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    claims = verify_oauth_state(query["state"][0])
    assert claims is not None, "the authorize leg signed a state that does not verify"
    return claims


async def test_the_state_carries_the_session_identity(oauth_module) -> None:
    """The member and the organization in the state are the session's."""
    me = UserContext(
        email="Colleague@Fracktal.in", role=UserRole.EMPLOYEE, organization_id=ORG,
    )

    claims = await _authorize_state(oauth_module, me)

    assert claims["member"] == "colleague@fracktal.in"
    assert claims["org"] == ORG
    assert claims["provider"] == "microsoft"


def test_the_authorize_leg_takes_no_user_email_parameter(oauth_module) -> None:
    """The query fallback is gone. A parameter cannot name the member."""
    import inspect

    assert "user_email" not in inspect.signature(oauth_module.oauth_authorize).parameters


async def test_no_organization_is_refused_with_403(oauth_module) -> None:
    from fastapi import HTTPException

    orgless = UserContext(email="colleague@fracktal.in", role=UserRole.EMPLOYEE)
    with pytest.raises(HTTPException) as exc:
        await oauth_module.oauth_authorize("microsoft", user=orgless, redirect_after="")
    assert exc.value.status_code == 403


async def test_no_member_is_refused_with_403(oauth_module) -> None:
    """The old ``anonymous`` member is gone with the fallback."""
    from fastapi import HTTPException

    nameless = UserContext(email=None, role=UserRole.AGENT, organization_id=ORG)
    with pytest.raises(HTTPException) as exc:
        await oauth_module.oauth_authorize("microsoft", user=nameless, redirect_after="")
    assert exc.value.status_code == 403
