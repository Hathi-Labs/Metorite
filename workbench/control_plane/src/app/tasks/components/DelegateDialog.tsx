"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import type { GtdItem, Person } from "../lib/types";
import { initials } from "../lib/utils";

// Hand a task to a teammate and start the clock on it.
//
// ⚠️ **Re-cut 2026-08-25 (D52, WS-39 S3a-client slice 4).** This dialog used to
// say "delegating a LOCAL task to a teammate can't stay local — the teammate
// lives in the PM tool", and it asked for a workspace and a ClickUp list before
// it would submit. Both premises are gone: Metorite IS the PM system of record
// (D52), and there is one task store, so a teammate can be assigned a task
// exactly where it already is.
//
// That mattered more than a stale comment. With the connectors retired,
// `accounts` was always empty, so the dialog rendered "Connect a workspace
// first to delegate tasks" and `canSubmit` could never become true — the
// Waiting-For flow, which migration 188 exists to hold, had no working entry
// point at all.
//
// What survives is the part that was always the product: WHO has it, WHAT you
// asked for, and SINCE WHEN. The since-when is written server-side
// (`delegated_at`), because 188 CHECKs that a chase has an age.
//
// ⚠️ `expected_by` is deliberately NOT collected here. It means an EXPLICIT
// human promise (settled 2026-08-02); left null the overdue line falls back to
// the task's own `due_at`, read live. A field that quietly defaulted to the due
// date would invent a promise nobody made and then let it go stale — the exact
// bug that fix closed at four insert sites.
export function DelegateDialog({
  item,
  assignee,
  onClose,
}: {
  item: GtdItem;
  assignee: Person;
  onClose: () => void;
}) {
  const delegate = useTaskStore((s) => s.delegateToPerson);

  const [nextAction, setNextAction] = useState<string>(
    item.nextAction || item.title,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await delegate(item.id, {
        assignee,
        nextAction: nextAction.trim() || undefined,
      });
      onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Couldn't delegate — try again.",
      );
      setBusy(false);
    }
  };

  return (
    <div
      className="chat-fade-in fixed inset-0 z-[90] flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[86vh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl sm:rounded-2xl sm:border"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
          <span className="inline-flex items-center gap-2 text-sm font-semibold text-foreground">
            <Icon name="UserPlus" className="h-4 w-4 text-primary" />
            Delegate to {assignee.name}
          </span>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={onClose} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="mb-3 flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <Icon name="UserCheck" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/70" />
            <span>
              {assignee.name.split(" ")[0]} becomes the owner and this moves to
              your Waiting-For list. The task itself does not move.
            </span>
          </p>

          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              The ask
            </p>
            <input
              value={nextAction}
              onChange={(e) => setNextAction(e.target.value)}
              placeholder="What you need them to do…"
              className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
            />
          </div>

          {error && <p className="mt-2 text-[11px] text-destructive">{error}</p>}
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-xs">
            Cancel
          </Button>
          <Button size="none" radius="keep" layout="inline-flex items-center" type="button" disabled={!canSubmit} onClick={submit} className="gap-1.5 rounded-md px-3 py-1.5 text-xs">
            {busy ? (
              <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary-foreground/20 text-[8px] font-bold">
                {initials(assignee.name)}
              </span>
            )}
            Delegate
          </Button>
        </div>
      </div>
    </div>
  );
}
