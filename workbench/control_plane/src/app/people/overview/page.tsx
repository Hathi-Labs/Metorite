"use client";

/**
 * People Center · the Center landing (WS-28l).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.9 · D-PC-14.
 *
 * One screen: headcount by department and status, who is away this week, the
 * load spread, the data-quality counts, and the org's unmanaged roots. Every
 * number is another surface's — the load lines are the workload dashboard's
 * own rollup and the quality line is §5.10's counts, so each section links to
 * the page that owns the full answer rather than re-answering here.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";

import { describeRollup, describeSpread } from "../lib/dashboard";
import { PeopleApiError, peopleApi } from "../lib/api";
import { PAGE_FRAME, PAGE_FRAME_BLOCK } from "../lib/frame";
import {
  type OverviewResponse,
  describeQuality,
  headcountMatrix,
} from "../lib/overview";

function Section({
  title,
  href,
  hrefLabel,
  children,
}: {
  title: string;
  href?: string;
  hrefLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {href ? (
          <Link href={href} className="text-xs text-primary hover:underline">
            {hrefLabel ?? "Open"}
          </Link>
        ) : null}
      </header>
      {children}
    </section>
  );
}

export default function PeopleOverviewPage() {
  const [res, setRes] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const out = await peopleApi.overview();
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
        <p className="text-sm text-muted-foreground">Rolling up…</p>
      </main>
    );
  }

  const matrix = headcountMatrix(res.headcount);
  const qualityLine = describeQuality(res.quality_counts);

  return (
    <main className={PAGE_FRAME}>
      <header>
        <h1 className="flex items-center gap-2 text-lg font-semibold">
          <Icon name="Users" size={20} />
          People
        </h1>
        <p className="text-xs text-muted-foreground">
          {res.total_people} people on record
          {res.partial ? " · work figures cover the projects you can see" : ""}
        </p>
      </header>

      <Section title="Headcount" href="/people" hrefLabel="Directory">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="py-1 pr-3 font-normal">Department</th>
                {matrix.statuses.map((s) => (
                  <th key={s} className="py-1 pr-3 text-right font-normal">
                    {s}
                  </th>
                ))}
                <th className="py-1 text-right font-normal">Total</th>
              </tr>
            </thead>
            <tbody>
              {matrix.departments.map((d) => (
                <tr key={d} className="border-t border-border">
                  <td className="py-1 pr-3">{d}</td>
                  {matrix.statuses.map((s) => (
                    <td
                      key={s}
                      className="py-1 pr-3 text-right text-muted-foreground"
                    >
                      {matrix.cell(d, s) || "–"}
                    </td>
                  ))}
                  <td className="py-1 text-right">{matrix.departmentTotal(d)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {res.work_visible ? (
        <Section
          title="Load"
          href="/people/dashboard"
          hrefLabel="Workload dashboard"
        >
          <p className="text-sm">{describeRollup(res.org)}</p>
          {describeSpread(res.org) ? (
            <p className="mt-1 text-xs text-muted-foreground">
              {describeSpread(res.org)}
            </p>
          ) : null}
          <ul className="mt-2 flex flex-col gap-1">
            {res.departments.map((g) => (
              <li key={g.department} className="text-xs text-muted-foreground">
                <span className="text-foreground">{g.department}</span> —{" "}
                {describeRollup(g)}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section
        title="Away this week"
        href="/people/dashboard"
        hrefLabel="Details"
      >
        {res.org.away.length === 0 ? (
          <p className="text-sm text-muted-foreground">Everybody is in.</p>
        ) : (
          <p className="text-sm">{res.org.away.join(", ")}</p>
        )}
      </Section>

      <Section
        title="Record health"
        href="/people/quality"
        hrefLabel="Coverage & data quality"
      >
        {qualityLine ? (
          <p className="text-sm">{qualityLine}</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Nothing wrong with the record.
          </p>
        )}
        {(res.quality_counts.no_manager ?? 0) > 1 ? (
          // The NUMBER is the §5.10 count; `roots` is the capped name list —
          // numbering from `.length` disagrees with the count past 50 rows.
          <p className="mt-1 text-xs text-muted-foreground">
            {res.quality_counts.no_manager} unmanaged roots
            {res.roots.length < res.quality_counts.no_manager
              ? ` (first ${res.roots.length})`
              : ""}
            :{" "}
            {res.roots.map((r, i) => (
              <span key={r.id}>
                {i > 0 ? ", " : ""}
                <Link href={`/people/${r.id}`} className="hover:underline">
                  {r.name}
                </Link>
              </span>
            ))}
          </p>
        ) : null}
      </Section>
    </main>
  );
}
