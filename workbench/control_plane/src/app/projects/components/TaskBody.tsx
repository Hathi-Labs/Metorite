"use client";

/**
 * Projects · the task's BODY — the field blocks every task panel draws.
 *
 * ## One composition, two hosts (WS-39 S6e, my_tasks_cutover.md §4.8 point 3)
 *
 * A member who opens a task in Projects and the same task in My Tasks must
 * meet the same fields in the same order: lane, priority, assignees, due
 * date, description, tags, custom fields, links and subtasks, files, and the
 * discussion (comments and the activity timeline). Until S6e those were two
 * compositions — `TaskPanel` here and a ClickUp-era `ItemDetail` there — and
 * "the same fields" was a claim nobody could keep by hand.
 *
 * So the blocks live HERE, once, with their state and their writes, and the
 * two panels are hosts: `TaskPanel` puts its header (the ref, the width
 * stops, the watch toggle) above this, and `ItemDetail` puts its overlay
 * strip (disposition, context, energy, estimate, defer — `pm_task_personal`,
 * the member's own facts) above it through `above`. Nothing below the strip
 * is written twice. The source fence in `tasks/lib/itemDetail.test.ts`
 * refuses a second field list.
 *
 * ⚠️ **The SURFACE decides the column count, never the viewport.** `twoColumn`
 * is the host's own width stop (`full` in Projects), so a 4K monitor does
 * not split a 448px docked column. The sections do not MOVE between the two
 * shapes: they pair inside the containers they already live in, so there is
 * no second copy to drift (the note in `TaskPanel.tsx`'s header records the
 * attempt that had one).
 *
 * ## One home per work fact (D76, WS-39 S6f, 2026-09-23)
 *
 * Everything a task IS lives in this body, so both apps read and write it
 * here: Priority, the estimate, the start and due dates, the description,
 * tags, and whether I watch it. S6f moved four of those in — the estimate
 * (Projects had no editor), the start date, the description editor (it was
 * read-only here, and My Tasks' Notes was the only writer) and the watch
 * toggle (it was Projects-header only). Time spent is the one read-only
 * cell: every member's timed blocks, summed, beside the estimate.
 * `itemDetail.test.ts` refuses the strip above drawing any of these again.
 *
 * The two-stream Discussion (comments apart from activity), the one reused
 * composer, the roll-up of old events, the Button-not-file-input picker and
 * the viewer mounted at the root all keep the reasons `TaskPanel.tsx` gave
 * them; those notes moved with the code.
 */

import Icon from "@/components/Icon";
import Tabs from "@/components/Tabs";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { Input, Textarea } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
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
import {
  type FoldableSection,
  readFolded,
  toggleFold,
  writeFolded,
} from "../lib/sectionFold";
import { previewKind, readableSize } from "../lib/preview";
import { isAutomated } from "../lib/lifecycle";
import { labelWith } from "../lib/grouping";
import { IMPORTANCE_OPTIONS } from "../lib/table";
import { dueInstantForDay } from "../lib/quickAdd";
import { AssigneePicker } from "./AssigneePicker";
import { AssigneeChips } from "./AssigneeChips";
import { CustomFieldValues } from "./CustomFieldValues";
import { TagPicker } from "./TagPicker";
import { RepeatEditor } from "./RepeatEditor";
import { RelationsBlock } from "./RelationsBlock";
import { AttachmentViewer } from "./AttachmentViewer";
import { durationLabel } from "@/lib/taskCard";
import {
  ROLLUP_AT,
  describeActivity,
  rollUp,
  threadComments,
} from "../lib/activityStream";
import { parseAssignees, withAssignee, withoutAssignee } from "../lib/assignees";
import { insertMention, notDeliveredNotice } from "../lib/notifications";

export interface TaskBodyProps {
  task: TaskRow;
  statuses: StatusRow[];
  onChanged: (task: TaskRow) => void;
  /**
   * Fired when this body adds a row the surrounding list does not know
   * about. `onChanged` merges one task; a new subtask is a task the board
   * has never seen, so it needs a real reload rather than a merge.
   */
  onTaskAdded?: () => void;
  /** WS-27l — the project's custom field definitions (the root's). */
  fields?: FieldRow[];
  /** WS-27m — the project's registered tags, for the picker's suggestions. */
  tags?: TagRow[];
  /** WS-27p — open another task by id, for a subtask or a linked task. */
  onOpenTask?: (taskId: string) => void;
  /** See TaskBoard's prop of the same name. */
  personLabels?: ReadonlyMap<string, string>;
  /** Addresses this body has seen that the board may not have. */
  onPeopleSeen?: (who: string[]) => void;
  /** Two columns — decided by the HOST's width stop, never the viewport. */
  twoColumn?: boolean;
  /**
   * What the host draws above the shared blocks, inside the one scroll
   * region. My Tasks' overlay strip. Projects passes nothing.
   */
  above?: React.ReactNode;
}

/**
 * The estimate presets, in minutes (D76). The same steps My Tasks' Estimate
 * offered, plus a working day, so neither app loses a value it could set.
 * A stored value off the list is kept as its own option, never snapped.
 */
export const ESTIMATE_PRESETS: readonly number[] = [5, 15, 30, 60, 120, 240, 480];

/** The Estimate select's options for one stored value. */
export function estimateOptions(current: number | null | undefined) {
  const steps = [...ESTIMATE_PRESETS];
  if (current && !steps.includes(current)) steps.push(current);
  steps.sort((a, b) => a - b);
  return [
    { value: "", label: "No estimate" },
    ...steps.map((m) => ({ value: String(m), label: durationLabel(m) })),
  ];
}

/**
 * The status a task is on, and its accent — one lookup for the host's chip
 * and the body's Status cell. `statusAccent`'s positional fallback wants the
 * lane's index; a status_id with no matching row (the panel opened before
 * its project's statuses resolved) draws no chip rather than a wrong one.
 */
export function resolveStatus(task: TaskRow, statuses: StatusRow[]) {
  const index = statuses.findIndex((s) => s.id === task.status_id);
  const current = index >= 0 ? statuses[index] : undefined;
  const tone = accentForStatus(current, Math.max(index, 0), statuses.length);
  return { current, tone };
}

/**
 * The container class for a group of sections that may pair up. One helper
 * rather than the same ternary at each container, so the two groups cannot
 * drift into different gaps or different breakpoints. `items-start`
 * matters: without it the grid stretches both cells to the taller one.
 */
const PAIRED_SECTIONS = (twoColumn: boolean): string =>
  twoColumn
    ? "grid grid-cols-2 items-start gap-4 px-3 py-3"
    : "flex flex-col gap-4 px-3 py-3";

/**
 * One labelled cell in the details block — the chrome `ItemDetail`'s
 * `MetaEdit` draws in its closed state, without the click-to-edit flip: the
 * controls here are live, and hiding them behind a click would be an
 * interaction change, not a composition one.
 *
 * `trailing` is the right-hand slot of the label row (an accent dot, a count).
 */
export function FieldCell({
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

/** The first few words of a comment, for the "Replying to …" line. */
function excerpt(body: string, max = 60): string {
  const flat = body.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/**
 * One line of either stream: who, when, and what it says.
 *
 * ⚠️ **Shared by both lists on purpose.** A comment and a status change are
 * the same row in the same table (§3.8), and drawing them through two
 * components is how the byline in one drifts from the byline in the other.
 */
function Entry({
  activity,
  fields,
  personLabels,
}: {
  activity: ActivityRow;
  fields: FieldRow[];
  personLabels?: ReadonlyMap<string, string>;
}) {
  const who = activity.created_by ?? "";
  return (
    <div className="text-sm">
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        {isAutomated(activity) ? (
          <Badge size="xs" icon="Bot" title="Automated by a workflow">
            auto
          </Badge>
        ) : null}
        <span className="min-w-0 truncate">
          {who ? labelWith(personLabels)(who) : "system"}
          {activity.created_at
            ? ` · ${new Date(activity.created_at).toLocaleString()}`
            : ""}
        </span>
      </p>
      <p className="whitespace-pre-wrap text-foreground">
        {describeActivity(activity, fields)}
      </p>
    </div>
  );
}

interface Stream {
  rows: ActivityRow[];
  total: number;
}

const EMPTY_STREAM: Stream = { rows: [], total: 0 };

export function TaskBody({
  task,
  statuses,
  onChanged,
  onTaskAdded,
  fields = [],
  tags = [],
  onOpenTask,
  personLabels,
  onPeopleSeen,
  twoColumn = false,
  above,
}: TaskBodyProps) {
  const toast = useToast();

  //: The attachment whose viewer is open. One at a time.
  const [viewing, setViewing] = useState<AttachmentRow | null>(null);

  /**
   * Which sections this member has folded away. Read in an EFFECT, not in a
   * lazy initialiser: `localStorage` does not exist while this renders on the
   * server, and a first paint that disagreed with the second is a hydration
   * mismatch.
   */
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
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

  const fold = (section: FoldableSection) => ({
    open: !folded.has(section),
    onOpenChange: (open: boolean) => setSectionOpen(section, open),
  });

  /**
   * ⚠️ **TWO streams, read separately, and that is the whole point.** Owner
   * request, 2026-09-21: the comments were "getting muddled up with the
   * activity". One table still (§3.8) — the SPLIT is a `kind` filter on the
   * one endpoint. Read separately because each list paginates on its own.
   */
  const [events, setEvents] = useState<Stream>(EMPTY_STREAM);
  const [comments, setComments] = useState<Stream>(EMPTY_STREAM);
  const [stream, setStream] = useState<"comments" | "activity">("comments");
  const [allEvents, setAllEvents] = useState(false);
  /** The comment the composer is answering, or `null` for a new thread. ONE
   *  composer, reused — a second editor under each comment would be a second
   *  place for the mention picker and the keyboard handling to drift (§5). */
  const [replyTo, setReplyTo] = useState<ActivityRow | null>(null);
  const [comment, setComment] = useState("");
  const [notDelivered, setNotDelivered] = useState<string | null>(null);
  const commentBox = useRef<HTMLTextAreaElement | null>(null);
  const [assignee, setAssignee] = useState("");
  const [subtask, setSubtask] = useState("");
  // Bumped when this body adds a subtask, so the relations block re-reads.
  const [relationsKey, setRelationsKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<AttachmentRow[]>([]);
  // The names of the files currently going up (S5) — what is IN FLIGHT.
  const [uploading, setUploading] = useState<string[]>([]);
  const filePicker = useRef<HTMLInputElement | null>(null);
  // D76 — the description is edited HERE, in both apps. A draft, so typing
  // does not PATCH per keystroke; saved on blur when it changed.
  const [description, setDescription] = useState(task.description ?? "");
  // D76 — whether I watch this task. Null until the read lands, so the
  // control never renders a state it is only guessing at.
  const [watching, setWatching] = useState<boolean | null>(null);
  // D76 — every member's timed work on the task, summed by the gateway
  // (`GET /projects/tasks/{id}` → `time_spent_mins`). Null until read.
  const [spent, setSpent] = useState<number | null>(null);
  const assignees = task.assignees ?? [];
  // Agents are excluded: an agent cannot receive a notification (migration
  // 152's CHECK), so offering to mention one would promise nothing.
  const mentionable = assignees.filter((who) => !who.startsWith("agent:"));
  const { tone: statusTone } = resolveStatus(task, statuses);

  useEffect(() => {
    let live = true;
    // ⚠️ Reset FIRST. Without this the next task's body paints the previous
    // task's comments for as long as the two reads take, and a reply posted
    // in that window would attach to a comment on another task.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEvents(EMPTY_STREAM);
    setComments(EMPTY_STREAM);
    setReplyTo(null);
    setAllEvents(false);
    void loadStreams(task.id, false).then((got) => {
      if (!got || !live) return;
      setComments(got.comments);
      setEvents(got.events);
      const seen = [...got.comments.rows, ...got.events.rows]
        .map((entry) => entry.created_by ?? "")
        .filter((who) => who && !who.startsWith("agent:") && !who.startsWith("system:"));
      if (seen.length) onPeopleSeen?.(seen);
    });
    attachmentsApi
      .list(task.id)
      .then((res) => {
        if (live) setFiles(res.rows);
      })
      // Attachments failing must not blank the body: the status control and
      // the assignees are the reason somebody opened it.
      .catch(() => undefined);
    setWatching(null);
    setSpent(null);
    watchersApi
      .get(task.id)
      .then((res) => {
        if (live) setWatching(res.watching);
      })
      // No toggle beats a blank body.
      .catch(() => undefined);
    projectsApi
      .task(task.id)
      .then((row) => {
        if (live) setSpent(row.time_spent_mins ?? 0);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
    // `onPeopleSeen` is `useCallback`'d with no dependencies on the page, so
    // listing it here costs nothing.
  }, [task.id, onPeopleSeen]);

  // A different task, or a save that came back: the draft follows the row.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(task.description ?? "");
  }, [task.id, task.description]);

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

  function saveDescription() {
    const next = description.trim();
    if (next === (task.description ?? "").trim()) return;
    void patch({ description: next || null });
  }

  async function uploadFiles(picked: FileList | null) {
    if (!picked || picked.length === 0) return;
    setBusy(true);
    setError(null);
    setUploading(Array.from(picked).map((f) => f.name));
    try {
      // Sequential, not Promise.all: each upload writes a timeline row, and a
      // burst of parallel writes would interleave them.
      for (const file of Array.from(picked)) {
        await attachmentsApi.upload(task.id, file);
      }
      const [fresh, got] = await Promise.all([
        attachmentsApi.list(task.id),
        loadStreams(task.id, allEvents),
      ]);
      setFiles(fresh.rows);
      if (got) {
        setComments(got.comments);
        setEvents(got.events);
      }
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

  /**
   * Both streams for one task. `deep` asks for the whole activity history
   * rather than the first page. Resolves `null` on failure, having already
   * reported it — a body that blanked because the timeline 500'd would take
   * the status control with it.
   */
  async function loadStreams(taskId: string, deep: boolean) {
    try {
      const [conv, evt] = await Promise.all([
        projectsApi.timeline(taskId, {
          kind: "comments",
          pageSize: projectsApi.MAX_TIMELINE_PAGE,
        }),
        projectsApi.timeline(taskId, {
          kind: "events",
          pageSize: deep ? projectsApi.MAX_TIMELINE_PAGE : 50,
        }),
      ]);
      return { comments: conv, events: evt };
    } catch (err) {
      setError(String((err as Error).message ?? err));
      return null;
    }
  }

  async function reload() {
    const [fresh, got] = await Promise.all([
      projectsApi.task(task.id),
      loadStreams(task.id, allEvents),
    ]);
    if (got) {
      setComments(got.comments);
      setEvents(got.events);
    }
    onChanged(fresh);
  }

  const shownEvents = rollUp(events.rows, {
    expanded: allEvents,
    total: events.total,
  });

  function startReply(root: ActivityRow) {
    setReplyTo(root);
    setStream("comments");
    commentBox.current?.focus();
  }

  async function showAllEvents() {
    setAllEvents(true);
    if (events.rows.length >= events.total) return;
    const got = await loadStreams(task.id, true);
    if (got) setEvents(got.events);
  }

  /**
   * WS-27ak(3) — the status change confirms through the toast. `setError`
   * stays: `useToast()` degrades to a no-op with no provider above it, and a
   * call site with no second channel would then fail silently.
   */
  async function changeStatus(statusId: string) {
    setBusy(true);
    setError(null);
    try {
      await toast.promise(projectsApi.patchTask(task.id, { status_id: statusId }), {
        key: `projects:task-status:${task.id}`,
        loading: "Changing status…",
        success: (fresh) =>
          `Moved to ${statuses.find((s) => s.id === fresh.status_id)?.name ?? "a new lane"}`,
        error: "Couldn't change the status",
        errorAction: {
          label: "Retry",
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

  /** One shared fact of the task — due date, priority, tags — patched and
   *  merged back. The board's cell editors write the same payloads. */
  async function patch(payload: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await projectsApi.patchTask(task.id, payload));
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
      const posted = await projectsApi.comment(task.id, body, replyTo?.id);
      setComment("");
      setReplyTo(null);
      setStream("comments");
      // WS-27j: a mention that reached nobody is said out loud.
      setNotDelivered(notDeliveredNotice(posted.not_notified ?? []));
      await reload();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  /** Insert `@address` at the caret, so nobody has to type the token by hand. */
  function mention(who: string) {
    const box = commentBox.current;
    const at = box ? box.selectionStart : comment.length;
    const next = insertMention(comment, at, who);
    setComment(next.body);
    requestAnimationFrame(() => {
      box?.focus();
      box?.setSelectionRange(next.caret, next.caret);
    });
  }

  return (
    <>
      {error ? (
        <p className="shrink-0 border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      {/* ONE scroll region: only the host's header and the composer are
          pinned. The host's strip (`above`) scrolls with the blocks. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {above}

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
                <SelectButton
                  label="Status"
                  widthClass="w-full"
                  value={task.status_id}
                  disabled={busy}
                  onChange={(next) => void changeStatus(next)}
                  options={statuses.map((s) => ({ value: s.id, label: s.name }))}
                />
              </FieldCell>

              {/* The shared Priority integer (`pm_tasks.importance`), the
                  table's vocabulary. ⚠️ Not the member's Eisenhower
                  `important` — that is on the overlay, and My Tasks draws it
                  in its strip above. */}
              <FieldCell label="Priority" icon="Flag">
                <SelectButton
                  label="Priority"
                  widthClass="w-full"
                  value={task.importance == null ? "" : String(task.importance)}
                  disabled={busy}
                  onChange={(next) =>
                    // `""` is "no priority" and `"0"` is Low. `Number("")` is
                    // 0, so the emptiness check is what keeps them apart.
                    void patch({ importance: next === "" ? null : Number(next) })
                  }
                  options={IMPORTANCE_OPTIONS.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                />
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
                <AssigneeChips
                  assignees={assignees}
                  disabled={busy}
                  personLabels={personLabels}
                  onRemove={(who) => void saveAssignees(withoutAssignee(assignees, who))}
                />
                {/* WS-28e: directory-backed suggestions. Free text still
                    commits: the server accepts any non-empty string, and the
                    picker must not invent a rule the API does not enforce. */}
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

              {/* One deadline, shared by everyone — on the task, never on
                  the overlay (`PersonalIn`'s docstring). Noon local, the
                  calendar quick-add's rule, so the day survives every
                  viewer's timezone. */}
              <FieldCell label="Due" icon="CalendarClock">
                <Input
                  type="date"
                  inputSize="sm"
                  aria-label="Due date"
                  value={task.due_at ? task.due_at.slice(0, 10) : ""}
                  disabled={busy}
                  onChange={(e) =>
                    void patch({
                      due_at: e.target.value ? dueInstantForDay(e.target.value) : null,
                    })
                  }
                />
              </FieldCell>

              {/* D76 — when the work starts, shared. A DATE, so the value is
                  the day itself, never routed through `new Date()` (the
                  `start_date` note on `TaskRow`). My Tasks hides the task
                  until this day. */}
              <FieldCell label="Start" icon="CalendarDays">
                <Input
                  type="date"
                  inputSize="sm"
                  aria-label="Start date"
                  value={task.start_date ? task.start_date.slice(0, 10) : ""}
                  disabled={busy}
                  onChange={(e) => void patch({ start_date: e.target.value || null })}
                />
              </FieldCell>

              {/* D76 — the ONE estimate: the number People capacity and the
                  analytics read, and the one My Tasks plans a day with. */}
              <FieldCell label="Estimate" icon="Timer">
                <SelectButton
                  label="Estimate"
                  widthClass="w-full"
                  value={task.estimate_mins == null ? "" : String(task.estimate_mins)}
                  disabled={busy}
                  onChange={(next) =>
                    void patch({ estimate_mins: next === "" ? null : Number(next) })
                  }
                  options={estimateOptions(task.estimate_mins)}
                />
              </FieldCell>

              {/* D76 — read-only: every member's timed blocks, summed. One
                  member's actuals are theirs; the total is the work's. */}
              <FieldCell label="Time spent" icon="Hourglass">
                <span className="text-sm text-foreground">
                  {spent === null
                    ? "…"
                    : spent > 0
                      ? durationLabel(spent)
                      : "Nothing timed yet"}
                </span>
              </FieldCell>

              {/* D76 — watching is the member's own notification choice on
                  the SHARED task, so it lives in the body both apps host.
                  Hidden until the state is known. */}
              {watching !== null ? (
                <FieldCell label="Watch" icon={watching ? "BellRing" : "Bell"}>
                  <Button
                    variant={watching ? "secondary" : "ghost"}
                    size="sm"
                    icon={watching ? "BellOff" : "Bell"}
                    aria-pressed={watching}
                    title={
                      watching
                        ? "Watching — click to stop being notified about this task"
                        : "Watch — get notified about this task"
                    }
                    onClick={() => void toggleWatch()}
                  >
                    {watching ? "Watching" : "Watch"}
                  </Button>
                </FieldCell>
              ) : null}
            </div>
          </CollapsibleSection>

          {/* D76 — the description is EDITED here, in both apps. It was
              read-only in Projects, and My Tasks' Notes was the only writer.
              Prose spans both columns. `order-last` re-places it for
              auto-flow without touching the reading order Peek and Side use. */}
          <CollapsibleSection
            label="Description"
            icon="AlignLeft"
            className={twoColumn ? "order-last col-span-2 min-w-0" : undefined}
            {...fold("description")}
          >
            <Textarea
              value={description}
              disabled={busy}
              rows={4}
              placeholder="What this task is about — notes, links, context…"
              aria-label="Description"
              onChange={(e) => setDescription(e.target.value)}
              onBlur={saveDescription}
            />
          </CollapsibleSection>

          {/* Tags and recurrence draw their own small labels, so they are
              sub-fields of one section rather than two sections whose
              headings would compete with the ones those components render. */}
          <CollapsibleSection
            label="Properties"
            icon="Tag"
            className={twoColumn ? "min-w-0" : undefined}
            {...fold("properties")}
          >
            <div className="space-y-3">
              <TagPicker
                value={task.tags ?? []}
                registry={tags}
                disabled={busy}
                onCreate={(name, color) =>
                  projectsApi.createTag(task.project_id, { name, color })
                }
                onChange={(next) => void patch({ tags: next })}
              />
              <RepeatEditor taskId={task.id} />
            </div>
          </CollapsibleSection>
        </div>

        {/* Custom fields bring their own section chrome, so they sit OUTSIDE
            the padded column. Renders nothing when the project defined no
            fields. */}
        <CustomFieldValues task={task} fields={fields} onChanged={onChanged} />

        <div className={PAIRED_SECTIONS(twoColumn)}>
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
                    {/* ⚠️ The thumbnail is a BUTTON, not a link: a link
                        opened a tab and lost the task. */}
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
                            .join(" · ")}
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
            {/* The picker is a Button, not a bare file input (S5). The input
                stays in the tree, hidden, because it is the only way to
                raise the OS file dialog. Fenced by conformance rule 7. */}
            <input
              ref={filePicker}
              type="file"
              multiple
              className="hidden"
              tabIndex={-1}
              aria-hidden
              onChange={(e) => {
                void uploadFiles(e.target.files);
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

          {/* ⚠️ TWO lists, not one stream with a filter drawn over it. `Tabs`
              is the shared bar (§5). The section SPANS in two-column mode:
              both lists are lists of sentences. */}
          <CollapsibleSection
            label="Discussion"
            icon="MessageSquare"
            count={comments.total}
            className={twoColumn ? "col-span-2 min-w-0" : undefined}
            {...fold("activity")}
          >
            <Tabs
              chrome="inline"
              className="mb-3"
              activeTab={stream}
              onTabChange={(id) => setStream(id as "comments" | "activity")}
              tabs={[
                {
                  id: "comments",
                  label: "Comments",
                  icon: "MessageSquare",
                  count: comments.total,
                  note: "What people wrote",
                },
                {
                  id: "activity",
                  label: "Activity",
                  icon: "History",
                  count: events.total,
                  note: "What happened to this task",
                },
              ]}
            />

            {stream === "comments" ? (
              <ol className="space-y-4">
                {threadComments(comments.rows).map(({ root, replies }) => (
                  <li key={root.id}>
                    <Entry
                      activity={root}
                      fields={fields}
                      personLabels={personLabels}
                    />
                    {/* ONE level: the gateway refuses a reply to a reply. */}
                    <div className="mt-1 flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        icon="CornerDownRight"
                        onClick={() => startReply(root)}
                      >
                        Reply
                      </Button>
                      {replies.length ? (
                        <span className="text-[11px] text-muted-foreground">
                          {replies.length}{" "}
                          {replies.length === 1 ? "reply" : "replies"}
                        </span>
                      ) : null}
                    </div>
                    {replies.length ? (
                      <ol className="mt-2 space-y-3 border-l border-border pl-3">
                        {replies.map((reply) => (
                          <li key={reply.id}>
                            <Entry
                              activity={reply}
                              fields={fields}
                              personLabels={personLabels}
                            />
                          </li>
                        ))}
                      </ol>
                    ) : null}
                  </li>
                ))}
                {comments.rows.length === 0 ? (
                  <li className="text-sm text-muted-foreground">
                    No comments yet. Say something below.
                  </li>
                ) : null}
              </ol>
            ) : (
              <>
                <ol className="space-y-3">
                  {shownEvents.shown.map((activity) => (
                    <li key={activity.id}>
                      <Entry
                        activity={activity}
                        fields={fields}
                        personLabels={personLabels}
                      />
                    </li>
                  ))}
                  {events.rows.length === 0 ? (
                    <li className="text-sm text-muted-foreground">
                      Nothing has happened to this task yet.
                    </li>
                  ) : null}
                </ol>
                {shownEvents.hidden > 0 ? (
                  <Button
                    className="mt-2"
                    variant="secondary"
                    size="sm"
                    icon="ChevronDown"
                    onClick={() => void showAllEvents()}
                  >
                    Show {shownEvents.hidden} older
                  </Button>
                ) : null}
                {allEvents && events.rows.length > ROLLUP_AT ? (
                  <Button
                    className="mt-2"
                    variant="ghost"
                    size="sm"
                    icon="ChevronUp"
                    onClick={() => setAllEvents(false)}
                  >
                    Show less
                  </Button>
                ) : null}
              </>
            )}
          </CollapsibleSection>
        </div>
      </div>

      {/* ⚠️ The composer belongs to the COMMENTS list, and is hidden with it. */}
      <div
        className="shrink-0 border-t border-border bg-card p-3"
        hidden={stream !== "comments"}
      >
        {replyTo ? (
          <div className="mb-2 flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5">
            <Icon name="CornerDownRight" className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              Replying to{" "}
              <span className="text-foreground">
                {labelWith(personLabels)(replyTo.created_by ?? "system")}
              </span>
              {replyTo.body ? ` — “${excerpt(replyTo.body)}”` : ""}
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label="Cancel reply"
              title="Write a new comment instead"
              onClick={() => setReplyTo(null)}
            />
          </div>
        ) : null}
        <Textarea
          ref={commentBox}
          inputSize="lg"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={replyTo ? "Write a reply…" : "Add a comment…"}
          aria-label={replyTo ? "Write a reply" : "Add a comment"}
          rows={3}
        />
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
          icon={replyTo ? "CornerDownRight" : "MessageSquare"}
          onClick={addComment}
          disabled={busy || !comment.trim()}
        >
          {replyTo ? "Reply" : "Comment"}
        </Button>
      </div>
      {/* The viewer is mounted at the root rather than inside the Files
          section, so it is not clipped by the scroll container. */}
      <AttachmentViewer
        file={viewing}
        busy={busy}
        onClose={() => setViewing(null)}
        onRemove={(id) => {
          setViewing(null);
          void detach(id);
        }}
      />
    </>
  );
}
