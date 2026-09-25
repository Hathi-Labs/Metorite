"use client";

/**
 * THE status picker — every place a member picks a task's exact status.
 *
 * D79, "stages group, statuses write". A stage (a status category) is how the
 * two apps GROUP work. A write always names one exact status id. So the menu
 * lists the real statuses of the task's status set, grouped under their
 * stages, and a pick is a status id. Callers:
 *
 * * My Tasks: the status pill, the card's right-click "Change status", and
 *   the drag prompt (a card dropped on a stage that holds two or more
 *   statuses asks here, filtered to that stage).
 * * Both apps: the Status control in the shared task body (`TaskBody`).
 *
 * ## How it looks
 *
 * The same panel as `SelectButton`'s list: `AnchoredPanel`, `p-1`, `text-xs`
 * rows, `bg-muted` on the current one. A header names the project, because a
 * status name ("Review") means something only inside its project. Each stage
 * is a small heading (`CATEGORY_LABEL`), and each status carries its colour
 * dot through `statusAccent`, the one status vocabulary (AGENTS.md rule 4).
 * A stage with no status shows one disabled row, so "there is no Cancelled
 * status here" is said rather than implied by a gap.
 *
 * ## Keyboard
 *
 * It opens with focus on the current status (or on `focusId`). Up and Down
 * move between statuses and skip the headings. Typing a letter jumps to the
 * next status whose name starts with what was typed. Enter picks. Escape
 * closes and gives focus back to the trigger.
 *
 * ⚠️ The keyboard rules are PURE functions below (`menuRows`, `stepFocus`,
 * `typeAhead`, `initialFocus`), because `vitest.config.ts` runs in `node` and
 * cannot render this component. `StatusMenu.test.ts` pins them.
 *
 * ⚠️ Not built on `@base-ui/react`: `Modal.tsx` is the one file that may
 * import it (AGENTS.md rule 8). Outside clicks go through
 * `lib/outsideClick.ts`, the sanctioned answer for a popover.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel, { type PanelAnchor } from "@/components/ui/AnchoredPanel";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { statusAccent } from "@/lib/statusAccent";
import { CATEGORY_LABEL, groupByCategory } from "@/lib/statusCategory";

/** One status the menu can offer. `color` is the stored colour name. */
export interface StatusOption {
  id: string;
  name: string;
  category: string;
  position: number;
  color?: string | null;
}

/** One row of the menu, top to bottom. */
export type MenuRow =
  | { kind: "stage"; category: string; label: string }
  | { kind: "status"; status: StatusOption }
  | { kind: "empty"; category: string; label: string };

/**
 * The rows the menu draws: each stage's heading, then its statuses in board
 * order, or one disabled row when the stage holds none. `onlyCategory` keeps
 * one stage, for the drag prompt.
 */
export function menuRows(
  statuses: readonly StatusOption[],
  onlyCategory?: string,
): MenuRow[] {
  const out: MenuRow[] = [];
  for (const group of groupByCategory(statuses)) {
    if (onlyCategory && group.category !== onlyCategory) continue;
    out.push({ kind: "stage", category: group.category, label: group.label });
    if (group.rows.length === 0) {
      out.push({
        kind: "empty",
        category: group.category,
        label: `No ${group.label.toLowerCase()} status`,
      });
    }
    for (const status of group.rows) out.push({ kind: "status", status });
  }
  return out;
}

const isStatus = (row: MenuRow | undefined): boolean => row?.kind === "status";

/**
 * Where focus lands when the menu opens: `focusId`, else the current
 * status, else the first status. `-1` when the menu has no status at all.
 */
export function initialFocus(
  rows: readonly MenuRow[],
  currentId?: string | null,
  focusId?: string | null,
): number {
  for (const wanted of [focusId, currentId]) {
    if (!wanted) continue;
    const at = rows.findIndex((r) => r.kind === "status" && r.status.id === wanted);
    if (at >= 0) return at;
  }
  return rows.findIndex(isStatus);
}

/** The next status row from `from`, down (`1`) or up (`-1`). It wraps. */
export function stepFocus(rows: readonly MenuRow[], from: number, dir: 1 | -1): number {
  const n = rows.length;
  if (n === 0) return -1;
  for (let step = 1; step <= n; step++) {
    const at = (((from + dir * step) % n) + n) % n;
    if (isStatus(rows[at])) return at;
  }
  return from;
}

/**
 * The next status after `from` whose name starts with `query`, ignoring
 * case. It wraps, and it may land on `from` itself when that is the only
 * match. `-1` when nothing matches.
 */
export function typeAhead(rows: readonly MenuRow[], from: number, query: string): number {
  const q = query.trim().toLowerCase();
  const n = rows.length;
  if (!q || n === 0) return -1;
  for (let step = 1; step <= n; step++) {
    const at = (((from + step) % n) + n) % n;
    const row = rows[at];
    if (row.kind === "status" && row.status.name.toLowerCase().startsWith(q)) return at;
  }
  return -1;
}

/** The dot for one status: its stored colour first, then its stage. */
export function statusDot(status: StatusOption): string {
  // `accentForStatus`'s order in Projects: the stored colour, then the
  // stage. The name is not read, so a lane reads one hue in both apps.
  return statusAccent({ color: status.color, category: status.category }).dot;
}

/** How long typed letters stay one query. */
const TYPE_AHEAD_MS = 600;

export interface StatusMenuProps {
  /** What the panel hangs from: the trigger, a card, or a pointer box. */
  anchor: PanelAnchor | null;
  open: boolean;
  /** The project the statuses belong to, named in the header. */
  projectName?: string;
  statuses: readonly StatusOption[];
  /** The task's status now. It carries the check mark. */
  currentId?: string | null;
  /** Keep one stage only — the drag prompt. */
  onlyCategory?: string;
  /** Focus this status on open instead of the current one. */
  focusId?: string | null;
  /** A line under the header, for the drag prompt's question. */
  prompt?: string;
  onPick: (statusId: string) => void;
  /** Escape or a click outside. The caller decides what that undoes. */
  onClose: () => void;
  /** Where focus goes back to on Escape. Defaults to `anchor` if it is focusable. */
  returnFocusTo?: HTMLElement | null;
}

export function StatusMenu({
  anchor,
  open,
  projectName,
  statuses,
  currentId,
  onlyCategory,
  focusId,
  prompt,
  onPick,
  onClose,
  returnFocusTo,
}: StatusMenuProps) {
  const rows = useMemo(() => menuRows(statuses, onlyCategory), [statuses, onlyCategory]);
  const buttons = useRef(new Map<number, HTMLButtonElement>());
  const [focused, setFocused] = useState(-1);
  const typed = useRef({ query: "", at: 0 });

  const giveBack = useCallback(() => {
    const target =
      returnFocusTo ?? (anchor instanceof HTMLElement ? anchor : null);
    target?.focus();
  }, [anchor, returnFocusTo]);

  // Focus on open: the current status, or `focusId`. Re-run when the rows
  // arrive, because a caller may open the menu before its lanes load.
  useEffect(() => {
    if (!open) return;
    setFocused(initialFocus(rows, currentId, focusId));
  }, [open, rows, currentId, focusId]);

  // Move DOM focus with `focused`. The panel draws one frame AFTER it opens
  // (`AnchoredPanel` measures first), so a row that is not there yet takes
  // focus as it mounts, through `wantFocus` in its ref below.
  const wantFocus = useRef(-1);
  useEffect(() => {
    if (!open || focused < 0) return;
    const el = buttons.current.get(focused);
    if (el) {
      el.focus();
      wantFocus.current = -1;
    } else {
      wantFocus.current = focused;
    }
  }, [open, focused]);

  // A click outside closes it. The anchor counts as inside, so a trigger
  // that toggles the menu is not closed and then reopened by one click.
  useEffect(() => {
    if (!open) return;
    const surface = anchor instanceof Element ? anchor : null;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(surface))) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, anchor, onClose]);

  // Every key the menu handles is stopped here. The menu is portalled, but a
  // React event still bubbles through the React tree, so a board's own
  // arrow keys, a My Tasks letter key or the focused task's Escape would act
  // on it too.
  const onKeyDown = (event: React.KeyboardEvent) => {
    const claim = () => {
      event.preventDefault();
      event.stopPropagation();
    };
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      claim();
      setFocused((at) => stepFocus(rows, at, event.key === "ArrowDown" ? 1 : -1));
      return;
    }
    if (event.key === "Escape") {
      claim();
      onClose();
      giveBack();
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      claim();
      const row = rows[focused];
      if (row?.kind === "status") onPick(row.status.id);
      return;
    }
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      claim();
      const now = Date.now();
      const query =
        now - typed.current.at < TYPE_AHEAD_MS ? typed.current.query + event.key : event.key;
      typed.current = { query, at: now };
      // A repeated first letter steps through the matches; a longer query
      // stays on the current row while it still matches.
      const from = query.length > 1 ? focused - 1 : focused;
      const hit = typeAhead(rows, from, query);
      if (hit >= 0) setFocused(hit);
    }
  };

  const stageLabel = onlyCategory ? CATEGORY_LABEL[onlyCategory] ?? onlyCategory : null;

  return (
    <AnchoredPanel
      anchor={anchor}
      open={open}
      layer="top"
      maxHeight={320}
      className="max-h-80 w-max min-w-[12rem] max-w-[18rem] p-1"
      panelProps={{
        role: "listbox",
        "aria-label": stageLabel ? `${stageLabel} statuses` : "Status",
        onKeyDown,
      }}
    >
      {projectName || prompt ? (
        <div className="px-2 pb-1 pt-0.5">
          {projectName ? (
            <p className="truncate text-[11px] font-medium text-foreground">{projectName}</p>
          ) : null}
          {prompt ? <p className="text-[11px] text-muted-foreground">{prompt}</p> : null}
        </div>
      ) : null}
      {rows.map((row, index) => {
        if (row.kind === "stage") {
          return (
            <p
              key={`stage:${row.category}`}
              role="presentation"
              className="px-2 pb-0.5 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
            >
              {row.label}
            </p>
          );
        }
        if (row.kind === "empty") {
          return (
            <p
              key={`empty:${row.category}`}
              role="option"
              aria-disabled
              aria-selected={false}
              className="cursor-not-allowed px-2 py-1 text-xs text-muted-foreground"
            >
              {row.label}
            </p>
          );
        }
        const { status } = row;
        const current = status.id === currentId;
        return (
          <button
            key={status.id}
            ref={(el) => {
              if (!el) {
                buttons.current.delete(index);
                return;
              }
              buttons.current.set(index, el);
              if (wantFocus.current === index) {
                wantFocus.current = -1;
                el.focus();
              }
            }}
            type="button"
            role="option"
            aria-selected={current}
            tabIndex={index === focused ? 0 : -1}
            onFocus={() => setFocused(index)}
            onClick={() => onPick(status.id)}
            className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs outline-none hover:bg-muted focus-visible:bg-muted focus-visible:ring-1 focus-visible:ring-ring ${
              current ? "bg-muted font-medium" : ""
            }`}
          >
            <span className={`h-2 w-2 shrink-0 rounded-full ${statusDot(status)}`} />
            <span className="min-w-0 flex-1 truncate pr-px">{status.name}</span>
            {current ? <Icon name="Check" size={12} className="shrink-0 text-primary" /> : null}
          </button>
        );
      })}
    </AnchoredPanel>
  );
}

export default StatusMenu;
