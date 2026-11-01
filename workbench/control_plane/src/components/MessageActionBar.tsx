"use client";

/**
 * MessageActionBar — per-message actions shown inline with the timestamp
 * underneath each message bubble.  Always visible (no hover-reveal).
 *
 * Copy | Edit (user only) | Retry (assistant only) | 👍 👎 (assistant only)
 */

import { useState } from "react";

interface MessageActionBarProps {
  content: string;
  messageId: string;
  role: "user" | "assistant";
  sessionId?: string;
  onEdit?: () => void;
  onRetry?: () => void;
}

export default function MessageActionBar({
  content,
  messageId,
  role,
  sessionId,
  onEdit,
  onRetry,
}: MessageActionBarProps) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(null);

  const copy = () => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const sendVote = (v: "up" | "down") => {
    const next = vote === v ? null : v;
    setVote(next);
    // Persist the vote (best-effort) as an audit event via /api/feedback.
    // Un-voting (next === null) just clears the local highlight.
    if (next) {
      void fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, vote: next, session_id: sessionId }),
        keepalive: true,
      }).catch(() => {});
    }
  };

  const btn =
    "text-muted-foreground hover:text-foreground tech-transition px-1 py-0.5 rounded hover:bg-secondary text-[10px] glass:rounded-full glass:px-2 glass:py-1 glass:text-caption";

  return (
    <>
      {/* Copy — always visible */}
      <button onClick={copy} className={btn} title="Copy" aria-label="Copy message">
        {copied ? "✓ Copied" : "Copy"}
      </button>

      {/* Edit — always visible (user messages only) */}
      {role === "user" && onEdit && (
        <button
          onClick={onEdit}
          className={btn}
          title="Edit"
          aria-label="Edit message"
        >
          Edit
        </button>
      )}

      {/* Retry — assistant messages only */}
      {role === "assistant" && onRetry && (
        <button
          onClick={onRetry}
          className={btn}
          title="Retry"
          aria-label="Retry response"
        >
          Retry
        </button>
      )}

      {/* Thumbs — always visible (assistant messages only) */}
      {role === "assistant" && (
        <>
          <button
            onClick={() => sendVote("up")}
            className={`${btn} ${vote === "up" ? "text-success" : ""}`}
            title="Good response"
            aria-label="Good response"
          >
            👍
          </button>
          <button
            onClick={() => sendVote("down")}
            className={`${btn} ${vote === "down" ? "text-destructive" : ""}`}
            title="Bad response"
            aria-label="Bad response"
          >
            👎
          </button>
        </>
      )}
    </>
  );
}
