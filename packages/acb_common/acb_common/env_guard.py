"""The one rule for a write to the deployment environment.

Four surfaces write ``os.environ`` and the env file of the box: the
Integrations routes (``gateway/routes/integrations.py``), the OAuth token
writer (``gateway/routes/oauth.py``), the Models routes
(``gateway/routes/settings.py::_write_env_key``), and the startup load of the
credential store (``acb_llm/key_store.py``). On the box the env file is
``/opt/acb/app/.env``. It is the ``EnvironmentFile`` of ``acb-gateway.service``,
``acb-backup.service`` and ``acb-whatsapp-bridge.service``, and
``deploy/hostinger/deploy.sh`` runs ``source`` on it. One process env and one
file serve every organization, so a write here changes the deployment.

This module holds the rule, and it holds no I/O. A caller asks it, and then
the caller refuses. Do not copy a rule into a route. A second copy of a
security rule misses the next fix.

⚠️ **The deny list is NOT the gate** (security fix round 1, 2026-10-05). It
can never be complete. The gate is an allowlist in the gateway:
``integrations.BUILTIN_ENV_KEYS``, the keys that a built-in setup guide
declares. This list is defence in depth behind it.

Three parts:

* ``control_problem`` (layer A). A key or a value that holds a control
  character, a format character or a line separator is refused. A newline in
  a value adds a line to the file, and systemd reads the last line of a key.
  ``str.splitlines`` also breaks on U+2028, U+0085, ``\\x0b``, ``\\x0c`` and
  ``\\x1c`` to ``\\x1e``, so a value that holds one of them is a line break
  that waits for the next rewrite.
* ``value_problem`` (layer A, the length cap and the file form). A value is
  ``MAX_VALUE_BYTES`` (4096) bytes at most. A value over 128 KiB in
  ``os.environ`` makes every subprocess start fail with E2BIG. The writer puts
  ``KEY=value`` into the file with no quotes, and it must stay so, because
  other readers parse the file line by line. So a value may hold only the
  characters that systemd, ``bash`` (``source``), python-dotenv and Docker
  Compose all read as themselves in an unquoted value. A space ends the
  assignment in ``bash`` and runs the next word as a command. ``$``, a
  backtick, a quote, a backslash, ``;``, ``&``, ``|``, ``<``, ``>``, ``(`` and
  ``)`` are code to the shell. A first ``~``, a ``~`` after ``:`` and a first
  ``#`` change the value in one reader or another. Decision recorded
  2026-10-05: refuse, and do not quote. Quotes would add a second form to a
  file that readers parse by hand, and this change only removes ability.
* ``is_platform_env`` (layer B). The names that no tenant route may write.
  The list comes from ``acb_common/settings.py``, ``.env.example``,
  ``deploy/``, the key names that the specs record for the box, the process
  environment that Python, Go, Node, git, the dynamic linker, the shell,
  uvicorn and the HTTP clients read, and every literal env name that the code
  reads. Two tests hold it: one sorts every ``Settings`` field, and one scans
  every literal env read in the code (``test_integrations_env_hardening.py``).
  A name that is neither on this list nor in the built-in allowlist fails
  them.

The wider fix is per-request provider credentials, owner gate §6 (f) of
``project-docs/work_plan.md``. Until then, these rules narrow the write.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = [
    "KEY_SHAPE",
    "MAX_VALUE_BYTES",
    "PLATFORM_ENV_NAMES",
    "PLATFORM_ENV_PREFIXES",
    "PLATFORM_ENV_SUFFIXES",
    "EnvWriteRefused",
    "check_env_write",
    "control_problem",
    "is_platform_env",
    "value_problem",
]

#: The shape of a key that a writer may put into the env file.
#: ``fullmatch`` only: ``re.match`` with ``$`` accepts a trailing newline.
KEY_SHAPE = re.compile(r"[A-Z][A-Z0-9_]{1,100}")

#: The largest value, in UTF-8 bytes, that any writer puts into the env file
#: or into ``os.environ``. No credential comes near it. A value over 128 KiB
#: in the environment makes every ``execve`` fail with E2BIG.
MAX_VALUE_BYTES = 4096

#: Unicode categories that are never part of a credential. ``Cc`` holds NUL,
#: TAB, LF, CR, VT, FF, the C1 set and U+0085. ``Zl`` and ``Zp`` are U+2028 and
#: U+2029. ``Cf`` holds U+FEFF, which systemd refuses, and the bidi controls.
#: ``Cs`` is a lone surrogate, which UTF-8 cannot encode. ``Cn`` holds the
#: noncharacters, which systemd refuses. ``Co`` is private use.
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: Characters that the shell reads as code or as a word break in
#: ``KEY=value``. Each one either runs a command under ``source`` or makes
#: two readers disagree about the value.
_SHELL_SPECIAL = frozenset(" $`\\'\";&|<>()")

# ── Layer B: the platform names ─────────────────────────────────────────────
#
# Matched case-insensitively after strip, because Settings reads the
# environment with ``case_sensitive=False``. Err on the side of refusing. A
# refused integration key is a bug report. A written platform key is an
# outage or a breach.
#
# ⚠️ No entry here may match a key of ``integrations.BUILTIN_ENV_KEYS``, or a
# tenant can no longer save that integration. A test checks it.

#: Exact names.
PLATFORM_ENV_NAMES: frozenset[str] = frozenset({
    # Database, cache and graph.
    "DATABASE_URL", "REDIS_URL", "CC_DSN", "APP_DB", "DBS",
    # Sign-in and access. EXECUTIVE_EMAILS grants the EXECUTIVE role, and
    # ALLOWED_EMAIL_DOMAIN decides who may sign in at all.
    "ALLOWED_EMAIL_DOMAIN", "EXECUTIVE_EMAILS", "ACCESS_LEGACY_FALLBACK",
    "IDENTITY_CUTOVER", "SELF_MUTATION_DISABLED", "WORKSPACE_BASE_DOMAIN",
    "SHARED_TOKEN",
    # The mail apps are the deployment's (D-EM-1). MICROSOFT_TENANT_ID pins
    # the directory of the Microsoft app, a per-deployment decision
    # (deploy.sh, H-13(a)). GOOGLE_CLIENT_* is the app of oauth.py.
    "MICROSOFT_TENANT_ID", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS",
    # Operator-only keys of the built-in guides. ZOHO_REGION picks the Zoho
    # data-centre domain. SMTP_USE_TLS sets the transport of the SMTP host.
    # GMAIL_DEFAULT_USER picks the mailbox that the operator's domain-wide
    # Gmail service account impersonates (round 2).
    "ZOHO_REGION", "SMTP_USE_TLS", "GMAIL_DEFAULT_USER",
    # LLM and speech provider keys. Only the BYOK-gated Models routes and the
    # key store write them, and each passes them as ``owned``.
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "TOGETHER_API_KEY", "OPENROUTER_API_KEY",
    "GITHUB_COPILOT_TOKEN",
    # Runtime settings of the deployment.
    "LOG_LEVEL", "LOG", "LLM_USAGE_AUDIT", "PROJECTS_IMPORT",
    "AGENTS_CLONE_DIR", "GITHUB_INSTALLATION_ID", "GITHUB_ORG",
    "GITHUB_BOT_NAME", "GITHUB_BOT_EMAIL", "GITHUB_PAT",
    "ENABLE_INSTRUMENTATION", "CI",
    # WS-45 (D90): the agents that the tier policy covers. It moves what an
    # organization pays, so only the operator writes it (ai_tier_routing.md).
    "AI_TIER_ROUTING",
    # WS-48 N3 (D93): System 1 on the decision model. It sends chat content
    # to another sub-processor and moves spend, so only the operator writes it.
    "SYSTEM_ONE_ON_DECIDE",
    # WS-43y1a: the fault hook of the data engine (exit, kill, sleep, grow).
    # Only its tests set it. A tenant that set it would break each verb.
    "DATA_ENGINE_FAULT",
    # Server: uvicorn reads these from the env of acb-gateway.service.
    "FORWARDED_ALLOW_IPS", "WEB_CONCURRENCY",
    # Deploy, backup and watchdog scripts (variables they read).
    "APP_DIR", "ENV_FILE", "ACTIONS", "CURRENT", "DEST", "DRY_RUN", "EUID",
    "FAILED", "FORENSICS", "OURS_FAILED", "ROUTES", "SCRATCH", "STAMP",
    "SVC_USER", "UNITS", "UNITS_CHANGED", "VENV_OWNER", "VERIFY_RESTORE",
    "UX_OUT",
    # The process environment: shell, Python, Go, linker, TLS, resolver.
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "TMPDIR", "TEMP",
    "TMP", "LANG", "LANGUAGE", "TZ", "IFS", "ENV", "PS1", "PS2", "PS3",
    "PS4", "SHELLOPTS", "CDPATH", "GLOBIGNORE", "POSIXLY_CORRECT",
    "VIRTUAL_ENV", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HOSTALIASES", "RES_OPTIONS", "LOCALDOMAIN",
    "NOTIFY_SOCKET", "INVOCATION_ID", "JOURNAL_STREAM", "DEBIAN_FRONTEND",
    "GO", "GOFLAGS", "GODEBUG", "GOMAXPROCS", "GOGC", "GOMEMLIMIT",
    "GOTRACEBACK", "GOROOT", "GOCACHE", "GOTOOLCHAIN", "GOPROXY",
    "CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "MAKEFLAGS",
    "AR", "LD", "RUBYOPT", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
})

#: Prefixes. ``AUTH_`` covers AUTH_SECRET, AUTH_URL and every
#: AUTH_MICROSOFT_ENTRA_ID_* and AUTH_GOOGLE_* sign-in key.
PLATFORM_ENV_PREFIXES: tuple[str, ...] = (
    # Database, cache and graph.
    "DATABASE_", "DB_", "PG", "POSTGRES_", "REDIS_", "NEO4J_", "GRAPHITI_",
    "MEM0_",
    # The deployment's own secrets and identity. ACB_MASTER_KEY encrypts
    # every stored credential.
    "ACB_", "FERNET", "GATEWAY_", "LITELLM_", "AUTH_", "NEXTAUTH_",
    "SUPABASE_", "OPERATOR_", "CUSTOMER_CONSOLE_", "CONSOLE_", "ROUTER_",
    "RESEND_", "LANGFUSE_", "OTEL_", "GOOGLE_SSO_", "CC_",
    # The mail apps (D-EM-1, WS-17 EM-G7, O-GM-5). This is the one list of
    # them. `test_email_gmail_connect.py` fails when `oauth_app` reads a
    # name that these prefixes do not cover.
    "GMAIL_OAUTH_", "MSFT_OAUTH_", "AUTH_MICROSOFT_ENTRA_ID_", "GMAIL_PUSH_",
    # Backup. acb-backup.service runs scripts/backup_db.sh as root with the
    # env file, so BACKUP_REMOTE sends every dump to the host it names, and
    # KEEP_DAILY deletes the backup history.
    "BACKUP_", "KEEP_",
    # Server and agent runtime switches.
    "UVICORN_", "V1_", "SKILLS_", "SUB_AGENT_", "TOOL_", "RUNTIME_",
    "SESSION_", "STREAM_", "HISTORY_", "PROMPT_", "RUN_", "WATCHDOG_",
    "WAIT_", "LOG_",
    # Flags and switches.
    "EMAIL_", "DECIDE_", "BYOK_", "CRM_", "INGESTION_", "ACTION_BROKER_",
    "WORKFLOW_", "WHATSAPP_", "MEETING_BOT_", "MIGRATION_", "SKIP_",
    # Agent runtime, sandbox and models.
    "MUTATION_", "SANDBOX_", "COPILOT_", "MAF_", "AGENT_", "CUSTOM_APPS_",
    "OPENHANDS_", "GITHUB_APP_", "NOTES_", "OAUTH_", "NEXT_",
    "AWS_", "AZURE_", "VERTEX_",
    # Speech, meetings and media providers of the deployment.
    "ASSEMBLYAI_", "DEEPGRAM_", "RECALL_", "PIXELLAB_", "LIVE_", "MEET_",
    "SHERPA_", "LOCAL_", "VIRTUAL_", "PULSE_", "CHROME_", "PLAYWRIGHT_",
    # Deploy, release and test harness.
    "DEPLOY_", "RELEASE_", "VPS_", "CADDY_", "SMOKE_", "GH_", "MB_", "OC_",
    "WB_", "E2E_", "CHECKOUT_", "VERSION_",
    # The process environment and the toolchain. ``BASH`` covers BASH_ENV,
    # BASHOPTS, BASH_XTRACEFD and every BASH_FUNC_ export.
    "LC_", "BASH", "PYTHON", "UV_", "PIP_", "LD_", "DYLD_", "NODE_",
    "NPM_", "GIT_", "SSH_", "DOCKER_", "COMPOSE_", "SSL_", "OPENSSL_",
    "SYSTEMD_", "XDG_", "PERL5",
)

#: Suffixes.
PLATFORM_ENV_SUFFIXES: tuple[str, ...] = (
    "_DATABASE_URL", "_SESSION_SECRET", "_ENCRYPTION_KEY", "_FERNET_KEY",
    "_MASTER_KEY", "_INTERNAL_TOKEN", "_OPERATOR_TOKEN", "_DEPLOYMENT_KEY",
    "_WEBHOOK_SECRET", "_PUBSUB_TOKEN",
    # A value that names a host sends a request, and often a credential with
    # it, to that host. The operator-only family of the guides is here too.
    "_URL", "_HOST", "_PORT", "_DOMAIN", "_PATH", "_ENDPOINT", "_API_BASE",
    # Feature flags. A dark flag is the owner's to flip.
    "_ENABLED", "_DISABLED", "_ENFORCE",
    # Paths on the box.
    "_DIR", "_ROOT",
    # HTTP_PROXY and the like send every outbound call through the host.
    "_PROXY",
    # Runtime knobs: timeouts, models, commands, cache lifetimes.
    "_TIMEOUT", "_TIMEOUT_S", "_SECONDS", "_SECS", "_MODEL", "_CMD", "_TTL",
)


class EnvWriteRefused(ValueError):
    """A write that the rule refuses. The message names the key, never the value.

    ``platform`` is True when the key is a platform name (layer B), and False
    when the key or the value is malformed (layer A).
    """

    def __init__(self, key: str, reason: str, *, platform: bool = False) -> None:
        self.key = key
        self.reason = reason
        self.platform = platform
        super().__init__(f"env write refused for {key!r}: {reason}")


def control_problem(text: str) -> str | None:
    """Say why ``text`` holds a control character, or return None.

    Layer A. It applies to every write of ``os.environ`` and of the env file.
    """
    for ch in str(text):
        if unicodedata.category(ch) in _CONTROL_CATEGORIES:
            return f"it holds the control character U+{ord(ch):04X}"
    return None


def value_problem(value: str) -> str | None:
    """Say why the env file cannot hold ``value`` unquoted, or return None.

    Layer A: the length cap, control characters and the file form. It applies
    to every value that goes into the env file or into ``os.environ``. The
    reason names a size or a character code and never the value, because the
    value is a credential.
    """
    value = str(value)
    size = len(value.encode("utf-8", "surrogatepass"))
    if size > MAX_VALUE_BYTES:
        return f"it is {size} bytes, and the limit is {MAX_VALUE_BYTES}"
    problem = control_problem(value)
    if problem:
        return problem
    for ch in value:
        if ch in _SHELL_SPECIAL:
            name = "a space" if ch == " " else repr(ch)
            return f"it holds {name}, which the shell reads as code"
        if not "\x21" <= ch <= "\x7e":
            return f"it holds U+{ord(ch):04X}, and the env file takes printable ASCII only"
    if value.startswith(("~", "#")) or ":~" in value:
        return "it starts with '~' or '#', or holds ':~', which a reader changes"
    return None


def is_platform_env(name: str) -> bool:
    """True when ``name`` is a platform setting that no tenant route may write.

    Layer B. The match ignores case and surrounding space.
    """
    n = str(name).strip().upper()
    if not n:
        return False
    return (
        n in PLATFORM_ENV_NAMES
        or n.startswith(PLATFORM_ENV_PREFIXES)
        or n.endswith(PLATFORM_ENV_SUFFIXES)
    )


def check_env_write(key: str, value: str, *, owned: frozenset[str] = frozenset()) -> None:
    """Raise ``EnvWriteRefused`` unless ``KEY=value`` is safe to write.

    The whole rule, for a writer of the env file or of ``os.environ``: the key
    shape, layer A on the key and the value, and layer B on the key. A route
    calls the parts first, to answer with the right status. The writer calls
    this again, so a caller that forgets cannot write.

    ``owned`` names, by exact name, the platform keys that the CALLING surface
    is the one legitimate writer of. Layer B lets them through, and layer A
    still applies. Two callers pass a set, and a test pins each one:
    ``routes/settings.py`` (``_MODELS_PAGE_ENV_KEYS``: the BYOK provider keys
    and ``COPILOT_CHAT_MODEL``) and ``key_store.configure_litellm`` (the
    provider keys that it loads). Never put a URL in it.
    """
    problem = control_problem(key)
    if problem:
        raise EnvWriteRefused(key, f"the key: {problem}")
    if not KEY_SHAPE.fullmatch(key):
        raise EnvWriteRefused(key, "the key is not A-Z, 0-9 and underscore")
    problem = value_problem(value)
    if problem:
        raise EnvWriteRefused(key, f"the value: {problem}")
    if is_platform_env(key) and key not in owned:
        raise EnvWriteRefused(key, "it is a platform setting", platform=True)
