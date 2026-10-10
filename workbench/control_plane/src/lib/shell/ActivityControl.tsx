"use client";

/**
 * The activity control and its panel: every live assistant run, across apps
 * (WS-51 S3, `project-docs/specs/chat_run_continuity.md` §4 S3).
 *
 * **The shell owns it** (rule 10 of `AGENTS.md`). The shell bar draws the
 * control at its right end, after the app's tools, on every desktop page. The
 * phone draws no shell row, so its Menu drawer opens the same panel
 * (`OPEN_ACTIVITY`). `ShellFrame` mounts the one panel for both.
 *
 * **One source.** The control's count and the panel's rows read the S1 store
 * (`lib/liveRuns.ts`) and this tab's own runs, the same two sources as every
 * nav badge. Nothing here polls, and nothing here reads a run that
 * `/chat/active-sessions` did not list for this member and org.
 *
 * **One colour rule.** The control wears `NavBadge` with `runBadge`: green
 * counts the running runs, and amber, which wins, counts the runs that wait on
 * the member. When nothing runs, it is quiet: the icon alone.
 *
 * **The panel** is a `Modal`: focus moves in, Tab stays in it, Escape and an
 * outside press close it, and focus returns to the control. A row opens its
 * chat with the shell job `open-chat` (`chatLink` in `lib/runActivity.ts`).
 *
 * Fence (R7): `activityControl.test.ts`.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import NavBadge from "@/components/NavBadge";
import Button from "@/components/ui/Button";
import Modal, { type ModalPlacement } from "@/components/ui/Modal";
import type { NavSection } from "@/lib/nav";
import {
  agentLabel,
  chatLink,
  paneForAgent,
  runBadge,
  runStatusText,
  type ActivityRow,
} from "@/lib/runActivity";

/** The control's spoken name: what it is, then what it counts. */
export function activityControlLabel(running: number, needsInput: number): string {
  const badge = runBadge(running, needsInput);
  return badge ? `Assistants: ${badge.label}` : "Assistants: none running";
}

/**
 * The control: an agent icon, with the run badge. `running` and `needsInput`
 * are the totals across apps, from `useRunActivity`.
 */
export function ActivityControl({
  running,
  needsInput,
  open,
  onOpen,
  className = "",
}: {
  running: number;
  needsInput: number;
  /** The panel is up. */
  open: boolean;
  onOpen: () => void;
  /** Layout only. */
  className?: string;
}) {
  const badge = runBadge(running, needsInput);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      icon="Bot"
      data-activity-control={badge ? badge.tone : "quiet"}
      aria-label={activityControlLabel(running, needsInput)}
      title={activityControlLabel(running, needsInput)}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={onOpen}
      className={`relative ${className}`}
    >
      {badge ? (
        <NavBadge
          count={badge.count}
          tone={badge.tone}
          label={badge.label}
          placement="corner"
          className="pointer-events-none"
        />
      ) : null}
    </Button>
  );
}

/** The empty state's words. */
export const NO_RUNS_TEXT = "No assistants are running.";

/**
 * The rows, newest first (`activityRows` orders them). Pure: it renders the
 * same markup for the same input, so the tests read it as a string.
 */
export function ActivityList({
  rows,
  now,
  visibleHrefs,
  sections,
  onNavigate,
}: {
  rows: readonly ActivityRow[];
  now: number;
  /** The panes the member holds. A run on any other opens in Chat. */
  visibleHrefs: ReadonlySet<string> | null;
  sections?: readonly NavSection[];
  onNavigate?: () => void;
}) {
  if (rows.length === 0) {
    return (
      <p data-activity-empty className="px-4 py-6 text-center text-sm text-muted-foreground">
        {NO_RUNS_TEXT}
      </p>
    );
  }
  return (
    <ul aria-label="Live assistants" className="flex flex-col py-1">
      {rows.map((row) => {
        const pane = paneForAgent(row.agentName, sections);
        const agent = agentLabel(row.agentName);
        const needs = row.state === "needs_input";
        const status = runStatusText(row, now);
        const title = row.title?.trim() || "Untitled chat";
        return (
          <li key={row.threadId}>
            <Link
              href={chatLink(row.agentName, row.threadId, visibleHrefs, sections)}
              onClick={onNavigate}
              data-activity-row={row.state}
              aria-label={`${agent}${pane ? ` in ${pane.label}` : ""}: ${title}. ${status}`}
              className="flex items-start gap-3 px-3 py-2.5 tech-transition hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
            >
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                <Icon name={pane?.icon ?? "Bot"} size={15} />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[11px] text-muted-foreground">
                  {agent}
                  {pane ? ` · ${pane.label}` : ""}
                </span>
                <span className="truncate text-sm font-medium text-foreground">{title}</span>
                <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    aria-hidden
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      needs ? "bg-warning" : "bg-success motion-safe:animate-pulse"
                    }`}
                  />
                  <span className={`truncate ${needs ? "font-medium text-foreground" : ""}`}>{status}</span>
                </span>
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** How often the panel's relative times move while it is open. */
const TICK_MS = 30_000;

/**
 * The panel: a `Modal` around `ActivityList`. Controlled: `open` comes in and
 * every dismissal, Escape among them, calls `onClose`.
 */
export function ActivityPanel({
  open,
  onClose,
  rows,
  visibleHrefs,
  placement = "end",
}: {
  open: boolean;
  onClose: () => void;
  rows: readonly ActivityRow[];
  visibleHrefs: ReadonlySet<string> | null;
  /** `end` under the shell bar's right end, `sheet` on a phone. */
  placement?: ModalPlacement;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    // At once on open, then every tick: the panel may have mounted hours ago.
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, TICK_MS);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Assistants"
      icon="Bot"
      size="sm"
      placement={placement}
      closeLabel="Close the assistants list"
      className="max-h-[70vh]"
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ActivityList rows={rows} now={now} visibleHrefs={visibleHrefs} onNavigate={onClose} />
      </div>
    </Modal>
  );
}
