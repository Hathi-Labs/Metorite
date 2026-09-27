#!/usr/bin/env python
"""Colleague-onboarding readiness check (WS-24).

WHY THIS EXISTS
---------------
Exactly one person is signed in to this deployment. Before a second one is
invited, seven things have to be true, and every one of them has been
re-derived by hand in conversation more than once. This script is the durable
version of that derivation: run it on the box, read the table, fix what is red,
and only then invite anybody.

The owning spec is ``project-docs/specs/colleague_onboarding.md`` §1 (the
readiness gate). This file is the executable half of that section — if a
criterion moves there, move it here in the same change.

WHAT IT WILL NOT DO
-------------------
* It never prints a secret. Checks that compare two secrets report a boolean,
  never a value, never a prefix, never a length.
* It never writes anything. Every DB statement is a ``SELECT``.
* It is safe for an agent to author, and it is NOT safe for an agent to run
  against production — the DB reads go through the live database. An agent
  running this must use ``--mode local``, which refuses every box-only check
  and says so in the output.
* It never PASSes a check it could not actually answer. Anything that is a
  claim about *the deployment* — the two secrets, the live Caddyfile, the
  backups that landed, the database — SKIPs in local mode rather than
  reporting a laptop's answer as the box's.

USAGE
-----
    # On the VPS, the real thing:
    cd /opt/acb/app && uv run python scripts/onboarding_preflight.py

    # On a laptop / in CI / from an agent — box-only checks report SKIP:
    uv run python scripts/onboarding_preflight.py --mode local

    # Machine-readable, for a future health job:
    uv run python scripts/onboarding_preflight.py --json

EXIT CODE
---------
``0`` when no check FAILed, ``1`` otherwise. SKIP is not a pass: a local run
that ends in ``0`` means "nothing that could be checked here is broken", which
the banner says out loud. Do not wire a local run into an onboarding gate.

A local run CAN reach ``0``, and that is deliberate: every check either answers
its locally-answerable half or SKIPs. Today it does not, because the repo
Caddyfile genuinely lacks the two ``header_up -`` deletions (G1) and there is
no ``scripts/backup_db.sh`` (G3) — both are repository defects an agent can fix
without touching the box, which is exactly the signal a local run should carry.

ENVIRONMENT (resolution is deliberately shaped like scripts/apply_migrations.sh
and shares its defaults, so an operator does not have to learn a second set)
    APP_DIR       default /opt/acb/app in box mode, the repo root in local mode
    WB_ENV        default $APP_DIR/workbench/control_plane/.env.local
                  (same variable name and default the deploy workflow uses)
    PG_CONTAINER  default acb-postgres
    POSTGRES_USER / POSTGRES_DB   read from $APP_DIR/.env, else acb / acb
    CADDYFILE     default /etc/caddy/Caddyfile
    BACKUP_DIR    default /opt/acb/backups
    BACKUP_TIMER  default acb-backup.timer (probed, never asserted — BO-23
                  ships no unit file; freshness is the real assertion)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# The permission vocabulary is a PURE module — no DB, no FastAPI, no I/O (see
# its docstring). Importing it here is what lets this script compare the live
# feature_catalog against the tuple the product actually reads, rather than
# against a second hand-maintained list that would drift.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "acb_auth"),
)
try:
    from acb_auth.permissions import (
        FEATURES,
        SYSTEM_ROLES,
        permission_matches,
    )
except Exception:  # pragma: no cover - only when run outside the workspace
    FEATURES = ()
    SYSTEM_ROLES = ()

    def permission_matches(pattern: str, required: str) -> bool:  # type: ignore[misc]
        return pattern in ("*", required)


REPO_ROOT = Path(__file__).resolve().parents[1]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: Addresses that mean "nobody filled this in". The 2026-07-30 production
#: lockout was a placeholder EXECUTIVE_EMAILS: every colleague authenticated
#: against the IdP, nobody was provisioned, and because
#: ``ensure_owner_bootstrap`` had no real candidate there was no inviter and no
#: way back in short of hand-run SQL.
_PLACEHOLDER_PATTERNS = (
    r"@example\.(com|org|net)$",
    r"@localhost$",
    r"@test$",
    r"^(you|your[-_.]?email|admin|user|someone|changeme|change[-_]me)@",
    r"^dev@fracktal\.in$",  # the DEV_IDENTITY sentinel in lib/gateway.ts
)


@dataclass
class Check:
    """One readiness criterion and its verdict.

    ``remediation`` is mandatory prose, not a hint: this table is read by
    somebody who is about to decide whether to let real people in, and "FAIL"
    without "here is the exact thing to do" is how a red check becomes a
    permanent red check.
    """

    key: str
    title: str
    status: str = SKIP
    detail: str = ""
    remediation: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "remediation": self.remediation,
            "notes": self.notes,
        }


# ── Environment resolution (mirrors scripts/apply_migrations.sh) ─────────────

def _parse_env_file(path: Path) -> dict[str, str]:
    """Read a dotenv file into a dict. Last assignment wins, like the shell.

    Deliberately tolerant: this file is hand-edited on the box, so a stray
    comment or a blank line must not make the whole preflight unusable. Values
    are returned verbatim (quotes stripped) and NEVER logged.
    """
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


class Env:
    """The effective environment for BOTH processes that hold service identity.

    Two files, read separately and deliberately not merged:

    * ``$APP_DIR/.env`` — the gateway's. Process env is layered over it, because
      that is how systemd + the gateway actually resolve these: a value exported
      in the unit file is what the running process sees, whatever the file says.
    * ``$APP_DIR/workbench/control_plane/.env.local`` — the Next.js BFF's.
      Process env is deliberately NOT layered over this one: the workbench is a
      separate process with a separate environment, and the only thing that
      answers "what Bearer will the BFF actually send" is the file it reads.
      The path mirrors ``WB_ENV`` in ``.github/workflows/deploy.yml:167``, and
      the ``WB_ENV`` variable overrides it here for the same reason.

    Reading both is not tidiness. ``GATEWAY_INTERNAL_TOKEN`` present in one and
    absent from the other is the total-lockout shape — see ``check_internal_token``.
    """

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir
        self.env_path = app_dir / ".env"
        self.file = _parse_env_file(self.env_path)
        self.wb_env_path = Path(
            os.environ.get("WB_ENV")
            or (app_dir / "workbench" / "control_plane" / ".env.local")
        )
        self.wb_file = _parse_env_file(self.wb_env_path)

    def get(self, key: str, default: str = "") -> str:
        val = os.environ.get(key)
        if val is None:
            val = self.file.get(key, default)
        return (val or "").strip()

    def get_workbench(self, key: str, default: str = "") -> str:
        """Read the workbench's ``.env.local`` ONLY. Never falls back to the
        gateway's ``.env`` or to process env — the whole point of this accessor
        is to detect that the two files disagree."""
        return (self.wb_file.get(key, default) or "").strip()


# ── Postgres access (docker exec psql, same shape as apply_migrations.sh) ────

class Psql:
    """A read-only psql runner, or a disabled stub when the DB is unreachable.

    Goes through ``docker exec <container> psql`` rather than a Python driver
    on purpose: it is the access path every other operational script in this
    directory uses, it needs no DSN parsing and no asyncpg, and it fails in a
    way an operator already recognises.
    """

    def __init__(self, env: Env, enabled: bool) -> None:
        self.container = env.get("PG_CONTAINER", "acb-postgres")
        self.user = env.get("POSTGRES_USER", "acb") or "acb"
        self.db = env.get("POSTGRES_DB", "acb") or "acb"
        self.reason = ""
        self.available = False
        if not enabled:
            self.reason = "local mode — the database is not reached from here"
            return
        if shutil.which("docker") is None:
            self.reason = "docker is not on PATH"
            return
        try:
            names = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=20, check=False,
            ).stdout.split()
        except (OSError, subprocess.SubprocessError) as exc:
            self.reason = f"could not list containers: {exc}"
            return
        if self.container not in names:
            self.reason = f"Postgres container '{self.container}' is not running"
            return
        self.available = True

    def query(self, sql: str) -> tuple[list[list[str]], str]:
        """``(rows, error)``. Rows are lists of raw column strings.

        ``-tA`` (tuples-only, unaligned) keeps parsing trivial; a pipe
        separator is used because no value this script reads can contain one
        (emails, permission strings and slugs are all constrained vocabularies).
        """
        if not self.available:
            return [], self.reason
        cmd = [
            "docker", "exec", "-i", self.container,
            "psql", "-U", self.user, "-d", self.db,
            "-tA", "-F", "|", "--no-psqlrc", "-c", sql,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [], f"psql failed: {exc}"
        if proc.returncode != 0:
            return [], f"psql exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        rows = [
            line.split("|")
            for line in proc.stdout.splitlines()
            if line.strip() != ""
        ]
        return rows, ""


# ── Check 1 — service identity is not the LLM key ────────────────────────────

def _internal_token_remediation(env: Env, box: bool) -> str:
    """The one remediation both halves of check 1 print.

    It says "redeploy, or set it in BOTH files" rather than the older "set it
    and restart", because the older wording was a recipe for a total lockout —
    see ``check_internal_token``'s docstring.

    In local mode it names the BOX's paths, not this machine's: the instruction
    is about the deployment, and printing a laptop's ``.env`` path here would
    read as "create this file", which is not the fix.
    """
    env_path = env.env_path if box else Path("/opt/acb/app/.env")
    wb_path = (
        env.wb_env_path if box
        else Path("/opt/acb/app/workbench/control_plane/.env.local")
    )
    return (
        "Preferred: put GATEWAY_INTERNAL_TOKEN in "
        f"{env_path} and then REDEPLOY. The pipeline reconciles the "
        "workbench copy from .env on every deploy, in place and idempotently "
        "(.github/workflows/deploy.yml:166-187), which is the only path that "
        "cannot leave the two files disagreeing.\n"
        "    By hand (no redeploy): you must write it to BOTH "
        f"{env_path} and {wb_path}, then restart the gateway and "
        "the workbench. Setting only the gateway .env is the lockout: the BFF "
        "keeps sending its 'sk-local-dev-change-me' default "
        "(workbench/control_plane/src/lib/gateway.ts:58-61), so every proxied "
        "browser call arrives with a bad Bearer and a real X-User-Email, and "
        "acb_auth/deps.py:356-361 resolves that to NO_ACCESS — for every "
        "signed-in member, not just new ones.\n"
        "    Provisioning the token makes GATEWAY_REFUSE_LLM_KEY_IDENTITY "
        "inert, which is the point: the flag exists for boxes that cannot be "
        "provisioned yet."
    )


def check_internal_token(env: Env, box: bool) -> Check:
    """GATEWAY_INTERNAL_TOKEN is provisioned in BOTH files and is not the LLM key.

    WHAT THIS PREVENTS, part one. ``acb_auth.deps._resolve_internal_token``
    (:93-117) falls back to ``LITELLM_MASTER_KEY`` when
    ``GATEWAY_INTERNAL_TOKEN`` is unset. That LLM key is handed to every agent's
    BYOK client, so while the fallback is in effect any agent holding it can
    present it as service identity and then either assert an arbitrary
    ``X-User-Email`` (``deps.py`` branch 1a, :311-331) or act as the platform
    itself with ``SERVICE_ACCESS`` (branch 1b, :339-344). With one user that is
    a curiosity. With colleagues it is "any agent can read anyone's mail".

    Two secrets set to the SAME string is the same defect wearing a fix's
    clothes, so equality is checked as well as presence.

    WHAT THIS PREVENTS, part two — and it is why the workbench file is read.
    The token lives in TWO places: the gateway's ``.env`` and the Next.js BFF's
    ``.env.local``. G2 is OWNER-GATE, so a human provisions it by hand, and a
    check that looked only at ``.env`` would report ``[PASS]`` on the exact
    state that bricks the deployment:

        .env has the token, .env.local does not
          -> the BFF sends "sk-local-dev-change-me" (lib/gateway.ts:58-61)
          -> bearer_ok is false while X-User-Email is present and an internal
             token IS configured
          -> deps.py:356-361 returns NO_ACCESS for EVERY signed-in member.

    A total lockout, produced by following the remediation, and previously
    certified green by this very check. The partial mitigation is that
    ``.github/workflows/deploy.yml:166-187`` reconciles ``.env.local`` from
    ``.env`` on every deploy — so the dangerous window is precisely
    "provisioned by hand without a redeploy", which is what a hand-run
    OWNER-GATE action looks like.

    WHY IT REFUSES TO ANSWER LOCALLY. Both values are claims about THE
    DEPLOYMENT, and a local run reads a developer machine's ``.env``. A laptop
    that happens to carry a real token would print ``[PASS] Service identity is
    its own secret`` about a box it never touched. Same posture as check 7: a
    local PASS here would be vacuous, so there is none.

    Neither value is printed, in any mode, ever.
    """
    c = Check("internal_token", "Service identity is its own secret")
    c.remediation = _internal_token_remediation(env, box)

    if not box:
        c.status = SKIP
        c.detail = (
            "local mode — this is a claim about the deployment's .env AND the "
            "workbench's .env.local, and a laptop's copies of those files say "
            "nothing about the box. A local PASS would be vacuous."
        )
        return c

    internal = env.get("GATEWAY_INTERNAL_TOKEN")
    llm = env.get("LITELLM_MASTER_KEY")

    if not internal:
        c.status = FAIL
        c.detail = (
            "GATEWAY_INTERNAL_TOKEN is unset, so service identity falls back "
            "to LITELLM_MASTER_KEY (acb_auth/deps.py:108-117)."
        )
        return c

    if llm and internal == llm:
        c.status = FAIL
        c.detail = (
            "GATEWAY_INTERNAL_TOKEN is set but is byte-identical to "
            "LITELLM_MASTER_KEY, so the two secrets are one secret and the "
            "separation does not exist."
        )
        return c

    # The second file. Failing here is a LOCKOUT, not a hardening gap, so it is
    # worded as one — nobody reading this line should think it can wait.
    if not env.wb_env_path.is_file():
        c.status = FAIL
        c.detail = (
            f"the gateway .env has the token but {env.wb_env_path} does not "
            "exist, so the BFF falls back to 'sk-local-dev-change-me' "
            "(lib/gateway.ts:58-61). LOCKOUT: every signed-in member resolves "
            "to NO_ACCESS at deps.py:356-361."
        )
        return c

    wb = env.get_workbench("GATEWAY_INTERNAL_TOKEN")
    if not wb:
        c.status = FAIL
        c.detail = (
            f"the gateway .env has the token but {env.wb_env_path} does not "
            "set GATEWAY_INTERNAL_TOKEN, so the BFF falls back to "
            "'sk-local-dev-change-me' (lib/gateway.ts:58-61). LOCKOUT: every "
            "signed-in member resolves to NO_ACCESS at deps.py:356-361."
        )
        return c
    if wb != internal:
        c.status = FAIL
        c.detail = (
            f"{env.env_path} and {env.wb_env_path} both set "
            "GATEWAY_INTERNAL_TOKEN and the two values DIFFER (compared in "
            "memory, never printed). LOCKOUT: the BFF's Bearer is rejected, so "
            "every proxied browser call arrives with an unaccompanied "
            "X-User-Email and resolves to NO_ACCESS at deps.py:356-361."
        )
        return c

    c.status = PASS
    c.remediation = ""
    c.detail = (
        "GATEWAY_INTERNAL_TOKEN is set in both "
        f"{env.env_path.name} and {env.wb_env_path.name}, the two agree, and "
        + ("it differs from LITELLM_MASTER_KEY" if llm
           else "LITELLM_MASTER_KEY is unset here so there is nothing it could "
                "collide with")
        + " (values compared in memory, never printed)."
    )
    if not llm:
        c.notes.append(
            "LITELLM_MASTER_KEY is not visible from this run — on the box it "
            "is always set, so re-read this line if it appears in box mode."
        )
    return c


# ── Check 2 — EXECUTIVE_EMAILS is a real person ──────────────────────────────

def check_executive_emails(env: Env, box: bool) -> Check:
    """EXECUTIVE_EMAILS names a real address, not a placeholder.

    WHY IT REFUSES TO ANSWER LOCALLY, like check 1. The claim is about the
    DEPLOYMENT's environment. A developer machine carrying a real
    EXECUTIVE_EMAILS in its own ``.env`` would otherwise print a green line
    about a box this run never reached — and this is one of the two checks
    somebody will cite when deciding it is safe to invite people.

    WHAT THIS PREVENTS. Two separate things read this variable:

    * ``workbench/control_plane/src/lib/gateway.ts:69-74`` builds the bootstrap
      admin set from it, and :144 stamps ``X-User-Role: executive`` for anyone
      in it. That header only feeds the gateway's LEGACY ``require_role``
      checks, so a wrong value here is a degradation, not a grant.
    * ``acb_auth.access.ensure_owner_bootstrap`` (:467-519) provisions the
      FIRST address in it as the owner when nobody holds ``owner`` at all.
      That is the one that bit: on 2026-07-30 ``app_user`` was empty and the
      variable was a placeholder, so the deployment had zero owners and
      therefore **no inviter** — everyone authenticated, nobody was
      provisioned, and the only fix was hand-run SQL.
    """
    c = Check("executive_emails", "EXECUTIVE_EMAILS names a real owner")
    if not box:
        c.status = SKIP
        c.detail = (
            "local mode — this is a claim about the deployment's environment, "
            "and a laptop's .env says nothing about the box. A local PASS "
            "would be vacuous."
        )
        c.remediation = (
            "Run on the box. EXECUTIVE_EMAILS must be set in "
            "/opt/acb/app/.env to the owner's real sign-in address; it is the "
            "only candidate list ensure_owner_bootstrap has "
            "(acb_auth/access.py:467-519)."
        )
        return c

    raw = env.get("EXECUTIVE_EMAILS")
    if not raw:
        c.status = FAIL
        c.detail = "EXECUTIVE_EMAILS is unset or empty."
        c.remediation = (
            f"Set EXECUTIVE_EMAILS in {env.env_path} to the owner's real "
            "sign-in address (comma-separated if there is more than one). "
            "ensure_owner_bootstrap only ever runs when NO member holds "
            "'owner', so this cannot overwrite real membership — but with it "
            "empty an ownerless deployment has no way to appoint anybody."
        )
        return c

    candidates = [e.strip().lower() for e in raw.split(",") if e.strip()]
    real = [e for e in candidates if "@" in e]
    placeholders = [
        e for e in real
        if any(re.search(p, e) for p in _PLACEHOLDER_PATTERNS)
    ]

    if not real:
        c.status = FAIL
        c.detail = f"EXECUTIVE_EMAILS holds {len(candidates)} value(s), none with an '@'."
        c.remediation = (
            "ensure_owner_bootstrap picks the first entry containing '@' "
            "(access.py:489-491); with none, it logs "
            "'ownership_bootstrap_no_candidate' and does nothing."
        )
        return c

    if placeholders:
        c.status = FAIL
        c.detail = (
            f"EXECUTIVE_EMAILS looks like a placeholder: {', '.join(placeholders)}."
        )
        c.remediation = (
            "Replace it with the owner's real address. This exact shape "
            "caused the 2026-07-30 lockout: a placeholder plus an empty "
            "app_user table = no owner, no inviter, no way in."
        )
        return c

    c.status = PASS
    c.detail = f"EXECUTIVE_EMAILS names {len(real)} real address(es); the bootstrap candidate is {real[0]}."

    # Not a failure — get_current_user only LOGS a domain mismatch and still
    # trusts the token holder's assertion (deps.py:312-331) — but an owner
    # whose address is off-domain is worth a second look before onboarding.
    domain = (env.get("ALLOWED_EMAIL_DOMAIN", "fracktal.in") or "fracktal.in").lower().lstrip("@")
    off_domain = [e for e in real if not e.endswith("@" + domain)]
    if off_domain:
        c.notes.append(
            f"{', '.join(off_domain)} is outside ALLOWED_EMAIL_DOMAIN "
            f"('{domain}'). Not fatal — the domain check only warns — but "
            "confirm it is the address the IdP actually returns."
        )
    return c


# ── Check 3 — Caddy strips inbound identity headers ──────────────────────────

_API_VHOST_RE = re.compile(r"^\s*api\.[^\s{]*\s*\{", re.MULTILINE)


def _api_reverse_proxy_block(text: str) -> str | None:
    """The body of the ``reverse_proxy`` block inside the ``api.*`` site block.

    Brace-counted rather than regexed end-to-end, because a Caddyfile nests and
    a lazy regex would happily match across two site blocks — which would let
    a strip directive on the *workbench* vhost satisfy a check about the *API*
    vhost. Returns ``None`` when there is no api vhost or it has no
    ``reverse_proxy`` block with a body.
    """
    m = _API_VHOST_RE.search(text)
    if m is None:
        return None
    # Walk from the opening brace of the site block to its match.
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    site = text[i + 1:j]

    rp = re.search(r"reverse_proxy[^\n{]*\{", site)
    if rp is None:
        return ""  # a one-line `reverse_proxy host:port` with no body
    k = site.index("{", rp.start())
    depth, m2 = 0, k
    while m2 < len(site):
        if site[m2] == "{":
            depth += 1
        elif site[m2] == "}":
            depth -= 1
            if depth == 0:
                break
        m2 += 1
    return site[k + 1:m2]


def _strips_identity(block: str | None) -> tuple[bool, list[str]]:
    """``(ok, missing)`` for the two required ``header_up -`` directives."""
    if block is None:
        return False, ["X-User-Email", "X-User-Role"]
    missing = [
        h for h in ("X-User-Email", "X-User-Role")
        if not re.search(rf"header_up\s+-{re.escape(h)}\b", block, re.IGNORECASE)
    ]
    return not missing, missing


def check_caddy_strip(env: Env, box: bool) -> Check:
    """The public API vhost deletes X-User-Email / X-User-Role from every request.

    WHAT THIS PREVENTS — and why it is the loudest check here. ``deps.py``'s
    own module docstring (:27-35) says it plainly: nothing in that module can
    tell a forwarded identity header from a forged one. The Bearer beside it is
    the whole of the proof, and that Bearer falls back to a key every agent
    holds (check 1). **The reverse proxy is the boundary.** Every access fix
    the platform has shipped — the Notes owner filter, the room intersection,
    default-deny — sits behind an assumption that a stranger cannot simply set
    ``X-User-Email: someone@fracktal.in`` on a request to the public API host.

    Two copies are examined because they can drift:

    * ``deploy/hostinger/caddy/Caddyfile`` — the repo copy, checked in EVERY
      mode. It is what a fresh bootstrap installs (``bootstrap.sh:59``).
    * ``/etc/caddy/Caddyfile`` — the live copy, box mode only.

    Since PR #498 (H-60) every deploy validates the repo copy and installs it
    over the live one (``scripts/vps_apply.sh``, "Installing the Caddy
    config"). So drift lasts only until the next deploy, or it means the last
    deploy failed. A change to the auth lines of the repo copy is owner-gated
    (``work_plan.md`` §6 gate (a), fence ``tests/unit/test_caddy_auth_gate.py``).

    LOCAL MODE. The repo copy IS checkable from anywhere and a wrong one is a
    real, locally-fixable defect, so it FAILs. The live copy is not reachable,
    so a correct repo copy earns a SKIP rather than a PASS: half the answer is
    not the answer, and the boundary is the file on the box.
    """
    c = Check("caddy_strip", "Caddy strips inbound identity headers (api vhost)")

    repo_file = REPO_ROOT / "deploy" / "hostinger" / "caddy" / "Caddyfile"
    repo_ok, repo_missing, repo_block = None, [], None
    if repo_file.is_file():
        repo_block = _api_reverse_proxy_block(
            repo_file.read_text(encoding="utf-8", errors="replace")
        )
        repo_ok, repo_missing = _strips_identity(repo_block)

    live_path = Path(env.get("CADDYFILE", "/etc/caddy/Caddyfile"))
    live_ok, live_missing, live_text = None, [], None
    if box and live_path.is_file():
        try:
            live_text = live_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            c.notes.append(f"could not read {live_path}: {exc}")
        else:
            live_ok, live_missing = _strips_identity(
                _api_reverse_proxy_block(live_text)
            )

    remediation = (
        "Add the two deletions to the api vhost's reverse_proxy block in "
        "deploy/hostinger/caddy/Caddyfile:\n"
        "        reverse_proxy 127.0.0.1:8080 {\n"
        "            header_up -X-User-Email\n"
        "            header_up -X-User-Role\n"
        "            header_up X-Real-IP {remote_host}\n"
        "        }\n"
        "    then install it live and reload Caddy. Installing it is an "
        "OWNER-GATE action (work_plan.md §6: any change to auth behaviour on "
        "the box); an agent may write the file and must stop there."
    )

    if repo_ok is None:
        c.status = FAIL
        c.detail = f"repo Caddyfile not found at {repo_file}"
        c.remediation = remediation
        return c

    parts = []
    if repo_ok:
        parts.append("repo copy strips both headers")
    else:
        parts.append(
            "repo copy does NOT strip " + ", ".join(repo_missing)
            + (" (the api vhost's reverse_proxy has no block body)"
               if repo_block == "" else "")
        )

    if live_ok is None:
        parts.append(
            "live copy NOT INSPECTED "
            + (f"(not found at {live_path})" if box else "(local mode)")
        )
    elif live_ok:
        parts.append(f"live copy at {live_path} strips both headers")
    else:
        parts.append(
            f"live copy at {live_path} does NOT strip " + ", ".join(live_missing)
        )

    c.detail = "; ".join(parts)
    if box:
        c.status = PASS if (repo_ok and live_ok) else FAIL
    else:
        # A wrong repo copy is a defect an agent can fix from here; a right one
        # proves only half, because the live file is the boundary.
        c.status = FAIL if not repo_ok else SKIP
    if c.status != PASS:
        c.remediation = remediation

    # Drift warning — deliberately a note on a check that may otherwise pass,
    # because two correct-looking files that differ is the state that produces
    # "but I fixed that" three months later.
    if (
        live_text is not None
        and repo_file.is_file()
        and live_text.strip()
        != repo_file.read_text(encoding="utf-8", errors="replace").strip()
    ):
        c.notes.append(
            f"DRIFT: {live_path} differs from {repo_file}. Every deploy "
            "installs the repo copy (vps_apply.sh, H-60), so the last deploy "
            "did not finish, or someone edited the box by hand. Diff them."
        )
    if live_ok is None and box:
        c.notes.append(
            f"{live_path} was not readable — if Caddy is installed, re-run "
            "with enough privilege to read it, or this check is only telling "
            "you about the repo copy."
        )
    return c


# ── Check 4 — a restore path exists, and backups land ────────────────────────

_BACKUP_MAX_AGE_SECONDS = 48 * 3600

#: A dump smaller than this is not a data backup. A ``pg_dump --schema-only``
#: of this database is a few hundred kB of DDL; a ``-Fc`` dump WITH rows is
#: orders of magnitude larger. This floor exists for exactly one failure the
#: previous version of this check waved through: a timer dumping schema-only
#: into a dated directory, with a MANIFEST beside it, reported as
#: "Backups run, land, and are recent" while backing up zero rows.
_MIN_DUMP_BYTES = 256 * 1024

#: Files that count as BO-23 done-when 3, "a runbook". The checklist names
#: ``deploy/hostinger/RESTORE.md`` "or a section in that README"; the BO-23
#: branch writes a differently-named file. All are accepted, because the
#: done-when is that the procedure is WRITTEN DOWN, not where.
_RUNBOOK_CANDIDATES = (
    "deploy/hostinger/RESTORE.md",
    "deploy/hostinger/BACKUP-RESTORE.md",
    "deploy/hostinger/BACKUP_RESTORE.md",
    "project-docs/specs/backup_and_restore.md",
    "docs/runbooks/restore.md",
)

#: Dump extensions this check will size-test. ``.dump``/``.pgc`` are ``-Fc``
#: custom-format archives (BO-23 done-when 1); ``.sql.gz`` is a compressed
#: plain dump, accepted because it still carries rows.
_DUMP_SUFFIXES = (".dump", ".pgc", ".backup", ".sql.gz", ".sql")


def _repo_restore_path() -> tuple[list[str], list[str]]:
    """``(problems, satisfied)`` for BO-23's four *repo-side* done-whens.

    Deliberately measured against ``FOUNDATION_BUILDOUT_CHECKLIST.md``'s BO-23
    §"Done when" (items 1-4) and nothing else. The previous version of this
    check asserted a timer unit name and a MANIFEST.txt that BO-23 never
    specifies, which meant G3 could not go green even after BO-23 shipped
    exactly what it promised — a permanently-blocked gate.
    """
    problems: list[str] = []
    satisfied: list[str] = []

    backup_sh = REPO_ROOT / "scripts" / "backup_db.sh"
    restore_sh = REPO_ROOT / "scripts" / "restore_db.sh"
    migrate_sh = REPO_ROOT / "scripts" / "apply_migrations.sh"

    # (1) a data-inclusive dump script.
    if not backup_sh.is_file():
        problems.append("scripts/backup_db.sh does not exist (BO-23 done-when 1)")
    else:
        body = backup_sh.read_text(encoding="utf-8", errors="replace")
        if "pg_dump" not in body:
            problems.append("scripts/backup_db.sh never calls pg_dump")
        elif not re.search(r"-Fc\b|--format[= ]c(ustom)?\b", body):
            problems.append(
                "scripts/backup_db.sh does not take a -Fc custom-format dump, "
                "so a single table cannot be restored out of it "
                "(BO-23 done-when 1)"
            )
        elif re.search(r"pg_dump[^\n|]*--schema-only", body):
            problems.append(
                "scripts/backup_db.sh passes --schema-only to pg_dump — that "
                "is structure with ZERO ROWS, which is what dump_schema.sh "
                "already does and is not a backup"
            )
        else:
            satisfied.append("scripts/backup_db.sh takes a data-inclusive -Fc dump")

    # (2) the documented inverse.
    if not restore_sh.is_file():
        problems.append("scripts/restore_db.sh does not exist (BO-23 done-when 2)")
    elif "pg_restore" not in restore_sh.read_text(
        encoding="utf-8", errors="replace"
    ):
        problems.append("scripts/restore_db.sh never calls pg_restore")
    else:
        satisfied.append("scripts/restore_db.sh restores via pg_restore")

    # (3) a runbook that contains the VERIFY step, not just the commands.
    #     "A backup nobody has restored is not a backup" — BO-23 done-when 3.
    runbook = next(
        (p for p in _RUNBOOK_CANDIDATES if (REPO_ROOT / p).is_file()), None
    )
    if runbook is None:
        problems.append(
            "no restore runbook found at any of: "
            + ", ".join(_RUNBOOK_CANDIDATES)
            + " (BO-23 done-when 3)"
        )
    else:
        text_ = (REPO_ROOT / runbook).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"verif|row count|checksum", text_, re.IGNORECASE):
            problems.append(
                f"{runbook} exists but describes no VERIFICATION step — "
                "BO-23 done-when 3 requires it in words: a backup nobody has "
                "restored is not a backup"
            )
        else:
            satisfied.append(f"{runbook} documents restore + verification")

    # (4) the pre-migration hook — the clause that stops "forward-only ladder"
    #     and "no backup" from compounding.
    if migrate_sh.is_file() and "backup_db.sh" in migrate_sh.read_text(
        encoding="utf-8", errors="replace"
    ):
        satisfied.append("apply_migrations.sh takes a backup before replaying")
    else:
        deploy_yml = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
        if deploy_yml.is_file() and "backup_db.sh" in deploy_yml.read_text(
            encoding="utf-8", errors="replace"
        ):
            satisfied.append("the deploy step takes a backup before migrating")
        else:
            problems.append(
                "nothing takes a backup BEFORE the migration ladder replays — "
                "neither scripts/apply_migrations.sh nor the deploy workflow "
                "references backup_db.sh (BO-23 done-when 4)"
            )

    return problems, satisfied


def check_restore_path(env: Env, box: bool) -> Check:
    """A restore path exists in the repo, and on the box backups actually land.

    WHAT THIS PREVENTS. Inviting colleagues multiplies the amount of work that
    exists only in this database — their notes, their tasks, their mail state.
    ``work_plan.md`` §2 exception 2 (checklist §BO-23) records the measured
    position: the only DB script that dumps anything is
    ``scripts/dump_schema.sh``, which is ``pg_dump --schema-only`` — structure,
    zero rows. So the spec's line is "do not invite colleagues onto a database
    with no restore path", and this check has to actually look for one.

    TWO HALVES, because they fail differently.

    * **The repo half, checked in every mode.** BO-23's done-whens 1-4: a
      data-inclusive ``-Fc`` dump script, a ``pg_restore`` inverse, a runbook
      that contains the *verification* step, and a pre-migration hook. This is
      the half that answers "is there a restore path at all", and it is
      answerable from a laptop because it is files in this repository.
    * **The box half, box mode only.** A real artefact under ``BACKUP_DIR``,
      newer than 48h, containing a dump above ``_MIN_DUMP_BYTES``.

    WHAT CHANGED AND WHY. This check used to assert four things — an
    ``acb-backup.timer`` unit, a subdirectory, a ``MANIFEST.txt``, an age — and
    **none** of them tested the dump's format, size, row count, or the
    existence of a restore script. Two consequences, both real:

    1. A timer running ``pg_dump --schema-only`` into a dated directory with a
       MANIFEST beside it printed "Backups run, land, and are recent" while
       backing up zero rows.
    2. BO-23's own done-when specifies ``scripts/backup_db.sh``,
       ``scripts/restore_db.sh``, a runbook and a pre-migration hook — and
       specifies **no timer unit name and no MANIFEST**. So the old check
       looked for artefacts BO-23 would never produce, and G3 could not go
       green even after BO-23 shipped precisely what it promised.

    The schedule is now inferred from freshness rather than asserted by unit
    name: a named timer is probed and reported as a NOTE when it is not found,
    because installing one is OWNER-GATE and BO-23 ships no unit file. A stale
    or absent artefact still FAILs — that is the assertion that a schedule is
    in fact running.
    """
    c = Check("restore_path", "A restore path exists and backups are recent")
    # Kept as the configured POSIX string for display: this path only exists on
    # the box, and rendering it through pathlib on a Windows dev machine turns
    # /opt/acb/backups into \opt\acb\backups, which reads as a typo.
    backup_dir_raw = env.get("BACKUP_DIR", "/opt/acb/backups")
    backup_dir = Path(backup_dir_raw)
    unit = env.get("BACKUP_TIMER", "acb-backup.timer")

    repo_problems, repo_satisfied = _repo_restore_path()

    remediation = (
        "BO-23 (FOUNDATION_BUILDOUT_CHECKLIST.md, 'Done when' 1-4) is the "
        "owning ticket, and this check is measured against it verbatim: "
        "scripts/backup_db.sh (data-inclusive pg_dump -Fc, not --schema-only), "
        "scripts/restore_db.sh (pg_restore into a NAMED database, refusing the "
        "live one without a force flag), a runbook that states how to verify a "
        "restore — a row-count or checksum assertion, because a backup nobody "
        "has restored is not a backup — and a pre-migration hook so the "
        "forward-only ladder cannot compound with having nothing to roll back "
        "to. Writing all four is AGENT-SAFE; RUNNING any of them, installing a "
        "timer, or pointing anything at prod data is OWNER-GATE (work_plan.md "
        "§6). Until it ships, the only backup is Hostinger's weekly whole-VPS "
        "image (deploy/hostinger/README.md:115) — up to seven days of loss, "
        "from an image whose restore has never been exercised. Do not invite "
        "colleagues onto a database with no restore path."
    )
    c.remediation = remediation

    if repo_satisfied:
        c.notes.extend(repo_satisfied)

    if repo_problems:
        c.status = FAIL
        c.detail = "no restore path in the repo: " + "; ".join(repo_problems)
        return c

    if not box:
        c.status = SKIP
        c.detail = (
            "the repo half is satisfied (see notes), but whether a backup has "
            f"actually landed recently is only answerable on the box — "
            f"{backup_dir_raw} and the schedule live there"
        )
        return c

    problems: list[str] = []

    if not backup_dir.is_dir():
        problems.append(f"{backup_dir} does not exist")
    else:
        entries = list(backup_dir.iterdir())
        if not entries:
            problems.append(f"{backup_dir} is empty — nothing has ever landed")
        else:
            newest = max(entries, key=lambda p: p.stat().st_mtime)
            age_h = (time.time() - newest.stat().st_mtime) / 3600.0
            if age_h > _BACKUP_MAX_AGE_SECONDS / 3600.0:
                problems.append(
                    f"newest backup {newest.name} is {age_h:.1f}h old (>48h) — "
                    "whatever schedule was installed is not running"
                )
            else:
                c.notes.append(f"newest backup {newest.name} is {age_h:.1f}h old")

            # Is there a dump in it, and does it plausibly carry rows?
            dumps = (
                [p for p in newest.iterdir()
                 if p.is_file() and p.name.endswith(_DUMP_SUFFIXES)]
                if newest.is_dir()
                else ([newest] if newest.name.endswith(_DUMP_SUFFIXES) else [])
            )
            if not dumps:
                problems.append(
                    f"{newest} contains no dump file "
                    f"({', '.join(_DUMP_SUFFIXES)}) — a dated directory is not "
                    "a backup"
                )
            else:
                biggest = max(dumps, key=lambda p: p.stat().st_size)
                size = biggest.stat().st_size
                if size < _MIN_DUMP_BYTES:
                    problems.append(
                        f"the largest dump in {newest.name} is {biggest.name} "
                        f"at {size / 1024:.0f} kB, under the "
                        f"{_MIN_DUMP_BYTES // 1024} kB floor — that is the "
                        "size of a --schema-only dump, i.e. zero rows"
                    )
                else:
                    c.notes.append(
                        f"largest dump {biggest.name} is "
                        f"{size / (1024 * 1024):.1f} MB"
                    )

            # A MANIFEST is not a BO-23 done-when, so it is read when present
            # and never required. Reported because it is what tells an operator
            # what was true when the backup was taken.
            manifest = (newest / "MANIFEST.txt") if newest.is_dir() else None
            if manifest is not None and manifest.is_file():
                c.notes.append(f"{manifest.name} is present and readable")

    # The schedule, probed but not asserted by name: BO-23 ships no unit file,
    # and installing one is the owner's act. Freshness above is the assertion.
    if shutil.which("systemctl") is not None:
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=20, check=False,
            )
            state = proc.stdout.strip() or proc.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            state = f"error: {exc}"
        if state == "active":
            c.notes.append(f"{unit} is active")
        else:
            c.notes.append(
                f"no active '{unit}' (systemctl says '{state}'). Not a "
                "failure: BO-23 specifies scripts and a runbook, not a unit "
                "name, so the schedule may be cron or a differently-named "
                "timer. Set BACKUP_TIMER to name it. The freshness check above "
                "is what actually proves a schedule is running."
            )

    if problems:
        c.status = FAIL
        c.detail = "; ".join(problems)
        return c

    c.status = PASS
    c.remediation = ""
    c.detail = (
        f"the repo carries a backup script, a pg_restore inverse, a verified "
        f"runbook and a pre-migration hook, and {backup_dir} holds a dump "
        "newer than 48h with rows in it"
    )
    return c


# ── Check 5 — the six system roles are seeded ────────────────────────────────

def check_roles_seeded(db: Psql) -> Check:
    """All six seeded roles exist, ``owner`` grants ``*``, and somebody can invite.

    WHAT THIS PREVENTS. Onboarding is entirely role-driven: ``POST
    /admin/members`` is gated on ``admin:members:invite``
    (``routes/admin/members.py:145-146``) and the role a new colleague gets is
    the whole of what they can see. If migration 130 has not applied, or has
    applied without its seed block, the invite screen is a dead end.

    The third clause — at least one ACTIVE member holds a role granting
    ``admin:members:invite`` — is the 2026-07-30 lockout expressed as a query.
    ``*`` is matched with the product's own ``permission_matches`` rather than
    a string compare, so an owner holding the wildcard counts.
    """
    c = Check("roles_seeded", "System roles seeded and an inviter exists")
    if not db.available:
        c.status = SKIP
        c.detail = db.reason
        c.remediation = (
            "Run on the box (or with the Postgres container reachable). "
            "Nothing here can be answered without the live database."
        )
        return c

    rows, err = db.query(
        "SELECT r.slug FROM org_role r "
        "JOIN organization o ON o.id = r.organization_id "
        "WHERE o.slug = 'default' ORDER BY r.slug"
    )
    if err:
        c.status = FAIL
        c.detail = err
        c.remediation = (
            "The org tables are unreadable. Apply "
            "infra/postgres/130_org_access_control.sql "
            "(scripts/apply_migrations.sh replays every numbered migration "
            "idempotently)."
        )
        return c

    seeded = {r[0] for r in rows if r}
    missing = [s for s in SYSTEM_ROLES if s not in seeded]

    owner_rows, owner_err = db.query(
        "SELECT rp.permission FROM org_role_permission rp "
        "JOIN org_role r ON r.id = rp.role_id "
        "JOIN organization o ON o.id = r.organization_id "
        "WHERE o.slug = 'default' AND r.slug = 'owner'"
    )
    owner_perms = {r[0] for r in owner_rows if r}

    inviter_rows, inviter_err = db.query(
        "SELECT u.email, rp.permission FROM app_user u "
        "JOIN user_role ur ON ur.user_id = u.id "
        "JOIN org_role r ON r.id = ur.role_id "
        "JOIN org_role_permission rp ON rp.role_id = r.id "
        "WHERE COALESCE(u.status, 'active') = 'active'"
    )
    inviters = sorted({
        r[0] for r in inviter_rows
        if len(r) >= 2 and permission_matches(r[1], "admin:members:invite")
    })

    problems = []
    if missing:
        problems.append(f"roles missing: {', '.join(missing)}")
    if owner_err:
        problems.append(f"owner permissions unreadable: {owner_err}")
    elif "*" not in owner_perms:
        problems.append("the 'owner' role does not grant '*'")
    if inviter_err:
        problems.append(f"member roles unreadable: {inviter_err}")
    elif not inviters:
        problems.append(
            "NO active member holds admin:members:invite — nobody can invite "
            "anybody (this is the 2026-07-30 lockout)"
        )

    if problems:
        c.status = FAIL
        c.detail = "; ".join(problems)
        c.remediation = (
            "Re-apply infra/postgres/130_org_access_control.sql and "
            "131_integration_memory_permissions.sql (both idempotent). If "
            "roles exist but nobody holds one, set EXECUTIVE_EMAILS to the "
            "owner's address and restart the gateway — "
            "acb_auth.access.ensure_owner_bootstrap (access.py:467-519) runs "
            "at startup and provisions the first entry as owner, but ONLY "
            "when no member holds 'owner' at all."
        )
        return c

    c.status = PASS
    c.detail = (
        f"all {len(SYSTEM_ROLES)} system roles seeded; 'owner' grants '*'; "
        f"{len(inviters)} active member(s) can invite: {', '.join(inviters)}"
    )
    return c


# ── Check 6 — Centers are reachable ──────────────────────────────────────────

def _center_slugs_from_migrations() -> tuple[set[str], list[str]]:
    """``center.*`` slugs seeded by any migration that writes ``feature_catalog``.

    The local-mode stand-in for querying the table. Same answer whenever the
    migrations have in fact been applied, and it is honest about its source in
    the check's detail line.
    """
    slugs: set[str] = set()
    files: list[str] = []
    mig_dir = REPO_ROOT / "infra" / "postgres"
    for path in sorted(mig_dir.glob("*.sql")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "feature_catalog" not in text:
            continue
        found = set(re.findall(r"'(center\.[a-z0-9._-]+)'", text))
        if found:
            slugs |= found
            files.append(path.name)
    return slugs, files


def check_centers_reachable(db: Psql) -> Check:
    """Every seeded ``center.*`` feature slug exists in ``permissions.FEATURES``.

    WHAT THIS PREVENTS — and it is not hypothetical. ``/auth/me`` returns
    ``list(access.allowed_features())`` (``routes/admin/me.py:84``) and
    ``allowed_features()`` iterates the hardcoded tuple
    ``acb_auth.permissions.FEATURES``, NOT the ``feature_catalog`` table. The
    frontend gates ``/centers/<slug>`` on exactly those slugs
    (``lib/access.ts``), and ``lib/nav.ts`` drops a whole nav section when it
    empties. Before the WS-13 fix, migration 140 seeded six catalog rows that
    nothing ever read, so **Centers were unreachable by everybody including an
    owner holding ``*``** — the wildcard is only evaluated against the literals
    in that tuple.

    This is the invariant that regression, as an operational check rather than
    only a unit test, because the next person to add a Center will edit SQL and
    a TS registry and may not know about the Python tuple.
    """
    c = Check("centers_reachable", "Every Center feature slug is in FEATURES")

    if db.available:
        rows, err = db.query(
            "SELECT slug FROM feature_catalog WHERE slug LIKE 'center.%' "
            "ORDER BY sort_order, slug"
        )
        if err:
            c.status = FAIL
            c.detail = f"could not read feature_catalog: {err}"
            c.remediation = (
                "Apply infra/postgres/130_org_access_control.sql then "
                "140_center_features.sql."
            )
            return c
        seeded = {r[0] for r in rows if r}
        source = "feature_catalog (live database)"
    else:
        seeded, files = _center_slugs_from_migrations()
        source = "migrations " + ", ".join(files) if files else "migrations (none found)"

    if not seeded:
        c.status = FAIL
        c.detail = f"no center.* slugs found in {source}"
        c.remediation = (
            "Migration 140_center_features.sql seeds the six Center rows. If "
            "the table is empty, migrations have not been applied."
        )
        return c

    known = set(FEATURES)
    missing = sorted(s for s in seeded if s not in known)
    if missing:
        c.status = FAIL
        c.detail = (
            f"{len(missing)} of {len(seeded)} Center slug(s) from {source} are "
            f"absent from acb_auth.permissions.FEATURES: {', '.join(missing)}"
        )
        c.remediation = (
            "Add each missing slug to the FEATURES tuple in "
            "packages/acb_auth/acb_auth/permissions.py, in the migration's "
            "sort_order. Registering a Center takes five edits, not one — see "
            "project-docs/specs/department_centers.md §2 for the "
            "checklist. tests/unit/test_org_access_control.py pins both "
            "directions."
        )
        return c

    c.status = PASS
    c.detail = (
        f"all {len(seeded)} Center slug(s) from {source} are present in "
        f"FEATURES: {', '.join(sorted(seeded))}"
    )
    return c


# ── Check 7 — default-deny holds ─────────────────────────────────────────────

_PROBE_EMAIL = "onboarding-preflight-probe@invalid.fracktal.in"


def check_default_deny(env: Env, db: Psql) -> Check:
    """An unknown email has no ``app_user`` row, and the fallback is off.

    WHAT THIS PREVENTS. ``acb_auth.access.resolve_access`` returns
    ``EffectiveAccess(is_active=False)`` when the ``app_user`` lookup finds no
    row (:257-263) — an authenticated stranger is provisioned by nobody and
    therefore holds nothing. That is the property the whole invite model rests
    on: ``POST /admin/members`` is what turns a directory identity into a
    member (``routes/admin/members.py:151-157``).

    WHY THIS CHECK REFUSES TO RUN LOCALLY. ``resolve_access`` ALSO returns
    ``is_active=False`` when the database is simply unreachable
    (``_degraded``, :159-167). So calling the resolver from a laptop and
    finding "inactive" proves nothing — it is exactly what a broken DSN looks
    like. A vacuous green here would be worse than a skip, because this is the
    check somebody will cite when they decide it is safe to invite people.

    On the box the check is made non-vacuous three ways: the probe address has
    no row (the same ``lower(u.email) = :email`` predicate ``_ACCESS_SQL:198``
    uses); at least one active member DOES have one (so "no row" is not "the
    table is empty", which is the lockout shape); and
    ``ACCESS_LEGACY_FALLBACK`` is off, since with it on an absent access table
    degrades every caller to an approximation of access instead of refusing
    them (``access.py:109-129``).
    """
    c = Check("default_deny", "An unknown email resolves to no access")

    fallback = env.get("ACCESS_LEGACY_FALLBACK", "").lower() in {
        "1", "true", "yes", "on",
    }

    if not db.available:
        c.status = SKIP
        c.detail = (
            db.reason
            + " — and a local run cannot answer this honestly: resolve_access "
            "degrades to is_active=False on an unreachable database too, so a "
            "local PASS would be indistinguishable from a broken DSN"
        )
        c.remediation = "Run on the box, against the live database."
        if fallback:
            c.notes.append(
                "ACCESS_LEGACY_FALLBACK is ON in this environment. Turn it "
                "off before onboarding: it makes a missing access table "
                "degrade to the legacy executive/employee mapping instead of "
                "refusing."
            )
        return c

    probe_rows, probe_err = db.query(
        "SELECT count(*) FROM app_user WHERE lower(email) = "
        f"'{_PROBE_EMAIL}'"
    )
    active_rows, active_err = db.query(
        "SELECT count(*) FROM app_user WHERE COALESCE(status, 'active') = 'active'"
    )

    problems = []
    if probe_err:
        problems.append(f"probe query failed: {probe_err}")
    elif probe_rows and probe_rows[0][0].strip() != "0":
        problems.append(
            f"an app_user row exists for the synthetic probe address "
            f"{_PROBE_EMAIL} — somebody has been inserting rows by hand"
        )
    if active_err:
        problems.append(f"member count failed: {active_err}")
    elif active_rows and active_rows[0][0].strip() == "0":
        problems.append(
            "app_user holds ZERO active members, so 'the unknown email has no "
            "row' is vacuously true and nobody can sign in either — this is "
            "the 2026-07-30 lockout state"
        )
    if fallback:
        problems.append(
            "ACCESS_LEGACY_FALLBACK is enabled, so a missing access table "
            "degrades to the legacy role mapping instead of refusing"
        )

    if problems:
        c.status = FAIL
        c.detail = "; ".join(problems)
        c.remediation = (
            "Unset ACCESS_LEGACY_FALLBACK (it is a recovery hatch for a "
            "failed migration, not a setting). If app_user is empty, set "
            "EXECUTIVE_EMAILS and restart the gateway so "
            "ensure_owner_bootstrap can provision an owner. The code-level "
            "guarantee that a missing row resolves to no access is pinned by "
            "tests/unit/test_default_deny_auth.py."
        )
        return c

    c.status = PASS
    c.detail = (
        f"the probe address has no app_user row while "
        f"{active_rows[0][0].strip()} active member(s) do; "
        "ACCESS_LEGACY_FALLBACK is off"
    )
    return c


# ── Reporting ────────────────────────────────────────────────────────────────

#: Typographic characters this file uses in prose, and their ASCII stand-ins.
#: The report is read on a VPS console, in a CI log and (when an agent runs it
#: locally) on Windows, where the default code page cannot encode any of them
#: and silently substitutes "?" mid-sentence. Folding is done at the boundary
#: rather than by writing ASCII prose, so the source stays readable.
#: Keyed by codepoint rather than by literal so the table itself does not
#: trip the ambiguous-character lint (RUF001) it exists to accommodate.
_ASCII_FOLD = {
    chr(0x2014): "--",   # em dash
    chr(0x2013): "-",    # en dash
    chr(0x2018): "'",    # left single quotation mark
    chr(0x2019): "'",    # right single quotation mark
    chr(0x201C): '"',    # left double quotation mark
    chr(0x201D): '"',    # right double quotation mark
    chr(0x00A7): "S",    # section sign
    chr(0x00B7): "*",    # middle dot
    chr(0x2026): "...",  # horizontal ellipsis
    chr(0x2192): "->",   # rightwards arrow
}


def _emit(text: str) -> None:
    """Print, degrading to ASCII when the output stream cannot carry the prose."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        for uni, ascii_ in _ASCII_FOLD.items():
            text = text.replace(uni, ascii_)
        text = text.encode("ascii", "replace").decode("ascii")
    print(text)


def render(checks: list[Check], box: bool) -> str:
    width = max(len(c.title) for c in checks) + 2
    lines = [
        "",
        "COLLEAGUE ONBOARDING PREFLIGHT (WS-24)",
        "spec: project-docs/specs/colleague_onboarding.md §1",
        f"mode: {'BOX — full check' if box else 'LOCAL — box-only checks are SKIPped, see below'}",
        "",
    ]
    for c in checks:
        lines.append(f"  [{c.status:4}] {c.title.ljust(width)} {c.detail}")
        for n in c.notes:
            lines.append(f"         note: {n}")
        if c.status != PASS and c.remediation:
            for i, para in enumerate(c.remediation.split("\n")):
                lines.append(("         fix:  " if i == 0 else "               ") + para)
        lines.append("")

    failed = [c for c in checks if c.status == FAIL]
    skipped = [c for c in checks if c.status == SKIP]
    lines.append(
        f"  {sum(1 for c in checks if c.status == PASS)} pass · "
        f"{len(failed)} FAIL · {len(skipped)} skip"
    )
    if failed:
        lines.append("")
        lines.append("  NOT READY. Do not invite anyone until these are green:")
        for c in failed:
            lines.append(f"    - {c.title}")
    if skipped:
        lines.append("")
        lines.append(
            "  SKIPPED checks were not answered, not passed. A local run that "
            "exits 0"
        )
        lines.append(
            "  means only 'nothing checkable from here is broken'. The gate is "
            "a BOX run."
        )
    if not failed and not skipped:
        lines.append("")
        lines.append("  READY. Follow the runbook in colleague_onboarding.md §2.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Are we ready to invite a colleague? (WS-24)",
    )
    ap.add_argument(
        "--mode", choices=("auto", "box", "local"), default="auto",
        help=(
            "'box' runs every check against the live host and database; "
            "'local' refuses the box-only ones and says so; 'auto' (default) "
            "picks box when /etc/caddy exists and systemctl is on PATH."
        ),
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.mode == "auto":
        box = Path("/etc/caddy").is_dir() and shutil.which("systemctl") is not None
    else:
        box = args.mode == "box"

    default_app_dir = "/opt/acb/app" if box else str(REPO_ROOT)
    env = Env(Path(os.environ.get("APP_DIR", default_app_dir)))
    db = Psql(env, enabled=box)

    checks = [
        check_internal_token(env, box),
        check_executive_emails(env, box),
        check_caddy_strip(env, box),
        check_restore_path(env, box),
        check_roles_seeded(db),
        check_centers_reachable(db),
        check_default_deny(env, db),
    ]

    if args.json:
        _emit(json.dumps(
            {
                "mode": "box" if box else "local",
                "app_dir": str(env.app_dir),
                "ready": all(c.status == PASS for c in checks),
                "checks": [c.as_dict() for c in checks],
            },
            indent=2,
        ))
    else:
        _emit(render(checks, box))

    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
