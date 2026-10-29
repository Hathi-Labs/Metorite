"""The seed of the synthetic set: the records as SQL rows of one org (WS-KG KG-1).

:func:`statements` turns the records of ``fixtures/synthetic.json`` into
``(sql, rows)`` pairs. Every value is a bound parameter, and every row carries
``organization_id``. The id of a record is :func:`questions.gold_id`, so the
gold of a question is known before the seed runs.

This module opens no connection. The R8 test runs the statements on the admin
connection of its fixture. ``run.py --fixture`` runs them in
``acb_common.db.tenant_session(org)``, the one seam.

The org also needs the switches that open CRM and Knowledge. :func:`member`
returns the one member who asks, with the features of the five sources. No
embedding is ever asked: ``KNOWLEDGE_EMBED`` stays off, so no model runs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from evals.search_company.questions import Fixture, Record, gold_id

Statement = tuple[str, list[dict[str, Any]]]

#: The features that the five sources need, and the Knowledge manager right.
PERMISSIONS = (
    "feature:projects", "feature:people", "feature:email", "feature:crm",
    "feature:knowledge", "knowledge:manage",
)


def member(org: str) -> str:
    """The one member of the synthetic org, who asks every question."""
    return f"kg1-{org[:8]}@kg1-eval.test"


def organization(org: str) -> Statement:
    """The org row, for a database that does not hold it yet (the CLI)."""
    return (
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (CAST(:o AS uuid), :s, :s) ON CONFLICT DO NOTHING",
        [{"o": org, "s": f"kg1-{org[:8]}"}],
    )


def _when(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _of(records: tuple[Record, ...], source: str) -> list[Record]:
    return [r for r in records if r.source == source]


def statements(org: str, fixture: Fixture, *, message_org: bool = True) -> list[Statement]:
    """The rows of one org: member, tasks, people, email, CRM and Knowledge.

    *message_org* is False for a ladder that phase 01 of the tenant promotion has
    not touched: there ``email_messages`` has no ``organization_id`` column.
    """
    who = member(org)
    out: list[Statement] = []
    out.append((
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING",
        [{"e": who, "o": org}],
    ))
    out += _tasks(org, who, _of(fixture.records, "tasks"))
    out += _people(org, _of(fixture.records, "people"))
    out += _email(org, who, _of(fixture.records, "email"), message_org)
    out += _crm(org, _of(fixture.records, "crm"))
    out += _knowledge(org, who, _of(fixture.records, "knowledge"))
    return out


def _tasks(org: str, who: str, records: list[Record]) -> list[Statement]:
    project, lane = gold_id(org, "project"), gold_id(org, "lane")
    return [
        ("INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
         "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, 'manual', :me, true)",
         [{"p": project, "o": org, "n": f"kg1-{org[:8]}", "me": who}]),
        # An org grant, so the member sees each task of the project.
        ("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id) "
         "VALUES (CAST(:p AS uuid), 'org', :me, CAST(:o AS uuid))",
         [{"p": project, "me": who, "o": org}]),
        ("INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
         "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', true)",
         [{"s": lane, "p": project}]),
        ("INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
         "status_id, title, description, source, created_by, task_number, updated_at) "
         "VALUES (CAST(:t AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
         "CAST(:s AS uuid), :ti, :d, 'manual', :me, :n, :u)",
         [{"t": gold_id(org, r.key), "o": org, "p": project, "s": lane,
           "ti": r.fields["title"], "d": r.fields.get("description", ""), "me": who,
           "n": i, "u": _when(r.fields.get("days_ago", 1))}
          for i, r in enumerate(records, start=1)]),
    ]


def _people(org: str, records: list[Record]) -> list[Statement]:
    return [(
        "INSERT INTO people (id, name, role, department, bio, source, organization_id) "
        "VALUES (CAST(:i AS uuid), :n, :r, :d, :b, :s, CAST(:o AS uuid))",
        [{"i": gold_id(org, r.key), "n": r.fields["name"], "r": r.fields["role"],
          "d": r.fields["department"], "b": r.fields.get("bio", ""),
          "s": f"kg1-{org[:8]}", "o": org} for r in records],
    )]


def _email(org: str, who: str, records: list[Record], message_org: bool) -> list[Statement]:
    account = gold_id(org, "account")
    extra_col, extra_val = (", organization_id", ", CAST(:o AS uuid)") if message_org else ("", "")
    return [
        ("INSERT INTO email_accounts (id, user_id, provider, email_address, "
         "credentials_encrypted, organization_id) VALUES (CAST(:a AS uuid), :u, "
         "'microsoft', :u, 'x', CAST(:o AS uuid))",
         [{"a": account, "u": who, "o": org}]),
        ("INSERT INTO email_messages (id, account_id, provider_message_id, "
         "from_address, to_addresses, subject, body_text, snippet, received_at"
         + extra_col + ") VALUES (CAST(:i AS uuid), CAST(:a AS uuid), :pm, "
         "CAST(:f AS jsonb), CAST(:t AS jsonb), :s, :b, :sn, :r" + extra_val + ")",
         [{"i": gold_id(org, r.key), "a": account, "pm": f"kg1-{r.key}-{org[:8]}",
           "f": json.dumps({"name": r.fields["from_name"], "email": r.fields["from_email"]}),
           "t": json.dumps([{"email": who}]), "s": r.fields["subject"],
           "b": r.fields["body"], "sn": r.fields["body"][:120],
           "r": _when(r.fields.get("days_ago", 1)), "o": org} for r in records]),
    ]


def _crm(org: str, records: list[Record]) -> list[Statement]:
    lead_status, deal_status = gold_id(org, "lead-status"), gold_id(org, "deal-status")
    out: list[Statement] = [
        ("INSERT INTO crm_settings (organization_id, access) VALUES (CAST(:o AS uuid), "
         "'everyone') ON CONFLICT (organization_id) DO UPDATE SET access = 'everyone'",
         [{"o": org}]),
        ("INSERT INTO crm_lead_statuses (id, name, position, type, is_default, "
         "organization_id) VALUES (CAST(:s AS uuid), 'New', 1, 'open', true, CAST(:o AS uuid))",
         [{"s": lead_status, "o": org}]),
        ("INSERT INTO crm_deal_statuses (id, name, position, type, is_default, "
         "organization_id) VALUES (CAST(:s AS uuid), 'Qualified', 1, 'open', true, "
         "CAST(:o AS uuid))", [{"s": deal_status, "o": org}]),
    ]
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        row = {"i": gold_id(org, r.key), "o": org, "ls": lead_status, "ds": deal_status}
        row.update({k: v for k, v in r.fields.items() if k != "kind"})
        by_kind.setdefault(r.fields["kind"], []).append(row)
    shapes = {
        "company": ("INSERT INTO crm_companies (id, name, industry, organization_id) "
                    "VALUES (CAST(:i AS uuid), :name, :industry, CAST(:o AS uuid))",
                    ("name", "industry")),
        "contact": ("INSERT INTO crm_contacts (id, first_name, last_name, email, "
                    "organization_id) VALUES (CAST(:i AS uuid), :first_name, :last_name, "
                    ":email, CAST(:o AS uuid))", ("first_name", "last_name", "email")),
        "lead": ("INSERT INTO crm_leads (id, lead_name, organization_name, email, status_id, "
                 "organization_id) VALUES (CAST(:i AS uuid), :lead_name, :organization_name, "
                 ":email, CAST(:ls AS uuid), CAST(:o AS uuid))",
                 ("lead_name", "organization_name", "email")),
        "deal": ("INSERT INTO crm_deals (id, name, next_step, status_id, organization_id) "
                 "VALUES (CAST(:i AS uuid), :name, :next_step, CAST(:ds AS uuid), "
                 "CAST(:o AS uuid))", ("name", "next_step")),
    }
    for kind, (sql, _cols) in shapes.items():
        if kind in by_kind:
            out.append((sql, by_kind[kind]))
    return out


def _knowledge(org: str, who: str, records: list[Record]) -> list[Statement]:
    space = gold_id(org, "space")
    return [
        ("INSERT INTO kb_spaces (id, organization_id, slug, name, kind) VALUES "
         "(CAST(:s AS uuid), CAST(:o AS uuid), :slug, 'KG1 company', 'company')",
         [{"s": space, "o": org, "slug": f"kg1-{org[:8]}"}]),
        ("INSERT INTO kb_pages (id, organization_id, space_id, title, body_md, state, "
         "author_email) VALUES (CAST(:i AS uuid), CAST(:o AS uuid), CAST(:s AS uuid), "
         ":t, :b, 'published', :me)",
         [{"i": gold_id(org, r.key), "o": org, "s": space, "t": r.fields["title"],
           "b": r.fields["body"], "me": who} for r in records]),
        # One chunk for each page: the lexical index reads `raw` (the `tsv` column
        # is generated from it). The page is the source of its own chunk.
        ("INSERT INTO kb_chunks (organization_id, space_id, source_kind, source_id, "
         "page_id, ordinal, raw, content_sha, heading_path) VALUES (CAST(:o AS uuid), "
         "CAST(:s AS uuid), 'page', CAST(:i AS uuid), CAST(:i AS uuid), 0, :raw, :sha, :h)",
         [{"o": org, "s": space, "i": gold_id(org, r.key),
           "raw": f"{r.fields['title']}. {r.fields['body']}",
           "sha": hashlib.sha256(r.fields["body"].encode("utf-8")).hexdigest(),
           "h": r.fields["title"]} for r in records]),
    ]


def purge(org: str) -> list[Statement]:
    """Delete the rows of :func:`statements`, children first. For the CLI run."""
    o = [{"o": org}]
    project = [{"p": gold_id(org, "project")}]
    out: list[Statement] = [
        (f"DELETE FROM {t} WHERE organization_id = CAST(:o AS uuid)", o)
        for t in ("kb_chunks", "kb_pages", "kb_spaces", "crm_deals", "crm_leads",
                  "crm_contacts", "crm_companies", "crm_deal_statuses", "crm_lead_statuses",
                  "crm_settings", "email_accounts", "people", "pm_tasks")
    ]
    out.insert(0, ("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)",
                   [{"a": gold_id(org, "account")}]))
    out += [
        (f"DELETE FROM {t} WHERE project_id = CAST(:p AS uuid)", project)
        for t in ("pm_task_statuses", "pm_project_grants")
    ]
    out += [
        ("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)", project),
        ("DELETE FROM app_user WHERE organization_id = CAST(:o AS uuid)", o),
        ("DELETE FROM organization WHERE id = CAST(:o AS uuid)", o),
    ]
    return out
