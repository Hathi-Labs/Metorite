"use client";

import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { Email } from "../lib/types";
import { getEmail } from "../lib/api";
import { fullDateLabel } from "../lib/utils";
import { MessageContent } from "./MessageContent";
import { remoteHtmlId } from "../lib/htmlPrefetch";

/**
 * A lightweight, closable email preview. Fetches the full message by id and
 * renders it in a modal — used from the History and Test tabs so a row can be
 * inspected without leaving the assistant. Pass an `Email` via `seed` to show
 * headers instantly while the body loads.
 */
export function EmailPreviewModal({
  messageId,
  seed,
  onClose,
}: {
  messageId: string;
  seed?: Email | null;
  onClose: () => void;
}) {
  const [email, setEmail] = useState<Email | null>(seed ?? null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getEmail(messageId)
      .then((e) => {
        if (!cancelled) setEmail(e);
      })
      .catch((e) => {
        if (!cancelled) setErr((e as Error).message || "Couldn't load this email.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [messageId]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const e = email;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-border flex-shrink-0">
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground truncate">
              {e?.subject || (loading ? "Loading…" : "(no subject)")}
            </div>
            {e && (
              <div className="text-[11px] text-muted-foreground truncate">
                {e.from?.name || e.from?.email}
                {e.from?.email && e.from?.name ? ` <${e.from.email}>` : ""}
                {e.receivedAt ? ` · ${fullDateLabel(e.receivedAt)}` : ""}
              </div>
            )}
            {e?.to?.length ? (
              <div className="text-[10px] text-muted-foreground/70 truncate">
                To: {e.to.map((t) => t.name || t.email).join(", ")}
              </div>
            ) : null}
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground flex-shrink-0"
            title="Close (Esc)"
          >
            <Icon name="X" size={16} />
          </button>
        </div>
        <div className="overflow-y-auto px-4 py-3 flex-1">
          {loading && !e ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-6">
              <Icon name="Loader2" className="animate-spin" size={14} /> Loading…
            </div>
          ) : err ? (
            <div className="text-xs text-destructive">{err}</div>
          ) : e && (e.bodyHtml || e.bodyText) ? (
            <MessageContent html={e.bodyHtml} text={e.bodyText} remoteId={remoteHtmlId(e)} />
          ) : (
            <div className="text-xs text-muted-foreground italic py-2">
              No preview text.
            </div>
          )}
          {(e?.attachments?.length ?? 0) > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {e!.attachments!.map((a) => (
                <span
                  key={a.id}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-border text-muted-foreground"
                >
                  <Icon name="Paperclip" size={10} /> {a.filename}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
