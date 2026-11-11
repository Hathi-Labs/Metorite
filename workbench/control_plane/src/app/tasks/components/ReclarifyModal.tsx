"use client";

import { useCloseOnBarTap } from "@/lib/phoneBarTap";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect } from "react";
import { useTaskStore } from "../lib/taskStore";
import { useVisualViewport } from "../lib/useVisualViewport";
import { ClarifyPanel } from "./ClarifyPanel";

// Re-clarify an ALREADY-processed task — the same wizard as the inbox Clarify,
// but opened on any task (a synced ClickUp task that skipped Clarify, or one
// that turned out to need breaking down into next actions). Unlike ClarifyModal
// this doesn't walk the inbox: it's a one-shot edit that closes on apply
// (ClarifyPanel calls onDone). For a synced task the ClickUp destination stays
// locked (the panel renders it read-only via proposal.lockedDestination).
export function ReclarifyModal() {
  const id = useTaskStore((s) => s.reclarifyItemId);
  const close = useTaskStore((s) => s.closeReclarify);
  const items = useTaskStore((s) => s.items);
  const vp = useVisualViewport();
  const item = id ? items.find((i) => i.id === id) : undefined;

  // A tap on a tab of the phone's bar closes it, and the tab then acts.
  useCloseOnBarTap(close, !!item);

  // The item vanished (deleted/undone elsewhere) → close.
  useEffect(() => {
    if (id && !item) close();
  }, [id, item, close]);

  useEffect(() => {
    if (!item) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, close]);

  if (!item) return null;

  return (
    <div
      className="chat-fade-in fixed inset-x-0 bottom-0 top-0 z-[80] flex items-end justify-center bg-black/50 p-0 pt-0 sm:items-start sm:p-4 sm:pt-[8vh]"
      style={vp ? { top: vp.top, height: vp.height, bottom: "auto" } : undefined}
      onClick={close}
    >
      <div
        className="flex max-h-full w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl pb-safe sm:max-h-[84vh] sm:rounded-2xl sm:border sm:pb-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2">
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Icon name="RefreshCw" className="h-3.5 w-3.5 text-primary" />
            Re-clarify task
          </span>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={close} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ClarifyPanel key={item.id} item={item} reclarify onDone={close} />
        </div>
      </div>
    </div>
  );
}
