"use client";

/**
 * The title of a page in an edit (WS-61 KB-2i, `knowledge_base.md` §21.7).
 *
 * A borderless, auto-growing textarea that draws like the read title
 * (`.kb-title`), joined to the body by a `TitleLink`. The keys follow
 * Docmost's title editor, and `lib/editor/titleFlow.ts` holds the rules:
 * Enter moves the text after the caret into a new first paragraph of the
 * body, and ArrowDown, or ArrowRight at the end, moves into the body. The
 * body's ArrowUp at its start comes back here. Mod-F opens find in the
 * page, as it does in the body.
 */
import { useLayoutEffect, useRef } from "react";

import { splitTitle } from "../../lib/editor/titleFlow";

/** What the body offers the title. `KnowledgeEditor` sets it in an edit. */
export interface BodyActs {
  focusStart: () => void;
  /** Put `text` in a new first paragraph, and the caret at its start. */
  prependParagraph: (text: string) => void;
  openFind: () => void;
}

/** The two ends of the title flow. */
export interface TitleLink {
  title: React.RefObject<HTMLTextAreaElement | null>;
  body: React.RefObject<BodyActs | null>;
}

/** One link for the life of the page view. */
export function useTitleLink(): TitleLink {
  const title = useRef<HTMLTextAreaElement | null>(null);
  const body = useRef<BodyActs | null>(null);
  const link = useRef<TitleLink>({ title, body });
  return link.current;
}

export function TitleField({
  value,
  onChange,
  link,
}: {
  value: string;
  onChange: (title: string) => void;
  link: TitleLink;
}) {
  // Grow with the text: the title wraps and never scrolls.
  useLayoutEffect(() => {
    const el = link.title.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value, link.title]);
  return (
    <textarea
      ref={link.title}
      aria-label="Title"
      placeholder="Untitled"
      rows={1}
      value={value}
      maxLength={300}
      // A title is one line. A pasted line break becomes a space.
      onChange={(e) => onChange(e.target.value.replace(/\s*\n+\s*/g, " "))}
      onKeyDown={(e) => {
        const body = link.body.current;
        const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean };
        // An input method still composing owns the keys (229 is Safari's).
        if (native.isComposing || native.keyCode === 229) return;
        if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "f" && body) {
          e.preventDefault();
          body.openFind();
          return;
        }
        if (!body || e.shiftKey || e.altKey || e.metaKey || e.ctrlKey) return;
        const el = e.currentTarget;
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? start;
        if (e.key === "Enter") {
          e.preventDefault();
          const { title, moved } = splitTitle(el.value, start, end);
          if (title !== el.value) onChange(title);
          body.prependParagraph(moved);
        } else if (e.key === "ArrowDown" || (e.key === "ArrowRight" && start === end && end === el.value.length)) {
          e.preventDefault();
          body.focusStart();
        }
      }}
      className="kb-title"
    />
  );
}
