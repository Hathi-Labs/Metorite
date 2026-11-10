"use client";

/**
 * The match bar of a page opened from a search (WS-61 KB-3d,
 * `knowledge_base.md` §21.7).
 *
 * A slim bar over the body says `N matches for "words"`. The up and down
 * buttons step through the matches, and the close button removes it. The bar
 * is no second match engine: it gives the words to the find engine of KB-2i
 * (`lib/editor/searchAndReplace.ts`) and reads the count back, exactly as
 * `FindBar` does. The marks are the `.kb-find-match` marks of that engine.
 *
 * The page owns `?q=`. Close calls `onClose`, and the page drops `q` from
 * the address.
 */
import { useCallback, useEffect } from "react";
import type { Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";

import Button from "@/components/ui/Button";

import { refresh, showCurrent } from "./FindBar";

/** "1 match", "4 matches", "No match" for these words. */
export function matchSummary(total: number, terms: readonly string[]): string {
  const words = terms.join(" ");
  if (total === 0) return `No match for "${words}"`;
  return `${total} ${total === 1 ? "match" : "matches"} for "${words}"`;
}

export function MatchBar({
  editor,
  terms,
  onClose,
  scroller,
}: {
  editor: Editor;
  terms: readonly string[];
  onClose: () => void;
  scroller?: HTMLElement | Window;
}) {
  const counts = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const s = e?.storage.searchAndReplace;
      return { total: s?.results.length ?? 0, index: s?.resultIndex ?? 0 };
    },
  });
  const key = terms.join("\u0000");

  // The words go to the find engine, from the first match.
  useEffect(() => {
    editor.commands.setSearchTerms([...terms]);
    editor.commands.setCaseSensitive(false);
    editor.commands.resetIndex();
    refresh(editor);
    showCurrent(editor, scroller);
    // `terms` follows `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, key, scroller]);

  // Closing takes the marks away.
  useEffect(
    () => () => {
      if (editor.isDestroyed) return;
      editor.commands.setSearchTerms([]);
      refresh(editor);
    },
    [editor],
  );

  const step = useCallback(
    (dir: 1 | -1) => {
      if (dir === 1) editor.commands.nextSearchResult();
      else editor.commands.previousSearchResult();
      refresh(editor);
      showCurrent(editor, scroller);
    },
    [editor, scroller],
  );

  const total = counts?.total ?? 0;
  return (
    <div
      role="search"
      aria-label="Matches in this page"
      className="sticky top-0 z-20 mb-3 flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground shadow-sm"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <span role="status" aria-live="polite" className="min-w-0 flex-1 truncate" data-match-summary="">
        {matchSummary(total, terms)}
        {total > 0 ? <span className="ml-2 text-muted-foreground">{(counts?.index ?? 0) + 1} of {total}</span> : null}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="ArrowUp"
        aria-label="Previous match"
        title="Previous match"
        disabled={!total}
        onClick={() => step(-1)}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="ArrowDown"
        aria-label="Next match"
        title="Next match"
        disabled={!total}
        onClick={() => step(1)}
      />
      <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close matches" title="Close" onClick={onClose} />
    </div>
  );
}
