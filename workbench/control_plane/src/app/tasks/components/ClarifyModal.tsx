"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect } from "react";
import { clarifyQueue, isClarifiable } from "../lib/clarify";
import { useTaskStore } from "../lib/taskStore";
import { useVisualViewport } from "../lib/useVisualViewport";
import { ClarifyPanel } from "./ClarifyPanel";

// Focused overlay for processing an inbox item. Bound to the store's selected
// item: store.clarify advances the selection to the next inbox item, so the
// modal walks the inbox automatically and closes when it hits zero.
//
// Premium touch: single-key shortcuts blitz through the obvious items without
// touching the mouse — t trash · s someday · r reference · 2 do-now — each
// files the current item and jumps to the next. (Disabled while typing in a
// field so the next-action input still works.)
export function ClarifyModal() {
  const open = useTaskStore((s) => s.clarifyModalOpen);
  const close = useTaskStore((s) => s.closeClarify);
  const clarify = useTaskStore((s) => s.clarify);
  const skip = useTaskStore((s) => s.skipToNextInbox);
  const selectedItemId = useTaskStore((s) => s.selectedItemId);
  const items = useTaskStore((s) => s.items);
  // S6e: an untriaged "From Projects" row is clarifiable too. Its disposition
  // is derived (NEXT, SOMEDAY or WAITING), never INBOX, so a test on INBOX
  // alone closed the modal on its first render (audit 2026-09-24).
  const fromProjectIds = useTaskStore((s) => s.fromProjectIds);
  const done = useTaskStore((s) => s.clarifiedThisSession);
  const selectItem = useTaskStore((s) => s.selectItem);

  const processed = useTaskStore((s) => s.processedThisSession);
  const vp = useVisualViewport();
  const item = selectedItemId
    ? items.find((i) => i.id === selectedItemId)
    : undefined;
  const queue = clarifyQueue(items, fromProjectIds, done);
  const inboxLeft = queue.length;
  const total = processed + inboxLeft;
  const pct = total ? Math.round((processed / total) * 100) : 0;
  const clarifiable = !!item && isClarifiable(item, fromProjectIds, done);
  const active = open && clarifiable;
  const nextId = queue[0]?.id;

  // The current item left the walk (decided, or re-read out of the group).
  // Advance while the queue still holds items. Close only when it is empty.
  useEffect(() => {
    if (!open || clarifiable) return;
    if (nextId) selectItem(nextId);
    else close();
  }, [open, clarifiable, nextId, selectItem, close]);

  // Keyboard: Escape closes; t/s/r/2 file the current item and advance.
  useEffect(() => {
    if (!active || !item) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        return;
      }
      const el = e.target as HTMLElement | null;
      const typing =
        !!el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.isContentEditable);
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "]" || e.key === "ArrowRight") {
        e.preventDefault();
        skip();
        return;
      }
      switch (e.key.toLowerCase()) {
        case "t":
          e.preventDefault();
          clarify(item.id, { kind: "trash" });
          break;
        case "s":
          e.preventDefault();
          clarify(item.id, { kind: "someday" });
          break;
        case "r":
          e.preventDefault();
          clarify(item.id, { kind: "reference" });
          break;
        case "2":
          e.preventDefault();
          clarify(item.id, { kind: "do-now" });
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, item, clarify, close, skip]);

  if (!active || !item) return null;

  return (
    <div
      className="chat-fade-in fixed inset-x-0 bottom-0 top-0 z-[80] flex items-end justify-center bg-black/50 p-0 pt-0 sm:items-start sm:p-4 sm:pt-[8vh]"
      style={vp ? { top: vp.top, height: vp.height, bottom: "auto" } : undefined}
      onClick={close}
    >
      <div
        // Mobile: a FIXED near-full-height sheet (small top gap keeps the
        // sheet mental model + tap-to-dismiss). Content-sized sheets jump
        // around as the AI proposal arrives/changes — a stable frame reads
        // calmer while blitzing through the inbox. Desktop keeps the sized
        // dialog.
        className="flex h-[calc(100%-1.75rem)] max-h-full w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl pb-safe sm:h-auto sm:max-h-[84vh] sm:rounded-2xl sm:border sm:pb-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 border-b border-border">
          <div className="flex items-center justify-between px-4 py-2">
            <span className="text-xs font-medium text-muted-foreground">
              Processing inbox · {inboxLeft} left
            </span>
            <div className="flex items-center gap-1">
              {inboxLeft > 1 && (
                <Button variant="ghost" size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={skip} title="Leave it in the inbox to decide later" className="gap-1 rounded-md px-2 py-1 text-[11px]">
                  <Icon name="SkipForward" className="h-3.5 w-3.5" />
                  Skip
                </Button>
              )}
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={close} aria-label="Close" className="rounded-md">
                <Icon name="X" className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="h-0.5 w-full bg-secondary" aria-hidden>
            <div
              className="tech-transition h-full bg-primary"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {/* keyed by id → the wizard resets as the modal advances to the next item */}
          <ClarifyPanel key={item.id} item={item} />
        </div>
        <div className="hidden shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-[10px] text-muted-foreground sm:flex">
          <span className="font-semibold uppercase tracking-wide">Shortcuts</span>
          <Kbd>↵</Kbd> accept
          <Kbd>t</Kbd> trash
          <Kbd>s</Kbd> someday
          <Kbd>r</Kbd> reference
          <Kbd>2</Kbd> do now
          <Kbd>]</Kbd> skip
          <Kbd>esc</Kbd> close
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-border px-1 py-0.5 font-mono text-[9px] text-foreground">
      {children}
    </kbd>
  );
}
