"use client";

/**
 * The emoji picker of Knowledge (WS-61 KB-2f, `knowledge_base.md` §21.7).
 *
 * Built once for the page icon, and used by the `:` insert of KB-2i. It is
 * drawn from emoji DATA (`lib/emoji.ts`) with our own parts: `AnchoredPanel`
 * for the popover, `Input` for the search and `Button` for the group tabs
 * and Remove. No emoji-picker library and no Mantine.
 *
 * The shape follows Docmost's: a search box, a row of group tabs, a grid of
 * eight emoji to a row with a heading for each group, and Remove at the foot
 * when there is an icon to remove. A search shows one list of matches.
 *
 * The keys stay in the search box, as in `OptionList`. The arrows move the
 * active emoji (Up and Down by one row), Enter picks it, and Escape closes
 * the picker. `aria-activedescendant` names the active emoji. A click
 * outside the panel and its trigger closes it.
 *
 * ⚠️ The data loads on the first open, in its own chunk. Until then the
 * panel says "Loading emoji…", and a failed load says what to do next.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import AnchoredPanel, { type PanelAnchor } from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

import { emojiGroups, loadEmoji, rankEmoji, type EmojiEntry, type EmojiGroup } from "../lib/emoji";

/** The emoji in one row of the grid. */
export const EMOJI_COLUMNS = 8;

/** The most matches a search shows. */
export const EMOJI_MATCHES = 120;

/** One section of the grid: a group, or the matches of a search. */
export interface EmojiSection {
  id: string;
  label: string;
  emojis: EmojiEntry[];
}

/** The sections on screen. A search gives one section of its matches. Pure. */
export function pickerSections(groups: readonly EmojiGroup[], query: string): EmojiSection[] {
  if (query.trim()) {
    return [{ id: "matches", label: "Matches", emojis: rankEmoji(groups, query, EMOJI_MATCHES) }];
  }
  return groups.map((g) => ({ id: g.id, label: g.label, emojis: g.emojis }));
}

/**
 * The next active emoji for a key, in a list of `count`, or `null` for a key
 * the grid does not take. Up and Down move one row. The ends do not wrap,
 * so a member never jumps from the flags to the smileys by accident. Pure.
 */
export function stepEmoji(active: number, key: string, count: number, columns = EMOJI_COLUMNS): number | null {
  if (count <= 0) return null;
  const clamp = (n: number) => Math.max(0, Math.min(count - 1, n));
  switch (key) {
    case "ArrowRight":
      return clamp(active + 1);
    case "ArrowLeft":
      return clamp(active - 1);
    case "ArrowDown":
      return clamp(active + columns);
    case "ArrowUp":
      return clamp(active - columns);
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

export interface EmojiPickerProps {
  /** The trigger the panel hangs from. A click on it never counts as outside. */
  anchor: (PanelAnchor & Partial<Element>) | null;
  open: boolean;
  /** The member picked an emoji. The picker does not close by itself. */
  onPick: (char: string) => void;
  /** Escape, a click outside, or Remove. */
  onClose: () => void;
  /** Absent: no Remove button. */
  onRemove?: () => void;
  /** What the picker chooses, for a screen reader: "Page icon". */
  label?: string;
}

export default function EmojiPicker({ anchor, open, onPick, onClose, onRemove, label = "Emoji" }: EmojiPickerProps) {
  const listId = useId();
  const [groups, setGroups] = useState<EmojiGroup[] | null>(() => emojiGroups());
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const gridRef = useRef<HTMLDivElement | null>(null);

  // Each open starts clean. Set during render, the adjust-state pattern.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setQuery("");
      setActive(0);
    }
  }

  const load = useCallback(() => {
    setFailed(false);
    loadEmoji()
      .then(setGroups)
      .catch(() => setFailed(true));
  }, []);
  useEffect(() => {
    if (open && !groups) load();
  }, [open, groups, load]);

  const sections = useMemo(() => (groups ? pickerSections(groups, query) : []), [groups, query]);
  const flat = useMemo(() => sections.flatMap((s) => s.emojis), [sections]);
  const current = flat[Math.min(active, Math.max(0, flat.length - 1))];

  // A click outside the panel and the trigger closes it. The panel carries
  // the `PREVENT_OUTSIDE_CLICK` marker of `AnchoredPanel`.
  useEffect(() => {
    if (!open) return;
    const surface = anchor && "contains" in anchor ? (anchor as Element) : null;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(surface))) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, anchor, onClose]);

  // Keep the active emoji in view inside the grid's own scroller. Never
  // `scrollIntoView`, which also scrolls the page.
  useEffect(() => {
    const grid = gridRef.current;
    const cell = grid?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (!grid || !cell) return;
    const top = cell.offsetTop - grid.offsetTop;
    const bottom = top + cell.offsetHeight;
    if (top < grid.scrollTop) grid.scrollTop = top;
    else if (bottom > grid.scrollTop + grid.clientHeight) grid.scrollTop = bottom - grid.clientHeight;
  }, [active, flat]);

  const jumpTo = (sectionId: string) => {
    const grid = gridRef.current;
    const head = grid?.querySelector<HTMLElement>(`[data-section="${sectionId}"]`);
    if (grid && head) grid.scrollTop = head.offsetTop - grid.offsetTop;
    const first = flat.findIndex((e) => e.group === sectionId);
    if (first >= 0) setActive(first);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (current) onPick(current.char);
      return;
    }
    // Left and Right move the caret while the box holds text.
    if ((event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End") && query) {
      return;
    }
    const next = stepEmoji(active, event.key, flat.length);
    if (next !== null) {
      event.preventDefault();
      setActive(next);
    }
  };

  let index = -1;
  return (
    <AnchoredPanel
      anchor={anchor}
      open={open}
      maxHeight={380}
      className="flex w-80 max-w-[calc(100vw-1rem)] flex-col gap-2 p-2"
      panelProps={{ role: "dialog", "aria-label": label }}
    >
      <div className="flex min-h-0 flex-col gap-2">
        <Input
          aria-label="Search emoji"
          placeholder="Search emoji"
          value={query}
          autoFocus
          role="combobox"
          aria-expanded
          aria-controls={listId}
          aria-activedescendant={current ? `${listId}-e-${active}` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          className="w-full"
        />
        {groups && !query.trim() ? (
          <div className="flex items-center justify-between gap-0.5" aria-label="Emoji groups">
            {groups.map((g) => (
              <Button
                key={g.id}
                variant="ghost"
                size="icon-xs"
                icon={g.icon}
                aria-label={g.label}
                title={g.label}
                selected={current?.group === g.id}
                onClick={() => jumpTo(g.id)}
              />
            ))}
          </div>
        ) : null}
        <div
          ref={gridRef}
          id={listId}
          role="listbox"
          aria-label={label}
          className="min-h-0 flex-1 overflow-y-auto"
          style={{ maxHeight: "14rem" }}
        >
          {failed ? (
            <div role="alert" className="flex flex-col items-start gap-2 px-1 py-2 text-xs text-muted-foreground">
              <span>Could not load the emoji. Check the connection, then try again.</span>
              <Button variant="secondary" size="sm" icon="RefreshCw" onClick={load}>
                Try again
              </Button>
            </div>
          ) : !groups ? (
            <p className="px-1 py-2 text-xs text-muted-foreground">Loading emoji…</p>
          ) : flat.length === 0 ? (
            <p className="px-1 py-2 text-xs text-muted-foreground">No emoji match “{query.trim()}”.</p>
          ) : (
            sections.map((section) => (
              <div key={section.id} role="group" aria-label={section.label}>
                <p
                  data-section={section.id}
                  aria-hidden
                  className="sticky top-0 z-10 bg-card px-1 pb-1 pt-1.5 text-[11px] font-medium text-muted-foreground"
                >
                  {section.label}
                </p>
                <div className="grid grid-cols-8 gap-0.5">
                  {section.emojis.map((emoji) => {
                    index += 1;
                    const at = index;
                    const on = at === active;
                    return (
                      <div
                        key={`${section.id}-${emoji.char}`}
                        id={`${listId}-e-${at}`}
                        role="option"
                        aria-selected={on}
                        aria-label={emoji.name}
                        title={emoji.name}
                        data-index={at}
                        onMouseDown={(e) => {
                          // Keep the focus in the search box.
                          e.preventDefault();
                          onPick(emoji.char);
                        }}
                        onMouseEnter={() => setActive(at)}
                        className={`flex aspect-square cursor-pointer select-none items-center justify-center rounded text-xl leading-none ${
                          on ? "bg-muted" : "hover:bg-muted/60"
                        }`}
                      >
                        {emoji.char}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
        <div className="flex min-h-7 items-center justify-between gap-2 border-t border-border pt-2">
          <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground" aria-live="polite">
            {current ? `${current.char}  ${current.name}` : ""}
          </span>
          {onRemove ? (
            <Button variant="secondary" size="sm" icon="X" onClick={onRemove}>
              Remove
            </Button>
          ) : null}
        </div>
      </div>
    </AnchoredPanel>
  );
}
