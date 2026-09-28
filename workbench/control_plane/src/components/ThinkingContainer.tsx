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
 *   • Action badges (Read, Edit, Search, Run, etc.)
 *   • Color-coded left borders for different action types
 *   • Timing info, diff-style change badges
 *   • Auto-expand during active streaming, auto-collapse on completion
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

// ─── Tool classification ────────────────────────────────────────────────────

type ActionKind = "read" | "edit" | "search" | "run" | "delegate" | "think" | "other";

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

function classifyTool(name: string): {
  kind: ActionKind; iconKey: IconKey; label: string;
  borderClass: string; iconClass: string;
} {
  const n = name.toLowerCase();
  if (/search|grep|find|list|semantic|codebase|query|retrieve|lookup/.test(n))
    return { kind: "search", iconKey: "search", label: "Search", borderClass: "border-cat-12/30", iconClass: "text-cat-12" };
  if (/read|get_file|problems|fetch|load|open|view|analyzing|generating/.test(n))
    return { kind: "read", iconKey: "book", label: "Read", borderClass: "border-sky-700/50", iconClass: "text-sky-400" };
  if (/edit|create|write|replace|patch|insert|update|append|fix/.test(n))
    return { kind: "edit", iconKey: "edit", label: "Edit", borderClass: "border-emerald-700/50", iconClass: "text-emerald-400" };
  if (/terminal|bash|shell|exec|run|command/.test(n))
    return { kind: "run", iconKey: "terminal", label: "Code", borderClass: "border-violet-700/50", iconClass: "text-violet-400" };
  if (/delegate|spawn|agent|call_agent/.test(n))
    return { kind: "delegate", iconKey: "branch", label: "Delegate", borderClass: "border-rose-700/50", iconClass: "text-rose-400" };
  if (/think|reason|reflect|plan|analyze|consider/.test(n))
    return { kind: "think", iconKey: "brain", label: "Think", borderClass: "border-purple-700/50", iconClass: "text-purple-400" };
  return { kind: "other", iconKey: "wrench", label: "Tool", borderClass: "border-border/50", iconClass: "text-muted-foreground" };
}

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
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => <a href={href} className="text-sky-400 underline" target="_blank" rel="noopener">{children}</a>,
  h1: ({ children }: { children?: React.ReactNode }) => <h1 className="text-[13px] font-bold text-foreground mt-2 mb-1">{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 className="text-[12px] font-semibold text-foreground mt-1.5 mb-1">{children}</h2>,
};

/** One prose entry (reasoning or narration) on the timeline axis. */
function ProseTimelineEntry({
  text, live, icon: Icon, iconClass,
}: {
  text: string;
  live: boolean;
  icon: ThemedIcon;
  iconClass: string;
}) {
  return (
    <div className="relative">
      <div className="absolute left-[6px] top-[5px] z-10">
        <Icon className={iconClass} size={13} strokeWidth={1.5} />
      </div>
      <div className="ml-8 mr-3 text-[11.5px] text-muted-foreground leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={PROSE_MD_COMPONENTS}>
          {text}
        </ReactMarkdown>
        {live && <span className="inline-block w-[2px] h-[1em] bg-muted-foreground/50 animate-pulse ml-0.5 align-middle rounded-full" />}
      </div>
    </div>
  );
}

function formatToolName(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Past/present-tense verbs per action kind (VS Code: "Ran", "Read", …). */
const KIND_VERBS: Record<ActionKind, { done: string; running: string }> = {
  run: { done: "Ran", running: "Running" },
  read: { done: "Read", running: "Reading" },
  search: { done: "Searched", running: "Searching" },
  edit: { done: "Edited", running: "Editing" },
  delegate: { done: "Delegated to", running: "Delegating to" },
  think: { done: "Thought", running: "Thinking" },
  other: { done: "Used", running: "Using" },
};

/** One-line headline for a tool row — the command, path, query or name. */
function toolHeadline(event: ToolEvent, kind: ActionKind): string {
  const args = event.args ?? {};
  if (kind === "run") return extractCommand(args, event.name);
  if (kind === "delegate") {
    const target = event.subAgentName ?? args.agent_name ?? args.agent ?? args.name;
    if (typeof target === "string" && target) return target;
  }
  if (kind === "search") {
    const q = args.query ?? args.q ?? args.pattern ?? args.search;
    if (typeof q === "string" && q) return q.slice(0, 120);
  }
  const path = formatArgsLikePath(args);
  if (path) return path;
  return formatToolName(event.name);
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

function formatArgsLikePath(args: Record<string, unknown>): string | null {
  const path = args.path ?? args.filePath ?? args.file ?? args.file_path
    ?? args.target ?? args.destination ?? args.url ?? args.repo;
  if (typeof path === "string" && path.length > 0) {
    const parts = path.includes("/") ? path.split("/") : path.split("\\");
    const short = parts.slice(-2).join("/");
    return short.length < path.length ? `…/${short}` : short;
  }
  return null;
}

/** Pull a displayable command string from tool arguments. */
function extractCommand(args: Record<string, unknown>, toolName: string): string {
  // Direct command field (bash/shell tools)
  const cmd = args.command ?? args.cmd ?? args.fullCommandText
    ?? args.full_command_text ?? args.script ?? args.code;
  if (typeof cmd === "string" && cmd.length > 0) return cmd;
  // Reconstruct from argv-style array
  const argv = args.argv ?? args.args ?? args.arguments;
  if (Array.isArray(argv) && argv.length > 0) return argv.join(" ");
  // Last resort: show the first arg value
  const firstVal = Object.values(args).find((v) => typeof v === "string" && v.length > 0);
  if (firstVal) return String(firstVal).slice(0, 120);
  return formatToolName(toolName);
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

// ─── Rotating working messages (mirrors VS Code's working-message pool) ──────

const WORKING_MESSAGES = [
  "Working on it",
  "Thinking it through",
  "Processing",
  "Crunching the details",
  "Pulling the data",
  "Putting it together",
];

const FUN_MESSAGES = [
  "Bribing the hamster",
  "Reticulating splines",
  "Untangling the spaghetti",
  "Summoning Clippy",
  "Mining diamonds",
];

function pickWorkingMessage(): string {
  // ~1-in-12 chance of an easter-egg message (VS Code uses 1-in-100).
  if (Math.floor(Math.random() * 12) === 0) {
    return FUN_MESSAGES[Math.floor(Math.random() * FUN_MESSAGES.length)];
  }
  return WORKING_MESSAGES[Math.floor(Math.random() * WORKING_MESSAGES.length)];
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ThinkingContainer({
  toolEvents,
  progressLines,
  reasoningBlocks,
  narrationSegments,
  isActive,
}: ThinkingContainerProps) {
  const [expanded, setExpanded] = useState(false);
  const userToggledRef = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  // Per-tool expansion override (user click).  Without an override, a tool
  // row is open while running (live output) and collapsed when done —
  // matching VS Code's chat tool invocation parts.
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({});

  const hasReasoning = !!(reasoningBlocks && reasoningBlocks.length > 0);
  const hasNarration = !!(narrationSegments && narrationSegments.length > 0);
  const hasTools = toolEvents.length > 0;
  const hasContent = hasTools || hasReasoning || hasNarration;

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

  // Derive the current/last tool label for the title.
  const lastLabel = useMemo(() => {
    if (toolEvents.length > 0) {
      return formatToolName(toolEvents[toolEvents.length - 1].name);
    }
    if (progressLines.length > 0) {
      const last = progressLines[progressLines.length - 1];
      // Live answer/progress snippets are marked with a leading "↳" so the
      // delta loop can replace them — but that marker reads like an "enter"
      // symbol in the minimized header.  Strip it and show the raw process
      // text as-is (no tool-name title-casing for prose snippets).
      if (last.startsWith("↳ ")) return last.slice(2).trim();
      return formatToolName(last);
    }
    return null;
  }, [toolEvents, progressLines]);

  // Final summary title once complete — verb-based, VS Code style
  // ("Ran 3 commands, read 2 files").
  const summaryTitle = useMemo(() => {
    if (toolEvents.length === 0) {
      return (hasReasoning || hasNarration)
        ? "Thought through the approach"
        : "Finished thinking";
    }
    const counts = new Map<ActionKind, number>();
    for (const t of toolEvents) {
      const k = classifyTool(t.name).kind;
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const NOUNS: Record<ActionKind, [string, string]> = {
      run: ["command", "commands"],
      read: ["file", "files"],
      search: ["search", "searches"],
      edit: ["edit", "edits"],
      delegate: ["agent", "agents"],
      think: ["reflection", "reflections"],
      other: ["tool", "tools"],
    };
    const parts = Array.from(counts.entries()).map(([k, n], i) => {
      const verb = k === "delegate" ? "called" : KIND_VERBS[k].done.toLowerCase();
      const noun = NOUNS[k][n === 1 ? 0 : 1];
      const text = k === "edit" ? `made ${n} ${noun}` : `${verb} ${n} ${noun}`;
      return i === 0 ? text.charAt(0).toUpperCase() + text.slice(1) : text;
    });
    return parts.slice(0, 3).join(", ");
  }, [toolEvents, hasReasoning]);

  const hasError = toolEvents.some((t) => t.status === "error");

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

  // Title shown in the header.  A currently-running tool takes priority;
  // otherwise show the live reasoning tail, then the last known label.
  const hasRunningTool = toolEvents.some((t) => t.status === "running");
  const title = isActive
    ? (hasRunningTool && lastLabel ? `Working: ${lastLabel}` : null)
      ?? liveReasoningTail
      ?? (lastLabel ? `Working: ${lastLabel}` : "Thinking…")
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
    <div className="my-2 rounded-lg border border-border/40 bg-card/30 overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <button
        onClick={() => { userToggledRef.current = true; setExpanded((o) => !o); }}
        disabled={!hasContent}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-secondary/40 transition-colors disabled:cursor-default"
      >
        <span className="shrink-0 flex items-center justify-center w-4">
          {isActive ? (
            hasError ? <AppIcon name="X" className="text-red-400" size={14} strokeWidth={2} /> : <AppIcon name="Brain" className="text-sky-400" size={14} strokeWidth={1.5} />
          ) : hasError ? (
            <AppIcon name="X" className="text-red-400" size={14} strokeWidth={2} />
          ) : (
            <AppIcon name="Check" className="text-emerald-500" size={14} strokeWidth={2} />
          )}
        </span>
        <span className={`text-xs font-medium min-w-0 truncate ${isActive ? "chat-shimmer-text" : "text-muted-foreground"}`}>
          {title}
        </span>
        {totalMs !== null && (
          <span className="shrink-0 text-[10px] text-muted-foreground font-mono">{totalMs}ms</span>
        )}
        {/* Action kind badges in header */}
        {!isActive && toolEvents.length > 0 && (
          <span className="shrink-0 flex items-center gap-1 ml-1">
            {Array.from(new Set(toolEvents.map((t) => classifyTool(t.name).kind))).map((k) => (
              <span key={k} className="text-[9px] px-1 py-0.5 rounded border border-border text-muted-foreground font-mono uppercase">{k}</span>
            ))}
          </span>
        )}
        {hasContent && <span className="ml-auto shrink-0 text-muted-foreground text-[10px]">{expanded ? "▲" : "▼"}</span>}
      </button>

      {/* ── Pre-content working indicator ──────────────────────────────
          The dead-air gap between "run started" and the first tool/reasoning
          event is now indicated by the persistent bottom bar in AgentChat
          rather than a message inside the container. */}

      {/* ── Body: vertical timeline ────────────────────────────────── */}
      {expanded && hasContent && (
        <div
          ref={bodyRef}
          className={`border-t border-border/40 chat-fade-in overflow-y-auto ${
            // While streaming, use a FIXED-height window (not max-height) so the
            // live "consciousness" stream scrolls INTERNALLY (auto-followed to the
            // newest line) instead of growing and pushing the whole chat down.
            // When the user manually expands a finished turn to review, allow it
            // to grow up to a larger cap.
            isActive ? "h-56" : "max-h-[32rem]"
          }`}
        >
          {/* Container with NO left padding — all items position relative to this.
              Content is indented via ml-8.  Line and dots share the same x=12px axis. */}
          <div className="relative py-2.5">
            {/* Vertical line at x=12px */}
            <div className="absolute left-[12px] top-2 bottom-2 w-px bg-secondary/60" />

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
                      iconClass={live ? "text-purple-400" : "text-muted-foreground/50"}
                    />
                  );
                }

                if (item.kind === "narration") {
                  if (!item.text.trim()) return null;
                  // Narration segments are the model's real prose before the
                  // answer — a distinct book icon separates them from the
                  // purple-brain chain-of-thought.  The last narration segment
                  // is live only while streaming.
                  const isLastNarration =
                    item.blockIndex === (narrationSegments?.length ?? 0) - 1;
                  const live = isActive && isLastNarration;
                  return (
                    <ProseTimelineEntry
                      key={`n-${item.blockIndex}`}
                      text={item.text}
                      live={live}
                      icon={themedIcon("BookOpen")}
                      iconClass={live ? "text-sky-400" : "text-muted-foreground/50"}
                    />
                  );
                }

                const event = item.event;
                const style = classifyTool(event.name);
                const isRunning = event.status === "running";
                const isError = event.status === "error";
                const headline = toolHeadline(event, style.kind);
                const verb = isRunning ? KIND_VERBS[style.kind].running : KIND_VERBS[style.kind].done;
                const dur = event.endedAt && event.startedAt ? event.endedAt - event.startedAt : undefined;
                const open = toolOverrides[event.id] ?? isRunning;
                const toggle = () =>
                  setToolOverrides((prev) => ({ ...prev, [event.id]: !open }));

                const hasSubAgent = !!(event.subAgentName && (event.subAgentTools?.length || event.subAgentText));
                return (
                  <div key={event.id} className="relative">
                    {/* Lucide icon on the timeline axis */}
                    <div className="absolute left-[5px] top-[5px] z-10">
                      <TimelineIcon
                        iconKey={style.iconKey}
                        className={isRunning ? `${style.iconClass} drop-shadow-[0_0_4px_currentColor]` : isError ? "text-red-500" : style.iconClass}
                      />
                    </div>

                    <div className="ml-8 mr-3 min-w-0">
                      {/* Compact one-line header — "Ran <cmd>", "Read <file>"… */}
                      <button
                        onClick={toggle}
                        className="w-full flex items-baseline gap-1.5 text-left group/tool min-w-0"
                      >
                        <span className={`text-[11.5px] shrink-0 ${isRunning ? "chat-shimmer-text" : "text-muted-foreground"}`}>
                          {verb}
                        </span>
                        <span className={`text-[11px] font-mono truncate min-w-0 px-1 py-px rounded bg-secondary/60 border border-border/40 ${isError ? "text-red-400" : "text-foreground"}`}>
                          {headline}
                        </span>
                        {isError && <span className="text-red-400 text-[10px] shrink-0">✗</span>}
                        {dur !== undefined && dur > 1000 && (
                          <span className="text-[9px] text-muted-foreground font-mono shrink-0">{(dur / 1000).toFixed(1)}s</span>
                        )}
                        <span className="ml-auto shrink-0 text-muted-foreground text-[9px] opacity-0 group-hover/tool:opacity-100 transition-opacity">
                          {open ? "▴" : "▾"}
                        </span>
                      </button>

                      {/* Expanded detail */}
                      {open && (
                        <div className="mt-1">
                          {style.kind === "run" && (event.args || event.result) ? (
                            <div className="rounded-md bg-term-bg border border-term-fg/10 overflow-hidden">
                              {/* Terminal body — no title bar (no Mac circles), just the prompt */}
                              <div className="px-2.5 pt-1.5 pb-2.5 font-mono text-[11px] leading-relaxed">
                                {event.args && (
                                  <div className="flex items-baseline gap-2 mb-1.5">
                                    <span className="text-term-prompt shrink-0 select-none font-medium">$</span>
                                    <span className="flex-1 text-term-fg break-all font-mono text-[11px] leading-relaxed">
                                      {highlightCommand(extractCommand(event.args, event.name))}
                                    </span>
                                    {isRunning && (
                                      <span className="text-[9px] text-term-running animate-pulse font-mono shrink-0">running</span>
                                    )}
                                    {dur !== undefined && !isRunning && (
                                      <span className="text-[9px] text-term-muted font-mono shrink-0">{dur}ms</span>
                                    )}
                                  </div>
                                )}
                                {event.result && (
                                  <div className="text-term-output whitespace-pre-wrap break-all max-h-64 overflow-y-auto leading-snug">
                                    {String(event.result)}
                                    {isRunning && (
                                      <span className="inline-block w-[6px] h-[14px] bg-term-output animate-pulse ml-0.5 align-middle" />
                                    )}
                                  </div>
                                )}
                                {!event.result && isRunning && (
                                  <div className="flex gap-2">
                                    <span className="text-term-prompt shrink-0 select-none">$</span>
                                    <span className="inline-block w-[6px] h-[14px] bg-term-output animate-pulse align-middle" />
                                  </div>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div className={`rounded-md border-l-2 ${style.borderClass} bg-card/40 px-2.5 py-1.5`}>
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <TimelineIcon iconKey={style.iconKey} className={style.iconClass} />
                                <span className="text-[10px] text-muted-foreground font-mono truncate">{formatToolName(event.name)}</span>
                                {dur !== undefined && <span className="text-[9px] text-muted-foreground font-mono ml-auto shrink-0">{dur}ms</span>}
                              </div>
                              {event.args && Object.keys(event.args).length > 0 && (
                                <div className="text-[10px] text-muted-foreground font-mono mt-1">
                                  {Object.entries(event.args).map(([k, v]) => (
                                    <span key={k} className="inline-block mr-2">
                                      <span className="text-muted-foreground">{k}:</span>{" "}
                                      <span className="text-muted-foreground">{String(v).slice(0, 80)}</span>
                                    </span>
                                  ))}
                                </div>
                              )}
                              {event.result && (
                                <pre className="text-[10px] text-muted-foreground font-mono mt-1 whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                                  {String(event.result).slice(0, 2000)}
                                </pre>
                              )}
                            </div>
                          )}

                          {/* ── Git-tree style sub-agent sub-timeline ── */}
                          {hasSubAgent && (
                            <div className="mt-1.5 ml-3 relative">
                              {/* Branch connector: horizontal line from parent line to sub-tree */}
                              <div className="absolute left-[-12px] top-0 bottom-0 w-px bg-rose-700/40" />
                              <div className="absolute left-[-12px] top-3 w-[12px] h-px bg-rose-700/40" />

                              {/* Sub-agent header */}
                              <div className="flex items-center gap-1.5 text-[10px] mb-1">
                                <AppIcon name="GitBranch" className="text-rose-400" size={12} strokeWidth={1.5} />
                                <span className="text-rose-400 font-medium">{event.subAgentName}</span>
                                {event.subAgentActive && (
                                  <span className="text-[9px] text-rose-400 animate-pulse">● running</span>
                                )}
                              </div>

                              {/* Sub-agent output text */}
                              {event.subAgentText && (
                                <pre className="text-muted-foreground whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed mb-1.5 max-h-24 overflow-y-auto bg-zinc-950/50 rounded px-2 py-1 border border-border/40">
                                  {event.subAgentText}
                                </pre>
                              )}

                              {/* Sub-agent's own tool calls as child nodes */}
                              {event.subAgentTools && event.subAgentTools.length > 0 && (
                                <div className="relative ml-2">
                                  {/* Sub-tree vertical line */}
                                  <div className="absolute left-[6px] top-1 bottom-1 w-px bg-rose-700/30" />
                                  <div className="space-y-1">
                                    {event.subAgentTools.map((st, si) => {
                                      const isLast = si === event.subAgentTools!.length - 1;
                                      const stRunning = st.status === "running";
                                      const stError = st.status === "error";
                                      const stStyle = classifyTool(st.name);
                                      return (
                                        <div key={st.id} className="relative flex items-start gap-2">
                                          {/* Sub-node connector */}
                                          <div className="absolute left-[6px] top-[8px] w-[8px] h-px bg-rose-700/30" />
                                          {/* Sub-node icon */}
                                          <span className={`shrink-0 mt-0.5 ml-[14px] ${stRunning ? `${stStyle.iconClass} drop-shadow-[0_0_3px_currentColor]` : stError ? "text-red-500" : stStyle.iconClass}`}>
                                            <TimelineIcon iconKey={stStyle.iconKey} />
                                          </span>
                                          <span className={`text-[10px] font-mono truncate px-1 py-px rounded bg-secondary/50 border border-border/30 ${stError ? "text-red-400" : "text-foreground"}`}>
                                            {formatToolName(st.name)}
                                          </span>
                                          {st.result && (
                                            <span className="text-[9px] text-muted-foreground font-mono truncate max-w-[200px]">
                                              {String(st.result).slice(0, 60)}
                                            </span>
                                          )}
                                          {stRunning && (
                                            <span className="text-[9px] text-rose-400 animate-pulse shrink-0">…</span>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
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
