"use client";

/**
 * People Center · skills coverage & data quality (WS-28m).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.10 · D-PC-13, D-PC-14.
 *
 * Two questions with one surface, because both are "what is wrong with the
 * record": coverage (bus factor of one, hired-for-but-unclaimed, declared-but-
 * never-used) and quality (no email, migration 148's quarantine, statuses
 * outside the vocabulary, managers who left, unmanaged roots, empty
 * AI-relevant fields).
 *
 * ⚠️ Every row is a defect in the RECORD, never in a person — the lists stay
 * in the server's alphabetical order, and fixing a row happens on the record's
 * own page under that page's own authorization. Nothing here writes.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import PageHeader from "@/components/PageHeader";

import { PeopleApiError, peopleApi } from "../lib/api";
import { PAGE_FRAME, PAGE_FRAME_BLOCK } from "../lib/frame";
import {
  type QualityResponse,
  describeMissing,
  describeScan,
  overflow,
} from "../lib/quality";

function Panel({
  title,
  note,
  count,
  shown,
  children,
}: {
  title: string;
  note: string;
  count: number;
  shown: number;
  children: React.ReactNode;
}) {
  const more = overflow(shown, count);
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium">{title}</h2>
          <p className="text-xs text-muted-foreground">{note}</p>
        </div>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
          {count}
        </span>
      </header>
      {count === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing to fix here.</p>
      ) : (
        <>
          {children}
          {more ? (
            <p className="mt-2 text-[11px] text-muted-foreground">{more}</p>
          ) : null}
        </>
      )}
    </section>
  );
}

function PersonLink({ id, name }: { id: string; name: string }) {
  return (
    <Link href={`/people/${id}`} className="text-primary hover:underline">
      {name}
    </Link>
  );
}

export default function QualityPage() {
  const [res, setRes] = useState<QualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const out = await peopleApi.quality();
        if (live) setRes(out);
      } catch (err) {
        if (live)
          setError(
            err instanceof PeopleApiError
              ? err.message
              : String((err as Error).message)
          );
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  if (error) {
    return (
      <main className={PAGE_FRAME_BLOCK}>
        <p className="text-sm text-muted-foreground">{error}</p>
      </main>
    );
  }
  if (!res) {
    return (
      <main className={PAGE_FRAME_BLOCK}>
        <p className="text-sm text-muted-foreground">Reading the record…</p>
      </main>
    );
  }

  const { coverage, quality, counts } = res;

  return (
    <main className={PAGE_FRAME}>
      <PageHeader
        title="Skills coverage & data quality"
        subtitle="What is wrong with the record — not with anybody. Each row links to where it gets fixed."
      />

      <h2 className="mt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Coverage
      </h2>

      <Panel
        title="Bus factor of one"
        note="Skills exactly one person holds — the org's single points of knowledge"
        count={counts.single_holder}
        shown={coverage.single_holder.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {coverage.single_holder.map((s) => (
            <li key={s.skill} className="flex items-baseline justify-between gap-2">
              <span>{s.skill}</span>
              <span className="text-xs text-muted-foreground">
                only <PersonLink {...s.person} />
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="In titles, in nobody's skills"
        note="The org hires for these words; nobody declares them"
        count={counts.title_terms}
        shown={coverage.title_terms.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {coverage.title_terms.map((t) => (
            <li key={t.term} className="flex items-baseline justify-between gap-2">
              <span>{t.term}</span>
              <span className="text-xs text-muted-foreground">
                {t.people.join(", ")}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Declared, never on a task"
        note={describeScan(coverage)}
        count={counts.unused_skills}
        shown={coverage.unused_skills.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {coverage.unused_skills.map((u) => (
            <li key={u.skill} className="flex items-baseline justify-between gap-2">
              <span>{u.skill}</span>
              <span className="text-xs text-muted-foreground">
                {u.holders === 1 ? "1 person" : `${u.holders} people`}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <h2 className="mt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Quality
      </h2>

      <Panel
        title="Quarantined addresses"
        note="Migration 148 moved these aside instead of dropping them — a human still has to choose which row is the real person"
        count={counts.email_conflict}
        shown={quality.email_conflict.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.email_conflict.map((c) => (
            <li key={c.id} className="flex items-baseline justify-between gap-2">
              <PersonLink id={c.id} name={c.name} />
              <span className="text-xs text-muted-foreground">
                {c.email_conflict}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="No email"
        note="No self-service, no assignment — the record cannot reach them and work cannot reach the record"
        count={counts.no_email}
        shown={quality.no_email.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.no_email.map((p) => (
            <li key={p.id}>
              <PersonLink {...p} />
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Status outside the vocabulary"
        note="148's CHECK tolerates these legacy rows; new writes are already refused"
        count={counts.bad_status}
        shown={quality.bad_status.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.bad_status.map((r) => (
            <li key={r.id} className="flex items-baseline justify-between gap-2">
              <PersonLink id={r.id} name={r.name} />
              <span className="text-xs text-muted-foreground">“{r.status}”</span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Manager left the company"
        note="These people report to an alumni row"
        count={counts.manager_alumni}
        shown={quality.manager_alumni.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.manager_alumni.map((r) => (
            <li key={r.id} className="flex items-baseline justify-between gap-2">
              <PersonLink id={r.id} name={r.name} />
              <span className="text-xs text-muted-foreground">
                reports to {r.manager_name}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="No manager"
        note="The org chart's roots. Exactly one is expected; more means a disconnected tree"
        count={counts.no_manager}
        shown={quality.no_manager.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.no_manager.map((p) => (
            <li key={p.id}>
              <PersonLink {...p} />
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Profile gaps the assistant feels"
        note="Empty fields the assignment suggestions reason over — each person can fill their own in"
        count={counts.missing_ai_fields}
        shown={quality.missing_ai_fields.length}
      >
        <ul className="flex flex-col gap-1 text-sm">
          {quality.missing_ai_fields.map((r) => (
            <li key={r.id} className="flex items-baseline justify-between gap-2">
              <PersonLink id={r.id} name={r.name} />
              <span className="text-xs text-muted-foreground">
                {describeMissing(r)}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </main>
  );
}
