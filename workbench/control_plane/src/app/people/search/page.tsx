"use client";

/**
 * People Center · capability search (WS-28d).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.5 · D-PC-13.
 *
 * A single box: "Who can help with extruder firmware?" Every result shows WHY
 * it matched — each signal with its own points, the load, the availability —
 * because a ranking whose reasoning is hidden cannot be argued with, and the
 * person reading it knows things the record does not.
 *
 * ⚠️ **This surface never assigns** (D-PC-13). There is no assign button on a
 * result; the reader takes what they learned to the ordinary task flow. The
 * pre-filled assign action is WS-28j3's, and even there a human confirms.
 */

import Link from "next/link";
import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import { Avatar } from "../components/Avatar";
import { PeopleApiError, peopleApi } from "../lib/api";
import { PAGE_FRAME } from "../lib/frame";
import {
  type CapabilityResponse,
  describeResultLoad,
  describeSignal,
  rankedRows,
} from "../lib/search";

export default function CapabilitySearchPage() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<CapabilityResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setRes(await peopleApi.capabilitySearch(q.trim()));
    } catch (err) {
      // 403 names the grant, 400 says what to type — both actionable verbatim.
      setError(
        err instanceof PeopleApiError ? err.message : String((err as Error).message)
      );
      setRes(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={PAGE_FRAME}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-sm font-medium text-foreground">
            Who can help with…
          </h1>
          <p className="text-[11px] text-muted-foreground">
            Stated skills, résumé evidence, and related work — each match shows
            its reasoning. Nothing here assigns anything.
          </p>
        </div>
      </header>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <Input
          inputSize="md"
          className="flex-1"
          placeholder="extruder firmware, Altium layout, German-speaking sales…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Button type="submit" size="md" loading={busy} icon="Search">
          Search
        </Button>
      </form>

      {/*
        Before the first search this page was blank below the box — the one
        surface that most justifies filling in a profile, showing nothing
        about what it can do. Three signals, named, because a ranking whose
        reasoning is hidden cannot be argued with (§5.5).
      */}
      {!res && !busy && !error && (
        <section className="rounded-xl border border-border p-4">
          <p className="text-sm text-foreground">
            Find who can help with a piece of work.
          </p>
          <ul className="mt-2 max-w-prose space-y-1 text-xs text-muted-foreground">
            <li>
              <span className="text-foreground">Stated skills</span> — weighted
              by level and how recently somebody used them.
            </li>
            <li>
              <span className="text-foreground">CV evidence</span> — the
              matching line from their newest résumé, quoted.
            </li>
            <li>
              <span className="text-foreground">Related work</span> — nearby
              experience, when semantic matching is available.
            </li>
          </ul>
          <p className="mt-3 max-w-prose text-xs text-muted-foreground">
            Every match shows which of the three it came from. Nothing here
            assigns anything — and a skill nobody wrote down cannot be found,
            so an empty result is also a prompt to fill in a profile.
          </p>
        </section>
      )}

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {res && !res.semantic_available && (
        <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <Icon name="Info" className="mt-0.5 size-3 shrink-0" />
          Semantic matching is off — results come from stated skills and CVs
          alone.
        </p>
      )}

      {res && res.total === 0 && (
        <p className="text-xs text-muted-foreground">
          Nobody&apos;s record mentions this. That can mean nobody can help — or
          that the skill exists and nobody wrote it down.
        </p>
      )}

      {res &&
        rankedRows(res).map((row) => (
          <article key={row.person_id} className="rounded-xl border border-border p-3">
            <div className="flex items-center gap-3">
              <Avatar name={row.name} avatar={row.avatar} className="size-8 text-xs" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Link
                    href={`/people?person=${row.person_id}`}
                    className="text-xs font-medium text-foreground hover:underline"
                  >
                    {row.name}
                  </Link>
                  {row.title && (
                    <span className="text-[11px] text-muted-foreground">
                      {row.title}
                    </span>
                  )}
                  {row.department && (
                    <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      {row.department}
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground">
                  {describeResultLoad(row)}
                  {row.timezone ? ` · ${row.timezone}` : ""}
                </p>
              </div>
              <span className="text-xs font-medium text-foreground">
                {row.score}
              </span>
              {/* WS-28e §6.4 — routes through the ordinary task-create flow
                  with the assignee pre-filled (visible, dismissible there).
                  Still no assign WRITE anywhere on this surface. */}
              {row.email ? (
                <Link
                  href={`/projects?assignee=${encodeURIComponent(row.email)}`}
                >
                  <Button variant="secondary" size="sm" icon="FolderKanban">
                    Assign to…
                  </Button>
                </Link>
              ) : null}
            </div>

            {/* The argument, line by line — the fact and its points. */}
            <ul className="mt-2 flex flex-col gap-0.5">
              {row.signals.map((signal, index) => (
                <li key={index} className="text-[11px] text-muted-foreground">
                  {describeSignal(signal)}
                </li>
              ))}
            </ul>

            {row.warnings.length > 0 && (
              <p className="mt-1.5 text-[11px] text-muted-foreground">
                <Icon
                  name="AlertTriangle"
                  className="mr-1 inline size-3 align-text-bottom"
                />
                {row.warnings.join(" · ")}
              </p>
            )}
          </article>
        ))}
    </main>
  );
}
