"use client";

/**
 * MarkdownMessage — VS Code Copilot-style rich message renderer.
 *
 * Features:
 *  • Full GitHub-flavoured Markdown (GFM): tables, strikethrough, task lists
 *  • Syntax-highlighted code blocks (token colours, `lib/codeTheme.ts`)
 *  • Terminal blocks with macOS-style chrome (red/yellow/green dots)
 *  • One-click copy button on every code block
 *  • Links: an in-app path opens in this tab, any other URL in a new tab
 *  • Entity pills (opt-in, WS-27bm S9): «names» from the tools as pills
 *  • Collapsible tool-call accordion blocks (mirrors VS Code's "Used tool: …")
 *  • Streaming cursor (blinking ▌) while the response is in-flight
 */

import { useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ChatEntityPill from "@/components/ChatEntityPill";
import { ControlLink } from "@/components/ControlLink";
import { isInAppPath } from "@/components/ui/EntityPill";
import { buildEntityIndex, type EntityIndex } from "@/lib/entityIndex";
import remarkEntityPills, {
  PILL_ATTR,
  PILL_NUMBER_ATTR,
  PILL_TEXT_ATTR,
  spaceBeforeBold,
} from "@/lib/remarkEntityPills";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { CODE_THEME } from "@/lib/codeTheme";
import Icon from "@/components/Icon";
import ThinkingContainer from "@/components/ThinkingContainer";

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ToolEvent {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: "running" | "done" | "error";
  startedAt?: number;
  endedAt?: number;
  /** Number of reasoning blocks that existed when this tool started —
   *  drives chronological interleaving in the ThinkingContainer timeline. */
  reasoningCutoff?: number;
  /** Number of message segments that existed when this tool started (Phase 3b)
   *  — drives segment↔tool interleaving when the runtime sent real ids. */
  segmentCutoff?: number;
  /** Set when this tool is a call_agent delegation — the target agent name. */
  subAgentName?: string;
  /** Live streaming text from the sub-agent (appended as it streams). */
  subAgentText?: string;
  /** Tool calls made by the sub-agent. */
  subAgentTools?: Array<{id: string; name: string; result?: string; status: "running" | "done" | "error"}>;
  /** True while the sub-agent is actively streaming. */
  subAgentActive?: boolean;
}

interface MarkdownMessageProps {
  content: string;
  streaming?: boolean;
  toolEvents?: ToolEvent[];
  /** Live tool-name status lines for the ThinkingContainer. */
  progressLines?: string[];
  /** True while the agent run is in progress (drives shimmer/working state). */
  isThinkingActive?: boolean;
  /** Sequential reasoning blocks — each its own timeline entry. */
  reasoningBlocks?: string[];
  /** Real assistant-message segments (Phase 3b, message-id-native). When
   *  present the LAST segment is the answer body and earlier segments render as
   *  narration in the ThinkingContainer timeline. Absent for id-less runtimes
   *  (litellm/langgraph), which fall back to `content` + folded reasoning. */
  segments?: { id: string; text: string }[];
  /** Invoked when the user clicks an MCQ choice button (```choices block). */
  onChoice?: (choice: string) => void;
  /** Session ID for resolving relative image paths through the workspace file proxy. */
  sessionId?: string;
  /** Optional file path context for resolving relative image src in markdown (e.g. the .md file path). */
  mdFilePath?: string;
  /** Draw the «names» the tools printed as pills, resolved against
   *  `toolEvents` (WS-27bm S9). The chat turns it on. A document does not. */
  entityPills?: boolean;
}

// ─── Media path resolver (shared with ArtifactViewerModal) ────────────────────

/**
 * Rewrite an image src found inside a markdown message so it routes through the
 * gateway file proxy.
 *
 * Rules (in priority order):
 *  1. Already a full URL (http/https/data:) → pass through unchanged
 *  2. Absolute path starting with /          → treat as workspace-relative and proxy
 *  3. Relative path                          → resolve against the mdFilePath's
 *                                             directory, then proxy
 */
function resolveMediaSrc(
  src: string,
  sessionId: string | undefined,
  mdFilePath: string | undefined,
): string {
  // Full URLs and data URIs pass through unchanged
  if (/^(https?:|data:)/i.test(src)) return src;
  // No session context → can't resolve; return as-is
  if (!sessionId) return src;

  let workspacePath: string;
  if (src.startsWith("/")) {
    // Treat absolute paths as workspace-root-relative
    workspacePath = src.replace(/^\/+/, "");
  } else if (mdFilePath) {
    // Relative: resolve against the directory containing the .md file
    const mdDir = mdFilePath.includes("/")
      ? mdFilePath.substring(0, mdFilePath.lastIndexOf("/"))
      : "";
    const parts = (mdDir ? `${mdDir}/${src}` : src).split("/");
    const resolved: string[] = [];
    for (const part of parts) {
      if (part === "..") resolved.pop();
      else if (part !== ".") resolved.push(part);
    }
    workspacePath = resolved.join("/");
  } else {
    // No mdFilePath context — treat as workspace-root-relative
    workspacePath = src;
  }

  return `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(workspacePath)}`;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const TERMINAL_LANGS = new Set([
  "bash", "sh", "shell", "terminal", "console",
  "zsh", "powershell", "cmd", "fish",
]);

// ─── Copy button ─────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() =>
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        })
      }
      className="text-[11px] text-muted-foreground hover:text-foreground transition-colors px-2 py-0.5 rounded hover:bg-secondary/60 font-mono"
    >
      {copied ? "✓ Copied" : "Copy"}
    </button>
  );
}

// ─── Code block ──────────────────────────────────────────────────────────────

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const isTerminal = TERMINAL_LANGS.has(lang);

  return (
    <div className="my-4 rounded-xl overflow-hidden border border-border/60 bg-background">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-secondary/80 border-b border-border/60">
        {isTerminal ? (
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
            <span className="ml-2 text-[11px] text-muted-foreground font-mono">{lang}</span>
          </div>
        ) : (
          <span className="text-[11px] text-muted-foreground font-mono">{lang || "code"}</span>
        )}
        <CopyButton text={code} />
      </div>

      {/* Syntax-highlighted code, in theme tokens (`lib/codeTheme.ts`): one
          look in both colour modes, no hex. */}
      <SyntaxHighlighter
        style={CODE_THEME}
        language={lang || "text"}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: 0,
          background: "transparent",
          fontSize: "0.785rem",
          padding: "1rem",
          lineHeight: "1.6",
        }}
        codeTagProps={{
          style: {
            fontFamily:
              "ui-monospace, SFMono-Regular, 'SF Mono', Consolas, 'Courier New', monospace",
          },
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

// ─── MCQ choice block ───────────────────────────────────────────────────────
// Renders a ```choices fenced block as clickable buttons. The first non-list
// line (if any) is treated as the question; lines beginning with - or * are
// the selectable options.

function parseChoices(raw: string): { question: string | null; options: string[] } {
  const lines = raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  const dashed = lines.filter((l) => /^[-*]\s+/.test(l));
  if (dashed.length > 0) {
    const firstDashIdx = lines.findIndex((l) => /^[-*]\s+/.test(l));
    const question = firstDashIdx > 0 ? lines.slice(0, firstDashIdx).join(" ") : null;
    const options = dashed.map((l) => l.replace(/^[-*]\s+/, "").trim()).filter(Boolean);
    return { question, options };
  }
  // No dashes — every line is an option.
  return { question: null, options: lines };
}

function ChoiceBlock({
  raw,
  onChoice,
}: {
  raw: string;
  onChoice?: (choice: string) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const { question, options } = parseChoices(raw);
  if (options.length === 0) return null;

  return (
    <div className="my-3 rounded-xl border border-border/60 bg-card/50 p-3">
      {question && (
        <div className="text-sm font-medium text-foreground mb-2.5">{question}</div>
      )}
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => {
          const isPicked = picked === opt;
          return (
            <button
              key={opt}
              disabled={picked !== null}
              onClick={() => {
                setPicked(opt);
                onChoice?.(opt);
              }}
              className={`text-left text-xs rounded-lg border px-3 py-2 transition-colors ${
                isPicked
                  ? "border-emerald-600/70 bg-emerald-900/40 text-emerald-200"
                  : picked !== null
                  ? "border-border bg-card/40 text-muted-foreground cursor-not-allowed"
                  : "border-border bg-secondary/70 text-foreground hover:border-emerald-600/60 hover:bg-secondary"
              }`}
            >
              {isPicked && <span className="mr-1.5 text-emerald-400">✓</span>}
              {opt}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Links ──────────────────────────────────────────────────────────────────
//
// An in-app path (`/projects?task=…`) navigates in THIS tab: it is a page of
// the app the member is already in. It is a real anchor (`ControlLink`), so a
// modified click still opens a tab. Every other URL opens in a new tab, with
// `noopener`. A `mailto:` opens the mail client and needs no tab. Colour is the
// `primary` token, never a palette blue (WS-27bm S9).

export const LINK_CLASS =
  "text-primary underline underline-offset-2 hover:opacity-80 transition-opacity break-all";

function InAppLink({ href, children }: { href: string; children?: ReactNode }) {
  const router = useRouter();
  return (
    <ControlLink href={href} onActivate={() => router.push(href)} className={LINK_CLASS}>
      {children}
    </ControlLink>
  );
}

function MarkdownLink({ href, children }: { href?: string; children?: ReactNode }) {
  if (href && isInAppPath(href)) return <InAppLink href={href}>{children}</InAppLink>;
  if (href?.toLowerCase().startsWith("mailto:")) {
    return <a href={href} className={LINK_CLASS}>{children}</a>;
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
      {children}
    </a>
  );
}

// ─── The Markdown body ─────────────────────────────────────────────────────
//
// The ONE Markdown renderer for chat content: GFM, the token-styled
// components, code blocks and MCQ choices. `MarkdownMessage` draws the
// answer with it, and `GenerativeUINode` draws its `markdown` node with it
// (WS-27bm S8 visual review: that node used bare ReactMarkdown, so its lists
// had no bullets and its tables no cells).
//
// `entityPills` is opt-in (WS-27bm S9). With it on, `remarkEntityPills` turns
// every «name» and bare email into a pill, and a missing space before a bold
// after a full stop is put back. The pills resolve against `entityIndex`, or
// against the nearest `EntityIndexContext` when no index is passed.

export function MarkdownBody({
  content,
  onChoice,
  sessionId,
  mdFilePath,
  entityPills = false,
  entityIndex,
}: {
  content: string;
  onChoice?: (choice: string) => void;
  sessionId?: string;
  mdFilePath?: string;
  entityPills?: boolean;
  entityIndex?: EntityIndex;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={entityPills ? [remarkGfm, remarkEntityPills] : [remarkGfm]}
      components={{
        // ── Entity pills (the plugin's `span[data-entity-pill]`) ──
        // `node` is react-markdown's own prop, and must not reach the DOM.
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        span: ({ node: _node, children, ...rest }) => {
          const attrs = rest as Record<string, unknown>;
          if (entityPills && attrs[PILL_ATTR] !== undefined) {
            const number = attrs[PILL_NUMBER_ATTR];
            return (
              <ChatEntityPill
                text={String(attrs[PILL_TEXT_ATTR] ?? "")}
                number={typeof number === "string" ? number : undefined}
                index={entityIndex}
              />
            );
          }
          return <span {...rest}>{children}</span>;
        },

        // ── Headings ──
        h1: ({ children }) => (
          <h1 className="text-[1.1rem] font-bold text-foreground mt-5 mb-3 pb-1 border-b border-border">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-[1rem] font-semibold text-foreground mt-4 mb-2">
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-[0.9rem] font-semibold text-foreground mt-3 mb-1.5">
            {children}
          </h3>
        ),
        h4: ({ children }) => (
          <h4 className="text-sm font-semibold text-foreground mt-2 mb-1">
            {children}
          </h4>
        ),

        // ── Paragraphs & text ──
        p: ({ children }) => (
          <p className="mb-3 last:mb-0 text-foreground">{children}</p>
        ),
        strong: ({ children }) => (
          <strong className="font-semibold text-foreground">{children}</strong>
        ),
        em: ({ children }) => (
          <em className="italic text-foreground">{children}</em>
        ),
        del: ({ children }) => (
          <del className="line-through text-muted-foreground">{children}</del>
        ),

        // ── Lists ──
        ul: ({ children }) => (
          <ul className="mb-3 ml-5 space-y-1 list-disc list-outside marker:text-muted-foreground">
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol className="mb-3 ml-5 space-y-1 list-decimal list-outside marker:text-muted-foreground">
            {children}
          </ol>
        ),
        li: ({ children }) => (
          <li className="text-foreground pl-0.5">{children}</li>
        ),

        // ── Links ──
        a: ({ href, children }) => <MarkdownLink href={href}>{children}</MarkdownLink>,

        // ── Blockquote ──
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-border pl-4 my-3 text-muted-foreground italic">
            {children}
          </blockquote>
        ),

        // ── Images ──
        // Rewrite src to route through the gateway workspace file proxy.
        // Full URLs (https://…) and data: URIs pass through unchanged.
        img({ src, alt, ...rest }) {
          const rawSrc = typeof src === "string" ? src : "";
          const resolvedSrc = resolveMediaSrc(rawSrc, sessionId, mdFilePath);
          // eslint-disable-next-line @next/next/no-img-element
          return (
            <img
              src={resolvedSrc}
              alt={alt ?? ""}
              {...rest}
              className="max-w-full max-h-96 rounded-lg my-3 border border-border/50 object-contain"
              loading="lazy"
            />
          );
        },

        // ── Tables (GFM) ──
        // A table keeps its words whole and scrolls when it is wider than its
        // box. Cells take `break-words` (overflow-wrap: break-word), which
        // breaks a word only when the word alone is wider than the whole
        // box, and does not shrink the table's minimum width. Round 4 used
        // `wrap-anywhere`, which did shrink it, so every table in every chat
        // broke headers and names mid-word ("FINISHE/D"). A table wider
        // than its box scrolls inside `overflow-x-auto`, with the thin bar
        // visible. Cells size from the box itself (`@container`), not from
        // the viewport, so the rail gets the tight padding at any width.
        // Row lines are a TOP border on each body cell, so the line runs
        // under every column (round 4 fixed a `last:` rule that dropped the
        // line under the last column).
        table: ({ children }) => (
          <div className="@container my-4 max-w-full overflow-x-auto scrollbar-thin rounded-lg border border-border/60" role="region" aria-label="Table" tabIndex={0}>
            <table className="w-full text-[12px] sm:text-[13px] border-collapse">{children}</table>
          </div>
        ),
        thead: ({ children }) => (
          <thead className="bg-secondary/60">{children}</thead>
        ),
        th: ({ children }) => (
          <th className="px-2.5 py-1.5 @md:px-4 @md:py-2 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide break-words">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="px-2.5 py-1.5 @md:px-4 @md:py-2 text-foreground border-t border-border/60 break-words">
            {children}
          </td>
        ),

        // ── GFM task list ──
        // The browser's disabled checkbox draws a black square in light
        // mode. A task list here is read-only, so it draws as an icon.
        input: ({ type, checked }) =>
          type === "checkbox" ? (
            <Icon
              name={checked ? "SquareCheck" : "Square"}
              size={14}
              className={`mr-1 inline-block align-text-bottom ${checked ? "text-primary" : "text-muted-foreground"}`}
              role="img"
              aria-label={checked ? "Done" : "Not done"}
            />
          ) : null,

        // ── HR ──
        hr: () => <hr className="my-5 border-border" />,

        // ── Code (inline + block) ──
        pre: ({ children }) => <>{children}</>,
        code({ className, children }) {
          const match = /language-(\w+)/.exec(className || "");
          const lang = match ? match[1].toLowerCase() : "";
          const codeString = String(children).replace(/\n$/, "");

          // Block code. react-markdown adds a `language-xxx` class only when a
          // language is specified; a fenced block WITHOUT a language has no
          // class, so also treat any multi-line code as a block — otherwise an
          // unlabeled ``` block renders cramped as inline with literal newlines.
          if (match || codeString.includes("\n")) {
            // MCQ choices block — render interactive buttons instead of code.
            if (lang === "choices") {
              return <ChoiceBlock raw={codeString} onChoice={onChoice} />;
            }
            return <CodeBlock lang={lang} code={codeString} />;
          }

          // Inline code
          return (
            <code className="bg-secondary text-foreground rounded px-1.5 py-0.5 font-mono text-[0.82em] border border-border/50">
              {children}
            </code>
          );
        },
      }}
    >
      {entityPills ? spaceBeforeBold(content) : content}
    </ReactMarkdown>
  );
}

// ─── Main component ───────────────────────────────────────────────────────

export default function MarkdownMessage({
  content,
  streaming,
  toolEvents,
  progressLines,
  isThinkingActive,
  reasoningBlocks,
  segments,
  onChoice,
  sessionId,
  mdFilePath,
  entityPills = false,
}: MarkdownMessageProps) {
  // The names this message's tools printed, for the pills (WS-27bm S9).
  const entityIndex = useMemo(
    () => (entityPills ? buildEntityIndex(toolEvents) : undefined),
    [entityPills, toolEvents],
  );
  // ── Segment-native rendering (Phase 3c — VS Code parity) ────────────────────
  // Every assistant TEXT segment is answer BODY, rendered inline in
  // chronological order. Assistant text a model emits *before* a tool call
  // ("Here's the current state: … Let me fix this now.") is real answer content
  // the user must see in the chat — NOT disposable narration to bury in the
  // thinking container. Only genuine chain-of-thought
  // (THINKING_TEXT_MESSAGE_CONTENT → reasoningBlocks) and tool calls belong in
  // the ThinkingContainer. This corrects the earlier last-segment-only split
  // that swept pre-tool answer text into the thinking pane.
  //
  // Absent segment ids (litellm/langgraph/legacy rows) → fall back to `content`
  // exactly as before (those runtimes never populated segments).
  const hasSegments = !!(segments && segments.length > 0);
  const answerBody = hasSegments
    // Join every non-empty segment in order — the full assistant answer,
    // including any prose emitted between tool calls. Blank a stale trailing
    // empty segment (a tool just opened a new one, no text yet) so nothing
    // flickers. Fall back to `content` if segments somehow hold no text.
    ? (segments!.map((s) => s.text).filter((t) => t.trim()).join("\n\n")
        || content)
    : content;
  // Assistant text NEVER goes to the ThinkingContainer anymore — it's body.
  const narrationSegments: string[] = [];

  const hasTools =
    (toolEvents && toolEvents.length > 0) ||
    (progressLines && progressLines.length > 0);
  const hasReasoning = !!(reasoningBlocks && reasoningBlocks.length > 0);
  const hasNarration = false;
  // Show the ThinkingContainer for the ENTIRE active phase (not just until the
  // first token). The container disappears mid-stream if we gate on content===""
  // — the user sees "Thinking…" flash then nothing while text is still arriving.
  // After completion: keep it only if there are tools, reasoning, or narration.
  const showThinking = isThinkingActive || hasTools || hasReasoning || hasNarration;

  return (
    <div className="text-[12px] sm:text-[13px] text-foreground leading-relaxed min-w-0">
      {/* Thinking container — groups the whole working phase (reasoning + tool calls + status) */}
      {showThinking && (
        <div className="mb-3">
          <ThinkingContainer
            toolEvents={toolEvents ?? []}
            progressLines={progressLines ?? []}
            reasoningBlocks={reasoningBlocks}
            narrationSegments={narrationSegments}
            isActive={!!isThinkingActive}
          />
        </div>
      )}

      {/* Markdown body — the one renderer, shared with GenerativeUINode. */}
      <MarkdownBody
        content={answerBody}
        onChoice={onChoice}
        sessionId={sessionId}
        mdFilePath={mdFilePath}
        entityPills={entityPills}
        entityIndex={entityIndex}
      />

      {/* Streaming cursor — only once text is actually streaming, so it doesn't
          float with no text during a tool-only phase (the ThinkingContainer
          shows activity there instead). */}
      {streaming && answerBody.trim().length > 0 && (
        <span className="inline-block w-[2px] h-[1em] bg-zinc-300 animate-pulse ml-0.5 align-middle rounded-full" />
      )}
    </div>
  );
}
