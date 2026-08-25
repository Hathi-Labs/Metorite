"use client";

import { useEffect, useMemo, useState } from "react";
import Icon from "@/components/Icon";
import { useTaskStore } from "../lib/taskStore";
import type { GtdItem, Person } from "../lib/types";
import { initials } from "../lib/utils";
import { DelegateDialog } from "./DelegateDialog";

// The "Delegate" popup — opened from the Delegate nudge pill / Suggestion
// column (store.openDelegate(id)). One card, scoped to THIS task, listing only
// ELIGIBLE delegates (a synced task's workspace members; the org roster for a
// local task). Mirrors the Next-Action detail's assignee flow:
//   • SYNCED task → multi-select the owner set, Apply patches `assignees`
//     (back-syncs the add/remove delta to ClickUp).
//   • LOCAL task → picking a person routes into the promote-to-ClickUp dialog
//     (a teammate can't see a private local task), same as the detail card.
// Global, mounted in page.tsx next to Schedule/Eliminate.
export function DelegatePopup() {
  const delegateItemId = useTaskStore((s) => s.delegateItemId);
  const items = useTaskStore((s) => s.items);
  const closeDelegate = useTaskStore((s) => s.closeDelegate);

  const item = delegateItemId
    ? items.find((i) => i.id === delegateItemId)
    : undefined;
  if (!item) return null;
  // Keyed by task so the selection state resets when a different task opens.
  return <DelegateBody key={item.id} item={item} onClose={closeDelegate} />;
}

/** Same person across the roster and the assignee set — by provider id when
 *  both have one, else by name. */
function samePerson(a: Person, b: Person): boolean {
  if (a.providerUserId && b.providerUserId)
    return a.providerUserId === b.providerUserId;
  return a.name === b.name;
}

function DelegateBody({
  item,
  onClose,
}: {
  item: GtdItem;
  onClose: () => void;
}) {
  const people = useTaskStore((s) => s.people);
  const updateItem = useTaskStore((s) => s.updateItem);
  const loadPeople = useTaskStore((s) => s.loadPeople);

  // The roster used to be loaded by opening the sidebar's People view, which
  // was removed when the directory moved to /people. Loading it HERE, where it
  // is actually needed, is what stops delegation quietly offering nobody —
  // a broken picker that looks like an empty company.
  useEffect(() => {
    if (people.length === 0) void loadPeople();
  }, [people.length, loadPeople]);

  // Everyone in the directory. ⚠️ There used to be a narrower arm here: a
  // SYNCED task offered only its workspace's MEMBERS, because a delegate who
  // could not see the task in the tool could not act on it. That constraint
  // died with the tool (D52, WS-39 S3a-client slice 4) — there is one store,
  // and who can see a task is decided by the project's grants, not by somebody
  // else's seat list.
  const eligible: Person[] = people;

  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Person[]>(
    item.assignees ?? (item.assignee ? [item.assignee] : []),
  );
  const [busy, setBusy] = useState(false);
  // LOCAL task: the picked person to promote-to-ClickUp for (opens the
  // destination dialog — same flow as the detail card's assignee pick).
  const [promoteTo, setPromoteTo] = useState<Person | null>(null);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return eligible;
    return eligible.filter((p) => p.name.toLowerCase().includes(needle));
  }, [eligible, q]);

  const toggle = (p: Person) =>
    setSelected((cur) =>
      cur.some((a) => samePerson(a, p))
        ? cur.filter((a) => !samePerson(a, p))
        : [...cur, p],
    );

  const apply = () => {
    setBusy(true);
    updateItem(item.id, { assignees: selected });
    onClose();
  };

  if (promoteTo) {
    return (
      <DelegateDialog
        item={item}
        assignee={promoteTo}
        onClose={() => {
          setPromoteTo(null);
          onClose();
        }}
      />
    );
  }

  return (
    <div
      className="chat-fade-in fixed inset-0 z-[95] flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-sm flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl sm:rounded-2xl sm:border"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="UserPlus" className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-foreground">
              Delegate this task
            </h2>
            <p className="truncate text-[11px] text-muted-foreground">
              {item.title}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="tech-transition rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <Icon name="X" className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="mb-2 text-[11px] text-muted-foreground">
            Picking someone makes them the owner and moves this to your
            Waiting-For list.
          </p>
          {eligible.length > 8 && (
            <div className="relative mb-2">
              <Icon name="Search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search people…"
                className="w-full rounded-md border border-border bg-background/60 py-1.5 pl-8 pr-3 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
              />
            </div>
          )}
          {visible.length === 0 ? (
            <p className="rounded-md border border-border bg-background/40 px-3 py-2 text-xs text-muted-foreground">
              {eligible.length === 0
                ? "No eligible people — connect a workspace to delegate."
                : "No one matches that search."}
            </p>
          ) : (
            <div className="flex flex-col gap-1">
              {visible.map((p) => {
                const on = selected.some((a) => samePerson(a, p));
                return (
                  <button
                    key={p.providerUserId || p.name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setPromoteTo(p)}
                    className={[
                      "tech-transition flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left text-sm",
                      on
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-foreground hover:bg-secondary",
                    ].join(" ")}
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[9px] font-bold text-primary">
                      {initials(p.name)}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                    {on && <Icon name="Check" className="h-3.5 w-3.5 shrink-0" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* ⚠️ The multi-select owner footer was DELETED here (WS-39
            S3a-client slice 4). It was the SYNCED arm: pick several people,
            apply in one write. Under one store that is a fine thing to want
            — but it is ASSIGNMENT, and this component is Delegate. Picking
            someone here now always means "they own it, I am waiting on
            them", which is the GTD semantic the Waiting-For list and
            migration 188 exist for, and it is the same for every task
            instead of depending on where the task was imported from.
            Multi-owner editing is unaffected: the task detail's assignee
            field still does it, and so does /projects. */}
      </div>
    </div>
  );
}
