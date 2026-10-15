#!/usr/bin/env python3
"""Backfill the artifact registry from the blob store (WS-60 AM-1).

Spec: ``project-docs/specs/artifact_management.md`` §11 AM-1 and §4.6.

Walks every ``agent_blob`` row under ``inputs/`` and ``outputs/`` of one
organization, or of every organization, and registers each one that has no
row in ``artifacts``, or whose row carries a different sha. The register
upsert is idempotent, so a second run changes nothing.

The owner of a row comes from, in order: the ``u:<email>`` instance key, the
``actor`` of the first history row when it names a member, else the
``--owner`` argument. A row with no owner is skipped and counted.

    uv run python scripts/backfill_artifacts.py --org <uuid>
    uv run python scripts/backfill_artifacts.py --all --owner ops@example.com
    uv run python scripts/backfill_artifacts.py --org <uuid> --dry-run

Runs as the migration runner does, on the box, with ``DATABASE_URL`` of the
tenant database. Every write goes through ``tenant_session(org)``, so FORCE
RLS stamps and checks the tenant. Report the counts in the same message as
the run (CLAUDE.md §3a rule 2).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass
class Counts:
    org: str
    seen: int = 0
    added: int = 0
    repaired: int = 0
    unchanged: int = 0
    no_owner: int = 0
    failed: int = 0

    def line(self) -> str:
        return (
            f"{self.org}: seen={self.seen} added={self.added} repaired={self.repaired} "
            f"unchanged={self.unchanged} no_owner={self.no_owner} failed={self.failed}"
        )


def _orgs(all_orgs: bool, org: str | None) -> list[str]:
    from acb_graph import get_session
    from sqlalchemy import text

    if org:
        return [org]
    if not all_orgs:
        return []
    with get_session() as s:
        rows = s.execute(text("SELECT id FROM organization ORDER BY created_at")).fetchall()
    return [str(r[0]) for r in rows]


def backfill_org(org: str, *, default_owner: str, dry_run: bool) -> Counts:
    from acb_graph import tenant_session
    from acb_memory import artifacts
    from sqlalchemy import text

    counts = Counts(org)
    with tenant_session(org) as s:
        blobs = s.execute(
            text(
                "SELECT b.agent_name, b.instance, b.path, b.content, b.sha256, "
                "       b.mime_type, a.id, a.sha256 AS reg_sha "
                "FROM agent_blob b "
                "LEFT JOIN artifacts a ON a.agent_name = b.agent_name "
                "  AND a.instance = b.instance AND a.path = b.path "
                "WHERE b.folder IN ('inputs', 'outputs') "
                "ORDER BY b.updated_at"
            )
        ).fetchall()
        for agent, instance, path, content, sha, mime, reg_id, reg_sha in blobs:
            counts.seen += 1
            if reg_id is not None and reg_sha == sha:
                counts.unchanged += 1
                continue
            first = s.execute(
                text(
                    "SELECT actor, session_id, run_id FROM agent_file_history "
                    "WHERE agent_name = :a AND instance = :i AND path = :p "
                    "AND action <> 'delete' ORDER BY created_at ASC LIMIT 1"
                ),
                {"a": agent, "i": instance, "p": path},
            ).fetchone()
            owner = instance[2:] if instance.startswith("u:") and "@" in instance else ""
            if not owner and first is not None and "@" in str(first[0] or ""):
                owner = str(first[0])
            owner = owner or default_owner
            if not owner:
                counts.no_owner += 1
                continue
            actor = str(first[0]) if first is not None else "agent"
            prov = artifacts.Provenance(
                owner_email=owner,
                made_by="member" if actor == "user" else agent,
                via="backfill",
                session_id=str(first[1]) if first is not None and first[1] else None,
                run_id=str(first[2]) if first is not None and first[2] else None,
            )
            if dry_run:
                counts.added += 1 if reg_id is None else 0
                counts.repaired += 1 if reg_id is not None else 0
                continue
            got = artifacts.register_in_session(
                s, agent_name=agent, instance=instance, path=path,
                data=bytes(content or b""), sha256=sha, mime_type=mime or "application/octet-stream",
                prov=prov,
            )
            if got is None:
                counts.failed += 1
            elif reg_id is None:
                counts.added += 1
            else:
                counts.repaired += 1
        if dry_run:
            s.rollback()
        else:
            s.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", help="one organization id")
    ap.add_argument("--all", action="store_true", help="every organization")
    ap.add_argument("--owner", default="", help="the owner of a row with no member in its key or history")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)

    from acb_memory import artifacts

    if not artifacts.enabled() and not args.dry_run:
        print("ARTIFACT_REGISTRY_ENABLED is off, so register_in_session writes nothing. "
              "Set it for this run, or pass --dry-run.")
        return 2
    orgs = _orgs(args.all, args.org)
    if not orgs:
        print("Pass --org <uuid> or --all.")
        return 2
    total = Counts("total")
    for org in orgs:
        c = backfill_org(org, default_owner=args.owner, dry_run=args.dry_run)
        print(c.line())
        for k in ("seen", "added", "repaired", "unchanged", "no_owner", "failed"):
            setattr(total, k, getattr(total, k) + getattr(c, k))
    print(total.line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
