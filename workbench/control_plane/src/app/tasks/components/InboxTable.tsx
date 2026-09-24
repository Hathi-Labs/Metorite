"use client";

import { useRouter } from "next/navigation";
import Icon from "@/components/Icon";
import { taskDeepLink } from "@/app/projects/lib/card";
import { MyTask } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import { proposeClarification, sortShapeSummary, type SortBucket } from "../lib/clarify";
import { type InboxKind, inboxRowActions } from "../lib/inbox";
import { promoteAllowed } from "../lib/promote";
import { relativeTime } from "../lib/utils";
import { InboxOrigin } from "./InboxOrigin";

// Chip styling per SORT bucket: do-now reads loud (primary), reference/someday
// stay quiet (muted), trash leans destructive — so the triage read is instant.
const SORT_CHIP: Record<SortBucket, string> = {
  "do-now": "border-primary/30 bg-primary/10 text-primary",
  actionable: "border-border bg-secondary/60 text-foreground",
  reference: "border-border bg-transparent text-muted-foreground",
  someday: "border-border bg-transparent text-muted-foreground",
  trash: "border-destructive/30 bg-destructive/10 text-destructive",
};

// Dense Notion-style list view for the inbox: one row per task with the
// attributes that matter at triage time as columns — far more items on
// screen than the card view. Someday · clarify reveal on hover. The kind's
// own actions (S6g: Move / Delete on a capture, Not mine / Open on board on a
// board row) are always visible. Row click selects, title click opens Clarify.
// The "Where" column is the card's origin marker (`InboxOrigin`).
export function InboxTable({
  items,
  kindOf,
  onMove,
  cursorId,
  selectedIds,
  onSelectToggle,
}: {
  items: MyTask[];
  /** S6g — which kind a row is (`inbox.ts`). */
  kindOf: (item: MyTask) => InboxKind;
  /** S6g — open the one promote dialog, hosted by the Inbox. */
  onMove: (id: string) => void;
  cursorId: string | null;
  /** Read-only: the table never mutates the caller's selection. */
  selectedIds: ReadonlySet<string>;
  /** `shift` extends the selection from the anchor (`@/lib/selection`). */
  onSelectToggle: (id: string, shift: boolean) => void;
}) {
  const people = useTaskStore((s) => s.people);
  const projects = useTaskStore((s) => s.projects);
  const openClarify = useTaskStore((s) => s.openClarify);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const selectItem = useTaskStore((s) => s.selectItem);
  const router = useRouter();

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[640px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-secondary/40 text-[10px] uppercase tracking-wide text-muted-foreground">
            <th className="w-7 px-2 py-1.5" aria-label="Select" />
            <th className="px-2 py-1.5 font-medium">Capture</th>
            <th className="w-36 px-2 py-1.5 font-medium">AI suggests</th>
            <th className="w-32 px-2 py-1.5 font-medium">From</th>
            <th className="w-56 px-2 py-1.5 font-medium">Where</th>
            <th className="w-20 px-2 py-1.5 font-medium">Age</th>
            <th className="w-28 px-2 py-1.5 font-medium" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const p = proposeClarification(item, people, projects);
            const selected = selectedIds.has(item.id);
            const kind = kindOf(item);
            const actions = inboxRowActions({
              kind,
              canPromote: promoteAllowed(item),
              move: () => onMove(item.id),
              // TRASH on my overlay, never a delete (see `InboxCard`).
              notMine: () => quickDispose(item.id, "TRASH"),
              remove: () => requestDelete([item.id]),
              openBoard: () => router.push(taskDeepLink(item)),
            });
            return (
              <tr
                key={item.id}
                onClick={() => selectItem(item.id)}
                className={[
                  "group cursor-pointer border-b border-border/60 text-sm last:border-b-0",
                  cursorId === item.id ? "bg-primary/5" : "hover:bg-secondary/50",
                ].join(" ")}
              >
                <td className="px-2 py-1.5 align-middle">
                  <button
                    type="button"
                    aria-label={selected ? "Deselect" : "Select"}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectToggle(item.id, e.shiftKey);
                    }}
                    className={[
                      "tech-transition h-3.5 w-3.5 rounded-full border-2",
                      selected ? "border-primary bg-primary" : "border-border group-hover:border-primary/50",
                    ].join(" ")}
                  />
                </td>
                <td className="max-w-0 px-2 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        openClarify(item.id);
                      }}
                      className="tech-transition min-w-0 truncate text-left text-foreground hover:text-primary"
                      title={item.title}
                    >
                      {item.title}
                    </button>
                    {item.attachments && item.attachments.length > 0 && (
                      <span
                        className="inline-flex shrink-0 items-center gap-0.5 text-[10px] text-muted-foreground"
                        title={item.attachments.map((a) => a.name).join(", ")}
                      >
                        <Icon name="Paperclip" className="h-3 w-3" />
                        {item.attachments.length}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-2 py-1.5">
                  {(() => {
                    // Sort→Shape read: the SORT verdict (actionable? do now?)
                    // as a colored chip, with the SIZE (single / steps /
                    // project) and any delegation shown beneath it.
                    const s = sortShapeSummary(p);
                    return (
                      <div className="flex flex-col items-start gap-0.5">
                        <span
                          className={[
                            "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                            SORT_CHIP[s.sort],
                          ].join(" ")}
                        >
                          {s.sortLabel}
                        </span>
                        {(s.sizeLabel || s.delegate) && (
                          <span className="text-[10px] text-muted-foreground">
                            {s.sizeLabel}
                            {s.delegate && (
                              <>
                                {s.sizeLabel ? " · " : ""}
                                → {s.delegateTo ?? "delegate"}
                              </>
                            )}
                          </span>
                        )}
                      </div>
                    );
                  })()}
                </td>
                <td className="max-w-0 truncate px-2 py-1.5 text-muted-foreground">
                  {item.origin?.kind === "email" ? (
                    <span
                      className="inline-flex max-w-full items-center gap-1"
                      title={item.origin.subject}
                    >
                      <Icon name="Mail" className="h-3 w-3 shrink-0" />
                      <span className="truncate">
                        {item.origin.fromName || item.origin.fromEmail}
                      </span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40">—</span>
                  )}
                </td>
                <td className="max-w-0 px-2 py-1.5 text-[11px]">
                  <InboxOrigin item={item} kind={kind} />
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 text-[11px] text-muted-foreground">
                  {relativeTime(item.createdAt)}
                </td>
                <td className="px-2 py-1.5">
                  <span className="flex items-center justify-end gap-0.5">
                    <button
                      type="button"
                      title="Someday / maybe"
                      onClick={(e) => {
                        e.stopPropagation();
                        quickDispose(item.id, "SOMEDAY");
                      }}
                      className="tech-transition rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground reveal-on-hover"
                    >
                      <Icon name="Lightbulb" className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      title="Clarify"
                      onClick={(e) => {
                        e.stopPropagation();
                        openClarify(item.id);
                      }}
                      className="tech-transition rounded p-1 text-primary hover:bg-primary/10 reveal-on-hover"
                    >
                      <Icon name="ArrowRight" className="h-3.5 w-3.5" />
                    </button>
                    {/* The kind's own actions — always visible, never
                        hover-gated. One list with the card (`inboxRowActions`). */}
                    {actions.map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        title={a.title}
                        aria-label={a.label}
                        onClick={(e) => {
                          e.stopPropagation();
                          a.run();
                        }}
                        className={[
                          "tech-transition rounded p-1",
                          a.id === "remove"
                            ? "text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
                            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                        ].join(" ")}
                      >
                        <Icon name={a.icon} className="h-3.5 w-3.5" />
                      </button>
                    ))}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
