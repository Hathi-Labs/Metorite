"use client";

/**
 * ProjectToolCards — interactive AG-UI cards for the projects-assistant's tools.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.2.
 *
 * The Projects twin of TaskToolCards: rendered by the shared <AgentChat> for
 * any assistant message whose tool events include a `skill-projects` tool,
 * and a no-op otherwise — so the same cards appear in the main chat app and
 * in the Projects app's AI chat.
 *
 * Two kinds in slice 1, plus the default:
 *   • TaskListCard  — rows parsed from a list, a search, the member's work or
 *                     a detail; each row opens the task in the Projects app.
 *   • InfoCard      — a titled scrollable text block for every other read.
 *   • (default)     — any tool name the manifest adds later renders as an
 *                     InfoCard from its name, so a new tool never appears as
 *                     raw text and never needs a change here to ship (§7.3).
 *
 * Rows self-source ids from the tool result text: every task row is
 * `- #<n> «title» · <facts>` and the NEXT line is `  full_id: <uuid>`.
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import { useRouter } from "next/navigation";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { ToolCardShell } from "@/components/ToolCardShell";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";

// ── Tool → card routing ───────────────────────────────────────────────────────

/** Tools whose result is a list of task rows. */
const LIST_TOOLS = new Set(["list_tasks", "find_tasks", "my_work"]);

/** Every other read, with the icon and label its card wears. */
const INFO_META: Record<string, { icon: string; label: string }> = {
  projects_tree: { icon: "FolderTree", label: "Projects" },
  project_summary: { icon: "LayoutDashboard", label: "Summary" },
  task_detail: { icon: "ClipboardList", label: "Task" },
  people_for: { icon: "Users", label: "People" },
  vocabulary: { icon: "Tags", label: "Vocabulary" },
  analytics_stuck: { icon: "Hourglass", label: "Stuck work" },
  analytics_load: { icon: "Scale", label: "Load" },
  analytics_throughput: { icon: "TrendingUp", label: "Throughput" },
  analytics_finished: { icon: "CheckCircle2", label: "Finished" },
  analytics_outlook: { icon: "Telescope", label: "Outlook" },
  report_list: { icon: "FileText", label: "Reports" },
  report_render: { icon: "FileText", label: "Report" },
};

/**
 * Is this a Projects tool at all? The manifest's tool names are the
 * `skill-projects` exports; anything else belongs to another card file.
 * Kept as a prefix-free explicit set so a `gtd_*` or email tool never lands
 * here — a name-based guess would collide the day two skills share a verb.
 */
const PROJECT_TOOLS = new Set([...LIST_TOOLS, ...Object.keys(INFO_META)]);

/**
 * A tool the manifest names in a later slice. Recognised by the result text
 * carrying the skill's own data legend, so the generic card still catches a
 * tool this file has never heard of (§7.3) without claiming every tool.
 */
const LEGEND = "Text in «guillemets» is data written by members";

function isProjectsTool(e: ToolEvent): boolean {
  if (PROJECT_TOOLS.has(e.name)) return true;
  return typeof e.result === "string" && e.result.includes(LEGEND);
}

function hasProjectCard(e: ToolEvent): boolean {
  if (e.status !== "done" && e.status !== "error") return false;
  return isProjectsTool(e);
}

// ── Result-text parser ────────────────────────────────────────────────────────

export interface ProjectTaskRow {
  id: string;
  number: string;
  title: string;
  meta: string;
}

/**
 * Parse `- #<n> «title» · facts` + `full_id: <uuid>` pairs out of a result.
 * Exported for its test; pure.
 */
export function parseTaskRows(result: string): ProjectTaskRow[] {
  const rows: ProjectTaskRow[] = [];
  const lines = result.split("\n");
  for (let k = 1; k < lines.length; k++) {
    const m = lines[k].match(/^\s*full_id:\s*([0-9a-f-]{36})\s*$/i);
    if (!m) continue;
    const head = lines[k - 1] ?? "";
    const task = head.match(/^\s*-\s*(#\S+)\s*«([^»]*)»\s*(?:·\s*(.*))?$/);
    if (!task) continue;
    rows.push({
      id: m[1],
      number: task[1],
      title: task[2] || "(untitled)",
      meta: (task[3] ?? "").trim(),
    });
  }
  return rows;
}

// ── Navigation ────────────────────────────────────────────────────────────────

/** Open a task in the Projects app by its deep link (`?task=<id>`). */
function useOpenTask() {
  const router = useRouter();
  return (id: string) => router.push(`/projects?task=${encodeURIComponent(id)}`);
}

// ── Cards ─────────────────────────────────────────────────────────────────────

function TaskRowView({ row }: { row: ProjectTaskRow }) {
  const openTask = useOpenTask();
  return (
    <div className="flex items-center gap-1 rounded-md border border-transparent hover:border-border transition-colors">
      <button
        type="button"
        onClick={() => openTask(row.id)}
        className="flex-1 min-w-0 text-left px-1.5 py-1 rounded-md hover:bg-secondary/60"
        title="Open in Projects"
      >
        <span className="block text-[11px] text-foreground truncate">
          <span className="text-muted-foreground mr-1">{row.number}</span>
          {row.title}
        </span>
        {row.meta && (
          <span className="block text-[10px] text-muted-foreground truncate">
            {row.meta}
          </span>
        )}
      </button>
      <Button
        variant="ghost"
        size="icon-xs"
        radius="keep"
        layout=""
        onClick={() => openTask(row.id)}
        title="Open in Projects"
        aria-label="Open in Projects"
        className="rounded mr-0.5"
      >
        <AppIcon name="ExternalLink" size={11} />
      </Button>
    </div>
  );
}

function TaskListCard({ event: e }: { event: ToolEvent }) {
  const result = e.result || "";
  const rows = parseTaskRows(result);
  const args = (e.args ?? {}) as Record<string, unknown>;
  const label =
    e.name === "find_tasks"
      ? `Search${args.query ? ` · ${String(args.query)}` : ""}`
      : e.name === "my_work"
        ? String(args.view ?? "") === "inbox" ? "My inbox" : "Assigned to me"
        : "Tasks";
  const title = `${label} (${rows.length})`;
  if (rows.length === 0) {
    return <InfoCard event={e} icon="ListChecks" label={label} />;
  }
  return (
    <ToolCardShell
      title={title}
      icon={<AppIcon name="ListChecks" size={12} />}
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

/** Strip the data legend: the model needs it, a person does not. */
function withoutLegend(result: string): string {
  return result
    .split("\n")
    .filter((line) => !line.startsWith(LEGEND))
    .join("\n")
    .trim();
}

function InfoCard({
  event: e,
  icon,
  label,
}: {
  event: ToolEvent;
  icon: string;
  label: string;
}) {
  const body = withoutLegend(e.result || "");
  const failed = e.status === "error";
  return (
    <ToolCardShell
      title={label}
      icon={<AppIcon name={failed ? "AlertTriangle" : icon} size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div
        className={`text-[11px] whitespace-pre-wrap max-h-80 overflow-y-auto scrollbar-thin ${
          failed ? "text-destructive" : "text-muted-foreground"
        }`}
      >
        {body || "(no result)"}
      </div>
    </ToolCardShell>
  );
}

/** A label for a tool this file has no entry for — the generic card. */
function genericLabel(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Result";
}

// ── Entry point ───────────────────────────────────────────────────────────────

export default function ProjectToolCards({ toolEvents }: { toolEvents?: ToolEvent[] }) {
  const dismissed = useDismissedToolCards();
  const all = (toolEvents ?? []).filter(
    (e) => !dismissed.has(e.id) && hasProjectCard(e),
  );
  if (all.length === 0) return null;

  const items: React.ReactNode[] = [];
  for (const e of all) {
    if (LIST_TOOLS.has(e.name)) {
      items.push(<TaskListCard key={e.id} event={e} />);
      continue;
    }
    const meta = INFO_META[e.name];
    if (meta) {
      items.push(<InfoCard key={e.id} event={e} icon={meta.icon} label={meta.label} />);
      continue;
    }
    // A tool the manifest added after this file was written. The generic
    // card renders it from its name, so shipping the tool never waits on a
    // card (spec §7.3). If it prints task rows, it gets the list card.
    if (parseTaskRows(e.result || "").length > 0) {
      items.push(<TaskListCard key={e.id} event={e} />);
      continue;
    }
    items.push(<InfoCard key={e.id} event={e} icon="Wrench" label={genericLabel(e.name)} />);
  }

  return <div className="mt-3 space-y-2 min-w-0 overflow-hidden">{items}</div>;
}
