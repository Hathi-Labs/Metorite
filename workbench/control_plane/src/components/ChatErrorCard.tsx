"use client";

import React from "react";
import type { ParsedAgentError } from "@/lib/parseAgentError";

// ── Error card — shown inline in the message thread ──────────────────────────
export default function ErrorCard({ parsed, compact = false }: { parsed: ParsedAgentError; compact?: boolean }) {
  const [copied, setCopied] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(parsed.raw).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const codeLabel = parsed.code === 429 ? "429 Rate limit"
    : parsed.code === 401 ? "401 Unauthorized"
    : parsed.code === 402 ? "402 Payment required"
    : parsed.code === 400 ? "400 Bad request"
    : parsed.code === 404 ? "404 Not found"
    : null;

  return (
    <div className={`rounded-xl border border-red-900/50 bg-red-950/30 ${compact ? "px-3 py-2" : "px-4 py-3"} text-sm`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-red-300 font-semibold">{parsed.title}</span>
            {codeLabel && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-red-800/60 text-red-400">{codeLabel}</span>
            )}
          </div>
          {!compact && (
            <p className="mt-1 text-xs text-muted-foreground leading-relaxed">{parsed.detail}</p>
          )}
          <div className="mt-2 flex items-start gap-1.5">
            <span className="text-warning shrink-0 text-xs mt-0.5">→</span>
            <p className="text-xs text-warning/90 leading-relaxed">{parsed.suggestion}</p>
          </div>
        </div>
        <button
          onClick={handleCopy}
          title="Copy full error"
          className="shrink-0 text-[10px] px-2 py-1 rounded border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 tech-transition mt-0.5 whitespace-nowrap"
        >
          {copied ? "Copied!" : "Copy error"}
        </button>
      </div>
      {!compact && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground tech-transition"
          >
            {expanded ? "▲ Hide full error" : "▼ Show full error"}
          </button>
          {expanded && (
            <pre className="mt-1.5 text-[10px] text-muted-foreground bg-muted/60 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-40 overflow-y-auto">
              {parsed.raw}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
