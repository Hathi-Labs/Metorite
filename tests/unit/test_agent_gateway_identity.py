"""An agent's gateway calls name the person they are for, or they do not happen.

Spec: ``docs/multiplayer/bff-identity.md`` §2.

The BFF half of this rule is enforced in TypeScript
(``workbench/control_plane/src/lib/gateway.test.ts``). The same omission
existed on the Python side, in the tool clients that agents call the gateway
through::

    headers = {"Authorization": f"Bearer {_internal_token()}", ...}
    user = _current_user_email()
    if user:
        headers["X-User-Email"] = user     # ← and if not, the bearer alone
    return headers

``acb_auth/deps.py`` §1b reads a bearer with no identity headers as the
platform acting as itself and grants SERVICE_ACCESS. So a run that could not
resolve its user did not make an unscoped call, as the docstring claimed — it
made a call that could reach anybody's mail, chats and tasks.

Failing closed is the more correct answer and not merely the safer one: each of
these clients talks to an inherently per-person surface, so a run with nobody
attributed has nothing to do rather than everything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: (module label, file) for every tool client that calls the gateway with the
#: internal token. Loaded by path because these live in per-agent packages that
#: are not on the default import path.
CLIENTS = [
    ("whatsapp", REPO / "apps/agents/agent-whatsapp-assistant/agents.py"),
    ("email", REPO / "apps/agents/agent-email-assistant/agents.py"),
    ("crm", REPO / "apps/agents/agent-crm/agents.py"),
    ("task-gtd", REPO / "apps/skills/skill-my-tasks/skill_my_tasks/core.py"),
]


def _load(label: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"_ident_{label}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        pytest.skip(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # optional deps (agent frameworks) may be absent
        pytest.skip(f"{label}: {exc}")
    return mod


@pytest.mark.parametrize(("label", "path"), CLIENTS, ids=[c[0] for c in CLIENTS])
def test_a_run_with_nobody_to_act_as_refuses_rather_than_acting_as_the_platform(
    label: str, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load(label, path)
    monkeypatch.setattr(mod, "_current_user_email", lambda: "")

    with pytest.raises(RuntimeError) as exc:
        mod._headers()
    # The message is relayed to the agent verbatim, so it has to say what to do.
    # It names the run payload, not ACB_AGENT_USER_EMAIL: that env var WAS the
    # remedy the message advertised, and it was also S1-4 — one process-global
    # slot no run cleared, so following the advice handed the next unattributed
    # run this user. See tests/unit/test_agent_run_identity.py.
    assert "nobody to act as" in str(exc.value)
    assert "user_email" in str(exc.value)


@pytest.mark.parametrize(("label", "path"), CLIENTS, ids=[c[0] for c in CLIENTS])
def test_the_acting_user_travels_with_the_token(
    label: str, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load(label, path)
    monkeypatch.setattr(mod, "_current_user_email", lambda: "alice@fracktal.in")

    headers = mod._headers()
    assert headers["X-User-Email"] == "alice@fracktal.in"
    assert headers["Authorization"].startswith("Bearer ")


def _headers_source(path: Path) -> str:
    """The body of ``_headers``, up to the next top-level ``def``."""
    # Explicit encoding: these files carry non-ASCII (₹, emoji), and a bare
    # read_text() takes the platform default — cp1252 on Windows — so this
    # fence raised UnicodeDecodeError instead of checking anything.
    src = path.read_text(encoding="utf-8")
    start = src.index("def _headers(")
    rest = src[start:]
    end = rest.find("\ndef ", 1)
    return rest if end < 0 else rest[:end]


@pytest.mark.parametrize(("label", "path"), CLIENTS, ids=[c[0] for c in CLIENTS])
def test_the_identity_header_is_not_attached_conditionally(
    label: str, path: Path
) -> None:
    """The source-level invariant, so a fourth client cannot reintroduce it.

    A conditional around the header assignment is the exact shape of the bug:
    it reads as "attach it when we have it", and what it means is "escalate
    when we do not". Asserted against the source because the failure is the
    ABSENCE of a header — there is no wrong value for a runtime check to catch,
    and a correct call looks identical either way.

    Scoped to ``_headers`` deliberately: ``_current_user_email`` uses
    ``if user:`` legitimately, to walk its fallback chain. What must never be
    conditional is the assignment that decides whether the gateway is told who
    is asking.
    """
    body = _headers_source(path)
    assert "X-User-Email" in body
    header_line = next(ln for ln in body.splitlines() if "X-User-Email" in ln)
    # An unconditional dict entry sits at one indent level inside the return.
    assert not header_line.lstrip().startswith("headers["), (
        f"{label}: X-User-Email is assigned after the fact, so it can be skipped"
    )
    assert "if " not in body.split("X-User-Email")[0].split("raise RuntimeError")[-1]
