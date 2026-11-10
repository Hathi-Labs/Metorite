"use client";

// The ⓘ beside a number: what it means, how it is computed, and this row's
// own arithmetic. WS-50 slice 2, decision D94.
//
// 🔴 **Why not `title`.** Every explanation in this console used to live in a
// `title` attribute. It is invisible until a person suspects it is there, it
// waits a second before it shows, and it never shows on a touch screen. The
// owner asked for an explanation on hover. This is a visible icon that opens
// on hover, on keyboard focus and on a tap.
//
// ⚠️ **`position: fixed`, measured from the icon.** Most numbers sit inside a
// `.tablewrap`, which scrolls and CLIPS. An absolutely placed popover would be
// cut off at the table's edge. A scroll closes an unpinned popover rather than
// leaving it floating away from its icon.
//
// ⚠️ **The words come from `lib/glossary.ts`, never from the call site.** A
// call site passes the term and its OWN numbers (`detail`), so one word keeps
// one meaning across every page.

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { GLOSSARY, type TermKey } from "@/lib/glossary";

const WIDTH = 320;
const GAP = 8;

export default function Explain({
  term,
  detail,
  notes,
  scope = "For this customer",
}: {
  term: TermKey;
  /** The worked arithmetic with this row's own numbers, from `lib/money.ts`. */
  detail?: string | null;
  /** Extra lines, such as why a figure is an estimate. */
  notes?: string[];
  /** The heading over `detail`. */
  scope?: string;
}) {
  const entry = GLOSSARY[term];
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const place = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const vw = window.innerWidth;
    const width = Math.min(WIDTH, vw - 2 * GAP);
    const left = Math.max(GAP, Math.min(r.left + r.width / 2 - width / 2, vw - width - GAP));
    setPos({ top: r.bottom + 6, left });
  }, []);

  const show = useCallback(() => {
    place();
    setOpen(true);
  }, [place]);

  const hide = useCallback(() => {
    setOpen(false);
    setPinned(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => (pinned ? place() : setOpen(false));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();
    const onDown = (e: MouseEvent) => {
      if (!btn.current?.parentElement?.contains(e.target as Node)) hide();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open, pinned, place, hide]);

  return (
    <span
      className="explain"
      onMouseEnter={show}
      onMouseLeave={() => !pinned && setOpen(false)}
    >
      <button
        ref={btn}
        type="button"
        className="explain-btn"
        aria-label={`What is ${entry.title}?`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onFocus={show}
        onBlur={() => !pinned && setOpen(false)}
        onClick={() => {
          if (pinned) {
            hide();
          } else {
            setPinned(true);
            show();
          }
        }}
      >
        i
      </button>
      {open && pos && (
        <span
          role="tooltip"
          id={id}
          className="explain-pop"
          style={{ top: pos.top, left: pos.left, width: Math.min(WIDTH, window.innerWidth - 2 * GAP) }}
        >
          <strong className="explain-title">{entry.title}</strong>
          <span className="explain-means">{entry.means}</span>
          <span className="explain-formula">{entry.formula}</span>
          {detail && (
            <span className="explain-detail">
              <span className="explain-label">{scope}</span>
              {detail}
            </span>
          )}
          {notes?.map((n) => (
            <span key={n} className="explain-note">
              {n}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
