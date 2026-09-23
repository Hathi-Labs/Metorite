"use client";

/**
 * Projects · the task detail panel — the HOST of the shared task body.
 *
 * ## Composition (S5, then S6e)
 *
 * The standing ruling on the two task surfaces is "Projects is canonical,
 * Tasks conforms". This surface was the exception in S5 — its grammar was
 * taken from `/tasks`' `ItemDetail` (a header carrying the chip row and the
 * title, a grouped details block of bordered field cells, then discrete
 * labelled sections), because that was the designed one.
 *
 * **S6e (my_tasks_cutover.md §4.8 point 3) went one step further: the field
 * blocks are now ONE component, `TaskBody`, and this file is a host.** It
 * draws the header — the task ref, the copy-link, the width stops, the
 * watch toggle, the close, the title and the chip row — and everything
 * under the header is `TaskBody`: status, priority, assignees, due date,
 * description, tags, custom fields, links and subtasks, files, and the
 * discussion with its composer. `ItemDetail` in My Tasks hosts the same
 * body under its own header and its overlay strip, so a member meets the
 * same fields in the same order in both apps. Neither host writes a field
 * block of its own.
 *
 * Everything Projects has and Tasks does not is IN the body, so both hosts
 * have it: the ref, tags, relations and links, watchers, recurrence, custom
 * fields, attachments, the activity timeline and comments. Nothing
 * Tasks-only is here either — context, energy and the founder priority
 * matrix live on `pm_task_personal` and are My Tasks' strip. The one
 * exception is the VIEWER's own disposition chip (§4.8 point 4): under the
 * lens the header says what I filed this task as in My Tasks, and nothing
 * about anybody else's overlay, because the route it reads resolves the
 * caller from the session.
 *
 * ⚠️ **The SURFACE decides the column count, never the viewport.** `mode`
 * is this panel's OWN width stop: at `full` (max-w-3xl, 768px) the body's
 * sections pair up, and at Peek (320px) and Side (448px) they do not.
 * `/tasks`' detail takes the same decision through its `focused` flag.
 *
 * ## Peek → side → full (WS-27ab item 1)
 *
 * The width is a per-user preference with three stops, owned by
 * `lib/panelMode.ts` — this file renders the switch and asks the page to
 * store the choice. Only the class on the `<aside>` and, at `full`, where
 * the page mounts it, differ between the three.
 *
 * **Escape gives the keyboard back.** Closing returns focus to whatever was
 * focused when the panel opened — on the board and the list that is the
 * `tabIndex={0}` canvas holding WS-27y's cursor.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { StatusChip } from "@/components/StatusChip";
import { useEffect, useState } from "react";

import { lensEnabled, lensMyOverlay } from "@/app/tasks/lib/lens";
import {
  type FieldRow,
  type StatusRow,
  type TagRow,
  type TaskRow,
  watchersApi,
} from "../lib/api";
import { taskDeepLink, taskRef } from "../lib/card";
import {
  PANEL_MODE_HINTS,
  PANEL_WIDTH_CLASS,
  type PanelMode,
  panelEscape,
} from "../lib/panelMode";
import { TaskBody, resolveStatus } from "./TaskBody";

interface Props {
  task: TaskRow;
  statuses: StatusRow[];
  onClose: () => void;
  onChanged: (task: TaskRow) => void;
  /**
   * Fired when this panel adds a row the surrounding list does not know about.
   * `onChanged` merges one task; a new subtask is a task the board has never
   * seen, so it needs a real reload rather than a merge.
   */
  onTaskAdded?: () => void;
  /**
   * WS-27l — the project's custom field definitions. Passed in rather than
   * fetched here: they belong to the root project, the page already holds them
   * for the selected node, and re-fetching per panel open would be a request
   * per click for data that does not change between clicks.
   */
  fields?: FieldRow[];
  /** WS-27m — the project's registered tags, for the picker's suggestions. */
  tags?: TagRow[];
  /**
   * WS-27p — open another task by id, for a subtask or a linked task. The page
   * owns it because opening one has to resolve ITS project's statuses, which is
   * a decision the panel does not have the tree to make.
   */
  onOpenTask?: (taskId: string) => void;
  /**
   * WS-27ab — how much room the panel takes. The page owns it because the page
   * decides where a `full` panel is MOUNTED (over the board rather than docked
   * beside it) and because the choice is persisted per user, which is a page
   * concern, not a per-task one.
   */
  mode?: PanelMode;
  /**
   * Absent means "no escalation control here" — the phone branch, where the
   * panel already IS the screen and three width buttons would be a lie.
   */
  onMode?: (next: PanelMode) => void;
  /** See TaskBoard's prop of the same name. */
  personLabels?: ReadonlyMap<string, string>;
  /**
   * Addresses this panel has seen that the board may not have.
   *
   * A comment's author is very often not an assignee, and `personLabels` is
   * built from assignees. Without this the byline over somebody's own words
   * is the local part of their email.
   */
  onPeopleSeen?: (who: string[]) => void;
}

/** The viewer's own overlay on this task, or null when they hold none. */
type MyOverlay = Awaited<ReturnType<typeof lensMyOverlay>>;

export function TaskPanel({
  task,
  statuses,
  onClose,
  onChanged,
  onTaskAdded,
  fields = [],
  tags = [],
  onOpenTask,
  mode = "side",
  onMode,
  personLabels,
  onPeopleSeen,
}: Props) {
  /**
   * Two columns, decided by the SURFACE rather than the viewport. `full` is
   * `max-w-3xl` (768px), the first stop with room for two readable columns.
   */
  const twoColumn = mode === "full";

  // WS-27w item 6 — the copy-deep-link affordance's "it worked" flash.
  const [copied, setCopied] = useState(false);
  // WS-27v — null until the read lands, so the toggle never renders a state
  // it is only guessing at.
  const [watching, setWatching] = useState<boolean | null>(null);
  // S6e — the way back (§4.8 point 4): what I filed this task as in My
  // Tasks. Null with the lens off, and null when I hold no overlay row.
  const [mine, setMine] = useState<MyOverlay>(null);
  const { current: currentStatus, tone: statusTone } = resolveStatus(task, statuses);

  /**
   * WS-27ab — hand the keyboard back on close.
   *
   * Captured on MOUNT, not per task: opening a linked task from inside the
   * panel must still return to the row on the board that started the trail.
   * `document.contains` is the guard that matters: the board reloads while a
   * panel is open, and focusing a detached node silently moves focus to
   * `<body>`, which is the state this exists to avoid.
   */
  useEffect(() => {
    const opener = typeof document === "undefined" ? null : document.activeElement;
    return () => {
      if (
        opener instanceof HTMLElement &&
        opener !== document.body &&
        document.contains(opener)
      )
        opener.focus();
    };
  }, []);

  useEffect(() => {
    let live = true;
    // The rule below wants these derived from `task.id` during render. They
    // cannot be: both are also written by the toggle and by the reads.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWatching(null);
    setMine(null);
    watchersApi
      .get(task.id)
      .then((res) => {
        if (live) setWatching(res.watching);
      })
      // No toggle beats a blank panel.
      .catch(() => undefined);
    if (lensEnabled()) {
      void lensMyOverlay(task.id).then((got) => {
        if (live) setMine(got);
      });
    }
    return () => {
      live = false;
    };
  }, [task.id]);

  async function toggleWatch() {
    if (watching === null) return;
    // Optimistic — both writes are idempotent, so a failure just reverts.
    const next = !watching;
    setWatching(next);
    try {
      await (next ? watchersApi.watch(task.id) : watchersApi.unwatch(task.id));
    } catch {
      setWatching(!next);
    }
  }

  /**
   * Copy a URL that reopens this panel — `/projects?task=<id>`, the deep-link
   * shape the page already reads (WS-28b). The icon flips to a check briefly,
   * because a copy with no acknowledgement gets clicked three times.
   */
  async function copyDeepLink() {
    try {
      await navigator.clipboard.writeText(
        taskDeepLink(task, window.location.origin),
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be unavailable (permissions, insecure context). The
      // link is not lost — opening the task by deep link shows it in the
      // address bar — so this fails quietly.
    }
  }

  return (
    // `bg-background` under a `bg-card` header, which is ItemDetail's
    // arrangement and the reason the field cells below read as cards at all.
    // ⚠️ The root must stay an `<aside>`: `projects/page.tsx`'s phone branch
    // lifts the width cap with `[&>aside]:max-w-none`, and the `full` overlay
    // rounds its corners the same way.
    <aside
      className={`flex h-full w-full ${PANEL_WIDTH_CLASS[mode]} flex-col border-l border-border bg-background`}
      // Escape belongs to the panel, not to the window: a global listener
      // would also fire for the search palette and the four dialogs that can
      // be open over it, closing two things with one key.
      onKeyDown={(event) => {
        const target = event.target as HTMLElement | null;
        const action = panelEscape(event, target);
        if (!action) return;
        event.stopPropagation();
        if (action === "blur") target?.blur();
        else onClose();
      }}
    >
      <header className="shrink-0 border-b border-border bg-card px-3 py-3">
        <div className="mb-1.5 flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-0.5">
            <p className="truncate text-xs text-muted-foreground">
              {taskRef(task) ?? "Task"}
            </p>
            <Button
              variant="ghost"
              size="icon-xs"
              icon={copied ? "Check" : "Link"}
              aria-label="Copy a link to this task"
              title="Copy a link that opens this task"
              onClick={() => void copyDeepLink()}
            />
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            {/* ONE toggle: the side panel, or the same panel as a full card.
                ⚠️ This was a three-stop segmented control (peek · side · full)
                until 2026-09-23. The owner cut it: *"a sidebar which can also
                open as a full card. The switcher where we change the width of
                the sidebar is not needed."* `/tasks` answers the same need
                with the same one control, and now so does this.
                Hidden entirely where the page did not pass `onMode` (the
                phone, where the panel is always the whole screen). */}
            {onMode ? (
              <Button
                variant="ghost"
                size="icon-xs"
                className="mr-1"
                icon={mode === "full" ? "Minimize2" : "Maximize2"}
                aria-label={
                  mode === "full" ? "Back to the side panel" : "Open as a full card"
                }
                aria-pressed={mode === "full"}
                title={PANEL_MODE_HINTS[mode]}
                onClick={() => onMode(mode === "full" ? "side" : "full")}
              />
            ) : null}
            {/* WS-27v — watch/unwatch. Hidden (not disabled) until the state
                is known. */}
            {watching !== null ? (
              <Button
                variant="ghost"
                size="icon-sm"
                icon={watching ? "BellOff" : "Bell"}
                aria-label={watching ? "Stop watching this task" : "Watch this task"}
                title={
                  watching
                    ? "Watching — click to stop being notified about this task"
                    : "Watch — get notified about this task"
                }
                onClick={() => void toggleWatch()}
              />
            ) : null}
            <Button
              variant="ghost"
              size="icon-sm"
              icon="X"
              aria-label="Close task"
              title="Close this task"
              onClick={onClose}
            />
          </div>
        </div>
        {/* Wraps rather than truncates: the title is the one thing on the
            panel nobody can afford to have cut off. */}
        <h2 className="text-base font-semibold leading-snug text-foreground">
          {task.title}
        </h2>
        {/* The chip row. The status is drawn through the shared `StatusChip`,
            in the accent `accentForStatus` resolves; the Details block in the
            body carries the CONTROL that changes it. */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {currentStatus ? (
            <StatusChip accent={statusTone} label={currentStatus.name} />
          ) : null}
          {task.completed_at ? (
            <Badge tone="success" icon="Check">
              Completed
            </Badge>
          ) : null}
          {/* S6e — the viewer's OWN disposition in My Tasks, when they hold
              one. Never anybody else's: `/my/tasks/{id}` answers for the
              session's caller only. */}
          {mine ? (
            <Badge
              tone="neutral"
              icon="UserRound"
              title="How you filed this task in My Tasks"
            >
              {mine.isTriaged ? mine.disposition : "Untriaged"}
              {mine.context ? ` · ${mine.context}` : ""}
            </Badge>
          ) : null}
        </div>
      </header>

      <TaskBody
        task={task}
        statuses={statuses}
        onChanged={onChanged}
        onTaskAdded={onTaskAdded}
        fields={fields}
        tags={tags}
        onOpenTask={onOpenTask}
        personLabels={personLabels}
        onPeopleSeen={onPeopleSeen}
        twoColumn={twoColumn}
      />
    </aside>
  );
}
