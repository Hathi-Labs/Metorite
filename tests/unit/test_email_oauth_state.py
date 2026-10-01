"""EM-T1a — the signed OAuth state and the callback that checks it.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.1, items 1 to 4.

R7 fences named here:

* ``email-oauth-state-signed``: a tampered, expired, wrong-provider or
  wrong-purpose state redirects with ``invalid_state`` and writes nothing.
* ``email-oauth-secret-usable``: an empty or public secret refuses to sign,
  and the authorize leg answers 503.
* ``email-oauth-callback-member``: a callback whose session member is not the
  member in the state redirects with ``invalid_state`` (risk R-4).
* ``email-oauth-provider-error``: a provider ``error`` with no ``code`` lands
  on the callback page, never on a 422.

The database half (a callback for a member of org B writes a row of org B
that org A cannot read) is R8 and lives in ``test_email_tenant_bind_rls.py``.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest
from acb_auth.member_proof import PUBLIC_DEFAULT_SECRETS
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.email.transport import oauth, signing

SECRET = "em-t1a-test-secret"
ORG = "11111111-2222-3333-4444-555555555555"
OTHER_ORG = "99999999-8888-7777-6666-555555555555"
MEMBER = "dana@example.com"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", SECRET, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")
    monkeypatch.setenv("MSFT_OAUTH_CLIENT_ID", "test-client-id")


def _state(**over) -> str:
    kw = {"org": ORG, "member": MEMBER, "provider": "microsoft", "redirect_after": ""}
    kw.update(over)
    return signing.sign_oauth_state(**kw)


# ── 1. The signer ───────────────────────────────────────────────────────────


def test_a_fresh_state_round_trips() -> None:
    claims = signing.verify_oauth_state(_state(member="Dana@Example.COM"))
    assert claims is not None
    assert claims["org"] == ORG
    assert claims["member"] == MEMBER, "the member is lower-cased when signed"
    assert claims["provider"] == "microsoft"
    assert claims["v"] == 1
    assert claims["nonce"]


def test_two_states_for_one_member_differ() -> None:
    assert _state() != _state(), "the nonce must make each state unique"


def test_a_tampered_state_does_not_verify() -> None:
    token = _state()
    body, tag = token.split(".")
    flipped = ("A" if tag[0] != "A" else "B") + tag[1:]
    assert signing.verify_oauth_state(f"{body}.{flipped}") is None
    # A payload swapped under a valid tag fails too.
    other_body = _state(member="mallory@example.com").split(".")[0]
    assert signing.verify_oauth_state(f"{other_body}.{tag}") is None


def test_an_expired_state_does_not_verify() -> None:
    issued = time.time() - signing.OAUTH_STATE_TTL_SECONDS - 1
    token = signing.sign_oauth_state(
        org=ORG, member=MEMBER, provider="microsoft", now=issued,
    )
    assert signing.verify_oauth_state(token) is None
    # One second inside the window still verifies.
    fresh = signing.sign_oauth_state(
        org=ORG, member=MEMBER, provider="microsoft",
        now=time.time() - signing.OAUTH_STATE_TTL_SECONDS + 5,
    )
    assert signing.verify_oauth_state(fresh) is not None


def test_a_value_signed_for_another_purpose_does_not_verify() -> None:
    """Same secret, same body, the webhook purpose: it must not pass."""
    body = _state().split(".")[0]
    wrong = signing._mac(SECRET, signing.WEBHOOK_PURPOSE, body)
    assert signing.verify_oauth_state(f"{body}.{wrong}") is None
    # And the converse: a state tag is not a webhook signature.
    assert signing.verify_webhook_org(
        ORG, signing._mac(SECRET, signing.OAUTH_STATE_PURPOSE, ORG),
    ) is None


@pytest.mark.parametrize("bad", ["", " ", *sorted(PUBLIC_DEFAULT_SECRETS)])
def test_an_empty_or_public_secret_refuses_to_sign(monkeypatch, bad: str) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", bad, raising=False)
    with pytest.raises(signing.SigningUnavailable):
        _state()
    with pytest.raises(signing.SigningUnavailable):
        signing.sign_webhook_org(ORG)


def test_a_public_secret_verifies_nothing(monkeypatch) -> None:
    """A state signed under the public default must not verify on a box
    that still runs with that default."""
    public = sorted(PUBLIC_DEFAULT_SECRETS)[0]
    body = _state().split(".")[0]
    forged = f"{body}.{signing._mac(public, signing.OAUTH_STATE_PURPOSE, body)}"
    monkeypatch.setattr(get_settings(), "gateway_session_secret", public, raising=False)
    assert signing.verify_oauth_state(forged) is None
    assert signing.verify_webhook_org(
        ORG, signing._mac(public, signing.WEBHOOK_PURPOSE, ORG),
    ) is None


@pytest.mark.parametrize("junk", [None, "", "a", "a.b.c", "x" * 5000, "é.é"])
def test_junk_does_not_verify(junk) -> None:
    assert signing.verify_oauth_state(junk) is None


def test_the_webhook_signature_binds_the_organization() -> None:
    sig = signing.sign_webhook_org(ORG)
    assert signing.verify_webhook_org(ORG, sig) == ORG
    assert signing.verify_webhook_org(ORG.upper(), sig) == ORG, "one spelling"
    assert signing.verify_webhook_org(OTHER_ORG, sig) is None
    assert signing.verify_webhook_org("not-a-uuid", sig) is None
    assert signing.verify_webhook_org(ORG, None) is None
    assert signing.verify_webhook_org(None, sig) is None


# ── 2. The authorize leg ────────────────────────────────────────────────────


def _member(email: str = MEMBER, org: str | None = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def test_authorize_answers_503_when_the_secret_is_unusable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        await oauth.oauth_authorize("microsoft", user=_member(), redirect_after="")
    assert exc.value.status_code == 503
    assert "GATEWAY_SESSION_SECRET" in exc.value.detail


async def test_authorize_and_exchange_use_one_redirect_uri(monkeypatch) -> None:
    """The provider refuses the exchange unless both legs send one value. It
    is the BFF callback on the workbench origin."""
    resp = await oauth.oauth_authorize("microsoft", user=_member(), redirect_after="")
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["redirect_uri"] == [
        "https://app.example.test/api/email/oauth/microsoft/callback"
    ]
    assert oauth._build_redirect_uri("microsoft") == query["redirect_uri"][0]
    assert "response_mode" not in query


@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({"GATEWAY_PUBLIC_URL": "http://localhost:8000"}, "http://localhost:3001"),
        ({"GATEWAY_PUBLIC_URL": "https://api.metorite.com"}, "https://app.metorite.com"),
        ({"GATEWAY_PUBLIC_URL": "https://gw.example.test/"}, "https://gw.example.test"),
        ({"WORKBENCH_PUBLIC_URL": "https://app.x.test/"}, "https://app.x.test"),
    ],
)
def test_the_workbench_url_derivation(monkeypatch, env, want) -> None:
    monkeypatch.delenv("WORKBENCH_PUBLIC_URL", raising=False)
    monkeypatch.delenv("GATEWAY_PUBLIC_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert oauth._workbench_public_url() == want


# ── 3. The callback ─────────────────────────────────────────────────────────


class _Spy:
    def __init__(self) -> None:
        self.exchanged = 0
        self.saved: list[dict] = []
        self.identity: tuple[str | None, str | None] = ("u1", ORG)
        self.identity_raises = False

    async def exchange(self, code, redirect_uri):
        self.exchanged += 1
        self.redirect_uri = redirect_uri
        return {"access_token": "at", "refresh_token": "rt"}

    async def mailbox(self, provider, token):
        return "dana@contoso.test"

    async def save(self, **kw):
        self.saved.append(kw)
        return "acc-1"

    async def resolve(self, email):
        if self.identity_raises:
            raise RuntimeError("pool exhausted")
        self.resolved = email
        return self.identity


class _Store:
    def encrypt(self, raw: str) -> str:
        return "enc:" + raw


@pytest.fixture()
def spy(monkeypatch) -> _Spy:
    from acb_auth import access
    from acb_llm import key_store

    s = _Spy()
    monkeypatch.setattr(oauth, "_exchange_msft_token", s.exchange)
    monkeypatch.setattr(oauth, "_get_provider_email", s.mailbox)
    monkeypatch.setattr(oauth, "_save_account", s.save)
    monkeypatch.setattr(access, "resolve_identity", s.resolve)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    return s


def _error_of(resp) -> str | None:
    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://app.example.test/email/oauth/callback"
    )
    return parse_qs(url.query).get("error", [None])[0]


async def _callback(state, *, user=None, provider="microsoft", code="c0de", error=None):
    return await oauth.oauth_callback(
        provider, user=user or _member(), code=code, state=state, error=error,
    )


async def test_a_valid_callback_saves_the_account_in_the_state_org(spy) -> None:
    resp = await _callback(_state(redirect_after="/email"))

    assert _error_of(resp) is None
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert q["account_id"] == ["acc-1"]
    assert q["redirect_after"] == ["/email"]
    assert spy.resolved == MEMBER
    assert spy.redirect_uri == "https://app.example.test/api/email/oauth/microsoft/callback"
    assert len(spy.saved) == 1
    assert spy.saved[0]["org"] == ORG
    assert spy.saved[0]["member"] == MEMBER
    assert spy.saved[0]["mailbox"] == "dana@contoso.test"


async def _assert_refused(spy, resp) -> None:
    assert _error_of(resp) == "invalid_state"
    assert spy.exchanged == 0, "a refused state must never reach the provider"
    assert spy.saved == [], "a refused state must never write"


async def test_a_tampered_state_is_refused(spy) -> None:
    body, tag = _state().split(".")
    await _assert_refused(spy, await _callback(f"{body}.x{tag[1:]}"))


async def test_an_expired_state_is_refused(spy) -> None:
    old = signing.sign_oauth_state(
        org=ORG, member=MEMBER, provider="microsoft", now=time.time() - 3600,
    )
    await _assert_refused(spy, await _callback(old))


async def test_a_wrong_provider_state_is_refused(spy) -> None:
    await _assert_refused(spy, await _callback(_state(provider="gmail")))


async def test_a_wrong_purpose_state_is_refused(spy) -> None:
    body = _state().split(".")[0]
    tag = signing._mac(SECRET, signing.WEBHOOK_PURPOSE, body)
    await _assert_refused(spy, await _callback(f"{body}.{tag}"))


async def test_a_missing_state_is_refused(spy) -> None:
    await _assert_refused(spy, await _callback(None))


async def test_another_session_member_is_refused(spy) -> None:
    """R-4: the attacker's state, the victim's session."""
    mallory = _member(email="mallory@example.com")
    await _assert_refused(spy, await _callback(_state(), user=mallory))


async def test_the_session_member_matches_in_any_case(spy) -> None:
    resp = await _callback(_state(), user=_member(email="DANA@example.com"))
    assert _error_of(resp) is None
    assert spy.saved[0]["owner"] == "DANA@example.com"


async def test_an_identity_in_another_org_is_refused(spy) -> None:
    spy.identity = ("u1", OTHER_ORG)
    await _assert_refused(spy, await _callback(_state()))


async def test_an_identity_with_no_org_is_refused(spy) -> None:
    spy.identity = (None, None)
    await _assert_refused(spy, await _callback(_state()))


async def test_a_failed_identity_read_is_refused_and_writes_nothing(spy) -> None:
    spy.identity_raises = True
    await _assert_refused(spy, await _callback(_state()))


async def test_an_empty_secret_refuses_every_callback(spy, monkeypatch) -> None:
    token = _state()
    monkeypatch.setattr(get_settings(), "gateway_session_secret", "", raising=False)
    await _assert_refused(spy, await _callback(token))


@pytest.mark.parametrize(
    ("sent", "shown"),
    [
        ("access_denied", "access_denied"),
        ("consent_required", "consent_required"),
        ("Interaction_Required", "interaction_required"),
        ("<script>alert(1)</script>", "provider_error"),
    ],
)
async def test_a_provider_error_with_no_code_lands_on_the_callback_page(
    spy, sent, shown,
) -> None:
    resp = await _callback(None, code=None, error=sent)
    assert _error_of(resp) == shown
    assert spy.exchanged == 0 and spy.saved == []


async def test_no_code_and_no_error_is_invalid_state(spy) -> None:
    await _assert_refused(spy, await _callback(_state(), code=None))


def test_the_callback_query_parameters_are_optional() -> None:
    """``code`` and ``state`` were ``Query(...)``. FastAPI answered a provider
    error, which carries neither, with a raw 422."""
    import inspect

    sig = inspect.signature(oauth.oauth_callback)
    for name in ("code", "state", "error"):
        assert sig.parameters[name].default.default is None, name


async def test_a_save_failure_redirects_and_does_not_raise(spy, monkeypatch) -> None:
    async def _boom(**_kw):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(oauth, "_save_account", _boom)
    resp = await _callback(_state())
    assert _error_of(resp) == "account_save_failed"


def test_the_callback_writes_inside_one_tenant_session() -> None:
    """Item 4: one ``tenant_session(org)``, and no explicit ``commit()``.

    A statement after a commit in the middle of the block would run with no
    tenant bound, because ``SET LOCAL`` ends at commit. The R8 test proves the
    SQL. This proves the shape cannot drift back.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(oauth._save_account)))
    withs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncWith)]
    assert len(withs) == 1
    call = withs[0].items[0].context_expr
    assert isinstance(call, ast.Call) and call.func.id == "_tenant_session"
    assert [a.id for a in call.args] == ["org"]
    src = inspect.getsource(oauth)
    assert ".commit()" not in src
    assert "_get_db" not in src
