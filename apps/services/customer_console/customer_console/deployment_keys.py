"""The operator CLI for deployment keys (``cc_depl_…``). No HTTP route does this.

Spec: ``project-docs/specs/customer_console.md`` §8 gates 7 and 8, and §6
"CP-APP · The Aroh app for an organization". Runbook:
``deploy/hostinger/CUSTOMER_CONSOLE.md`` step 4.

🔴 **OWNER-GATE.** Minting a REAL key, and growing a real key's capabilities,
is the owner's act. This module is the tool for that act. It is safe for an
agent to build it and to run it against a scratch database. It is not safe for
an agent to run it against the live Console database.

**Why a CLI and not a route.** The Console has deliberately NO HTTP route that
issues or widens a deployment key (``customer_console/AGENTS.md``): a route
would put the widest credential a box holds one HTTP call away from anybody
who holds an operator session. A CLI needs the database URL, which only the
Console box holds.

Two commands::

    # Register a box (if it is new), name its app, and mint one key for it.
    uv run python -m customer_console.deployment_keys mint \\
        --deployment-label aroh --base-url https://aroh.dev.metorite.com \\
        --app aroh --capabilities resolve,seat_admin,serve --label "aroh hub"

    # Add capabilities to a live key. The set only grows.
    uv run python -m customer_console.deployment_keys grant \\
        --prefix cc_depl_0123456789ab --capabilities serve

``mint`` prints the token ONCE, on stdout, and the prefix. Only the hash is
stored, so a lost token is replaced by a new mint, never read back. Each
command needs ``CUSTOMER_CONSOLE_DATABASE_URL``.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy.engine import Connection

from customer_console import apps, store
from customer_console.auth import (
    BILLING_PURCHASE_CAPABILITY,
    BILLING_READ_CAPABILITY,
    MEMBER_ADMIN_CAPABILITY,
    PROVISION_CAPABILITY,
    RESOLVE_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
    SERVE_CAPABILITY,
)
from customer_console.keys import ENV_DEPLOYMENT, is_deployment_key, mint_key

__all__ = [
    "AROH_CAPABILITIES",
    "KNOWN_CAPABILITIES",
    "KeyRefused",
    "MintedDeploymentKey",
    "grant",
    "main",
    "mint",
    "parse_capabilities",
]

#: Every capability a deployment key may carry, from the ONE place that names
#: them (``auth.py``). A name outside this set is refused before any write: a
#: typo in a capability is a key that opens no door and fails at the first
#: sign-in, far from the command that caused it.
KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    RESOLVE_CAPABILITY,
    PROVISION_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
    MEMBER_ADMIN_CAPABILITY,
    SERVE_CAPABILITY,
    BILLING_READ_CAPABILITY,
    BILLING_PURCHASE_CAPABILITY,
})

#: The set the Aroh box needs, and no more (CP-APP): ``resolve`` at sign-in,
#: ``seat_admin`` to take and free the member's Aroh seat, ``serve`` for the
#: Router. Named so the runbook and the tests do not each retype it.
AROH_CAPABILITIES: tuple[str, ...] = (
    RESOLVE_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
    SERVE_CAPABILITY,
)


class KeyRefused(Exception):
    """The command refused, and wrote nothing. The message says why."""


@dataclass(frozen=True)
class MintedDeploymentKey:
    """What ``mint`` made. ``token`` is the ONLY copy of the secret."""

    deployment_id: str
    deployment_label: str
    app: str | None
    prefix: str
    token: str
    capabilities: tuple[str, ...]
    created_deployment: bool


def parse_capabilities(raw: str) -> tuple[str, ...]:
    """``"resolve, serve"`` → ``("resolve", "serve")``, in order, once each.

    Refuses an empty set and any unknown name.
    """
    seen: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in seen:
            seen.append(name)
    if not seen:
        raise KeyRefused("name at least one capability")
    unknown = sorted(set(seen) - KNOWN_CAPABILITIES)
    if unknown:
        raise KeyRefused(
            f"unknown capabilities {unknown}; known: {sorted(KNOWN_CAPABILITIES)}"
        )
    return tuple(seen)


def mint(
    conn: Connection,
    *,
    deployment_label: str,
    capabilities: tuple[str, ...],
    base_url: str | None = None,
    app: str | None = None,
    label: str | None = None,
    created_by: str | None = None,
) -> MintedDeploymentKey:
    """Mint one deployment key for the box *deployment_label*.

    * The box exists → the key is minted for it. ``base_url`` is not changed.
    * The box is new → ``base_url`` is required, and the box is registered.
    * ``app`` names the app the box serves. A box that serves another app, or
      an app that another box serves, is refused: one box for each app.

    The caller owns the transaction, so a refusal writes nothing.
    """
    if app is not None and app not in apps.KNOWN_APPS:
        raise KeyRefused(f"unknown app {app!r}; known: {sorted(apps.KNOWN_APPS)}")

    created = False
    deployment_id = store.deployment_by_label(conn, label=deployment_label)
    if deployment_id is None:
        if not base_url:
            raise KeyRefused(
                f"no deployment {deployment_label!r}; pass --base-url to register it"
            )
        deployment_id = store.insert_deployment(
            conn, label=deployment_label, base_url=base_url
        )
        created = True

    if app is not None:
        current = store.deployment_row(conn, deployment_id=deployment_id)
        assert current is not None
        if current["app"] not in (None, app):
            raise KeyRefused(
                f"deployment {deployment_label!r} already serves the "
                f"{current['app']!r} app"
            )
        other = store.app_deployment(conn, app=app)
        if other is not None and other["id"] != deployment_id:
            raise KeyRefused(
                f"the {app!r} app is already served by deployment {other['label']!r}"
            )
        if current["app"] is None:
            store.set_deployment_app(conn, deployment_id=deployment_id, app=app)

    minted = mint_key(env=ENV_DEPLOYMENT)
    store.issue_deployment_key(
        conn,
        deployment_id=deployment_id,
        prefix=minted.prefix,
        key_hash=minted.key_hash,
        label=label,
        created_by=created_by or "deployment_keys-cli",
        capabilities=list(capabilities),
    )
    return MintedDeploymentKey(
        deployment_id=deployment_id,
        deployment_label=deployment_label,
        app=app,
        prefix=minted.prefix,
        token=minted.token,
        capabilities=capabilities,
        created_deployment=created,
    )


def grant(conn: Connection, *, prefix: str, capabilities: tuple[str, ...]) -> list[str]:
    """Add *capabilities* to the live key *prefix*. Returns the new set.

    ⚠️ **The PREFIX, never the token.** The prefix is the public half of a key
    and is safe to type into a shell history. A token is never asked for.
    """
    if not is_deployment_key(prefix):
        raise KeyRefused(f"{prefix!r} is not a deployment key prefix (cc_depl_…)")
    result = store.add_deployment_key_capabilities(
        conn, prefix=prefix, capabilities=list(capabilities)
    )
    if result is None:
        raise KeyRefused(f"no live deployment key {prefix!r}")
    return result


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m customer_console.deployment_keys",
        description="Mint a deployment key, or add capabilities to one (OWNER-GATE).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mint", help="mint one key for a box")
    m.add_argument("--deployment-label", required=True)
    m.add_argument("--capabilities", required=True, help="comma-separated")
    m.add_argument("--base-url", help="required when the box is new")
    m.add_argument("--app", help=f"the app the box serves: {sorted(apps.KNOWN_APPS)}")
    m.add_argument("--label", help="a label for the key")
    m.add_argument("--created-by", help="who mints, for the record")

    g = sub.add_parser("grant", help="add capabilities to a live key")
    g.add_argument("--prefix", required=True, help="cc_depl_<prefix>, never the token")
    g.add_argument("--capabilities", required=True, help="comma-separated")
    return ap


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    engine=None,
) -> int:
    """Run one command. ``0`` on success, ``2`` on a refusal (nothing written)."""
    args = _parser().parse_args(argv)
    if engine is None:
        from customer_console.db import get_engine

        engine = get_engine()
    try:
        caps = parse_capabilities(args.capabilities)
        with engine.begin() as conn:
            if args.command == "mint":
                made = mint(
                    conn,
                    deployment_label=args.deployment_label,
                    capabilities=caps,
                    base_url=args.base_url,
                    app=args.app,
                    label=args.label,
                    created_by=args.created_by,
                )
            else:
                now = grant(conn, prefix=args.prefix, capabilities=caps)
    except KeyRefused as exc:
        print(f"refused: {exc}", file=err)
        return 2

    if args.command == "mint":
        if made.created_deployment:
            print(f"registered deployment {made.deployment_label!r}", file=err)
        print(f"prefix: {made.prefix}", file=err)
        print(f"capabilities: {','.join(made.capabilities)}", file=err)
        if made.app:
            print(f"app: {made.app}", file=err)
        print(
            "The token is printed ONCE on stdout. Store it now; it cannot be read back.",
            file=err,
        )
        print(made.token, file=out)
    else:
        print(f"{args.prefix}: {','.join(now)}", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover - the shell entry point
    raise SystemExit(main())
