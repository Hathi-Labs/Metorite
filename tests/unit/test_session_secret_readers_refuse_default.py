"""Each reader of ``gateway_session_secret`` refuses the public default (task 3854).

``acb_common.settings`` ships ``change-me-dev-only`` as the default of
``GATEWAY_SESSION_SECRET``, and ``.env.example`` repeats it. A box that never
set the secret signs under a string that anyone can read. An HMAC under that
key is a plain hash. For the WhatsApp reply code, a table of 923 521 rows
then gives the code back from the stored hash.

``acb_auth.member_proof._usable`` is the one test of the secret, over
``PUBLIC_DEFAULT_SECRETS``. Each reader passes the secret through it.

R7 fences named here:

* ``reply-code-refuses-default``: ``acts.code_hash`` raises under an empty
  or public secret. ``park_code`` makes no code and stores nothing.
  ``_settle_code`` matches no code, counts no wrong try and gives an honest
  reply.
* ``session-secret-readers-guarded``: an AST walk of ``apps/``,
  ``packages/`` and ``scripts/`` finds each read of the secret. The function
  that reads it must name the shared check, or the allow-list must name the
  function with a reason.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
from pathlib import Path
from typing import Any

import pytest
import structlog
from acb_auth import member_proof
from acb_common import get_settings
from gateway.routes.whatsapp_channel import acts, bot_run

REPO = Path(__file__).resolve().parents[2]
FIELD = "gateway_session_secret"
ENV = "GATEWAY_SESSION_SECRET"
PUBLIC = "change-me-dev-only"
GOOD = "a-real-secret-3854"

# ── reply-code-refuses-default ──────────────────────────────────────────────


def _req() -> Any:
    return bot_run.RunRequest(message_id="m-1", organization_id="o-1",
                              member_email="jane@acme.test", wa_id="919990000001",
                              wamid="w-1", chat_session_id="th-1")


def _set(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), FIELD, value)


def test_the_settings_default_is_in_the_public_list() -> None:
    """The list that the check reads holds the default that settings ships."""
    from acb_common.settings import Settings

    default = Settings.model_fields[FIELD].default
    assert default == PUBLIC
    assert default in member_proof.PUBLIC_DEFAULT_SECRETS


@pytest.mark.parametrize("value", [PUBLIC, f"  {PUBLIC}  ", "", "   "])
def test_code_hash_refuses_an_empty_or_public_secret(monkeypatch, value: str) -> None:
    _set(monkeypatch, value)
    with pytest.raises(acts.CodeKeyUnusable) as caught:
        acts.code_hash("7K3Q")
    # The reason names the setting, never a value.
    assert PUBLIC not in str(caught.value)


def test_code_hash_works_under_a_real_secret(monkeypatch) -> None:
    _set(monkeypatch, GOOD)
    keyed = hmac.new(GOOD.encode(), b"whatsapp-act-code:7K3Q", hashlib.sha256).hexdigest()
    assert acts.code_hash("7k3q") == keyed


def test_a_hash_made_under_the_default_never_matches(monkeypatch) -> None:
    """The stored hash an attacker can make under the public key matches no code."""
    _set(monkeypatch, PUBLIC)
    forged = hmac.new(PUBLIC.encode(), b"whatsapp-act-code:7K3Q", hashlib.sha256).hexdigest()
    assert acts._code_matches(forged, "7K3Q") is False


async def test_park_code_makes_no_code_and_stores_nothing(monkeypatch) -> None:
    _set(monkeypatch, PUBLIC)
    touched: list[str] = []

    async def link(*_: Any) -> str:
        touched.append("link")
        return "link-1"

    async def store(*_: Any, **__: Any) -> str:
        touched.append("store")
        return "act-1"

    monkeypatch.setattr(acts, "_current_link_id", link)
    monkeypatch.setattr(acts, "_store", store)
    with structlog.testing.capture_logs() as caps:
        assert await acts.park_code(_req(), object()) is None
    assert touched == []
    lines = [c for c in caps if c["event"] == "whatsapp_channel.act.code_key_unusable"]
    assert len(lines) == 1 and lines[0]["log_level"] == "warning"
    assert lines[0]["step"] == "park"
    assert all(PUBLIC not in str(v) for c in caps for v in c.values())


async def test_the_member_reads_an_honest_reply_when_no_code_is_made(monkeypatch) -> None:
    """``bot_run`` sends the not-held line. It names no sign-in and no error."""
    _set(monkeypatch, PUBLIC)

    async def link(*_: Any) -> str:
        raise AssertionError("no link read without a usable key")

    monkeypatch.setattr(acts, "_current_link_id", link)

    class _Act:
        kind = "code"

    reply = await bot_run._offer_act(_req(), _Act())
    assert reply == acts.REPLY_NOT_HELD
    assert "web app" in reply and "sign" not in reply.lower()


async def test_settle_refuses_with_no_key_and_counts_no_try(monkeypatch) -> None:
    _set(monkeypatch, GOOD)
    stored = acts.code_hash("7K3Q")
    _set(monkeypatch, PUBLIC)
    closed: list[tuple[str, str, str, str | None]] = []

    async def close(act_id: str, frm: str, to: str, code: str | None) -> bool:
        closed.append((act_id, frm, to, code))
        return True

    async def wrong(_act_id: str) -> int:
        raise AssertionError("no wrong try without a usable key")

    monkeypatch.setattr(acts, "_close", close)
    monkeypatch.setattr(acts, "_wrong_code", wrong)
    act = {"id": "act-1", "confirm_code_hash": stored, "offered": True, "live": True,
           "tap_of": None, "member_email": "jane@acme.test", "link_id": "link-1",
           "card_text": "Send this draft?"}
    with structlog.testing.capture_logs() as caps:
        reply = await acts._settle_code(_req(), act, "YES 7K3Q")
    assert reply == acts.REPLY_CODE_KEY
    assert closed == [("act-1", "pending", "void", "code_key")]
    assert [c["step"] for c in caps
            if c["event"] == "whatsapp_channel.act.code_key_unusable"] == ["settle"]
    assert "web app" in reply and "sign" not in reply.lower()


# ── session-secret-readers-guarded ──────────────────────────────────────────

#: The names that prove a function passes the secret through the one check.
#: ``verify_member`` and ``sign_member`` call ``_usable`` first, and the test
#: below proves it, so a function that hands them the secret is guarded.
GUARDS = frozenset({"_usable", "PUBLIC_DEFAULT_SECRETS", "verify_member", "sign_member"})

#: ``path::function`` of a reader that names no guard, with the reason. Keep
#: it empty. An entry that matches no reader fails the walk.
ALLOWED: dict[str, str] = {}

#: Readers the walk must find, so it cannot pass with nothing found.
KNOWN = frozenset({
    "apps/services/gateway/gateway/routes/email/transport/signing.py::_secret",
    "apps/services/gateway/gateway/routes/v1_compat.py::_member_for",
    "apps/services/gateway/gateway/routes/whatsapp_channel/acts.py::_code_key",
    "packages/acb_llm/acb_llm/attribution.py::_proof_for",
    "packages/acb_skills/acb_skills/agent_paths.py::_author_secret",
})

_ROOTS = ("apps", "packages", "scripts")
_SKIP = frozenset({"tests", "node_modules", ".venv", "__pycache__", "build", "dist"})


def _is_read(node: ast.AST) -> bool:
    """A read of the secret: the attribute, ``getattr`` by name, or the env name."""
    if isinstance(node, ast.Attribute) and node.attr == FIELD:
        return True
    if isinstance(node, ast.Call):
        return any(isinstance(a, ast.Constant) and a.value in (FIELD, ENV)
                   for a in node.args)
    return False


def _names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add(node.asname or node.name)
    return out


def _readers(path: Path, rel: str) -> dict[str, bool]:
    """``rel::function`` of each read in *path*, and whether it names a guard."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    found: dict[str, bool] = {}

    def visit(node: ast.AST, fn: ast.AST | None, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, child, child.name)
                continue
            if _is_read(child):
                key = f"{rel}::{name}"
                guarded = fn is not None and bool(_names(fn) & GUARDS)
                found[key] = found.get(key, True) and guarded
            visit(child, fn, name)

    visit(tree, None, "<module>")
    return found


def _walk() -> dict[str, bool]:
    found: dict[str, bool] = {}
    for root in _ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            if _SKIP & set(path.relative_to(REPO).parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            source = path.read_text(encoding="utf-8")
            if FIELD not in source and ENV not in source:
                continue
            found.update(_readers(path, rel))
    return found


def test_each_reader_of_the_secret_passes_the_shared_check() -> None:
    found = _walk()
    missing = KNOWN - set(found)
    assert not missing, f"the walk lost known readers: {sorted(missing)}"
    bad = sorted(k for k, ok in found.items() if not ok and k not in ALLOWED)
    assert not bad, (
        "these functions read gateway_session_secret and do not pass it through "
        "acb_auth.member_proof._usable (or PUBLIC_DEFAULT_SECRETS). Refuse the "
        f"public default, or add a reason to ALLOWED: {bad}")
    stale = sorted(set(ALLOWED) - set(found))
    assert not stale, f"ALLOWED names readers that no longer exist: {stale}"


def test_the_handoff_guards_call_the_check_first() -> None:
    """``verify_member`` and ``sign_member`` count as guards only while they call it."""
    for fn in (member_proof.verify_member, member_proof.sign_member):
        assert "_usable(secret)" in inspect.getsource(fn), fn.__name__


def test_the_walk_sees_an_unguarded_reader(tmp_path) -> None:
    """The walk is not blind: a bare read under a plain HMAC goes red."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import hmac\nfrom acb_common import get_settings\n\n"
        "def sign(msg):\n"
        "    key = get_settings().gateway_session_secret.encode()\n"
        "    return hmac.new(key, msg, 'sha256').hexdigest()\n\n"
        "def sign_too(msg):\n"
        "    return getattr(get_settings(), 'gateway_session_secret', 'x')\n",
        encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text(
        "from acb_auth.member_proof import _usable\n\n"
        "def key(s):\n"
        "    raw = s.gateway_session_secret\n"
        "    return raw if _usable(raw) else None\n",
        encoding="utf-8")
    assert _readers(bad, "bad.py") == {"bad.py::sign": False, "bad.py::sign_too": False}
    assert _readers(good, "good.py") == {"good.py::key": True}
