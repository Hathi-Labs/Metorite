"use client";

/**
 * TaskToolCards — interactive AG-UI cards for the task-manager agent's tools.
 *
 * The task-app twin of EmailToolCards: rendered by the shared <AgentChat> for
 * any assistant message whose tool events include `my_tasks_*` tools, and a no-op
 * otherwise — so the rich task cards appear in BOTH the main chat app and the
 * Tasks app's Assistant rail.
 *
 * Card types:
 *   • TaskListCard    — clickable task rows (my_tasks_list / my_tasks_list_schedule):
 *                       open the task in the Tasks app, or check it off inline
 *   • PlanResultCard  — a proposed/applied day plan (my_tasks_plan_day / replan /
 *                       rollover) with a one-click Apply for proposals
 *   • InfoResultCard  — titled scrollable text (insights, digest, people,
 *                       accounts, projects, detail, clarify proposal…)
 *   • ActionResultCard— confirmation for every mutating my_tasks_* tool, with an
 *                       "open in Tasks" jump when the result names an item
 *
 * Rows self-source item ids from the tool result text — every my_tasks_* tool prints
 * `full_id: <uuid>` (lists) or `(id: <uuid>)` (schedules) for exactly this.
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import { useState } from "react";
import { useRouter } from "next/navigation";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { apiPatchItem, apiAgentPlanToday } from "@/app/tasks/lib/api";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import { ToolCardShell, DismissableCard } from "@/components/ToolCardShell";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";

// ── Tool → card routing ───────────────────────────────────────────────────────

/**
 * LEGACY TOOL NAMES — for stored chat history only (my_tasks_cutover.md S9).
 *
 * S9 renamed the 29 chat tools from `gtd_*` to `my_tasks_*`. A chat message
 * saved before that still carries the old name in its tool events, and it must
 * still render its card. So this map turns each old name into its new name
 * before the router below reads it. The router knows only the new names.
 *
 * Do not add a name here, and do not use one anywhere else. This is the one
 * file in `src/` that may spell the old name, and
 * `tests/unit/test_no_gtd_table_names.py` fails on any other.
 */
export const LEGACY_TOOL_NAMES: Readonly<Record<string, string>> = {
  gtd_accounts: "my_tasks_accounts",
  gtd_add_subtasks: "my_tasks_add_subtasks",
  gtd_archive: "my_tasks_archive",
  gtd_capture: "my_tasks_capture",
  gtd_capture_many: "my_tasks_capture_many",
  gtd_clarify: "my_tasks_clarify",
  gtd_complete: "my_tasks_complete",
  gtd_day_digest: "my_tasks_day_digest",
  gtd_delegate: "my_tasks_delegate",
  gtd_detail: "my_tasks_detail",
  gtd_estimate_stats: "my_tasks_estimate_stats",
  gtd_inbox_insights: "my_tasks_inbox_insights",
  gtd_list: "my_tasks_list",
  gtd_list_projects: "my_tasks_list_projects",
  gtd_list_schedule: "my_tasks_list_schedule",
  gtd_move: "my_tasks_move",
  gtd_organize: "my_tasks_organize",
  gtd_people: "my_tasks_people",
  gtd_plan_day: "my_tasks_plan_day",
  gtd_plan_project: "my_tasks_plan_project",
  gtd_replan_day: "my_tasks_replan_day",
  gtd_rollover: "my_tasks_rollover",
  gtd_schedule: "my_tasks_schedule",
  gtd_set_one_thing: "my_tasks_set_one_thing",
  gtd_set_stage: "my_tasks_set_stage",
  gtd_subtasks: "my_tasks_subtasks",
  gtd_sync: "my_tasks_sync",
  gtd_unschedule: "my_tasks_unschedule",
  gtd_update: "my_tasks_update",
};

/** The current name of a tool. A stored old name maps to its new name. */
export function currentToolName(name: string): string {
  return Object.hasOwn(LEGACY_TOOL_NAMES, name) ? LEGACY_TOOL_NAMES[name] : name;
}

const LIST_TOOL = "my_tasks_list";
const SCHEDULE_TOOL = "my_tasks_list_schedule";

/** The AI day-planner tools — result is a block timeline; a proposal gets an
 *  Apply button that commits it via the same endpoint the agent would use. */
const PLAN_META: Record<string, { label: string; kind: "plan-today" | "replan-today" | "rollover-today" }> = {
  my_tasks_plan_day: { label: "Day plan", kind: "plan-today" },
  my_tasks_replan_day: { label: "Replanned day", kind: "replan-today" },
  my_tasks_rollover: { label: "Rollover", kind: "rollover-today" },
};

/** Read-only tools that return a titled text blob. */
const INFO_META: Record<string, { icon: string; label: string }> = {
  my_tasks_inbox_insights: { icon: "Inbox", label: "Inbox health" },
  my_tasks_day_digest: { icon: "CalendarDays", label: "Day summary" },
  my_tasks_estimate_stats: { icon: "Timer", label: "Estimate accuracy" },
  my_tasks_accounts: { icon: "Milestone", label: "No connected tool" },
  my_tasks_people: { icon: "Users", label: "People" },
  my_tasks_list_projects: { icon: "FolderKanban", label: "Projects" },
  my_tasks_subtasks: { icon: "ListTree", label: "Subtasks" },
  my_tasks_detail: { icon: "ClipboardList", label: "Task detail" },
  my_tasks_clarify: { icon: "Sparkles", label: "Clarify proposal" },
};
const INFO_TOOLS = new Set(Object.keys(INFO_META));

/** Friendly label + icon for the generic confirmation card (mutating tools). */
const ACTION_META: Record<string, { icon: string; label: string; danger?: boolean }> = {
  my_tasks_capture: { icon: "Inbox", label: "Captured to inbox" },
  my_tasks_capture_many: { icon: "Inbox", label: "Captured to inbox" },
  my_tasks_organize: { icon: "ListChecks", label: "Organized" },
  my_tasks_update: { icon: "PenLine", label: "Task updated" },
  my_tasks_complete: { icon: "CheckCircle2", label: "Task completed" },
  my_tasks_move: { icon: "Milestone", label: "Task moved" },
  my_tasks_set_stage: { icon: "Milestone", label: "Stage changed" },
  my_tasks_delegate: { icon: "Send", label: "Delegated" },
  my_tasks_add_subtasks: { icon: "ListTree", label: "Subtasks added" },
  my_tasks_archive: { icon: "Trash2", label: "Archived", danger: true },
  my_tasks_schedule: { icon: "CalendarClock", label: "Scheduled" },
  my_tasks_unschedule: { icon: "CalendarClock", label: "Unscheduled" },
  my_tasks_set_one_thing: { icon: "Star", label: "One Thing set" },
  my_tasks_sync: { icon: "RefreshCw", label: "No connected tool" },
  my_tasks_plan_project: { icon: "FolderKanban", label: "Project plan" },
};

/** Context-sensitive relabels driven by the call's args. */
function actionMeta(e: ToolEvent): { icon: string; label: string; danger?: boolean } {
  const args = (e.args ?? {}) as Record<string, unknown>;
  if (e.name === "my_tasks_complete" && args.undo)
    return { icon: "RefreshCw", label: "Task reopened" };
  if (e.name === "my_tasks_archive" && args.restore)
    return { icon: "RefreshCw", label: "Task restored" };
  if (e.name === "my_tasks_set_one_thing" && !String(args.item_id ?? "").trim())
    return { icon: "Star", label: "One Thing cleared" };
  return ACTION_META[e.name] ?? { icon: "Wrench", label: e.name.replace(/_/g, " ") };
}

/** True when a tool of this (current) name draws a task card. */
export function isTaskCardTool(name: string): boolean {
  return (
    name === LIST_TOOL ||
    name === SCHEDULE_TOOL ||
    name in PLAN_META ||
    INFO_TOOLS.has(name) ||
    name in ACTION_META
  );
}

function hasTaskCard(e: ToolEvent): boolean {
  if (e.status !== "done" && e.status !== "error") return false;
  return isTaskCardTool(e.name);
}

// ── Result-text parsers ───────────────────────────────────────────────────────
// my_tasks_list prints each item as `[DISP·SRC] "Title" · meta · id=…` followed by
// an indented `full_id: <uuid>` line; my_tasks_list_schedule prints
// `• <when> <title> (id: <uuid>)`. Both exist precisely so cards (and the
// agent) can address items — parse, don't guess.

interface TaskRow {
  id: string;
  title: string;
  meta: string;
  done?: boolean;
}

function parseListRows(result: string): TaskRow[] {
  const rows: TaskRow[] = [];
  const lines = result.split("\n");
  for (let k = 1; k < lines.length; k++) {
    const m = lines[k].match(/^\s*full_id:\s*(\S+)\s*$/);
    if (!m) continue;
    const head = lines[k - 1] ?? "";
    const tagM = head.match(/^\s*\[([^\]]+)\]\s*/);
    const rest = head.slice(tagM?.[0]?.length ?? 0);
    const title = rest.match(/^"([^"]*)"/)?.[1] ?? rest.trim();
    const metaBits = rest
      .replace(/^"[^"]*"\s*·?\s*/, "")
      .split(" · ")
      .map((s) => s.trim())
      .filter((s) => s && !s.startsWith("id="));
    const tag = tagM?.[1] ?? "";
    rows.push({
      id: m[1],
      title: title || "(untitled)",
      meta: [tag, ...metaBits].filter(Boolean).join(" · "),
      done: tag.startsWith("DONE"),
    });
  }
  return rows;
}

function parseScheduleRows(result: string): TaskRow[] {
  const rows: TaskRow[] = [];
  for (const line of result.split("\n")) {
    const m = line.match(/^\s*•\s*(.+?)\s*\(id:\s*([^)\s]+)\)\s*$/);
    if (m) rows.push({ id: m[2], title: m[1], meta: "", done: m[1].includes("✓done") });
  }
  return rows;
}

// ── Navigation helper ─────────────────────────────────────────────────────────

/** Open a task in the Tasks app: select it (the detail modal keys off the
 *  store) and route to /tasks — a no-op route change inside the Tasks app,
 *  navigation from the chat app. */
function useOpenTask() {
  const router = useRouter();
  return (id: string) => {
    try {
      useTaskStore.getState().openFocus(id);
    } catch {
      /* store unavailable — still navigate */
    }
    router.push("/tasks");
  };
}

// ── Task list card ────────────────────────────────────────────────────────────

function TaskRowView({ row }: { row: TaskRow }) {
  const openTask = useOpenTask();
  const [state, setState] = useState<"idle" | "busy" | "done">(
    row.done ? "done" : "idle",
  );
  const complete = async () => {
    setState("busy");
    try {
      await apiPatchItem(row.id, { disposition: "DONE" });
      setState("done");
    } catch {
      setState("idle");
    }
  };
  const done = state === "done";
  return (
    <div className="flex items-center gap-1 rounded-md border border-transparent hover:border-border transition-colors">
      <button
        onClick={() => openTask(row.id)}
        className="flex-1 min-w-0 text-left px-1.5 py-1 rounded-md hover:bg-secondary/60"
        title="Open in My Tasks"
      >
        <span
          className={`block text-[11px] truncate ${
            done ? "line-through text-muted-foreground" : "text-foreground"
          }`}
        >
          {row.title}
        </span>
        {row.meta && (
          <span className="block text-[10px] text-muted-foreground truncate">
            {row.meta}
          </span>
        )}
      </button>
      <div className="flex items-center flex-shrink-0 mr-0.5">
        {done ? (
          <span className="flex items-center gap-0.5 text-[9px] text-emerald-500 px-1">
            <AppIcon name="CheckCircle2" size={10} /> Done
          </span>
        ) : state === "busy" ? (
          <AppIcon name="Loader2" size={12} className="animate-spin text-muted-foreground mx-1" />
        ) : (
          <>
            <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={complete} title="Mark done" aria-label="Mark done" className="rounded">
              <AppIcon name="CheckCircle2" size={11} />
            </Button>
            <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={() => openTask(row.id)} title="Open in My Tasks" aria-label="Open in My Tasks" className="rounded">
              <AppIcon name="ExternalLink" size={11} />
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TaskListCard({ event: e }: { event: ToolEvent }) {
  const result = e.result || "";
  const rows =
    e.name === SCHEDULE_TOOL ? parseScheduleRows(result) : parseListRows(result);
  const args = (e.args ?? {}) as Record<string, unknown>;
  const view = String(args.view ?? "").trim();
  const title =
    e.name === SCHEDULE_TOOL
      ? `Scheduled (${rows.length})`
      : `My Tasks${view ? ` · ${view}` : ""} (${rows.length})`;
  // Nothing parseable (e.g. "No items in inbox.") → plain text info card.
  if (rows.length === 0) {
    return (
      <ToolCardShell
        title={title}
        icon={<AppIcon name="ListChecks" size={12} />}
        onDismiss={() => dismissToolCard(e.id)}
      >
        <div className="text-[11px] text-muted-foreground whitespace-pre-wrap">
          {result.trim() || "(no result)"}
        </div>
      </ToolCardShell>
    );
  }
  return (
    <ToolCardShell
      title={title}
      icon={e.name === SCHEDULE_TOOL ? <AppIcon name="CalendarClock" size={12} /> : <AppIcon name="ListChecks" size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="space-y-0.5 max-h-80 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {rows.map((r) => (
          <TaskRowView key={r.id} row={r} />
        ))}
      </div>
    </ToolCardShell>
  );
}

// ── Day-plan card ─────────────────────────────────────────────────────────────

/** A proposed or applied day plan. Proposals carry a one-click Apply that
 *  commits via the agent-facing endpoint (identical to the user saying
 *  "apply it"), so plan → commit is a single gesture. */
function PlanResultCard({ event: e }: { event: ToolEvent }) {
  const meta = PLAN_META[e.name];
  const args = (e.args ?? {}) as Record<string, unknown>;
  const result = (e.result || "").trim();
  const wasProposal = !args.apply && /^Proposed plan/i.test(result);
  const [state, setState] = useState<"idle" | "busy" | "applied" | "failed">(
    "idle",
  );
  const apply = async () => {
    setState("busy");
    try {
      await apiAgentPlanToday(
        meta.kind,
        typeof args.energy_note === "string" ? args.energy_note : undefined,
      );
      setState("applied");
    } catch {
      setState("failed");
    }
  };
  // Drop the instruction header — the card chrome carries the state.
  const body = result
    .split("\n")
    .filter((l) => !/^(Proposed plan|Applied —)/i.test(l.trim()))
    .join("\n")
    .trim();
  const applied = state === "applied" || !wasProposal;
  return (
    <ToolCardShell
      title={`${meta.label} ${applied ? "· applied" : "· proposed"}`}
      icon={<AppIcon name="CalendarDays" size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="text-[11px] whitespace-pre-wrap break-words text-foreground/90 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {body || "(no blocks)"}
      </div>
      {wasProposal && (
        <div className="mt-2 flex items-center gap-2">
          {state === "applied" ? (
            <span className="flex items-center gap-1 text-[10px] text-emerald-500">
              <AppIcon name="CheckCircle2" size={11} /> Applied — calendar updated
            </span>
          ) : state === "failed" ? (
            <span className="flex items-center gap-1 text-[10px] text-destructive">
              <AppIcon name="X" size={11} /> Couldn&apos;t apply — try again
            </span>
          ) : null}
          {state !== "applied" && (
            <button
              onClick={apply}
              disabled={state === "busy"}
              className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-2 py-1 text-[10px] font-medium text-primary hover:bg-primary/20 disabled:opacity-60"
            >
              {state === "busy" ? (
                <AppIcon name="Loader2" size={10} className="animate-spin" />
              ) : (
                <AppIcon name="Zap" size={10} />
              )}
              Apply plan
            </button>
          )}
        </div>
      )}
    </ToolCardShell>
  );
}

// ── Info card ─────────────────────────────────────────────────────────────────

function InfoResultCard({ event: e }: { event: ToolEvent }) {
  const meta = INFO_META[e.name];
  const iconName = meta.icon;
  const text = (e.result || "").trim();
  const openTask = useOpenTask();
  const args = (e.args ?? {}) as Record<string, unknown>;
  const itemId = typeof args.item_id === "string" ? args.item_id : "";
  return (
    <ToolCardShell
      title={meta.label}
      icon={<AppIcon name={iconName} size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="text-[11px] whitespace-pre-wrap break-words text-foreground/90 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {text || "(no result)"}
      </div>
      {itemId && (
        <button
          onClick={() => openTask(itemId)}
          className="mt-1.5 inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
        >
          <AppIcon name="ExternalLink" size={10} /> Open in My Tasks
        </button>
      )}
    </ToolCardShell>
  );
}

// ── Generic action card ───────────────────────────────────────────────────────

/** Confirmation card for a mutating my_tasks_* tool — icon + label + result summary,
 *  with a jump to the task when the result names one. */
function ActionResultCard({ event: e }: { event: ToolEvent }) {
  const meta = actionMeta(e);
  const failed = e.status === "error";
  const iconName = failed ? "X" : meta.icon;
  const result = (e.result || "").trim();
  // Every _fmt_item line ends with `full_id: <uuid>`; capture results print
  // `(id: <uuid>)` instead — either is the jump target, args as last resort.
  const itemId =
    result.match(/full_id:\s*(\S+)/)?.[1] ??
    result.match(/\(id:\s*([^)\s]+)\)/)?.[1] ??
    ((e.args as Record<string, unknown> | undefined)?.item_id as string | undefined) ??
    "";
  const openTask = useOpenTask();
  // The summary without the plumbing lines (full_id / id=…).
  const detailLines = result
    .split("\n")
    .filter((l) => !/^\s*full_id:/.test(l))
    .join("\n")
    .trim();
  const detail =
    detailLines.length > 200 ? detailLines.slice(0, 200) + "…" : detailLines;

  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${
        failed
          ? "border-destructive/40 bg-destructive/5"
          : meta.danger
            ? "border-amber-500/40 bg-amber-500/5"
            : "border-sidebar-border bg-secondary/40"
      }`}
    >
      <div className="flex items-start gap-2">
        <span
          className={`mt-0.5 flex-shrink-0 ${
            failed ? "text-destructive" : meta.danger ? "text-amber-500" : "text-emerald-500"
          }`}
        >
          <AppIcon name={iconName} size={13} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium text-foreground">{meta.label}</div>
          {detail && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap line-clamp-3">
              {detail}
            </div>
          )}
          {!failed && itemId && (
            <button
              onClick={() => openTask(itemId)}
              className="mt-1 inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
            >
              <AppIcon name="ExternalLink" size={10} /> Open in My Tasks
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Public component ──────────────────────────────────────────────────────────

/**
 * Render rich, interactive cards for the task-manager tool calls in a message.
 * Returns null when there are none (inert for non-task agents).
 */
export default function TaskToolCards({ toolEvents }: { toolEvents?: ToolEvent[] }) {
  const dismissed = useDismissedToolCards();
  const all = (toolEvents ?? [])
    .map((e) => {
      const name = currentToolName(e.name);
      return name === e.name ? e : { ...e, name };
    })
    .filter((e) => !dismissed.has(e.id) && hasTaskCard(e));
  if (all.length === 0) return null;

  const items: React.ReactNode[] = [];
  for (const e of all) {
    if (e.name === LIST_TOOL || e.name === SCHEDULE_TOOL) {
      items.push(<TaskListCard key={e.id} event={e} />);
      continue;
    }
    if (e.name in PLAN_META) {
      items.push(<PlanResultCard key={e.id} event={e} />);
      continue;
    }
    if (INFO_TOOLS.has(e.name)) {
      items.push(<InfoResultCard key={e.id} event={e} />);
      continue;
    }
    items.push(
      <DismissableCard key={e.id} onDismiss={() => dismissToolCard(e.id)}>
        <ActionResultCard event={e} />
      </DismissableCard>,
    );
  }

  return <div className="mt-3 space-y-2 min-w-0 overflow-hidden">{items}</div>;
}
