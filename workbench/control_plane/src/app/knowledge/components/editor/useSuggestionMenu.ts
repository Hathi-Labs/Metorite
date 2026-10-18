"use client";

/**
 * The state of one suggestion menu of the editor: the `/` menu or the `[[`
 * page picker (WS-61 KB-2b).
 *
 * `@tiptap/suggestion` tells us when the typed trigger starts, changes and
 * ends, and it hands each key to us first. This hook keeps what the menu
 * draws in React state, and a ref of the same state, because a key handler
 * runs between two renders and must read the newest rows.
 *
 * The caret never leaves the editor. Up and Down move the active row, Enter
 * and Tab pick it, and Escape closes the menu.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import type { Range } from "@tiptap/core";
import type { SuggestionKeyDownProps, SuggestionProps } from "@tiptap/suggestion";

import { stepActive } from "@/components/ui/OptionList";

export interface MenuState {
  query: string;
  range: Range;
  active: number;
  /** Where the typed trigger is on screen. */
  rect: (() => DOMRect | null) | null;
}

export interface SuggestionMenu<T> {
  state: MenuState | null;
  /** Pass to the Suggestion plugin's `render`. */
  render: () => {
    onStart: (props: SuggestionProps<T, T>) => void;
    onUpdate: (props: SuggestionProps<T, T>) => void;
    onKeyDown: (props: SuggestionKeyDownProps) => boolean;
    onExit: () => void;
  };
  setActive: (index: number) => void;
  /** Pick a row: the plugin's command runs with it, and the menu closes. */
  pick: (row: T) => void;
  close: () => void;
}

/**
 * `rowsFor` gives the rows for a query. It is read on each key, so the
 * caller may change what it returns (the page list loads after the menu
 * opens).
 */
export function useSuggestionMenu<T>(rowsFor: (query: string) => readonly T[]): SuggestionMenu<T> {
  const [state, setState] = useState<MenuState | null>(null);
  const stateRef = useRef<MenuState | null>(null);
  const commandRef = useRef<((row: T) => void) | null>(null);
  const rowsForRef = useRef(rowsFor);
  rowsForRef.current = rowsFor;

  const commit = useCallback((next: MenuState | null) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const fromProps = useCallback(
    (props: SuggestionProps<T, T>, keepActive: boolean) => {
      commandRef.current = props.command;
      const count = rowsForRef.current(props.query).length;
      const prev = stateRef.current;
      const active = keepActive && prev && prev.query === props.query ? Math.min(prev.active, Math.max(0, count - 1)) : 0;
      commit({ query: props.query, range: props.range, active, rect: props.clientRect ?? null });
    },
    [commit],
  );

  const pick = useCallback(
    (row: T) => {
      const command = commandRef.current;
      commit(null);
      command?.(row);
    },
    [commit],
  );

  const render = useCallback(
    () => ({
      onStart: (props: SuggestionProps<T, T>) => fromProps(props, false),
      onUpdate: (props: SuggestionProps<T, T>) => fromProps(props, true),
      onKeyDown: ({ event }: SuggestionKeyDownProps) => {
        const current = stateRef.current;
        if (!current) return false;
        const rows = rowsForRef.current(current.query);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          commit({ ...current, active: stepActive(current.active, event.key === "ArrowDown" ? 1 : -1, rows.length) });
          return true;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          const row = rows[current.active];
          if (!row) return false;
          pick(row);
          return true;
        }
        if (event.key === "Escape") {
          commit(null);
          return true;
        }
        return false;
      },
      onExit: () => commit(null),
    }),
    [commit, fromProps, pick],
  );

  const setActive = useCallback(
    (index: number) => {
      const current = stateRef.current;
      if (current) commit({ ...current, active: index });
    },
    [commit],
  );

  const close = useCallback(() => commit(null), [commit]);

  return useMemo(() => ({ state, render, setActive, pick, close }), [state, render, setActive, pick, close]);
}
