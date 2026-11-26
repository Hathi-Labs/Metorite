"use client";

/**
 * The custom React Flow node card — the unit the whole editor is read through,
 * so it follows the mockup's anatomy (docs/workflow-editor/mockup.html §.node):
 *
 *   ▔▔▔▔▔▔▔▔▔  category accent bar (RFC §5.2 — colour is a functional encoding)
 *   [icon]  KIND        ← what this block *is*, in the category hue
 *           Title       ← what the maker called it
 *   summary line(s)     ← what it will actually do, from its config
 *   `detail` · badges   ← the agent / action / expression it is bound to
 *   ─────────────────
 *   ● status · 1.2s     node_id   ← live run state and the {{ref}} root
 *
 * A card has to answer "what does this step do?" without opening the inspector;
 * everything below the title exists for that.
 *
 * The height of the card has a limit. A saved workflow can hold two nodes 160 px
 * apart, and a saved position does not change. So each text row is one line, and
 * the summary takes two lines only when the card has room (`summaryClamp`). The
 * browser fence is `e2e/workflow-card-height.spec.ts`.
 *
 * Status comes from the SSE test run (queued → running → ok/err) or from a
 * recorded run painted onto the canvas. Issue badges come from design-time
 * validation.
 */

import AppIcon from "@/components/Icon";
import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { NODE_CATEGORY_STYLE, categoryForType } from "../lib/types";
import { FALLBACK_ICON, NODE_ICON, NODE_KIND_LABEL } from "../lib/nodeVisuals";
import type { NodeType } from "../lib/types";

export type CCNodeRunStatus =
  | "running"
  | "ok"
  | "error"
  | "skipped"
  | "waiting"
  | "pending";

export type CCNodeData = {
  label: string;
  nodeType: NodeType;
  /** Plain-English sentence: what this step does with its current config. */
  summary?: string;
  /** The capability it is bound to — agent name, tool action, expression. */
  detail?: string;
  /** Short flags worth seeing without selecting the node, e.g. "write". */
  badges?: string[];
  runStatus?: CCNodeRunStatus;
  /** Wall time of the last run of this node, in ms. */
  durationMs?: number;
  issueCount?: number;
  [key: string]: unknown;
};

/** Footer wording per run state — past tense once the node is done. */
const STATUS_LABEL: Record<CCNodeRunStatus, string> = {
  running: "running",
  ok: "done",
  error: "failed",
  skipped: "skipped",
  waiting: "waiting",
  pending: "queued",
};

const STATUS_TONE: Record<CCNodeRunStatus, { dot: string; text: string }> = {
  running: { dot: "bg-primary animate-pulse", text: "text-primary" },
  ok: { dot: "bg-success", text: "text-success" },
  error: { dot: "bg-destructive", text: "text-destructive" },
  skipped: { dot: "bg-muted-foreground/50", text: "text-muted-foreground" },
  waiting: { dot: "bg-warning animate-pulse", text: "text-warning" },
  pending: { dot: "bg-muted-foreground/50", text: "text-muted-foreground" },
};

/** "820ms" / "3.9s" — same scale the run console prints. */
function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

function WorkflowNodeInner(props: NodeProps) {
  const data = props.data as CCNodeData;
  const type = data.nodeType;
  const category = categoryForType(type);
  const style = NODE_CATEGORY_STYLE[category] ?? NODE_CATEGORY_STYLE.logic;
  const Icon = NODE_ICON[type] ?? FALLBACK_ICON;
  const status = data.runStatus;
  const tone = status ? STATUS_TONE[status] : null;
  const issues = data.issueCount ?? 0;
  const badges = data.badges ?? [];
  // A card with a detail row AND a row below it (badges, or the yes and no
  // labels) has room for one line of summary. Each other card has room for two.
  const summaryClamp =
    data.detail && (badges.length > 0 || type === "condition")
      ? "line-clamp-1"
      : "line-clamp-2";

  // Selection, live status and validation all want the card's outline. Run
  // state wins while a run is on screen — that is the thing you are watching.
  const ring =
    status === "running"
      ? "border-primary ring-2 ring-primary/30"
      : status === "ok"
        ? "border-success/50"
        : status === "error"
          ? "border-destructive ring-2 ring-destructive/25"
          : status === "waiting"
            ? "border-warning/60 ring-2 ring-warning/20"
            : props.selected
              ? "border-primary ring-2 ring-primary/25"
              : issues > 0
                ? "border-warning/50"
                : style.border;
  const dim = status === "skipped" || status === "pending" ? "opacity-45" : "";

  const handleCls =
    "!w-3 !h-3 !border-2 !bg-card !border-muted-foreground/70 hover:!border-primary";

  return (
    <div
      className={`w-52 rounded-xl border bg-card shadow-sm overflow-hidden tech-transition ${ring} ${dim}`}
    >
      {/* Category accent — readable when the graph is zoomed past the text */}
      <div className={`h-[3px] w-full ${style.bar}`} />

      {type !== "trigger" && (
        <Handle type="target" position={Position.Top} className={handleCls} />
      )}

      <div className="px-2.5 py-1.5">
        <div className="flex items-start gap-2">
          <span
            className={`w-6 h-6 shrink-0 rounded-lg border grid place-items-center ${style.tile}`}
          >
            <Icon className="w-3.5 h-3.5" />
          </span>
          <div className="min-w-0 flex-1">
            <div
              className={`font-mono text-[10px] uppercase tracking-[0.12em] leading-tight truncate ${style.text}`}
            >
              {NODE_KIND_LABEL[type] ?? type}
            </div>
            <div className="text-xs font-semibold text-foreground leading-snug truncate">
              {data.label || type}
            </div>
          </div>
          <span className="flex items-center gap-1 shrink-0 pt-0.5">
            {issues > 0 && (
              <span
                title={`${issues} validation issue${issues === 1 ? "" : "s"} — see the inspector`}
                className="flex items-center gap-0.5 text-warning"
              >
                <AppIcon name="AlertTriangle" className="w-3 h-3" />
                {issues > 1 && (
                  <span className="text-[10px] font-medium">{issues}</span>
                )}
              </span>
            )}
            {status === "running" && (
              <AppIcon name="Loader2" className="w-3 h-3 text-primary animate-spin" />
            )}
          </span>
        </div>

        {(data.summary || data.detail) && (
          <div className="mt-0.5 space-y-0.5">
            {data.summary && (
              <p
                className={`text-[11px] leading-snug text-muted-foreground ${summaryClamp}`}
                title={data.summary}
              >
                {data.summary}
              </p>
            )}
            {data.detail && (
              <p
                className="font-mono text-[10px] leading-tight text-foreground/80 bg-secondary/60 border border-border/60 rounded px-1.5 py-px truncate"
                title={data.detail}
              >
                {data.detail}
              </p>
            )}
          </div>
        )}

        {badges.length > 0 && (
          <div className="mt-0.5 flex gap-1 overflow-hidden">
            {badges.map((badge) => (
              <span
                key={badge}
                title={badge}
                // One row. A long badge gives way, and a short flag such as
                // "write" stays whole.
                className={`text-[10px] leading-tight uppercase tracking-wide px-1.5 py-px rounded-full border border-border text-muted-foreground ${
                  badge.length > 18 ? "min-w-0 truncate" : "shrink-0 whitespace-nowrap"
                }`}
              >
                {badge}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Footer: run state on the left, the {{ref}} root on the right */}
      <div className="flex items-center gap-1.5 px-2.5 py-0.5 leading-tight whitespace-nowrap border-t border-border/70 bg-secondary/25">
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${tone ? tone.dot : "bg-muted-foreground/40"}`}
        />
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.1em] ${tone ? tone.text : "text-muted-foreground/70"}`}
        >
          {status ? STATUS_LABEL[status] : "idle"}
        </span>
        {typeof data.durationMs === "number" && data.durationMs >= 0 && (
          <span className="font-mono text-[10px] text-muted-foreground/70">
            · {formatDuration(data.durationMs)}
          </span>
        )}
        <span
          className="ml-auto font-mono text-[10px] text-muted-foreground/60 truncate max-w-[46%]"
          title={`Reference this block's output as {{${props.id}.field}}`}
        >
          {props.id}
        </span>
      </div>

      {type === "condition" ? (
        <>
          <Handle
            id="true"
            type="source"
            position={Position.Bottom}
            style={{ left: "28%" }}
            className="!w-3 !h-3 !border-2 !bg-card !border-success"
          />
          <Handle
            id="false"
            type="source"
            position={Position.Bottom}
            style={{ left: "72%" }}
            className="!w-3 !h-3 !border-2 !bg-card !border-muted-foreground"
          />
          <div className="flex justify-between px-4 pb-0.5 leading-tight">
            <span className="font-mono text-[10px] uppercase tracking-wider text-success">
              yes
            </span>
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              no
            </span>
          </div>
        </>
      ) : type !== "output" ? (
        <Handle type="source" position={Position.Bottom} className={handleCls} />
      ) : null}
    </div>
  );
}

export default memo(WorkflowNodeInner);
