"use client";

/**
 * The record timeline + its quick composer (specs/crm_app.md §5 surface 3).
 *
 * Newest first, with status changes and notes/tasks/calls inline — one
 * merged stream, because "what happened to this deal" is one question. A
 * deal's stream unions its originating lead's history, labelled: everything
 * said before conversion was said about this deal, and losing it at the
 * conversion boundary is what the `lead_id` provenance column exists to
 * prevent.
 *
 * `status_change` and `system` entries are read-only by construction — the
 * gateway refuses to edit or delete them (409), because a funnel with
 * editable history is not a record of anything.
 *
 * Email threads are the third kind (WS-26d-email). ⚠️ They are scoped to the
 * SIGNED-IN CALLER's mailboxes, not to the record: two colleagues opening the
 * same lead can legitimately see different threads, and somebody with no
 * mailbox connected sees none at all. The dispatch below goes through
 * `timelineBranch` rather than a ternary, because a ternary makes "not a
 * status change" mean "an activity" and `ActivityEntry` reads `entry.activity!`
 * — a `!` tsc cannot see past.
 *
 * WS-53 CRM-X4 (`crm_platform.md` section 19.8) adds a filter over the stream
 * (`filterTimeline`), the due date of a task with its "Overdue" chip
 * (`taskDueChips`, drawn by `TaskMeta`), and the shared field primitives in
 * the composer. `TimelineItem` is one row, and the Overview tab draws the next
 * open activities with it.
 */

import FilterPills from "@/components/FilterPills";
import Icon from "@/components/Icon";
import { TaskMeta } from "@/components/TaskMeta";
import Button from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { useEffect, useRef, useState } from "react";
import Badge from "@/components/ui/Badge";
import { ONLY_IN_METORITE, onlyInMetorite } from "../lib/connect";
import { dateTime, dwellLabel } from "../lib/format";
import { afterDialogsClose, browserHost } from "../lib/shellJobs";
import {
  entryKind,
  filterTimeline,
  TIMELINE_FILTERS,
  taskDueChips,
  threadStatusLabel,
  timelineBranch,
  timelineKey,
  type TimelineFilter,
} from "../lib/timeline";
import type { TimelineEntry } from "../lib/types";

type Composable = "note" | "call" | "meeting" | "task";

const COMPOSERS: { id: Composable; label: string; icon: string }[] = [
  { id: "note", label: "Note", icon: "MessageSquare" },
  { id: "task", label: "Task", icon: "ListTodo" },
  { id: "call", label: "Call", icon: "Phone" },
  { id: "meeting", label: "Meeting", icon: "Users" },
];

export default function Timeline({
  entries,
  onLog,
  onToggleTask,
  saving,
  parentMirrored = false,
  now,
  callRequest = 0,
}: {
  /**
   * The `log-call` job of the command bar (CRM-X2). A number other than 0
   * sets the composer to Call and focuses it. Each new number asks again.
   */
  callRequest?: number;
  /** The record is owned by Zoho, so Metorite-only entries get a badge. */
  parentMirrored?: boolean;
  /** Today, for the due dates. A test passes a fixed one. */
  now?: Date;
  entries: TimelineEntry[];
  onLog: (body: {
    type: Composable;
    subject?: string;
    body?: string;
    due_at?: string;
  }) => void;
  onToggleTask: (activityId: string, completed: boolean) => void;
  saving: boolean;
}) {
  const [kind, setKind] = useState<Composable>(callRequest ? "call" : "note");
  // A new ask while the Timeline is already open. React's rule for state that
  // follows a prop: compare during the render, not in an effect.
  const [askSeen, setAskSeen] = useState(callRequest);
  if (callRequest !== askSeen) {
    setAskSeen(callRequest);
    if (callRequest) setKind("call");
  }
  const composer = useRef<HTMLTextAreaElement>(null);
  // The command bar gives focus back when it has closed, so the composer
  // waits for it. Its own record sheet is a dialog too, and does not count.
  useEffect(() => {
    if (!callRequest) return;
    return afterDialogsClose(
      () => composer.current?.focus(),
      browserHost(composer.current),
    );
  }, [callRequest]);
  const [draft, setDraft] = useState("");
  const [due, setDue] = useState("");
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const shown = filterTimeline(entries, filter);
  const filterLabel = TIMELINE_FILTERS.find((f) => f.id === filter)?.label ?? "";

  function submit() {
    const value = draft.trim();
    if (!value) return;
    onLog({
      type: kind,
      // A note's text is its body; a task's is what you have to do, which is
      // the subject — that is the field the due-date list reads.
      ...(kind === "task"
        ? { subject: value, due_at: due ? new Date(due).toISOString() : undefined }
        : { body: value }),
    });
    setDraft("");
    setDue("");
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border p-3">
        <div className="mb-2 flex items-center gap-1">
          {COMPOSERS.map(({ id, label, icon }) => (
            <button
              key={id}
              onClick={() => setKind(id)}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs tech-transition ${
                kind === id
                  ? "bg-primary text-primary-foreground font-medium"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              }`}
            >
              <Icon name={icon} className="w-3 h-3" />
              {label}
            </button>
          ))}
        </div>
        {/* The shared primitives, never raw fields (design rule 3, CRM-X4). */}
        <Textarea
          ref={composer}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          aria-label={kind === "task" ? "The task" : "What happened"}
          placeholder={
            kind === "task" ? "What needs doing?" : "What happened?"
          }
        />
        <div className="mt-2 flex items-center gap-2">
          {kind === "task" && (
            <Input
              type="date"
              inputSize="sm"
              aria-label="Due date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              className="w-40"
            />
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={submit}
            loading={saving}
            disabled={!draft.trim()}
            className="ml-auto"
          >
            Log
          </Button>
        </div>
      </div>

      {/* The filter of the stream (CRM-X4 item 3). Each kind shows its count. */}
      <FilterPills
        items={TIMELINE_FILTERS.map((f) => ({
          id: f.id,
          label: f.label,
          count: f.id === "all" ? entries.length : entries.filter((e) => entryKind(e) === f.id).length,
        }))}
        activeId={filter}
        onChange={(id) => setFilter(id as TimelineFilter)}
      />

      <div className="flex-1 overflow-y-auto p-3">
        {shown.length === 0 && (
          <p className="py-8 text-center text-xs text-muted-foreground">
            {entries.length === 0
              ? "Nothing logged yet."
              : `No ${filterLabel.toLowerCase()} on this record.`}
          </p>
        )}
        <ol className="space-y-3">
          {shown.map((entry, index) => (
            <li key={timelineKey(entry, index)}>
              <TimelineItem
                entry={entry}
                onToggleTask={onToggleTask}
                parentMirrored={parentMirrored}
                now={now}
              />
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/**
 * One entry, routed to its renderer by the pure `timelineBranch`.
 *
 * The `"unknown"` arm renders nothing on purpose. It is reachable only when the
 * gateway sends a kind this build does not know, or a kind with no payload —
 * and in that state the two alternatives are worse: an empty row reads as data
 * loss on a record somebody is trying to trust, and falling through to
 * `ActivityEntry` throws on `entry.activity!`.
 */
export function TimelineItem({
  entry,
  onToggleTask,
  parentMirrored,
  now,
}: {
  parentMirrored: boolean;
  entry: TimelineEntry;
  onToggleTask: (activityId: string, completed: boolean) => void;
  now?: Date;
}) {
  switch (timelineBranch(entry)) {
    case "status_change":
      return <StatusEntry entry={entry} />;
    case "email_thread":
      return <EmailEntry entry={entry} />;
    case "activity":
      return (
        <ActivityEntry
          entry={entry}
          onToggleTask={onToggleTask}
          parentMirrored={parentMirrored}
          now={now}
        />
      );
    default:
      return null;
  }
}

/** The inherited-history marker. A deal's own words and its lead's are both
 *  true; pretending the lead's happened to the deal is not. */
function Origin({ origin }: { origin: TimelineEntry["origin"] }) {
  if (origin !== "lead") return null;
  return (
    <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
      from the lead
    </span>
  );
}

function StatusEntry({ entry }: { entry: TimelineEntry }) {
  const change = entry.status_change!;
  // dwell_seconds is the part a human actually reads off a timeline ("sat in
  // Proposal for 11 days"), which is why status changes come from the funnel
  // log rather than from the activity that mirrors them.
  const dwell = dwellLabel(change.dwell_seconds);
  return (
    <div className="flex items-start gap-2">
      <Icon name="ArrowRight" className="mt-0.5 w-3.5 h-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-xs text-foreground">
          {change.from_status ?? "—"} <span className="opacity-50">→</span>{" "}
          <span className="font-medium">{change.to_status}</span>
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>{dateTime(change.changed_at)}</span>
          <span>·</span>
          <span>{change.changed_by}</span>
          {dwell && (
            <>
              <span>·</span>
              <span>after {dwell}</span>
            </>
          )}
          <Origin origin={entry.origin} />
        </p>
      </div>
    </div>
  );
}

/**
 * One email conversation — the newest message in it, standing for the thread.
 *
 * Read-only, and there is no reply affordance on purpose: this timeline shows
 * mail the caller can already read in the email app, and a compose box here
 * would be a second, unsupervised way to send from their mailbox.
 */
function EmailEntry({ entry }: { entry: TimelineEntry }) {
  const thread = entry.email_thread;
  // Never `entry.email_thread!`: the `!` is exactly what let a payload-less
  // entry reach `ActivityEntry` and throw, and tsc cannot see past it.
  if (!thread) return null;
  const status = threadStatusLabel(thread.status);

  return (
    <div className="flex items-start gap-2">
      <Icon name="Mail" className="mt-0.5 w-3.5 h-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs text-foreground">
          {thread.subject || "(no subject)"}
        </p>
        {thread.snippet && (
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {thread.snippet}
          </p>
        )}
        <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          <span className="uppercase">email</span>
          <span>·</span>
          <span>{dateTime(thread.received_at)}</span>
          {(thread.from_name || thread.from_email) && (
            <>
              <span>·</span>
              <span className="truncate">
                {thread.from_name || thread.from_email}
              </span>
            </>
          )}
          {status && (
            <>
              <span>·</span>
              <span>{status}</span>
            </>
          )}
          <Origin origin={entry.origin} />
        </p>
      </div>
    </div>
  );
}

const ACTIVITY_ICONS: Record<string, string> = {
  note: "MessageSquare",
  call: "Phone",
  meeting: "Users",
  task: "ListTodo",
  status_change: "ArrowRight",
  system: "Circle",
};

function ActivityEntry({
  entry,
  onToggleTask,
  parentMirrored,
  now,
}: {
  parentMirrored: boolean;
  entry: TimelineEntry;
  onToggleTask: (activityId: string, completed: boolean) => void;
  now?: Date;
}) {
  const activity = entry.activity!;
  const iconName = ACTIVITY_ICONS[activity.type] ?? "Circle";
  const isTask = activity.type === "task";
  const done = Boolean(activity.completed_at);

  return (
    <div className="flex items-start gap-2">
      {isTask ? (
        <button
          onClick={() => onToggleTask(activity.id, !done)}
          className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground tech-transition"
          aria-label={done ? "Reopen task" : "Complete task"}
        >
          {done ? (
            <Icon name="CheckCircle2" className="w-3.5 h-3.5 text-success" />
          ) : (
            <Icon name="Circle" className="w-3.5 h-3.5" />
          )}
        </button>
      ) : (
        <Icon name={iconName} className="mt-0.5 w-3.5 h-3.5 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        {activity.subject && (
          <p
            className={`text-xs ${
              done ? "text-muted-foreground line-through" : "text-foreground"
            }`}
          >
            {activity.subject}
          </p>
        )}
        {activity.body && (
          <p className="whitespace-pre-wrap text-xs text-foreground">
            {activity.body}
          </p>
        )}
        {/* The due date of a task, and "Overdue" when it is late (CRM-X4 item 4). */}
        <TaskMeta chips={taskDueChips(activity, now)} className="mt-0.5" />
        <p className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span className="uppercase">{activity.type}</span>
          <span>·</span>
          <span>{dateTime(activity.occurred_at ?? activity.created_at)}</span>
          {activity.created_by && (
            <>
              <span>·</span>
              <span>{activity.created_by}</span>
            </>
          )}
          <Origin origin={entry.origin} />
          {onlyInMetorite(activity, parentMirrored) && (
            <Badge size="xs">{ONLY_IN_METORITE}</Badge>
          )}
        </p>
      </div>
    </div>
  );
}
