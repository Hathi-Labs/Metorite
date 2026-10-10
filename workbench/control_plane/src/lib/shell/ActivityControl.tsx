"use client";

/**
 * The activity control and its panel: every live assistant run, across apps
 * (WS-51 S3, `project-docs/specs/chat_run_continuity.md` §4 S3).
 *
 * **The shell owns it** (rule 10 of `AGENTS.md`), and it works with the shell
 * bar flag ON or OFF. It is ONE control, `ActivityControl`, drawn in three
 * places:
 *
 *   - the shell bar's right end, after the app's tools (`ShellBar.tsx`), when
 *     `NEXT_PUBLIC_SHELL_BAR` is on;
 *   - the sidebar's head, beside the fold control (`Sidebar.tsx`), when it is
 *     off, because that layout has no top bar;
 *   - the phone's Menu drawer, in both cases (`AppShell.tsx`).
 *
 * `ActivityHost` mounts the one panel. `AppShell` renders it in every layout,
 * so no flag decides whether the panel exists.
 *
 * **One source.** The count and the rows read the S1 store
 * (`lib/liveRuns.ts`) and this tab's own runs, the same two sources as every
 * nav badge. Nothing here polls. The open panel asks the store for each run's
 * step (`wantLiveSteps`), so the server reads a stream only while somebody
 * looks at the panel.
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
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import NavBadge from "@/components/NavBadge";
import Button from "@/components/ui/Button";
import Modal, { type ModalPlacement } from "@/components/ui/Modal";
import { useActivityRows, useRunActivity } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { onClear } from "@/lib/dataCache";
import { wantLiveSteps } from "@/lib/liveRuns";
import { visibleSections, type NavSection } from "@/lib/nav";
import {
  agentLabel,
  chatLink,
  paneForAgent,
  runBadge,
  runStatusText,
  type ActivityRow,
} from "@/lib/runActivity";

// ── Whether the panel is up ──────────────────────────────────────────────────
//
// One small store, so any control in any layout opens the one panel, and every
// control can say whether it is open. Memory only.

let _open = false;
const _openListeners = new Set<() => void>();

function _setOpen(next: boolean): void {
  if (_open === next) return;
  _open = next;
  _openListeners.forEach((l) => l());
}

/** Open the activity panel. */
export function openActivity(): void {
  _setOpen(true);
}

/** Close the activity panel. */
export function closeActivity(): void {
  _setOpen(false);
}

function subscribeOpen(listener: () => void): () => void {
  _openListeners.add(listener);
  return () => {
    _openListeners.delete(listener);
  };
}

const getOpen = () => _open;
const getServerOpen = () => false;

/** True while the panel is up. */
export function useActivityOpen(): boolean {
  return useSyncExternalStore(subscribeOpen, getOpen, getServerOpen);
}

// A new member on this browser starts with the panel closed.
onClear(() => _setOpen(false));

// ── The control ──────────────────────────────────────────────────────────────

/** The control's spoken name: what it is, then what it counts. */
export function activityControlLabel(running: number, needsInput: number, unread = 0): string {
  const badge = runBadge(running, needsInput, unread);
  return badge ? `Assistants: ${badge.label}` : "Assistants: none running";
}

/**
 * The control's look: an agent icon, with the run badge. Pure, so the tests
 * read its markup. `ActivityControl` feeds it.
 */
export function ActivityButton({
  running,
  needsInput,
  unread = 0,
  open,
  onOpen,
  className = "",
}: {
  running: number;
  needsInput: number;
  /** Chats with a reply the member has not read (WS-51 S5). */
  unread?: number;
  /** The panel is up. */
  open: boolean;
  onOpen: () => void;
  /** Layout only. */
  className?: string;
}) {
  const badge = runBadge(running, needsInput, unread);
  const label = activityControlLabel(running, needsInput, unread);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      icon="Bot"
      data-activity-control={badge ? badge.tone : "quiet"}
      aria-label={label}
      title={label}
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

/**
 * THE activity control, live. It counts the runs across apps from the S1
 * store, and it opens the one panel. It draws nothing for a person with no
 * workspace, who has no runs to show.
 */
export function ActivityControl({
  onBeforeOpen,
  className = "",
}: {
  /** Runs first: the phone drawer closes itself before the sheet rises. */
  onBeforeOpen?: () => void;
  /** Layout only. */
  className?: string;
}) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const { total, needsTotal, unreadTotal } = useRunActivity(null, workspace);
  const open = useActivityOpen();
  if (!workspace) return null;
  return (
    <ActivityButton
      running={total}
      needsInput={needsTotal}
      unread={unreadTotal}
      open={open}
      onOpen={() => {
        onBeforeOpen?.();
        openActivity();
      }}
      className={className}
    />
  );
}

// ── The panel ────────────────────────────────────────────────────────────────

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
        const reply = row.state === "new_reply";
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
                      needs ? "bg-warning" : reply ? "bg-info" : "bg-success motion-safe:animate-pulse"
                    }`}
                  />
                  <span className={`truncate ${needs || reply ? "font-medium text-foreground" : ""}`}>{status}</span>
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
 * every dismissal, Escape among them, calls `onClose`. While it is open it
 * asks the store for each run's step.
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
  /** `end` under the shell bar's right end, `top` beside a sidebar, `sheet` on a phone. */
  placement?: ModalPlacement;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    // The steps cost a stream read per run, so they are asked for only now.
    const release = wantLiveSteps();
    // At once on open, then every tick: the panel may have mounted hours ago.
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, TICK_MS);
    return () => {
      release();
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

/**
 * The panes the member holds, as a stable set. A chat link to any other pane
 * opens in Chat (`chatLink`). The panel, the toast and the phone pill share it.
 */
export function useHeldHrefs(): ReadonlySet<string> {
  const { access, loading } = useAccess();
  const heldKey = useMemo(
    () =>
      visibleSections(loading ? null : access.features, access.is_admin)
        .flatMap((s) => s.items.map((p) => p.href))
        .join(","),
    [loading, access.features, access.is_admin],
  );
  return useMemo(() => new Set(heldKey ? heldKey.split(",") : []), [heldKey]);
}

/**
 * The one panel, for every layout. `AppShell` renders it on the desktop and
 * on the phone, with the shell bar flag on or off.
 */
export function ActivityHost({ placement }: { placement: ModalPlacement }) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const rows = useActivityRows(workspace);
  const open = useActivityOpen();
  const heldHrefs = useHeldHrefs();
  if (!workspace) return null;
  return (
    <ActivityPanel
      open={open}
      onClose={closeActivity}
      rows={rows}
      visibleHrefs={heldHrefs}
      placement={placement}
    />
  );
}
