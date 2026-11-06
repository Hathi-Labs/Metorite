"use client";

// Per-message audit timeline (review 3.9). Reply Zero's History is a global
// feed of what the rules engine did across the mailbox; this is its inverse —
// open one email and read its whole story, oldest first: when it arrived, and
// each rule run that classified, labelled, drafted, moved, or failed on it.
// Reuses the same audit rows (email_executed_rules), scoped to this one message.

import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { useEffect, useState } from "react";
import type { MessageTimeline, MessageTimelineEvent } from "../lib/types";
import { getMessageTimeline } from "../lib/api";
import { fullDateLabel } from "../lib/utils";
import { GONE_MAIL_LABEL, isGoneMailSkip, showActionErrors } from "./automation/ai-settings/actionFormat";

export function MessageTimelineModal({
  messageId,
  subject,
  onClose,
}: {
  messageId: string;
  subject?: string | null;
  onClose: () => void;
}) {
  const [data, setData] = useState<MessageTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getMessageTimeline(messageId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled)
          setErr((e as Error).message || "Couldn't load this activity.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [messageId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const events = data?.events ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-border flex-shrink-0">
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">Activity</div>
            <div className="text-[11px] text-muted-foreground truncate">
              {data?.subject || subject || "This message"}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground flex-shrink-0"
            title="Close (Esc)"
          >
            <AppIcon name="X" size={16} />
          </button>
        </div>

        <div className="overflow-y-auto px-4 py-4 flex-1">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-6">
              <AppIcon name="Loader2" className="animate-spin" size={14} /> Loading…
            </div>
          ) : err ? (
            <div className="text-xs text-destructive">{err}</div>
          ) : events.length === 0 ? (
            <div className="text-xs text-muted-foreground italic py-4">
              No automation has touched this message yet.
            </div>
          ) : (
            <ol className="relative">
              {events.map((ev, i) => (
                <TimelineRow key={i} ev={ev} last={i === events.length - 1} />
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

function TimelineRow({
  ev,
  last,
}: {
  ev: MessageTimelineEvent;
  last: boolean;
}) {
  const { icon: Icon, tint } = rowIcon(ev);
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      {/* Connector spine between dots. */}
      {!last && (
        <span
          className="absolute left-[11px] top-6 bottom-0 w-px bg-border"
          aria-hidden
        />
      )}
      <span
        className={`relative z-10 flex-shrink-0 mt-0.5 h-6 w-6 rounded-full border border-border flex items-center justify-center bg-card ${tint}`}
      >
        <Icon size={13} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-foreground">{rowTitle(ev)}</div>
        {ev.reason && ev.kind !== "received" && (
          <div className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5">
            {ev.reason}
          </div>
        )}
        {ev.actions && ev.actions.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {ev.actions.map((a, i) => (
              <span
                key={i}
                className="text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-secondary text-foreground/70"
              >
                {a.replace(/_/g, " ").toLowerCase()}
              </span>
            ))}
          </div>
        )}
        {showActionErrors(isGoneMailSkip(ev), ev.action_errors?.length ?? 0) && ev.action_errors && (
          <div className="mt-1 space-y-0.5">
            {ev.action_errors.map((e, i) => (
              <div key={i} className="text-[10px] text-destructive">
                {e.type}: {e.error}
              </div>
            ))}
          </div>
        )}
        <div className="text-[10px] text-muted-foreground/70 mt-0.5">
          {ev.at ? fullDateLabel(ev.at) : ""}
          {ev.kind !== "received" && ev.automated === false ? " · manual" : ""}
        </div>
      </div>
    </li>
  );
}

function rowIcon(ev: MessageTimelineEvent): {
  icon: ThemedIcon;
  tint: string;
} {
  if (ev.kind === "received") return { icon: themedIcon("Inbox"), tint: "text-primary" };
  if (ev.kind === "skipped")
    return { icon: themedIcon("MinusCircle"), tint: "text-muted-foreground" };
  if (ev.status === "FAILED" || (ev.action_errors?.length ?? 0) > 0)
    return { icon: themedIcon("AlertTriangle"), tint: "text-destructive" };
  return { icon: themedIcon("CheckCircle2"), tint: "text-emerald-500" };
}

export function rowTitle(ev: MessageTimelineEvent): string {
  if (ev.kind === "received")
    return ev.from ? `Received from ${ev.from}` : "Received";
  if (ev.kind === "skipped")
    return isGoneMailSkip(ev) ? GONE_MAIL_LABEL : "Checked — no rule matched";
  const name = ev.rule_name || "A rule";
  if (ev.status === "FAILED") return `${name} failed to apply`;
  if (ev.status === "UNDONE") return `${name} was undone`;
  if (ev.status === "REJECTED") return `${name} was rejected`;
  return `${name} applied`;
}
