"use client";

import Icon from "@/components/Icon";
import { Checkbox } from "@/components/ui/Checkbox";
import { useFlash } from "@/components/useFlash";
import { clampCursor, stepCursor } from "@/lib/cursor";
import { useMemo, useState } from "react";
import { GtdItem } from "../lib/types";
import { lensNudge } from "../lib/lens";
import { useTaskStore } from "../lib/taskStore";
import { initials, relativeTime } from "../lib/utils";
import {
  STALE_WAITING_DAYS,
  daysWaiting,
  groupByWaitingOn,
  isStaleWaiting,
  isWaitingOverdue,
  nudgeOutcome,
  waitingLine,
} from "../lib/waiting";

// The GTD "Waiting For" list (spec: task_manager_app.md §1 line 46, §6).
//
// Every other processed-task view answers "what should I do?". This one answers
// "who owes me what, and since when?" — so it is grouped by PERSON, not by
// stage or priority: the human action is "chase Sai", once, about everything
// he has of mine. Each row carries the three facts §1 pins — who / what /
// since-when — plus the two flags §6 asks for:
//
//   overdue — past the date the row is judged on: an explicit `expectedBy`
//             (someone promised it) when there is one, else the item's own
//             `dueAt`. The row SAYS which of the two it is, because "promised
//             by Friday" and "due Friday" are different claims about the world
//   stale   — nothing heard for STALE_WAITING_DAYS since `delegatedAt`
//
// The predicates live in lib/waiting.ts (pure, unit-tested, and the same
// 5-day rule the gateway's /tasks/insights uses).
//
// ⚠️ **Two different acts were both called "the nudge", and this comment used
// to say only the second one.** It read "that write is
// Action-Broker/owner-gated", which left the in-app half looking forbidden and
// kept `lastNudgedAt` a column nothing ever wrote.
//
//   IN-APP  — one `pm_notifications` row, the same path a mention takes.
//             §9.12.9's own shape, AGENT-SAFE, and it ships here as
//             `NudgeControl` → `lensNudge` → `POST /tasks/{id}/nudge`.
//   OUTWARD — drafting and sending a MESSAGE (mail, WhatsApp). Action-Broker
//             work, owner-gated, and still unbuilt.
//
// WS-27ad: this view now walks with the shared keyboard cursor
// (`@/lib/cursor`) like every other /tasks and /projects surface — arrows move,
// Shift extends the selection inside select mode, Enter opens the focus pane.
//
// It gets NO quick-add, and that is a decision rather than an omission
// (`lib/quickAdd.viewQuickAdd` records it in the one place the mapping lives):
// this view's groups are PEOPLE, and a create can set the WAITING bucket but
// not the delegation — who owes it and since when comes from the delegate path,
// which asks. A box under "Priya" that filed its task into "Unassigned" would
// be a lie about where the task went.

const NOBODY: ReadonlySet<string> = new Set();

export function WaitingForView({ items }: { items: GtdItem[] }) {
  const openFocus = useTaskStore((s) => s.openFocus);
  const selectMode = useTaskStore((s) => s.selectMode);
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const toggleSelected = useTaskStore((s) => s.toggleSelected);
  const extendSelection = useTaskStore((s) => s.extendSelection);

  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const { attach, scrollTo } = useFlash();

  // ONE wall-clock read, threaded through every predicate below, so a single
  // list can never disagree with itself about what "now" is. Real time is the
  // point here (the frozen-demo-clock bug this view replaces); memoised so the
  // render stays idempotent — same shape as CalendarView's overdue scan.
  const { now, groups } = useMemo(() => {
    // eslint-disable-next-line react-hooks/purity
    const nowMs = Date.now();
    return { now: nowMs, groups: groupByWaitingOn(items, nowMs) };
  }, [items]);

  // The cursor's world: every row in render order — person by person, in the
  // order the groups are drawn.
  const rows = useMemo(
    () => groups.flatMap((group) => group.items.map((item) => item.id)),
    [groups],
  );
  const cursorAt = clampCursor(rows.length, cursor);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.defaultPrevented) return;
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]",
      )
    )
      return;
    const picked = selectMode ? selectedIds : NOBODY;
    const next = stepCursor(
      rows,
      { cursor: cursorAt, anchor, selection: picked },
      event.key,
      selectMode && event.shiftKey,
    );
    if (!next) return;
    event.preventDefault();
    setCursor(next.cursor);
    setAnchor(next.anchor);
    if (next.selection !== picked) extendSelection([...next.selection]);
    if (next.open) openFocus(next.open);
    if (next.cursor >= 0) scrollTo(rows[next.cursor]);
  }

  const atCursor = (id: string) => cursorAt >= 0 && rows[cursorAt] === id;

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="Waiting for — arrow keys move, Enter opens"
      className="flex-1 overflow-y-auto outline-none"
    >
      {groups.map((g) => (
        <section key={g.key}>
          {/* WHO — carried by the group header, since it's the same person for
              every row beneath it. */}
          <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-card/95 px-3 py-1.5 backdrop-blur">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[9px] font-semibold text-primary">
              {initials(g.label)}
            </span>
            <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-foreground">
              {g.label}
            </span>
            <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
              {g.items.length}
            </span>
            {g.overdueCount > 0 && (
              <span className="shrink-0 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-semibold text-destructive">
                {g.overdueCount} overdue
              </span>
            )}
          </div>
          {g.items.map((item) => (
            <div
              key={item.id}
              ref={attach(item.id)}
              // `flex` so the nudge sits BESIDE the opening button rather than
              // inside it. See the note at the foot of `WaitingRow`.
              className={`flex items-start border-b border-border/60 ${
                atCursor(item.id) ? "bg-muted/60 ring-2 ring-inset ring-ring" : ""
              }`}
            >
              <div className="min-w-0 flex-1">
              {selectMode ? (
                <label className="flex cursor-pointer items-center gap-2 pl-3 hover:bg-secondary/40">
                  <Checkbox
                    checked={selectedIds.has(item.id)}
                    onChange={(e) =>
                      toggleSelected(
                        item.id,
                        (e.nativeEvent as MouseEvent).shiftKey,
                        rows,
                      )
                    }
                    aria-label={selectedIds.has(item.id) ? "Deselect task" : "Select task"}
                  />
                  <div className="pointer-events-none min-w-0 flex-1">
                    <WaitingRow item={item} who={g.label} nowMs={now} />
                  </div>
                </label>
              ) : (
                <button
                  type="button"
                  onClick={() => openFocus(item.id)}
                  className="tech-transition block w-full text-left hover:bg-secondary/40"
                >
                  <WaitingRow item={item} who={g.label} nowMs={now} />
                </button>
              )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1 px-3 py-2">
                <NudgeControl item={item} nowMs={now} />
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

/** One waiting-for line: who · what · since-when, then the flags. `who` is
 *  repeated here (small, muted) so a row copied or read out of its group still
 *  carries all three facts §1 requires. */
function WaitingRow({
  item,
  who,
  nowMs,
}: {
  item: GtdItem;
  who: string;
  nowMs: number;
}) {
  const days = daysWaiting(item, nowMs);
  const overdue = isWaitingOverdue(item, nowMs);
  const stale = isStaleWaiting(item, nowMs);
  // The date this row is judged on, and WHICH fact it is. Always rendered when
  // there is one (it used to appear only for an explicit expectedBy, which hid
  // the line the Overdue badge was actually reading).
  const line = waitingLine(item);
  return (
    <div className="flex min-w-0 items-start gap-2 px-3 py-2">
      <div className="min-w-0 flex-1">
        {/* WHAT — the clarified next action if there is one, else the title. */}
        <p className="truncate text-sm text-foreground">
          {item.nextAction || item.title}
        </p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-[10px] text-muted-foreground">{who}</span>
          {/* SINCE-WHEN — the age of the ask; the exact date on hover. */}
          {days === null ? (
            <span className="text-[10px] text-muted-foreground/70">
              no delegation date
            </span>
          ) : (
            <span
              title={`Delegated ${relativeTime(item.delegatedAt, nowMs)}`}
              className={[
                "inline-flex items-center gap-1 text-[10px]",
                stale ? "font-medium text-warning" : "text-muted-foreground",
              ].join(" ")}
            >
              <Icon name="Hourglass" className="h-3 w-3" />
              {days}d waiting
            </span>
          )}
          {line && (
            <span
              title={
                line.kind === "promised"
                  ? `Promised by ${new Date(line.iso).toLocaleString()}`
                  : `No promised-by date — judged on this task's own due date, ${new Date(line.iso).toLocaleString()}`
              }
              className={[
                "inline-flex items-center gap-1 text-[10px]",
                overdue ? "font-medium text-destructive" : "text-muted-foreground",
              ].join(" ")}
            >
              {overdue ? (
                <Icon name="AlertTriangle" className="h-3 w-3" />
              ) : (
                <Icon name="Clock" className="h-3 w-3" />
              )}
              {/* WHICH fact — an explicit promise reads differently from the
                  task's own deadline, and the row must not blur them. */}
              {line.kind === "promised" ? "promised by " : "due "}
              {relativeTime(line.iso, nowMs)}
            </span>
          )}
          {item.origin?.kind === "email" && (
            <span
              title={`From email — ${item.origin.fromName || item.origin.fromEmail || ""}`}
              className="inline-flex items-center text-[10px] text-muted-foreground"
            >
              <Icon name="Mail" className="h-3 w-3" />
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {overdue && (
          <span className="rounded border border-destructive/30 bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
            Overdue
          </span>
        )}
        {stale && !overdue && (
          <span
            title={`No movement for over ${STALE_WAITING_DAYS} days`}
            className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
          >
            Stale
          </span>
        )}
        {/* ⚠️ The nudge button and "nudged 3d ago" are NOT here, and the reason
            is structural rather than aesthetic: this row renders INSIDE the
            `<button>` that opens the task, so a second button here would be a
            button inside a button — invalid HTML, and dead under the select
            mode's `pointer-events-none`. `NudgeControl` is a sibling of that
            button, in the row container above. */}
      </div>
    </div>
  );
}

/** "Nudge", and afterwards what it actually achieved.
 *
 * ⚠️ The outcome sentence comes from `nudgeOutcome` rather than being written
 * here, because `vitest` never collects a `.tsx` (D-PM-21). The rule that
 * matters — an empty `notified` on a 200 is NOT a success — is fenced in
 * `lib/waiting.test.ts`. */
function NudgeControl({ item, nowMs }: { item: GtdItem; nowMs: number }) {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);
  const [sentAt, setSentAt] = useState<string | null>(null);

  // The server's answer wins once there is one, so the row updates without the
  // whole view refetching.
  const nudgedAt = sentAt ?? item.lastNudgedAt ?? null;

  async function send() {
    setBusy(true);
    setOutcome(null);
    try {
      const result = await lensNudge(item.id);
      const read = nudgeOutcome(result);
      setOutcome(read);
      // ⚠️ Only when somebody was told. The server stamps on the same
      // condition, and a row that drew "nudged just now" over a chase that
      // reached nobody is exactly the lie this pair exists to prevent.
      if (read.ok) setSentAt(result.last_nudged_at);
    } catch (err) {
      setOutcome({ ok: false, message: String((err as Error).message) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={busy}
        onClick={(e) => {
          // The row opens the task on click. The nudge is its own act.
          e.stopPropagation();
          void send();
        }}
        className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted disabled:opacity-50"
      >
        <Icon name="Send" className="h-3 w-3" />
        {busy ? "Nudging…" : "Nudge"}
      </button>
      {outcome && (
        <span
          className={`text-[10px] ${outcome.ok ? "text-muted-foreground" : "text-destructive"}`}
        >
          {outcome.message}
        </span>
      )}
      {nudgedAt && (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
          <Icon name="Send" className="h-3 w-3" />
          nudged {relativeTime(nudgedAt, nowMs)}
        </span>
      )}
    </>
  );
}
