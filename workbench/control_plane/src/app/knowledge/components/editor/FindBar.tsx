"use client";

/**
 * Adapted from Docmost (AGPL-3.0),
 * apps/client/src/features/editor/components/search-and-replace/search-and-replace-dialog.tsx.
 * Docmost draws it with Mantine and Tabler. Here it draws with our `Input`,
 * `Button` and `<Icon>`, and the colours are tokens.
 *
 * ── Find and replace in the page (WS-61 KB-2i, `knowledge_base.md` §21.7) ──
 *
 * Mod-F opens it, in the read view and in an edit. It hangs at the top
 * right of the body and stays in view while the page scrolls.
 *
 * - Enter goes to the next match, and Shift+Enter to the one before.
 * - Alt+C turns "Match case" on and off. Alt+R shows the replace row, in an
 *   edit only.
 * - In the replace row, Enter replaces the match in focus, and
 *   Ctrl+Alt+Enter replaces all of them.
 * - Escape closes it, and the marks go.
 *
 * ⚠️ Mod-F only. Mod-K belongs to the shell's command bar (D87).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

/** Recount the matches: the plugin reads the storage on the next transaction. */
export function refresh(editor: Editor) {
  if (!editor.isDestroyed) editor.view.dispatch(editor.state.tr.setMeta("kbFind", true));
}

/** Scroll the match in focus to the middle of the box that scrolls the page. */
export function showCurrent(editor: Editor, scroller: HTMLElement | Window | undefined) {
  if (editor.isDestroyed) return;
  const el = editor.view.dom.querySelector<HTMLElement>(".kb-find-match-current");
  if (!el) return;
  const rect = el.getBoundingClientRect();
  if (!scroller || scroller === window) {
    window.scrollBy({ top: rect.top - window.innerHeight / 2 });
    return;
  }
  const box = (scroller as HTMLElement).getBoundingClientRect();
  if (rect.top >= box.top + 48 && rect.bottom <= box.bottom - 16) return;
  (scroller as HTMLElement).scrollTop += rect.top - box.top - box.height / 2;
}

export interface FindBarProps {
  editor: Editor;
  /** Replace shows only in an edit. */
  editable: boolean;
  /** A new number opens the bar again and takes the selected text. */
  openCount: number;
  onClose: () => void;
  scroller?: HTMLElement | Window;
}

export function FindBar({ editor, editable, openCount, onClose, scroller }: FindBarProps) {
  const [find, setFind] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [showReplace, setShowReplace] = useState(false);
  const [matchCase, setMatchCase] = useState(false);
  const findRef = useRef<HTMLInputElement | null>(null);

  const counts = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const s = e?.storage.searchAndReplace;
      return { total: s?.results.length ?? 0, index: s?.resultIndex ?? 0 };
    },
  });

  // Each open takes the selected text, as Docmost does, and selects the field.
  useEffect(() => {
    const { from, to } = editor.state.selection;
    const picked = from === to ? "" : editor.state.doc.textBetween(from, to, " ").slice(0, 200);
    if (picked && !picked.includes("\n")) setFind(picked);
    requestAnimationFrame(() => {
      findRef.current?.focus();
      findRef.current?.select();
    });
  }, [openCount, editor]);

  // A change of the words or of the case finds again, from the first match.
  useEffect(() => {
    editor.commands.setSearchTerms([find]);
    editor.commands.setCaseSensitive(matchCase);
    editor.commands.resetIndex();
    refresh(editor);
    showCurrent(editor, scroller);
  }, [find, matchCase, editor, scroller]);

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

  const replaceOne = () => {
    editor.commands.setReplaceTerm(replaceWith);
    editor.commands.replace();
    // The plugin counted again in the replace, and may need the index fixed.
    window.setTimeout(() => {
      refresh(editor);
      showCurrent(editor, scroller);
    }, 0);
  };

  const replaceAll = () => {
    editor.commands.setReplaceTerm(replaceWith);
    editor.commands.replaceAll();
    refresh(editor);
  };

  const close = () => {
    onClose();
    if (editable) editor.commands.focus();
  };

  const status = !find.trim() ? "" : counts && counts.total > 0 ? `${counts.index + 1} of ${counts.total}` : "No match";

  return (
    <div className="kb-find-anchor">
      <div
        role="search"
        aria-label="Find in this page"
        className="kb-find shadow-md"
        // Escape closes the bar from any control in it, a button too.
        onKeyDown={(e) => {
          if (e.key === "Escape" && !e.defaultPrevented) {
            e.preventDefault();
            close();
          }
        }}
      >
        <div className="flex items-center gap-1">
          <div className="relative min-w-0 flex-1">
            <Input
              ref={findRef}
              icon="Search"
              inputSize="sm"
              aria-label="Find"
              placeholder="Find"
              value={find}
              onChange={(e) => setFind(e.target.value)}
              aria-describedby="kb-find-status"
              className="w-full pr-16"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  step(e.shiftKey ? -1 : 1);
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  close();
                } else if (e.altKey && e.code === "KeyC") {
                  e.preventDefault();
                  setMatchCase((v) => !v);
                } else if (e.altKey && e.code === "KeyR" && editable) {
                  e.preventDefault();
                  setShowReplace((v) => !v);
                }
              }}
            />
            <span
              id="kb-find-status"
              aria-live="polite"
              className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground"
            >
              {status}
            </span>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ArrowUp"
            aria-label="Previous match"
            title="Previous match (Shift+Enter)"
            disabled={!counts?.total}
            onClick={() => step(-1)}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ArrowDown"
            aria-label="Next match"
            title="Next match (Enter)"
            disabled={!counts?.total}
            onClick={() => step(1)}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            icon="CaseSensitive"
            aria-label="Match case"
            title="Match case (Alt+C)"
            selected={matchCase}
            onClick={() => setMatchCase((v) => !v)}
          />
          {editable ? (
            <Button
              variant="ghost"
              size="icon-sm"
              icon="Replace"
              aria-label="Replace"
              title="Replace (Alt+R)"
              selected={showReplace}
              onClick={() => setShowReplace((v) => !v)}
            />
          ) : null}
          <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close find" title="Close (Escape)" onClick={close} />
        </div>
        {editable && showReplace ? (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <Input
              autoFocus
              icon="Replace"
              inputSize="sm"
              aria-label="Replace with"
              placeholder="Replace with"
              value={replaceWith}
              onChange={(e) => setReplaceWith(e.target.value)}
              className="min-w-0 flex-1"
              onKeyDown={(e) => {
                if (e.key === "Enter" && e.ctrlKey && e.altKey) {
                  e.preventDefault();
                  replaceAll();
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  replaceOne();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  close();
                }
              }}
            />
            <Button variant="secondary" size="sm" disabled={!counts?.total} onClick={replaceOne} title="Replace (Enter)">
              Replace
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!counts?.total}
              onClick={replaceAll}
              title="Replace all (Ctrl+Alt+Enter)"
            >
              Replace all
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
