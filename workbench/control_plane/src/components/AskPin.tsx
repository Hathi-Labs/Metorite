"use client";

/**
 * AskPin — a compact bar above the composer for the element that waits on
 * the member (`lib/askPin.ts`).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24, rule 1. The element
 * stays in the flow, in order. This bar shows only while that element is out
 * of view in the thread, so the member always sees one of the two. A press
 * scrolls the element into view and moves the focus to it, so the next Tab
 * lands on its controls. It is a `Button`, so a keyboard reaches it, and it
 * is one line at every width, which is the compact bar a phone needs.
 *
 * It answers nothing itself. The confirmation queue keeps its "1 of N" and
 * its one consent per card, and the bar only names the count.
 */

import { useEffect, useState, type RefObject } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { PIN_LEAD, type PendingAsk } from "@/lib/askPin";
import { revealWithin } from "@/lib/scrollWithin";

/** The element the bar points at, or null when it is not mounted. */
function targetIn(root: HTMLElement | null, target: string): HTMLElement | null {
  if (!root) return null;
  const sel = `[data-chat-ask="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(target) : target}"]`;
  const all = root.querySelectorAll<HTMLElement>(sel);
  return all.length > 0 ? all[all.length - 1] : null;
}

/** In view: half of it shows, or enough of it to act on. */
const SEEN_RATIO = 0.5;
const SEEN_PX = 120;

export default function AskPin({
  ask,
  threadRef,
  initialInView = null,
}: {
  ask: PendingAsk | null;
  threadRef: RefObject<HTMLElement | null>;
  /**
   * What the bar assumes before the observer reports. `null` (the default)
   * draws nothing until the first report, so an element already in view
   * never flashes the bar (review round 1). The markup tests set it.
   */
  initialInView?: boolean | null;
}) {
  const target = ask?.target ?? null;
  // What the observer last said, and about which element. A report about
  // another target says nothing about this one.
  const [seen, setSeen] = useState<{ target: string | null; inView: boolean | null }>({
    target,
    inView: initialInView,
  });

  useEffect(() => {
    if (!target) return;
    const root = threadRef.current;
    const el = targetIn(root, target);
    // Nothing to watch: the bar shows, as the only way to the element.
    if (!el || typeof IntersectionObserver === "undefined") {
      const raf = requestAnimationFrame(() => setSeen({ target, inView: false }));
      return () => cancelAnimationFrame(raf);
    }
    const io = new IntersectionObserver(
      (entries) => {
        const e = entries[entries.length - 1];
        if (!e) return;
        const visible =
          e.isIntersecting && (e.intersectionRatio >= SEEN_RATIO || e.intersectionRect.height >= SEEN_PX);
        setSeen({ target, inView: visible });
      },
      { root, threshold: [0, 0.25, SEEN_RATIO, 0.75, 1] },
    );
    io.observe(el);
    return () => io.disconnect();
    // `ask.title` changes when the queue moves on: the element may be new.
  }, [target, ask?.title, ask?.count, threadRef]);

  // Not known yet for this target: wait for the observer.
  const inView = seen.target === target ? seen.inView : null;
  if (!ask || inView !== false) return null;

  const show = () => {
    const el = targetIn(threadRef.current, ask.target);
    if (!el) return;
    // The thread only. `scrollIntoView` also scrolls each box above the
    // thread, and so it could shift the page (owner report, 2026-10-10).
    revealWithin(threadRef.current, el, "smooth");
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  };

  return (
    <div
      data-ask-pin=""
      role="status"
      aria-live="polite"
      className="max-w-3xl mx-auto mb-2 rounded-lg border border-primary/30 bg-primary/5 chat-fade-in"
    >
      <Button
        type="button"
        variant="ghost"
        size="none"
        radius="keep"
        layout="flex items-center"
        onClick={show}
        className="w-full min-w-0 gap-2 rounded-lg px-3 py-1.5 text-left text-[11px]"
        aria-label={`${PIN_LEAD[ask.kind]}: ${ask.title}. Show it`}
      >
        <Icon name="BellRing" size={13} className="shrink-0 text-primary" />
        <span className="shrink-0 font-medium text-foreground">{PIN_LEAD[ask.kind]}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{ask.title}</span>
        {ask.count > 1 && (
          <span className="shrink-0 tabular-nums text-muted-foreground">1 of {ask.count}</span>
        )}
        <span className="shrink-0 inline-flex items-center gap-0.5 text-primary">
          Show
          <Icon name="ArrowDown" size={12} />
        </span>
      </Button>
    </div>
  );
}
