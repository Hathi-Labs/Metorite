"""WS-24c — scripts/setup_secrets.sh gives the identity token its own value.

Spec: project-docs/specs/org_access_control.md §8b (BO-2 residual #4).
Board: WS-24c, Metorite task 1843.

The old script appended ``GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}``, so a new
box started with the two secrets as one value. When ``.env`` came from
``.env.example``, the empty ``GATEWAY_INTERNAL_TOKEN=`` line already
existed. The ``grep -q`` matched it, the script wrote nothing, and the token
stayed blank. After WS-24b, a blank token gets 401 on every internal call.

These tests run a COPY of the script in a temp directory, against a fake
``.env``. The copy goes to the temp directory with ``cd``, never to
``/opt/acb/app``. ``_copy`` refuses a copy that still names the box path.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup_secrets.sh"
BOX_CD = "cd /opt/acb/app"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


pytestmark = [
    pytest.mark.skipif(_bash() is None, reason="needs a POSIX bash"),
    pytest.mark.skipif(shutil.which("openssl") is None,
                       reason="needs openssl, the generator of the script"),
]

BASE = (
    "POSTGRES_PASSWORD=oldpw\n"
    "LITELLM_MASTER_KEY=old-llm-key\n"
    "DATABASE_URL=postgresql+psycopg://acb:oldpw@db.example:5432/acb\n"
    "GATEWAY_SESSION_SECRET=old-session\n"
)


def _copy(tmp: Path, body: str | None = None) -> tuple[Path, Path]:
    """A copy of the script whose ``cd`` goes to ``tmp/app``."""
    app = tmp / "app"
    app.mkdir()
    text = SCRIPT.read_text(encoding="utf-8") if body is None else body
    assert text.count(BOX_CD) == 1, "the script must cd to the box path once"
    text = text.replace(BOX_CD, f'cd "{app.as_posix()}"')
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("/opt/acb" in ln for ln in code), "the copy must not reach the box path"
    copy = tmp / "setup_secrets.sh"
    copy.write_text(text, encoding="utf-8", newline="\n")
    return copy, app


def _run(tmp: Path, env_text: str, body: str | None = None) -> tuple[dict, str, Path]:
    copy, app = _copy(tmp, body)
    env = app / ".env"
    env.write_text(env_text, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [_bash(), copy.as_posix()], capture_output=True, text=True, encoding="utf-8",
        timeout=60, stdin=subprocess.DEVNULL, cwd=tmp,
        env={"PATH": os.environ.get("PATH", ""), "HOME": tmp.as_posix()},
    )
    assert r.returncode == 0, r.stderr
    return _parse(env), r.stdout, env


def _parse(env: Path) -> dict:
    out: dict = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


def _token_ok(kv: dict) -> str:
    assert len(kv["GATEWAY_INTERNAL_TOKEN"]) == 1, "one token line, no duplicate"
    token = kv["GATEWAY_INTERNAL_TOKEN"][0]
    assert _HEX64.match(token), "a 64-hex random token"
    assert token != kv["LITELLM_MASTER_KEY"][0], "never the new LLM key"
    assert token != "old-llm-key", "never the old LLM key"
    return token


def test_a_no_token_line_gets_a_distinct_token(tmp_path: Path) -> None:
    kv, _, _ = _run(tmp_path, BASE)
    _token_ok(kv)


def test_b_an_empty_token_line_gets_a_distinct_token(tmp_path: Path) -> None:
    """The .env.example case: the line exists and is empty."""
    kv, _, _ = _run(tmp_path, BASE + "GATEWAY_INTERNAL_TOKEN=\n")
    _token_ok(kv)


def test_c_a_distinct_token_is_kept(tmp_path: Path) -> None:
    """A box that has the split keeps it. The run does not rotate it."""
    kept = "a" * 64
    kv, out, _ = _run(tmp_path, BASE + f"GATEWAY_INTERNAL_TOKEN={kept}\n")
    assert kv["GATEWAY_INTERNAL_TOKEN"] == [kept]
    assert "kept it" in out


def test_d_a_token_equal_to_the_old_llm_key_is_replaced(tmp_path: Path) -> None:
    """The old script seeded this. The value is the key every agent held."""
    kv, out, _ = _run(tmp_path, BASE + "GATEWAY_INTERNAL_TOKEN=old-llm-key\n")
    _token_ok(kv)
    assert "wrote a new random token" in out


def test_the_real_env_example_gets_a_distinct_token(tmp_path: Path) -> None:
    kv, _, _ = _run(tmp_path, (ROOT / ".env.example").read_text(encoding="utf-8"))
    _token_ok(kv)


def test_a_last_line_with_no_newline_is_not_joined(tmp_path: Path) -> None:
    kv, _, _ = _run(tmp_path, BASE.rstrip("\n"))
    _token_ok(kv)
    assert kv["GATEWAY_SESSION_SECRET"][0] != "old-session"


def test_the_other_secrets_keep_their_behaviour(tmp_path: Path) -> None:
    """WS-24c changes the token only. Each run still rotates the other
    three, and still points DATABASE_URL at localhost."""
    kv, _, _ = _run(tmp_path, BASE + "GATEWAY_INTERNAL_TOKEN=\n")
    pw = kv["POSTGRES_PASSWORD"][0]
    assert pw != "oldpw" and len(pw) == 32
    assert kv["LITELLM_MASTER_KEY"][0] != "old-llm-key"
    assert len(kv["LITELLM_MASTER_KEY"][0]) == 64
    assert kv["GATEWAY_SESSION_SECRET"][0] != "old-session"
    assert kv["DATABASE_URL"] == [f"postgresql+psycopg://acb:{pw}@localhost:5432/acb"]
    assert kv["GATEWAY_BASE_URL"] == ["http://127.0.0.1:8000"]


def test_the_write_keeps_the_inode(tmp_path: Path) -> None:
    """WS-49 BH-2: the gateway sandbox bind-mounts the inode of .env."""
    copy, app = _copy(tmp_path)
    env = app / ".env"
    env.write_text(BASE + "GATEWAY_INTERNAL_TOKEN=\n", encoding="utf-8", newline="\n")
    before = env.stat().st_ino
    subprocess.run([_bash(), copy.as_posix()], check=True, capture_output=True,
                   timeout=60, stdin=subprocess.DEVNULL, cwd=tmp_path)
    assert env.stat().st_ino == before


def test_the_output_masks_every_value(tmp_path: Path) -> None:
    kv, out, _ = _run(tmp_path, BASE + "GATEWAY_INTERNAL_TOKEN=\n")
    for key in ("POSTGRES_PASSWORD", "LITELLM_MASTER_KEY",
                "GATEWAY_SESSION_SECRET", "GATEWAY_INTERNAL_TOKEN"):
        assert kv[key][0] not in out
        assert f"{key}=***" in out


def test_the_old_seed_line_goes_red(tmp_path: Path) -> None:
    """Mutation: put the old line back, and case (b) keeps a blank token."""
    body = SCRIPT.read_text(encoding="utf-8")
    start = body.index('NEW_LLM_KEY="$(env_value LITELLM_MASTER_KEY)"')
    end = body.index("# Add missing vars")
    old = 'grep -q "^GATEWAY_INTERNAL_TOKEN=" .env || echo "GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}" >> .env\n'
    mutant = body[:start] + old + body[end:]
    kv, _, _ = _run(tmp_path, BASE + "GATEWAY_INTERNAL_TOKEN=\n", mutant)
    assert kv["GATEWAY_INTERNAL_TOKEN"] == [""]
    with pytest.raises(AssertionError):
        _token_ok(kv)
