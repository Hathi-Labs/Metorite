"use client";

/**
 * Projects · the task detail panel and its timeline.
 *
 * Comments and system events come from ONE endpoint because they are one table
 * (§3.8) — the timeline shows a status change, an assignment, an agent run and
 * a comment in the same stream, which is the point of the shared spine.
 *
 * ## Composition (S5) — this panel follows `/tasks`' `ItemDetail`
 *
 * The standing ruling on the two task surfaces is "Projects is canonical, Tasks
 * conforms". **This surface is the exception, and the direction is reversed**
 * (owner-reported from screenshots of the deployed app, comparing the two
 * detail panels: *"Task cards seem to be very different"*). `ItemDetail` is a
 * designed surface — a header carrying the chip row and the title, a grouped
 * details block of bordered field cells, then discrete labelled sections. This
 * was a plain vertical form: a bare `<select>` for status, a raw file input
 * rendering the browser's own *"Choose Files / No file chosen"*, and flat
 * labels stacked with no grouping. Neither of those controls expresses
 * Material's pill buttons or Graphite's uppercase labels, which is AGENTS.md
 * rule 3 and is now fenced by conformance rule 7.
 *
 * So the grammar here is `ItemDetail`'s: `SectionLabel` for a section,
 * `FieldCell` for a labelled cell (the chrome its `MetaEdit` draws closed).
 * What is NOT copied is the interaction — Projects' controls are always
 * visible rather than click-to-edit, because changing that is an interaction
 * change wearing a composition change's clothes.
 *
 * **Everything Projects has and Tasks does not stays**: the task ref, tags,
 * relations and links, watchers, recurrence, custom fields, attachments, the
 * activity timeline and comments. Nothing Tasks-only is imported either —
 * context, energy and the founder priority matrix live on `pm_task_personal`
 * and are not this surface's data.
 *
 * ⚠️ **The SURFACE decides the column count, never the viewport.** A
 * `sm:grid-cols-2` keys off the WINDOW, so it would split the 448px docked
 * column on a 4K monitor — exactly the collision `/tasks`' detail hit when it
 * was docked at 380px.
 *
 * That rule stands; what changed on 2026-09-19 is that it now has a second
 * answer rather than only "one column, always". `twoColumn` reads the panel's
 * OWN width stop: at `full` (max-w-3xl, 768px) the sections pair up, and at
 * Peek (320px) and Side (448px) they do not. `/tasks`' detail takes the same
 * decision through its `focused` flag, so the two apps split at the same kind
 * of moment rather than at the same number of pixels.
 *
 * ⚠️ **The sections do not MOVE between the two shapes.** An earlier attempt
 * lifted them into a main/aside split, which meant writing every section
 * twice — one per branch — and the next edit landed in one copy only. They
 * pair inside the containers they already live in, so there is no second copy
 * to drift. Prose and the timeline span both columns; a paragraph set in half
 * a panel is one nobody finishes.
 *
 * ## Peek → side → full (WS-27ab item 1)
 *
 * The width is now a per-user preference with three stops, owned by
 * `lib/panelMode.ts` — this file renders the switch and asks the page to store
 * the choice. Only the class on the `<aside>` and, at `full`, where the page
 * mounts it, differ between the three: one panel, three widths, never a second
 * "expanded task" component to keep in step.
 *
 * **Escape gives the keyboard back.** Closing returns focus to whatever was
 * focused when the panel opened — on the board and the list that is the
 * `tabIndex={0}` canvas holding WS-27y's cursor, so Esc lands the caret back on
 * the row it came from and `j`/arrows keep working. Without it, Escape left
 * focus on `<body>` and the next arrow key scrolled the window.
 */
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input, Select, Textarea } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { StatusChip } from "@/components/StatusChip";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { useEffect, useRef, useState } from "react";

import { accentForStatus } from "../lib/accent";
import {
  type ActivityRow,
  type AttachmentRow,
  type FieldRow,
  type StatusRow,
  type TagRow,
  type TaskRow,
  attachmentsApi,
  projectsApi,
  watchersApi,
} from "../lib/api";
import { taskDeepLink, taskRef } from "../lib/card";
import {
  type FoldableSection,
  readFolded,
  toggleFold,
  writeFolded,
} from "../lib/sectionFold";
import { previewKind, readableSize } from "../lib/preview";
import { isAutomated } from "../lib/lifecycle";
import { AssigneePicker } from "./AssigneePicker";
import { CustomFieldValues } from "./CustomFieldValues";
import { TagPicker } from "./TagPicker";
import { RepeatEditor } from "./RepeatEditor";
import { RelationsBlock } from "./RelationsBlock";
import { AttachmentViewer } from "./AttachmentViewer";
import { changeLabel } from "../lib/customFields";
import {
  assigneeLabel,
  classify,
  parseAssignees,
  withAssignee,
  withoutAssignee,
} from "../lib/assignees";
import { insertMention, notDeliveredNotice } from "../lib/notifications";
import {
  PANEL_MODES,
  PANEL_MODE_HINTS,
  PANEL_MODE_ICONS,
  PANEL_MODE_LABELS,
  PANEL_WIDTH_CLASS,
  type PanelMode,
  panelEscape,
} from "../lib/panelMode";

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
}

function describe(activity: ActivityRow, defs: FieldRow[] = []): string {
  const meta = (activity.meta ?? {}) as Record<string, unknown>;
  switch (activity.type) {
    case "comment":
      return activity.body ?? "";
    case "status_change":
      return activity.body ?? "Status changed";
    case "assignment": {
      const added = (meta.added as string[] | undefined) ?? [];
      const removed = (meta.removed as string[] | undefined) ?? [];
      const parts: string[] = [];
      if (added.length) parts.push(`assigned ${added.join(", ")}`);
      if (removed.length) parts.push(`unassigned ${removed.join(", ")}`);
      return parts.join("; ") || "Assignment changed";
    }
    case "field_change": {
      const changes = (meta.changes as Array<{ field: string }> | undefined) ?? [];
      // `patch_task` files a custom edit as `custom.<key>`. Rendering that raw
      // would put a database key in front of somebody reading their own
      // history, so the definition's label is used where one is loaded.
      const named = changes.map((c) => changeLabel(c.field, defs as never));
      return `Edited ${named.join(", ") || "fields"}`;
    }
    case "agent_run":
      return `Agent run ${String(meta.agent ?? "")}`.trim();
    case "sync":
      return activity.body ?? "Synced";
    case "attachment":
      return activity.body ?? "Attachment changed";
    default:
      return activity.body ?? activity.type;
  }
}

/**
 * A section heading — `ItemDetail`'s grammar, re-derived from that file
 * (`text-[11px] font-semibold uppercase tracking-wide text-muted-foreground`,
 * optional leading glyph).
 *
 * ⚠️ It is a deliberate second copy, and it should not stay one. The shared
 * home for it is `src/components/`, promoted together with `ItemDetail`'s — and
 * that edit touches `app/tasks
/**
 * One labelled cell in the details block — the chrome `ItemDetail`'s `MetaEdit`
 * draws in its closed state, without the click-to-edit flip: this panel's
 * controls are live, and hiding them behind a click would be an interaction
 * change, not a composition one.
 *
 * `trailing` is the right-hand slot of the label row (an accent dot, a count).
 */
/**
 * The container class for a group of sections that may pair up.
 *
 * One helper rather than the same ternary written at each container, so the
 * two groups cannot drift into different gaps or different breakpoints.
 *
 * `items-start` matters: without it the grid stretches both cells to the
 * taller one, and a short Details block grows a field of empty card.
 */
const PAIRED_SECTIONS = (twoColumn: boolean): string =>
  twoColumn
    ? "grid grid-cols-2 items-start gap-4 px-3 py-3"
    : "flex flex-col gap-4 px-3 py-3";


function FieldCell({
  label,
  icon,
  trailing,
  children,
}: {
  label: string;
  icon: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          <Icon name={icon} className="h-3 w-3" />
          {label}
        </span>
        {trailing}
      </div>
      <div className="mt-1.5 text-sm text-foreground">{children}</div>
    </div>
  );
}

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
}: Props) {
  // WS-27ak(3) — the confirmation channel. `changeStatus` below is the one
  // mutation on this panel wired to it in that slice.
  const toast = useToast();
  /**
   * Two columns, decided by the SURFACE rather than the viewport.
   *
   * `full` is `max-w-3xl` (768px), the first stop with room for two readable
   * columns. `/tasks`' detail takes the same decision through its `focused`
   * flag, so the two apps split at the same kind of moment rather than at
   * the same number of pixels.
   */
  const twoColumn = mode === "full";

  //: The attachment whose viewer is open. One at a time — two open previews
  //: is two things asking for the same attention.
  const [viewing, setViewing] = useState<AttachmentRow | null>(null);

  /**
   * Which sections this member has folded away.
   *
   * ⚠️ Read in an EFFECT, not in a lazy initialiser. `localStorage` does not
   * exist while this renders on the server, and a first paint that disagreed
   * with the second is a hydration mismatch — the same reason `panelMode` is
   * read the way it is, four fields below.
   */
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    // Same suppression and the same reason as `panelMode` on the page: the
    // read must happen after mount or the server's paint disagrees with the
    // client's, and that is a hydration mismatch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFolded(readFolded());
  }, []);

  function setSectionOpen(section: FoldableSection, open: boolean) {
    setFolded((current) => {
      const next = toggleFold(current, section, open);
      writeFolded(next);
      return next;
    });
  }

  /** The props every foldable section shares. */
  const fold = (section: FoldableSection) => ({
    open: !folded.has(section),
    onOpenChange: (open: boolean) => setSectionOpen(section, open),
  });

  const [timeline, setTimeline] = useState<ActivityRow[]>([]);
  const [comment, setComment] = useState("");
  const [notDelivered, setNotDelivered] = useState<string | null>(null);
  const commentBox = useRef<HTMLTextAreaElement | null>(null);
  const [assignee, setAssignee] = useState("");
  const [subtask, setSubtask] = useState("");
  // Bumped when this panel adds a subtask, so the relations block re-reads
  // rather than showing a list that is one item short.
  const [relationsKey, setRelationsKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // WS-27w item 6 — the copy-deep-link affordance's "it worked" flash.
  const [copied, setCopied] = useState(false);
  const [files, setFiles] = useState<AttachmentRow[]>([]);
  // S5 — the names of the files currently going up. The picker is a <Button>
  // now, so the browser's "3 files selected" text went with the raw control;
  // this replaces it, and says something truer — what is IN FLIGHT, rather than
  // what was last highlighted in a dialog.
  const [uploading, setUploading] = useState<string[]>([]);
  const filePicker = useRef<HTMLInputElement | null>(null);
  // WS-27v — null until the read lands, so the toggle never renders a state
  // it is only guessing at.
  const [watching, setWatching] = useState<boolean | null>(null);
  const assignees = task.assignees ?? [];
  // Agents are excluded: an agent cannot receive a notification (migration
  // 152's CHECK), so offering to mention one would promise nothing.
  const mentionable = assignees.filter((who) => !who.startsWith("agent:"));
  // The status this task is on, and its accent. `statusAccent`'s positional
  // fallback wants the lane's index, so both come from one lookup; a status_id
  // with no matching row (the panel opened before its project's statuses
  // resolved) draws no chip rather than a wrong one.
  const statusIndex = statuses.findIndex((s) => s.id === task.status_id);
  const currentStatus = statusIndex >= 0 ? statuses[statusIndex] : undefined;
  const statusTone = accentForStatus(
    currentStatus,
    Math.max(statusIndex, 0),
    statuses.length,
  );

  /**
   * WS-27ab — hand the keyboard back on close.
   *
   * Captured on MOUNT, not per task: opening a linked task from inside the
   * panel must still return to the row on the board that started the trail,
   * not to the link that was clicked half a panel ago. The cleanup runs on
   * every close — the ✕, Escape, and the page dropping the panel for any other
   * reason — so there is one path rather than three.
   *
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
    projectsApi
      .timeline(task.id)
      .then((res) => {
        if (live) setTimeline(res.rows);
      })
      .catch((err) => live && setError(String(err.message ?? err)));
    attachmentsApi
      .list(task.id)
      .then((res) => {
        if (live) setFiles(res.rows);
      })
      // Attachments failing must not blank the panel: the timeline and the
      // status control are the reason somebody opened it.
      .catch(() => undefined);
    setWatching(null);
    watchersApi
      .get(task.id)
      .then((res) => {
        if (live) setWatching(res.watching);
      })
      // Same posture as attachments: no toggle beats a blank panel.
      .catch(() => undefined);
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

  async function uploadFiles(picked: FileList | null) {
    if (!picked || picked.length === 0) return;
    setBusy(true);
    setError(null);
    setUploading(Array.from(picked).map((f) => f.name));
    try {
      // Sequential, not Promise.all: each upload writes a timeline row, and a
      // burst of parallel writes would interleave them into an order that does
      // not match what the person did.
      for (const file of Array.from(picked)) {
        await attachmentsApi.upload(task.id, file);
      }
      const [fresh, tl] = await Promise.all([
        attachmentsApi.list(task.id),
        projectsApi.timeline(task.id),
      ]);
      setFiles(fresh.rows);
      setTimeline(tl.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setUploading([]);
      setBusy(false);
    }
  }

  async function detach(attachmentId: string) {
    setBusy(true);
    setError(null);
    try {
      await attachmentsApi.detach(task.id, attachmentId);
      const fresh = await attachmentsApi.list(task.id);
      setFiles(fresh.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    const [fresh, tl] = await Promise.all([
      projectsApi.task(task.id),
      projectsApi.timeline(task.id),
    ]);
    setTimeline(tl.rows);
    onChanged(fresh);
  }

  /**
   * WS-27ak(3) — the first call site of the toast primitive.
   *
   * Before it, moving a task through its lanes from here confirmed **nothing**:
   * the select changed, the panel reloaded, and whether the write landed was
   * something you inferred from the chip. A failure said so in the inline line
   * at the top of a panel that is routinely scrolled past its own header.
   *
   * ⚠️ **`setError` stays.** The toast is an addition, not a replacement: it is
   * persistent and carries Retry, but `useToast()` degrades to a no-op when no
   * provider is above it, and a call site with no second channel would then
   * fail silently — the exact defect this primitive exists to remove, arriving
   * through its own front door. Retiring the inline lines is a later ticket
   * with the provider fence (`conformance.test.ts` rule 8) already in place.
   */
  async function changeStatus(statusId: string) {
    setBusy(true);
    setError(null);
    try {
      // Only the PATCH is inside the toast. Folding `reload()` in as well
      // would report a failed re-read as a failed status change, which is a
      // different, and wrong, sentence.
      await toast.promise(projectsApi.patchTask(task.id, { status_id: statusId }), {
        // Keyed on the TASK, so hammering the select mutates one toast rather
        // than stacking one per keystroke.
        key: `projects:task-status:${task.id}`,
        loading: "Changing status…",
        // Derived from the row the server sent back, not from the id that was
        // asked for — so the toast names where the task actually landed.
        success: (fresh) =>
          `Moved to ${statuses.find((s) => s.id === fresh.status_id)?.name ?? "a new lane"}`,
        error: "Couldn't change the status",
        errorAction: {
          label: "Retry",
          // Never rejects — `changeStatus` catches — so this cannot become an
          // unhandled rejection from a click handler.
          onClick: () => void changeStatus(statusId),
        },
      });
      await reload();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function saveAssignees(next: string[]) {
    setBusy(true);
    setError(null);
    try {
      await projectsApi.setAssignees(task.id, next);
      await reload();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function addAssignees() {
    // A whole pasted list at once, not one at a time: the PUT replaces the set
    // anyway, so batching them is one event rather than N.
    let next = assignees;
    for (const who of parseAssignees(assignee)) next = withAssignee(next, who);
    // Identity means nothing changed — skipping the PUT is what stops a
    // re-assert emitting pm.task.assigned and re-dispatching an agent run.
    if (next === assignees) {
      setAssignee("");
      return;
    }
    setAssignee("");
    await saveAssignees(next);
  }

  async function addSubtask() {
    const title = subtask.trim();
    if (!title) return;
    setBusy(true);
    setError(null);
    try {
      // A subtask is a task with a parent (§3.5) — no second endpoint and no
      // second table, so it inherits statuses, timeline and assignment whole.
      await projectsApi.createTask({
        project_id: task.project_id,
        parent_task_id: task.id,
        title,
      });
      setSubtask("");
      await reload();
      onTaskAdded?.();
      setRelationsKey((k) => k + 1);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function addComment() {
    const body = comment.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    setNotDelivered(null);
    try {
      const posted = await projectsApi.comment(task.id, body);
      setComment("");
      // WS-27j: a mention that reached nobody is said out loud. The comment
      // still posted, so this is a notice rather than an error — but silence
      // would leave the author believing a colleague was pulled in.
      setNotDelivered(notDeliveredNotice(posted.not_notified ?? []));
      await reload();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
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
      // address bar — so this fails quietly rather than turning a copy
      // button into an error banner.
    }
  }

  /** Insert `@address` at the caret, so nobody has to type the token by hand. */
  function mention(who: string) {
    const box = commentBox.current;
    const at = box ? box.selectionStart : comment.length;
    const next = insertMention(comment, at, who);
    setComment(next.body);
    // Restoring the caret is what makes the picker usable twice in a row:
    // React resets it to the end of the new value otherwise.
    requestAnimationFrame(() => {
      box?.focus();
      box?.setSelectionRange(next.caret, next.caret);
    });
  }

  return (
    // `bg-background` under a `bg-card` header, which is ItemDetail's
    // arrangement and the reason the field cells below read as cards at all — a
    // `bg-card` cell on a `bg-card` panel is an invisible box with a border.
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
            {/* WS-27ab — peek · side · full. A three-stop segmented control
                rather than one cycling button: the stops are named, the
                current one is `aria-pressed`, and nobody has to press a
                button three times to find out what it does. Hidden entirely
                where the page did not pass `onMode` (the phone, where the
                panel already fills the screen). */}
            {onMode ? (
              <div
                className="mr-1 flex items-center gap-0.5 rounded-md border border-border p-0.5"
                role="group"
                aria-label="Panel width"
              >
                {PANEL_MODES.map((option) => (
                  <Button
                    key={option}
                    // The house's pressed-state idiom, as FilterBar's Mine /
                    // Unassigned / Overdue toggles wear it — never a colour
                    // class passed through `className`, which the primitive
                    // reserves for layout.
                    variant={mode === option ? "primary" : "ghost"}
                    size="icon-xs"
                    icon={PANEL_MODE_ICONS[option]}
                    aria-label={PANEL_MODE_LABELS[option]}
                    aria-pressed={mode === option}
                    title={PANEL_MODE_HINTS[option]}
                    onClick={() => onMode(option)}
                  />
                ))}
              </div>
            ) : null}
            {/* WS-27v — watch/unwatch. Watching means the bell hears about this
                task; assignees hear regardless, so unwatching never silences
                work you hold. Hidden (not disabled) until the state is known. */}
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
        {/* Wraps rather than truncates: the panel is 448px docked, and the title
            is the one thing on it nobody can afford to have cut off. */}
        <h2 className="text-base font-semibold leading-snug text-foreground">
          {task.title}
        </h2>
        {/* The chip row — ItemDetail's status/source strip. The status is drawn
            here through the shared `StatusChip`, in the accent
            `accentForStatus` resolves from the owner's stored colour and then
            from the machine-readable category (`lib/statusAccent.ts`): two
            facts /tasks does not have, so this reads richer than the pill next
            door rather than poorer. The Details block below carries the CONTROL
            that changes it. */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {currentStatus ? (
            <StatusChip accent={statusTone} label={currentStatus.name} />
          ) : null}
          {task.completed_at ? (
            <Badge tone="success" icon="Check">
              Completed
            </Badge>
          ) : null}
        </div>
      </header>

      {error ? (
        <p className="shrink-0 border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      {/* ONE scroll region. It used to be two: a fixed field block that could
          eat the whole panel on a short window, with the timeline scrolling
          under it. ItemDetail scrolls the whole detail, and so does this now —
          only the header and the comment composer are pinned. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ⚠️ **Two columns are decided by the SURFACE, never by the viewport.**
            This file's header records why, and it is the rule `/tasks`' detail
            already follows with its `focused` flag: an `sm:grid-cols-2` keys
            off the WINDOW, so on a 4K monitor it would split the 448px docked
            panel into two 220px columns — the exact collision `/tasks` hit
            when it was docked at 380px. `twoColumn` reads the panel's own
            width stop instead, so the split happens at `full` and nowhere
            else, and Peek and Side keep the single column they need.

            ⚠️ The SECTIONS DO NOT MOVE. An earlier attempt lifted them into
            a main/aside split and had to write each one twice, once per
            branch — and the very next edit landed in one copy only. Pairing
            them inside the containers they already live in gets the same
            halving of the panel's length with no second copy to drift. */}
        <div className={PAIRED_SECTIONS(twoColumn)}>
          <CollapsibleSection
              label="Details"
              icon="SlidersHorizontal"
              className={twoColumn ? "min-w-0" : undefined}
              {...fold("details")}
            >
            {/* `grid-cols-1`, with no responsive variant on purpose — see the
                width note in this file's header. */}
            <div className="grid grid-cols-1 gap-2">
              <FieldCell
                label="Status"
                icon="CircleDot"
                trailing={
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${statusTone.dot}`}
                    aria-hidden
                  />
                }
              >
                <Select
                  value={task.status_id}
                  disabled={busy}
                  aria-label="Status"
                  onChange={(e) => void changeStatus(e.target.value)}
                >
                  {statuses.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              </FieldCell>

              <FieldCell
                label="Assignees"
                icon="Users"
                trailing={
                  assignees.length ? (
                    <span
                      className="text-[10px] text-muted-foreground"
                      title={`${assignees.length} assignee${
                        assignees.length === 1 ? "" : "s"
                      }`}
                    >
                      {assignees.length}
                    </span>
                  ) : null
                }
              >
                <div className="flex flex-wrap gap-1">
                  {assignees.map((who) => {
                    const kind = classify(who);
                    return (
                      <Badge
                        key={who}
                        // An address that is neither an email nor `agent:<name>`
                        // is a typo somebody has to see, so it takes the warning
                        // tone rather than a quieter outline.
                        tone={kind === "unknown" ? "warning" : "neutral"}
                        // Agents and people are one vocabulary (D-PM-4), so the
                        // difference is an icon, never a separate field.
                        icon={kind === "agent" ? "Bot" : undefined}
                        title={
                          kind === "unknown" ? "Not an email or agent:<name>" : who
                        }
                      >
                        {assigneeLabel(who)}
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Unassign ${who}`}
                          onClick={() =>
                            void saveAssignees(withoutAssignee(assignees, who))
                          }
                          className="opacity-70 hover:opacity-100"
                        >
                          <Icon name="X" className="h-3 w-3" />
                        </button>
                      </Badge>
                    );
                  })}
                  {assignees.length === 0 ? (
                    <span className="text-xs text-muted-foreground">Nobody yet</span>
                  ) : null}
                </div>
                {/* WS-28e: directory-backed suggestions replace the bare
                    input — people and agents, each row carrying its warning.
                    Free text still commits exactly as before: the server
                    accepts any non-empty string, and the picker must not
                    invent a rule the API does not enforce. */}
                <AssigneePicker
                  value={assignee}
                  disabled={busy}
                  due={task.due_at ?? null}
                  onChange={setAssignee}
                  onCommitText={() => void addAssignees()}
                  onPick={(who) => {
                    setAssignee("");
                    const next = withAssignee(assignees, who);
                    if (next !== assignees) void saveAssignees(next);
                  }}
                />
              </FieldCell>
            </div>
          </CollapsibleSection>

          {task.description ? (
            // Prose spans both columns: a description set in a 50% column
            // beside a field stack is a paragraph nobody finishes.
            //
            // ⚠️ `order-last` is what makes the pairing work, and it is why
            // no JSX had to move. Description sits BETWEEN Details and
            // Properties in the DOM, so in a grid it landed on its own row
            // and pushed Properties onto a third — one column of content
            // with an empty column beside it. `order` re-places it for
            // auto-flow without touching the reading order that Peek and
            // Side still use.
            <CollapsibleSection
              label="Description"
              icon="AlignLeft"
              className={twoColumn ? "order-last col-span-2 min-w-0" : undefined}
              {...fold("description")}
            >
              <p className="whitespace-pre-wrap text-sm text-foreground">
                {task.description}
              </p>
            </CollapsibleSection>
          ) : null}

          {/* Tags and recurrence draw their own small labels, so they are
              sub-fields of one section rather than two sections whose headings
              would compete with the ones those components already render.
              Promoting those labels onto `SectionLabel` means editing
              `TagPicker`/`RepeatEditor`, which S5 does not own. */}
          <CollapsibleSection
              label="Properties"
              icon="Tag"
              className={twoColumn ? "min-w-0" : undefined}
              {...fold("properties")}
            >
            <div className="space-y-3">
              {/* Saved on every change rather than behind a button: a chip is a
                  single decision, and a Save beside it would be a second click
                  for something that is already unambiguous. */}
              <TagPicker
                value={task.tags ?? []}
                registry={tags}
                disabled={busy}
                onChange={(next) => {
                  void (async () => {
                    try {
                      onChanged(await projectsApi.patchTask(task.id, { tags: next }));
                    } catch (err) {
                      setError(String((err as Error).message));
                    }
                  })();
                }}
              />
              <RepeatEditor taskId={task.id} />
            </div>
          </CollapsibleSection>
        </div>

        {/* Custom fields bring their own section chrome — a top rule, their own
            gutter and an `h4` — so they sit OUTSIDE the padded column rather
            than nesting one gutter inside another. Renders nothing at all when
            the project defined no fields, so a project that never wanted them
            never grows an empty heading. */}
        <CustomFieldValues task={task} fields={fields} onChanged={onChanged} />

        <div className={PAIRED_SECTIONS(twoColumn)}>
          {/* Both halves existed in the schema since WS-27a with no surface:
              links could be created and deleted but never listed, and subtasks
              could be created but never shown. */}
          <CollapsibleSection
              label="Links & subtasks"
              icon="GitBranch"
              className={twoColumn ? "min-w-0" : undefined}
              {...fold("links")}
            >
            <div className="space-y-2">
              {onOpenTask ? (
                <RelationsBlock
                  taskId={task.id}
                  task={task}
                  refreshKey={relationsKey}
                  onOpenTask={onOpenTask}
                />
              ) : null}
              <Input
                icon="Plus"
                value={subtask}
                disabled={busy}
                onChange={(e) => setSubtask(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void addSubtask();
                  }
                }}
                placeholder="Break this down…"
                aria-label="Add a subtask"
              />
            </div>
          </CollapsibleSection>

          <CollapsibleSection
              label="Files"
              icon="Paperclip"
              count={files.length}
              className={twoColumn ? "min-w-0" : undefined}
              {...fold("files")}
            >
            <div className="space-y-1">
              {files.map((f) => {
                const kind = previewKind(f.mime);
                const size = readableSize(f.size);
                return (
                  <div
                    key={f.attachment_id}
                    className="flex items-center gap-2 rounded-md border border-border bg-card p-1.5 text-xs"
                  >
                    {/* ⚠️ The thumbnail is a BUTTON, not a link.
                        A link opened a tab and lost the task, which is the
                        wrong answer for the common case: a screenshot on a
                        bug, which you want beside the description you are
                        reading. Open-in-a-tab is still one click away, in
                        the viewer. */}
                    <button
                      type="button"
                      onClick={() => setViewing(f)}
                      aria-label={`Open ${f.name}`}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      {kind === "image" ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={f.url}
                          alt=""
                          // `alt=""` on purpose: the name is right beside it,
                          // and a screen reader reading the filename twice is
                          // noise. The button carries the accessible name.
                          loading="lazy"
                          className="h-9 w-9 shrink-0 rounded border border-border object-cover"
                        />
                      ) : (
                        <span
                          className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-border bg-muted"
                          aria-hidden
                        >
                          <Icon
                            name={kind === "document" ? "FileText" : "Paperclip"}
                            className="h-4 w-4 text-muted-foreground"
                          />
                        </span>
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-foreground">
                          {f.name}
                        </span>
                        <span className="block truncate text-[10px] text-muted-foreground">
                          {[kind === "none" ? "Downloads" : "Preview", size]
                            .filter(Boolean)
                            .join(" \u00b7 ")}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Remove ${f.name}`}
                      title="Removes it from this task; the file itself is kept"
                      onClick={() => void detach(f.attachment_id)}
                      className="shrink-0 text-muted-foreground hover:text-foreground"
                    >
                      <Icon name="X" className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
              {/* What is going up right now, by name. */}
              {uploading.map((name) => (
                <div
                  key={`uploading:${name}`}
                  className="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1.5 text-xs text-muted-foreground"
                >
                  <Icon name="Loader2" className="h-3.5 w-3.5 shrink-0 animate-spin" />
                  <span className="min-w-0 flex-1 truncate">{name}</span>
                </div>
              ))}
              {files.length === 0 && uploading.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nothing attached.</p>
              ) : null}
            </div>
            {/* S5 — the picker is a Button, not a bare file input. The
                browser's own "Choose Files / No file chosen" is not a design:
                it follows neither the theme's control personality nor the icon
                pack, and it was the most visibly unstyled control on this
                panel. The input stays in the tree, hidden, because it is the
                only way to raise the OS file dialog. Fenced by conformance
                rule 7. */}
            <input
              ref={filePicker}
              type="file"
              multiple
              className="hidden"
              tabIndex={-1}
              aria-hidden
              onChange={(e) => {
                void uploadFiles(e.target.files);
                // Reset so picking the SAME file twice still fires a change.
                e.target.value = "";
              }}
            />
            <Button
              className="mt-2"
              variant="secondary"
              size="sm"
              icon="Upload"
              loading={uploading.length > 0}
              disabled={busy}
              onClick={() => filePicker.current?.click()}
            >
              {uploading.length > 0 ? "Uploading…" : "Attach files"}
            </Button>
          </CollapsibleSection>

          {/* The timeline spans: it is a list of sentences, and half a
              panel is not enough line length for one. */}
          <CollapsibleSection
              label="Activity"
              icon="History"
              count={timeline.length}
              className={twoColumn ? "col-span-2 min-w-0" : undefined}
              {...fold("activity")}
            >
            <ol className="space-y-3">
              {timeline.map((activity) => (
                <li key={activity.id} className="text-sm">
                  <p className="flex items-center gap-1 text-xs text-muted-foreground">
                    {/* WS-27z — an automated entry says so. The flag is the row's
                        meta.automation, written only by the workflow engine; a
                        sweep archiving a task must not read as a person did it. */}
                    {isAutomated(activity) ? (
                      <Badge size="xs" icon="Bot" title="Automated by a workflow">
                        auto
                      </Badge>
                    ) : null}
                    <span className="min-w-0 truncate">
                      {activity.created_by ?? "system"}
                      {activity.created_at
                        ? ` · ${new Date(activity.created_at).toLocaleString()}`
                        : ""}
                    </span>
                  </p>
                  <p className="whitespace-pre-wrap text-foreground">
                    {describe(activity, fields)}
                  </p>
                </li>
              ))}
              {timeline.length === 0 ? (
                <li className="text-sm text-muted-foreground">
                  Nothing on the timeline yet.
                </li>
              ) : null}
            </ol>
          </CollapsibleSection>
        </div>
      </div>

      <div className="shrink-0 border-t border-border bg-card p-3">
        <Textarea
          ref={commentBox}
          inputSize="lg"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Add a comment…"
          aria-label="Add a comment"
          rows={2}
        />
        {/* Mentionable people are this task's assignees. A full directory
            picker is WS-28e's job; these are who a comment names in practice,
            and typing the address by hand still works for anyone else. */}
        {mentionable.length ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            <span className="text-[10px] text-muted-foreground">Mention</span>
            {mentionable.map((who) => (
              <Button
                key={who}
                variant="secondary"
                size="sm"
                onClick={() => mention(who)}
              >
                @{who.split("@")[0]}
              </Button>
            ))}
          </div>
        ) : null}
        {notDelivered ? (
          <p className="mt-1 text-[11px] text-muted-foreground">{notDelivered}</p>
        ) : null}
        <Button
          className="mt-2 w-full"
          icon="MessageSquare"
          onClick={addComment}
          disabled={busy || !comment.trim()}
        >
          Comment
        </Button>
      </div>
      {/* The viewer is mounted at the panel root rather than inside the
          Files section, so it is not clipped by the scroll container and
          does not move when that section reflows into the other column. */}
      <AttachmentViewer
        file={viewing}
        busy={busy}
        onClose={() => setViewing(null)}
        onRemove={(id) => {
          setViewing(null);
          void detach(id);
        }}
      />
    </aside>
  );
}
