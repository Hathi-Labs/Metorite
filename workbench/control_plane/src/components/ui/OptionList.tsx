"use client";

/**
 * A list of options that hangs at a point and that another control drives.
 *
 * The Knowledge editor's `/` menu and its `[[` page picker use it (WS-61
 * KB-2b). The caret stays in the editor while the list is open, so the
 * keys belong to the editor: it moves the active row with `stepActive` and
 * picks on Enter. The list only draws the rows and takes a pointer pick.
 *
 * ## How it looks
 *
 * The same panel as `SelectButton` and `StatusMenu`: `AnchoredPanel`, `p-1`,
 * `text-xs` rows, `bg-muted` on the active row, and an `<Icon>` for each row
 * that has one. A list with no rows says so in one muted line, never an
 * empty box.
 *
 * ## Why the rows take `mousedown`
 *
 * A click moves the focus out of the editor before `click` fires, and the
 * editor then closes the list. `onMouseDown` with `preventDefault` keeps the
 * caret where it was and picks the row.
 *
 * ⚠️ Not built on `@base-ui/react`: `Modal.tsx` is the one file that may
 * import it (AGENTS.md rule 8).
 */
import { Fragment, useEffect, useRef } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel, { type PanelAnchor } from "@/components/ui/AnchoredPanel";

export interface OptionRow {
  id: string;
  label: string;
  /** A second, muted line or column: a space name, a shortcut. */
  hint?: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon?: string;
  /** One emoji, drawn in the icon's place (a page emoji). */
  glyph?: string;
  /** A muted line under the label. */
  description?: string;
  /**
   * The heading above the first row of a run with this name. Rows of one
   * section must follow each other. A list with no `section` draws none.
   */
  section?: string;
}

/** The next active row of a list of `count`, wrapping at both ends. */
export function stepActive(active: number, delta: number, count: number): number {
  if (count <= 0) return 0;
  return (((active + delta) % count) + count) % count;
}

/** The DOM id of a row, so the driver can name it in `aria-activedescendant`. */
export function optionId(listId: string, index: number): string {
  return `${listId}-option-${index}`;
}

export interface OptionListProps {
  /** Where the list hangs: the caret, or a control. */
  anchor: PanelAnchor | null;
  open: boolean;
  /** The id of the `listbox`. Each row's id derives from it. */
  id: string;
  /** What the list offers, for a screen reader: "Blocks", "Pages". */
  label: string;
  rows: readonly OptionRow[];
  active: number;
  onPick: (index: number) => void;
  /** The pointer moved onto a row. */
  onHover?: (index: number) => void;
  /** The one line a list with no rows shows. */
  emptyText: string;
  /** A line above the rows, such as "Loading pages…". */
  status?: string | null;
  maxHeight?: number;
}

/** The list's width, `w-72`, in rem. */
const LIST_WIDTH_REM = 18;

/**
 * An anchor that keeps the list inside the window. A caret near the right
 * edge, as on a phone, would hang the list off the screen.
 */
export function keepInWindow(anchor: PanelAnchor | null): PanelAnchor | null {
  if (!anchor) return null;
  return {
    getBoundingClientRect: () => {
      const r = anchor.getBoundingClientRect();
      if (typeof window === "undefined") return r;
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const width = Math.min(LIST_WIDTH_REM * rem, window.innerWidth - 16);
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
      // Not `{ ...r }`: a DOMRect keeps its fields on the prototype.
      return { left, right: left, top: r.top, bottom: r.bottom, width: r.width };
    },
  };
}

export function OptionList({
  anchor,
  open,
  id,
  label,
  rows,
  active,
  onPick,
  onHover,
  emptyText,
  status,
  maxHeight = 320,
}: OptionListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  // Keep the active row in view inside the list's own scroller. Never
  // `scrollIntoView`, which also scrolls the page (AGENTS.md, AppShell).
  useEffect(() => {
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (!list || !row) return;
    if (active === 0) {
      list.scrollTop = 0; // The first row's heading stays in view.
      return;
    }
    const top = row.offsetTop;
    const bottom = top + row.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }, [active, rows]);

  return (
    <AnchoredPanel anchor={keepInWindow(anchor)} open={open} maxHeight={maxHeight} className="w-72 max-w-[calc(100vw-1rem)] p-1">
      <div ref={listRef} role="listbox" id={id} aria-label={label} className="max-h-[inherit] overflow-y-auto">
        {status ? <p className="px-2 py-1 text-[11px] text-muted-foreground">{status}</p> : null}
        {rows.length === 0 && !status ? (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">{emptyText}</p>
        ) : null}
        {rows.map((row, index) => {
          const current = index === active;
          const heading = row.section && row.section !== rows[index - 1]?.section ? row.section : null;
          return (
            <Fragment key={row.id}>
              {heading ? (
                <div
                  role="presentation"
                  className="px-2 pb-1 pt-2 text-[11px] font-medium text-muted-foreground first:pt-1"
                >
                  {heading}
                </div>
              ) : null}
              <div
                id={optionId(id, index)}
                role="option"
                aria-selected={current}
                data-index={index}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onPick(index);
                }}
                onMouseEnter={() => onHover?.(index)}
                className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs text-foreground ${
                  current ? "bg-muted" : "hover:bg-muted/60"
                }`}
              >
                {row.glyph ? (
                  <span aria-hidden className="w-3.5 shrink-0 text-center text-sm leading-none">
                    {row.glyph}
                  </span>
                ) : row.icon ? (
                  <Icon name={row.icon} size={14} className="shrink-0 text-muted-foreground" />
                ) : null}
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{row.label}</span>
                  {row.description ? (
                    <span className="block truncate text-[11px] text-muted-foreground">{row.description}</span>
                  ) : null}
                </span>
                {row.hint ? (
                  <span className="max-w-[45%] shrink-0 truncate text-[11px] text-muted-foreground">{row.hint}</span>
                ) : null}
              </div>
            </Fragment>
          );
        })}
      </div>
    </AnchoredPanel>
  );
}

export default OptionList;
