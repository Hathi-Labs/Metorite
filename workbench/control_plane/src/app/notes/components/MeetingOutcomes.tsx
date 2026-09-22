"use client";

/**
 * MeetingOutcomes — what came out of the meeting, above the tabs.
 *
 * The value of a finished meeting is its notes, its action items and its
 * follow-up. Actions were tab three, so "this produced four tasks and two still
 * need you" was invisible until you went looking. This puts the yield first.
 *
 * The agenda recap costs nothing — coverage was already computed live — and it
 * turns "what did we not get to" into the natural start of the next meeting
 * rather than something you have to remember.
 */

import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { getAgendaProgress } from "../lib/api";
import type { ActionItem, AgendaProgress, MeetingDetail } from "../lib/types";

interface Props {
  meeting: MeetingDetail;
  actions: ActionItem[];
  hasNotes: boolean;
  /** Jump to the Actions tab — the strip states the count, the tab does the work. */
  onOpenActions: () => void;
  onDraftFollowup: () => void;
}

export default function MeetingOutcomes({
  meeting,
  actions,
  hasNotes,
  onOpenActions,
  onDraftFollowup,
}: Props) {
  const [progress, setProgress] = useState<AgendaProgress | null>(null);

  useEffect(() => {
    getAgendaProgress(meeting.id)
      .then((p) => setProgress(p.total > 0 ? p : null))
      .catch(() => {});
  }, [meeting.id]);

  const pending = actions.filter((a) => a.status === "draft").length;
  const approved = actions.filter(
    (a) => a.status === "approved" || a.status === "created"
  ).length;

  const missed = progress?.items.filter((i) => !i.covered) ?? [];

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3">
        {/* Actions — the only card that can ask for something. */}
        <button
          onClick={onOpenActions}
          disabled={actions.length === 0}
          className={`tech-transition rounded-xl border border-l-[3px] bg-card p-3 text-left disabled:cursor-default ${
            pending > 0
              ? "border-border border-l-accent hover:bg-secondary/40"
              : "border-border border-l-success"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Actions
          </p>
          <p className="mt-1 text-base font-bold leading-none">
            {actions.length}
            {pending > 0 && (
              <span className="ml-1.5 text-[11px] font-semibold text-accent">
                · {pending} need approval
              </span>
            )}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {actions.length === 0
              ? "None found"
              : pending > 0
                ? "Approved ones become tasks in My Tasks"
                : `${approved} sent to My Tasks`}
          </p>
        </button>

        <div
          className={`rounded-xl border border-l-[3px] bg-card p-3 ${
            hasNotes ? "border-border border-l-success" : "border-border border-l-muted"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Notes
          </p>
          <p className="mt-1 text-base font-bold leading-none">
            {hasNotes ? "Ready" : "Not yet"}
          </p>
          <p className="mt-1 truncate text-[11px] text-muted-foreground">
            {meeting.template_key
              ? meeting.template_key.replace(/_/g, " ")
              : "Standard meeting"}
          </p>
        </div>

        <button
          onClick={onDraftFollowup}
          disabled={!hasNotes}
          className="tech-transition rounded-xl border border-l-[3px] border-border border-l-primary bg-card p-3 text-left hover:bg-secondary/40 disabled:cursor-default disabled:opacity-60"
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Follow-up
          </p>
          <p className="mt-1 text-base font-bold leading-none">
            {hasNotes ? "Draft email" : "—"}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {hasNotes
              ? meeting.attendees.length > 0
                ? `To ${meeting.attendees
                    .map((a) => a.name || a.email)
                    .slice(0, 2)
                    .join(", ")}`
                : "Add attendees to address it"
              : "Generate notes first"}
          </p>
        </button>
      </div>

      {/* Agenda recap — free, because coverage was computed during the call. */}
      {progress && (
        <div className="rounded-xl border border-border bg-card p-3">
          <p className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            <Icon name="ListChecks" className="h-3.5 w-3.5" /> Agenda — what you covered
            <span className="font-mono normal-case tracking-normal opacity-70">
              {progress.covered_count} of {progress.total}
            </span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {progress.items.map((it, i) => (
              <span
                key={i}
                className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                  it.covered
                    ? "border-success/30 bg-success/10 text-success"
                    : "border-warning/30 bg-warning/10 text-warning"
                }`}
              >
                {it.covered ? (
                  <Icon name="Check" className="h-3 w-3" />
                ) : (
                  <Icon name="Circle" className="h-3 w-3" />
                )}
                {it.title}
              </span>
            ))}
          </div>
          {missed.length > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {missed.length === 1
                ? "One item was never reached"
                : `${missed.length} items were never reached`}{" "}
              — worth carrying into the next one.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
