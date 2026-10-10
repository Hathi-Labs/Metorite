"use client";

import AppIcon from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * THE right-click menu (WS-27bd item 5).
 *
 * A lightweight menu with no primitive behind it: it follows the established
 * click-away popover pattern (a full-screen catcher + an absolute `bg-popover`
 * panel — see StatusPicker/SnoozeMenu) and adds viewport-flip so it never opens
 * off-screen. Flat item list (with optional section labels / separators /
 * checkmarks) — no flyout submenus, which keeps it robust; a "Change stage"
 * group is just labelled items.
 *
 * **Promoted here from `app/tasks/components/` unchanged**, because /projects
 * needed the same interaction and writing a second one is the defect CLAUDE.md
 * §5 names. `/tasks` reaches it through a re-export shim at the old path, so
 * its five call sites are untouched. `src/lib/sharedTaskUi.test.ts` fences the
 * "only one of these exists" half — including the pre-existing THIRD copy
 * inside `app/email/components/EmailList.tsx`, which is recorded there as an
 * exemption naming it rather than swept into this slice.
 *
 * What it deliberately does NOT own: the items. A surface builds its own
 * `CtxItem[]` — /projects builds them from the declared registry in
 * `app/projects/lib/taskMenu.ts`, /tasks from `useCardActions`.
 */

export type CtxItem =
  | {
      kind: "item";
      label: string;
      icon?: ThemedIcon;
      onSelect: () => void;
      danger?: boolean;
      checked?: boolean;
    }
  | { kind: "label"; label: string }
  | { kind: "sep" };

export function ContextMenu({
  x,
  y,
  items,
  onClose,
  autoFocus = false,
}: {
  x: number;
  y: number;
  items: CtxItem[];
  onClose: () => void;
  /**
   * Move the focus to the first item when the menu opens. A menu opened from
   * the keyboard (a "···" button) needs it, or Tab walks the page behind it.
   * Escape still closes it, and the caller puts the focus back.
   */
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

  // Flip into view if the click was near a screen edge.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = x;
    let ny = y;
    if (x + r.width > window.innerWidth) {
      nx = Math.max(4, window.innerWidth - r.width - 4);
    }
    if (y + r.height > window.innerHeight) {
      ny = Math.max(4, window.innerHeight - r.height - 4);
    }
    setPos({ x: nx, y: ny });
  }, [x, y]);

  useEffect(() => {
    if (autoFocus) ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [autoFocus]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[80]"
      onClick={onClose}
      onContextMenu={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div
        ref={ref}
        style={{ left: pos.x, top: pos.y }}
        onClick={(e) => e.stopPropagation()}
        className="absolute min-w-[196px] max-w-[240px] overflow-hidden rounded-lg border border-border bg-popover py-1 text-popover-foreground shadow-xl"
      >
        {items.map((it, i) => {
          if (it.kind === "sep") {
            return <div key={i} className="my-1 h-px bg-border" />;
          }
          if (it.kind === "label") {
            return (
              <div
                key={i}
                className="px-3 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
              >
                {it.label}
              </div>
            );
          }
          const Icon = it.icon;
          return (
            <button
              key={i}
              type="button"
              onClick={() => {
                it.onSelect();
                onClose();
              }}
              className={[
                "tech-transition flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-secondary",
                it.danger ? "text-destructive" : "text-foreground",
              ].join(" ")}
            >
              {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
              <span className="min-w-0 flex-1 truncate">{it.label}</span>
              {it.checked && (
                <AppIcon name="Check" className="h-3.5 w-3.5 shrink-0 text-primary" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
