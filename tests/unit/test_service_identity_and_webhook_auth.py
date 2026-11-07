"""BO-2 residuals #3 and #4 — service identity vs. LLM key, and webhook signing.

Two holes that let a caller act as the platform:

* **The LLM key was the identity token.** ``_get_internal_token`` fell back to
  ``LITELLM_MASTER_KEY``, and the orchestrator handed agents whichever value it
  resolved to. Every agent therefore held a credential that grants
  ``SERVICE_ACCESS`` — it could call ``/admin/*`` and grant itself anything,
  defeating the entire org access model. WS-24b (2026-11-06) removed the
  fallback. An unset token now fails closed outside ``ACB_ENV=dev``.

* **``/agent/webhook/{source}`` was unauthenticated.** It dispatches an agent
  run, so it was a fourth run path bypassing ``assert_can_run_agent``.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acb_auth import require_internal_auth, require_llm_api_auth
from acb_auth.deps import _get_internal_token, _get_llm_api_token

IDENTITY = "sk-identity-secret"
LLM_KEY = "sk-llm-api-key"


@pytest.fixture
def split_tokens(monkeypatch):
    """A correctly provisioned deployment: two distinct secrets."""
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", IDENTITY)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    return IDENTITY, LLM_KEY


# ── Token resolution ────────────────────────────────────────────────────────

def test_the_two_tokens_are_distinct_when_provisioned(split_tokens) -> None:
    assert _get_internal_token() == IDENTITY
    assert _get_llm_api_token() == LLM_KEY
    assert _get_internal_token() != _get_llm_api_token()


def test_identity_never_falls_back_to_the_llm_key(unprovisioned) -> None:
    """WS-24b (2026-11-06): an unset token is unset. It is never the LLM key.

    This test used to pin the fallback (``..._falls_back_to_the_llm_key_when_
    unprovisioned``). The fallback made the key that every agent holds a
    platform credential, so the pin now runs the other way.
    """
    assert _get_llm_api_token() == LLM_KEY
    assert _get_internal_token() == ""


def test_no_secrets_at_all_gives_no_identity_token(unprovisioned, monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert _get_internal_token() == ""


# ── An unprovisioned box fails closed ───────────────────────────────────────
#
# With no GATEWAY_INTERNAL_TOKEN, no Bearer is service identity, and nothing
# else stands in for one. Before WS-24b the LLM-key fallback kept the token
# non-empty, so these checks were closed on every real box. Removing the
# fallback must not open them.

VICTIM = "someone-else@fracktal.in"


@pytest.fixture
def unprovisioned(monkeypatch):
    """A production box that forgot GATEWAY_INTERNAL_TOKEN, LLM key set."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.delenv("GATEWAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(settings, "gateway_internal_token", "", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    monkeypatch.setattr(settings, "acb_env", "prod", raising=False)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)

    async def _passthrough(user):
        return user

    monkeypatch.setattr("acb_auth.deps._with_resolved_access", _passthrough)
    return settings


async def test_the_llm_key_cannot_assert_an_identity(unprovisioned) -> None:
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert user.email is None
    assert not user.has_permission("*")


async def test_the_llm_key_is_not_service_access(unprovisioned) -> None:
    """A bare Bearer that matched would be SERVICE_ACCESS (branch 1b)."""
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=None, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert user.email != "system:internal"
    assert not user.has_permission("*")


async def test_a_bare_identity_header_is_refused(unprovisioned) -> None:
    """No token to check means no proof, so the header is not trusted."""
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=VICTIM, x_user_role=None, authorization=None,
    )
    assert user.email is None
    assert not user.has_permission("*")


def test_identity_routes_answer_401(unprovisioned) -> None:
    client = _client()
    assert client.get("/identity").status_code == 401
    assert client.get(
        "/identity", headers={"Authorization": f"Bearer {LLM_KEY}"}
    ).status_code == 401
    assert client.get(
        "/identity", headers={"Authorization": "Bearer "}
    ).status_code == 401


def test_v1_still_accepts_the_llm_key(unprovisioned) -> None:
    """The /v1 key keeps its one job, so agents can still reach their model."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 200


async def test_a_dev_laptop_stays_open_but_the_llm_key_is_still_no_identity(
    unprovisioned, monkeypatch
) -> None:
    """ACB_ENV=dev keeps the old open behaviour of an unset token. The LLM key
    is not service identity there either."""
    from acb_auth.deps import get_current_user

    monkeypatch.setattr(unprovisioned, "acb_env", "dev", raising=False)
    assert _client().get("/identity").status_code == 200
    header_only = await get_current_user(
        x_user_email="dev@fracktal.in", x_user_role=None, authorization=None,
    )
    assert header_only.email == "dev@fracktal.in"
    bare_key = await get_current_user(
        x_user_email=None, x_user_role=None, authorization=f"Bearer {LLM_KEY}",
    )
    assert bare_key.email != "system:internal"
    assert not bare_key.has_permission("*")


async def test_a_provisioned_token_is_identity_and_the_llm_key_is_not(
    split_tokens, monkeypatch
) -> None:
    from acb_auth.deps import get_current_user

    async def _passthrough(user):
        return user

    monkeypatch.setattr("acb_auth.deps._with_resolved_access", _passthrough)

    proxied = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {IDENTITY}",
    )
    assert proxied.email == VICTIM

    agent = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert agent.email is None


# ── No caller sends the LLM key as service identity ─────────────────────────
#
# WS-24c (task 1843) folded the wide scanner of
# ``test_no_llm_key_identity_fence.py`` into this file. PR 1765 (WS-24b) is
# merged, so no file keeps an old shape and the known-debt set is gone. The
# two regexes of the first WS-24b fence stay as the ``legacy`` shape, so the
# set of shapes caught did not shrink.
#
# The scan reads ``.py``, ``.ts``, ``.tsx``, ``.sh``, ``.mjs`` and ``.js``
# under the trees in ``_TREES``. Its shapes:
#
# 1. ``fallback``: an identity read, then ``or``, ``||`` or ``??``, then an
#    LLM-key read, in one expression. Comments and blank lines do not count.
# 2. ``reverse``: the same with the LLM key first.
# 3. ``nested``: an identity getter whose default argument reads the LLM key.
#    The shell form ``${GATEWAY_INTERNAL_TOKEN:-$LITELLM_MASTER_KEY}`` too.
# 4. ``seed``: a name that says identity is set from an LLM-key read.
# 5. ``reassign``: a variable that holds the identity token gets the LLM key
#    in a later line of the same block.
# 6. ``bearer``: ``Bearer`` with an LLM-key read, and no ``/v1`` target in
#    the lines before it. A ``/v1`` client may send the LLM key.
# 7. ``legacy``: the two regexes of the first WS-24b fence, on the raw text.

ROOT = Path(__file__).resolve().parents[2]

#: The first WS-24b fence: an identity read that falls back to the LLM key.
_LEGACY_FALLBACK = re.compile(
    r"(?:gateway_internal_token|GATEWAY_INTERNAL_TOKEN)[^\n]{0,80}"
    r"(?:\n[^\n]{0,80}){0,2}?(?:\bor\b|\?\?)\s*"
    r"(?:getattr\([^)]*?|os\.environ\.get\(|os\.getenv\(|process\.env\.)\s*"
    r"[\"']?(?:litellm_master_key|LITELLM_MASTER_KEY)"
)
#: The nested-default shape of the same fallback.
_LEGACY_NESTED = re.compile(
    r"GATEWAY_INTERNAL_TOKEN[\"'],\s*os\.environ\.get\(\s*[\"']LITELLM_MASTER_KEY"
)

_TREES = (
    "apps", "packages", "scripts", "evals", "deploy", "tests/integration",
    "workbench/control_plane/src", "workbench/operator_console/src",
)
_SUFFIXES = {".py", ".ts", ".tsx", ".sh", ".mjs", ".js"}
_SKIP_PARTS = {"node_modules", ".next", "dist", "build", "__pycache__", ".venv"}

#: An identity-token read. ``INTERNAL_TOKEN`` covers the env name and the
#: TS constant. ``_internal_token(`` covers the Python helpers.
_ID = (
    r"(?:gateway_internal_token|GATEWAY_INTERNAL_TOKEN|\bINTERNAL_TOKEN\b"
    r"|_get_internal_token\s*\(|\b_internal_token\s*\(|\binternalToken\b)"
)
#: An LLM-key read. ``LITELLM_KEY`` is the shell name in setup_secrets.sh.
_LLM = (
    r"(?:litellm_master_key|LITELLM_MASTER_KEY|\bllm_api_key\b|\bLLM_API_KEY\b"
    r"|_get_llm_api_token\s*\(|\bLITELLM_KEY\b)"
)
#: ``else`` is the Python ternary: ``a if a else b`` falls back to ``b``.
_OR = r"(?:\bor\b|\belse\b|\|\||\?\?)"

#: Between the two reads: no statement end, and no comparison. A comparison
#: (``key == a or key == b``) checks a value and sends nothing.
_GAP = r"[^;{}\n=]{0,160}"

#: After the operator: the read itself, through one accessor at most. So
#: ``x or ""`` and a comma list that names the LLM key later do not hit.
_ACCESS = (
    r"\s*\(?\s*(?:getattr\(\s*[\w.()]+\s*,\s*[\"']|[\w.]*[(\[]\s*[\"']"
    r"|[\w.()]*\.|\$\{?)?\s*"
)
_FALLBACK = re.compile(_ID + _GAP + _OR + _ACCESS + _LLM)
_REVERSE = re.compile(_LLM + _GAP + _OR + _ACCESS + _ID)
#: A read of a value, just before the LLM-key name. It is a template or
#: shell expansion, wrapper calls such as ``str(``, then a call or subscript
#: with a quoted name, or an attribute access. It is never a bare quote, so a
#: list, a set or a dict key that only names the env var is not a read.
_READ = (
    r"(?:f[\"']\{|`\$\{|[\"']?\$\{?)?"
    r"(?:\w+\(\s*)*"
    r"(?:[\w.]+\s*[(\[]\s*(?:[\w.()]+\s*,\s*)?[\"']|\w[\w.()]*\.)?"
)
#: A getter whose default argument is a read of the LLM key.
_NESTED = re.compile(
    r"(?:getenv|\.get|getattr)\s*\(\s*(?:[\w.()]+\s*,\s*)?[\"']"
    r"(?:GATEWAY_INTERNAL_TOKEN|gateway_internal_token)[\"']\s*,\s*" + _READ + _LLM
    + r"|\$\{GATEWAY_INTERNAL_TOKEN:?[-=]\s*\"?\$\{?" + _LLM
)
_SEED = re.compile(
    r"(?:[\"']?\w*(?:internal|INTERNAL|identity|IDENTITY)\w*(?:token|TOKEN)\w*[\"']?\]?)"
    r"\s*(?::\s*[\w\[\]|. ]+)?\s*(?<![=!<>])[:=](?!=)\s*" + _READ + _LLM
)
_BEARER = re.compile(r"Bearer[^\n]{0,60}?" + _LLM)
#: A ``Bearer`` string that ends its line, so the next lines add the token.
_BEARER_OPEN = re.compile(r"Bearer\s*[\"'`]\s*\+?\s*$")
_BEARER_SPLIT = re.compile(r"Bearer\s*[\"'`][\s+()]*" + _READ + _LLM)
_V1 = re.compile(r"/v1\b|chat/completions|/embeddings|v1Base\s*\(|LITELLM_BASE_URL|litellm_base_url")

#: A shell test compares with one ``=``. It checks a value and sets nothing.
_SHELL_TEST = re.compile(r"\[\[?\s.*\s\]\]?")
_COMMENT = re.compile(r"^\s*(?:#|//|\*|/\*)")
_ASSIGN_ID = re.compile(r"^\s*(?:const|let|var|local)?\s*(\w+)\s*(?::[^=]+)?=(?!=)[^\n]*" + _ID)
_STATEMENT = re.compile(r"^\s*(?:def|class|async def|function|export)\b")


_TRIPLE = re.compile(r'("""|\'\'\')(.*?)\1', re.S)


def _code_lines(text: str) -> list[tuple[int, str]]:
    """The code lines with their line numbers, comments and blanks out.

    A docstring is prose, so a triple-quoted string goes too, and its line
    count stays. A trailing ``#`` or ``//`` comment goes too. A ``#`` inside
    a string can cut a line short. That only hides text, so it cannot add a
    false hit.
    """
    text = _TRIPLE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or _COMMENT.match(line):
            continue
        line = re.sub(r"\s(?:#|//)\s.*$", "", line)
        out.append((n, line))
    return out


def scan(text: str) -> list[tuple[str, int]]:
    """Every hit in *text*, as (shape, line number)."""
    lines = _code_lines(text)
    hits: list[tuple[str, int]] = []

    # One string of code, with the original line number kept per line. A
    # newline stays, but the expression shapes may cross up to 6 code lines.
    for i in range(len(lines)):
        window = " ".join(ln for _, ln in lines[i:i + 6])
        first = lines[i][1]
        for shape, rx in (("fallback", _FALLBACK), ("reverse", _REVERSE)):
            m = rx.search(window)
            if m and m.start() < len(first):
                hits.append((shape, lines[i][0]))
        if _NESTED.search(first):
            hits.append(("nested", lines[i][0]))
        if _SEED.search(first) and not _SHELL_TEST.search(first):
            hits.append(("seed", lines[i][0]))
        bearer = _BEARER.search(first)
        if not bearer and _BEARER_OPEN.search(first):
            bearer = _BEARER_SPLIT.search(" ".join(ln for _, ln in lines[i:i + 3]))
        if bearer:
            before = " ".join(ln for _, ln in lines[max(0, i - 8):i + 1])
            if not _V1.search(before):
                hits.append(("bearer", lines[i][0]))
        m = _ASSIGN_ID.match(first)
        if m:
            var = re.escape(m.group(1))
            later = re.compile(
                rf"^\s*(?:if\b[^\n]*)?\b{var}\s*(?:=|\|\|=|\?\?=|:=)(?!=)[^\n]*" + _LLM
                + rf"|\$\{{{var}:?-\s*\"?\$\{{?" + _LLM
            )
            for _, nxt in lines[i + 1:i + 30]:
                if _STATEMENT.match(nxt):
                    break
                if later.search(nxt):
                    hits.append(("reassign", lines[i][0]))
                    break
    # The two regexes of the first WS-24b fence, on the raw text. They stay,
    # so that the set of shapes that this fence catches never shrinks.
    for rx in (_LEGACY_FALLBACK, _LEGACY_NESTED):
        for m in rx.finditer(text):
            hits.append(("legacy", text.count("\n", 0, m.start()) + 1))
    return sorted(set(hits), key=lambda h: (h[1], h[0]))


def _tree_files() -> list[Path]:
    out = []
    for tree in _TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix in _SUFFIXES and p.is_file() and not _SKIP_PARTS & set(p.parts):
                out.append(p)
    return sorted(out)


# ── The positive controls ────────────────────────────────────────────────────

#: Each sample is a shape that escaped the WS-24b fence. Each must hit.
ESCAPED = {
    "attribute access": (
        "token = settings.gateway_internal_token or settings.litellm_master_key\n",
        "fallback",
    ),
    "llm_api_key fallback": (
        "token = settings.gateway_internal_token or settings.llm_api_key\n",
        "fallback",
    ),
    "helper fallback": (
        "token = _get_internal_token() or _get_llm_api_token()\n",
        "fallback",
    ),
    "nested getenv": (
        'TOKEN = os.getenv("GATEWAY_INTERNAL_TOKEN", os.getenv("LITELLM_MASTER_KEY"))\n',
        "nested",
    ),
    "typescript or": (
        "const t = process.env.GATEWAY_INTERNAL_TOKEN || process.env.LITELLM_MASTER_KEY;\n",
        "fallback",
    ),
    "environ subscript as bearer": (
        'headers = {"Authorization": f"Bearer {os.environ[\'LITELLM_MASTER_KEY\']}"}\n'
        'httpx.post(f"{base}/internal/tasks", headers=headers)\n',
        "bearer",
    ),
    "far fallback": (
        "token = (\n"
        "    settings.gateway_internal_token\n"
        "    # The LLM key keeps an old box working.\n"
        "\n"
        "    # Remove it when every box has the split.\n"
        "    # Ask the owner first.\n"
        "    or settings.litellm_master_key\n"
        ")\n",
        "fallback",
    ),
    "reverse order": (
        "token = settings.litellm_master_key or settings.gateway_internal_token\n",
        "reverse",
    ),
}

#: More shapes in the languages the old fence did not read.
EXTRA = {
    "shell seed": ('echo "GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}" >> .env\n', "seed"),
    "shell default": ('token="${GATEWAY_INTERNAL_TOKEN:-$LITELLM_MASTER_KEY}"\n', "nested"),
    "typescript nullish": (
        "const t = process.env.GATEWAY_INTERNAL_TOKEN ?? process.env.LITELLM_MASTER_KEY;\n",
        "fallback",
    ),
    "typescript reverse": (
        'const t = process.env.LITELLM_MASTER_KEY ?? process.env.GATEWAY_INTERNAL_TOKEN ?? "x";\n',
        "reverse",
    ),
    "getattr fallback": (
        "return (\n"
        '    getattr(settings, "gateway_internal_token", "")\n'
        '    or getattr(settings, "litellm_master_key", "")\n'
        ")\n",
        "fallback",
    ),
    "environ get nested": (
        'T = os.environ.get("GATEWAY_INTERNAL_TOKEN", os.environ.get("LITELLM_MASTER_KEY", ""))\n',
        "nested",
    ),
    "named seed": ('internal_token = os.environ["LITELLM_MASTER_KEY"]\n', "seed"),
    "reassign later": (
        "token = settings.gateway_internal_token\n"
        "log.info('x')\n"
        "log.info('y')\n"
        "log.info('z')\n"
        "if not token:\n"
        "    token = settings.litellm_master_key\n",
        "reassign",
    ),
    "js or": (
        "const t = process.env.GATEWAY_INTERNAL_TOKEN || process.env.LITELLM_MASTER_KEY\n",
        "fallback",
    ),
}

#: The legitimate /v1 readers. None may hit.
CLEAN = {
    "v1 provider": "api_key = settings.llm_api_key\n",
    "v1 bearer": (
        'res = await gatewayFetch(`${v1Base()}/chat/completions`, {\n'
        '  headers: { Authorization: `Bearer ${LITELLM_KEY}` },\n'
    ),
    "ts v1 key": 'const LITELLM_KEY =\n  process.env.LITELLM_MASTER_KEY ?? "";\n',
    "identity only": 'const INTERNAL_TOKEN = process.env.GATEWAY_INTERNAL_TOKEN ?? "";\n',
    "comparison": (
        'if (key === INTERNAL_TOKEN || key === (process.env.LITELLM_MASTER_KEY ?? "").trim()) {\n'
    ),
    "python comparison": "if tok == settings.gateway_internal_token or tok == settings.litellm_master_key:\n",
    "shell new token": 'INTERNAL_TOKEN=$(openssl rand -hex 32)\n',
    "two reads apart": (
        "def a():\n"
        "    return settings.gateway_internal_token\n"
        "def b():\n"
        "    return settings.litellm_master_key or ''\n"
    ),
    "distinct check": 'if [ "$current" = "$LITELLM_KEY" ]; then\n',
    "shell identity check": 'elif [ "$INTERNAL_TOKEN" = "$LITELLM_KEY" ]; then\n',
}


#: Shapes the review of PR 1843 found. Each must hit.
REVIEW = {
    "python ternary": (
        "token = s.gateway_internal_token if s.gateway_internal_token else s.litellm_master_key\n",
        "fallback",
    ),
    "subscript seed from environ": (
        "env['GATEWAY_INTERNAL_TOKEN'] = os.environ['LITELLM_MASTER_KEY']\n",
        "seed",
    ),
    "subscript seed from settings": (
        'env["GATEWAY_INTERNAL_TOKEN"] = settings.litellm_master_key\n',
        "seed",
    ),
    "split bearer": (
        "headers = {\n"
        "    'Authorization': (\n"
        "        'Bearer '\n"
        "        + settings.litellm_master_key\n"
        "    ),\n"
        "}\n",
        "bearer",
    ),
    "getattr nested": (
        'T = getattr(settings, "gateway_internal_token", getattr(settings, "litellm_master_key", ""))\n',
        "nested",
    ),
}

#: A bare quoted name in a list, a tuple, a set or a dict key is not a read.
#: None may hit. The first two are copies of real lines in the tree.
NOT_A_READ = {
    "ts loop list (emailOtpAdapter.test.ts)": (
        "    for (const secret of [\n"
        '      "GATEWAY_INTERNAL_TOKEN",\n'
        '      "LITELLM_MASTER_KEY",\n'
        '      "CUSTOMER_CONSOLE_DEPLOYMENT_KEY",\n'
        "    ]) {\n"
    ),
    "redaction set (agent_signing.py)": (
        "    values = {\n"
        '        os.getenv("GATEWAY_INTERNAL_TOKEN", ""),\n'
        '        str(getattr(settings, "gateway_internal_token", "") or ""),\n'
        '        os.getenv("LITELLM_MASTER_KEY", ""),\n'
        '        str(getattr(settings, "litellm_master_key", "") or ""),\n'
        "    }\n"
    ),
    "required tuple": 'REQUIRED = ("GATEWAY_INTERNAL_TOKEN", "LITELLM_MASTER_KEY")\n',
    "redaction set on one line": 'REDACT = {"GATEWAY_INTERNAL_TOKEN", "LITELLM_MASTER_KEY"}\n',
    "ts list": "const names = ['GATEWAY_INTERNAL_TOKEN', 'LITELLM_MASTER_KEY'];\n",
    "secret keys list": 'SECRET_KEYS = ["GATEWAY_INTERNAL_TOKEN", "LITELLM_MASTER_KEY", "X"]\n',
    "child env dict": 'env = {"GATEWAY_INTERNAL_TOKEN": tok, "LITELLM_MASTER_KEY": key}\n',
    "identity token names list": 'INTERNAL_TOKEN_NAMES = ["LITELLM_MASTER_KEY", "GATEWAY_INTERNAL_TOKEN"]\n',
    "ts env object": "const env = { GATEWAY_INTERNAL_TOKEN: tok, LITELLM_MASTER_KEY: key };\n",
    "getenv with a literal default": 'T = os.getenv("GATEWAY_INTERNAL_TOKEN", "LITELLM_MASTER_KEY")\n',
}


@pytest.mark.parametrize("name", sorted(ESCAPED))
def test_each_shape_that_escaped_ws24b_is_caught(name: str) -> None:
    text, shape = ESCAPED[name]
    assert shape in {s for s, _ in scan(text)}, (name, scan(text))


@pytest.mark.parametrize("name", sorted(EXTRA))
def test_each_extra_shape_is_caught(name: str) -> None:
    text, shape = EXTRA[name]
    assert shape in {s for s, _ in scan(text)}, (name, scan(text))


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_the_legitimate_reads_do_not_hit(name: str) -> None:
    assert scan(CLEAN[name]) == [], (name, scan(CLEAN[name]))


@pytest.mark.parametrize("name", sorted(REVIEW))
def test_each_shape_from_the_review_is_caught(name: str) -> None:
    text, shape = REVIEW[name]
    assert shape in {s for s, _ in scan(text)}, (name, scan(text))


@pytest.mark.parametrize("name", sorted(NOT_A_READ))
def test_a_name_that_is_not_a_read_does_not_hit(name: str) -> None:
    assert scan(NOT_A_READ[name]) == [], (name, scan(NOT_A_READ[name]))


def test_the_scan_reads_every_language_and_tree() -> None:
    """The old fence read .py, .ts and .tsx only, and skipped deploy/."""
    files = _tree_files()
    rel = {p.relative_to(ROOT).parts[0] for p in files}
    assert {"apps", "packages", "scripts", "evals", "deploy"} <= rel
    assert {p.suffix for p in files} >= {".py", ".ts", ".sh", ".mjs"}


#: The samples of the first WS-24b fence. Each must still hit.
LEGACY = {
    "getattr chain": (
        'return (\n    getattr(settings, "gateway_internal_token", "")\n'
        '    or getattr(settings, "litellm_master_key", "")\n    or "sk-local"\n)'
    ),
    "ts nullish across lines": (
        "process.env.GATEWAY_INTERNAL_TOKEN ??\n  process.env.LITELLM_MASTER_KEY ??"
    ),
    "environ or across lines": (
        'os.environ.get("GATEWAY_INTERNAL_TOKEN")\n    or os.environ.get("LITELLM_MASTER_KEY")'
    ),
    "environ nested": (
        'os.environ.get("GATEWAY_INTERNAL_TOKEN", os.environ.get("LITELLM_MASTER_KEY", ""))'
    ),
    "keyword default": (
        'tok = os.getenv("GATEWAY_INTERNAL_TOKEN", default="") or os.getenv("LITELLM_MASTER_KEY")'
    ),
}

#: The legal reads of the first WS-24b fence. None may hit.
LEGACY_CLEAN = {
    "llm key alone": 'key = getattr(settings, "litellm_master_key", "")',
    "two names side by side": (
        'os.getenv("GATEWAY_INTERNAL_TOKEN", ""),\n        os.getenv("LITELLM_MASTER_KEY", ""),'
    ),
}


@pytest.mark.parametrize("name", sorted(LEGACY))
def test_each_shape_of_the_first_fence_is_still_caught(name: str) -> None:
    assert scan(LEGACY[name]), name


@pytest.mark.parametrize("name", sorted(LEGACY_CLEAN))
def test_the_legal_reads_of_the_first_fence_do_not_hit(name: str) -> None:
    assert scan(LEGACY_CLEAN[name]) == [], (name, scan(LEGACY_CLEAN[name]))


def test_the_scan_reads_every_language_and_tree() -> None:
    """The first fence read .py, .ts and .tsx only, and skipped deploy/."""
    files = _tree_files()
    rel = {p.relative_to(ROOT).parts[0] for p in files}
    assert {"apps", "packages", "scripts", "evals", "deploy"} <= rel
    assert {p.suffix for p in files} >= {".py", ".ts", ".sh", ".mjs"}


def test_no_caller_sends_the_llm_key_as_service_identity() -> None:
    """Zero hits in the tree. No known-debt set exists (PR 1765 merged).

    The gateway does not accept the LLM key as identity, so a caller that
    falls back to it sends a token that gets 401. A box that seeds the two as
    one value has no split at all.
    """
    found = []
    for p in _tree_files():
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        for shape, line in scan(text):
            found.append(f"{rel}:{line} ({shape})")
    assert not found, (
        "These lines send, seed or fall back to the LLM key as the "
        f"service-identity token. Use GATEWAY_INTERNAL_TOKEN only: {found}"
    )


# ── One reader of the internal token, one header rule (WS-24c slice 2) ──────

#: The one module that may define the caller-side token reader.
_ACCESSOR = "packages/acb_common/acb_common/settings.py"
#: A function name that says "I read the internal token". The gateway side
#: (``acb_auth.deps._get_internal_token``) is the verifier, not a caller.
_COPY_NAMES = frozenset({"_internal_token", "internal_token", "internal_service_token"})


def _token_reader_copies() -> list[str]:
    """Every ``def`` of a token reader under the code trees, except the one."""
    import ast

    out = []
    for tree in ("apps", "packages", "scripts", "evals"):
        for p in sorted((ROOT / tree).rglob("*.py")):
            if _SKIP_PARTS & set(p.parts):
                continue
            rel = p.relative_to(ROOT).as_posix()
            try:
                mod = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(mod):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in _COPY_NAMES and rel != _ACCESSOR):
                    out.append(f"{rel}:{node.lineno} ({node.name})")
    return out


def test_no_copy_of_the_internal_token_reader() -> None:
    """R7. One reader: ``acb_common.settings.internal_service_token``.

    Ten copies of ``_internal_token`` each held the same rule. WS-24b had to
    edit all ten to drop the LLM-key rung. A new copy fails here. Mutation:
    add ``def _internal_token()`` to any module under ``apps/`` -> red.
    """
    assert _token_reader_copies() == [], (
        "Use acb_common.settings.internal_service_token() or "
        "internal_auth_headers(). Do not copy the reader."
    )


def test_no_caller_builds_the_bearer_from_the_raw_token() -> None:
    """A caller spreads ``internal_auth_headers()``. It does not write
    ``Bearer {internal_service_token()}``, which sends ``Bearer `` when the
    token is empty. Mutation: write that line in any caller -> red."""
    rx = re.compile(r"Bearer \{\s*internal_service_token\(")
    found = [
        p.relative_to(ROOT).as_posix()
        for tree in ("apps", "packages", "scripts", "evals")
        for p in sorted((ROOT / tree).rglob("*.py"))
        if not _SKIP_PARTS & set(p.parts)
        and rx.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert found == [], found


def test_the_accessor_reads_the_token_and_never_the_llm_key(
    unprovisioned, monkeypatch
) -> None:
    from acb_common.settings import internal_service_token

    assert internal_service_token() == ""
    monkeypatch.setattr(unprovisioned, "gateway_internal_token", " from-settings ")
    assert internal_service_token() == "from-settings"
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", IDENTITY)
    assert internal_service_token() == IDENTITY


def test_the_accessor_sends_what_the_gateway_checks(split_tokens) -> None:
    from acb_common.settings import internal_service_token

    assert internal_service_token() == _get_internal_token() == IDENTITY


def test_an_empty_token_sends_no_authorization_header(unprovisioned) -> None:
    """``Bearer `` with no token is an illegal header value in httpx."""
    from acb_common.settings import internal_auth_headers

    assert internal_auth_headers() == {}


def test_a_set_token_sends_its_bearer(split_tokens) -> None:
    from acb_common.settings import internal_auth_headers

    assert internal_auth_headers() == {"Authorization": f"Bearer {IDENTITY}"}


def test_no_header_gets_a_clean_401_from_the_gateway(unprovisioned) -> None:
    """The call leaves with no header, and the identity route answers 401.

    The wire layer of httpx (h11) refuses the old ``Bearer `` header with
    ``LocalProtocolError``, so the caller crashed before any answer came.
    """
    import h11

    from acb_common.settings import internal_auth_headers

    def _send(headers: dict[str, str]) -> None:
        h11.Connection(h11.CLIENT).send(h11.Request(
            method="GET", target="/identity",
            headers=[("Host", "gw"), *headers.items()]))

    with pytest.raises(h11.LocalProtocolError):
        _send({"Authorization": "Bearer "})
    _send(internal_auth_headers())
    assert _client().get("/identity", headers=internal_auth_headers()).status_code == 401


# ── The guards ──────────────────────────────────────────────────────────────

def _client() -> TestClient:
    from fastapi import Depends

    app = FastAPI()

    @app.get("/identity", dependencies=[Depends(require_internal_auth)])
    async def _identity() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/models", dependencies=[Depends(require_llm_api_auth)])
    async def _v1() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_llm_key_does_not_open_identity_routes(split_tokens) -> None:
    """The headline fix: an agent's key is not a platform credential."""
    res = _client().get("/identity", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 401


def test_identity_token_opens_identity_routes(split_tokens) -> None:
    res = _client().get("/identity", headers={"Authorization": f"Bearer {IDENTITY}"})
    assert res.status_code == 200


def test_v1_accepts_the_llm_key(split_tokens) -> None:
    """Agents route completions with it, so /v1 must keep working."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 200


def test_v1_also_accepts_the_identity_token(split_tokens) -> None:
    """The Next.js server and internal jobs route completions too."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {IDENTITY}"})
    assert res.status_code == 200


def test_v1_rejects_an_unknown_token(split_tokens) -> None:
    res = _client().get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_both_routes_reject_a_missing_header(split_tokens) -> None:
    client = _client()
    assert client.get("/identity").status_code == 401
    assert client.get("/v1/models").status_code == 401


# ── The BYOK clients must not carry identity ────────────────────────────────

def test_llm_api_key_accessor_never_returns_the_identity_token(
    split_tokens, monkeypatch
) -> None:
    """`settings.llm_api_key` is what every /v1 client is pointed at."""
    from acb_common import Settings

    settings = Settings(
        litellm_master_key=LLM_KEY, gateway_internal_token=IDENTITY
    )
    assert settings.llm_api_key == LLM_KEY
    assert settings.llm_api_key != settings.gateway_internal_token


def test_no_v1_client_reads_the_identity_token() -> None:
    """Guards against a regression re-introducing the escalation.

    Every module that builds a `/v1` provider config must resolve its key from
    `llm_api_key`. Modules calling gateway *business* APIs still legitimately
    use `gateway_internal_token` and are excluded by name.
    """
    import pathlib

    v1_client_modules = [
        "apps/services/orchestrator/orchestrator/agents.py",
        "apps/services/orchestrator/orchestrator/_model_resolution.py",
        "apps/services/orchestrator/orchestrator/code_session.py",
        "apps/services/orchestrator/orchestrator/mutation.py",
    ]
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in v1_client_modules:
        source = (root / rel).read_text(encoding="utf-8")
        offending = [
            line.strip()
            for line in source.split("\n")
            if "gateway_internal_token" in line and not line.strip().startswith("#")
        ]
        assert not offending, (
            f"{rel} resolves gateway_internal_token for a /v1 client: {offending}. "
            f"Use settings.llm_api_key — the identity token must not reach "
            f"model-authored code."
        )


# ── Webhook signing ─────────────────────────────────────────────────────────

BODY = b'{"event_type":"taskCreated","payload":{}}'


def _sign(secret: str, body: bytes = BODY) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_accepts_a_valid_signature(monkeypatch) -> None:
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    verify_webhook_signature("clickup", BODY, _sign("wh-secret"))  # must not raise


def test_webhook_accepts_a_bare_hex_signature(monkeypatch) -> None:
    """The `sha256=` prefix is conventional, not required."""
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    bare = _sign("wh-secret").removeprefix("sha256=")
    verify_webhook_signature("clickup", BODY, bare)


def test_webhook_rejects_a_wrong_signature(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, _sign("wrong-secret"))
    assert exc.value.status_code == 401


def test_webhook_rejects_a_tampered_body(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    signature = _sign("wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", b'{"event_type":"evil"}', signature)
    assert exc.value.status_code == 401


def test_webhook_rejects_a_missing_signature(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, None)
    assert exc.value.status_code == 401


def test_webhook_fails_closed_when_unconfigured(monkeypatch) -> None:
    """No secret means the endpoint is shut, not open.

    This endpoint starts an agent run and is internet-reachable, so an
    implicit allow would leave the hole this change exists to close.
    """
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.delenv("AGENT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("AGENT_WEBHOOK_SECRET_CLICKUP", raising=False)
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, _sign("anything"))
    assert exc.value.status_code == 503
    assert "AGENT_WEBHOOK_SECRET" in exc.value.detail


def test_per_source_secret_overrides_the_global(monkeypatch) -> None:
    """One leaked integration must not authorise every agent on the platform."""
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "global-secret")
    monkeypatch.setenv("AGENT_WEBHOOK_SECRET_CLICKUP", "clickup-secret")

    verify_webhook_signature("clickup", BODY, _sign("clickup-secret"))
    # The global secret no longer works for this source...
    with pytest.raises(HTTPException):
        verify_webhook_signature("clickup", BODY, _sign("global-secret"))
    # ...but still does for a source with no override.
    verify_webhook_signature("zoho", BODY, _sign("global-secret"))


def test_source_name_is_normalised_for_the_env_lookup(monkeypatch) -> None:
    """`zoho-crm` → AGENT_WEBHOOK_SECRET_ZOHO_CRM (dashes are not env-safe)."""
    monkeypatch.setenv("AGENT_WEBHOOK_SECRET_ZOHO_CRM", "zoho-secret")
    from gateway.routes.agent import verify_webhook_signature

    verify_webhook_signature("zoho-crm", BODY, _sign("zoho-secret"))
