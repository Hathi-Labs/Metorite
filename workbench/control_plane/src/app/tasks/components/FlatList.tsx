"use client";

/**
 * /tasks · the ungrouped views — Done, Someday, Archive, Engage, Priority.
 *
 * Lifted out of `ItemList` by WS-27ad, because the reason it had to move is the
 * whole ticket: the board and the grouped list had grown the shared keyboard
 * cursor, the post-drop flash and the group-context quick-add, and these lists
 * — a third of the app's surfaces — had none of them. Arrow keys did nothing
 * here while they walked rows one click away.
 *
 * Everything interactive in this file is the shared implementation:
 * `@/lib/cursor` for the cursor and its Shift-sweep, `@/components/useFlash`
 * for the landing flash, `@/components/QuickAdd` for the capture box. What
 * stays local is GTD: WHICH bucket a quick-add lands in is `lib/quickAdd`'s
 * `viewQuickAdd`, and a view that cannot honestly take one shows no box.
 */

import { QuickAdd } from "@/components/QuickAdd";
import { Checkbox } from "@/components/ui/Checkbox";
import { useFlash } from "@/components/useFlash";
import { clampCursor, stepCursor } from "@/lib/cursor";
import { useMemo, useState } from "react";

import { viewQuickAdd } from "../lib/quickAdd";
import { useTaskStore } from "../lib/taskStore";
import { GtdItem, ViewKey } from "../lib/types";
import { TaskCard } from "./TaskCard";

export function FlatList({
  items,
  view,
  showPriority = false,
}: {
  items: GtdItem[];
  view: ViewKey;
  showPriority?: boolean;
}) {
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const toggleSelected = useTaskStore((s) => s.toggleSelected);
  const extendSelection = useTaskStore((s) => s.extendSelection);
  const quickAddNext = useTaskStore((s) => s.quickAddNext);
  const openFocus = useTaskStore((s) => s.openFocus);

  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const { flash, attach, scrollTo } = useFlash();

  const rows = useMemo(() => items.map((item) => item.id), [items]);
  // Clamped at READ time rather than synced by an effect: the rows shrink under
  // the cursor on every reload, and a state write per reload is exactly the
  // cascading-render pattern the lint forbids.
  const cursorAt = clampCursor(rows.length, cursor);

  const box = viewQuickAdd(view);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.defaultPrevented) return;
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]",
      )
    )
      return;
    // Ungated, as on /projects: Shift+Arrow sweeps whether or not anything is
    // already selected. It used to require entering select mode first.
    const picked = selectedIds;
    const next = stepCursor(
      rows,
      { cursor: cursorAt, anchor, selection: picked },
      event.key,
      event.shiftKey,
    );
    if (!next) return;
    event.preventDefault();
    setCursor(next.cursor);
    setAnchor(next.anchor);
    if (next.selection !== picked) extendSelection([...next.selection]);
    if (next.open) openFocus(next.open);
    if (next.cursor >= 0) scrollTo(rows[next.cursor]);
  }

  const quickAdd = async (title: string) => {
    if (!box) return;
    const id = quickAddNext(title, box.prefill);
    if (id) flash(id);
  };

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="Task list — arrow keys move, Enter opens"
      className="flex-1 overflow-y-auto outline-none"
    >
      {items.map((item, index) => {
        const atCursor = cursorAt >= 0 && cursorAt === index;
        const selected = selectedIds.has(item.id);
        return (
          <div
            key={item.id}
            ref={attach(item.id)}
            className={atCursor ? "bg-muted/60 ring-2 ring-inset ring-ring" : ""}
          >
            {/* Checkbox BESIDE the card, never over it (/projects' shape): the
                card keeps its own click — opening the task — and the box is a
                second target. The card used to be wrapped in the label and made
                inert, so the same click meant two different things depending on
                a mode set three components away. The gutter carries the card's
                own bottom border so the rule runs the full width. */}
            <div
              className={[
                "flex items-stretch",
                selected ? "bg-primary/5" : "",
              ].join(" ")}
            >
              <label className="flex w-6 shrink-0 cursor-pointer items-center justify-center border-b border-border">
                <Checkbox
                  checked={selected}
                  onChange={(e) =>
                    toggleSelected(
                      item.id,
                      (e.nativeEvent as MouseEvent).shiftKey,
                      rows,
                    )
                  }
                  aria-label={selected ? "Deselect task" : "Select task"}
                />
              </label>
              <div className="min-w-0 flex-1">
                <TaskCard item={item} variant="row" showPriority={showPriority} />
              </div>
            </div>
          </div>
        );
      })}
      {/* The view's own capture box, where the view can honestly take one. */}
      {box ? (
        <div className="px-3.5 py-2">
          <QuickAdd label={box.label} onAdd={quickAdd} className="max-w-md" />
        </div>
      ) : null}
    </div>
  );
}
