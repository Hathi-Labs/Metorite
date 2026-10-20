"use client";

/**
 * The headings and the text counts of the body on screen (WS-61 KB-2d).
 *
 * The editor draws the body, read-only or in an edit. This hook reads what
 * it drew: each heading (`[data-md-level]`, which `schema.ts` writes), and the
 * text of each block for the counts. It reads again when the body changes,
 * through a `MutationObserver`, so an edit and a re-read keep the outline
 * true. It never writes into the editor's DOM, because ProseMirror owns it.
 *
 * It also follows the scroll of the page, and marks the heading in view
 * (`activeHeadingIndex`). `jump` scrolls a heading to the top of the view,
 * below the sticky row of the breadcrumbs. On the first read, a `#anchor`
 * in the address scrolls to its heading once, and so does each new one.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { activeHeadingIndex, textStats, withSlugs, type OutlineHeading, type TextStats } from "../lib/outline";

/** The blocks whose text the counts read. A table cell holds a paragraph. */
const TEXT_BLOCKS = ".ProseMirror :is(p, h2, h3, h4, h5, h6, pre, summary)";

/** The headings of the body. `schema.ts` marks each one with its stored level. */
const HEADINGS = ".ProseMirror [data-md-level]";

export interface PageHeadings {
  headings: OutlineHeading[];
  /** The heading elements, in the order of `headings`. */
  elements: HTMLElement[];
  stats: TextStats;
  /** The index of the heading in view, or -1. */
  active: number;
  /** Scroll heading `index` to the top of the view. */
  jump: (index: number, opts?: { smooth?: boolean }) => void;
}

interface Found {
  headings: OutlineHeading[];
  elements: HTMLElement[];
}

const NONE: Found = { headings: [], elements: [] };

function sameFound(a: Found, b: Found): boolean {
  return (
    a.headings.length === b.headings.length &&
    a.headings.every((h, i) => h.slug === b.headings[i].slug && h.level === b.headings[i].level) &&
    a.elements.every((el, i) => el === b.elements[i])
  );
}

function prefersLessMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function usePageHeadings({
  body,
  scroller,
  stickyTop,
}: {
  /** The element that holds the editor. */
  body: HTMLElement | null;
  /** The element that scrolls the page. */
  scroller: HTMLElement | null;
  /** The sticky row at the top of the scroller, which covers a heading. */
  stickyTop?: HTMLElement | null;
}): PageHeadings {
  const [found, setFound] = useState<Found>(NONE);
  const [stats, setStats] = useState<TextStats>({ words: 0, characters: 0 });
  const [active, setActive] = useState(-1);
  // The newest elements, for the scroll handler, which runs between renders.
  const elements = useRef<HTMLElement[]>([]);
  const hashDone = useRef(false);

  /** The px that the sticky row covers, and a little air under it. */
  const offset = useCallback(() => (stickyTop?.offsetHeight ?? 0) + 12, [stickyTop]);

  const follow = useCallback(() => {
    const els = elements.current;
    if (!scroller || !els.length) {
      setActive(-1);
      return;
    }
    const top = scroller.getBoundingClientRect().top;
    const tops = els.map((el) => el.getBoundingClientRect().top - top);
    const line = offset() + Math.min(96, scroller.clientHeight * 0.2);
    const atEnd = scroller.scrollTop > 0 && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
    setActive(activeHeadingIndex(tops, line, { atEnd, viewBottom: scroller.clientHeight }));
  }, [scroller, offset]);

  const jump = useCallback(
    (index: number, opts: { smooth?: boolean } = {}) => {
      const el = elements.current[index];
      if (!scroller || !el) return;
      const target =
        scroller.scrollTop + el.getBoundingClientRect().top - scroller.getBoundingClientRect().top - offset();
      scroller.scrollTo({
        top: Math.max(0, target),
        behavior: opts.smooth && !prefersLessMotion() ? "smooth" : "auto",
      });
      setActive(index);
    },
    [scroller, offset],
  );

  // Read the body: now, and after each change the editor makes.
  useEffect(() => {
    if (!body) return;
    let frame = 0;
    const read = () => {
      frame = 0;
      const els = [...body.querySelectorAll<HTMLElement>(HEADINGS)];
      const next: Found = {
        headings: withSlugs(els.map((el) => ({ level: Number(el.dataset.mdLevel) || 1, text: el.textContent ?? "" }))),
        elements: els,
      };
      elements.current = els;
      setFound((prev) => (sameFound(prev, next) ? prev : next));
      const counted = textStats([...body.querySelectorAll<HTMLElement>(TEXT_BLOCKS)].map((el) => el.textContent ?? ""));
      setStats((prev) => (prev.words === counted.words && prev.characters === counted.characters ? prev : counted));
      follow();
      // A `#anchor` in the address scrolls to its heading, once.
      if (!hashDone.current && next.headings.length) {
        hashDone.current = true;
        const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
        const at = hash ? next.headings.findIndex((h) => h.slug === hash) : -1;
        if (at >= 0) jump(at);
      }
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(read);
    };
    schedule();
    const watch = new MutationObserver(schedule);
    watch.observe(body, { childList: true, subtree: true, characterData: true });
    return () => {
      watch.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [body, follow, jump]);

  // A new `#anchor` on the same page scrolls to its heading too.
  const slugs = useRef<string[]>([]);
  useEffect(() => {
    slugs.current = found.headings.map((h) => h.slug);
  }, [found]);
  useEffect(() => {
    const onHash = () => {
      const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
      const at = hash ? slugs.current.indexOf(hash) : -1;
      if (at >= 0) jump(at);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [jump]);

  // Follow the scroll and the size of the view.
  useEffect(() => {
    if (!scroller) return;
    let frame = 0;
    const onScroll = () => {
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          follow();
        });
      }
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [scroller, follow]);

  return { headings: found.headings, elements: found.elements, stats, active, jump };
}
