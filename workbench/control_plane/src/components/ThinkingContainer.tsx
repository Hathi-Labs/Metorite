"use client";

/**
 * ThinkingContainer — VS Code Copilot Chat-style "working" group.
 *
 * Groups the entire working phase of one assistant turn into a single
 * collapsible container with a vertical timeline connecting each step.
 *
 * Visual design mirrors VS Code's chatThinkingContentPart:
 *   • Vertical connecting line with Lucide line-art icons per step
 *     (Brain, BookOpen, Terminal, Search, SquarePen, GitBranch, Wrench)
 *   • Git-tree style sub-timeline for sub-agent tool calls
 *   • Timing info
 *   • Auto-expand during active streaming, auto-collapse on completion
 *
 * Every chat surface draws its trail here: `/chat`, and the Projects, Tasks
 * and email rails, which all mount the shared `AgentChat`. A step's words come
 * from `lib/toolSteps.ts` ("Ran a script in the sandbox", "Created a task"),
 * the one place a tool name becomes a sentence. Fence: `toolSteps.test.ts`.
 *
 * Patterns sourced from VS Code Copilot Chat UI study —
 * see project-docs/spec_chat_ux.md.
 */

import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ToolEvent } from "@/components/MarkdownMessage";
import MarkdownImage from "@/components/MarkdownImage";
import { markdownUrlTransform } from "@/lib/markdownMedia";
import rehypeStreamCaret from "@/lib/streamCaret";
import { TRAIL_AXIS, TRAIL_ICON_CELL, trailBodySize } from "@/lib/trailLayout";
import {
  COMMAND_CAP,
  capOutput,
  commandOf,
  describeToolStep,
  type StepKind,
  type StepStatus,
} from "@/lib/toolSteps";

interface ThinkingContainerProps {
  toolEvents: ToolEvent[];
  progressLines: string[];
  /** Sequential reasoning blocks — each rendered as its own timeline entry. */
  reasoningBlocks?: string[];
  /** Narration message segments (Phase 3b) — real assistant text emitted before
   *  the final answer segment, interleaved with tools via each tool's
   *  segmentCutoff. Empty for id-less runtimes (which use the reasoning fold). */
  narrationSegments?: string[];
  isActive: boolean;
}

// ─── Step kinds ─────────────────────────────────────────────────────────────

/** Icon key used to look up the Lucide component from the icon map. */
type IconKey = "brain" | "book" | "search" | "edit" | "terminal" | "branch" | "wrench";

/** Map icon keys to their Lucide line-art components. */
const ICON_MAP: Record<IconKey, ThemedIcon> = {
  brain: themedIcon("Brain"),
  book: themedIcon("BookOpen"),
  search: themedIcon("Search"),
  edit: themedIcon("SquarePen"),
  terminal: themedIcon("Terminal"),
  branch: themedIcon("GitBranch"),
  wrench: themedIcon("Wrench"),
};

/** The icon on the axis says WHAT a step does. Its colour says WHERE it is
 *  (see {@link STATUS_INK}), so a kind carries no hue of its own. */
const KIND_ICON: Record<StepKind, IconKey> = {
  read: "book",
  edit: "edit",
  search: "search",
  run: "terminal",
  delegate: "branch",
  think: "brain",
  other: "wrench",
};

/** A step's status, as ink. Semantic tokens: a status is information. */
const STATUS_INK: Record<StepStatus, string> = {
  running: "text-info",
  done: "text-muted-foreground",
  failed: "text-destructive",
};

/** Render a Lucide icon for a given icon key, sized for the timeline axis. */
function TimelineIcon({ iconKey, className }: { iconKey: IconKey; className?: string }) {
  const Icon = ICON_MAP[iconKey];
  return <Icon className={className} size={14} strokeWidth={1.5} />;
}

// ─── Timeline prose (reasoning + narration) ────────────────────────────────
// Reasoning blocks (chain-of-thought) and narration segments (Phase 3b real
// assistant text before the answer) render as the same compact markdown; only
// the axis icon differs (Brain vs BookOpen).

const PROSE_MD_COMPONENTS = {
  p: ({ children }: { children?: React.ReactNode }) => <p className="my-1">{children}</p>,
  code: ({ className, children, ...props }: { className?: string; children?: React.ReactNode }) => {
    const inline = !className;
    return inline ? (
      <code className="bg-secondary/80 text-foreground text-[10.5px] px-1 py-0.5 rounded" {...props}>{children}</code>
    ) : (
      <code className={`block bg-secondary/80 text-foreground text-[10.5px] p-2 rounded overflow-x-auto ${className || ""}`} {...props}>{children}</code>
    );
  },
  pre: ({ children }: { children?: React.ReactNode }) => <pre className="bg-card/80 rounded-md overflow-x-auto my-1.5">{children}</pre>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className="list-disc list-inside my-1 space-y-0.5">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className="list-decimal list-inside my-1 space-y-0.5">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li className="text-[11px]">{children}</li>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong className="text-foreground font-semibold">{children}</strong>,
  em: ({ children }: { children?: React.ReactNode }) => <em className="italic">{children}</em>,
  blockquote: ({ children }: { children?: React.ReactNode }) => <blockquote className="border-l-2 border-border pl-2 my-1 text-muted-foreground">{children}</blockquote>,
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => <a href={href} className="text-primary underline" target="_blank" rel="noopener">{children}</a>,
  h1: ({ children }: { children?: React.ReactNode }) => <h1 className="text-[13px] font-bold text-foreground mt-2 mb-1">{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 className="text-[12px] font-semibold text-foreground mt-1.5 mb-1">{children}</h2>,
  // Reasoning is agent text too: a remote image loads only on a click.
  // The container has no session, so a workspace-relative path stays as it
  // is and does not resolve through the file proxy (as before the gate).
  img: ({ src, alt, title }: { src?: unknown; alt?: unknown; title?: unknown }) => (
    <MarkdownImage src={src} alt={alt} title={title} className="my-1 max-h-48 max-w-full rounded border border-border/50 object-contain" />
  ),
};

/** One prose entry (reasoning or narration) on the timeline axis. */
export function ProseTimelineEntry({
  text, live, icon: Icon, iconClass,
}: {
  text: string;
  live: boolean;
  icon: ThemedIcon;
  iconClass: string;
}) {
  return (
    <div className="relative flex min-w-0">
      {/* The trail's one icon column (`lib/trailLayout.ts`). */}
      <span data-trail-icon="" className={`${TRAIL_ICON_CELL} relative z-10 mt-[0.3rem]`}>
        <Icon className={iconClass} size={14} strokeWidth={1.5} />
      </span>
      <div className="flex-1 min-w-0 mr-3 text-[11.5px] text-muted-foreground leading-relaxed">
        {/* The caret goes inside the last line of words (`lib/streamCaret.ts`).
            A span after the Markdown started a line of its own: a lone "|". */}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={live ? [rehypeStreamCaret] : []}
          urlTransform={markdownUrlTransform}
          components={PROSE_MD_COMPONENTS}
        >
          {text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

// ─── Interleaved timeline (VS Code chatThinkingContentPart parity) ─────────

type TimelineItem =
  | { kind: "reasoning"; text: string; blockIndex: number }
  | { kind: "narration"; text: string; blockIndex: number }
  | { kind: "tool"; event: ToolEvent; toolIndex: number };

/**
 * Merge narration segments, reasoning blocks and tool events into one
 * chronological list.  Each tool carries two independent anchors:
 *   • reasoningCutoff — # of reasoning blocks that existed when it started
 *   • segmentCutoff   — # of narration segments that existed when it started
 * Blocks/segments below the respective cutoff precede the tool.  The two
 * channels advance independently so reasoning and narration keep their own
 * chronology relative to the tools.
 *
 * Narration segments (Phase 3b) are the real assistant text before the answer;
 * reasoning blocks are chain-of-thought.  For id-less runtimes narrationSegments
 * is empty and this degrades to the original reasoning-only interleave (legacy
 * events without a cutoff sort after everything — the previous rendering order —
 * so old persisted messages still display correctly).
 */
function buildTimeline(
  reasoningBlocks: string[],
  toolEvents: ToolEvent[],
  narrationSegments: string[] = [],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  let r = 0;
  let s = 0;
  toolEvents.forEach((event, toolIndex) => {
    const rCut = event.reasoningCutoff ?? Number.POSITIVE_INFINITY;
    const sCut = event.segmentCutoff ?? Number.POSITIVE_INFINITY;
    while (s < narrationSegments.length && s < sCut) {
      items.push({ kind: "narration", text: narrationSegments[s], blockIndex: s });
      s++;
    }
    while (r < reasoningBlocks.length && r < rCut) {
      items.push({ kind: "reasoning", text: reasoningBlocks[r], blockIndex: r });
      r++;
    }
    items.push({ kind: "tool", event, toolIndex });
  });
  while (s < narrationSegments.length) {
    items.push({ kind: "narration", text: narrationSegments[s], blockIndex: s });
    s++;
  }
  while (r < reasoningBlocks.length) {
    items.push({ kind: "reasoning", text: reasoningBlocks[r], blockIndex: r });
    r++;
  }
  return items;
}

// ─── Shell syntax highlighting (VS Code terminal colours) ──────────────────

const POWERSHELL_KEYWORDS = new Set([
  "Select-Object", "Where-Object", "ForEach-Object", "Sort-Object",
  "Group-Object", "Measure-Object", "Write-Host", "Write-Output",
  "Get-ChildItem", "Get-Content", "Set-Content", "Invoke-WebRequest",
  "ConvertFrom-Json", "ConvertTo-Json", "ForEach-Object",
  "Start-Process", "Stop-Process", "Get-Process",
  "Test-Path", "New-Item", "Remove-Item",
  "Copy-Item", "Move-Item", "Rename-Item",
]);

/** Tokenize a shell command into syntax-highlighted spans. */
function highlightCommand(cmd: string): React.ReactNode[] {
  const parts = cmd.split(/(\||&&|\|\||;|>>|>|<|2>&1)/g);
  return parts.map((part, i) => {
    if (/^(\||&&|\|\||;|>>|>|<|2>&1)$/.test(part.trim())) {
      return <span key={i} className="text-term-muted mx-0.5">{part}</span>;
    }
    return <span key={i}>{tokenizeSegment(part, i === 0)}</span>;
  });
}

function tokenizeSegment(segment: string, isFirst: boolean): React.ReactNode[] {
  const tokens = segment.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [segment];
  return tokens.map((token, j) => {
    const first = isFirst && j === 0;
    if (/^["'].*["']$/.test(token))
      return <span key={j} className="text-term-string">{token} </span>;
    if (/^--?[a-zA-Z]/.test(token))
      return <span key={j} className="text-term-flag">{token} </span>;
    if (/[\/\\]/.test(token) && !/^[0-9]+$/.test(token))
      return <span key={j} className="text-term-path">{token} </span>;
    if (/^[0-9]+(\.[0-9]+)?$/.test(token))
      return <span key={j} className="text-term-number">{token} </span>;
    if (first)
      return <span key={j} className="text-term-command font-medium">{token} </span>;
    if (POWERSHELL_KEYWORDS.has(token))
      return <span key={j} className="text-term-keyword">{token} </span>;
    return <span key={j} className="text-term-fg">{token} </span>;
  });
}

// ─── One step on the trail ──────────────────────────────────────────────────

/** The word a screen reader hears, and the word a failed step shows. */
const STATUS_WORD: Record<StepStatus, string> = {
  running: "running",
  done: "done",
  failed: "failed",
};

/** "Output cut at 4,000 characters" under a long output, so a cut is visible. */
function CutNote({ cut }: { cut: number }) {
  if (cut <= 0) return null;
  return (
    <div className="mt-1 text-[10px] text-term-muted">
      {cut.toLocaleString()} more characters not shown
    </div>
  );
}

/** The terminal a script step expands into: the command, then its output. */
function RunDetail({ event, running, dur }: { event: ToolEvent; running: boolean; dur?: number }) {
  const out = event.result ? capOutput(String(event.result)) : null;
  // A running row opens by itself, so a long script body must not draw whole.
  const cmd = capOutput(commandOf(event.args, event.name), COMMAND_CAP);
  return (
    <div className="rounded-md bg-term-bg border border-term-fg/10 overflow-hidden">
      <div className="px-2.5 pt-1.5 pb-2.5 font-mono text-[11px] leading-relaxed">
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className="text-term-prompt shrink-0 select-none font-medium">$</span>
          <div
            data-step-command=""
            className="flex-1 min-w-0 text-term-fg break-all font-mono text-[11px] leading-relaxed max-h-40 overflow-y-auto"
          >
            {highlightCommand(cmd.text)}
            <CutNote cut={cmd.cut} />
          </div>
          {running && (
            <span className="text-[10px] text-term-running animate-pulse font-mono shrink-0">running</span>
          )}
          {dur !== undefined && !running && (
            <span className="text-[10px] text-term-muted font-mono shrink-0">{dur}ms</span>
          )}
        </div>
        {out ? (
          <div
            data-step-output=""
            className="text-term-output whitespace-pre-wrap break-all max-h-64 overflow-y-auto leading-snug"
          >
            {out.text}
            {running && (
              <span className="inline-block w-[6px] h-[14px] bg-term-output animate-pulse ml-0.5 align-middle" />
            )}
            <CutNote cut={out.cut} />
          </div>
        ) : running ? (
          <div className="flex gap-2">
            <span className="text-term-prompt shrink-0 select-none">$</span>
            <span className="inline-block w-[6px] h-[14px] bg-term-output animate-pulse align-middle" />
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Any other step expands into its arguments and its result. */
function ArgsDetail({ event, dur }: { event: ToolEvent; dur?: number }) {
  const out = event.result ? capOutput(String(event.result), 2000) : null;
  return (
    <div className="rounded-md border-l-2 border-border bg-card/40 px-2.5 py-1.5 min-w-0">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-muted-foreground font-mono truncate">{event.name}</span>
        {dur !== undefined && <span className="text-[10px] text-muted-foreground tabular-nums ml-auto shrink-0">{dur}ms</span>}
      </div>
      {event.args && Object.keys(event.args).length > 0 && (
        <div className="text-[10px] text-muted-foreground font-mono mt-1 break-all">
          {Object.entries(event.args).map(([k, v]) => (
            <span key={k} className="inline-block mr-2">
              <span className="text-muted-foreground/70">{k}:</span>{" "}
              <span className="text-muted-foreground">
                {(typeof v === "string" ? v : JSON.stringify(v) ?? "").slice(0, 80)}
              </span>
            </span>
          ))}
        </div>
      )}
      {out && (
        <pre
          data-step-output=""
          className="text-[10px] text-muted-foreground font-mono mt-1 whitespace-pre-wrap break-all max-h-48 overflow-y-auto"
        >
          {out.text}
          <CutNote cut={out.cut} />
        </pre>
      )}
    </div>
  );
}

/** A hand-off's own steps, as a branch under the step that asked. */
function SubAgentSteps({ event }: { event: ToolEvent }) {
  return (
    <div className="mt-1.5 ml-3 relative">
      <div className="absolute left-[-12px] top-0 bottom-0 w-px bg-border" />
      <div className="absolute left-[-12px] top-3 w-[12px] h-px bg-border" />
      <div className="flex items-center gap-1.5 text-[10px] mb-1 min-w-0">
        <AppIcon name="GitBranch" className="text-muted-foreground shrink-0" size={12} strokeWidth={1.5} />
        <span className="text-foreground font-medium truncate">{event.subAgentName}</span>
        {event.subAgentActive && (
          <span className="text-[10px] text-info animate-pulse shrink-0">working</span>
        )}
      </div>
      {event.subAgentText && (
        <pre className="text-muted-foreground whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed mb-1.5 max-h-24 overflow-y-auto bg-secondary/40 rounded px-2 py-1 border border-border/40">
          {capOutput(event.subAgentText, 2000).text}
        </pre>
      )}
      {event.subAgentTools && event.subAgentTools.length > 0 && (
        <div className="relative ml-2">
          <div className="absolute left-[6px] top-1 bottom-1 w-px bg-border/70" aria-hidden="true" />
          <ul className="space-y-1">
          {event.subAgentTools.map((st) => {
            const step = describeToolStep(st);
            return (
              <li key={st.id} className="relative flex items-start gap-2 min-w-0" data-step-status={step.status}>
                <div className="absolute left-[6px] top-[8px] w-[8px] h-px bg-border/70" aria-hidden="true" />
                <span className={`shrink-0 mt-0.5 ml-[14px] ${STATUS_INK[step.status]}`}>
                  <TimelineIcon iconKey={KIND_ICON[step.kind]} />
                </span>
                <span className={`text-[11px] min-w-0 truncate ${step.status === "failed" ? "text-destructive" : step.status === "running" ? "chat-shimmer-text" : "text-foreground"}`}>
                  {step.label}
                </span>
                <span className="sr-only">{STATUS_WORD[step.status]}</span>
                {step.status === "failed" && (
                  <span className="text-[10px] text-destructive shrink-0">failed</span>
                )}
                {st.result && step.status !== "running" && (
                  <span className="text-[10px] text-muted-foreground font-mono truncate min-w-0">
                    {String(st.result).slice(0, 60)}
                  </span>
                )}
              </li>
            );
          })}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * One tool call on the trail: a one-line row that says what the step did
 * ("Ran a script in the sandbox", "Asked email-assistant", "Created a task"),
 * with its status, and a detail that opens below it.
 *
 * Controlled: the container owns `open`, so a test can render either state.
 * Exported for `toolSteps.test.ts`.
 */
export function ToolStepRow({
  event,
  open,
  onToggle,
}: {
  event: ToolEvent;
  open: boolean;
  onToggle?: () => void;
}) {
  const step = describeToolStep(event);
  const running = step.status === "running";
  const failed = step.status === "failed";
  const dur = event.endedAt && event.startedAt ? event.endedAt - event.startedAt : undefined;
  const hasSubAgent = !!(event.subAgentName && (event.subAgentTools?.length || event.subAgentText));
  return (
    <div className="relative flex min-w-0" data-step-status={step.status} data-step-kind={step.kind}>
      {/* The icon on the axis: what the step does, inked by where it is. It
          sits in the trail's one icon column, the same cell as the header's
          (`lib/trailLayout.ts`), so the two cannot drift apart. */}
      <span data-trail-icon="" className={`${TRAIL_ICON_CELL} relative z-10 mt-[0.3rem]`}>
        <TimelineIcon
          iconKey={KIND_ICON[step.kind]}
          className={running ? `${STATUS_INK.running} drop-shadow-[0_0_4px_currentColor]` : STATUS_INK[step.status]}
        />
      </span>

      <div className="flex-1 mr-3 min-w-0">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="w-full flex items-baseline gap-1.5 text-left group/tool min-w-0"
        >
          {/* The words keep their width and the target gives way: in a
              narrow rail "Ran a script in …" said less than the command. */}
          <span
            className={`text-[11.5px] truncate ${step.target ? "flex-none max-w-[75%]" : "min-w-0"} ${
              running ? "chat-shimmer-text" : failed ? "text-destructive" : "text-foreground"
            }`}
          >
            {step.label}
          </span>
          {step.target && (
            <span className="text-[11px] font-mono truncate min-w-0 px-1 py-px rounded bg-secondary/60 border border-border/40 text-muted-foreground">
              {step.target}
            </span>
          )}
          <span className="sr-only">{STATUS_WORD[step.status]}</span>
          {failed && <span className="text-destructive text-[10px] shrink-0">failed</span>}
          {dur !== undefined && dur > 1000 && (
            <span data-step-duration="" className="text-[10px] text-muted-foreground tabular-nums shrink-0">{(dur / 1000).toFixed(1)}s</span>
          )}
          <span className="ml-auto shrink-0 text-muted-foreground text-[10px] opacity-0 group-hover/tool:opacity-100 transition-opacity">
            {open ? "▴" : "▾"}
          </span>
        </button>

        {open && (
          <div className="mt-1 min-w-0">
            {step.kind === "run" ? (
              <RunDetail event={event} running={running} dur={dur} />
            ) : (
              <ArgsDetail event={event} dur={dur} />
            )}
            {hasSubAgent && <SubAgentSteps event={event} />}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ThinkingContainer({
  toolEvents,
  progressLines,
  reasoningBlocks,
  narrationSegments,
  isActive,
}: ThinkingContainerProps) {
  const hasReasoning = !!(reasoningBlocks && reasoningBlocks.length > 0);
  const hasNarration = !!(narrationSegments && narrationSegments.length > 0);
  const hasTools = toolEvents.length > 0;
  const hasContent = hasTools || hasReasoning || hasNarration;

  // Open from the first render when the turn is already working. A surface
  // that mounts mid-run (a rail re-opened beside the board, a reconnect) then
  // shows the trail at once, instead of a closed "Thinking…" until the next
  // step lands.
  const [expanded, setExpanded] = useState(() => isActive && hasContent);
  const userToggledRef = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  // Per-tool expansion override (user click).  Without an override, a tool
  // row is open while running (live output) and collapsed when done —
  // matching VS Code's chat tool invocation parts.
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({});

  // Chronologically interleaved narration + reasoning + tool timeline
  // (VS Code style; narration segments are Phase 3b message-id ground truth).
  const timeline = useMemo(
    () => buildTimeline(reasoningBlocks ?? [], toolEvents, narrationSegments ?? []),
    [reasoningBlocks, toolEvents, narrationSegments],
  );

  // Auto-follow: while the agent streams verbose reasoning, keep the
  // timeline scrolled to the newest content (VS Code thinking-pane style).
  const reasoningLen = reasoningBlocks?.reduce((n, b) => n + b.length, 0) ?? 0;
  const narrationLen = narrationSegments?.reduce((n, b) => n + b.length, 0) ?? 0;
  useEffect(() => {
    if (!isActive || !expanded) return;
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [isActive, expanded, reasoningLen, narrationLen, toolEvents.length, progressLines.length]);

  // Auto-expand while the agent is actively working so the user sees the
  // thought process in real time (VS Code Copilot Chat style). Expands on:
  // - First tool call or reasoning delta (content appears)
  // - Active streaming with content (user sees live progress)
  // Collapses after completion unless the user manually toggled.
  useEffect(() => {
    if (hasContent && isActive && !userToggledRef.current) {
      setExpanded(true);
    }
  }, [hasContent, isActive]);

  // Auto-collapse shortly after completion (unless the user manually expanded).
  useEffect(() => {
    if (isActive) return;
    if (userToggledRef.current) return;
    const t = setTimeout(() => setExpanded(false), 300);
    return () => clearTimeout(t);
  }, [isActive]);

  // The newest step, in words, for the header while the turn works.
  const lastLabel = useMemo(() => {
    if (toolEvents.length > 0) {
      return describeToolStep(toolEvents[toolEvents.length - 1]).label;
    }
    if (progressLines.length > 0) {
      const last = progressLines[progressLines.length - 1];
      // Live answer/progress snippets are marked with a leading "↳" so the
      // delta loop can replace them — but that marker reads like an "enter"
      // symbol in the minimized header.  Strip it and show the raw process
      // text as-is.
      if (last.startsWith("↳ ")) return last.slice(2).trim();
      return describeToolStep({ name: last, status: "running" }).label;
    }
    return null;
  }, [toolEvents, progressLines]);

  // Final summary title once complete — VS Code style ("Ran 3 commands,
  // read 2 files"), counted by step kind.
  const summaryTitle = useMemo(() => {
    if (toolEvents.length === 0) {
      return (hasReasoning || hasNarration)
        ? "Thought through the approach"
        : "Finished thinking";
    }
    if (toolEvents.length === 1) return describeToolStep(toolEvents[0]).label;
    const counts = new Map<StepKind, number>();
    for (const t of toolEvents) {
      const k = describeToolStep(t).kind;
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const PHRASE: Record<StepKind, [string, string]> = {
      run: ["ran 1 script", "ran {n} scripts"],
      read: ["read 1 item", "read {n} items"],
      search: ["ran 1 search", "ran {n} searches"],
      edit: ["made 1 change", "made {n} changes"],
      delegate: ["asked 1 agent", "asked {n} agents"],
      think: ["made 1 plan step", "made {n} plan steps"],
      other: ["used 1 tool", "used {n} tools"],
    };
    const parts = Array.from(counts.entries()).map(([k, n], i) => {
      const text = (n === 1 ? PHRASE[k][0] : PHRASE[k][1]).replace("{n}", String(n));
      return i === 0 ? text.charAt(0).toUpperCase() + text.slice(1) : text;
    });
    return parts.slice(0, 3).join(", ");
  }, [toolEvents, hasReasoning, hasNarration]);

  const hasError = toolEvents.some((t) => t.status === "error");
  const failedCount = toolEvents.filter((t) => t.status === "error").length;

  // Live tail of the model's chain-of-thought — shown in the header while
  // active so the user sees the "stream of consciousness" even collapsed
  // (mirrors VS Code Copilot's live thinking snippet).
  const liveReasoningTail = useMemo(() => {
    if (!isActive) return null;
    // Prefer the newest reasoning block; fall back to the newest narration
    // segment (Phase 3b) so id-carrying runs still show a live snippet even
    // when reasoningBlocks holds only genuine chain-of-thought (often empty).
    const pool = [...(reasoningBlocks ?? []), ...(narrationSegments ?? [])];
    const last = [...pool].reverse().find((b) => b.trim())?.trim();
    if (!last) return null;
    return last.length > 90 ? `…${last.slice(-90)}` : last;
  }, [isActive, reasoningBlocks, narrationSegments]);

  // Title shown in the header.  A currently-running step takes priority;
  // otherwise show the live reasoning tail, then the last known step.
  const hasRunningTool = toolEvents.some((t) => t.status === "running");
  const title = isActive
    ? (hasRunningTool && lastLabel ? lastLabel : null)
      ?? liveReasoningTail
      ?? lastLabel
      ?? "Thinking…"
    : summaryTitle;

  const totalMs = useMemo(() => {
    if (isActive || toolEvents.length === 0) return null;
    let earliest = Infinity;
    let latest = 0;
    for (const t of toolEvents) {
      if (t.startedAt !== undefined && t.startedAt < earliest) earliest = t.startedAt;
      if (t.endedAt && t.endedAt > latest) latest = t.endedAt;
    }
    if (!isFinite(earliest) || latest === 0) return null;
    return latest - earliest;
  }, [isActive, toolEvents]);

  return (
    <div className="my-2 rounded-lg border border-border/40 bg-card/30 overflow-hidden" data-trail="">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <button
        type="button"
        onClick={() => { userToggledRef.current = true; setExpanded((o) => !o); }}
        disabled={!hasContent}
        aria-expanded={hasContent ? expanded : undefined}
        data-trail-head=""
        className="w-full flex items-center pr-3 py-2 text-left hover:bg-secondary/40 transition-colors disabled:cursor-default min-w-0"
      >
        {/* The trail's one icon column: the same cell as every step's, so
            the header icon sits on the axis (`lib/trailLayout.ts`). */}
        <span data-trail-icon="" className={TRAIL_ICON_CELL}>
          {hasError ? (
            <AppIcon name="X" className="text-destructive" size={14} strokeWidth={2} />
          ) : isActive ? (
            <AppIcon name="Brain" className="text-info" size={14} strokeWidth={1.5} />
          ) : (
            <AppIcon name="Check" className="text-success" size={14} strokeWidth={2} />
          )}
        </span>
        <span className="flex flex-1 items-center gap-2 min-w-0">
        <span title={title} className={`text-xs font-medium min-w-0 truncate ${isActive ? "chat-shimmer-text" : "text-muted-foreground"}`}>
          {title}
        </span>
        {!isActive && failedCount > 0 && (
          <span className="shrink-0 text-[10px] text-destructive">
            {failedCount} failed
          </span>
        )}
        {totalMs !== null && (
          <span data-step-duration="" className="shrink-0 text-[10px] text-muted-foreground tabular-nums">
            {totalMs >= 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`}
          </span>
        )}
        {hasContent && <span className="ml-auto shrink-0 text-muted-foreground text-[10px]">{expanded ? "▲" : "▼"}</span>}
        </span>
      </button>

      {/* ── Pre-content working indicator ──────────────────────────────
          The dead-air gap between "run started" and the first tool/reasoning
          event is now indicated by the persistent bottom bar in AgentChat
          rather than a message inside the container. */}

      {/* ── Body: vertical timeline ────────────────────────────────── */}
      {expanded && hasContent && (
        <div
          ref={bodyRef}
          data-trail-body=""
          className={`border-t border-border/40 chat-fade-in overflow-y-auto ${
            // A cap, never a height (`lib/trailLayout.ts`). A fixed `h-56`
            // here drew ~10rem of empty border under two steps for the whole
            // run. Over the cap, the trail scrolls inside itself, and the
            // effect above follows it to the newest step.
            trailBodySize(isActive)
          }`}
        >
          {/* NO left padding: every row starts with the icon column, and the
              line runs down the centre of it (`lib/trailLayout.ts`). */}
          <div className="relative py-2.5">
            <div className={`absolute ${TRAIL_AXIS} top-2 bottom-2 w-px bg-secondary/60`} aria-hidden="true" />

            <div className="space-y-1.5">
              {/* Chronologically interleaved reasoning + tool timeline
                  (VS Code chatThinkingContentPart parity). */}
              {timeline.map((item) => {
                if (item.kind === "reasoning") {
                  if (!item.text.trim()) return null; // skip empty sentinels
                  const isLastReasoning =
                    item.blockIndex === (reasoningBlocks?.length ?? 0) - 1;
                  const live = isActive && isLastReasoning;
                  return (
                    <ProseTimelineEntry
                      key={`r-${item.blockIndex}`}
                      text={item.text}
                      live={live}
                      icon={themedIcon("Brain")}
                      iconClass={live ? "text-info" : "text-muted-foreground/50"}
                    />
                  );
                }

                if (item.kind === "narration") {
                  if (!item.text.trim()) return null;
                  // Narration segments are the model's real prose before the
                  // answer — a distinct book icon separates them from the
                  // chain-of-thought.  The last narration segment is live
                  // only while streaming.
                  const isLastNarration =
                    item.blockIndex === (narrationSegments?.length ?? 0) - 1;
                  const live = isActive && isLastNarration;
                  return (
                    <ProseTimelineEntry
                      key={`n-${item.blockIndex}`}
                      text={item.text}
                      live={live}
                      icon={themedIcon("BookOpen")}
                      iconClass={live ? "text-info" : "text-muted-foreground/50"}
                    />
                  );
                }

                const event = item.event;
                const open = toolOverrides[event.id] ?? event.status === "running";
                return (
                  <ToolStepRow
                    key={event.id}
                    event={event}
                    open={open}
                    onToggle={() =>
                      setToolOverrides((prev) => ({ ...prev, [event.id]: !open }))
                    }
                  />
                );
              })}

              {/* Working indicator removed from here — it now lives as a
                  persistent bottom bar in AgentChat, always visible regardless
                  of whether the thinking container is expanded or collapsed. */}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
