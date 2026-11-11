"""WS-24c — no caller sends the LLM key as the service-identity token.

Spec: project-docs/specs/org_access_control.md §8b (BO-2 residual #4).
Board: WS-24c, Metorite task 1843.

WS-24b took the ``LITELLM_MASTER_KEY`` fallback out of the gateway. The
gateway accepts ``GATEWAY_INTERNAL_TOKEN`` only, so a caller that falls back
to the LLM key gets 401, and a box that seeds the two as one value has no
split at all. The WS-24b fence
(``tests/unit/test_service_identity_and_webhook_auth.py::
test_no_caller_sends_the_llm_key_as_service_identity``) let seven shapes
through. This scan is wider. Merge the two when PR 1765 lands: keep this
scanner and delete the older regexes.

The scan reads ``.py``, ``.ts``, ``.tsx``, ``.sh``, ``.mjs`` and ``.js``
under the trees in ``_TREES``. It finds six shapes:

1. ``fallback`` — an identity read, then ``or``, ``||`` or ``??``, then an
   LLM-key read, in one expression. Comments and blank lines do not count,
   so a far fallback is still one expression.
2. ``reverse`` — the same with the LLM key first.
3. ``nested`` — an identity getter whose default argument reads the LLM key.
   The shell form ``${GATEWAY_INTERNAL_TOKEN:-$LITELLM_MASTER_KEY}`` is the same.
4. ``seed`` — a name that says identity is set from an LLM-key read. The
   shell form ``GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}`` is the old bug of
   ``scripts/setup_secrets.sh``.
5. ``reassign`` — a variable that holds the identity token gets the LLM key
   in a later line of the same block.
6. ``bearer`` — ``Bearer`` with an LLM-key read, and no ``/v1`` target in the
   lines before it. A ``/v1`` client may send the LLM key. Nothing else may.

The legitimate ``/v1`` readers read the LLM key alone, so no shape matches
them. ``test_the_tree_is_clean`` proves that on the real tree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

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


#: ⚠️ KNOWN DEBT, not an allow-list. WS-24b (PR 1765) removes the fallback
#: from each of these files. This branch must not edit them, because the
#: two PRs would conflict. Each file keeps only the shapes it has today, so a
#: new shape in a KNOWN file still fails. The set may only shrink.
#: ``test_the_known_debt_is_only_files_ws24b_owns`` fences it.
#: Delete this set after PR 1765 merges (task 1843 next step).
_F = frozenset({"fallback"})
KNOWN_WS24B: dict[str, frozenset[str]] = {
    "apps/agents/agent-crm/agents.py": _F,
    "apps/agents/agent-email-assistant/agents.py": _F,
    "apps/agents/agent-whatsapp-assistant/agents.py": _F,
    "apps/services/orchestrator/orchestrator/executor.py": _F,
    "apps/skills/skill-my-tasks/skill_my_tasks/core.py": _F,
    "apps/skills/skill-projects/skill_projects/client.py": _F,
    "packages/acb_skills/acb_skills/company_search_tools.py": _F,
    "packages/acb_skills/acb_skills/crm_tools.py": _F,
    "packages/acb_skills/acb_skills/email_chat_context.py": _F,
    "packages/acb_skills/acb_skills/knowledge_tools.py": _F,
    "packages/acb_skills/acb_skills/studio_tools.py": _F,
    "scripts/_test_memory_e2e.py": frozenset({"nested"}),
    "tests/integration/test_auth_identity_chain.py": _F,
    "workbench/control_plane/src/lib/gateway.ts": _F,
}

#: The code files that PR 1765 changes (``gh pr view 1765``, 2026-11-07).
#: KNOWN_WS24B may hold these only. The scan of the PR 1765 head finds no
#: hit in any of them.
_WS24B_FILES = frozenset({
    "apps/agents/agent-crm/agents.py",
    "apps/agents/agent-email-assistant/agents.py",
    "apps/agents/agent-whatsapp-assistant/agents.py",
    "apps/services/orchestrator/orchestrator/_model_resolution.py",
    "apps/services/orchestrator/orchestrator/executor.py",
    "apps/skills/skill-my-tasks/skill_my_tasks/core.py",
    "apps/skills/skill-projects/skill_projects/client.py",
    "evals/coding_engine/run.py",
    "evals/email_narrowing/run.py",
    "evals/whatsapp_narrowing/run.py",
    "packages/acb_auth/acb_auth/access.py",
    "packages/acb_auth/acb_auth/deps.py",
    "packages/acb_common/acb_common/settings.py",
    "packages/acb_graph/acb_graph/db.py",
    "packages/acb_skills/acb_skills/company_search_tools.py",
    "packages/acb_skills/acb_skills/crm_tools.py",
    "packages/acb_skills/acb_skills/email_chat_context.py",
    "packages/acb_skills/acb_skills/knowledge_tools.py",
    "packages/acb_skills/acb_skills/studio_tools.py",
    "scripts/_test_memory_e2e.py",
    "scripts/onboarding_preflight.py",
    "tests/integration/test_auth_identity_chain.py",
    "workbench/control_plane/src/lib/gateway.ts",
})


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


def test_the_tree_is_clean() -> None:
    """Zero hits, except the shapes that WS-24b fixes in its own files."""
    found = []
    for p in _tree_files():
        rel = p.relative_to(ROOT).as_posix()
        known = KNOWN_WS24B.get(rel, frozenset())
        text = p.read_text(encoding="utf-8", errors="replace")
        for shape, line in scan(text):
            if shape not in known:
                found.append(f"{rel}:{line} ({shape})")
    assert not found, (
        "These lines send, seed or fall back to the LLM key as the "
        f"service-identity token. Use GATEWAY_INTERNAL_TOKEN only: {found}"
    )


def test_the_known_debt_is_only_files_ws24b_owns() -> None:
    assert set(KNOWN_WS24B) <= _WS24B_FILES
